"""Layer 05 — LLM Gateway: task -> model routing, provider failover, cost metering.

Add a new provider by implementing `complete()` with the same signature as
AnthropicProvider below — the rest of the framework only depends on the
Provider protocol in contracts.py, never on a specific vendor SDK.
"""
from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator

from .contracts import LLMResponse, Provider, ToolCall
from .runtime import RateLimiterLike, RateLimitExceeded

_IMAGE_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


def image_content(path: str) -> dict[str, Any]:
    """Builds a real multimodal content block (base64-inlined) from a local image
    file, in Anthropic's native format — pass it inside a message's `content`
    list alongside a text block: {"role": "user", "content": [image_content(p),
    {"type": "text", "text": "what's in this?"}]}."""
    media_type = _IMAGE_MEDIA_TYPES.get(Path(path).suffix.lower())
    if media_type is None:
        raise ValueError(f"unsupported image type: {path!r}")
    data = base64.standard_b64encode(Path(path).read_bytes()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}

# List pricing per 1M tokens (input, output), USD. Extend for other models/providers.
_PRICING: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": (3.00, 15.00),
    "claude-haiku-4-5-20251001": (0.80, 4.00),
    "claude-opus-5": (15.00, 75.00),
}


class AnthropicProvider:
    """Reference Provider implementation. Swap in OpenAI/Gemini/local by matching this interface."""

    def __init__(self, *, workspace_id: str | None = None) -> None:
        import os

        import anthropic

        # Identity-linked API keys (Console keys tied to a personal identity
        # rather than a plain workspace key) require this header on every
        # request, or the API rejects it with "anthropic-workspace-id is
        # required when authenticating with an identity-linked API key" — a
        # real 400 hit while wiring a live key, not a hypothetical. Standard
        # workspace-scoped keys ignore the header harmlessly, so it's safe to
        # always set it when a workspace id is available (explicit arg, else
        # ANTHROPIC_WORKSPACE_ID from the environment) and to leave it off
        # entirely otherwise.
        workspace_id = workspace_id or os.environ.get("ANTHROPIC_WORKSPACE_ID")
        if workspace_id:
            self._client = anthropic.Anthropic(default_headers={"anthropic-workspace-id": workspace_id})
        else:
            self._client = anthropic.Anthropic()

    def _turns(self, messages: list[dict]) -> tuple[str | None, list[dict]]:
        """Reconstructs Anthropic's actual tool-use conversation format on every
        call: an assistant turn that made a native tool call must carry a
        tool_use content block, and the following turn must carry a matching
        tool_result block referencing it by id — Anthropic's API rejects the
        request otherwise. orchestration.py's messages carry `tool_calls` /
        `tool_call_id` exactly so this can be rebuilt correctly, every turn."""
        system = next((m["content"] for m in messages if m["role"] == "system"), None)
        turns = []
        for m in messages:
            if m["role"] == "system":
                continue
            if m["role"] == "assistant" and m.get("tool_calls"):
                blocks: list[dict] = []
                if m["content"]:
                    blocks.append({"type": "text", "text": m["content"]})
                blocks.extend({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["args"]} for tc in m["tool_calls"])
                turns.append({"role": "assistant", "content": blocks})
            elif m["role"] == "tool" and m.get("tool_call_id"):
                turns.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": str(m["content"]), "is_error": not m.get("ok", True)}
                ]})
            elif m["role"] in ("user", "assistant"):
                turns.append({"role": m["role"], "content": m["content"]})
            else:
                # a tool-result message from the text-convention fallback path,
                # with no native tool_call_id to correlate against.
                turns.append({"role": "user", "content": f"[tool result] {m['content']}"})
        return system, turns

    def complete(self, messages: list[dict], *, model: str, max_tokens: int = 1024, tools: list[dict] | None = None, **kw: Any) -> LLMResponse:
        system, turns = self._turns(messages)
        create_kw: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": turns, **kw}
        if system is not None:
            # Anthropic's API rejects an explicit `system: null` outright
            # ("system: Input should be a valid array") — a real 400 hit on
            # any call with no system message (document_store.py's image
            # transcription, guardrails.py's LLM-judge check), both of which
            # build messages with no {"role": "system", ...} entry. Omit the
            # key entirely rather than pass None through.
            create_kw["system"] = system
        if tools:
            # tools arrive in ToolRegistry's provider-agnostic shape
            # ({"name","description","parameters"}) — translate to Anthropic's
            # own wire shape ({"name","description","input_schema"}) here.
            create_kw["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools]
        resp = self._client.messages.create(**create_kw)
        in_price, out_price = _PRICING.get(model, (0.0, 0.0))
        cost = (resp.usage.input_tokens * in_price + resp.usage.output_tokens * out_price) / 1_000_000
        tool_calls = [
            ToolCall(id=block.id, name=block.name, args=block.input)
            for block in resp.content if block.type == "tool_use"
        ]
        return LLMResponse(
            text="".join(block.text for block in resp.content if block.type == "text"),
            model=model,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            cost_usd=cost,
            tool_calls=tool_calls,
        )

    def stream(self, messages: list[dict], *, model: str, max_tokens: int = 1024, **kw: Any) -> Iterator[str]:
        """Real token streaming via Anthropic's streaming API — yields text deltas
        as they arrive, not a completed response chunked after the fact."""
        system, turns = self._turns(messages)
        stream_kw: dict[str, Any] = {"model": model, "max_tokens": max_tokens, "messages": turns, **kw}
        if system is not None:  # same "no literal system: null" fix as complete() above
            stream_kw["system"] = system
        with self._client.messages.stream(**stream_kw) as stream:
            yield from stream.text_stream


# OpenAI pricing intentionally left blank — fill in your account's model ids and
# per-1M-token rates; an unlisted model just meters as $0 rather than guessing.
_OPENAI_PRICING: dict[str, tuple[float, float]] = {}


class OpenAIProvider:
    """A second Provider, so LLMGateway's failover is genuinely cross-vendor, not
    just cross-model within one vendor. Full parity with AnthropicProvider:
    native tool-calling (OpenAI's own tool_calls wire shape, reconstructed on
    every turn the same way AnthropicProvider does for its shape), streaming."""

    def __init__(self) -> None:
        import openai

        self._client = openai.OpenAI()

    def _turns(self, messages: list[dict]) -> list[dict]:
        """Reconstructs OpenAI's tool-calling conversation format: an assistant
        turn that made a tool call carries `tool_calls`, and the following turn
        is `{"role": "tool", "tool_call_id": ..., "content": ...}` — a different
        wire shape than Anthropic's, built from the exact same
        tool_calls/tool_call_id fields orchestration.py already puts on every
        message, provider-agnostically."""
        turns = []
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                turns.append({
                    "role": "assistant",
                    "content": m["content"] or None,
                    "tool_calls": [
                        {"id": tc["id"], "type": "function", "function": {"name": tc["name"], "arguments": json.dumps(tc["args"])}}
                        for tc in m["tool_calls"]
                    ],
                })
            elif m["role"] == "tool" and m.get("tool_call_id"):
                turns.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": str(m["content"])})
            elif m["role"] in ("system", "user", "assistant"):
                turns.append({"role": m["role"], "content": m["content"]})
            else:
                # text-convention fallback path, no native tool_call_id to correlate against
                turns.append({"role": "user", "content": f"[tool result] {m['content']}"})
        return turns

    def complete(self, messages: list[dict], *, model: str, max_tokens: int = 1024, tools: list[dict] | None = None, **kw: Any) -> LLMResponse:
        create_kw: dict[str, Any] = {"model": model, "messages": self._turns(messages), "max_tokens": max_tokens, **kw}
        if tools:
            # tools arrive in ToolRegistry's provider-agnostic shape
            # ({"name","description","parameters"}) — translate to OpenAI's own
            # wire shape ({"type": "function", "function": {...}}) here.
            create_kw["tools"] = [
                {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
                for t in tools
            ]
        resp = self._client.chat.completions.create(**create_kw)
        in_price, out_price = _OPENAI_PRICING.get(model, (0.0, 0.0))
        cost = (resp.usage.prompt_tokens * in_price + resp.usage.completion_tokens * out_price) / 1_000_000
        message = resp.choices[0].message
        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, args=json.loads(tc.function.arguments))
            for tc in (message.tool_calls or [])
        ]
        return LLMResponse(
            text=message.content or "",
            model=model,
            input_tokens=resp.usage.prompt_tokens,
            output_tokens=resp.usage.completion_tokens,
            cost_usd=cost,
            tool_calls=tool_calls,
        )

    def stream(self, messages: list[dict], *, model: str, max_tokens: int = 1024, **kw: Any) -> Iterator[str]:
        """Real token streaming via OpenAI's streaming API."""
        stream = self._client.chat.completions.create(model=model, messages=self._turns(messages), max_tokens=max_tokens, stream=True, **kw)
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta


@dataclass
class MultiProvider:
    """Dispatches by model name to whichever vendor client owns it — the seam that makes
    'every model, one contract' real: LLMGateway never needs to know which vendor a
    model belongs to."""

    by_model: dict[str, Provider]

    def complete(self, messages: list[dict], *, model: str, **kw: Any) -> LLMResponse:
        if model not in self.by_model:
            raise KeyError(f"no provider registered for model {model!r}")
        return self.by_model[model].complete(messages, model=model, **kw)


@dataclass
class PromptCache:
    """In-memory, exact-match prompt cache keyed by (model, message sequence). Swap
    the dict for Redis/Memcached behind the same get()/set() interface for a cache
    shared across processes — nothing in LLMGateway changes."""

    ttl_s: float = 300.0
    _store: dict[tuple, tuple[float, LLMResponse]] = field(default_factory=dict)

    def _key(self, model: str, messages: list[dict]) -> tuple:
        # content may be a list of multimodal blocks (unhashable) — serialize
        # anything that isn't already a plain string before using it as a key.
        def content_key(content: Any) -> Any:
            return content if isinstance(content, str) else json.dumps(content, sort_keys=True)
        return (model, tuple((m["role"], content_key(m["content"])) for m in messages))

    def get(self, model: str, messages: list[dict]) -> LLMResponse | None:
        hit = self._store.get(self._key(model, messages))
        if hit is None:
            return None
        ts, resp = hit
        if time.time() - ts > self.ttl_s:
            return None
        return resp

    def set(self, model: str, messages: list[dict], response: LLMResponse) -> None:
        self._store[self._key(model, messages)] = (time.time(), response)


@dataclass
class ModelRegistry:
    """Tracks which models exist and their measured eval score, so routing can be
    built from evidence — eval.py's atomic-level records are the natural feed."""

    _scores: dict[str, list[float]] = field(default_factory=dict)

    def record_score(self, model: str, score: float) -> None:
        self._scores.setdefault(model, []).append(score)

    def avg_score(self, model: str) -> float:
        scores = self._scores.get(model, [])
        return sum(scores) / len(scores) if scores else 0.0

    def ranked(self, models: list[str]) -> list[str]:
        """Best-measured-first — feed this straight into LLMGateway.routes[task]."""
        return sorted(models, key=self.avg_score, reverse=True)


@dataclass
class LLMGateway:
    """Routes a task to a ranked list of models and fails over across them."""

    provider: Provider
    routes: dict[str, list[str]] = field(default_factory=lambda: {
        "default": ["claude-sonnet-5"],
        "cheap": ["claude-haiku-4-5-20251001", "claude-sonnet-5"],
        "hard": ["claude-opus-5", "claude-sonnet-5"],
    })
    cache: PromptCache | None = None
    rate_limiter: RateLimiterLike | None = None
    registry: ModelRegistry | None = None

    def complete(self, messages: list[dict], *, task: str = "default", **kw: Any) -> LLMResponse:
        last_err: Exception | None = None
        for model in self.routes.get(task, self.routes["default"]):
            if self.cache is not None:
                cached = self.cache.get(model, messages)
                if cached is not None:
                    return cached
            if self.rate_limiter is not None and not self.rate_limiter.allow(model):
                last_err = RateLimitExceeded(f"rate limit exceeded for model {model!r}")
                continue
            try:
                resp = self.provider.complete(messages, model=model, **kw)
            except Exception as e:  # provider outage / rate limit -> try next model in the route
                last_err = e
                continue
            if self.cache is not None:
                self.cache.set(model, messages, resp)
            return resp
        raise RuntimeError(f"all models for task={task!r} failed") from last_err

    def stream(self, messages: list[dict], *, task: str = "default", **kw: Any) -> Iterator[str]:
        """Real streaming — delegates to the provider's own stream() if it has
        one (AnthropicProvider does); raises if the routed model's provider
        doesn't support it rather than faking it by chunking a full response."""
        model = self.routes.get(task, self.routes["default"])[0]
        if not hasattr(self.provider, "stream"):
            raise NotImplementedError(f"{type(self.provider).__name__} does not support streaming")
        yield from self.provider.stream(messages, model=model, **kw)


def make_llm_judge(llm: LLMGateway, criterion: str, *, task: str = "cheap") -> Callable[[str], float]:
    """Ready-made `judge` callable for kpi.llm_judge_kpi(): asks the model to rate
    `criterion` 0-10 and returns it normalized to 0..1. Route `task` to your
    cheapest capable model — judging doesn't need your strongest one."""

    def judge(text: str) -> float:
        prompt = f"Rate the following on {criterion}, from 0 (worst) to 10 (best). Reply with ONLY the number.\n\nText:\n{text}"
        resp = llm.complete([{"role": "user", "content": prompt}], task=task)
        try:
            return max(0.0, min(10.0, float(resp.text.strip()))) / 10.0
        except ValueError:
            return 0.0

    return judge


def make_grounding_judge(llm: LLMGateway, *, task: str = "cheap") -> Callable[[str, list[str]], float]:
    """Like make_llm_judge, but evidence-aware — groundedness specifically
    can't be judged from the answer text alone (make_llm_judge's single-arg
    judge never sees what the answer is supposed to be grounded IN, so it
    can only rate style/confidence, not whether claims are actually
    supported). Rates 0-10 how well `text`'s claims are supported by
    `references`, normalized to 0..1. Built for
    kpi.composite_grounding_kpi's `judge=` — a different signature,
    (text, references) -> float, from llm_judge_kpi's single-arg one."""

    def judge(text: str, references: list[str]) -> float:
        evidence = "\n".join(f"- {p}" for p in references) or "(no reference passages provided)"
        prompt = (
            f"Evidence:\n{evidence}\n\nAnswer to check:\n{text}\n\n"
            "Rate how well every claim in the answer is actually supported by the evidence "
            "above, from 0 (unsupported or contradicted) to 10 (fully supported). Reply with "
            "ONLY the number."
        )
        resp = llm.complete([{"role": "user", "content": prompt}], task=task)
        try:
            return max(0.0, min(10.0, float(resp.text.strip()))) / 10.0
        except ValueError:
            return 0.0

    return judge

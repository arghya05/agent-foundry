"""AgentSpec — declarative agent configuration: the "define an agent in
YAML/JSON instead of Python" entry point. Every field maps straight onto an
existing core.agent.Agent(...) kwarg — this is a pure composition layer over
Agent.__init__, not a second agent-construction path with its own semantics.

`policy.max_cost_usd_per_thread`/`max_steps_per_thread` are how a spec sets
its budget — Agent.__init__'s own `budget=` kwarg (core/agent.py) just
defaults to `RunBudget(policy)`, so there is no independent "budget" shape to
expose here without duplicating what policy already carries.

Memory here is deliberately shallow: `memory: {"enabled": true}` gets a
default, in-process, non-persistent `MemoryStore()` (see agent_foundry/
context.py) — working/episodic/semantic/procedural/knowledge_graph all start
empty. Seeding real documents/facts into semantic memory or knowledge_graph
is a Python-level task (see examples/support_agent.py), not something this
declarative shape tries to cover.
"""
from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from .contracts import AutonomyLevel, Identity, Policy, ToolSpec
from .context import MemoryStore
from .core.agent import Agent, _toolspec_from_callable
from .kpi import KPI, composite_grounding_kpi, llm_judge_kpi
from .llm_gateway import AnthropicProvider, LLMGateway, OpenAIProvider, make_grounding_judge, make_llm_judge
from .orchestration import CritiqueConfig

_PROVIDERS: dict[str, Callable[[], Any]] = {"anthropic": AnthropicProvider, "openai": OpenAIProvider}


def resolve_tool(ref: str) -> Callable[..., Any]:
    """"pkg.module:function_name" -> the actual callable — the same
    uvicorn/gunicorn-style convention for naming Python code from outside
    Python. Security note: this imports and executes `ref`'s module at
    resolve time, same trust boundary `foundry run <script>` already has
    (it also execs an arbitrary .py file) — not a new attack surface, just
    worth knowing a spec's `tools:` list is not sandboxed."""
    if ":" not in ref:
        raise ValueError(f"tool ref {ref!r} must be 'module.path:function_name'")
    module_name, _, attr = ref.partition(":")
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise ImportError(f"tool ref {ref!r}: no such module {module_name!r}") from e
    try:
        return getattr(module, attr)
    except AttributeError as e:
        raise AttributeError(f"tool ref {ref!r}: module {module_name!r} has no attribute {attr!r}") from e


def _tool_from_entry(entry: str | dict[str, Any]) -> Callable[..., Any] | ToolSpec:
    """A `tools:` entry is either a bare "module:function" string (resolved
    straight to the callable, unchanged from before) or a dict with an
    `implementation` key plus ToolSpec metadata (destructive/timeout_s/
    max_retries/permissions/scopes/requires_confirmation) — resolved into a
    real ToolSpec instead. Agent.__init__'s _coerce_tools (core/agent.py)
    already accepts a list mixing plain callables and ToolSpec objects, so
    nothing downstream needs to change to consume either shape. This is
    what lets a declarative spec express e.g. `destructive: true` on a
    tool, which a bare "module:function" string never could — the gap
    examples/commerce_agent/agent.yaml's own comment documents."""
    if isinstance(entry, str):
        return resolve_tool(entry)
    entry = dict(entry)
    ref = entry.pop("implementation")
    fn = resolve_tool(ref)
    name = entry.pop("name", None)
    description = entry.pop("description", None)
    # Same type-hints+docstring -> JSON-schema-ish `parameters` inference
    # core.agent.Agent(tools=[a_plain_callable]) already uses — a structured
    # entry gets the identical inferred schema unless it overrides
    # `parameters:` itself below.
    spec = _toolspec_from_callable(fn, name=name, description=description)
    for frozenset_field in ("scopes", "permissions"):
        if frozenset_field in entry and not isinstance(entry[frozenset_field], frozenset):
            entry[frozenset_field] = frozenset(entry[frozenset_field])
    return replace(spec, **entry)


def _named_evaluator_kpi(name: str, llm: LLMGateway, *, threshold: float) -> KPI:
    """Resolves AgentSpec.critique's `evaluator:` name into a real KPI,
    using the agent's own LLMGateway as the judge — the one named evaluator
    with dedicated treatment is "groundedness" (composite_grounding_kpi,
    deterministic word-overlap + numeric-claim checks blended with an LLM
    judge via make_grounding_judge); any other name falls through to
    llm_judge_kpi(judge=make_llm_judge(llm, name)) — already fully generic
    over any criterion string (correctness, relevance, tool-selection,
    plan-adherence, conversation-quality, ...), so those don't need their
    own named branch here, just a name that becomes the judge's criterion."""
    if name == "groundedness":
        return composite_grounding_kpi(
            "groundedness", references=lambda ctx: ctx.get("references", []),
            judge=make_grounding_judge(llm), threshold=threshold,
        )
    return llm_judge_kpi(name, judge=make_llm_judge(llm, name), threshold=threshold)


def _default_critique_context(state: Mapping[str, Any], draft: str) -> dict[str, Any]:
    """Default CritiqueConfig.context for a declarative critique gate —
    grounds the draft answer against every tool-result message this turn
    produced. Promotes the exact pattern examples/autonomous_workflow/
    agent.py's own hand-written _critique_context uses (the codebase's only
    other precedent for building one of these) so a spec doesn't need
    Python to get equivalent behavior — a KPI object and a context callable
    (CritiqueConfig's two required fields) are otherwise not
    JSON/YAML-expressible at all."""
    references = [m["content"] for m in state.get("messages", []) if m.get("role") == "tool"]
    return {"output_text": draft, "references": references}


def _frozenset_fields(d: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    out = dict(d)
    for name in names:
        if name in out and not isinstance(out[name], frozenset):
            out[name] = frozenset(out[name])
    return out


def _policy_from_dict(d: dict[str, Any]) -> Policy:
    d = _frozenset_fields(d, ("allowed_tools", "requires_approval"))
    if "autonomy" in d and not isinstance(d["autonomy"], AutonomyLevel):
        raw = d["autonomy"]
        d["autonomy"] = AutonomyLevel[raw] if isinstance(raw, str) else AutonomyLevel(raw)
    return Policy(**d)


def _identity_from_dict(d: dict[str, Any]) -> Identity:
    d = dict(d)
    if "roles" in d and not isinstance(d["roles"], tuple):
        d["roles"] = tuple(d["roles"])
    return Identity(**d)


@dataclass
class AgentSpec:
    """The declarative shape of one Agent(...) call. `model` is Agent's own
    `model=` kwarg — AgentConfig actually stores it as `task`, LLMGateway's
    routing key (see orchestration.AgentConfig), not a literal model name."""

    name: str
    instructions: str
    model: str = "default"
    provider: str | None = None  # "anthropic" | "openai" -> LLMGateway(provider=...); None keeps Agent's own default
    # Each entry is a bare "module:function" ref (see resolve_tool) or a dict
    # {implementation, name?, description?, destructive?, timeout_s?,
    # max_retries?, permissions?, scopes?, requires_confirmation?} — see
    # _tool_from_entry. The dict form is what lets a spec express e.g.
    # destructive=True, which a bare string reference never could.
    tools: list[str | dict[str, Any]] = field(default_factory=list)
    policy: dict[str, Any] | None = None
    identity: dict[str, Any] | None = None
    memory: dict[str, Any] | None = None  # {"enabled": true} -> MemoryStore(); see module docstring
    runtime: str = "langgraph"
    workflow: str = "react"
    # {"evaluator": "groundedness"|"correctness"|..., "threshold": float,
    # "escalate_threshold": float?, "max_retries": int?} -> a real
    # CritiqueConfig, see _named_evaluator_kpi/_default_critique_context.
    # None (default): no critique gate, same as omitting Agent(critique=...).
    critique: dict[str, Any] | None = None
    eval: dict[str, Any] | None = None  # {"dataset": "path", "thresholds": {...}} — read by callers (e.g. `foundry eval`), not by build_agent
    serve: dict[str, Any] | None = None  # {"host": ..., "port": ...} — same, read by callers that serve this spec

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentSpec":
        known = {f.name for f in fields(cls)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"AgentSpec: unknown field(s) {sorted(unknown)} — known fields are {sorted(known)}")
        return cls(**data)

    @classmethod
    def from_json(cls, path: str | Path) -> "AgentSpec":
        return cls.from_dict(json.loads(Path(path).read_text()))

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AgentSpec":
        try:
            import yaml
        except ImportError as e:
            raise ImportError("AgentSpec.from_yaml needs PyYAML — install with `pip install agent-foundry[spec]`") from e
        return cls.from_dict(yaml.safe_load(Path(path).read_text()))


def build_agent(spec: AgentSpec, *, llm: LLMGateway | None = None) -> Agent:
    """The declarative-config equivalent of hand-calling Agent(...) — every
    AgentSpec field maps onto the matching Agent.__init__ kwarg (core/
    agent.py), reusing its own tool/memory coercion and defaulting as-is.
    No new agent-construction logic lives here.

    `llm=` is the one escape hatch to real Python objects a JSON/YAML spec
    can't itself express — an already-configured LLMGateway (custom cache,
    rate limiter, model registry) or, in tests, a scripted provider. When
    given, it wins outright and `spec.provider` is ignored."""
    if llm is None and spec.provider is not None:
        if spec.provider not in _PROVIDERS:
            raise ValueError(f"AgentSpec.provider {spec.provider!r} must be one of {sorted(_PROVIDERS)}")
        llm = LLMGateway(provider=_PROVIDERS[spec.provider]())

    critique_config = None
    if spec.critique:
        # Resolved eagerly (not left to Agent()'s own AnthropicProvider
        # default) so the critique judge and the agent's own completions
        # share the exact same LLMGateway, not two independently-defaulted
        # ones — matters for anything gateway-level: cache, rate limiter,
        # cost ledger.
        if llm is None:
            llm = LLMGateway(provider=AnthropicProvider())
        critique_config = CritiqueConfig(
            kpi=_named_evaluator_kpi(spec.critique["evaluator"], llm, threshold=spec.critique.get("threshold", 0.5)),
            context=_default_critique_context,
            escalate_threshold=spec.critique.get("escalate_threshold"),
            max_retries=spec.critique.get("max_retries", 0),
        )

    return Agent(
        spec.name,
        spec.instructions,
        model=spec.model,
        tools=[_tool_from_entry(entry) for entry in spec.tools] or None,
        memory=MemoryStore() if spec.memory and spec.memory.get("enabled") else None,
        policy=_policy_from_dict(spec.policy) if spec.policy else None,
        identity=_identity_from_dict(spec.identity) if spec.identity else None,
        workflow=spec.workflow,
        runtime=spec.runtime,
        critique=critique_config,
        llm=llm,
    )

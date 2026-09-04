"""Layer 04 — Tools Gateway: registry, schema-carrying specs, permission-scoped invocation.

Register any Python callable as a tool for any agent; the registry enforces
RBAC/policy scopes at call time regardless of what the tool does.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from .contracts import Identity, Policy, ToolResult, ToolSpec
from .runtime import RateLimiterLike, RateLimitExceeded


class PermissionDenied(Exception):
    pass


_JSON_TYPES = {"string", "number", "integer", "boolean", "array", "object"}


def tool_json_schema(spec: ToolSpec) -> dict[str, Any]:
    """Converts a ToolSpec into a *provider-agnostic* native tool definition:
    {"name", "description", "parameters"} — plain JSON Schema under "parameters",
    not nested under a vendor-specific key. Each Provider's complete() is
    responsible for translating this into its own wire shape (Anthropic wants
    "input_schema"; OpenAI wants {"type": "function", "function": {...}}) —
    that translation lives in llm_gateway.py, not here, so this stays correct
    for whichever provider LLMGateway is actually routed to. Accepts either
    loose docs-only params ({"order_id": "string"}) or a real JSON Schema
    object already (passed through unchanged if it has "type": "object")."""
    params = spec.parameters
    if params.get("type") == "object":
        schema = params
    else:
        properties = {k: {"type": v if v in _JSON_TYPES else "string"} for k, v in params.items()}
        schema = {"type": "object", "properties": properties, "required": list(properties)}
    return {"name": spec.name, "description": spec.description, "parameters": schema}


class ToolCacheLike(Protocol):
    """Same swappable-interface posture as runtime.RunBudgetLike — a cache
    is only useful across a fleet if every replica shares it (a Redis-
    backed cache, e.g.); ToolCache's in-process dict means N replicas each
    independently re-execute the same call at least once."""

    def get(self, name: str, args: dict) -> Any: ...
    def set(self, name: str, args: dict, result: Any) -> None: ...


@dataclass
class ToolCache:
    """Exact-match result cache keyed by (tool name, args) — same shape as
    llm_gateway.PromptCache, one per ToolRegistry. Skip caching tools with side
    effects (a refund) by simply not calling set() for them, or wrap invoke()
    with a per-tool allowlist if you want that automatic."""

    ttl_s: float = 60.0
    _store: dict[tuple, tuple[float, Any]] = field(default_factory=dict)

    def _key(self, name: str, args: dict) -> tuple:
        return (name, tuple(sorted(args.items())))

    def get(self, name: str, args: dict) -> Any:
        hit = self._store.get(self._key(name, args))
        if hit is None:
            return None
        ts, result = hit
        return None if time.time() - ts > self.ttl_s else result

    def set(self, name: str, args: dict, result: Any) -> None:
        self._store[self._key(name, args)] = (time.time(), result)


class IdempotencyStore(Protocol):
    def get(self, key: str) -> ToolResult | None: ...
    def set(self, key: str, result: ToolResult) -> None: ...


@dataclass
class InMemoryIdempotencyStore:
    """Keyed by a caller-supplied idempotency key (e.g. "refund-order-A100" —
    the caller decides what makes two calls "the same operation", this store
    just remembers the outcome). Only successful (ok=True) results are cached —
    a failed attempt should be retryable, not permanently stuck. This is what
    stops a retried `issue_refund` call (after a timeout, a flaky network) from
    executing the side effect twice."""

    ttl_s: float = 3600.0
    _store: dict[str, tuple[float, ToolResult]] = field(default_factory=dict)

    def get(self, key: str) -> ToolResult | None:
        hit = self._store.get(key)
        if hit is None:
            return None
        ts, result = hit
        return None if time.time() - ts > self.ttl_s else result

    def set(self, key: str, result: ToolResult) -> None:
        self._store[key] = (time.time(), result)


@dataclass
class ToolRegistry:
    _tools: dict[str, ToolSpec] = field(default_factory=dict)
    rate_limiter: RateLimiterLike | None = None
    cache: ToolCacheLike | None = None
    idempotency_store: IdempotencyStore | None = None

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        return self._tools[name]

    def has(self, name: str) -> bool:
        return name in self._tools

    def names(self) -> list[str]:
        return list(self._tools)

    def list_for(self, policy: Policy) -> list[str]:
        return [name for name in self._tools if name in policy.allowed_tools]

    def native_tools(self, policy: Policy) -> list[dict[str, Any]]:
        """Real provider-native tool definitions for whatever's in policy.allowed_tools
        — pass straight to LLMGateway.complete(tools=...) for native tool-calling."""
        return [tool_json_schema(self._tools[name]) for name in self.list_for(policy)]

    def invoke(self, name: str, args: dict, *, identity: Identity, policy: Policy, idempotency_key: str | None = None) -> ToolResult:
        if name not in policy.allowed_tools:
            raise PermissionDenied(f"{identity.id} is not permitted to call {name!r}")
        if idempotency_key is not None and self.idempotency_store is not None:
            cached = self.idempotency_store.get(idempotency_key)
            if cached is not None:
                return cached
        if self.cache is not None:
            cached = self.cache.get(name, args)
            if cached is not None:
                return ToolResult(tool=name, ok=True, output=cached, latency_ms=0.0)
        if self.rate_limiter is not None and not self.rate_limiter.allow(name):
            return ToolResult(tool=name, ok=False, error=str(RateLimitExceeded(f"rate limit exceeded for tool {name!r}")))
        spec = self.get(name)
        start = time.time()
        try:
            output = spec.fn(**args)
            if self.cache is not None:
                self.cache.set(name, args, output)
            result = ToolResult(tool=name, ok=True, output=output, latency_ms=(time.time() - start) * 1000)
            if idempotency_key is not None and self.idempotency_store is not None:
                self.idempotency_store.set(idempotency_key, result)
            return result
        except Exception as e:
            return ToolResult(tool=name, ok=False, error=str(e), latency_ms=(time.time() - start) * 1000)

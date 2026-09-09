"""Layer 04 — Tools Gateway: registry, schema-carrying specs, permission-scoped invocation.

Register any Python callable as a tool for any agent; the registry enforces
RBAC/policy scopes at call time regardless of what the tool does.
"""
from __future__ import annotations

import json
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


_SCHEMA_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str, "number": (int, float), "integer": int, "boolean": bool, "array": list, "object": dict,
}


def _validate_basic(schema: dict[str, Any], value: Any) -> str | None:
    """Dependency-free fallback: required keys present, basic type match.
    Not a full JSON Schema implementation (no $ref/oneOf/pattern/etc.) —
    real Draft 2020-12 validation happens automatically once `jsonschema`
    is installed (_validate_against_schema below lazily prefers it, same
    optional-dependency posture as chromadb/redis/cedarpy elsewhere in this
    package)."""
    expected_type = schema.get("type")
    if expected_type == "object":
        if not isinstance(value, dict):
            return f"expected an object, got {type(value).__name__}"
        properties = schema.get("properties", {})
        for required in schema.get("required", []):
            if required not in value:
                return f"missing required argument {required!r}"
        for key, sub_value in value.items():
            sub_type = properties.get(key, {}).get("type")
            py_type = _SCHEMA_TYPES.get(sub_type)
            if py_type is not None and not isinstance(sub_value, py_type):
                return f"argument {key!r} expected type {sub_type!r}, got {type(sub_value).__name__}"
        return None
    py_type = _SCHEMA_TYPES.get(expected_type)
    if py_type is not None and not isinstance(value, py_type):
        return f"expected type {expected_type!r}, got {type(value).__name__}"
    return None


def _validate_against_schema(schema: dict[str, Any], value: Any) -> str | None:
    """The one validation boundary used for both a tool call's input args
    (against tool_json_schema(spec)["parameters"]) and its declared
    ToolSpec.output_schema — until now the schema shown to the model was
    never actually checked against what the model (for args) or the tool
    (for output) produced. Returns None when `value` passes, else a
    human-readable reason."""
    try:
        import jsonschema
    except ImportError:
        return _validate_basic(schema, value)
    try:
        jsonschema.validate(value, schema)
        return None
    except jsonschema.exceptions.ValidationError as e:
        return e.message


def _validate_args(spec: ToolSpec, args: dict[str, Any]) -> str | None:
    """A real validation/coercion boundary between a model's tool-call args
    and spec.fn(**args) — the schema in tool_json_schema() is exposed to the
    model, but until now nothing checked the model actually followed it, so
    a missing/mistyped argument surfaced as a raw exception from inside the
    tool body instead of a clear, structured denial."""
    return _validate_against_schema(tool_json_schema(spec)["parameters"], args)


class ToolCacheLike(Protocol):
    """Same swappable-interface posture as runtime.RunBudgetLike — a cache
    is only useful across a fleet if every replica shares it (a Redis-
    backed cache, e.g.); ToolCache's in-process dict means N replicas each
    independently re-execute the same call at least once. `tenant` is
    optional (defaults to None) so a cache that doesn't need multi-tenant
    isolation can ignore it — ToolRegistry.invoke() always passes
    identity.tenant_id, closing the gap where two tenants sharing one
    registry/cache could otherwise read each other's cached tool results."""

    def get(self, name: str, args: dict, *, tenant: str | None = None) -> Any: ...
    def set(self, name: str, args: dict, result: Any, *, tenant: str | None = None) -> None: ...


@dataclass
class ToolCache:
    """Exact-match result cache keyed by (tenant, tool name, args) — same
    key shape as distributed.RedisToolCache. Skip caching tools with side
    effects (a refund) by setting ToolSpec.destructive=True — ToolRegistry.
    invoke() never calls set() for a destructive tool, cache configured or
    not — or ToolSpec.cacheable=False for a non-destructive tool that still
    shouldn't be cached (e.g. "get current time")."""

    ttl_s: float = 60.0
    _store: dict[str, tuple[float, Any]] = field(default_factory=dict)

    def _key(self, name: str, args: dict, tenant: str | None) -> str:
        # json.dumps (not the previous tuple(sorted(args.items()))) so a
        # list/dict-valued arg doesn't crash on being used as a dict key —
        # found live: any tool taking e.g. a list argument.
        return f"{tenant}:{name}:{json.dumps(args, sort_keys=True, default=str)}"

    def get(self, name: str, args: dict, *, tenant: str | None = None) -> Any:
        hit = self._store.get(self._key(name, args, tenant))
        if hit is None:
            return None
        ts, result = hit
        return None if time.time() - ts > self.ttl_s else result

    def set(self, name: str, args: dict, result: Any, *, tenant: str | None = None) -> None:
        self._store[self._key(name, args, tenant)] = (time.time(), result)


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
            cached = self.cache.get(name, args, tenant=identity.tenant_id)
            if cached is not None:
                return ToolResult(tool=name, ok=True, output=cached, latency_ms=0.0)
        if self.rate_limiter is not None and not self.rate_limiter.allow(name):
            return ToolResult(tool=name, ok=False, error=str(RateLimitExceeded(f"rate limit exceeded for tool {name!r}")))
        spec = self.get(name)
        invalid = _validate_args(spec, args)
        if invalid is not None:
            return ToolResult(tool=name, ok=False, error=f"invalid arguments for {name!r}: {invalid}")
        start = time.time()
        attempt = 0
        while True:
            try:
                output = spec.fn(**args)
                if spec.output_schema is not None:
                    # Same validation boundary as input args, applied to
                    # what the tool actually returned — previously
                    # ToolSpec.output_schema was pure metadata, never
                    # checked against anything. A schema-violating output
                    # is treated like any other execution failure (retried
                    # up to spec.max_retries, same as an exception).
                    invalid_output = _validate_against_schema(spec.output_schema, output)
                    if invalid_output is not None:
                        raise ValueError(f"tool {name!r} returned output that doesn't match its declared output_schema: {invalid_output}")
                if self.cache is not None and spec.cacheable and not spec.destructive:
                    self.cache.set(name, args, output, tenant=identity.tenant_id)
                result = ToolResult(tool=name, ok=True, output=output, latency_ms=(time.time() - start) * 1000)
                if idempotency_key is not None and self.idempotency_store is not None:
                    self.idempotency_store.set(idempotency_key, result)
                return result
            except Exception as e:
                if attempt >= spec.max_retries:
                    return ToolResult(tool=name, ok=False, error=str(e), latency_ms=(time.time() - start) * 1000)
                attempt += 1

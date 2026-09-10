"""Core — @tool: the ergonomic path to a governed ToolSpec, no ToolRegistry/
JSON-schema boilerplate. Real behavior, not just sugar: `timeout` enforces via
runtime.with_timeout (the same primitive orchestration.py's own
step_timeout_s uses), `cache_ttl` is a real exact-args-match cache.
`permissions` is inspectable metadata on the returned ToolSpec
(`spec.permissions`) for a caller's own RBAC check before registering into
Policy.allowed_tools — ToolRegistry.invoke()'s own enforcement is unchanged
(still the existing name-based allowed_tools allowlist).
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import time
from typing import Any, Callable, Iterable

from ..contracts import ToolSpec
from ..runtime import with_timeout
from .agent import _toolspec_from_callable


def tool(
    *,
    name: str | None = None,
    description: str | None = None,
    permissions: Iterable[str] = (),
    timeout: float | None = None,
    cache_ttl: float | None = None,
) -> Callable[[Callable[..., Any]], ToolSpec]:
    def decorator(fn: Callable[..., Any]) -> ToolSpec:
        cache: dict[tuple[tuple[str, Any], ...], tuple[float, Any]] = {}

        if inspect.iscoroutinefunction(fn):
            # `wrapped` must itself be `async def` — a plain sync wrapper
            # around an async fn would call fn(**kwargs) and return the
            # unawaited coroutine as the tool's "output" (silently wrong),
            # and inspect.iscoroutinefunction(wrapped) would read False even
            # though calling it returns a coroutine, hiding it from
            # ToolRegistry.invoke()'s own async-tool check (tools_gateway.py)
            # — the exact bug class that check exists to catch, reintroduced
            # one layer up. asyncio.wait_for (not with_timeout's thread-based
            # approach) times an in-flight coroutine out correctly.
            @functools.wraps(fn)
            async def wrapped(**kwargs: Any) -> Any:
                key = tuple(sorted(kwargs.items()))
                if cache_ttl is not None:
                    hit = cache.get(key)
                    if hit is not None and time.time() - hit[0] <= cache_ttl:
                        return hit[1]
                result = await fn(**kwargs) if timeout is None else await asyncio.wait_for(fn(**kwargs), timeout=timeout)
                if cache_ttl is not None:
                    cache[key] = (time.time(), result)
                return result
        else:
            @functools.wraps(fn)
            def wrapped(**kwargs: Any) -> Any:
                key = tuple(sorted(kwargs.items()))
                if cache_ttl is not None:
                    hit = cache.get(key)
                    if hit is not None and time.time() - hit[0] <= cache_ttl:
                        return hit[1]
                result = fn(**kwargs) if timeout is None else with_timeout(lambda: fn(**kwargs), seconds=timeout)
                if cache_ttl is not None:
                    cache[key] = (time.time(), result)
                return result

        spec = _toolspec_from_callable(wrapped, name=name, description=description)
        spec.permissions = frozenset(permissions)  # informational metadata — not a declared ToolSpec field
        return spec

    return decorator

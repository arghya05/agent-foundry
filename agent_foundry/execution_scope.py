"""Process-local request controls, independent of graph/checkpoint formats.

Live cancellation/budget handles must not enter serialized graph state. Public
entry points establish a scope; runtime resolvers and delegated threads consume
it. This is cooperative admission, not process isolation or durable revocation.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import inspect
import math
import time
from typing import Any, Mapping

from .contracts import Policy
from .runtime import BudgetExceeded, RunCancelled

_CURRENT: ContextVar[dict[str, Any]] = ContextVar('foundry_execution_scope', default={})


def request_value(state: Mapping[str, Any], key: str) -> Any:
    current = _CURRENT.get()
    return current[key] if key in current else state.get(key)


def intersect_policies(base: Policy, other: Policy) -> Policy:
    return Policy(allowed_tools=base.allowed_tools & other.allowed_tools,
                  requires_approval=base.requires_approval | other.requires_approval,
                  max_cost_usd_per_thread=min(base.max_cost_usd_per_thread, other.max_cost_usd_per_thread),
                  max_steps_per_thread=min(base.max_steps_per_thread, other.max_steps_per_thread),
                  autonomy=min(base.autonomy, other.autonomy))


class _CancellationGroup:
    def __init__(self, *tokens: Any):
        self.tokens = tokens

    def is_cancelled(self) -> bool:
        return any(t.is_cancelled() for t in self.tokens)


class _ScopedBudget:
    """Child calls charge the parent's bucket, even with different thread IDs."""
    def __init__(self, budgets: list[tuple[Any, str]]):
        self.budgets = list({(id(b), key): (b, key) for b, key in budgets}.values())

    def _apply(self, method: str, *args: Any) -> None:
        failure = None
        for budget, key in self.budgets:
            try:
                getattr(budget, method)(*args, thread_id=key)
            except Exception as exc:
                failure = failure or exc
        if failure is not None:
            raise failure

    def spend(self, amount: float, *, thread_id: str = '') -> None:
        self._apply('spend', amount)

    def step(self, *, thread_id: str = '') -> None:
        self._apply('step')

    def cost_usd_for(self, thread_id: str = '') -> float:
        return max(b.cost_usd_for(key) for b, key in self.budgets)

    def steps_for(self, thread_id: str = '') -> int:
        return max(b.steps_for(key) for b, key in self.budgets)


def check_admission(state: Mapping[str, Any] | None = None) -> None:
    state = state or {}
    cancellation = request_value(state, 'request_cancellation_token')
    if cancellation is not None and cancellation.is_cancelled():
        raise RunCancelled('run was cancelled')
    deadline = request_value(state, 'request_deadline')
    if deadline is not None:
        if not isinstance(deadline, (float, int)) or not math.isfinite(deadline):
            raise ValueError('deadline must be a finite Unix timestamp')
        if time.time() >= deadline:
            raise BudgetExceeded('run passed its deadline')


@contextmanager
def request_scope(values: Mapping[str, Any], *, thread_id: str):
    parent = _CURRENT.get()
    merged = {**parent, **values}
    for key in ('request_identity', 'request_tool_policy', 'request_model_policy',
                'request_cancellation_token', 'request_deadline'):
        if key not in parent or key not in values:
            continue
        old, new = parent[key], values[key]
        if key == 'request_identity':
            if (old['id'], old['tenant_id']) != (new['id'], new['tenant_id']):
                raise PermissionError('nested execution cannot replace the calling principal')
            merged[key] = {**new, 'roles': tuple(set(old.get('roles', ())) & set(new.get('roles', ())))}
        elif key == 'request_tool_policy':
            merged[key] = intersect_policies(old, new)
        elif key == 'request_model_policy':
            # Invalid policy shape is rejected by the model resolver.
            merged[key] = {'allowed_models': sorted(set(old.get('allowed_models', ())) & set(new.get('allowed_models', ())))}
        elif key == 'request_cancellation_token':
            merged[key] = _CancellationGroup(old, new)
        else:
            merged[key] = min(old, new)
    if 'request_budget' in values:
        inherited = parent.get('request_budget')
        entries = list(inherited.budgets) if isinstance(inherited, _ScopedBudget) else []
        entries.append((values['request_budget'], thread_id))
        merged['request_budget'] = _ScopedBudget(entries)
    token = _CURRENT.set(merged)
    try:
        check_admission()
        yield
    finally:
        _CURRENT.reset(token)


def scoped_execution(fn):
    """Apply identical request scope to sync, async, and streaming surfaces."""
    def scope(kwargs):
        from .core.execution_context import ExecutionContext
        context = kwargs.get('context') or ExecutionContext()
        kwargs['context'] = context
        return request_scope(context.to_request_state(), thread_id=context.resolved_thread_id())

    if inspect.isasyncgenfunction(fn):
        @wraps(fn)
        async def async_stream(*args, **kwargs):
            with scope(kwargs):
                iterator = fn(*args, **kwargs)
            try:
                while True:
                    with scope(kwargs):
                        try:
                            item = await iterator.__anext__()
                        except StopAsyncIteration:
                            break
                    yield item
            finally:
                await iterator.aclose()
        return async_stream
    if inspect.iscoroutinefunction(fn):
        @wraps(fn)
        async def asynchronous(*args, **kwargs):
            with scope(kwargs):
                return await fn(*args, **kwargs)
        return asynchronous
    if inspect.isgeneratorfunction(fn):
        @wraps(fn)
        def stream(*args, **kwargs):
            with scope(kwargs):
                iterator = fn(*args, **kwargs)
            try:
                while True:
                    with scope(kwargs):
                        try:
                            item = next(iterator)
                        except StopIteration:
                            break
                    yield item
            finally:
                iterator.close()
        return stream
    @wraps(fn)
    def synchronous(*args, **kwargs):
        with scope(kwargs):
            return fn(*args, **kwargs)
    return synchronous

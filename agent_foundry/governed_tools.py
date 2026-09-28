"""Explicit Foundry action boundary for external framework tool adapters.

Controls apply to each exported tool, not opaque nested-agent internals. Approval
requests fail closed; this gateway has no boolean approval bypass or durable
approval store. Authentication and downstream resource authorization are the
hosting application's responsibility.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from .contracts import ToolResult
from .core.execution_context import ExecutionContext
from .execution_scope import check_admission, scoped_execution
from .orchestration import (
    AgentConfig, _invoke_tool_call, _resolve_budget, _resolve_identity,
    _resolve_tool_policy, _tool_param_names, _tool_timeout,
)
from .runtime import BudgetExceeded
from .tools_gateway import PermissionDenied, _validate_against_schema, _validate_args


class ToolApprovalRequired(PermissionDenied):
    """Execution stopped before effects; use a supported approval workflow."""


class GovernedToolGateway:
    """Execute registered tools under a fresh trusted context on every call.

    Each admitted call consumes a step, including cached/invalid-input calls.
    No model call is made here; model costs in an external host need its own
    metered transport. Timeouts/cancellation remain cooperative. Trusted config
    and registry mutation must be serialized by the application.
    """

    capabilities = frozenset({'action_policy', 'request_identity', 'request_limits',
                              'input_output_schema', 'decision_audit',
                              'cooperative_cancellation', 'sync_async_tools'})

    def __init__(self, config: AgentConfig):
        self.config = config

    def require_capabilities(self, required: set[str] | frozenset[str]) -> None:
        missing = set(required) - self.capabilities
        if missing:
            raise ValueError(f'unsupported required tool capabilities: {sorted(missing)}')

    @scoped_execution
    def invoke(
        self, name: str, arguments: dict[str, Any], *, context: ExecutionContext,
        idempotency_key: str | None = None,
    ) -> ToolResult:
        if not context.user_id or not context.tenant_id:
            raise PermissionDenied('governed export requires a trusted user and tenant on every call')
        state = {'thread_id': context.resolved_thread_id()}
        identity = _resolve_identity(self.config, state)
        policy = _resolve_tool_policy(self.config, state)
        budget = _resolve_budget(self.config, state)
        thread_id = context.resolved_thread_id()
        audit = dict(tool=name, thread_id=thread_id, run_id=context.run_id, trace_id=context.trace_id)

        def deny(reason: str, *, approval: bool = False) -> None:
            self.config.audit.record(identity=identity, action='tool_admission',
                                     allowed=False, requires_approval=approval, reason=reason, **audit)
            raise (ToolApprovalRequired if approval else PermissionDenied)(reason)

        if not self.config.tools.has(name):
            deny('unknown registered tool')
        spec = self.config.tools.get(name)
        # JSON copy prevents a caller or external policy from mutating this
        # invocation's argument object while it is being authorized.
        args = json.loads(json.dumps(arguments, allow_nan=False))
        if not isinstance(args, dict):
            raise TypeError('tool arguments must be a JSON object')
        parameter_names = _tool_param_names(spec)
        for field, value in (('user_id', identity.id), ('tenant_id', identity.tenant_id),
                             ('session_id', thread_id)):
            if field in parameter_names:
                args[field] = value
        cost = budget.cost_usd_for(thread_id)
        if cost >= policy.max_cost_usd_per_thread:
            raise BudgetExceeded('tool request cost budget exhausted')
        if budget.steps_for(thread_id) >= policy.max_steps_per_thread:
            raise BudgetExceeded('tool request step budget exhausted')
        assert self.config.pdp is not None
        decision = self.config.pdp.decide(
            name, args, identity=identity, policy=policy, destructive=spec.destructive,
            cost_so_far=cost, hosts=spec.egress_hosts, scopes=spec.scopes,
            requires_confirmation=spec.requires_confirmation, data_classification=spec.data_classification,
        )
        if not decision.allowed:
            deny(decision.reason or 'action policy denied', approval=decision.requires_approval)
        if spec.destructive and spec.max_retries and not spec.idempotent:
            deny('non-idempotent destructive exports cannot automatically retry')
        if self.config.breaker.is_open(name):
            deny('tool circuit breaker is open')
        action = json.dumps({'tool': name, 'version': spec.version, 'arguments': args,
                             'user': identity.id, 'tenant': identity.tenant_id},
                            sort_keys=True, separators=(',', ':'), allow_nan=False)
        audit['action_sha256'] = hashlib.sha256(action.encode()).hexdigest()
        # Identity and payload binding prevents cross-tenant/payload collisions
        # in an optional idempotency store. It does not establish exactly-once.
        key = None if idempotency_key is None else hashlib.sha256(
            json.dumps([idempotency_key, action], separators=(',', ':')).encode()).hexdigest()
        check_admission()
        budget.step(thread_id=thread_id)
        self.config.audit.record(identity=identity, action='tool_admission', allowed=True, **audit)
        invalid = _validate_args(spec, args)
        if invalid is not None:
            result = ToolResult(name, False, error=f'invalid arguments: {invalid}')
        else:
            try:
                result = _invoke_tool_call(self.config, name, args, identity=identity, policy=policy,
                                           idem_key=key, timeout=_tool_timeout(self.config, spec))
            except Exception as exc:
                self.config.audit.record(identity=identity, action='tool_exception',
                                         error_type=type(exc).__name__, **audit)
                raise
        # Validate cached/idempotent receipts as well as fresh outputs.
        if result.ok and spec.output_schema is not None:
            invalid = _validate_against_schema(spec.output_schema, result.output)
            if invalid is not None:
                result = ToolResult(name, False, error=f'invalid output: {invalid}', latency_ms=result.latency_ms)
        self.config.breaker.record(name, result.ok)
        self.config.audit.record(identity=identity, action='tool_result', ok=result.ok, **audit)
        return result

    async def ainvoke(
        self, name: str, arguments: dict[str, Any], *, context: ExecutionContext,
        idempotency_key: str | None = None,
    ) -> ToolResult:
        # to_thread propagates contextvars and avoids blocking the host loop for
        # synchronous tools. The common executor handles coroutine tools too.
        return await asyncio.to_thread(self.invoke, name, arguments, context=context,
                                       idempotency_key=idempotency_key)

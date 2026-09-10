"""Core — ExecutionContext: the one object every layer (model, tool, memory,
policy, retrieval, audit, eval, events) can receive, instead of loose
thread_id/session_id/user_id/tenant_id strings threaded separately through
each layer and recomputed ad hoc at the top of every orchestration node.

Threaded through orchestration.py's nodes themselves too, via
to_request_state() — see that method's own docstring, and
orchestration.AgentState for why each field needs a matching declared key
there on the LangGraph engine specifically.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..contracts import Policy
from ..runtime import RunBudgetLike


class CancellationToken:
    """A cooperative stop signal for one run's think/act loop (both
    engines) — checked at loop-safe points (top of think(), top of act(),
    before each individual tool call), NOT preemptive: a call already in
    flight (an LLM request, a tool execution) still runs to completion;
    cancellation takes effect at the next checkpoint after that.
    core.run.Run.cancel() sets one of these automatically; construct and
    pass your own via ExecutionContext(cancellation_token=...) to cancel
    several runs together with one signal."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


@dataclass
class ExecutionContext:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    thread_id: str | None = None
    session_id: str | None = None
    user_id: str | None = None
    tenant_id: str | None = None
    agent_id: str | None = None
    trace_id: str | None = None
    locale: str | None = None
    channel: str | None = None
    permissions: frozenset[str] = field(default_factory=frozenset)
    # A RunBudgetLike (e.g. runtime.RunBudget) used INSTEAD OF the agent's
    # own static AgentConfig.budget for this one run, when set — e.g. a
    # per-customer spend cap tighter than the agent's default. None
    # (default): AgentConfig.budget governs, unchanged behavior for every
    # existing caller. See orchestration._resolve_budget.
    budget: RunBudgetLike | None = None
    # Absolute unix timestamp this run must finish by. Composes with (does
    # not replace) AgentConfig.step_timeout_s/latency_budget — those already
    # bound a single step/the whole session; this bounds THIS run against
    # the caller's own clock (e.g. an inbound HTTP request's own deadline).
    deadline: float | None = None
    cancellation_token: CancellationToken | None = None
    # Narrows (never widens) AgentConfig.policy for this run's tool calls
    # only — e.g. a stricter policy for a request arriving over an
    # untrusted channel. See orchestration._resolve_tool_policy for the
    # exact merge rule.
    tool_policy: Policy | None = None
    # {"allowed_models": [...]} narrows (never widens) which of
    # AgentConfig.llm.routes[task]'s models this run's LLM calls may use.
    # See orchestration._resolve_model_names.
    model_policy: dict[str, Any] | None = None
    # Overrides the key used for THIS run's semantic/RAG memory reads (not
    # cost/audit, which stay thread-scoped) — e.g. sharing retrieved
    # context across two otherwise-separate threads. None (default): the
    # existing session_id-keyed behavior. See orchestration._memory_key.
    memory_scope: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def resolved_thread_id(self) -> str:
        """What LangGraph's checkpointer actually keys persisted state on —
        thread_id if set, else session_id, else this context's own run_id."""
        return self.thread_id or self.session_id or self.run_id

    def to_request_state(self) -> dict[str, Any]:
        """Fields this context should seed into a fresh call's graph state,
        keyed request_<field> — read back by orchestration.py's
        _resolve_identity/_resolve_budget/_resolve_tool_policy/
        _resolve_model_names/_memory_key/_check_deadline/_check_cancellation.
        LangGraph-engine only for now: native_engine._NativeGraph.invoke
        forwards request_identity alone (see NativeEngine.run's
        request_identity param) — budget/deadline/cancellation_token/
        tool_policy/model_policy/memory_scope are silently no-ops on the
        native runtime until that engine grows matching _resolve_* support.
        Only includes fields the caller actually set, so a continuing
        thread's earlier turn isn't reset to "no override" just because a
        later turn's caller passed a plain ExecutionContext() with
        defaults."""
        out: dict[str, Any] = {}
        if self.user_id is not None or self.tenant_id is not None or self.permissions:
            out["request_identity"] = {"id": self.user_id or "unknown", "tenant_id": self.tenant_id or "", "roles": tuple(self.permissions)}
        if self.trace_id is not None:
            out["request_trace_id"] = self.trace_id
        if self.budget is not None:
            out["request_budget"] = self.budget
        if self.deadline is not None:
            out["request_deadline"] = self.deadline
        if self.cancellation_token is not None:
            out["request_cancellation_token"] = self.cancellation_token
        if self.tool_policy is not None:
            out["request_tool_policy"] = self.tool_policy
        if self.model_policy is not None:
            out["request_model_policy"] = self.model_policy
        if self.memory_scope is not None:
            out["request_memory_scope"] = self.memory_scope
        return out

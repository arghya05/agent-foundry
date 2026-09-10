"""Core — the two real WorkflowEngine implementations, existing specifically
to make core.protocols.WorkflowEngine a verified contract instead of
documentation. `Agent` itself does NOT route through these — it calls
build_agent_graph/_NativeGraph directly (unchanged, already tested) — these
wrap the exact same functions/classes behind the protocol's literal shape.

Both `.run`/`.stream`/`.resume` are identical code across the two engines:
both `.build()` outputs (a LangGraph compiled graph, or a _NativeGraph)
speak the same `.invoke(state_or_command, run_config)`/`.stream(...)`
calling convention — that's the whole reason core.agent._CompiledWorkflow
needs no engine-specific branching either.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..orchestration import AgentConfig, build_agent_graph
from .execution_context import ExecutionContext
from .native_engine import _NativeGraph
from .result import RunResult, result_from_graph_output


@dataclass
class _ResumePayload:
    """A langgraph-free stand-in for langgraph.types.Command(resume=...) —
    same shim core.agent._CompiledWorkflow uses, duplicated here rather than
    imported (this module is a separate, parallel WorkflowEngine
    implementation, not layered on core.agent — see this module's own
    docstring). native_engine._NativeGraph.invoke() duck-types on exactly
    the `.resume` attribute, never a real Command, so this satisfies it
    identically without requiring langgraph installed."""

    resume: dict[str, Any]


def _invoke(compiled: Any, *, message: str, context: ExecutionContext) -> RunResult:
    thread_id = context.resolved_thread_id()
    raw = compiled.invoke({"messages": [{"role": "user", "content": message}], "thread_id": thread_id}, {"configurable": {"thread_id": thread_id}})
    return result_from_graph_output(raw, thread_id=thread_id)


def _invoke_resume(compiled: Any, *, approved: bool, decision: dict[str, Any] | None = None, context: ExecutionContext) -> RunResult:
    thread_id = context.resolved_thread_id()
    # `approved` stays the default so every existing caller (resume(approved=...))
    # is untouched; `decision` layers in richer payloads (clarification
    # answers, an external event's data, a payment confirmation id) for
    # HITL/event resumes beyond a plain yes/no — both make_act_node's tool-
    # approval interrupt() and make_critique_node's escalation interrupt()
    # only ever read `.get("approved")` off this dict, so extra keys are
    # additive and never break either.
    payload = {"approved": approved, **(decision or {})}
    if hasattr(compiled, "ainvoke"):  # a real LangGraph compiled graph — needs a real Command
        from langgraph.types import Command
        command: Any = Command(resume=payload)
    else:
        command = _ResumePayload(resume=payload)
    raw = compiled.invoke(command, {"configurable": {"thread_id": thread_id}})
    return result_from_graph_output(raw, thread_id=thread_id)


class LangGraphWorkflowEngine:
    """WorkflowEngine over build_agent_graph. `spec` is an AgentConfig."""

    def build(self, spec: AgentConfig, *, checkpointer: Any = None) -> Any:
        return build_agent_graph(
            system_prompt=spec.system_prompt, llm=spec.llm, tools=spec.tools, guardrails=spec.guardrails,
            eval_harness=spec.eval_harness, identity=spec.identity, policy=spec.policy, budget=spec.budget,
            tracer=spec.tracer, task=spec.task, audit=spec.audit, breaker=spec.breaker, cost_ledger=spec.cost_ledger,
            memory=spec.memory, context_engine=spec.context_engine, step_timeout_s=spec.step_timeout_s,
            latency_budget=spec.latency_budget, sla_tracker=spec.sla_tracker, critique=spec.critique,
            user_id=spec.user_id, pdp=spec.pdp, checkpointer=checkpointer,
        )

    def run(self, compiled: Any, *, message: str, context: ExecutionContext) -> RunResult:
        return _invoke(compiled, message=message, context=context)

    def stream(self, compiled: Any, *, message: str, context: ExecutionContext) -> Any:
        thread_id = context.resolved_thread_id()
        return compiled.stream(
            {"messages": [{"role": "user", "content": message}], "thread_id": thread_id},
            {"configurable": {"thread_id": thread_id}}, stream_mode="values",
        )

    def resume(self, compiled: Any, *, approved: bool, decision: dict[str, Any] | None = None, context: ExecutionContext) -> RunResult:
        return _invoke_resume(compiled, approved=approved, decision=decision, context=context)


class NativeWorkflowEngine:
    """WorkflowEngine over native_engine._NativeGraph. `spec` is an
    AgentConfig — same input type as LangGraphWorkflowEngine.build(), a
    different `compiled` object comes out, and run/stream/resume don't care
    which, because both speak the same invoke/stream convention."""

    def build(self, spec: AgentConfig, *, checkpointer: Any = None) -> Any:
        if checkpointer is not None:
            raise ValueError("NativeWorkflowEngine keeps its own in-memory state and doesn't accept a checkpointer")
        return _NativeGraph(spec)

    def run(self, compiled: Any, *, message: str, context: ExecutionContext) -> RunResult:
        return _invoke(compiled, message=message, context=context)

    def stream(self, compiled: Any, *, message: str, context: ExecutionContext) -> Any:
        thread_id = context.resolved_thread_id()
        return compiled.stream(
            {"messages": [{"role": "user", "content": message}], "thread_id": thread_id},
            {"configurable": {"thread_id": thread_id}}, stream_mode="values",
        )

    def resume(self, compiled: Any, *, approved: bool, decision: dict[str, Any] | None = None, context: ExecutionContext) -> RunResult:
        return _invoke_resume(compiled, approved=approved, decision=decision, context=context)

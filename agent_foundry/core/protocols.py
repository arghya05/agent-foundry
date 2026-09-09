"""Core — the framework's protocol seam. Re-exports the protocols that already
exist under their established names elsewhere in the package — Evaluator
(eval.py), EventBus (events.py), PolicyEngine (policy_engine.py; the memo's
"Policy" role — the name `Policy` itself is already the dataclass in
contracts.py) — and adds the two that didn't exist yet: `Tool`/`Memory`
(restating ToolSpec's and MemoryStore's own established shapes as protocols,
so anything structurally matching — not just those two concrete types — is
accepted) and `WorkflowEngine`, the seam LangGraph now sits behind instead of
being the framework itself.
"""
from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable

from ..eval import Evaluator
from ..events import EventBus
from ..policy_engine import PolicyEngine
from .execution_context import ExecutionContext
from .result import RunResult

__all__ = ["Evaluator", "EventBus", "PolicyEngine", "Tool", "Memory", "WorkflowEngine"]


@runtime_checkable
class Tool(Protocol):
    """contracts.ToolSpec's own shape, restated as a protocol — anything with
    these four attributes (a hand-written class, not just the ToolSpec
    dataclass) is a valid tool to core.agent._coerce_tools."""

    name: str
    description: str
    parameters: dict[str, Any]
    fn: Callable[..., Any]


@runtime_checkable
class Memory(Protocol):
    """context.MemoryStore's own public surface, restated as a protocol —
    the four operations orchestration.py's node closures actually call on
    AgentConfig.memory (profiles + episodic history)."""

    def get_profile(self, user_id: str) -> dict[str, Any]: ...
    def update_profile(self, user_id: str, **fields: Any) -> None: ...
    def append_turn(self, thread_id: str, turn: dict) -> None: ...
    def history(self, thread_id: str) -> list[dict]: ...


@runtime_checkable
class WorkflowEngine(Protocol):
    """One runtime that can build a workflow from a spec and run/stream/resume
    it against an ExecutionContext. Two real implementations exist —
    core.engines.LangGraphWorkflowEngine and .NativeWorkflowEngine — see that
    module; `Agent` itself doesn't route through either (it calls
    build_agent_graph/_NativeGraph directly, unchanged, to avoid touching
    already-tested code paths) — these are the seam's verification-facing
    adapters, proving `isinstance(engine, WorkflowEngine)` is real for both
    engines, not just documentation. (This class was NOT `@runtime_checkable`
    until this was actually checked — isinstance() against it used to raise
    TypeError instead of returning False.)"""

    def build(self, spec: Any, *, checkpointer: Any = None) -> Any: ...
    def run(self, compiled: Any, *, message: str, context: ExecutionContext) -> RunResult: ...
    def stream(self, compiled: Any, *, message: str, context: ExecutionContext) -> Any: ...
    def resume(self, compiled: Any, *, approved: bool, decision: dict[str, Any] | None = None, context: ExecutionContext) -> RunResult: ...

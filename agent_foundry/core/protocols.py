"""Core — the framework's protocol seam. Re-exports the protocols that already
exist under their established names elsewhere in the package — Evaluator
(eval.py), EventBus (events.py), PolicyEngine (policy_engine.py — a distinct
role from the name `Policy`, which is already the dataclass in
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

__all__ = ["Evaluator", "EventBus", "PolicyEngine", "Tool", "Memory", "WorkflowEngine", "StateStore"]


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
    module. `Agent.__init__` (core/agent.py) actually dispatches through
    this: `RUNTIMES[runtime].build(...)`, not an `if runtime == "native":
    ... else: ...` special case — so `isinstance(engine, WorkflowEngine)`
    being real for both engines isn't just documentation, it's the literal
    lookup a new backend (Temporal, say) gets added to by implementing this
    Protocol and adding one RUNTIMES entry, with zero changes to Agent
    itself. (`@runtime_checkable` is required here — without it,
    `isinstance()` against a Protocol class raises `TypeError` instead of
    returning `False`, a real gotcha with Protocol classes.)"""

    def build(self, spec: Any, *, checkpointer: Any = None) -> Any: ...
    def run(self, compiled: Any, *, message: str, context: ExecutionContext) -> RunResult: ...
    def stream(self, compiled: Any, *, message: str, context: ExecutionContext) -> Any: ...
    def resume(self, compiled: Any, *, approved: bool, decision: dict[str, Any] | None = None, context: ExecutionContext) -> RunResult: ...


@runtime_checkable
class StateStore(Protocol):
    """Durable per-thread state, keyed the same way core/native_engine.py's
    NativeEngine._threads already is (one dict per thread_id, shaped like
    AgentState). `core/state_store.py`'s `MemoryStateStore` is the one real
    implementation today — a process-restart-durable Redis/Postgres backend
    is a real, separate piece of work (connection lifecycle, error handling,
    its own test suite against a live process — the same bar this repo's
    other real-external-process integrations hold themselves to, not an
    untested guess) that this Protocol makes possible without touching
    NativeEngine's own state shape, but doesn't itself ship.

    Deliberately 3 methods, not 4 — no separate `checkpoint()` alongside
    `save()`: for a plain per-thread state dict there's no distinct
    "durable snapshot" operation that isn't just "save now," so a fourth
    method would be a name with no different behavior behind it.

    Deliberately sync, not async, matching every other Protocol in this
    module (`Tool`/`Memory`/`WorkflowEngine` above) — this codebase's async
    surface (`Agent.arun`/`.astream`/`.aresume`) is a non-blocking layer
    wrapped around sync primitives (`asyncio.to_thread` for native,
    LangGraph's own off-thread scheduling for langgraph), not an
    async-native rewrite of the core loop; an async-only StateStore would
    be the one place that decision got silently reversed.

    Not yet wired into NativeEngine's own state storage (`_state_for`,
    `run`, `resume`, `update_state` in native_engine.py) — those methods'
    thread-safety is a lock-guarded plain in-process dict specifically
    (see NativeEngine's class docstring), and swapping the storage backing
    underneath that lock is real surgery on a hot, carefully-documented
    path that deserves its own dedicated change and test pass, not a rider
    on an unrelated batch of fixes."""

    def load(self, run_id: str) -> dict[str, Any] | None: ...
    def save(self, run_id: str, state: dict[str, Any]) -> None: ...
    def delete(self, run_id: str) -> None: ...

"""Core — Run: a formal lifecycle around one thread's execution, on top of
Agent.run()/.resume() (unchanged, still return a plain RunResult — this is
purely additive). `Agent.start(message)` is the new entry point that returns
one of these instead of a bare RunResult.

Verified empirically before building fork()/retry() (see the exchange this
was built from): LangGraph's `update_state()` on a reducer-typed channel
(`messages: Annotated[list, operator.add]`) APPENDS rather than replaces —
tested live: seeding an already-invoked thread with a shorter "replacement"
list just concatenates it onto the existing history. Truncating history
in-place for retry() is therefore unsafe on the LangGraph engine. The
verified-safe primitive instead: seed a BRAND-NEW thread via update_state()
*before* any invoke() on it — appending onto empty is exactly equivalent to
setting — which is what fork() does. retry() is built on fork(), not on
truncation, specifically to avoid this trap on both engines uniformly
(NativeEngine's own state IS a plain dict with no reducer, so a truncating
assign would actually be safe there — but building retry() on the one
primitive verified safe on *both* engines keeps the two genuinely
interchangeable, matching core.protocols.WorkflowEngine's whole point).

Honest scope: RunStatus.WAITING_TOOL is reserved but never produced by any
code path today — tool execution is synchronous in both engines, so there's
no "waiting on tool completion" state distinct from RUNNING. WAITING_EVENT
IS real and reachable, via .wait_for_event().
"""
from __future__ import annotations

import uuid
from dataclasses import replace
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

from ..events import EventBus
from .execution_context import ExecutionContext
from .result import RunResult

if TYPE_CHECKING:
    from .agent import Agent


class RunStatus(str, Enum):
    STARTED = "started"
    RUNNING = "running"
    WAITING_TOOL = "waiting_tool"  # reserved — see module docstring
    WAITING_HUMAN = "waiting_human"
    WAITING_EVENT = "waiting_event"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL = (RunStatus.FAILED, RunStatus.CANCELLED)  # COMPLETED is "this turn is done", not "dead" — .run() stays callable


class Run:
    """One thread's execution, with a formal status instead of just a
    RunResult. Create via Agent.start(message) — Agent.run()/.resume()
    themselves are unchanged."""

    def __init__(self, *, agent: "Agent", context: ExecutionContext) -> None:
        self.run_id = context.run_id
        self.agent = agent
        self.context = context
        self.status = RunStatus.STARTED
        self.result: RunResult | None = None
        self.error: str | None = None
        self._last_message: str | None = None
        self._pre_turn_messages: list[dict[str, Any]] = []
        self._paused_from: RunStatus | None = None
        self._event_subscription: tuple[EventBus, str] | None = None

    def _apply(self, result: RunResult) -> RunResult:
        self.result = result
        self.status = RunStatus.WAITING_HUMAN if result.awaiting_approval else RunStatus.COMPLETED
        return result

    # ---- driving the run ---------------------------------------------------------

    def run(self, message: str) -> RunResult:
        if self.status == RunStatus.CANCELLED:
            raise RuntimeError(f"run {self.run_id!r} was cancelled")
        if self.status == RunStatus.SUSPENDED:
            raise RuntimeError(f"run {self.run_id!r} is suspended — call .unpause() first")
        self._pre_turn_messages = list(self.result.messages) if self.result is not None else []
        self._last_message = message
        self.status = RunStatus.RUNNING
        try:
            return self._apply(self.agent.run(message, context=self.context))
        except Exception as e:
            self.status = RunStatus.FAILED
            self.error = str(e)
            raise

    def resume(self, *, approved: bool, decision: dict[str, Any] | None = None) -> RunResult:
        if self.status != RunStatus.WAITING_HUMAN:
            raise RuntimeError(f"run {self.run_id!r} has nothing to resume (status={self.status.value})")
        self.status = RunStatus.RUNNING
        try:
            return self._apply(self.agent.resume(approved=approved, decision=decision, context=self.context))
        except Exception as e:
            self.status = RunStatus.FAILED
            self.error = str(e)
            raise

    # ---- lifecycle control -----------------------------------------------------

    def pause(self) -> None:
        """Freezes this run in place — .run()/.resume() refuse until
        .unpause(). Meaningful between turns (e.g. "freeze this conversation
        pending a compliance review"), not a mid-flight interrupt of an
        actively executing turn — both engines run one turn synchronously to
        completion or to a HITL pause, there's no partial-turn state to
        suspend."""
        if self.status in _TERMINAL:
            raise RuntimeError(f"can't pause a {self.status.value} run")
        self._paused_from = self.status
        self.status = RunStatus.SUSPENDED

    def unpause(self) -> None:
        if self.status != RunStatus.SUSPENDED:
            raise RuntimeError(f"run {self.run_id!r} is not suspended")
        self.status = self._paused_from or RunStatus.WAITING_HUMAN
        self._paused_from = None

    def cancel(self) -> None:
        if self._event_subscription is not None:
            self._event_subscription = None  # InMemoryEventBus/EventBus has no unsubscribe; drop our own reference so the closure becomes a no-op below
        self.status = RunStatus.CANCELLED

    def retry(self) -> "Run":
        """Re-attempts the last turn as a fresh Run, forked from the state
        BEFORE that turn — for a turn that raised (FAILED) or one you simply
        want to re-attempt (a flaky provider). Does not mutate the original
        thread or Run; returns the new, already-run attempt as its own Run
        (`.result`/`.status` reflect the retry) so you can keep going on it,
        fork it again, or discard it."""
        if self._last_message is None:
            raise RuntimeError("nothing to retry — call .run() at least once first")
        forked = self.fork(messages=self._pre_turn_messages)
        forked.run(self._last_message)
        return forked

    def fork(self, *, messages: list[dict[str, Any]] | None = None) -> "Run":
        """A new Run on a brand-new thread, seeded with `messages` (default:
        this run's current message history) — the original run is untouched
        and can keep going independently. Works identically on both engines
        (see module docstring for why this exact seed-a-fresh-thread
        approach, not in-place truncation)."""
        source = messages if messages is not None else (self.result.messages if self.result else [])
        new_thread_id = f"{self.context.resolved_thread_id()}-fork-{uuid.uuid4().hex[:8]}"
        new_context = replace(self.context, thread_id=new_thread_id, run_id=uuid.uuid4().hex)
        if source:
            self.agent.graph.update_state({"configurable": {"thread_id": new_thread_id}}, {"messages": list(source), "thread_id": new_thread_id})
        new_run = Run(agent=self.agent, context=new_context)
        if source:
            new_run.status = RunStatus.COMPLETED
            new_run.result = RunResult(content=source[-1].get("content", ""), messages=list(source), thread_id=new_thread_id, raw={"messages": list(source), "thread_id": new_thread_id})
        return new_run

    def replay(self) -> list[dict[str, Any]]:
        """The full recorded transcript for this run so far — for audit/
        debugging, not a deterministic from-a-past-checkpoint re-execution
        (this codebase's MemorySaver-equivalent checkpointers don't retain
        per-step history for either engine)."""
        return list(self.result.messages) if self.result else []

    def wait_for_event(self, topic: str, *, bus: EventBus, on_event: Callable[[dict[str, Any]], str] | None = None) -> None:
        """Suspends this run in WAITING_EVENT until `topic` fires on `bus`,
        then automatically runs the agent with the event turned into a
        message (default: str(event); pass on_event to customize) and
        updates status/result the same way .run() would."""
        if self.status in _TERMINAL:
            raise RuntimeError(f"can't wait for an event on a {self.status.value} run")
        self.status = RunStatus.WAITING_EVENT
        to_message = on_event or (lambda event: str(event))

        def handler(event: dict[str, Any]) -> None:
            if self.status != RunStatus.WAITING_EVENT:
                return  # cancelled/moved on since subscribing — ignore a late event
            self._event_subscription = None
            self.run(to_message(event))

        bus.subscribe(topic, handler)
        self._event_subscription = (bus, topic)

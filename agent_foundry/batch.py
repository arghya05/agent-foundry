"""Batch & Schedule runner — the "Batch/Schedule Processing" chip. Distinct from
orchestration.build_fanout_graph: fanout parallelizes sub-tasks *within* one live
turn of one graph; run_batch runs a compiled graph *N separate times* over N
independent items (offline scoring, a nightly re-classification job, bulk
outreach) — separate threads, separate checkpointer state, no shared turn.

IntervalScheduler is the zero-dependency default for "run this every N seconds."
It is not a cron-expression engine — swap in `croniter` behind the same
Scheduler protocol if a team needs real cron syntax; unbuilt here on purpose
rather than faked with a heuristic parser.
"""
from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


@dataclass
class BatchItemResult:
    item_id: str
    ok: bool
    output: Any = None
    error: str | None = None
    latency_ms: float = 0.0


@dataclass
class BatchReport:
    results: list[BatchItemResult]

    @property
    def success_rate(self) -> float:
        return sum(1 for r in self.results if r.ok) / len(self.results) if self.results else 0.0


def run_batch(
    graph: Any,
    items: list[dict[str, Any]],
    *,
    thread_id_fn: Callable[[dict[str, Any]], str] | None = None,
    max_workers: int = 4,
) -> BatchReport:
    """Each item is a dict with at least a "message" key. thread_id_fn derives a
    distinct thread id per item (default: a fresh uuid per item, so items never
    share checkpointer state with each other)."""
    thread_id_fn = thread_id_fn or (lambda item: f"batch-{uuid.uuid4().hex}")

    def run_one(item: dict[str, Any]) -> BatchItemResult:
        thread_id = thread_id_fn(item)
        start = time.time()
        try:
            result = graph.invoke(
                {"messages": [{"role": "user", "content": item["message"]}], "thread_id": thread_id},
                {"configurable": {"thread_id": thread_id}},
            )
            return BatchItemResult(item_id=thread_id, ok=True, output=result["messages"][-1]["content"], latency_ms=(time.time() - start) * 1000)
        except Exception as e:
            return BatchItemResult(item_id=thread_id, ok=False, error=str(e), latency_ms=(time.time() - start) * 1000)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        results = list(executor.map(run_one, items))
    return BatchReport(results=results)


class Scheduler(Protocol):
    def schedule(self, fn: Callable[[], None], *, every_seconds: float) -> str: ...
    def cancel(self, handle: str) -> None: ...


@dataclass
class IntervalScheduler:
    """Recurring-job scheduler: runs `fn` every `every_seconds` on a background
    daemon thread until cancel()'d. A real threading.Timer chain, genuinely
    fires on a wall-clock interval — not a mocked loop."""

    _timers: dict[str, threading.Timer] = field(default_factory=dict)
    _cancelled: set[str] = field(default_factory=set)

    def schedule(self, fn: Callable[[], None], *, every_seconds: float) -> str:
        handle = uuid.uuid4().hex

        def tick() -> None:
            if handle in self._cancelled:
                return
            fn()
            if handle not in self._cancelled:
                self._arm(handle, tick, every_seconds)

        self._arm(handle, tick, every_seconds)
        return handle

    def _arm(self, handle: str, tick: Callable[[], None], every_seconds: float) -> None:
        timer = threading.Timer(every_seconds, tick)
        timer.daemon = True
        self._timers[handle] = timer
        timer.start()

    def cancel(self, handle: str) -> None:
        self._cancelled.add(handle)
        timer = self._timers.get(handle)
        if timer is not None:
            timer.cancel()

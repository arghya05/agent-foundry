"""Core — StateStore reference implementation. See protocols.StateStore's own
docstring for the shape/scope contract (3 sync methods, why it's sync, why
it's not yet wired into NativeEngine).

MemoryStateStore is a standalone, tested building block today — not a
drop-in replacement for anything native_engine.py already does (that engine
keeps its own in-process dict, unchanged). It exists so the seam itself is
real and verifiable (`isinstance(MemoryStateStore(), StateStore)` — proof
the same way core.engines.RUNTIMES proved WorkflowEngine was a real seam
before Agent.__init__ was wired through it) ahead of a real Redis/Postgres
backend, which is separate, larger work: connection lifecycle, error
handling, and its own test suite against a live process, not something to
guess at inline here.
"""
from __future__ import annotations

import copy
import threading
from typing import Any


class MemoryStateStore:
    """The in-process reference implementation — literally the same shape
    NativeEngine._threads already keeps (one dict per run_id), just exposed
    as a standalone StateStore instead of private to that one engine.
    save()/load() deep-copy (not just the top-level dict — state here nests
    a list of message dicts) so a caller mutating what load() returned, or
    later mutating the object it passed to save(), can never reach back into
    this store's own storage — the same guarantee a real Redis/Postgres
    backend gets for free from serializing on the way in and out. No
    persistence across a process restart — that's what a Redis/Postgres-
    backed StateStore is for."""

    def __init__(self) -> None:
        self._states: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def load(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._states.get(run_id)
            return copy.deepcopy(state) if state is not None else None

    def save(self, run_id: str, state: dict[str, Any]) -> None:
        with self._lock:
            self._states[run_id] = copy.deepcopy(state)

    def delete(self, run_id: str) -> None:
        with self._lock:
            self._states.pop(run_id, None)

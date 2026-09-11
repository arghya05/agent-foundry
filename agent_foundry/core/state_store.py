"""Core — StateStore implementations. See protocols.StateStore's own
docstring for the shape/scope contract (3 sync methods, why it's sync).

MemoryStateStore is the in-process reference implementation — not a
drop-in replacement for anything native_engine.py already does on its own
(NativeEngine keeps its own in-process `self._threads` cache regardless of
which store, if any, sits underneath it — see NativeEngine's own docstring).
It exists so the seam itself is real and verifiable
(`isinstance(MemoryStateStore(), StateStore)` — proof the same way
core.engines.RUNTIMES proved WorkflowEngine was a real seam before
Agent.__init__ was wired through it).

PostgresStateStore is the real cross-restart backend with no existing
sibling convention in this repo to follow (no psycopg/sqlalchemy usage
anywhere else) — see RedisStateStore in distributed.py (that module's own
established home for every other Redis-backed swappable primitive) for the
other real backend. Both are wired the identical way: `Agent(...,
runtime="native", state_store=<either one>)`.
"""
from __future__ import annotations

import copy
import json
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
    persistence across a process restart — that's what RedisStateStore/
    PostgresStateStore are for."""

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


class PostgresStateStore:
    """core.protocols.StateStore, Postgres-backed. `pip install
    agent-foundry[postgres]` (`psycopg2-binary`, lazily imported — not a
    base dependency). One table, created on first use if missing (`CREATE
    TABLE IF NOT EXISTS`, not a migration system — this is a 3-method
    key/value contract, not a schema with relationships): `run_id TEXT
    PRIMARY KEY, state JSONB`. `save()` is `INSERT ... ON CONFLICT (run_id)
    DO UPDATE` — an upsert, matching MemoryStateStore's own "save overwrites
    a previous save for the same run_id" contract exactly (see
    tests/test_state_store.py's own contract tests, run against this class
    too)."""

    def __init__(self, *, dsn: str = "dbname=agent_foundry", table: str = "agent_foundry_state"):
        import psycopg2

        self._table = table
        self._conn = psycopg2.connect(dsn)
        self._conn.autocommit = True
        with self._conn.cursor() as cur:
            cur.execute(f"CREATE TABLE IF NOT EXISTS {self._table} (run_id TEXT PRIMARY KEY, state JSONB NOT NULL)")

    def load(self, run_id: str) -> dict[str, Any] | None:
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT state FROM {self._table} WHERE run_id = %s", (run_id,))
            row = cur.fetchone()
        return None if row is None else (row[0] if isinstance(row[0], dict) else json.loads(row[0]))

    def save(self, run_id: str, state: dict[str, Any]) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {self._table} (run_id, state) VALUES (%s, %s) "
                f"ON CONFLICT (run_id) DO UPDATE SET state = EXCLUDED.state",
                (run_id, json.dumps(state)),
            )

    def delete(self, run_id: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute(f"DELETE FROM {self._table} WHERE run_id = %s", (run_id,))

"""Native state stores. Memory and local SQLite implement atomic versioned
writes; Postgres and distributed.RedisStateStore retain the legacy single-writer
contract. State consistency does not establish ownership of external effects.
See docs/STATE_CONSISTENCY.md for deployment boundaries and migration notes.
"""
from __future__ import annotations

import copy
import json
import threading
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any


class StateConflict(RuntimeError):
    """A newer state version exists. Do not blindly replay external effects."""

    def __init__(self, run_id: str, expected: int, actual: int) -> None:
        self.run_id, self.expected_version, self.actual_version = run_id, expected, actual
        super().__init__(f"state conflict for {run_id!r}: expected version {expected}, found {actual}")


class MemoryStateStore:
    """The in-process reference implementation — literally the same shape
    NativeEngine._threads already keeps (one dict per run_id), just exposed
    as a standalone StateStore instead of private to that one engine.
    save()/load() deep-copy (not just the top-level dict — state here nests
    a list of message dicts) so a caller mutating what load() returned, or
    later mutating the object it passed to save(), can never reach back into
    this store's own storage — the same guarantee a real Redis/Postgres
    backend gets for free from serializing on the way in and out. No
    persistence across a process restart. Versioned reads and compare-and-swap
    protect state shared by engines using THIS store instance; SQLite provides
    the same version contract across local processes."""

    def __init__(self) -> None:
        self._states: dict[str, dict[str, Any]] = {}
        self._versions: dict[str, int] = {}
        self._lock = threading.Lock()

    def load(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._states.get(run_id)
            return copy.deepcopy(state) if state is not None else None

    def save(self, run_id: str, state: dict[str, Any]) -> None:
        with self._lock:
            self._states[run_id] = copy.deepcopy(state)
            self._versions[run_id] = self._versions.get(run_id, 0) + 1

    def delete(self, run_id: str) -> None:
        with self._lock:
            self._states.pop(run_id, None)
            self._versions[run_id] = self._versions.get(run_id, 0) + 1

    def load_versioned(self, run_id: str) -> tuple[dict[str, Any] | None, int]:
        with self._lock:
            return copy.deepcopy(self._states.get(run_id)), self._versions.get(run_id, 0)

    def compare_and_swap(self, run_id: str, state: dict[str, Any], *, expected_version: int) -> int:
        with self._lock:
            actual = self._versions.get(run_id, 0)
            if actual != expected_version:
                raise StateConflict(run_id, expected_version, actual)
            self._states[run_id] = copy.deepcopy(state)
            self._versions[run_id] = actual + 1
            return actual + 1


class SQLiteStateStore:
    """Versioned, JSON state on a local disk, shared by processes on one host.

    Each operation opens/closes its own connection. BEGIN IMMEDIATE serializes
    the read/check/write transaction; a stale compare-and-swap rolls back.
    Deleted rows retain their revision. Default SQLite full synchronization
    is used; deployment storage durability remains the operator's responsibility.
    This is not a network-filesystem, distributed-ownership or tool-effect journal.
    """

    def __init__(self, path: str | Path, *, timeout_s: float = 5.0) -> None:
        if str(path) in ("", ":memory:"):
            raise ValueError("SQLiteStateStore requires a persistent file path")
        self.path = str(Path(path).expanduser().resolve())
        self.timeout_s = timeout_s
        with closing(self._connect()) as conn, conn:
            conn.execute("CREATE TABLE IF NOT EXISTS agent_foundry_state "
                         "(run_id TEXT PRIMARY KEY, state TEXT, version INTEGER NOT NULL)")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=self.timeout_s)

    def load(self, run_id: str) -> dict[str, Any] | None:
        return self.load_versioned(run_id)[0]

    def load_versioned(self, run_id: str) -> tuple[dict[str, Any] | None, int]:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT state, version FROM agent_foundry_state WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            return None, 0
        return (None if row[0] is None else json.loads(row[0])), int(row[1])

    def _write(self, run_id: str, state: dict[str, Any] | None, expected: int | None) -> int:
        encoded = None if state is None else json.dumps(state, allow_nan=False)
        with closing(self._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT version FROM agent_foundry_state WHERE run_id = ?", (run_id,)).fetchone()
            actual = 0 if row is None else int(row[0])
            if expected is not None and actual != expected:
                raise StateConflict(run_id, expected, actual)
            conn.execute("INSERT INTO agent_foundry_state (run_id, state, version) VALUES (?, ?, ?) "
                         "ON CONFLICT(run_id) DO UPDATE SET state=excluded.state, version=excluded.version",
                         (run_id, encoded, actual + 1))
        return actual + 1

    def save(self, run_id: str, state: dict[str, Any]) -> None:
        self._write(run_id, state, None)

    def delete(self, run_id: str) -> None:
        self._write(run_id, None, None)

    def compare_and_swap(self, run_id: str, state: dict[str, Any], *, expected_version: int) -> int:
        return self._write(run_id, state, expected_version)


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
    too).

    A real ThreadedConnectionPool backs this, not one shared connection —
    ThreadedConnectionPool is psycopg2's own thread-safe pool variant (unlike
    plain SimpleConnectionPool), so getconn()/putconn() need no lock of ours
    on top: concurrent calls for DIFFERENT run_ids get genuinely different
    connections and run in real parallel against Postgres, instead of
    queuing behind one Python-level lock the way an earlier version of this
    class did. `minconn`/`maxconn` bound how many real Postgres connections
    this ever opens — size `maxconn` to your actual concurrency, not
    unboundedly (Postgres itself has a connection ceiling). Call `close()`
    when you're done with this store (process shutdown, end of a test) to
    release every pooled connection — the pool doesn't do this for you on
    garbage collection the way a single connection object more casually
    might."""

    def __init__(
        self, *, dsn: str = "dbname=agent_foundry", table: str = "agent_foundry_state", minconn: int = 1, maxconn: int = 10,
    ):
        from psycopg2 import sql
        from psycopg2.pool import ThreadedConnectionPool

        self._table_ident = sql.Identifier(table)  # never f-string a table name into raw SQL — table= is caller-supplied
        self._pool = ThreadedConnectionPool(minconn, maxconn, dsn)
        conn = self._pool.getconn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(sql.SQL("CREATE TABLE IF NOT EXISTS {} (run_id TEXT PRIMARY KEY, state JSONB NOT NULL)").format(self._table_ident))
        finally:
            self._pool.putconn(conn)

    def close(self) -> None:
        self._pool.closeall()

    def load(self, run_id: str) -> dict[str, Any] | None:
        from psycopg2 import sql

        conn = self._pool.getconn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(sql.SQL("SELECT state FROM {} WHERE run_id = %s").format(self._table_ident), (run_id,))
                row = cur.fetchone()
        finally:
            self._pool.putconn(conn)
        return None if row is None else (row[0] if isinstance(row[0], dict) else json.loads(row[0]))

    def save(self, run_id: str, state: dict[str, Any]) -> None:
        from psycopg2 import sql

        conn = self._pool.getconn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    sql.SQL("INSERT INTO {} (run_id, state) VALUES (%s, %s) ON CONFLICT (run_id) DO UPDATE SET state = EXCLUDED.state")
                    .format(self._table_ident),
                    (run_id, json.dumps(state)),
                )
        finally:
            self._pool.putconn(conn)

    def delete(self, run_id: str) -> None:
        from psycopg2 import sql

        conn = self._pool.getconn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(sql.SQL("DELETE FROM {} WHERE run_id = %s").format(self._table_ident), (run_id,))
        finally:
            self._pool.putconn(conn)

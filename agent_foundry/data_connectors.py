"""Data Layer connector — structured data sources (SQL databases, warehouses,
business systems), distinct from context.py's unstructured RAG/vector search.
This is the "any data source" input side: give an agent read access to a real
table, not just documents.

SQLiteDataSource is the zero-dependency reference implementation. Any DB-API
2.0-shaped connection (psycopg2 for Postgres, mysql-connector, a Snowflake/
BigQuery client wrapped to match) implements the same DataSource.query() shape
and plugs in identically — nothing else in the framework changes.
"""
from __future__ import annotations

import sqlite3
import threading
from typing import Any, Protocol

from .contracts import ToolSpec


class DataSource(Protocol):
    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]: ...


class SQLiteDataSource:
    """Real bug, found only once this was actually served over real HTTP (a
    ScriptedProvider/direct-call test never exercises this): a plain
    `sqlite3.connect()` object is only usable from the thread that created
    it. FastAPI runs sync route handlers (like serve.py's `def chat(...)`)
    in a thread-pool executor, so the tool call executes on a *different*
    thread than the one that opened this connection at agent-build time —
    every single query raised `sqlite3.ProgrammingError: SQLite objects
    created in a thread can only be used in that same thread`, silently
    swallowed into a generic ToolResult(ok=False) with no exception text
    surfaced anywhere. `check_same_thread=False` permits cross-thread use;
    the lock then serializes actual access, since permitting cross-thread
    use doesn't by itself make concurrent access from multiple threads at
    once safe."""

    def __init__(self, path: str) -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def close(self) -> None:
        self._conn.close()


def data_query_tool(
    source: DataSource, *, name: str = "query_data", allowed_tables: frozenset[str] | None = None, description: str | None = None,
) -> ToolSpec:
    """Wraps a DataSource as a read-only ToolSpec: SELECT-only, and — if
    allowed_tables is given — restricted to queries that mention one of them.
    RBAC on top of this is the usual Policy.allowed_tools, same as any tool.

    `description` matters more than it looks: the generic default below tells
    a model nothing about the actual schema, so it has to guess table and
    column names — verified for real against the live Anthropic API (not a
    scripted stand-in) generating `SELECT * FROM formulary WHERE drug_name
    ILIKE '%metformin%'` against a table actually named `drug_formulary`,
    guessing both the table name and a PostgreSQL-only operator SQLite
    doesn't support. Pass a description naming the real table and columns
    (and the SQL dialect, if it's not obvious) for any table an agent will
    actually query."""

    def run(sql: str) -> list[dict[str, Any]]:
        stripped = sql.strip().lower()
        if not stripped.startswith("select"):
            raise ValueError("only SELECT statements are permitted")
        if allowed_tables is not None and not any(t.lower() in stripped for t in allowed_tables):
            raise ValueError(f"query must reference one of: {sorted(allowed_tables)}")
        return source.query(sql)

    return ToolSpec(
        name=name,
        description=description or "Run a read-only SQL SELECT against the connected data source.",
        parameters={"sql": "string"},
        fn=run,
    )

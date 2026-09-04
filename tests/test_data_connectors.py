import sqlite3
import tempfile
import threading

import pytest

from agent_foundry.contracts import Identity, Policy
from agent_foundry.data_connectors import SQLiteDataSource, data_query_tool
from agent_foundry.tools_gateway import ToolRegistry


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        conn = sqlite3.connect(f.name)
        conn.execute("CREATE TABLE orders (id TEXT, status TEXT)")
        conn.executemany("INSERT INTO orders VALUES (?, ?)", [("A100", "shipped"), ("A101", "processing")])
        conn.commit()
        conn.close()
        yield f.name


def test_sqlite_data_source_queries_real_rows(db_path):
    source = SQLiteDataSource(db_path)
    rows = source.query("SELECT * FROM orders WHERE id = ?", ("A100",))
    assert rows == [{"id": "A100", "status": "shipped"}]
    source.close()


def test_sqlite_data_source_is_usable_from_a_different_thread(db_path):
    """Regression: a plain sqlite3.connect() is only usable from the thread
    that created it — invisible in every prior test (all single-threaded),
    but real the moment this runs behind a real ASGI server: FastAPI executes
    sync route handlers in a thread-pool executor, so the tool call happens
    on a different thread than the one that built the agent. Reproduced
    directly against a live uvicorn server before this fix; every query
    failed with sqlite3.ProgrammingError, silently swallowed into a generic
    ToolResult(ok=False) with no visible exception text."""
    source = SQLiteDataSource(db_path)
    result: dict = {}

    def worker():
        try:
            result["rows"] = source.query("SELECT * FROM orders WHERE id = ?", ("A100",))
        except Exception as e:
            result["error"] = e

    t = threading.Thread(target=worker)
    t.start()
    t.join()

    assert "error" not in result, result.get("error")
    assert result["rows"] == [{"id": "A100", "status": "shipped"}]
    source.close()


def test_data_query_tool_rejects_non_select(db_path):
    source = SQLiteDataSource(db_path)
    tool = data_query_tool(source)
    with pytest.raises(ValueError):
        tool.fn(sql="DROP TABLE orders")
    source.close()


def test_data_query_tool_enforces_allowed_tables(db_path):
    source = SQLiteDataSource(db_path)
    tool = data_query_tool(source, allowed_tables=frozenset({"orders"}))
    assert tool.fn(sql="SELECT * FROM orders") == [{"id": "A100", "status": "shipped"}, {"id": "A101", "status": "processing"}]
    with pytest.raises(ValueError):
        tool.fn(sql="SELECT * FROM customers")
    source.close()


def test_data_query_tool_composes_through_registry_rbac(db_path):
    source = SQLiteDataSource(db_path)
    registry = ToolRegistry()
    registry.register(data_query_tool(source, name="query_orders", allowed_tables=frozenset({"orders"})))
    identity = Identity(id="t", tenant_id="acme")
    policy = Policy(allowed_tools=frozenset({"query_orders"}))
    result = registry.invoke("query_orders", {"sql": "SELECT status FROM orders WHERE id = 'A101'"}, identity=identity, policy=policy)
    assert result.ok and result.output == [{"status": "processing"}]
    source.close()

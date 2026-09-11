"""RedisStateStore (distributed.py) and PostgresStateStore
(core/state_store.py) against the SAME contract test_state_store.py already
proves for MemoryStateStore — plus the one property only a real backend can
prove: two SEPARATE store instances pointed at the same key/table share one
real value, same "two replicas, one Redis" posture as test_distributed.py.

Requires a real Redis/Postgres reachable — each half of this file skips on
its own if its server isn't there, rather than mocking either away and
testing nothing real (same convention as test_distributed.py).
"""
from __future__ import annotations

import os
import uuid

import pytest

from agent_foundry.core.protocols import StateStore

redis = pytest.importorskip("redis")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
try:
    redis.Redis.from_url(REDIS_URL).ping()
    _REDIS_UP = True
except Exception:
    _REDIS_UP = False

psycopg2 = pytest.importorskip("psycopg2")

PG_DSN = os.environ.get("POSTGRES_DSN", "dbname=agent_foundry")
try:
    psycopg2.connect(PG_DSN).close()
    _POSTGRES_UP = True
except Exception:
    _POSTGRES_UP = False

from agent_foundry.core.state_store import PostgresStateStore
from agent_foundry.distributed import RedisStateStore


def _prefix() -> str:
    return f"test-{uuid.uuid4().hex[:8]}"


@pytest.mark.skipif(not _REDIS_UP, reason=f"no Redis reachable at {REDIS_URL}")
class TestRedisStateStore:
    def _store(self) -> RedisStateStore:
        return RedisStateStore(redis_url=REDIS_URL, key_prefix=f"agent_foundry:test:{_prefix()}")

    def test_satisfies_the_protocol(self):
        assert isinstance(self._store(), StateStore)

    def test_load_on_an_unknown_run_id_returns_none(self):
        assert self._store().load("no-such-run") is None

    def test_save_then_load_round_trips(self):
        store = self._store()
        store.save("run-1", {"messages": [{"role": "user", "content": "hi"}]})
        assert store.load("run-1") == {"messages": [{"role": "user", "content": "hi"}]}

    def test_save_overwrites_a_previous_save_for_the_same_run_id(self):
        store = self._store()
        store.save("run-1", {"messages": ["first"]})
        store.save("run-1", {"messages": ["second"]})
        assert store.load("run-1") == {"messages": ["second"]}

    def test_delete_removes_the_state(self):
        store = self._store()
        store.save("run-1", {"messages": []})
        store.delete("run-1")
        assert store.load("run-1") is None

    def test_delete_on_an_unknown_run_id_is_a_no_op_not_an_error(self):
        self._store().delete("no-such-run")  # must not raise

    def test_two_separate_store_instances_share_the_same_underlying_redis_key(self):
        """The actual cross-replica property MemoryStateStore can never
        have — two independently-constructed RedisStateStore objects
        pointed at the same prefix must see each other's writes."""
        prefix = f"agent_foundry:test:{_prefix()}"
        writer = RedisStateStore(redis_url=REDIS_URL, key_prefix=prefix)
        reader = RedisStateStore(redis_url=REDIS_URL, key_prefix=prefix)

        writer.save("shared-run", {"messages": ["from writer"]})
        assert reader.load("shared-run") == {"messages": ["from writer"]}


@pytest.mark.skipif(not _POSTGRES_UP, reason=f"no Postgres reachable at {PG_DSN!r}")
class TestPostgresStateStore:
    def _store(self) -> PostgresStateStore:
        return PostgresStateStore(dsn=PG_DSN, table=f"agent_foundry_test_{_prefix().replace('-', '_')}")

    def test_satisfies_the_protocol(self):
        assert isinstance(self._store(), StateStore)

    def test_load_on_an_unknown_run_id_returns_none(self):
        assert self._store().load("no-such-run") is None

    def test_save_then_load_round_trips(self):
        store = self._store()
        store.save("run-1", {"messages": [{"role": "user", "content": "hi"}]})
        assert store.load("run-1") == {"messages": [{"role": "user", "content": "hi"}]}

    def test_save_overwrites_a_previous_save_for_the_same_run_id(self):
        store = self._store()
        store.save("run-1", {"messages": ["first"]})
        store.save("run-1", {"messages": ["second"]})
        assert store.load("run-1") == {"messages": ["second"]}

    def test_delete_removes_the_state(self):
        store = self._store()
        store.save("run-1", {"messages": []})
        store.delete("run-1")
        assert store.load("run-1") is None

    def test_delete_on_an_unknown_run_id_is_a_no_op_not_an_error(self):
        self._store().delete("no-such-run")  # must not raise

    def test_two_separate_store_instances_share_the_same_underlying_table(self):
        table = f"agent_foundry_test_{_prefix().replace('-', '_')}"
        writer = PostgresStateStore(dsn=PG_DSN, table=table)
        reader = PostgresStateStore(dsn=PG_DSN, table=table)

        writer.save("shared-run", {"messages": ["from writer"]})
        assert reader.load("shared-run") == {"messages": ["from writer"]}

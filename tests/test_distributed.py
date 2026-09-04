"""Proves distributed.py's Redis-backed classes are a real drop-in for the
in-process RunBudget/RateLimiter/ToolCache/SLATracker/CostLedger AND — the
whole point of this module — that two SEPARATE instances pointed at the
same Redis key (simulating two replicas behind a load balancer) share one
real ceiling/cache/window instead of each enforcing its own. That second
property is exactly what the in-process versions cannot do, and exactly
what test_protocol_substitutability.py's fakes don't prove either (an
in-memory fake has no cross-process story at all).

Requires a real Redis reachable at REDIS_URL (default redis://localhost:6379/0)
— skips the whole module if one isn't there, rather than mocking Redis away
and testing nothing real.
"""
from __future__ import annotations

import os
import uuid

import pytest

redis = pytest.importorskip("redis")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

try:
    redis.Redis.from_url(REDIS_URL).ping()
    _REDIS_UP = True
except Exception:
    _REDIS_UP = False

pytestmark = pytest.mark.skipif(not _REDIS_UP, reason=f"no Redis reachable at {REDIS_URL}")

from agent_foundry.contracts import Policy, ToolSpec
from agent_foundry.distributed import RedisCostLedger, RedisRateLimiter, RedisRunBudget, RedisSLATracker, RedisToolCache
from agent_foundry.orchestration import build_agent_graph
from agent_foundry.runtime import BudgetExceeded
from agent_foundry.tools_gateway import ToolRegistry

from conftest import ScriptedProvider, make_config_kwargs


def _prefix() -> str:
    return f"test:{uuid.uuid4().hex}"


def _invoke(graph, thread_id, text):
    return graph.invoke({"messages": [{"role": "user", "content": text}], "thread_id": thread_id}, {"configurable": {"thread_id": thread_id}})


def test_redis_run_budget_enforces_one_ceiling_across_two_separate_instances():
    """Simulates two replicas: instance_a handles the first two requests,
    instance_b (a DIFFERENT Python object, same Redis key) handles the
    third — and still sees the combined spend and trips the budget. An
    in-process RunBudget could never do this; each replica would only see
    its own requests and the real ceiling would be 2x what was configured."""
    prefix = _prefix()
    policy = Policy(allowed_tools=frozenset(), max_cost_usd_per_thread=0.25, max_steps_per_thread=100)
    instance_a = RedisRunBudget(policy, redis_url=REDIS_URL, key_prefix=prefix)
    instance_b = RedisRunBudget(policy, redis_url=REDIS_URL, key_prefix=prefix)

    instance_a.spend(0.10, thread_id="t1")
    instance_a.spend(0.10, thread_id="t1")
    assert instance_b.cost_usd_for("t1") == pytest.approx(0.20)  # instance_b sees instance_a's spend

    with pytest.raises(BudgetExceeded):
        instance_b.spend(0.10, thread_id="t1")  # combined 0.30 > 0.25, tripped from the OTHER instance


def test_redis_run_budget_is_a_real_drop_in_for_a_full_graph_turn(identity, policy, tool_registry):
    budget = RedisRunBudget(policy, redis_url=REDIS_URL, key_prefix=_prefix())
    provider = ScriptedProvider(['CALL lookup_order {"order_id": "A100"}', "All set!"])
    kwargs = make_config_kwargs(identity=identity, policy=policy, tools=tool_registry, provider=provider)
    kwargs["budget"] = budget
    graph = build_agent_graph(system_prompt="sys", **kwargs)

    state = _invoke(graph, "t-redis-budget", "status of A100?")

    assert state["messages"][-1]["content"] == "All set!"
    assert budget.steps_for("t-redis-budget") >= 1


def test_redis_rate_limiter_shares_its_bucket_across_two_separate_instances():
    prefix = _prefix()
    instance_a = RedisRateLimiter(rate_per_s=0.0, burst=2, redis_url=REDIS_URL, key_prefix=prefix)  # no refill — exhaust the burst, nothing regenerates
    instance_b = RedisRateLimiter(rate_per_s=0.0, burst=2, redis_url=REDIS_URL, key_prefix=prefix)

    assert instance_a.allow("tool-x") is True   # token 1 of 2
    assert instance_a.allow("tool-x") is True   # token 2 of 2
    assert instance_b.allow("tool-x") is False  # instance_b sees the SAME bucket, already empty


def test_redis_tool_cache_shares_a_cached_result_across_two_separate_instances():
    prefix = _prefix()
    instance_a = RedisToolCache(ttl_s=30.0, redis_url=REDIS_URL, key_prefix=prefix)
    instance_b = RedisToolCache(ttl_s=30.0, redis_url=REDIS_URL, key_prefix=prefix)

    assert instance_b.get("lookup_order", {"order_id": "A100"}) is None
    instance_a.set("lookup_order", {"order_id": "A100"}, "order A100 shipped")
    assert instance_b.get("lookup_order", {"order_id": "A100"}) == "order A100 shipped"


def test_redis_tool_cache_is_a_real_drop_in_and_prevents_a_repeat_call(identity):
    call_count = 0

    def counted_lookup(order_id: str) -> str:
        nonlocal call_count
        call_count += 1
        return f"order {order_id} shipped"

    tools = ToolRegistry(cache=RedisToolCache(ttl_s=30.0, redis_url=REDIS_URL, key_prefix=_prefix()))
    tools.register(ToolSpec("lookup_order", "Look up an order", {"order_id": "string"}, counted_lookup))
    open_policy = Policy(allowed_tools=frozenset({"lookup_order"}), max_cost_usd_per_thread=1.0, max_steps_per_thread=10)

    first = tools.invoke("lookup_order", {"order_id": "A100"}, identity=identity, policy=open_policy)
    second = tools.invoke("lookup_order", {"order_id": "A100"}, identity=identity, policy=open_policy)

    assert first.output == second.output == "order A100 shipped"
    assert call_count == 1


def test_redis_sla_tracker_shares_its_window_across_two_separate_instances():
    prefix = _prefix()
    instance_a = RedisSLATracker(redis_url=REDIS_URL, key_prefix=prefix)
    instance_b = RedisSLATracker(redis_url=REDIS_URL, key_prefix=prefix)

    instance_a.record(ok=True, latency_ms=100.0)
    instance_a.record(ok=False, latency_ms=200.0)

    assert instance_b.success_rate() == pytest.approx(0.5)  # instance_b sees both of instance_a's records


def test_redis_cost_ledger_shares_totals_across_two_separate_instances():
    prefix = _prefix()
    instance_a = RedisCostLedger(redis_url=REDIS_URL, key_prefix=prefix)
    instance_b = RedisCostLedger(redis_url=REDIS_URL, key_prefix=prefix)

    instance_a.close_task(thread_id="t1", tenant_id="acme", cost_usd=0.05, steps=2, outcome="answered")
    instance_b.close_task(thread_id="t2", tenant_id="acme", cost_usd=0.03, steps=1, outcome="answered")

    assert instance_a.total_cost_usd() == pytest.approx(0.08)  # sees both instances' writes
    assert instance_b.by_tenant() == pytest.approx({"acme": 0.08})

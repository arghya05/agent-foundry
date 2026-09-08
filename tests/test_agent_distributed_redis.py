"""Agent/NativeEngine combined with distributed.py's Redis-backed primitives
— previously never combined; distributed.py was only ever tested against the
old direct build_agent_graph path (test_distributed.py). Requires a real,
reachable redis-server — skipped automatically if one isn't available, never
faked. Run against a local instance on port 6399 (started for this test run,
independent of whatever's on the default 6379) so it can't collide with any
other project's data.
"""
from __future__ import annotations

import pytest

redis = pytest.importorskip("redis")

try:
    _client = redis.Redis.from_url("redis://localhost:6399/0", socket_connect_timeout=1)
    _client.ping()
    _REDIS_AVAILABLE = True
except Exception:
    _REDIS_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _REDIS_AVAILABLE, reason="no reachable redis-server on localhost:6399")

from agent_foundry import Agent, ExecutionContext
from agent_foundry.contracts import Policy
from agent_foundry.distributed import RedisRunBudget
from agent_foundry.llm_gateway import LLMGateway
from agent_foundry.runtime import BudgetExceeded

from conftest import ScriptedProvider

_REDIS_URL = "redis://localhost:6399/0"


@pytest.fixture(autouse=True)
def _clean_redis():
    _client.flushdb()
    yield
    _client.flushdb()


@pytest.mark.parametrize("runtime", ["langgraph", "native"])
def test_agent_with_redis_backed_budget_enforces_and_persists(runtime):
    policy = Policy(max_cost_usd_per_thread=0.05, max_steps_per_thread=100)
    budget = RedisRunBudget(policy, redis_url=_REDIS_URL, key_prefix=f"test:{runtime}")
    provider = ScriptedProvider([_reply(0.02), _reply(0.02), _reply(0.02)])
    agent = Agent("chatty", "Chat.", runtime=runtime, policy=policy, budget=budget, llm=LLMGateway(provider=provider))
    context = ExecutionContext(thread_id=f"redis-budget-{runtime}")

    agent.run("hi", context=context)
    agent.run("again", context=context)
    with pytest.raises(BudgetExceeded):
        agent.run("once more", context=context)

    # a SEPARATE RedisRunBudget instance (simulating a second replica) sees
    # the same spend — this is the entire point of the Redis-backed version
    other_replica_view = RedisRunBudget(policy, redis_url=_REDIS_URL, key_prefix=f"test:{runtime}")
    assert other_replica_view.cost_usd_for(f"redis-budget-{runtime}") >= 0.04


def test_two_agent_instances_share_one_redis_backed_budget_across_the_same_thread():
    """The actual multi-replica scenario: two independent Agent instances
    (simulating two server replicas) enforcing ONE combined ceiling for one
    conversation, not one ceiling each."""
    policy = Policy(max_cost_usd_per_thread=0.03, max_steps_per_thread=100)
    thread_id = "shared-replica-thread"
    context = ExecutionContext(thread_id=thread_id)

    replica_a = Agent(
        "chatty", "Chat.", runtime="native", policy=policy,
        budget=RedisRunBudget(policy, redis_url=_REDIS_URL, key_prefix="test:shared"),
        llm=LLMGateway(provider=ScriptedProvider([_reply(0.02)])),
    )
    replica_b = Agent(
        "chatty", "Chat.", runtime="native", policy=policy,
        budget=RedisRunBudget(policy, redis_url=_REDIS_URL, key_prefix="test:shared"),
        llm=LLMGateway(provider=ScriptedProvider([_reply(0.02)])),
    )

    replica_a.run("hi", context=context)  # spends 0.02 via replica A
    with pytest.raises(BudgetExceeded):
        replica_b.run("hi again", context=context)  # replica B sees the combined 0.04 > 0.03 ceiling


def _reply(cost_usd: float):
    from agent_foundry.contracts import LLMResponse

    def make(messages, model):
        return LLMResponse(text="ok", model=model, input_tokens=1, output_tokens=1, cost_usd=cost_usd)

    return make

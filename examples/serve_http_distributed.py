"""The same support agent as examples/serve_http.py, wired for a REAL
multi-replica deployment instead of a single process: budget, rate limiter,
tool cache, SLA tracker and cost ledger are all backed by Redis
(agent_foundry/distributed.py), so N replicas of this container behind a
load balancer share one real ceiling for each — not N independent ones.

Run:
    pip install -r requirements.txt -r requirements-distributed.txt
    export ANTHROPIC_API_KEY=...
    export REDIS_URL=redis://localhost:6379/0   # defaults to this if unset
    python examples/serve_http_distributed.py

For the checkpointer (session/thread state) — the other piece of state that
needs to survive past one process — pass a real BaseCheckpointSaver
(SqliteSaver for one node, PostgresSaver for a real fleet) to
build_agent_graph's checkpointer= kwarg instead of the MemorySaver default.
See orchestration.py's module docstring.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_foundry.contracts import Identity, Policy, ToolSpec
from agent_foundry.distributed import RedisCostLedger, RedisRateLimiter, RedisRunBudget, RedisSLATracker, RedisToolCache
from agent_foundry.eval import EvalHarness
from agent_foundry.guardrails import GuardrailEngine
from agent_foundry.llm_gateway import AnthropicProvider, LLMGateway
from agent_foundry.observability import Tracer
from agent_foundry.orchestration import build_agent_graph
from agent_foundry.serve import build_http_app
from agent_foundry.tools_gateway import ToolRegistry

_ORDERS = {"A100": {"status": "shipped"}}
_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def lookup_order(order_id: str) -> str:
    return f"{order_id}: {_ORDERS.get(order_id, 'not found')}"


identity = Identity(id="support-agent-1", tenant_id="acme")
policy = Policy(allowed_tools=frozenset({"lookup_order"}), max_cost_usd_per_thread=0.50, max_steps_per_thread=10)

tools = ToolRegistry(
    rate_limiter=RedisRateLimiter(rate_per_s=10.0, burst=20, redis_url=_REDIS_URL),
    cache=RedisToolCache(ttl_s=300.0, redis_url=_REDIS_URL),
)
tools.register(ToolSpec("lookup_order", "Look up an order by id", {"order_id": "string"}, lookup_order))

graph = build_agent_graph(
    system_prompt='You are a support agent. To use a tool, reply with exactly: CALL <tool_name> {"arg": "value"}. Tools: lookup_order(order_id).',
    llm=LLMGateway(provider=AnthropicProvider()), tools=tools, guardrails=GuardrailEngine(policy),
    eval_harness=EvalHarness(), identity=identity, policy=policy,
    budget=RedisRunBudget(policy, redis_url=_REDIS_URL),
    sla_tracker=RedisSLATracker(redis_url=_REDIS_URL),
    cost_ledger=RedisCostLedger(redis_url=_REDIS_URL),
    tracer=Tracer("http"),
)
app = build_http_app(graph)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

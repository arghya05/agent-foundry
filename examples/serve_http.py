"""Serves a support agent over HTTP — the same agent as examples/support_agent.py,
wrapped for deployment instead of the CLI console. Run locally:

    export ANTHROPIC_API_KEY=...
    python examples/serve_http.py

Or containerize it (see the repo-root Dockerfile) and deploy the container to
any cloud — nothing here is cloud-specific.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_foundry.contracts import Identity, Policy, ToolSpec
from agent_foundry.eval import EvalHarness
from agent_foundry.guardrails import GuardrailEngine
from agent_foundry.llm_gateway import AnthropicProvider, LLMGateway
from agent_foundry.observability import Tracer
from agent_foundry.orchestration import build_agent_graph
from agent_foundry.runtime import RunBudget
from agent_foundry.serve import build_http_app
from agent_foundry.tools_gateway import ToolRegistry

_ORDERS = {"A100": {"status": "shipped"}}


def lookup_order(order_id: str) -> str:
    return f"{order_id}: {_ORDERS.get(order_id, 'not found')}"


identity = Identity(id="support-agent-1", tenant_id="acme")
policy = Policy(allowed_tools=frozenset({"lookup_order"}))
tools = ToolRegistry()
tools.register(ToolSpec("lookup_order", "Look up an order by id", {"order_id": "string"}, lookup_order))

graph = build_agent_graph(
    system_prompt='You are a support agent. To use a tool, reply with exactly: CALL <tool_name> {"arg": "value"}. Tools: lookup_order(order_id).',
    llm=LLMGateway(provider=AnthropicProvider()), tools=tools, guardrails=GuardrailEngine(policy),
    eval_harness=EvalHarness(), identity=identity, policy=policy, budget=RunBudget(policy), tracer=Tracer("http"),
)
app = build_http_app(graph)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))

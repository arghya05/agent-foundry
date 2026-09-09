"""Agent.serve() — previously zero test coverage. _NativeGraph was designed
to duck-type into serve.build_http_app well enough to work unmodified;
this actually starts the FastAPI app (via TestClient, an in-process real
HTTP round trip through Starlette's routing/request handling, not a mock)
and hits /health, /chat, and /resume for both engines.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_foundry import Agent
from agent_foundry.kpi import KPI
from agent_foundry.llm_gateway import LLMGateway
from agent_foundry.orchestration import CritiqueConfig

from conftest import ScriptedProvider


@pytest.mark.parametrize("runtime", ["langgraph", "native"])
def test_serve_health_and_chat_round_trip(runtime):
    provider = ScriptedProvider(["hello from serve"])
    agent = Agent("chatty", "Chat.", runtime=runtime, llm=LLMGateway(provider=provider))
    app = agent.serve(allow_unauthenticated_demo=True)
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200

    chat = client.post("/chat", json={"message": "hi", "thread_id": f"serve-{runtime}"})
    assert chat.status_code == 200
    body = chat.json()
    assert body["status"] == "ok"
    assert body["reply"] == "hello from serve"


@pytest.mark.parametrize("runtime", ["langgraph", "native"])
def test_serve_chat_then_resume_round_trip_for_a_paused_turn(runtime):
    kpi = KPI(name="confidence", score=lambda ctx: ctx["score"], direction="maximize", threshold=0.5)
    critique = CritiqueConfig(kpi=kpi, context=lambda state, draft: {"score": 0.02}, escalate_threshold=0.05)
    provider = ScriptedProvider(["an ambiguous answer"])
    agent = Agent("advisor", "Answer.", runtime=runtime, llm=LLMGateway(provider=provider), critique=critique)
    app = agent.serve(allow_unauthenticated_demo=True)
    client = TestClient(app)
    thread_id = f"serve-resume-{runtime}"

    paused = client.post("/chat", json={"message": "what should I do?", "thread_id": thread_id})
    assert paused.status_code == 200
    assert paused.json()["status"] == "awaiting_approval"

    resumed = client.post("/resume", json={"thread_id": thread_id, "approved": True})
    assert resumed.status_code == 200
    body = resumed.json()
    assert body["status"] == "ok"
    assert body["reply"] == "an ambiguous answer"


@pytest.mark.parametrize("runtime", ["langgraph", "native"])
def test_serve_budget_exceeded_becomes_http_429(runtime):
    from agent_foundry.contracts import Policy
    from agent_foundry.runtime import RunBudget

    policy = Policy(max_cost_usd_per_thread=0.0)

    class ExpensiveProvider:
        def complete(self, messages, *, model, tools=None, **kw):
            from agent_foundry.contracts import LLMResponse
            return LLMResponse(text="hi", model=model, input_tokens=1, output_tokens=1, cost_usd=1.0)

    agent = Agent("pricey", "Answer.", runtime=runtime, policy=policy, budget=RunBudget(policy), llm=LLMGateway(provider=ExpensiveProvider()))
    app = agent.serve(allow_unauthenticated_demo=True)
    client = TestClient(app)

    resp = client.post("/chat", json={"message": "hi", "thread_id": f"serve-budget-{runtime}"})

    assert resp.status_code == 429

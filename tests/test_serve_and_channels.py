import hashlib
import hmac
import json
import time

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from agent_foundry.contracts import Identity, LLMResponse, Policy, ToolSpec
from agent_foundry.eval import EvalHarness
from agent_foundry.guardrails import GuardrailEngine
from agent_foundry.llm_gateway import LLMGateway
from agent_foundry.observability import Tracer
from agent_foundry.orchestration import build_agent_graph
from agent_foundry.runtime import LatencyBudget, RunBudget
from agent_foundry.serve import build_http_app
from agent_foundry.tools_gateway import ToolRegistry


def _support_graph(*, requires_approval=False, latency_budget=None):
    def issue_refund(order_id, amount_usd):
        return f"refunded ${amount_usd}"

    class Provider:
        def complete(self, messages, *, model, tools=None, **kw):
            last = messages[-1]
            if last["role"] == "user" and "refund" in str(last["content"]).lower():
                return LLMResponse(text='CALL issue_refund {"order_id": "A100", "amount_usd": 20}', model=model, input_tokens=1, output_tokens=1, cost_usd=0.0)
            return LLMResponse(text="Refund processed.", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0)

    identity = Identity(id="t", tenant_id="acme")
    policy = Policy(allowed_tools=frozenset({"issue_refund"}), requires_approval=frozenset({"issue_refund"}) if requires_approval else frozenset())
    tools = ToolRegistry()
    tools.register(ToolSpec("issue_refund", "refund", {"order_id": "string", "amount_usd": "number"}, issue_refund))
    return build_agent_graph(system_prompt="sys", llm=LLMGateway(provider=Provider()), tools=tools,
        guardrails=GuardrailEngine(policy), eval_harness=EvalHarness(), identity=identity, policy=policy,
        budget=RunBudget(policy), tracer=Tracer("serve-test"), latency_budget=latency_budget)


def test_health_and_index_endpoints():
    app = build_http_app(_support_graph())
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}
    page = client.get("/")
    assert page.status_code == 200 and "/chat" in page.text


def test_chat_endpoint_returns_429_not_an_unhandled_500_when_latency_budget_is_exceeded():
    """Regression test: BudgetExceeded (cost/step/latency ceiling) used to
    propagate out of graph.invoke() completely uncaught — a raw 500 that (a
    real FastAPI/Starlette quirk) doesn't reliably carry CORS headers,
    surfacing to a browser as an opaque "Failed to fetch" with no
    explanation at all. Found live testing a genuine multi-turn conversation
    against a too-short LatencyBudget. max_seconds=-1 makes the very first
    check() already past the ceiling, so this is deterministic and fast —
    no real waiting required."""
    graph = _support_graph(latency_budget=LatencyBudget(max_seconds=-1))
    app = build_http_app(graph)
    client = TestClient(app)
    resp = client.post("/chat", json={"thread_id": "t1", "message": "hello"})
    assert resp.status_code == 429
    assert "latency budget" in resp.json()["detail"].lower()


def test_serve_resume_route_false_lets_a_caller_register_its_own_resume_endpoint():
    """A deployment with more than one graph (one per role, healthcare/
    backend/app.py) can't resume a paused thread through a route bound to a
    single fixed graph — the paused thread might belong to a different
    graph object. Proves the caller's own /resume wins, same pattern as
    serve_chat_route=False."""
    app = build_http_app(_support_graph(), serve_resume_route=False)

    @app.post("/resume")
    def custom_resume() -> dict:
        return {"custom": True}

    client = TestClient(app)
    assert client.post("/resume", json={"thread_id": "t", "approved": True}).json() == {"custom": True}


def test_serve_chat_route_false_lets_a_caller_register_its_own_chat_endpoint():
    """healthcare/backend/app.py needs POST /chat to route by role to one of
    several graphs — FastAPI matches routes in registration order, so this
    proves the caller's own /chat (registered after build_http_app returns)
    actually wins, not the generic one, when serve_chat_route=False."""
    from agent_foundry.serve import chat_response_from_result

    app = build_http_app(_support_graph(), serve_chat_route=False)

    @app.post("/chat")
    def custom_chat() -> dict:
        return {"custom": True}

    client = TestClient(app)
    assert client.post("/chat", json={"thread_id": "t", "message": "hi"}).json() == {"custom": True}


def test_serve_demo_ui_false_skips_the_generic_index_page():
    """A deployment with its own real frontend (healthcare/backend/app.py)
    passes serve_demo_ui=False and registers its own GET / — found live: a
    real user landed on the API's own base URL instead of the frontend's and
    mistook this generic, unbranded reference chat page for a broken/old
    version of the real app. Without a route of its own registered
    afterward, GET / here should 404, not silently fall back to the demo
    page."""
    app = build_http_app(_support_graph(), serve_demo_ui=False)
    client = TestClient(app)
    assert client.get("/health").json() == {"status": "ok"}  # everything else still works
    assert client.get("/").status_code == 404


def test_chat_endpoint_full_turn():
    app = build_http_app(_support_graph())
    client = TestClient(app)
    r = client.post("/chat", json={"thread_id": "t1", "message": "hello"})
    assert r.json()["status"] == "ok"


def test_chat_then_resume_hitl_flow():
    app = build_http_app(_support_graph(requires_approval=True))
    client = TestClient(app)
    r1 = client.post("/chat", json={"thread_id": "t2", "message": "refund order A100"})
    data1 = r1.json()
    assert data1["status"] == "awaiting_approval" and data1["pending"]["tool"] == "issue_refund"
    r2 = client.post("/resume", json={"thread_id": "t2", "approved": True})
    assert r2.json() == {"status": "ok", "reply": "Refund processed.", "pending": None}


def test_slack_channel_signature_verification_and_full_round_trip():
    from agent_foundry.channels import build_slack_app, verify_slack_signature

    secret = "test-signing-secret"

    def sign(body: bytes, ts: str) -> str:
        return "v0=" + hmac.new(secret.encode(), f"v0:{ts}:{body.decode()}".encode(), hashlib.sha256).hexdigest()

    body = b'{"a": 1}'
    ts = str(int(time.time()))
    assert verify_slack_signature(signing_secret=secret, timestamp=ts, body=body, signature=sign(body, ts))
    assert not verify_slack_signature(signing_secret=secret, timestamp=ts, body=body, signature="v0=bogus")
    old_ts = str(int(time.time()) - 3600)
    assert not verify_slack_signature(signing_secret=secret, timestamp=old_ts, body=body, signature=sign(body, old_ts))

    posted = []
    app = build_slack_app(_support_graph(), signing_secret=secret, post_message=lambda channel, text: posted.append((channel, text)))
    client = TestClient(app)

    challenge_body = json.dumps({"type": "url_verification", "challenge": "xyz"}).encode()
    ts2 = str(int(time.time()))
    r = client.post("/slack/events", content=challenge_body, headers={"X-Slack-Request-Timestamp": ts2, "X-Slack-Signature": sign(challenge_body, ts2)})
    assert r.status_code == 200 and r.text == "xyz"

    event_body = json.dumps({"type": "event_callback", "event": {"type": "message", "channel": "C1", "ts": "1.1", "text": "hello"}}).encode()
    ts3 = str(int(time.time()))
    r2 = client.post("/slack/events", content=event_body, headers={"X-Slack-Request-Timestamp": ts3, "X-Slack-Signature": sign(event_body, ts3)})
    assert r2.status_code == 200
    assert posted and posted[0][0] == "C1"

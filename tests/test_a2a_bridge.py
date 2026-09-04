import socket
import threading
import time

import pytest

pytest.importorskip("a2a")
fastapi = pytest.importorskip("fastapi")

from agent_foundry.contracts import Identity, LLMResponse, Policy
from agent_foundry.eval import EvalHarness
from agent_foundry.guardrails import GuardrailEngine
from agent_foundry.llm_gateway import LLMGateway
from agent_foundry.observability import Tracer
from agent_foundry.orchestration import build_agent_graph
from agent_foundry.runtime import RunBudget
from agent_foundry.tools_gateway import ToolRegistry

from agent_foundry.a2a_bridge import agent_card_for, build_a2a_app, send_a2a_message


def _echo_graph():
    class Provider:
        def complete(self, messages, *, model, tools=None, **kw):
            last = messages[-1]
            return LLMResponse(text=f"echo: {last['content']}", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0)

    identity = Identity(id="t", tenant_id="acme")
    policy = Policy(allowed_tools=frozenset())
    return build_agent_graph(system_prompt="sys", llm=LLMGateway(provider=Provider()), tools=ToolRegistry(),
        guardrails=GuardrailEngine(policy), eval_harness=EvalHarness(), identity=identity, policy=policy,
        budget=RunBudget(policy), tracer=Tracer("a2a-test"))


class _DummyConfig:
    system_prompt = "A support agent that answers order status questions."


def test_agent_card_for_builds_a_real_card_with_transport():
    card = agent_card_for(_DummyConfig(), name="support-agent", skills=[{"id": "status", "name": "Order Status", "description": "checks order status"}], rpc_url="http://localhost:8787/a2a/rpc")
    assert card.name == "support-agent"
    assert card.skills[0].id == "status"
    assert card.supported_interfaces[0].url == "http://localhost:8787/a2a/rpc"


def test_agent_card_without_rpc_url_has_no_interfaces():
    card = agent_card_for(_DummyConfig(), name="support-agent", skills=[])
    assert card.supported_interfaces == []


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def a2a_server():
    import uvicorn

    port = _free_port()
    rpc_url = f"http://127.0.0.1:{port}/a2a/rpc"
    card = agent_card_for(_DummyConfig(), name="echo-agent", skills=[{"id": "echo", "name": "Echo", "description": "echoes input"}], rpc_url=rpc_url)
    try:
        app = build_a2a_app(card, _echo_graph(), rpc_url="/a2a/rpc")
    except AttributeError as e:
        if "is_repeated" in str(e):
            pytest.skip(
                "known upstream bug: a2a-sdk's OpenAPI schema generator "
                "(_proto_schema.py) reads FieldDescriptor.is_repeated, which "
                "doesn't exist on the installed protobuf's FieldDescriptor "
                "(neither the C++/upb nor the pure-Python backend has it) — "
                "see https://github.com/a2aproject/a2a-python, not fixable "
                "from agent_foundry.a2a_bridge"
            )
        raise

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 5
    while not getattr(server, "started", False) and time.time() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.mark.integration
def test_full_a2a_round_trip_against_a_real_local_server(a2a_server):
    import asyncio

    reply = asyncio.run(send_a2a_message(a2a_server, "hello there"))
    assert "echo:" in reply and "hello there" in reply

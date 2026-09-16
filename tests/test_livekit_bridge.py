import asyncio

import pytest

pytest.importorskip("livekit")

from agent_foundry.contracts import Identity, LLMResponse, Policy, ToolSpec
from agent_foundry.eval import EvalHarness
from agent_foundry.guardrails import GuardrailEngine
from agent_foundry.llm_gateway import LLMGateway
from agent_foundry.observability import Tracer
from agent_foundry.orchestration import build_agent_graph
from agent_foundry.runtime import RunBudget
from agent_foundry.tools_gateway import ToolRegistry

from agent_foundry.livekit_bridge import build_voice_agent, to_livekit_function_tool


def _echo_graph():
    class Provider:
        def complete(self, messages, *, model, tools=None, **kw):
            last = messages[-1]
            return LLMResponse(text=f"echo: {last['content']}", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0)

    identity = Identity(id="t", tenant_id="acme")
    policy = Policy(allowed_tools=frozenset())
    return build_agent_graph(system_prompt="sys", llm=LLMGateway(provider=Provider()), tools=ToolRegistry(),
        guardrails=GuardrailEngine(policy), eval_harness=EvalHarness(), identity=identity, policy=policy,
        budget=RunBudget(policy), tracer=Tracer("livekit-test"))


async def _drain(agen):
    return [chunk async for chunk in agen]


def test_build_voice_agent_returns_a_real_livekit_agent():
    from livekit.agents import Agent

    agent = build_voice_agent(_echo_graph(), thread_id="room-1", instructions="be helpful")
    assert isinstance(agent, Agent)


def test_llm_node_returns_the_graphs_reply_for_a_plain_turn():
    from livekit.agents import ChatContext, ModelSettings

    agent = build_voice_agent(_echo_graph(), thread_id="room-1")
    chat_ctx = ChatContext()
    chat_ctx.add_message(role="user", content="hello there")

    chunks = asyncio.run(_drain(agent.llm_node(chat_ctx, [], ModelSettings())))

    assert chunks == ["echo: hello there"]


def test_llm_node_carries_identity_into_the_graph_state():
    from livekit.agents import ChatContext, ModelSettings

    seen_state = {}

    class RecordingGraph:
        def invoke(self, state, run_config):
            seen_state.update(state)
            return {"messages": [{"role": "assistant", "content": "ok"}]}

    agent = build_voice_agent(
        RecordingGraph(), thread_id="room-2", identity=Identity(id="caller-1", tenant_id="acme", roles=("agent",)),
    )
    chat_ctx = ChatContext()
    chat_ctx.add_message(role="user", content="hi")

    asyncio.run(_drain(agent.llm_node(chat_ctx, [], ModelSettings())))

    assert seen_state["request_identity"] == {"id": "caller-1", "tenant_id": "acme", "roles": ("agent",)}
    assert seen_state["thread_id"] == "room-2"


def test_llm_node_speaks_a_fallback_line_instead_of_pausing_on_an_interrupt():
    from livekit.agents import ChatContext, ModelSettings

    class InterruptingGraph:
        def invoke(self, state, run_config):
            return {"__interrupt__": [type("I", (), {"value": {"tool": "refund"}})()]}

    agent = build_voice_agent(InterruptingGraph(), thread_id="room-3")
    chat_ctx = ChatContext()
    chat_ctx.add_message(role="user", content="issue a refund")

    chunks = asyncio.run(_drain(agent.llm_node(chat_ctx, [], ModelSettings())))

    assert len(chunks) == 1
    assert "approval" in chunks[0]


def test_llm_node_reports_a_budget_ceiling_as_speech_not_an_exception():
    from livekit.agents import ChatContext, ModelSettings

    from agent_foundry.runtime import BudgetExceeded

    class OverBudgetGraph:
        def invoke(self, state, run_config):
            raise BudgetExceeded("cost ceiling reached")

    agent = build_voice_agent(OverBudgetGraph(), thread_id="room-4")
    chat_ctx = ChatContext()
    chat_ctx.add_message(role="user", content="one more question")

    chunks = asyncio.run(_drain(agent.llm_node(chat_ctx, [], ModelSettings())))

    assert len(chunks) == 1
    assert "usage limit" in chunks[0]


def _lookup_order(order_id: str) -> str:
    return f"order {order_id} is shipped"


def test_to_livekit_function_tool_round_trips_through_the_real_tool_registry():
    spec = ToolSpec(
        name="lookup_order", description="Look up an order's status",
        parameters={"order_id": "string"}, fn=_lookup_order,
    )
    registry = ToolRegistry()
    registry.register(spec)
    identity = Identity(id="caller-1", tenant_id="acme")
    policy = Policy(allowed_tools=frozenset({"lookup_order"}))

    tool = to_livekit_function_tool(spec, registry=registry, identity=identity, policy=policy)

    output = asyncio.run(tool._func({"order_id": "A1"}, context=None))
    assert output == "order A1 is shipped"


def test_to_livekit_function_tool_surfaces_a_policy_denial_as_text_not_an_exception():
    spec = ToolSpec(
        name="lookup_order", description="Look up an order's status",
        parameters={"order_id": "string"}, fn=_lookup_order,
    )
    registry = ToolRegistry()
    registry.register(spec)
    identity = Identity(id="caller-1", tenant_id="acme")
    policy = Policy(allowed_tools=frozenset())  # lookup_order NOT allowed

    tool = to_livekit_function_tool(spec, registry=registry, identity=identity, policy=policy)

    output = asyncio.run(tool._func({"order_id": "A1"}, context=None))
    assert output.startswith("Error:")

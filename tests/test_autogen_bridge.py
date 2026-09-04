import pytest

autogen_agentchat = pytest.importorskip("autogen_agentchat")
autogen_core = pytest.importorskip("autogen_core")

from agent_foundry.autogen_bridge import autogen_as_tool
from agent_foundry.contracts import Identity, Policy
from agent_foundry.tools_gateway import ToolRegistry


def _assistant_agent(reply_text: str):
    from autogen_agentchat.agents import AssistantAgent
    from autogen_ext.models.replay import ReplayChatCompletionClient

    client = ReplayChatCompletionClient([reply_text])
    return AssistantAgent(name="pricing_agent", model_client=client)


def test_autogen_as_tool_wraps_a_real_assistant_agent():
    agent = _assistant_agent("The quoted price is $42.")
    tool = autogen_as_tool(name="pricing_agent", description="Ask the AutoGen pricing agent", agent=agent)
    assert tool.name == "pricing_agent"
    result = tool.fn(query="what does a widget cost?")
    assert "$42" in result


def test_autogen_bridged_agent_composes_through_registry_rbac():
    agent = _assistant_agent("Answer: 42")
    tool = autogen_as_tool(name="pricing_agent", description="pricing", agent=agent)
    registry = ToolRegistry()
    registry.register(tool)

    identity = Identity(id="t", tenant_id="acme")
    policy = Policy(allowed_tools=frozenset({"pricing_agent"}))
    result = registry.invoke("pricing_agent", {"query": "cost?"}, identity=identity, policy=policy)
    assert result.ok and "42" in result.output


def test_a_plain_agent_foundry_tool_function_works_unchanged_as_an_autogen_tool():
    from autogen_core.tools import FunctionTool

    def lookup_order(order_id: str) -> str:
        """Look up an order by id"""
        return f"order {order_id} shipped"

    func_tool = FunctionTool(lookup_order, description="Look up an order by id")
    schema = func_tool.schema
    assert schema["name"] == "lookup_order"
    assert "order_id" in schema["parameters"]["properties"]

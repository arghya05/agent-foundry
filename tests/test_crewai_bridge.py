"""crewai_bridge.crewai_as_tool doesn't import crewai at all — it's duck-typed
against any object with `.kickoff(inputs=...)` returning something with
`.raw`, exactly what a real CrewAI Crew provides. That means these tests
exercise the real adapter logic without needing crewai installed (unlike
autogen_bridge, which does import a real AutoGen package already present in
this environment) — crewai itself was deliberately not installed here after
it caused a real, verified langchain-core/openai version conflict in this
shared environment; see crewai_bridge.py's module docstring.
"""
from __future__ import annotations

from agent_foundry.contracts import Identity, Policy
from agent_foundry.crewai_bridge import crewai_as_tool
from agent_foundry.tools_gateway import ToolRegistry


class _FakeCrewOutput:
    def __init__(self, raw: str) -> None:
        self.raw = raw


class _FakeCrew:
    """Stands in for a real CrewAI Crew's public surface: kickoff(inputs=...)
    -> an object with `.raw`, per CrewAI's documented CrewOutput API."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def kickoff(self, inputs: dict) -> _FakeCrewOutput:
        self.calls.append(inputs)
        return _FakeCrewOutput(self.reply)


def test_crewai_as_tool_wraps_a_crew_and_extracts_raw_output():
    crew = _FakeCrew("The quoted price is $42.")
    tool = crewai_as_tool(name="pricing_crew", description="Ask the CrewAI pricing crew", crew=crew)

    assert tool.name == "pricing_crew"
    result = tool.fn(query="what does a widget cost?")

    assert result == "The quoted price is $42."
    assert crew.calls == [{"query": "what does a widget cost?"}]


def test_crewai_as_tool_respects_a_custom_input_key():
    crew = _FakeCrew("done")
    tool = crewai_as_tool(name="ops_crew", description="ops", crew=crew, input_key="task_description")

    tool.fn(query="escalate order O1")

    assert crew.calls == [{"task_description": "escalate order O1"}]


def test_crewai_bridged_crew_composes_through_registry_rbac():
    crew = _FakeCrew("Answer: 42")
    tool = crewai_as_tool(name="pricing_crew", description="pricing", crew=crew)
    registry = ToolRegistry()
    registry.register(tool)

    identity = Identity(id="t", tenant_id="acme")
    policy = Policy(allowed_tools=frozenset({"pricing_crew"}))
    result = registry.invoke("pricing_crew", {"query": "cost?"}, identity=identity, policy=policy)

    assert result.ok and "42" in result.output

"""crewai_as_tool against a REAL crewai.Agent/Task/Crew/CrewOutput — not the
hand-written stub in test_crewai_bridge.py. Skips automatically wherever
crewai isn't installed (which is everywhere in this project's own shared
environment on purpose — see crewai_bridge.py's module docstring for why:
installing it there previously broke langchain-core/openai version pins for
every other project on that same Python). This was verified once, manually,
in an isolated venv (crewai==1.15.20) before being written up as a test:
Crew.kickoff()'s real signature and CrewOutput's real fields (raw, pydantic,
json_dict, tasks_output, token_usage) match crewai_bridge.py's assumptions
exactly. kickoff() itself is monkeypatched here (not run for real) because it
would otherwise need a live LLM call — the API shape being tested is the
Crew/CrewOutput construction and crewai_as_tool()'s use of them, not
CrewAI's own agent loop.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

crewai = pytest.importorskip("crewai")

from crewai import Agent as CrewAgent  # noqa: E402
from crewai import Crew, Task  # noqa: E402
from crewai.crews.crew_output import CrewOutput  # noqa: E402
from crewai.types.usage_metrics import UsageMetrics  # noqa: E402

from agent_foundry.crewai_bridge import crewai_as_tool  # noqa: E402


def _real_crew() -> Crew:
    agent = CrewAgent(role="Pricing Analyst", goal="Answer pricing questions", backstory="Expert on product pricing.", llm="gpt-4")
    task = Task(description="Answer: {query}", expected_output="A price quote", agent=agent)
    return Crew(agents=[agent], tasks=[task])


def test_crewai_as_tool_against_a_real_crew_and_crewoutput():
    crew = _real_crew()

    def fake_kickoff(self, inputs=None, **kw):
        assert inputs == {"query": "what does a widget cost?"}
        return CrewOutput(raw="The quoted price is $42.", pydantic=None, json_dict=None, tasks_output=[], token_usage=UsageMetrics())

    with patch.object(Crew, "kickoff", fake_kickoff):
        tool = crewai_as_tool(name="pricing_crew", description="Ask the pricing crew", crew=crew)
        result = tool.fn(query="what does a widget cost?")

    assert result == "The quoted price is $42."

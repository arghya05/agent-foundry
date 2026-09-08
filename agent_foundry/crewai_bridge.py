"""CrewAI bridge — incorporates a CrewAI crew as a single tool call, the same
"agents as tools" pattern orchestration.agent_as_tool uses for a
LangGraph-compiled graph and autogen_bridge.autogen_as_tool uses for an
AutoGen agent, applied to a third underlying engine entirely. A CrewAI Crew
(agents + tasks already wired) becomes an ordinary ToolSpec, indistinguishable
downstream from a local function, an MCP tool, or an AutoGen agent — RBAC,
guardrails, the circuit breaker, the audit log all apply to it exactly as
they would to anything else in a ToolRegistry.

The reverse direction needs no adapter at all: a CrewAI Agent's own `tools`
parameter accepts LangChain BaseTool instances, and quickstart.py's
to_langchain_tool() already turns a governed ToolSpec into exactly that.

Requires `pip install crewai`. Written against CrewAI's documented
Crew.kickoff()/CrewOutput API (stable across recent releases) — unlike MCP
and OPA elsewhere in this package, which were verified against real live
processes, there's no CrewAI install round-tripped against this one in this
environment, same posture as events.KafkaEventBus. If you install crewai
yourself, install it in an isolated virtualenv for this project rather than
a shared base Python environment — crewai's own dependency tree pulls in
version-incompatible pins for langchain-core/openai that can break other
projects sharing that environment.
"""
from __future__ import annotations

from typing import Any

from .contracts import ToolSpec


def crewai_as_tool(*, name: str, description: str, crew: Any, input_key: str = "query") -> ToolSpec:
    """Wraps any CrewAI Crew as a ToolSpec — call it like any other tool, get
    its final output back as plain text. `input_key` is the variable name the
    crew's task descriptions interpolate (CrewAI's `kickoff(inputs={...})`
    convention) — override it to match how the crew's tasks were written."""

    def call(query: str) -> str:
        result = crew.kickoff(inputs={input_key: query})
        return getattr(result, "raw", str(result))

    return ToolSpec(name=name, description=description, parameters={"query": "string"}, fn=call)

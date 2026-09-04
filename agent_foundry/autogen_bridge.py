"""AutoGen bridge — incorporates a Microsoft AutoGen agent as a single tool
call, the same "agents as tools" pattern orchestration.agent_as_tool uses for a
LangGraph-compiled graph, applied to a different underlying engine entirely.
Proves cross-framework interop is real: an AutoGen AssistantAgent becomes an
ordinary ToolSpec, indistinguishable downstream from a local function or an
MCP tool — RBAC, guardrails, the circuit breaker, the audit log all apply to
it exactly as they would to anything else in a ToolRegistry.

The reverse direction needs no adapter at all: AssistantAgent's own `tools`
parameter accepts plain Python callables directly, so a tool function written
for Agent Foundry's ToolRegistry already works unchanged as an AutoGen tool.

Requires `pip install autogen-agentchat`.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .contracts import ToolSpec


def autogen_as_tool(*, name: str, description: str, agent: Any) -> ToolSpec:
    """Wraps any AutoGen ConversableAgent/AssistantAgent as a ToolSpec — call it
    like any other tool, get its final response back as plain text."""

    def call(query: str) -> str:
        result = asyncio.run(agent.run(task=query))
        final = result.messages[-1]
        return getattr(final, "content", str(final))

    return ToolSpec(name=name, description=description, parameters={"query": "string"}, fn=call)

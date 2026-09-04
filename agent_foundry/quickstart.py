"""Quickstart — the actual plug-and-play entry point, built on real LangChain and
LangGraph abstractions (langchain.agents.create_agent, langchain_core tools and
messages), not a hand-rolled convention. A junior developer's whole agent is:

    from agent_foundry.quickstart import plug_and_play_agent
    from langchain_anthropic import ChatAnthropic

    def lookup_order(order_id: str) -> str:
        '''Look up the status of an order by its id.'''
        return db.get(order_id)

    agent = plug_and_play_agent(ChatAnthropic(model="claude-sonnet-5"), tools=[lookup_order],
                                 system_prompt="You are a support agent.")
    agent.invoke({"messages": [{"role": "user", "content": "status of order A100?"}]}, config)

`tools` takes plain Python functions — type hints and the docstring become the
tool's schema automatically, no ToolSpec, no JSON schema to write by hand. The
model does real structured tool-calling (AIMessage.tool_calls), not text parsing.

This is the simple path. `orchestration.py`'s AgentConfig/build_*_graph path is
the governed one — RBAC, guardrails, eval, cost/audit, autonomy levels, multi-agent
topologies. to_langchain_tool() below is the bridge between them: a tool already
registered in a ToolRegistry (governed) becomes a plain callable this module's
create_agent-based path accepts too, so a team can start here and grow into the
full framework without rewriting their tools.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from .contracts import ToolSpec


def plug_and_play_agent(
    model: "str | BaseChatModel",
    tools: list[Callable[..., Any]],
    *,
    system_prompt: str,
    checkpointer: Any = None,
):
    """Thin wrapper over langchain.agents.create_agent — the real, current
    (LangGraph v1.0+) prebuilt agent executor. `checkpointer` defaults to an
    in-memory one so multi-turn conversations work out of the box."""
    from langchain.agents import create_agent
    from langgraph.checkpoint.memory import MemorySaver

    return create_agent(model, tools=tools, system_prompt=system_prompt, checkpointer=checkpointer or MemorySaver())


def to_langchain_tool(spec: "ToolSpec") -> Any:
    """Bridges a governed ToolSpec (tools_gateway.ToolRegistry) into a real
    LangChain StructuredTool, so the same tool works in either path. Schema is
    inferred from spec.fn's own type hints, not spec.parameters (which is a loose
    documentation dict, not JSON Schema) — write real type hints on your tool
    functions and both paths get a correct schema for free."""
    from langchain_core.tools import StructuredTool

    return StructuredTool.from_function(func=spec.fn, name=spec.name, description=spec.description)


def to_langchain_tools(registry: Any) -> list[Any]:
    """Every tool in a ToolRegistry, as LangChain StructuredTools."""
    return [to_langchain_tool(registry.get(name)) for name in registry.names()]

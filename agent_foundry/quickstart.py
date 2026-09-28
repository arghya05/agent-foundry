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
topologies. to_langchain_tool() below converts a ToolSpec's callable and
description. It does not carry ToolRegistry or AgentConfig enforcement into
LangChain: policy, approval, audit, rate limits and budgets must be supplied by
the host application. Use the governed Agent path for Foundry controls.
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
    """Convert a raw callable to a LangChain StructuredTool without governance.

    This exports spec.fn directly; it does not preserve registry policy,
    approvals, validation, audit, caching or limits. LangChain infers its schema
    from function type hints instead of using spec.parameters.
    """
    from langchain_core.tools import StructuredTool

    return StructuredTool.from_function(func=spec.fn, name=spec.name, description=spec.description)


def to_langchain_tools(registry: Any) -> list[Any]:
    """Every tool in a ToolRegistry, as LangChain StructuredTools."""
    return [to_langchain_tool(registry.get(name)) for name in registry.names()]

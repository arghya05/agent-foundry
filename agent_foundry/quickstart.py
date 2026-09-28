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
    from .core.execution_context import ExecutionContext
    from .governed_tools import GovernedToolGateway


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


def to_langchain_tool(spec: "ToolSpec", *, allow_unguarded: bool = False) -> Any:
    """Convert a raw callable to a LangChain StructuredTool without governance.

    This exports spec.fn directly; it does not preserve registry policy,
    approvals, validation, audit, caching or limits. LangChain infers its schema
    from function type hints instead of using spec.parameters. Explicit opt-in
    is required; use to_governed_langchain_tool for Foundry enforcement.
    """
    if not allow_unguarded:
        raise ValueError('raw tool export omits Foundry controls; use to_governed_langchain_tool '
                         'or explicitly pass allow_unguarded=True')
    from langchain_core.tools import StructuredTool

    return StructuredTool.from_function(func=spec.fn, name=spec.name, description=spec.description)


def to_langchain_tools(registry: Any, *, allow_unguarded: bool = False) -> list[Any]:
    """Explicitly export raw callables, without retaining registry governance."""
    if not allow_unguarded:
        raise ValueError('raw registry export requires allow_unguarded=True; prefer governed exports')
    return [to_langchain_tool(registry.get(name), allow_unguarded=True) for name in registry.names()]


def to_governed_langchain_tool(
    gateway: "GovernedToolGateway", name: str, *,
    context_provider: "Callable[[], ExecutionContext]",
    required_capabilities: frozenset[str] = frozenset(),
) -> Any:
    """Export a tool through Foundry controls, resolving trusted context per call.

    The provider runs outside the model's argument schema. It must obtain current
    authenticated identity/permissions from the host. Approval requests raise
    ToolApprovalRequired before effects; a durable approval flow is not supplied.
    """
    from langchain_core.tools import StructuredTool, ToolException

    from .guardrails import screen_tool_output
    from .tools_gateway import tool_json_schema

    gateway.require_capabilities(required_capabilities)
    spec = gateway.config.tools.get(name)
    schema = tool_json_schema(spec)['parameters']
    bound_fields = {'user_id', 'tenant_id', 'session_id'}
    schema = {**schema, 'properties': {k: v for k, v in schema.get('properties', {}).items() if k not in bound_fields},
              'required': [k for k in schema.get('required', []) if k not in bound_fields]}

    def output(result):
        if not result.ok:
            raise ToolException(result.error or 'tool execution failed')
        return screen_tool_output(result.output)

    def invoke(**arguments):
        return output(gateway.invoke(name, arguments, context=context_provider()))

    async def ainvoke(**arguments):
        return output(await gateway.ainvoke(name, arguments, context=context_provider()))

    return StructuredTool.from_function(func=invoke, coroutine=ainvoke, name=name,
                                        description=spec.description,
                                        args_schema=schema, infer_schema=False)

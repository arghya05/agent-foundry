"""Async tool function support in ToolRegistry (agent_foundry/tools_gateway.py)
— invoke()/ainvoke() share pre-invoke checks (_pre_invoke) and finishing
(_finish); these tests exercise the part that differs: sync invoke() must
reject an async tool loudly instead of returning an unawaited coroutine as
output, and ainvoke() must actually run either kind. Driven with
asyncio.run() directly rather than pytest-asyncio (not a project dependency)."""
from __future__ import annotations

import asyncio

import pytest

from agent_foundry.contracts import ToolSpec
from agent_foundry.tools_gateway import ToolRegistry


async def _async_lookup(order_id: str) -> str:
    await asyncio.sleep(0)
    return f"order {order_id} shipped (async)"


def _sync_lookup(order_id: str) -> str:
    return f"order {order_id} shipped (sync)"


@pytest.fixture
def async_registry():
    reg = ToolRegistry()
    reg.register(ToolSpec("lookup_order", "Look up an order", {"order_id": "string"}, _async_lookup))
    reg.register(ToolSpec("lookup_order_sync", "Look up an order", {"order_id": "string"}, _sync_lookup))
    return reg


def test_sync_invoke_raises_a_clear_error_on_an_async_tool(identity, policy, async_registry):
    with pytest.raises(TypeError, match="ainvoke"):
        async_registry.invoke("lookup_order", {"order_id": "A100"}, identity=identity, policy=policy)


def test_ainvoke_runs_an_async_tool(identity, policy, async_registry):
    result = asyncio.run(async_registry.ainvoke("lookup_order", {"order_id": "A100"}, identity=identity, policy=policy))
    assert result.ok
    assert "async" in result.output


def test_ainvoke_also_runs_a_plain_sync_tool(identity, async_registry):
    from agent_foundry.contracts import Policy

    policy = Policy(allowed_tools=frozenset({"lookup_order_sync"}))
    result = asyncio.run(async_registry.ainvoke("lookup_order_sync", {"order_id": "A100"}, identity=identity, policy=policy))
    assert result.ok
    assert "sync" in result.output


def test_ainvoke_still_enforces_policy(identity, async_registry):
    from agent_foundry.contracts import Policy
    from agent_foundry.tools_gateway import PermissionDenied

    policy = Policy(allowed_tools=frozenset())
    with pytest.raises(PermissionDenied):
        asyncio.run(async_registry.ainvoke("lookup_order", {"order_id": "A100"}, identity=identity, policy=policy))

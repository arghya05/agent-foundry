import os
import shutil
import sys

import pytest

pytest.importorskip("mcp")

from agent_foundry.contracts import Identity, Policy
from agent_foundry.mcp_tools import MCPToolSource
from agent_foundry.tools_gateway import PermissionDenied, ToolRegistry

_SERVER_SCRIPT = os.path.join(os.path.dirname(__file__), "_mcp_test_server.py")


@pytest.fixture
def mcp_source():
    src = MCPToolSource()
    src.connect_stdio(command=sys.executable, args=[_SERVER_SCRIPT])
    yield src
    src.close()


def test_mcp_tool_discovery_and_invocation(mcp_source):
    registry = ToolRegistry()
    names = mcp_source.register_all(registry)
    assert set(names) == {"add", "fail_on_purpose"}

    identity = Identity(id="t", tenant_id="acme")
    policy = Policy(allowed_tools=frozenset({"add", "fail_on_purpose"}))
    result = registry.invoke("add", {"a": 3, "b": 4}, identity=identity, policy=policy)
    assert result.ok and result.output == "7"


def test_mcp_tool_error_propagates_as_failure(mcp_source):
    registry = ToolRegistry()
    mcp_source.register_all(registry)
    identity = Identity(id="t", tenant_id="acme")
    policy = Policy(allowed_tools=frozenset({"fail_on_purpose"}))
    result = registry.invoke("fail_on_purpose", {}, identity=identity, policy=policy)
    assert not result.ok


def test_mcp_rbac_applies_exactly_like_a_local_tool(mcp_source):
    registry = ToolRegistry()
    mcp_source.register_all(registry)
    identity = Identity(id="t", tenant_id="acme")
    restricted = Policy(allowed_tools=frozenset())
    with pytest.raises(PermissionDenied):
        registry.invoke("add", {"a": 1, "b": 1}, identity=identity, policy=restricted)


def test_local_tool_and_two_mcp_servers_compose_in_one_registry(mcp_source):
    from agent_foundry.contracts import ToolSpec

    registry = ToolRegistry()
    registry.register(ToolSpec("ping", "liveness", {}, lambda: "pong"))
    mcp_source.register_all(registry)
    assert {"ping", "add", "fail_on_purpose"} <= set(registry.names())


@pytest.mark.integration
def test_against_the_real_published_mcp_reference_server():
    """Slower, network/npx-dependent — the official
    @modelcontextprotocol/server-everything, not our own test double. Skipped
    if npx isn't available."""
    if shutil.which("npx") is None:
        pytest.skip("npx not available")

    src = MCPToolSource()
    try:
        src.connect_stdio(command="npx", args=["-y", "@modelcontextprotocol/server-everything"])
    except Exception:
        pytest.skip("could not reach the real MCP reference server (offline?)")
    try:
        registry = ToolRegistry()
        names = src.register_all(registry)
        assert "echo" in names
        identity = Identity(id="t", tenant_id="acme")
        policy = Policy(allowed_tools=frozenset({"echo"}))
        result = registry.invoke("echo", {"message": "hi"}, identity=identity, policy=policy)
        assert result.ok
    finally:
        src.close()

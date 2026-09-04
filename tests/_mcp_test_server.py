"""A tiny local MCP server used only by test_mcp_tools.py — deliberately not
the official reference server, so the default suite runs fast and offline;
the real-published-server check lives in a separately marked test."""
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("pytest-fixture-server")


@mcp.tool()
def add(a: int, b: int) -> str:
    """Add two numbers"""
    return str(a + b)


@mcp.tool()
def fail_on_purpose() -> str:
    """Always raises, to test error propagation"""
    raise ValueError("intentional failure")


if __name__ == "__main__":
    mcp.run()

"""MCP client bridge for the Tools Gateway — connects to any MCP server (stdio or
streamable HTTP) and registers each of its tools into a ToolRegistry as a normal
ToolSpec. This is what makes "connect to any MCP server" literally true: everything
downstream (RBAC, guardrails, circuit breaker, audit log) treats an MCP tool exactly
like a local Python function, with no idea of the difference. Requires `pip install mcp`.

MCP has three primitives, not one: tools, resources, and prompts. list_resources()/
read_resource() and list_prompts()/get_prompt() below cover the other two — a server
can expose readable content (files, data) and reusable prompt templates alongside
its tools, and a builder may want either independent of registering anything into
the Tools Gateway (e.g. pull a resource straight into context.py's semantic memory,
or a prompt template into prompts.py).

The MCP SDK is async; the rest of the framework is sync (orchestration.py calls
tools.invoke() directly, with no event loop running). MCPToolSource bridges the two
by running one persistent event loop on a background thread for the life of the
connection, so a session opened once survives across many synchronous tool calls
instead of reconnecting per call.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from .contracts import ToolSpec
from .tools_gateway import ToolRegistry


class MCPToolSource:
    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._session: Any = None
        self._close_event: Any = None  # asyncio.Event, created on the loop
        self._lifetime_future: Any = None
        self._connect_error: BaseException | None = None

    def _run(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def connect_stdio(self, *, command: str, args: list[str], env: dict[str, str] | None = None) -> None:
        """Launches an MCP server as a subprocess and connects over its stdio."""
        from mcp import StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=command, args=args, env=env or {})
        self._start(lambda: stdio_client(params))

    def connect_http(self, url: str) -> None:
        """Connects to a remote MCP server over streamable HTTP."""
        from mcp.client.streamable_http import streamable_http_client

        self._start(lambda: streamable_http_client(url))

    def _start(self, make_transport: Any) -> None:
        # anyio's cancel scopes are bound to the asyncio Task that entered them, so the
        # transport and session context managers must be entered *and* exited from the
        # same task. That task is this coroutine: it stays alive, parked on
        # close_event.wait(), for the connection's whole lifetime; close() just signals
        # it to unwind.
        ready = threading.Event()

        async def lifetime() -> None:
            from mcp import ClientSession

            close_event = asyncio.Event()
            self._close_event = close_event
            try:
                async with make_transport() as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        self._session = session
                        ready.set()
                        await close_event.wait()
            except BaseException as e:  # surfaced to the connecting thread below
                self._connect_error = e
                ready.set()
                raise

        self._lifetime_future = asyncio.run_coroutine_threadsafe(lifetime(), self._loop)
        if not ready.wait(timeout=30):
            raise TimeoutError("timed out connecting to MCP server")
        if self._connect_error is not None:
            raise self._connect_error

    def close(self) -> None:
        if self._close_event is not None:
            self._loop.call_soon_threadsafe(self._close_event.set)
            self._lifetime_future.result(timeout=10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    def list_tools(self) -> list[Any]:
        assert self._session is not None, "call connect_stdio()/connect_http() first"
        return self._run(self._session.list_tools()).tools

    def list_resources(self) -> list[Any]:
        assert self._session is not None, "call connect_stdio()/connect_http() first"
        return self._run(self._session.list_resources()).resources

    def read_resource(self, uri: str) -> list[str]:
        """Returns the text contents of a resource — binary/blob contents are
        skipped (contents without a `.text` attribute), text is what's usable
        as agent context."""
        result = self._run(self._session.read_resource(uri))
        return [c.text for c in result.contents if getattr(c, "text", None) is not None]

    def list_prompts(self) -> list[Any]:
        assert self._session is not None, "call connect_stdio()/connect_http() first"
        return self._run(self._session.list_prompts()).prompts

    def get_prompt(self, name: str, arguments: dict[str, str] | None = None) -> str:
        """Renders a server-side prompt template, returning its messages joined
        as plain text — usable directly as a system/user prompt."""
        result = self._run(self._session.get_prompt(name, arguments))
        parts = []
        for m in result.messages:
            content = m.content
            text = getattr(content, "text", None)
            if text is not None:
                parts.append(text)
        return "\n".join(parts)

    def register_all(self, registry: ToolRegistry) -> list[str]:
        """Registers every tool the connected server exposes; returns their names."""
        names = []
        for tool in self.list_tools():
            registry.register(self._make_spec(tool))
            names.append(tool.name)
        return names

    def _make_spec(self, tool: Any) -> ToolSpec:
        def call(**kwargs: Any) -> str:
            result = self._run(self._session.call_tool(tool.name, kwargs))
            text = "\n".join(b.text for b in result.content if getattr(b, "type", None) == "text")
            if result.is_error:
                raise RuntimeError(text or f"MCP tool {tool.name!r} failed")
            return text

        return ToolSpec(
            name=tool.name,
            description=tool.description or "",
            parameters=tool.input_schema or {},
            fn=call,
        )

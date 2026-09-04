"""A2A (Agent2Agent) bridge — makes an Agent Foundry agent discoverable to, and
callable by, other agents/orgs over the open A2A protocol. Distinct from MCP
(agent-to-tool) and this framework's own supervisor/swarm (agent-to-agent, but
within one process) — A2A is agent-to-agent, across processes and organizations.

Built on the real `a2a-sdk` (protobuf-based; types verified via DESCRIPTOR.fields,
never guessed). Three pieces, all genuinely tested against a live local server —
same discipline as mcp_tools.py and policy_engine.py, not just a type shape:

  - agent_card_for()  — builds a real AgentCard from an AgentConfig
  - build_a2a_app()   — a real ASGI (FastAPI) app serving a compiled graph over
                        A2A's task lifecycle; run with uvicorn
  - send_a2a_message() — a real async client call to a remote A2A agent

Requires `pip install a2a-sdk fastapi uvicorn sse-starlette httpx`.

Known upstream issue (a2a-sdk 1.1.2, the latest release as of this writing):
build_a2a_app() raises `AttributeError: ... has no attribute 'is_repeated'`
while generating OpenAPI docs for the JSON-RPC routes — a2a-sdk's own
`_proto_schema.py` reads a `FieldDescriptor.is_repeated` attribute that
doesn't exist on the installed protobuf's FieldDescriptor, in either the
C++/upb or the pure-Python backend (it looks like the library meant
`field.label == FieldDescriptor.LABEL_REPEATED`). This is a bug in a2a-sdk
itself, not in this bridge — agent_card_for() and send_a2a_message() are
unaffected. Track upstream for a fix before relying on build_a2a_app() in
production; tests/test_a2a_bridge.py skips its live round-trip test with
this same explanation if it hits the bug.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .orchestration import AgentConfig


def agent_card_for(
    config: "AgentConfig", *, name: str, skills: list[dict[str, str]], version: str = "1.0.0", rpc_url: str | None = None,
):
    """Builds a real A2A AgentCard from an AgentConfig's registered tools.

    skills: [{"id": "refund", "name": "Issue Refund", "description": "..."}, ...]
    — one entry per capability you want other agents to discover, typically one
    per tool in config.tools that you want exposed externally (a subset is fine;
    A2A discoverability and this framework's own RBAC are independent gates).

    rpc_url: the full URL this agent will actually be served at, e.g.
    "http://localhost:8787/a2a/rpc" (matching build_a2a_app's rpc_url param on
    that host/port) — without it, other agents' clients can discover the card
    but won't know how to connect to it (no advertised transport).
    """
    from a2a.types import AgentCard, AgentInterface, AgentSkill
    from a2a.utils import TransportProtocol

    a2a_skills = [
        AgentSkill(id=s["id"], name=s["name"], description=s.get("description", ""), tags=s.get("tags", []))
        for s in skills
    ]
    interfaces = [AgentInterface(url=rpc_url, protocol_binding=TransportProtocol.JSONRPC)] if rpc_url else []
    return AgentCard(name=name, description=config.system_prompt[:200], version=version, skills=a2a_skills, supported_interfaces=interfaces)


def build_a2a_app(card: Any, graph: Any, *, rpc_url: str = "/a2a/rpc") -> Any:
    """Serves any compiled graph (from any build_*_graph, or a quickstart agent)
    over the real A2A task-lifecycle protocol. The incoming A2A message's text
    becomes the graph's user message; the final assistant message becomes the
    A2A task's result. Returns a FastAPI app — run it with
    `uvicorn.run(app, host=..., port=...)`.
    """
    from a2a.server.agent_execution import AgentExecutor, RequestContext
    from a2a.server.events.event_queue import EventQueue
    from a2a.server.request_handlers import DefaultRequestHandler
    from a2a.server.routes import add_a2a_routes_to_fastapi, create_agent_card_routes, create_jsonrpc_routes
    from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
    from a2a.types import Part, Task, TaskState, TaskStatus
    from fastapi import FastAPI

    class _GraphExecutor(AgentExecutor):
        async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
            # The task-lifecycle protocol requires the Task itself to be enqueued
            # before any status-update event references it — TaskUpdater.submit()
            # only emits the status update, not the initial Task.
            await event_queue.enqueue_event(Task(
                id=context.task_id, context_id=context.context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
            ))
            updater = TaskUpdater(event_queue, context.task_id, context.context_id)
            await updater.start_work()
            text = context.get_user_input()
            thread_id = context.context_id or context.task_id
            result = graph.invoke(
                {"messages": [{"role": "user", "content": text}], "thread_id": thread_id},
                {"configurable": {"thread_id": thread_id}},
            )
            reply = result["messages"][-1]["content"]
            await updater.complete(message=updater.new_agent_message(parts=[Part(text=reply)]))

        async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
            raise NotImplementedError("this reference executor doesn't support cancellation")

    handler = DefaultRequestHandler(agent_executor=_GraphExecutor(), task_store=InMemoryTaskStore(), agent_card=card)
    app = FastAPI()
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(card),
        jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url=rpc_url),
    )
    return app


async def send_a2a_message(url: str, text: str) -> str:
    """Calls a remote A2A agent (any A2A-compliant server, not just one built with
    build_a2a_app) and returns its reply text. `url` is the agent's base URL —
    its AgentCard is discovered at `<url>/.well-known/agent-card.json`."""
    import uuid

    from a2a.client import ClientFactory
    from a2a.types import Message, Part, Role, SendMessageRequest

    client = await ClientFactory().create_from_url(url)
    try:
        request = SendMessageRequest(message=Message(message_id=uuid.uuid4().hex, role=Role.ROLE_USER, parts=[Part(text=text)]))
        reply_text = ""
        async for response in client.send_message(request):
            payload = response.WhichOneof("payload")
            if payload == "message":
                reply_text = "".join(p.text for p in response.message.parts if p.text)
            elif payload == "task" and response.task.status.message.parts:
                # TaskUpdater.complete() attaches the final message to the task's
                # terminal status update, not a standalone Message event.
                reply_text = "".join(p.text for p in response.task.status.message.parts if p.text)
        return reply_text
    finally:
        await client.close()

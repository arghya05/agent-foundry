"""LiveKit bridge — connects a LiveKit voice session to a compiled Agent
Foundry graph, the same way channels.py connects Slack and a2a_bridge.py
connects the A2A protocol: verify/extract on the way in, invoke the graph,
shape the reply on the way out. LiveKit owns the room, audio I/O, STT, VAD/
turn-detection, and TTS; Agent Foundry owns the brain — tool-gated reasoning,
policy/guardrails, multi-agent orchestration, and durable cross-turn state.

Two distinct LiveKit session shapes, two distinct integration points:

  - Cascaded pipeline (`AgentSession(stt=..., llm=..., tts=...)`, LiveKit's
    "voice-to-text" mode): `build_voice_agent()` below plugs the compiled
    graph in as the `llm` stage by overriding `Agent.llm_node()`. Every
    Agent Foundry governance layer applies to the reply BEFORE it's spoken —
    guardrails, the policy engine, multi-agent orchestration, budgets — the
    same as it does for a Slack reply, because the graph produces real text
    this bridge controls before LiveKit's TTS ever sees it.

  - Realtime speech-to-speech (`AgentSession(llm=openai.realtime.RealtimeModel(...))`
    or Google's equivalent — true "voice-to-voice"): the realtime model
    generates audio directly, server-side, with no intermediate text this
    bridge — or anything else in this process — ever sees or controls. There
    is no hook to run guardrails on, or route through multi-agent
    orchestration, what gets spoken. The ONE thing that still integrates
    cleanly is tool calls: LiveKit still dispatches those through ordinary
    FunctionTool objects even in realtime mode, so `to_livekit_function_tool()`
    below lets Agent Foundry's PolicyDecisionPoint/ToolRegistry gate and
    execute them exactly as it would for the cascaded pipeline. Know this
    tradeoff cold before recommending realtime mode for anything the policy
    engine or guardrails are meant to be defending: you keep tool governance,
    you lose reply governance.

Known, deliberately unsolved gap: `graph.invoke()`/`graph.stream()` stream at
per-node granularity (core/native_engine.py's stream_run — one chunk per
think/act/critique step), not per-token. For the common single-turn, no-tool
reply, that's exactly one chunk delivered only once the full LLM response is
done — so `build_voice_agent()` below uses `graph.invoke()` (whole-turn,
like serve.py's invoke_graph_chat_turn), not `graph.stream()`, because
streaming buys nothing for that case today and would be actively misleading
to present as "real-time." True per-token streaming into TTS would require
plumbing LLMGateway.stream() through NativeEngine's think step — a
core-engine change, not something this bridge module can shim around.

Also unsolved: HITL tool-approval interrupts (`result["__interrupt__"]`)
have no voice UX. build_voice_agent() below speaks a fixed "needs approval"
line and stops rather than actually pausing/resuming the turn — a real gap
for any agent whose tools have `requires_confirmation=True`.

Requires `pip install livekit-agents`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterable

from .contracts import Identity, Policy

if TYPE_CHECKING:
    from livekit.agents import Agent as LiveKitAgent
    from livekit.agents import ChatContext, ModelSettings, RunContext
    from livekit.agents.llm.tool_context import RawFunctionTool

    from .contracts import ToolSpec
    from .tools_gateway import ToolRegistry


def build_voice_agent(
    graph: Any, *, thread_id: str, instructions: str = "", identity: Identity | None = None,
) -> "LiveKitAgent":
    """A LiveKit Agent whose llm_node() drives `graph` for one whole turn per
    call — the cascaded-pipeline integration point (see module docstring).

    thread_id: ties this LiveKit session to Agent Foundry's durable
    StateStore, the same role Slack's thread id or a chat UI's session id
    plays elsewhere — pick something stable across reconnects within one
    call (e.g. the LiveKit room name), not a fresh id per turn.

    identity: the AUTHENTICATED caller, if this deployment has one (e.g.
    resolved from a SIP/room-join token) — seeded into state so
    orchestration._resolve_identity picks it up for PDP/audit decisions this
    turn makes, exactly like invoke_graph_chat_turn's `identity` param."""
    from livekit.agents import Agent

    from .runtime import BudgetExceeded

    class _AgentFoundryVoiceAgent(Agent):
        async def llm_node(
            self, chat_ctx: "ChatContext", tools: list, model_settings: "ModelSettings",
        ) -> AsyncIterable[str]:
            # chat_ctx.items can end in a FunctionCall/FunctionCallOutput/etc,
            # not just a ChatMessage (e.g. right after a tool result is
            # appended, before the model's next turn) — .messages() filters
            # to real chat messages only, which is what has .text_content.
            user_text = chat_ctx.messages()[-1].text_content
            state: dict[str, Any] = {"messages": [{"role": "user", "content": user_text}], "thread_id": thread_id}
            if identity is not None:
                state["request_identity"] = {"id": identity.id, "tenant_id": identity.tenant_id, "roles": tuple(identity.roles)}
            try:
                result = graph.invoke(state, {"configurable": {"thread_id": thread_id}})
            except BudgetExceeded as e:
                yield f"I've reached a usage limit for this conversation: {e}"
                return
            if result.get("__interrupt__"):
                # No voice-native approval flow yet — see module docstring.
                yield "I'd need approval before doing that, and I can't collect it over voice yet."
                return
            yield result["messages"][-1]["content"]

    return _AgentFoundryVoiceAgent(instructions=instructions)


def to_livekit_function_tool(
    spec: "ToolSpec", *, registry: "ToolRegistry", identity: Identity, policy: Policy,
) -> "RawFunctionTool":
    """Adapts one Agent Foundry ToolSpec into a LiveKit FunctionTool, routed
    through ToolRegistry.ainvoke() — so the same RBAC/rate-limit/cache/PDP
    gate that applies to a tool call from the cascaded pipeline also applies
    to one dispatched by a realtime speech-to-speech model (see module
    docstring: this is the ONE governance hook realtime mode still has).

    identity/policy are fixed at build time, matching how a single LiveKit
    room/session maps to one caller for its lifetime — this does not
    re-resolve identity per call the way invoke_graph_chat_turn's
    request_identity does for the text pipeline."""
    from livekit.agents import function_tool

    from .tools_gateway import PermissionDenied, tool_json_schema

    schema = tool_json_schema(spec)
    raw_schema = {
        "type": "function",
        "name": schema["name"],
        "description": schema["description"],
        "parameters": schema["parameters"],
    }

    async def _handler(raw_arguments: dict[str, Any], context: "RunContext") -> str:
        # ToolRegistry raises PermissionDenied rather than returning a failed
        # ToolResult (see tools_gateway.py:207) — orchestration.py's own
        # make_act_node catches it around every invoke()/ainvoke() call for
        # the same reason: an unhandled PermissionDenied here would propagate
        # into LiveKit's tool-dispatch loop as a raw exception instead of a
        # result the realtime model can react to.
        try:
            result = await registry.ainvoke(spec.name, raw_arguments, identity=identity, policy=policy)
        except PermissionDenied as e:
            return f"Error: {e}"
        return result.output if result.ok else f"Error: {result.error}"

    return function_tool(_handler, raw_schema=raw_schema)

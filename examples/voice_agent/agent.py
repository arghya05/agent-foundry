"""Reference app: a voice agent — the same Agent Foundry brain as
examples/commerce_agent/agent.py (a plain agent_foundry.Agent, one read-only
tool, no destructive/approval-gated tools — see agent_foundry/livekit_bridge.py's
module docstring for why HITL approval has no voice UX yet, so this example
deliberately doesn't need one), fronted by LiveKit's cascaded STT-LLM-TTS
voice pipeline instead of a text chat turn.

Stack, each piece independently swappable without touching the others:
    STT   Deepgram nova-3            (livekit.plugins.deepgram)
    LLM   this agent's own graph     (agent_foundry.livekit_bridge.build_voice_agent)
    TTS   Cartesia sonic-3           (livekit.plugins.cartesia)
    Noise Krisp BVC                  (livekit.plugins.noise_cancellation)

Deepgram's current model lineup (verified against the installed
livekit-plugins-deepgram package, not assumed) has no model literally named
"flash" — nova-3 is the current flagship real-time model; Deepgram's newer
turn-based model is "flux-general-en" (livekit.plugins.deepgram.STTv2). Swap
DEEPGRAM_MODEL below if you specifically need one of those instead.

Run:
    export ANTHROPIC_API_KEY=...      # this agent's brain
    export DEEPGRAM_API_KEY=...       # STT
    export CARTESIA_API_KEY=...       # TTS
    export LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=...
    pip install "agent-foundry[livekit]" "livekit-agents[deepgram,cartesia]" livekit-plugins-noise-cancellation
    python examples/voice_agent/agent.py dev   # joins a LiveKit room and talks
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent_foundry import Agent
from agent_foundry.contracts import Policy, ToolSpec
from agent_foundry.livekit_bridge import build_voice_agent

DEEPGRAM_MODEL = "nova-3"
CARTESIA_MODEL = "sonic-3"

_ORDERS = {
    "A1": "shipped, arriving Thursday",
    "A2": "out for delivery today",
}


def lookup_order(order_id: str) -> str:
    return _ORDERS.get(order_id.upper(), f"no order found with id {order_id}")


def build_agent() -> Agent:
    tools = [ToolSpec("lookup_order", "Look up an order's shipping status by id.", {"order_id": "string"}, lookup_order)]
    policy = Policy(allowed_tools=frozenset({"lookup_order"}), max_cost_usd_per_thread=1.0, max_steps_per_thread=10)
    return Agent(
        "voice-support",
        "You are a friendly voice support assistant. Keep replies short and "
        "conversational — you're being read aloud, not displayed as text. "
        "Use lookup_order to check an order's status; never guess one.",
        tools=tools,
        policy=policy,
    )


agent = build_agent()


def entrypoint() -> None:
    """Only imports the LiveKit SDK here, not at module level — this file
    (and `agent` above) stays importable/testable without livekit-agents
    installed, the same posture channels.py and a2a_bridge.py take for their
    own optional deps."""
    from livekit import agents
    from livekit.agents import AgentServer, room_io
    from livekit.plugins import cartesia, deepgram, noise_cancellation

    server = AgentServer()

    @server.rtc_session(agent_name="voice-support")
    async def run_session(ctx: agents.JobContext) -> None:
        session = agents.AgentSession(
            stt=deepgram.STT(model=DEEPGRAM_MODEL, language="en"),
            tts=cartesia.TTS(model=CARTESIA_MODEL),
        )
        await session.start(
            room=ctx.room,
            agent=build_voice_agent(agent.graph, thread_id=ctx.room.name, instructions=agent.name),
            room_options=room_io.RoomOptions(
                audio_input=room_io.AudioInputOptions(noise_cancellation=noise_cancellation.BVC()),
            ),
        )
        await session.generate_reply(instructions="Greet the caller and ask how you can help.")

    agents.cli.run_app(server)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        entrypoint()  # `python agent.py dev` / `console` / `start` — livekit-agents' own CLI verbs
    else:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise SystemExit("set ANTHROPIC_API_KEY to run this example for real")
        result = agent.run("What's the status of order A1?")
        print(result.content)

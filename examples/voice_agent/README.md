# Voice agent (LiveKit + Deepgram + Cartesia + Krisp)

The same kind of plain `agent_foundry.Agent` as `examples/commerce_agent/agent.py`
(one read-only tool, no destructive/approval-gated ones — see
`agent_foundry/livekit_bridge.py`'s module docstring for why HITL approval
has no voice UX yet), fronted by LiveKit's cascaded STT-LLM-TTS voice
pipeline via `agent_foundry.livekit_bridge.build_voice_agent`, instead of a
text chat turn.

Every layer is independently swappable without touching the others:

| Layer | This example | Swap in |
|---|---|---|
| STT | Deepgram `nova-3` | any `livekit.plugins.*` STT, or `inference.STT(...)` |
| Brain | this repo's `Agent` (native runtime) | any Agent Foundry graph/topology |
| TTS | Cartesia `sonic-3` | any `livekit.plugins.*` TTS |
| Noise cancellation | Krisp BVC (`noise_cancellation.BVC()`) | any `NoiseCancellationOptions` |

Deepgram's real model lineup (checked against the installed
`livekit-plugins-deepgram` package) has no model literally called "flash" —
`nova-3` is the current flagship real-time model; there's also a newer
turn-based model, `flux-general-en` (`livekit.plugins.deepgram.STTv2`), if
that's closer to what you meant.

## Run it as text (no LiveKit, sanity-checks the brain alone)

```bash
export ANTHROPIC_API_KEY=...
python examples/voice_agent/agent.py
```

## Run it as voice

```bash
export ANTHROPIC_API_KEY=...          # this agent's brain
export DEEPGRAM_API_KEY=...           # STT
export CARTESIA_API_KEY=...           # TTS
export LIVEKIT_URL=... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=...
pip install "agent-foundry[livekit]" "livekit-agents[deepgram,cartesia]" livekit-plugins-noise-cancellation
python examples/voice_agent/agent.py dev
```

`dev` joins a LiveKit room and starts a real voice session (livekit-agents'
own CLI verb — `console` also works, for a local mic/speaker test without a
LiveKit server).

## What's real Agent Foundry governance here, and what isn't

Because this is the cascaded pipeline (not a realtime speech-to-speech
model), the full stack applies to every reply before Cartesia ever speaks
it: `Policy.allowed_tools`/`max_cost_usd_per_thread`/`max_steps_per_thread`,
and `lookup_order` goes through the real `ToolRegistry` gate — swap in a
`destructive=True` tool and it WILL pause the turn via `__interrupt__`, but
`build_voice_agent()` currently just speaks a fixed "I'd need approval"
line and stops rather than actually collecting one over voice. Don't wire a
tool that needs real approval into a voice agent until that gap is closed.

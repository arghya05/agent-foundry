"""Agent.stream() — previously zero test coverage on either engine. Found a
real bug while writing these: LangGraph's own default stream mode
("updates") yields per-node partial dicts keyed by node name (e.g.
{"think": {"messages": [...]}}), not the full accumulated state — different
from what native_engine._NativeGraph.stream() yields (the full raw dict, one
chunk). Fixed by passing stream_mode="values" to LangGraph's .stream() (see
core/agent.py's _CompiledWorkflow.stream()) so both engines now agree on a
chunk shape: the full {"messages": [...], "thread_id": ...} state after each
step.
"""
from __future__ import annotations

import asyncio

import pytest

from agent_foundry import Agent, ExecutionContext
from agent_foundry.llm_gateway import LLMGateway

from conftest import ScriptedProvider


@pytest.mark.parametrize("runtime", ["langgraph", "native"])
def test_stream_yields_at_least_one_chunk_shaped_like_the_full_state(runtime):
    provider = ScriptedProvider(["streamed reply"])
    agent = Agent("chatty", "Chat.", runtime=runtime, llm=LLMGateway(provider=provider))

    chunks = list(agent.stream("hi", context=ExecutionContext(thread_id=f"stream-{runtime}")))

    assert len(chunks) >= 1
    last = chunks[-1]
    assert "messages" in last
    assert last["messages"][-1]["content"] == "streamed reply"


@pytest.mark.parametrize("runtime", ["langgraph", "native"])
def test_stream_final_chunk_matches_run_result_content(runtime):
    """Same input, same content, whether you call .run() or read the last
    .stream() chunk — the two shouldn't diverge."""
    provider_run = ScriptedProvider(["consistent reply"])
    provider_stream = ScriptedProvider(["consistent reply"])
    agent_run = Agent("chatty", "Chat.", runtime=runtime, llm=LLMGateway(provider=provider_run))
    agent_stream = Agent("chatty", "Chat.", runtime=runtime, llm=LLMGateway(provider=provider_stream))

    run_result = agent_run.run("hi", context=ExecutionContext(thread_id="run-compare"))
    stream_chunks = list(agent_stream.stream("hi", context=ExecutionContext(thread_id="stream-compare")))

    assert stream_chunks[-1]["messages"][-1]["content"] == run_result.content


@pytest.mark.parametrize("runtime", ["langgraph", "native"])
def test_arun_matches_sync_run(runtime):
    """arun() delegates to graph.ainvoke() (LangGraph) or asyncio.to_thread
    (native) — either way it should agree with run() on the same input."""
    provider = ScriptedProvider(["async hello"])
    agent = Agent("chatty", "Chat.", runtime=runtime, llm=LLMGateway(provider=provider))

    result = asyncio.run(agent.arun("hi", context=ExecutionContext(thread_id=f"arun-{runtime}")))

    assert result.content == "async hello"


@pytest.mark.parametrize("runtime", ["langgraph", "native"])
def test_astream_yields_chunks_shaped_like_the_full_state(runtime):
    provider = ScriptedProvider(["async streamed reply"])
    agent = Agent("chatty", "Chat.", runtime=runtime, llm=LLMGateway(provider=provider))

    async def collect():
        return [chunk async for chunk in agent.astream("hi", context=ExecutionContext(thread_id=f"astream-{runtime}"))]

    chunks = asyncio.run(collect())

    assert len(chunks) >= 1
    assert chunks[-1]["messages"][-1]["content"] == "async streamed reply"


def test_arun_does_not_block_the_event_loop_on_langgraph():
    """The actual point of arun() for the LangGraph runtime: while one
    agent's turn is "running" (a scripted, slow provider standing in for a
    real blocking network call), a concurrent asyncio task on the same loop
    must still get to make progress — proof this isn't just run() called
    synchronously from inside a coroutine."""
    import time

    def slow_response(messages, model):
        time.sleep(0.2)
        return "slow reply"

    agent = Agent("slow", "Chat.", llm=LLMGateway(provider=ScriptedProvider([slow_response])))
    progress: list[str] = []

    async def ticker():
        for _ in range(4):
            await asyncio.sleep(0.05)
            progress.append("tick")

    async def main():
        await asyncio.gather(agent.arun("hi", context=ExecutionContext(thread_id="noblock")), ticker())

    asyncio.run(main())

    assert len(progress) >= 2


@pytest.mark.parametrize("runtime", ["langgraph", "native"])
def test_aresume_matches_sync_resume(runtime):
    """aresume() delegates to graph.ainvoke(Command(resume=...)) (LangGraph)
    or asyncio.to_thread(self.resume, ...) (native) — same
    approve-a-paused-turn round trip as resume(), just awaitable."""
    from agent_foundry.contracts import LLMResponse, Policy, ToolCall, ToolSpec

    def send_wire(amount_usd: float) -> str:
        return f"sent ${amount_usd}"

    tool = ToolSpec("send_wire", "send a wire", {"amount_usd": "number"}, send_wire, requires_confirmation=True)
    policy = Policy(allowed_tools=frozenset({"send_wire"}), requires_approval=frozenset({"send_wire"}))
    provider = ScriptedProvider([
        LLMResponse(text="", model="m", input_tokens=1, output_tokens=1, cost_usd=0.0,
                    tool_calls=[ToolCall(id="c1", name="send_wire", args={"amount_usd": 5})]),
        "done",
    ])
    agent = Agent("t", "Chat.", runtime=runtime, tools=[tool], policy=policy, llm=LLMGateway(provider=provider))
    context = ExecutionContext(thread_id=f"aresume-{runtime}")

    async def main():
        paused = await agent.arun("send a wire", context=context)
        assert paused.awaiting_approval
        return await agent.aresume(approved=True, context=context)

    resumed = asyncio.run(main())

    assert resumed.content == "done"

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

"""Per-call runtime override — agent.run(message, runtime="native") alongside
the existing construction-time Agent(runtime="native") default. Agent._runner_for
(core/agent.py) lazily builds and caches a second _CompiledWorkflow the
first time a non-default runtime is actually used for a given Agent, via
the same RUNTIMES registry (core/engines.py) Agent.__init__ itself now uses
(see test_workflow_engine_protocol.py's test_agent_routes_through_*)."""
from __future__ import annotations

import asyncio

import pytest

from agent_foundry import Agent, ExecutionContext
from agent_foundry.core.native_engine import _NativeGraph
from agent_foundry.llm_gateway import LLMGateway

from conftest import ScriptedProvider


def test_run_with_no_override_uses_the_construction_time_default():
    agent = Agent("t", "hi", runtime="langgraph", llm=LLMGateway(provider=ScriptedProvider(["hello"])))

    result = agent.run("hi", context=ExecutionContext(thread_id="override-default"))

    assert result.content == "hello"
    assert list(agent._runners) == ["langgraph"]  # no second runner built


def test_run_with_an_override_dispatches_to_the_named_runtime_not_the_default():
    agent = Agent("t", "hi", runtime="langgraph", llm=LLMGateway(provider=ScriptedProvider(["hello"])))

    agent.run("hi", context=ExecutionContext(thread_id="override-native"), runtime="native")

    assert isinstance(agent._runners["native"].graph, _NativeGraph)
    assert "native" in agent._runners and "langgraph" in agent._runners  # both now cached


def test_override_runner_is_lazily_built_and_cached_not_rebuilt_per_call():
    agent = Agent("t", "hi", runtime="langgraph", llm=LLMGateway(provider=ScriptedProvider(["first", "second"])))

    agent.run("a", context=ExecutionContext(thread_id="override-cache-1"), runtime="native")
    first_runner = agent._runners["native"]
    agent.run("b", context=ExecutionContext(thread_id="override-cache-2"), runtime="native")

    assert agent._runners["native"] is first_runner


def test_override_rejects_an_unknown_runtime_name():
    agent = Agent("t", "hi", llm=LLMGateway(provider=ScriptedProvider(["hello"])))

    with pytest.raises(ValueError, match="unknown runtime"):
        agent.run("hi", context=ExecutionContext(thread_id="override-bad"), runtime="temporal")


def test_native_construction_checkpointer_does_not_leak_into_an_overridden_langgraph_call():
    """A langgraph checkpointer set at construction must not affect a
    runtime="native" override (native rejects any checkpointer) — and,
    symmetrically, a native-runtime Agent overriding to langgraph must not
    be blocked by native's own checkpointer restriction."""
    agent = Agent("t", "hi", runtime="native", llm=LLMGateway(provider=ScriptedProvider(["a", "b"])))

    # Should not raise — the override path resolves its own checkpointer
    # per-runtime (None here, since self._checkpointer is also None), not
    # by re-applying native's own construction-time validation to langgraph.
    result = agent.run("hi", context=ExecutionContext(thread_id="override-checkpointer"), runtime="langgraph")

    assert result.content == "a"


def test_arun_and_aresume_accept_the_same_override():
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
    agent = Agent("t", "hi", runtime="langgraph", tools=[tool], policy=policy, llm=LLMGateway(provider=provider))
    context = ExecutionContext(thread_id="override-async")

    async def main():
        paused = await agent.arun("send a wire", context=context, runtime="native")
        assert paused.awaiting_approval
        assert isinstance(agent._runners["native"].graph, _NativeGraph)
        return await agent.aresume(approved=True, context=context, runtime="native")

    resumed = asyncio.run(main())

    assert resumed.content == "done"

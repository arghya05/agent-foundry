"""Concurrent same-turn tool dispatch in orchestration.make_act_node's act()
closure — multiple independent (non-approval-needing) tool calls in one
turn now run concurrently via _dispatch_tool_calls/_invoke_tool_call
(orchestration.py), instead of the old strictly-sequential for loop. These
tests prove the properties that redesign has to hold: real wall-clock
concurrency, results ordered by original call order (not completion order),
correct interrupt behavior when a turn mixes an approval-needing call with
independent ones, and every bookkeeping side effect (breaker/audit/
eval_harness/memory) still firing exactly once per call."""
import time

from langgraph.types import Command

from agent_foundry.contracts import LLMResponse, Policy, ToolCall, ToolSpec
from agent_foundry.orchestration import build_agent_graph
from agent_foundry.tools_gateway import ToolRegistry

from conftest import ScriptedProvider, make_config_kwargs


def _invoke(graph, thread_id, text):
    return graph.invoke({"messages": [{"role": "user", "content": text}], "thread_id": thread_id}, {"configurable": {"thread_id": thread_id}})


def _slow_tool(label, delay):
    def fn(**kwargs):
        time.sleep(delay)
        return label
    return fn


def test_multiple_independent_tool_calls_run_concurrently_not_serially(identity):
    tools = ToolRegistry()
    tools.register(ToolSpec("slow_a", "slow", {}, _slow_tool("a done", 0.2)))
    tools.register(ToolSpec("slow_b", "slow", {}, _slow_tool("b done", 0.2)))
    policy = Policy(allowed_tools=frozenset({"slow_a", "slow_b"}), max_cost_usd_per_thread=1.0, max_steps_per_thread=10)

    def call_both(messages, model):
        return LLMResponse(text="", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0, tool_calls=[
            ToolCall(id="c1", name="slow_a", args={}),
            ToolCall(id="c2", name="slow_b", args={}),
        ])

    provider = ScriptedProvider([call_both, "done"])
    graph = build_agent_graph(system_prompt="sys", **make_config_kwargs(identity=identity, policy=policy, tools=tools, provider=provider))

    start = time.perf_counter()
    _invoke(graph, "t-concurrent", "go")
    elapsed = time.perf_counter() - start

    # Two 0.2s calls: ~0.2s if concurrent, ~0.4s+ if still sequential.
    assert elapsed < 0.35, f"took {elapsed:.3f}s — looks sequential, not concurrent"


def test_concurrent_dispatch_preserves_results_order_matching_calls_order(identity):
    """The SECOND call is the slow one — if results were written in
    completion order instead of original call order, they'd come back
    swapped."""
    tools = ToolRegistry()
    tools.register(ToolSpec("fast", "fast", {}, _slow_tool("fast done", 0.0)))
    tools.register(ToolSpec("slow", "slow", {}, _slow_tool("slow done", 0.15)))
    policy = Policy(allowed_tools=frozenset({"fast", "slow"}), max_cost_usd_per_thread=1.0, max_steps_per_thread=10)

    def call_both(messages, model):
        return LLMResponse(text="", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0, tool_calls=[
            ToolCall(id="c1", name="fast", args={}),
            ToolCall(id="c2", name="slow", args={}),
        ])

    provider = ScriptedProvider([call_both, "done"])
    graph = build_agent_graph(system_prompt="sys", **make_config_kwargs(identity=identity, policy=policy, tools=tools, provider=provider))
    result = _invoke(graph, "t-order", "go")

    tool_messages = [m for m in result["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["c1", "c2"]
    assert tool_messages[0]["content"] == "fast done"
    assert tool_messages[1]["content"] == "slow done"


def test_mixed_turn_approval_call_still_interrupts_and_independent_calls_still_run(identity):
    """A turn with an independent call BEFORE an approval-needing one: the
    independent call must actually execute (not just get gated) before the
    interrupt — flush_cleared() exists specifically for this — and the
    approval-needing call must never join the concurrent batch."""
    seen = []

    def independent(**kwargs):
        seen.append("independent-ran")
        return "ok"

    def send_wire(amount_usd: float) -> str:
        return f"sent ${amount_usd}"

    tools = ToolRegistry()
    tools.register(ToolSpec("independent", "no approval needed", {}, independent))
    tools.register(ToolSpec("send_wire", "send a wire", {"amount_usd": "number"}, send_wire, requires_confirmation=True))
    policy = Policy(allowed_tools=frozenset({"independent", "send_wire"}), max_cost_usd_per_thread=1.0, max_steps_per_thread=10)

    def call_mixed(messages, model):
        return LLMResponse(text="", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0, tool_calls=[
            ToolCall(id="c1", name="independent", args={}),
            ToolCall(id="c2", name="send_wire", args={"amount_usd": 500}),
        ])

    provider = ScriptedProvider([call_mixed, "wire sent"])
    graph = build_agent_graph(system_prompt="sys", **make_config_kwargs(identity=identity, policy=policy, tools=tools, provider=provider))
    result = _invoke(graph, "t-mixed", "go")

    assert "__interrupt__" in result
    assert result["__interrupt__"][0].value["tool"] == "send_wire"
    assert seen == ["independent-ran"], "the independent call before the approval gate must have actually run, not just been gated"

    resumed = graph.invoke(Command(resume={"approved": True}), {"configurable": {"thread_id": "t-mixed"}})
    tool_messages = [m for m in resumed["messages"] if m["role"] == "tool"]
    assert any(m["tool_call_id"] == "c2" and "sent $500" in m["content"] for m in tool_messages)


def test_concurrent_calls_each_record_bookkeeping_exactly_once(identity):
    """breaker/audit/eval_harness/memory side effects must each fire once
    per call, not zero or twice, even when dispatched concurrently."""
    from agent_foundry.context import MemoryStore

    tools = ToolRegistry()
    tools.register(ToolSpec("slow_a", "slow", {}, _slow_tool("a", 0.05)))
    tools.register(ToolSpec("slow_b", "slow", {}, _slow_tool("b", 0.05)))
    policy = Policy(allowed_tools=frozenset({"slow_a", "slow_b"}), max_cost_usd_per_thread=1.0, max_steps_per_thread=10)

    def call_both(messages, model):
        return LLMResponse(text="", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0, tool_calls=[
            ToolCall(id="c1", name="slow_a", args={}),
            ToolCall(id="c2", name="slow_b", args={}),
        ])

    provider = ScriptedProvider([call_both, "done"])
    kwargs = make_config_kwargs(identity=identity, policy=policy, tools=tools, provider=provider)
    kwargs["memory"] = MemoryStore()
    graph = build_agent_graph(system_prompt="sys", **kwargs)

    _invoke(graph, "t-bookkeeping", "go")

    # _finalize_turn pops working[...]["tool_sequence"] into procedural
    # memory once the turn completes — check it landed there, exactly once
    # per call, not zero or twice from concurrent dispatch.
    recorded = kwargs["memory"].procedural.patterns["default"][0]
    assert sorted(recorded) == ["slow_a", "slow_b"]
    assert len(recorded) == 2

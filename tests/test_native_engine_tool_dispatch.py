"""Concurrent same-turn tool dispatch in native_engine.NativeEngine._act —
the same _dispatch_tool_calls/_invoke_tool_call orchestration.py's act()
closure now uses (see test_orchestration_concurrent_tools.py for the
LangGraph-engine version of these same properties). Proves the native
engine's own pause/resume mechanism (a plain early `return`, not LangGraph's
interrupt()) still gets the same ordering guarantee: calls gated before an
approval-needing one have genuinely run by the time a caller sees the pause."""
import time

from agent_foundry import Agent, ExecutionContext
from agent_foundry.contracts import Policy, ToolSpec
from agent_foundry.llm_gateway import LLMGateway

from conftest import ScriptedProvider


def _slow_tool(label, delay):
    def fn(**kwargs):
        time.sleep(delay)
        return label
    return fn


def test_native_multiple_independent_tool_calls_run_concurrently_not_serially():
    from agent_foundry.contracts import LLMResponse, ToolCall

    def call_both(messages, model):
        return LLMResponse(text="", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0, tool_calls=[
            ToolCall(id="c1", name="slow_a", args={}),
            ToolCall(id="c2", name="slow_b", args={}),
        ])

    tools = [
        ToolSpec("slow_a", "slow", {}, _slow_tool("a done", 0.2)),
        ToolSpec("slow_b", "slow", {}, _slow_tool("b done", 0.2)),
    ]
    policy = Policy(allowed_tools=frozenset({"slow_a", "slow_b"}), max_cost_usd_per_thread=1.0, max_steps_per_thread=10)
    provider = ScriptedProvider([call_both, "done"])
    agent = Agent("t", "Chat.", runtime="native", tools=tools, policy=policy, llm=LLMGateway(provider=provider))

    start = time.perf_counter()
    agent.run("go", context=ExecutionContext(thread_id="native-concurrent"))
    elapsed = time.perf_counter() - start

    assert elapsed < 0.35, f"took {elapsed:.3f}s — looks sequential, not concurrent"


def test_native_concurrent_dispatch_preserves_results_order_matching_calls_order():
    from agent_foundry.contracts import LLMResponse, ToolCall

    def call_both(messages, model):
        return LLMResponse(text="", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0, tool_calls=[
            ToolCall(id="c1", name="fast", args={}),
            ToolCall(id="c2", name="slow", args={}),
        ])

    tools = [
        ToolSpec("fast", "fast", {}, _slow_tool("fast done", 0.0)),
        ToolSpec("slow", "slow", {}, _slow_tool("slow done", 0.15)),
    ]
    policy = Policy(allowed_tools=frozenset({"fast", "slow"}), max_cost_usd_per_thread=1.0, max_steps_per_thread=10)
    provider = ScriptedProvider([call_both, "done"])
    agent = Agent("t", "Chat.", runtime="native", tools=tools, policy=policy, llm=LLMGateway(provider=provider))

    result = agent.run("go", context=ExecutionContext(thread_id="native-order"))

    tool_messages = [m for m in result.raw["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["c1", "c2"]
    assert tool_messages[0]["content"] == "fast done"
    assert tool_messages[1]["content"] == "slow done"


def test_native_mixed_turn_approval_call_still_pauses_and_independent_calls_still_run():
    from agent_foundry.contracts import LLMResponse, ToolCall

    seen = []

    def independent(**kwargs):
        seen.append("independent-ran")
        return "ok"

    def send_wire(amount_usd: float) -> str:
        return f"sent ${amount_usd}"

    tools = [
        ToolSpec("independent", "no approval needed", {}, independent),
        ToolSpec("send_wire", "send a wire", {"amount_usd": "number"}, send_wire, requires_confirmation=True),
    ]
    policy = Policy(allowed_tools=frozenset({"independent", "send_wire"}), max_cost_usd_per_thread=1.0, max_steps_per_thread=10)

    def call_mixed(messages, model):
        return LLMResponse(text="", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0, tool_calls=[
            ToolCall(id="c1", name="independent", args={}),
            ToolCall(id="c2", name="send_wire", args={"amount_usd": 500}),
        ])

    provider = ScriptedProvider([call_mixed, "wire sent"])
    agent = Agent("t", "Chat.", runtime="native", tools=tools, policy=policy, llm=LLMGateway(provider=provider))
    context = ExecutionContext(thread_id="native-mixed")

    paused = agent.run("go", context=context)

    assert paused.awaiting_approval is True
    assert paused.raw["__interrupt__"][0].value["tool"] == "send_wire"
    assert seen == ["independent-ran"], "the independent call before the approval gate must have actually run, not just been gated"

    resumed = agent.resume(approved=True, context=context)
    tool_messages = [m for m in resumed.raw["messages"] if m["role"] == "tool"]
    assert any(m["tool_call_id"] == "c2" and "sent $500" in m["content"] for m in tool_messages)

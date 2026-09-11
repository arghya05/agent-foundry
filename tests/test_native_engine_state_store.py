"""Proves NativeEngine's state_store= wiring does what it claims — a real
process-restart durability test, not just "the store's own save/load work"
(that's test_state_store.py's job). A brand-new NativeEngine instance
(standing in for a fresh process) sharing the same StateStore as an earlier
one must be able to continue that earlier instance's thread_id.
"""
from __future__ import annotations

import threading
import time

from agent_foundry import Agent, ExecutionContext
from agent_foundry.core.native_engine import NativeEngine
from agent_foundry.core.state_store import MemoryStateStore
from agent_foundry.llm_gateway import LLMGateway

from conftest import ScriptedProvider


def test_a_fresh_native_engine_continues_an_earlier_ones_thread_via_the_shared_store():
    store = MemoryStateStore()

    first = Agent("bot", "chat", runtime="native", llm=LLMGateway(provider=ScriptedProvider(["hello!"])), state_store=store)
    result = first.run("hi", context=ExecutionContext(thread_id="restart-thread"))
    assert result.content == "hello!"

    # A brand-new Agent/NativeEngine instance — standing in for a process
    # restart — sharing only the store, not the first Agent's own in-process
    # NativeEngine object.
    second = Agent("bot", "chat", runtime="native", llm=LLMGateway(provider=ScriptedProvider(["welcome back!"])), state_store=store)
    result2 = second.run("hi again", context=ExecutionContext(thread_id="restart-thread"))

    assert result2.content == "welcome back!"
    assert [m["content"] for m in result2.messages] == ["hi", "hello!", "hi again", "welcome back!"]


def test_without_a_state_store_a_fresh_native_engine_starts_a_new_conversation():
    """Confirms the default (no state_store) behavior is unchanged — a
    second, independent NativeEngine (no shared store) must NOT see the
    first one's history, same as before state_store existed."""
    first = Agent("bot", "chat", runtime="native", llm=LLMGateway(provider=ScriptedProvider(["hello!"])))
    first.run("hi", context=ExecutionContext(thread_id="no-store-thread"))

    second = Agent("bot", "chat", runtime="native", llm=LLMGateway(provider=ScriptedProvider(["fresh start"])))
    result2 = second.run("hi again", context=ExecutionContext(thread_id="no-store-thread"))

    assert result2.content == "fresh start"
    assert [m["content"] for m in result2.messages] == ["hi again", "fresh start"]


def test_state_store_round_trips_a_tool_approval_pause_across_a_fresh_engine():
    """The store must persist mid-turn state too (a pending tool approval),
    not just completed turns — resume() on a fresh instance sharing the
    store should be able to continue a paused turn started elsewhere."""
    from agent_foundry.contracts import Policy, ToolSpec

    def send_wire(amount_usd: float) -> str:
        return f"sent ${amount_usd}"

    tool = ToolSpec("send_wire", "send a wire", {"amount_usd": "number"}, send_wire, requires_confirmation=True)
    policy = Policy(allowed_tools=frozenset({"send_wire"}), requires_approval=frozenset({"send_wire"}))
    store = MemoryStateStore()

    from agent_foundry.contracts import LLMResponse, ToolCall

    provider = ScriptedProvider([
        LLMResponse(text="", model="m", input_tokens=1, output_tokens=1, cost_usd=0.0,
                    tool_calls=[ToolCall(id="c1", name="send_wire", args={"amount_usd": 5})]),
    ])
    first = Agent("payer", "Pay.", runtime="native", tools=[tool], policy=policy, llm=LLMGateway(provider=provider), state_store=store)
    paused = first.run("send $5", context=ExecutionContext(thread_id="pause-thread"))
    assert paused.awaiting_approval

    provider2 = ScriptedProvider(["done"])
    second = Agent("payer", "Pay.", runtime="native", tools=[tool], policy=policy, llm=LLMGateway(provider=provider2), state_store=store)
    resumed = second.resume(approved=True, context=ExecutionContext(thread_id="pause-thread"))

    assert resumed.content == "done"


def test_a_slow_state_store_load_for_one_thread_does_not_block_a_different_thread_id():
    """_state_for()'s cache-miss load() is potentially slow network I/O
    (Redis/Postgres) — it must happen OUTSIDE the shared _threads_lock, or
    one thread_id's slow load blocks every OTHER thread_id's unrelated
    _state_for/_persist call too, defeating the whole point of NativeEngine's
    own per-thread_id locking design."""

    class _BlockingLoadStore:
        def __init__(self) -> None:
            self.entered = threading.Event()
            self.release = threading.Event()

        def load(self, run_id):
            if run_id == "slow-thread":
                self.entered.set()
                self.release.wait(timeout=5)
            return None

        def save(self, run_id, state):
            pass

        def delete(self, run_id):
            pass

    store = _BlockingLoadStore()
    engine = NativeEngine(state_store=store)
    config_a = Agent("bot", "chat", llm=LLMGateway(provider=ScriptedProvider(["slow reply"]))).config
    config_b = Agent("bot", "chat", llm=LLMGateway(provider=ScriptedProvider(["fast reply"]))).config

    thread_a = threading.Thread(target=lambda: engine.run(config_a, "hi", thread_id="slow-thread"))
    thread_a.start()
    store.entered.wait(timeout=5)  # thread A is now blocked inside store.load()

    start = time.time()
    result_b = engine.run(config_b, "hi", thread_id="fast-thread")
    elapsed = time.time() - start

    assert result_b["messages"][-1]["content"] == "fast reply"
    assert elapsed < 1.0, f"thread B was blocked by thread A's slow store.load() ({elapsed:.2f}s)"

    store.release.set()
    thread_a.join(timeout=5)

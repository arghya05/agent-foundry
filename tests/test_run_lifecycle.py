"""Tests for Agent.start() / core.run.Run — the formal run lifecycle on top
of Agent.run()/.resume() (which stay unchanged, verified by the rest of the
suite still passing untouched).
"""
from __future__ import annotations

import pytest

from agent_foundry import Agent, ExecutionContext, RunStatus
from agent_foundry.events import InMemoryEventBus
from agent_foundry.kpi import KPI
from agent_foundry.llm_gateway import LLMGateway
from agent_foundry.orchestration import CritiqueConfig
from agent_foundry.runtime import BudgetExceeded

from conftest import ScriptedProvider


@pytest.mark.parametrize("runtime", ["langgraph", "native"])
def test_run_completes_and_reports_status(runtime):
    provider = ScriptedProvider(["hello there"])
    agent = Agent("greeter", "Say hi.", runtime=runtime, llm=LLMGateway(provider=provider))

    run = agent.start("hi")

    assert run.status == RunStatus.COMPLETED
    assert run.result.content == "hello there"


@pytest.mark.parametrize("runtime", ["langgraph", "native"])
def test_run_reports_waiting_human_and_resume_completes_it(runtime):
    kpi = KPI(name="confidence", score=lambda ctx: ctx["score"], direction="maximize", threshold=0.5)
    critique = CritiqueConfig(kpi=kpi, context=lambda state, draft: {"score": 0.02}, escalate_threshold=0.05)
    provider = ScriptedProvider(["an ambiguous answer"])
    agent = Agent("advisor", "Answer.", runtime=runtime, llm=LLMGateway(provider=provider), critique=critique)

    run = agent.start("what should I do?")
    assert run.status == RunStatus.WAITING_HUMAN

    result = run.resume(approved=True)
    assert run.status == RunStatus.COMPLETED
    assert result.content == "an ambiguous answer"


def test_run_resume_without_a_pending_approval_raises():
    provider = ScriptedProvider(["hi"])
    agent = Agent("greeter", "Say hi.", runtime="native", llm=LLMGateway(provider=provider))
    run = agent.start("hi")

    with pytest.raises(RuntimeError):
        run.resume(approved=True)


def test_run_pause_blocks_further_activity_until_unpause():
    provider = ScriptedProvider(["one", "two"])
    agent = Agent("chatty", "Chat.", runtime="native", llm=LLMGateway(provider=provider))
    run = agent.start("hi")
    assert run.status == RunStatus.COMPLETED

    run.pause()
    assert run.status == RunStatus.SUSPENDED
    with pytest.raises(RuntimeError):
        run.run("again")

    run.unpause()
    assert run.status == RunStatus.COMPLETED
    result = run.run("again")
    assert result.content == "two"


def test_run_cancel_blocks_further_activity():
    provider = ScriptedProvider(["one"])
    agent = Agent("chatty", "Chat.", runtime="native", llm=LLMGateway(provider=provider))
    run = agent.start("hi")

    run.cancel()
    assert run.status == RunStatus.CANCELLED
    with pytest.raises(RuntimeError):
        run.run("again")


def test_run_failed_turn_sets_status_and_error():
    from agent_foundry.contracts import Policy
    from agent_foundry.runtime import RunBudget

    policy = Policy(max_cost_usd_per_thread=0.0)  # any spend at all exceeds this
    provider = ScriptedProvider(["a reply that costs something"])

    class ExpensiveProvider:
        def complete(self, messages, *, model, tools=None, **kw):
            from agent_foundry.contracts import LLMResponse
            return LLMResponse(text="hi", model=model, input_tokens=1, output_tokens=1, cost_usd=1.0)

    agent = Agent("pricey", "Answer.", runtime="native", policy=policy, budget=RunBudget(policy), llm=LLMGateway(provider=ExpensiveProvider()))

    with pytest.raises(BudgetExceeded):
        agent.start("hi")


def test_run_fork_creates_an_independent_thread_with_the_same_history():
    provider = ScriptedProvider(["first reply", "on the original", "on the fork"])
    agent = Agent("chatty", "Chat.", runtime="native", llm=LLMGateway(provider=provider))
    run = agent.start("hi")

    forked = run.fork()
    assert forked.result.messages == run.result.messages
    assert forked.context.thread_id != run.context.thread_id

    original_next = run.run("continue original")
    fork_next = forked.run("continue fork")

    assert original_next.content == "on the original"
    assert fork_next.content == "on the fork"
    # the fork's own continuation didn't leak into the original thread
    assert "on the fork" not in [m["content"] for m in run.result.messages]


def test_run_fork_works_on_the_langgraph_engine_too():
    provider = ScriptedProvider(["first reply", "on the fork"])
    agent = Agent("chatty", "Chat.", runtime="langgraph", llm=LLMGateway(provider=provider))
    run = agent.start("hi")

    forked = run.fork()
    fork_next = forked.run("continue fork")

    assert fork_next.content == "on the fork"
    assert forked.context.thread_id != run.context.thread_id


def test_run_retry_reattempts_the_last_turn_as_a_new_run():
    provider = ScriptedProvider(["first attempt", "second attempt"])
    agent = Agent("chatty", "Chat.", runtime="native", llm=LLMGateway(provider=provider))
    run = agent.start("hi")
    assert run.result.content == "first attempt"

    retried = run.retry()

    assert retried.status == RunStatus.COMPLETED
    assert retried.result.content == "second attempt"
    assert retried.context.thread_id != run.context.thread_id
    # original run's own recorded result is untouched by the retry
    assert run.result.content == "first attempt"


def test_run_retry_without_a_prior_run_raises():
    from agent_foundry.core.run import Run

    provider = ScriptedProvider(["hi"])
    agent = Agent("chatty", "Chat.", runtime="native", llm=LLMGateway(provider=provider))
    run = Run(agent=agent, context=ExecutionContext())

    with pytest.raises(RuntimeError):
        run.retry()


def test_run_replay_returns_the_transcript():
    provider = ScriptedProvider(["reply one"])
    agent = Agent("chatty", "Chat.", runtime="native", llm=LLMGateway(provider=provider))
    run = agent.start("hi")

    transcript = run.replay()

    assert transcript == run.result.messages
    assert transcript[0] == {"role": "user", "content": "hi"}


def test_run_wait_for_event_resumes_automatically_when_the_event_fires():
    provider = ScriptedProvider(["started monitoring", "order O1 has been escalated"])
    agent = Agent("ops", "Handle ops events.", runtime="native", llm=LLMGateway(provider=provider))
    bus = InMemoryEventBus()
    run = agent.start("start monitoring")
    assert run.status == RunStatus.COMPLETED

    # now suspend it waiting for a specific event instead of a new .run() call
    run.wait_for_event("order.delayed", bus=bus, on_event=lambda event: f"order {event['order_id']} was delayed")
    assert run.status == RunStatus.WAITING_EVENT

    bus.publish("order.delayed", {"order_id": "O1"})

    assert run.status == RunStatus.COMPLETED
    assert run.result.content == "order O1 has been escalated"


def test_run_wait_for_event_ignored_after_cancel():
    provider = ScriptedProvider(["should not run"])
    agent = Agent("ops", "Handle ops events.", runtime="native", llm=LLMGateway(provider=provider))
    bus = InMemoryEventBus()
    run = agent.start("start")
    calls_before = len(provider.calls)
    run.wait_for_event("order.delayed", bus=bus)
    run.cancel()

    bus.publish("order.delayed", {"order_id": "O1"})

    assert len(provider.calls) == calls_before  # the cancelled run never re-ran
    assert run.status == RunStatus.CANCELLED

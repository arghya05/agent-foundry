"""Tests for agent_foundry.core.native_orchestration — the native (LangGraph-
free) counterparts of all 6 multi-agent topologies, reached via
`Workflow.*(..., runtime="native")`. Mirrors test_core_agent.py's own
Workflow.* scenarios exactly (same ScriptedProvider scripts, same
assertions), just requesting the native runtime instead of the LangGraph
default — the same "mirrors the other engine's own test scenarios"
convention test_native_engine.py already uses for the single-agent case.
"""
from __future__ import annotations

import pytest

from agent_foundry import Agent, ExecutionContext, Workflow
from agent_foundry.blackboard import Blackboard
from agent_foundry.llm_gateway import LLMGateway
from agent_foundry.orchestration import CritiqueConfig, DAGStep, SupervisorRoutingError

from conftest import ScriptedProvider


def test_workflow_supervisor_native_routes_to_named_specialist():
    def routed(messages, model):
        system = messages[0]["content"]
        return "ROUTE billing" if "ROUTE" in system else "Refund handled."

    provider = ScriptedProvider([routed, routed])
    llm = LLMGateway(provider=provider)
    billing = Agent("billing", "You are the billing agent.", llm=llm)

    workflow = Workflow.supervisor(prompt="route", agents={"billing": billing}, llm=llm, runtime="native")
    result = workflow.run("refund please", context=ExecutionContext(thread_id="sup-native"))

    assert result.content == "Refund handled."


def test_workflow_supervisor_native_retries_once_then_routes_on_a_corrected_reply():
    provider = ScriptedProvider(["ROUTE nobody", "ROUTE billing", "Handled after retry."])
    llm = LLMGateway(provider=provider)
    billing = Agent("billing", "You are the billing agent.", llm=llm)

    workflow = Workflow.supervisor(prompt="route", agents={"billing": billing}, llm=llm, runtime="native")
    result = workflow.run("refund please", context=ExecutionContext(thread_id="sup-native-retry"))

    assert result.content == "Handled after retry."


def test_workflow_supervisor_native_fails_closed_when_no_fallback_configured():
    provider = ScriptedProvider(["ROUTE nobody", "ROUTE still_nobody"])
    llm = LLMGateway(provider=provider)
    billing = Agent("billing", "You are the billing agent.", llm=llm)

    workflow = Workflow.supervisor(prompt="route", agents={"billing": billing}, llm=llm, runtime="native")

    with pytest.raises(SupervisorRoutingError, match="could not resolve"):
        workflow.run("refund please", context=ExecutionContext(thread_id="sup-native-fail-closed"))


def test_workflow_supervisor_native_uses_fallback_agent_after_failed_retry():
    provider = ScriptedProvider(["ROUTE nobody", "ROUTE still_nobody", "Handled via fallback."])
    llm = LLMGateway(provider=provider)
    billing = Agent("billing", "You are the billing agent.", llm=llm)

    workflow = Workflow.supervisor(
        prompt="route", agents={"billing": billing}, llm=llm, fallback_agent="billing", runtime="native",
    )
    result = workflow.run("refund please", context=ExecutionContext(thread_id="sup-native-fallback"))

    assert result.content == "Handled via fallback."


def test_workflow_swarm_native_handoff_between_peers():
    def triage_reply(messages, model):
        return "HANDOFF billing" if any(m["role"] == "user" for m in messages[-2:]) else "(should not speak again)"

    llm = LLMGateway(provider=ScriptedProvider([triage_reply, "Billing here."]))
    triage = Agent("triage", "triage", llm=llm)
    billing = Agent("billing", "billing", llm=llm)

    workflow = Workflow.swarm(agents={"triage": triage, "billing": billing}, entry="triage", runtime="native")
    result = workflow.run("I need a refund", context=ExecutionContext(thread_id="swarm-native"))

    assert result.content == "Billing here."


def test_workflow_blackboard_native_accumulates_contributions_across_rounds():
    researcher = Agent("researcher", "researcher", llm=LLMGateway(provider=ScriptedProvider(["POST fact: revenue grew 12%"] * 2)))
    skeptic = Agent("skeptic", "skeptic", llm=LLMGateway(provider=ScriptedProvider(["POST contradiction: may be one-time"] * 2)))
    bb = Blackboard()

    workflow = Workflow.blackboard(agents={"researcher": researcher, "skeptic": skeptic}, blackboard=bb, rounds=2, runtime="native")
    workflow.run("assess", context=ExecutionContext(thread_id="bb-native"))

    assert len(bb.facts) == 2 and len(bb.contradictions) == 2


def test_workflow_debate_native_judge_synthesizes_from_both_debaters():
    def judge_reply(messages, model):
        user_turn = messages[-1]["content"]
        assert "Buy" in user_turn and "Sell" in user_turn
        return "Verdict: Hold."

    optimist = Agent("optimist", "bullish", llm=LLMGateway(provider=ScriptedProvider(["Buy — strong fundamentals."])))
    pessimist = Agent("pessimist", "bearish", llm=LLMGateway(provider=ScriptedProvider(["Sell — overvalued."])))
    judge = Agent("judge", "judge", llm=LLMGateway(provider=ScriptedProvider([judge_reply])))

    workflow = Workflow.debate(debaters={"optimist": optimist, "pessimist": pessimist}, judge=judge, runtime="native")
    result = workflow.run("should we invest?", context=ExecutionContext(thread_id="debate-native"))

    assert result.content == "Verdict: Hold."


def test_workflow_fanout_native_dispatches_all_items_in_parallel():
    provider = ScriptedProvider([lambda messages, model: f"processed: {messages[-1]['content']}"] * 3)
    worker = Agent("classifier", "classify", llm=LLMGateway(provider=provider))

    workflow = Workflow.fanout(agent=worker, runtime="native")
    outputs = workflow.run(["a", "b", "c"], context=ExecutionContext(thread_id="fanout-native"))

    assert set(outputs) == {"processed: a", "processed: b", "processed: c"}


def test_workflow_fanout_native_rejects_critique():
    kpi_config = CritiqueConfig(kpi=None, context=lambda state, draft: {})  # type: ignore[arg-type]
    worker = Agent("classifier", "classify", llm=LLMGateway(provider=ScriptedProvider([])), critique=kpi_config)

    with pytest.raises(ValueError, match="critique"):
        Workflow.fanout(agent=worker, runtime="native")


def test_workflow_dag_native_diamond_dependency_merges_correctly():
    steps = [
        DAGStep(name="fetch", fn=lambda results: 10),
        DAGStep(name="double", fn=lambda results: results["fetch"] * 2, depends_on=("fetch",)),
        DAGStep(name="square", fn=lambda results: results["fetch"] ** 2, depends_on=("fetch",)),
        DAGStep(name="merge", fn=lambda results: results["double"] + results["square"], depends_on=("double", "square")),
    ]

    workflow = Workflow.dag(steps=steps, runtime="native")
    results = workflow.run()

    assert results["merge"] == 120


def test_workflow_dag_native_detects_unresolvable_dependency():
    steps = [DAGStep(name="a", fn=lambda results: 1, depends_on=("missing",))]
    workflow = Workflow.dag(steps=steps, runtime="native")

    with pytest.raises(RuntimeError, match="unsatisfiable"):
        workflow.run()


@pytest.mark.parametrize("factory_kwargs", [
    dict(name="supervisor", kwargs=dict(prompt="route", agents={}, llm=LLMGateway(provider=ScriptedProvider([])))),
    dict(name="dag", kwargs=dict(steps=[])),
])
def test_workflow_rejects_unknown_runtime(factory_kwargs):
    factory = getattr(Workflow, factory_kwargs["name"])
    with pytest.raises(ValueError, match="runtime"):
        factory(**factory_kwargs["kwargs"], runtime="temporal")


def test_workflow_dag_native_rejects_checkpointer():
    with pytest.raises(ValueError, match="checkpointer"):
        Workflow.dag(steps=[], runtime="native", checkpointer=object())

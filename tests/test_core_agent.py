"""Tests for agent_foundry.core.agent — Agent/Workflow as a LangGraph-free
facade over orchestration.py's build_*_graph functions. Every test exercises
the real graphs (no mocking of orchestration.py itself), same posture as
test_orchestration.py, just calling through Agent/Workflow instead of
build_*_graph/AgentConfig directly.
"""
from __future__ import annotations

import pytest

from agent_foundry import Agent, ExecutionContext, Workflow
from agent_foundry.blackboard import Blackboard
from agent_foundry.kpi import KPI
from agent_foundry.llm_gateway import LLMGateway
from agent_foundry.orchestration import CritiqueConfig, DAGStep

from conftest import ScriptedProvider


def lookup_order(order_id: str) -> str:
    """Look up an order by id."""
    return f"order {order_id} shipped"


def test_agent_run_round_trip_with_tool_use():
    provider = ScriptedProvider(['CALL lookup_order {"order_id": "A100"}', "All set!"])
    agent = Agent(
        "support", "You help with orders.",
        tools=[lookup_order], llm=LLMGateway(provider=provider),
    )

    result = agent.run("status of A100?", context=ExecutionContext(thread_id="t-core-1"))

    assert result.content == "All set!"
    assert result.thread_id == "t-core-1"
    assert result.awaiting_approval is False


def test_agent_run_generates_a_thread_id_when_no_context_given():
    provider = ScriptedProvider(["hello"])
    agent = Agent("greeter", "Say hi.", llm=LLMGateway(provider=provider))

    result = agent.run("hi")

    assert result.content == "hello"
    assert result.thread_id  # non-empty, auto-generated


def test_agent_resume_after_critique_escalation():
    kpi = KPI(name="confidence", score=lambda ctx: ctx["score"], direction="maximize", threshold=0.5)
    critique = CritiqueConfig(kpi=kpi, context=lambda state, draft: {"score": 0.02}, escalate_threshold=0.05)
    provider = ScriptedProvider(["a truly ambiguous answer"])
    agent = Agent("advisor", "Answer carefully.", llm=LLMGateway(provider=provider), critique=critique)
    context = ExecutionContext(thread_id="t-core-resume")

    paused = agent.run("what should I do?", context=context)
    assert paused.awaiting_approval is True

    resumed = agent.resume(approved=True, context=context)
    assert resumed.content == "a truly ambiguous answer"


def test_agent_rejects_a_memory_of_the_wrong_type_instead_of_silently_substituting_one():
    """Regression: Agent(memory=<wrong type>) used to silently create a
    fresh MemoryStore() instead of erroring — any real memory the caller
    thought they configured was quietly dropped with no explanation."""
    provider = ScriptedProvider(["hi"])
    with pytest.raises(TypeError, match="memory"):
        Agent("greeter", "Say hi.", llm=LLMGateway(provider=provider), memory={"not": "a memory store"})


def test_agent_resume_merges_decision_into_the_resume_payload():
    """resume() widened from approved-only to accept a richer `decision`
    dict (a clarification answer, an event's data, a payment confirmation
    id) for HITL/event resumes beyond plain yes/no. Proven directly against
    the Command payload LangGraph actually receives, since orchestration.py's
    own built-in interrupt() consumers only ever read .get("approved") off it
    — the extra keys are for a caller's own downstream use."""
    from agent_foundry.core.agent import _CompiledWorkflow
    from agent_foundry.core.execution_context import ExecutionContext as ExecCtx

    captured = {}

    class FakeGraph:
        def invoke(self, command, run_config):
            captured["resume"] = command.resume
            return {"messages": [{"role": "assistant", "content": "ok"}], "thread_id": run_config["configurable"]["thread_id"]}

    runner = _CompiledWorkflow(FakeGraph(), name="fake")
    runner.resume(approved=True, decision={"payment_id": "pay_123"}, context=ExecCtx(thread_id="t-decision"))

    assert captured["resume"] == {"approved": True, "payment_id": "pay_123"}


def test_native_engine_resume_accepts_a_decision_kwarg_for_signature_parity():
    """Same widened resume() signature on the native runtime — accepted for
    parity with the LangGraph engine (core.protocols.WorkflowEngine), even
    though NativeEngine's own fixed tool-approval logic only reads
    `approved` off it, exactly like orchestration.py's built-in nodes do."""
    provider = ScriptedProvider(['CALL issue_refund {"order_id": "A100"}', "done"])
    from agent_foundry.contracts import Policy

    def issue_refund(order_id: str) -> str:
        """Refund an order."""
        return f"refunded {order_id}"

    policy = Policy(allowed_tools=frozenset({"issue_refund"}), requires_approval=frozenset({"issue_refund"}))
    agent = Agent("refunds", "Handle refunds.", runtime="native", tools=[issue_refund], policy=policy, llm=LLMGateway(provider=provider))
    context = ExecutionContext(thread_id="t-native-decision")

    paused = agent.run("refund A100", context=context)
    assert paused.awaiting_approval is True

    resumed = agent.resume(approved=True, decision={"note": "approved by manager"}, context=context)
    assert resumed.content == "done"


def test_agent_batch():
    provider = ScriptedProvider(["reply one", "reply two"])
    agent = Agent("batcher", "Reply briefly.", llm=LLMGateway(provider=provider))

    report = agent.batch([{"message": "a"}, {"message": "b"}])

    assert report.success_rate == 1.0
    assert {r.output for r in report.results} == {"reply one", "reply two"}


def test_agent_as_tool():
    provider = ScriptedProvider(["Refund processed."])
    agent = Agent("refunds", "Handle refunds.", llm=LLMGateway(provider=provider))

    tool = agent.as_tool(description="Handles refund requests")
    result = tool.fn(query="refund my order")

    assert result == "Refund processed."


def test_agent_rejects_multi_agent_workflow_names():
    with pytest.raises(ValueError):
        Agent("bad", "x", workflow="supervisor")


def test_workflow_supervisor_routes_to_the_right_specialist():
    def routed(messages, model):
        system = next(m["content"] for m in messages if m["role"] == "system")
        return "ROUTE billing" if "ROUTE" in system else "Refund handled."

    provider = ScriptedProvider([routed, routed])
    llm = LLMGateway(provider=provider)
    billing = Agent("billing", "You are the billing agent.", llm=llm)

    workflow = Workflow.supervisor(prompt="route", agents={"billing": billing}, llm=llm)
    result = workflow.run("refund please", context=ExecutionContext(thread_id="sup-core"))

    assert result.content == "Refund handled."


def test_workflow_swarm_handoff_between_peers():
    def triage_reply(messages, model):
        return "HANDOFF billing" if any(m["role"] == "user" for m in messages[-2:]) else "(should not speak again)"

    llm = LLMGateway(provider=ScriptedProvider([triage_reply, "Billing here."]))
    triage = Agent("triage", "triage", llm=llm)
    billing = Agent("billing", "billing", llm=llm)

    workflow = Workflow.swarm(agents={"triage": triage, "billing": billing}, entry="triage")
    result = workflow.run("I need a refund", context=ExecutionContext(thread_id="swarm-core"))

    assert result.content == "Billing here."


def test_workflow_blackboard_accumulates_contributions_across_rounds():
    researcher = Agent("researcher", "researcher", llm=LLMGateway(provider=ScriptedProvider(["POST fact: revenue grew 12%"] * 2)))
    skeptic = Agent("skeptic", "skeptic", llm=LLMGateway(provider=ScriptedProvider(["POST contradiction: may be one-time"] * 2)))
    bb = Blackboard()

    workflow = Workflow.blackboard(agents={"researcher": researcher, "skeptic": skeptic}, blackboard=bb, rounds=2)
    workflow.run("assess", context=ExecutionContext(thread_id="bb-core"))

    assert len(bb.facts) == 2 and len(bb.contradictions) == 2


def test_workflow_debate_judge_synthesizes_from_both_debaters():
    def judge_reply(messages, model):
        # judge_node routes through build_agent_graph (see
        # orchestration._run_governed_turn) — the candidate transcript
        # arrives as the user turn, not hand-embedded in the system message.
        user_turn = messages[-1]["content"]
        assert "Buy" in user_turn and "Sell" in user_turn
        return "Verdict: Hold."

    optimist = Agent("optimist", "bullish", llm=LLMGateway(provider=ScriptedProvider(["Buy — strong fundamentals."])))
    pessimist = Agent("pessimist", "bearish", llm=LLMGateway(provider=ScriptedProvider(["Sell — overvalued."])))
    judge = Agent("judge", "judge", llm=LLMGateway(provider=ScriptedProvider([judge_reply])))

    workflow = Workflow.debate(debaters={"optimist": optimist, "pessimist": pessimist}, judge=judge)
    result = workflow.run("should we invest?", context=ExecutionContext(thread_id="debate-core"))

    assert result.content == "Verdict: Hold."


def test_workflow_fanout_dispatches_all_items_in_parallel():
    provider = ScriptedProvider([lambda messages, model: f"processed: {messages[-1]['content']}"] * 3)
    worker = Agent("classifier", "classify", llm=LLMGateway(provider=provider))

    workflow = Workflow.fanout(agent=worker)
    outputs = workflow.run(["a", "b", "c"], context=ExecutionContext(thread_id="fanout-core"))

    assert set(outputs) == {"processed: a", "processed: b", "processed: c"}


def test_workflow_dag_diamond_dependency_merges_correctly():
    steps = [
        DAGStep(name="fetch", fn=lambda results: 10),
        DAGStep(name="double", fn=lambda results: results["fetch"] * 2, depends_on=("fetch",)),
        DAGStep(name="square", fn=lambda results: results["fetch"] ** 2, depends_on=("fetch",)),
        DAGStep(name="merge", fn=lambda results: results["double"] + results["square"], depends_on=("double", "square")),
    ]

    workflow = Workflow.dag(steps=steps)
    results = workflow.run()

    assert results["merge"] == 120

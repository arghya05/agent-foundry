"""Topology-level human-in-the-loop: a specialist's tool call requiring
approval must pause the WHOLE topology turn (Workflow.*(runtime="native")),
and Workflow.resume(...) must continue exactly that paused specialist, not
re-route from scratch. Mirrors test_native_engine.py's own
test_native_engine_tool_approval_pause_and_resume, one layer up.

Before this, native_run_governed_turn drove an ephemeral, immediately-
discarded NativeEngine per specialist call — any interrupt was silently
lost the instant the call returned. core.native_orchestration._PausableTurns
is the fix: it keeps a specialist's engine alive across the call boundary
when its turn pauses.
"""
from __future__ import annotations

from agent_foundry import Agent, ExecutionContext, Workflow
from agent_foundry.blackboard import Blackboard
from agent_foundry.contracts import Policy, ToolSpec
from agent_foundry.llm_gateway import LLMGateway

from conftest import ScriptedProvider


def send_wire(amount_usd: float) -> str:
    return f"sent ${amount_usd}"


def _wire_tool_and_policy():
    tool = ToolSpec("send_wire", "send a wire", {"amount_usd": "number"}, send_wire, requires_confirmation=True)
    policy = Policy(allowed_tools=frozenset({"send_wire"}), requires_approval=frozenset({"send_wire"}))
    return tool, policy


def test_workflow_supervisor_native_pauses_for_a_specialists_tool_approval_and_resumes():
    tool, policy = _wire_tool_and_policy()

    def routed(messages, model):
        system = messages[0]["content"]
        return "ROUTE billing" if "ROUTE" in system else '{"unused": true}'  # never reached — billing responds via tool_calls below

    from agent_foundry.contracts import LLMResponse, ToolCall

    provider = ScriptedProvider([
        routed,
        LLMResponse(text="", model="m", input_tokens=1, output_tokens=1, cost_usd=0.0,
                    tool_calls=[ToolCall(id="c1", name="send_wire", args={"amount_usd": 5})]),
        "wire sent, all done",
    ])
    llm = LLMGateway(provider=provider)
    billing = Agent("billing", "You are the billing agent.", llm=llm, tools=[tool], policy=policy)

    workflow = Workflow.supervisor(prompt="route", agents={"billing": billing}, llm=llm, runtime="native")
    context = ExecutionContext(thread_id="sup-hitl")

    paused = workflow.run("please pay the invoice", context=context)
    assert paused.awaiting_approval is True
    pending = paused.raw["__interrupt__"][0].value
    assert pending["tool"] == "send_wire"

    resumed = workflow.resume(approved=True, context=context)
    assert resumed.content == "wire sent, all done"


def test_workflow_swarm_native_pauses_inside_the_handed_off_specialist_and_resumes():
    tool, policy = _wire_tool_and_policy()

    from agent_foundry.contracts import LLMResponse, ToolCall

    def triage_reply(messages, model):
        return "HANDOFF billing" if any(m["role"] == "user" for m in messages[-2:]) else "(should not speak again)"

    provider = ScriptedProvider([
        triage_reply,
        LLMResponse(text="", model="m", input_tokens=1, output_tokens=1, cost_usd=0.0,
                    tool_calls=[ToolCall(id="c1", name="send_wire", args={"amount_usd": 5})]),
        "wire sent, all done",
    ])
    llm = LLMGateway(provider=provider)
    triage = Agent("triage", "triage", llm=llm)
    billing = Agent("billing", "billing", llm=llm, tools=[tool], policy=policy)

    workflow = Workflow.swarm(agents={"triage": triage, "billing": billing}, entry="triage", runtime="native")
    context = ExecutionContext(thread_id="swarm-hitl")

    paused = workflow.run("please pay the invoice", context=context)
    assert paused.awaiting_approval is True
    pending = paused.raw["__interrupt__"][0].value
    assert pending["tool"] == "send_wire"

    resumed = workflow.resume(approved=True, context=context)
    assert resumed.content == "wire sent, all done"


def test_workflow_blackboard_native_pauses_mid_round_on_a_participants_tool_approval_and_resumes():
    tool, policy = _wire_tool_and_policy()
    from agent_foundry.contracts import LLMResponse, ToolCall

    researcher = Agent("researcher", "researcher", llm=LLMGateway(provider=ScriptedProvider(["POST fact: revenue grew 12%"])))
    skeptic_provider = ScriptedProvider([
        LLMResponse(text="", model="m", input_tokens=1, output_tokens=1, cost_usd=0.0,
                    tool_calls=[ToolCall(id="c1", name="send_wire", args={"amount_usd": 5})]),
        "POST contradiction: verified via wire audit",
    ])
    skeptic = Agent("skeptic", "skeptic", llm=LLMGateway(provider=skeptic_provider), tools=[tool], policy=policy)
    bb = Blackboard()

    workflow = Workflow.blackboard(agents={"researcher": researcher, "skeptic": skeptic}, blackboard=bb, rounds=1, runtime="native")
    context = ExecutionContext(thread_id="bb-hitl")

    paused = workflow.run("assess", context=context)
    assert paused.awaiting_approval is True
    assert len(bb.facts) == 1  # researcher's post landed before skeptic paused

    resumed = workflow.resume(approved=True, context=context)
    assert not resumed.awaiting_approval
    assert len(bb.contradictions) == 1


def test_workflow_debate_native_pauses_on_a_debaters_tool_approval_and_resumes_through_the_judge():
    tool, policy = _wire_tool_and_policy()
    from agent_foundry.contracts import LLMResponse, ToolCall

    optimist = Agent("optimist", "bullish", llm=LLMGateway(provider=ScriptedProvider(["Buy — strong fundamentals."])))
    pessimist_provider = ScriptedProvider([
        LLMResponse(text="", model="m", input_tokens=1, output_tokens=1, cost_usd=0.0,
                    tool_calls=[ToolCall(id="c1", name="send_wire", args={"amount_usd": 5})]),
        "Sell — confirmed via wire audit.",
    ])
    pessimist = Agent("pessimist", "bearish", llm=LLMGateway(provider=pessimist_provider), tools=[tool], policy=policy)
    judge = Agent("judge", "judge", llm=LLMGateway(provider=ScriptedProvider(["Verdict: Hold."])))

    workflow = Workflow.debate(debaters={"optimist": optimist, "pessimist": pessimist}, judge=judge, runtime="native")
    context = ExecutionContext(thread_id="debate-hitl")

    paused = workflow.run("should we invest?", context=context)
    assert paused.awaiting_approval is True

    resumed = workflow.resume(approved=True, context=context)
    assert resumed.content == "Verdict: Hold."

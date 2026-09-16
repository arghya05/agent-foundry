"""Native multi-agent topology state, surviving a "restart" — a SECOND,
independently-constructed Workflow.*(runtime="native", state_store=...)
sharing the same StateStore as the first, simulating a process restart or a
resume() landing on a different replica than the one that paused.

Before core.native_orchestration._TopologyState/_PausableTurns' state_store
wiring, ALL of this (which specialist a pending approval belongs to, the
accumulated outer conversation, a swarm's hop count, a blackboard's
round/agent index, a debate's phase/index) lived in plain in-process dicts
with no state_store parameter at all — a second Workflow object, even
pointed at the exact same MemoryStateStore instance, had zero way to see a
first object's paused turn; workflow2.resume(...) always hit "nothing
pending to resume" (there was no `state_store=` kwarg to even attempt it
with). Mirrors test_native_orchestration_hitl.py's own scenarios exactly,
just resuming through a fresh object instead of the one that paused.
"""
from __future__ import annotations

from agent_foundry import Agent, ExecutionContext, Workflow
from agent_foundry.blackboard import Blackboard
from agent_foundry.contracts import LLMResponse, Policy, ToolCall, ToolSpec
from agent_foundry.core.state_store import MemoryStateStore
from agent_foundry.llm_gateway import LLMGateway

from conftest import ScriptedProvider


def send_wire(amount_usd: float) -> str:
    return f"sent ${amount_usd}"


def _wire_tool_and_policy():
    tool = ToolSpec("send_wire", "send a wire", {"amount_usd": "number"}, send_wire, requires_confirmation=True)
    policy = Policy(allowed_tools=frozenset({"send_wire"}), requires_approval=frozenset({"send_wire"}))
    return tool, policy


def test_workflow_supervisor_native_resumes_a_paused_turn_through_a_second_independent_instance():
    tool, policy = _wire_tool_and_policy()

    def routed(messages, model):
        system = messages[0]["content"]
        return "ROUTE billing" if "ROUTE" in system else '{"unused": true}'

    provider = ScriptedProvider([
        routed,
        LLMResponse(text="", model="m", input_tokens=1, output_tokens=1, cost_usd=0.0,
                    tool_calls=[ToolCall(id="c1", name="send_wire", args={"amount_usd": 5})]),
        "wire sent, all done",
    ])
    llm = LLMGateway(provider=provider)
    billing = Agent("billing", "You are the billing agent.", llm=llm, tools=[tool], policy=policy)
    store = MemoryStateStore()

    workflow_a = Workflow.supervisor(prompt="route", agents={"billing": billing}, llm=llm, runtime="native", state_store=store)
    context = ExecutionContext(thread_id="sup-durability")
    paused = workflow_a.run("please pay the invoice", context=context)
    assert paused.awaiting_approval is True

    # A brand-new graph object, zero in-process memory of workflow_a's pause —
    # only the shared `store` connects them.
    workflow_b = Workflow.supervisor(prompt="route", agents={"billing": billing}, llm=llm, runtime="native", state_store=store)
    resumed = workflow_b.resume(approved=True, context=context)
    assert resumed.content == "wire sent, all done"


def test_workflow_swarm_native_resumes_a_handed_off_specialists_pause_through_a_second_instance():
    tool, policy = _wire_tool_and_policy()

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
    store = MemoryStateStore()
    agents = {"triage": triage, "billing": billing}

    workflow_a = Workflow.swarm(agents=agents, entry="triage", runtime="native", state_store=store)
    context = ExecutionContext(thread_id="swarm-durability")
    paused = workflow_a.run("please pay the invoice", context=context)
    assert paused.awaiting_approval is True

    workflow_b = Workflow.swarm(agents=agents, entry="triage", runtime="native", state_store=store)
    resumed = workflow_b.resume(approved=True, context=context)
    assert resumed.content == "wire sent, all done"


def test_workflow_blackboard_native_resumes_a_paused_round_through_a_second_instance():
    tool, policy = _wire_tool_and_policy()

    researcher = Agent("researcher", "researcher", llm=LLMGateway(provider=ScriptedProvider(["POST fact: revenue grew 12%"])))
    skeptic_provider = ScriptedProvider([
        LLMResponse(text="", model="m", input_tokens=1, output_tokens=1, cost_usd=0.0,
                    tool_calls=[ToolCall(id="c1", name="send_wire", args={"amount_usd": 5})]),
        "POST contradiction: verified via wire audit",
    ])
    skeptic = Agent("skeptic", "skeptic", llm=LLMGateway(provider=skeptic_provider), tools=[tool], policy=policy)
    bb = Blackboard()
    store = MemoryStateStore()
    agents = {"researcher": researcher, "skeptic": skeptic}

    workflow_a = Workflow.blackboard(agents=agents, blackboard=bb, rounds=1, runtime="native", state_store=store)
    context = ExecutionContext(thread_id="bb-durability")
    paused = workflow_a.run("assess", context=context)
    assert paused.awaiting_approval is True
    assert len(bb.facts) == 1

    # Same Blackboard object passed again — see Workflow.blackboard's own
    # docstring: the Blackboard's CONTENTS are a separate, still-open gap
    # this fix does not close, so the test wires the same one deliberately
    # rather than claiming that part survives a real restart too.
    workflow_b = Workflow.blackboard(agents=agents, blackboard=bb, rounds=1, runtime="native", state_store=store)
    resumed = workflow_b.resume(approved=True, context=context)
    assert not resumed.awaiting_approval
    assert len(bb.contradictions) == 1


def test_workflow_debate_native_resumes_a_paused_debater_through_a_second_instance():
    tool, policy = _wire_tool_and_policy()

    optimist = Agent("optimist", "bullish", llm=LLMGateway(provider=ScriptedProvider(["Buy — strong fundamentals."])))
    pessimist_provider = ScriptedProvider([
        LLMResponse(text="", model="m", input_tokens=1, output_tokens=1, cost_usd=0.0,
                    tool_calls=[ToolCall(id="c1", name="send_wire", args={"amount_usd": 5})]),
        "Sell — confirmed via wire audit.",
    ])
    pessimist = Agent("pessimist", "bearish", llm=LLMGateway(provider=pessimist_provider), tools=[tool], policy=policy)
    judge = Agent("judge", "judge", llm=LLMGateway(provider=ScriptedProvider(["Verdict: Hold."])))
    store = MemoryStateStore()
    debaters = {"optimist": optimist, "pessimist": pessimist}

    workflow_a = Workflow.debate(debaters=debaters, judge=judge, runtime="native", state_store=store)
    context = ExecutionContext(thread_id="debate-durability")
    paused = workflow_a.run("should we invest?", context=context)
    assert paused.awaiting_approval is True

    workflow_b = Workflow.debate(debaters=debaters, judge=judge, runtime="native", state_store=store)
    resumed = workflow_b.resume(approved=True, context=context)
    assert resumed.content == "Verdict: Hold."


def test_workflow_supervisor_native_second_instance_continues_the_conversation_after_a_clean_turn():
    """Durability for a COMPLETED (non-paused) turn too, not just a pause:
    a second graph object must see the full routing history a first object
    already finished, so a follow-up message on the same thread_id routes
    with real context instead of starting the conversation over blind."""
    seen_histories = []

    def routed(messages, model):
        seen_histories.append(len(messages))
        return "ROUTE billing"

    provider = ScriptedProvider([routed, "here's your balance", routed, "thanks, noted"])
    llm = LLMGateway(provider=provider)
    billing = Agent("billing", "billing agent", llm=llm)
    store = MemoryStateStore()

    workflow_a = Workflow.supervisor(prompt="route", agents={"billing": billing}, llm=llm, runtime="native", state_store=store)
    context = ExecutionContext(thread_id="sup-continuity")
    first = workflow_a.run("what's my balance?", context=context)
    assert first.content == "here's your balance"

    # Not a resume() — a brand-new message on the SAME thread, via the fresh instance.
    workflow_b = Workflow.supervisor(prompt="route", agents={"billing": billing}, llm=llm, runtime="native", state_store=store)
    result = workflow_b.run("ok thanks", context=context)
    assert result.content == "thanks, noted"
    # The router call for the SECOND message must have seen the first
    # turn's history too (>1 message), proving workflow_b hydrated the
    # accumulated outer history from `store` rather than starting fresh.
    assert seen_histories[-1] > 1

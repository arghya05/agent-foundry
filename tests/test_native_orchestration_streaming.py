"""Real, discrete event streaming for native multi-agent topologies —
Workflow.*(runtime="native").stream() used to be `yield self.invoke(...)`
(one final chunk); now each yields a real sequence of named `{"event": ...}`
progress dicts, with the same {"messages"/"results": ...} state dict
.invoke() itself returns as the LAST item. See
core/native_orchestration.py's own module docstring for why this is a
deliberate divergence from single-agent streaming's full-state-per-step
shape, not an attempt to match it.
"""
from __future__ import annotations

from agent_foundry import Agent, ExecutionContext, Workflow
from agent_foundry.blackboard import Blackboard
from agent_foundry.contracts import LLMResponse, Policy, ToolCall, ToolSpec
from agent_foundry.llm_gateway import LLMGateway
from agent_foundry.orchestration import DAGStep

from conftest import ScriptedProvider


def test_supervisor_native_stream_emits_router_and_specialist_events_then_final_state():
    provider = ScriptedProvider([
        lambda messages, model: "ROUTE billing" if "ROUTE" in messages[0]["content"] else "Refund handled.",
    ] * 2)
    llm = LLMGateway(provider=provider)
    billing = Agent("billing", "billing", llm=llm)

    workflow = Workflow.supervisor(prompt="route", agents={"billing": billing}, llm=llm, runtime="native")
    chunks = list(workflow.graph.stream({"messages": [{"role": "user", "content": "refund please"}], "thread_id": "sup-stream"},
                                        {"configurable": {"thread_id": "sup-stream"}}))

    events = [c["event"] for c in chunks if "event" in c]
    assert events == ["router.selected", "specialist.completed"]
    assert chunks[-1]["messages"][-1]["content"] == "Refund handled."


def test_swarm_native_stream_emits_agent_and_handoff_events():
    def triage_reply(messages, model):
        return "HANDOFF billing" if any(m["role"] == "user" for m in messages[-2:]) else "(quiet)"

    llm = LLMGateway(provider=ScriptedProvider([triage_reply, "Billing here."]))
    triage = Agent("triage", "triage", llm=llm)
    billing = Agent("billing", "billing", llm=llm)

    workflow = Workflow.swarm(agents={"triage": triage, "billing": billing}, entry="triage", runtime="native")
    chunks = list(workflow.graph.stream({"messages": [{"role": "user", "content": "help"}], "thread_id": "swarm-stream"},
                                        {"configurable": {"thread_id": "swarm-stream"}}))

    events = [(c["event"], c.get("agent") or (c.get("from"), c.get("to"))) for c in chunks if "event" in c]
    assert events == [("agent.completed", "triage"), ("handoff", ("triage", "billing")), ("agent.completed", "billing")]
    assert chunks[-1]["messages"][-1]["content"] == "Billing here."


def test_blackboard_native_stream_emits_a_posted_event_per_agent_per_round():
    researcher = Agent("researcher", "researcher", llm=LLMGateway(provider=ScriptedProvider(["POST fact: x"] * 2)))
    skeptic = Agent("skeptic", "skeptic", llm=LLMGateway(provider=ScriptedProvider(["POST contradiction: y"] * 2)))
    bb = Blackboard()

    workflow = Workflow.blackboard(agents={"researcher": researcher, "skeptic": skeptic}, blackboard=bb, rounds=2, runtime="native")
    chunks = list(workflow.graph.stream({"messages": [{"role": "user", "content": "assess"}], "thread_id": "bb-stream", "round": 0},
                                        {"configurable": {"thread_id": "bb-stream"}}))

    events = [c["event"] for c in chunks if "event" in c]
    assert events == ["agent.posted"] * 4  # 2 agents x 2 rounds
    assert chunks[-1]["round"] == 2


def test_debate_native_stream_emits_debater_then_judge_events():
    optimist = Agent("optimist", "bullish", llm=LLMGateway(provider=ScriptedProvider(["Buy."])))
    pessimist = Agent("pessimist", "bearish", llm=LLMGateway(provider=ScriptedProvider(["Sell."])))
    judge = Agent("judge", "judge", llm=LLMGateway(provider=ScriptedProvider(["Verdict: Hold."])))

    workflow = Workflow.debate(debaters={"optimist": optimist, "pessimist": pessimist}, judge=judge, runtime="native")
    chunks = list(workflow.graph.stream({"messages": [{"role": "user", "content": "invest?"}], "thread_id": "debate-stream"},
                                        {"configurable": {"thread_id": "debate-stream"}}))

    events = [c["event"] for c in chunks if "event" in c]
    assert events == ["debater.answered", "debater.answered", "judge.decided"]
    assert chunks[-1]["messages"][-1]["content"] == "Verdict: Hold."


def test_fanout_native_stream_emits_one_item_completed_event_per_item():
    provider = ScriptedProvider([lambda messages, model: f"processed: {messages[-1]['content']}"] * 3)
    worker = Agent("classifier", "classify", llm=LLMGateway(provider=provider))

    workflow = Workflow.fanout(agent=worker, runtime="native")
    chunks = list(workflow.graph.stream({"items": ["a", "b", "c"], "thread_id": "fanout-stream"}, {"configurable": {"thread_id": "fanout-stream"}}))

    events = [c["event"] for c in chunks if "event" in c]
    assert events == ["item.completed"] * 3
    assert {m["content"] for m in chunks[-1]["messages"]} == {"processed: a", "processed: b", "processed: c"}


def test_dag_native_stream_emits_one_step_completed_event_per_step():
    steps = [
        DAGStep(name="fetch", fn=lambda r: 10),
        DAGStep(name="double", fn=lambda r: r["fetch"] * 2, depends_on=("fetch",)),
    ]
    workflow = Workflow.dag(steps=steps, runtime="native")
    chunks = list(workflow.graph.stream({"results": {}}, {"configurable": {"thread_id": "dag-stream"}}))

    events = [c["event"] for c in chunks if "event" in c]
    assert events == ["step.completed", "step.completed"]
    assert chunks[-1]["results"] == {"fetch": 10, "double": 20}


def test_supervisor_native_stream_pauses_with_an_interrupt_chunk_and_no_specialist_completed_event():
    tool = ToolSpec("send_wire", "send a wire", {"amount_usd": "number"}, lambda amount_usd: f"sent ${amount_usd}", requires_confirmation=True)
    policy = Policy(allowed_tools=frozenset({"send_wire"}), requires_approval=frozenset({"send_wire"}))

    provider = ScriptedProvider([
        "ROUTE billing",
        LLMResponse(text="", model="m", input_tokens=1, output_tokens=1, cost_usd=0.0,
                    tool_calls=[ToolCall(id="c1", name="send_wire", args={"amount_usd": 5})]),
    ])
    llm = LLMGateway(provider=provider)
    billing = Agent("billing", "billing", llm=llm, tools=[tool], policy=policy)

    workflow = Workflow.supervisor(prompt="route", agents={"billing": billing}, llm=llm, runtime="native")
    chunks = list(workflow.graph.stream({"messages": [{"role": "user", "content": "pay"}], "thread_id": "sup-stream-pause"},
                                        {"configurable": {"thread_id": "sup-stream-pause"}}))

    events = [c["event"] for c in chunks if "event" in c]
    assert events == ["router.selected"]  # paused before "specialist.completed" ever fires
    assert "__interrupt__" in chunks[-1]

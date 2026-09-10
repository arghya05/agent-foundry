"""examples/autonomous_workflow — a single governed Agent driven through
core.run.Run's formal lifecycle, self-verifying its own answers via
CritiqueConfig and reacting to events via Agent.on(). Verified against a
ScriptedProvider, same convention as the rest of this suite.

run_eval (core.evalgate) needs a plain Agent, not a Workflow.dag/.supervisor
topology — see agent.py's own module docstring for why this reference app
stays a single Agent rather than a multi-agent topology. The eval_dataset.json
cases cover the plain task-success path; escalation and the Run/event
lifecycle are Python-only behaviors (same reason research_agent's KPI cases
live here instead of in JSON) and are exercised directly below."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_foundry import ExecutionContext, run_eval
from agent_foundry.contracts import LLMResponse, ToolCall
from agent_foundry.core.run import RunStatus
from agent_foundry.eval_dataset import EvalDataset
from agent_foundry.llm_gateway import LLMGateway

from conftest import ScriptedProvider
from examples.autonomous_workflow.agent import build_agent

_DATASET_PATH = Path(__file__).resolve().parent.parent / "examples" / "autonomous_workflow" / "eval_dataset.json"


def _call(order_id: str, call_id: str = "c1"):
    def fn(messages, model):
        return LLMResponse(text="", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0,
                            tool_calls=[ToolCall(id=call_id, name="check_order_status", args={"order_id": order_id})])
    return fn


def test_eval_dataset_file_is_versioned_and_loads():
    dataset = EvalDataset.load(_DATASET_PATH)
    assert dataset.name == "autonomous_workflow"
    assert len(dataset.to_cases()) == 2


def test_autonomous_workflow_passes_its_own_eval_dataset():
    dataset = EvalDataset.load(_DATASET_PATH)
    responses = [
        _call("O-500"), "Order O-500 is in transit, last scan Denver CO.",
        _call("O-501"), "Order O-501 is delayed at customs.",
    ]
    provider = ScriptedProvider(responses)
    agent = build_agent(llm=LLMGateway(provider=provider))

    scorecard = run_eval(agent, dataset.to_cases(), dataset_name=dataset.name)

    ok, reasons = scorecard.passes({"task_success_rate_min": 1.0, "tool_accuracy_rate_min": 1.0})
    assert ok, reasons


def test_critique_escalates_on_a_severely_ungrounded_answer():
    """The point of the critique gate: an answer sharing almost no words
    with what check_order_status actually returned must pause for a human,
    not get auto-replied as if it were trustworthy."""
    responses = [_call("O-500"), "Everything is completely fine, no issues whatsoever, nothing to report."]
    agent = build_agent(llm=LLMGateway(provider=ScriptedProvider(responses)))

    result = agent.run("What's the status of order O-500?", context=ExecutionContext(thread_id="autonomous-escalate"))

    assert result.awaiting_approval
    pending = result.raw["__interrupt__"][0].value
    assert pending["reason"] == "low_confidence"


def test_agent_start_returns_a_run_with_formal_lifecycle():
    responses = [_call("O-500"), "Order O-500 is in transit, last scan Denver CO."]
    agent = build_agent(llm=LLMGateway(provider=ScriptedProvider(responses)))

    run = agent.start("What's the status of order O-500?")

    assert run.status == RunStatus.COMPLETED
    assert "in transit" in run.result.content
    assert run.run_id


def test_event_wiring_triggers_a_turn_and_the_decorated_handler():
    """@agent.on(topic) wires TWO things to the same publish: the graph
    itself (via events.wire_event_driven — the event becomes the triggered
    turn's message) and the decorated function as a plain subscriber. Both
    must fire on one publish()."""
    responses = [_call("O-501"), "Order O-501 is delayed at customs."]
    agent = build_agent(llm=LLMGateway(provider=ScriptedProvider(responses)))
    seen = []

    @agent.on("order.delayed")
    def on_delay(event):
        seen.append(event)

    agent.event_bus.publish("order.delayed", {"order_id": "O-501"})

    assert seen == [{"order_id": "O-501"}]
    state = agent.graph.get_state({"configurable": {"thread_id": "event-order.delayed"}})
    assert "delayed at customs" in state.values["messages"][-1]["content"]


def test_agent_spec_yaml_runs_the_same_tool_without_the_critique_gate():
    """agent.yaml deliberately has no critique gate (AgentSpec has no
    critique: field) — confirms it still runs the tool and answers, just
    without self-verification."""
    from agent_foundry.agent_spec import AgentSpec, build_agent as build_agent_from_spec

    spec = AgentSpec.from_yaml(Path(__file__).resolve().parent.parent / "examples" / "autonomous_workflow" / "agent.yaml")
    responses = [_call("O-500"), "Order O-500 is in transit."]
    agent = build_agent_from_spec(spec, llm=LLMGateway(provider=ScriptedProvider(responses)))

    result = agent.run("What's the status of order O-500?", context=ExecutionContext(thread_id="autonomous-spec"))

    assert result.content == "Order O-500 is in transit."
    assert not result.awaiting_approval

"""Reference app: a long-running autonomous order monitor — a single
governed Agent driven through core.run.Run's formal lifecycle
(STARTED/RUNNING/WAITING_HUMAN/WAITING_EVENT/...) instead of Agent.run()'s
plain RunResult, wired to react to events (Agent.on()/Run.wait_for_event())
and self-verify its own answers (CritiqueConfig), escalating to a human only
when its answer doesn't hold up against the evidence it gathered — not on
every action, unlike examples/commerce_agent's destructive-tool-approval
pattern. Different HITL shape, same underlying interrupt() mechanism.

kept eval()-compatible (core.evalgate.run_eval needs a plain Agent, not a
Workflow.dag/.supervisor topology — a genuine constraint, not a simplification
for its own sake) — the event/Run-lifecycle behavior is demonstrated
directly in tests/test_examples_autonomous_workflow.py instead of through
eval_dataset.json, the same split examples/research_agent uses for its
KPI-based cases that JSON can't carry.

Two ways to run this file:
    export ANTHROPIC_API_KEY=...
    python examples/autonomous_workflow/agent.py
    foundry eval examples/autonomous_workflow/agent.py \\
        examples/autonomous_workflow/eval_dataset.json

Also declarative — examples/autonomous_workflow/agent.yaml builds the same
agent via agent_foundry.AgentSpec, MINUS the critique gate (AgentSpec has no
`critique:` field — see agent_spec.py; a self-verification/escalation gate
is a Python-level construction detail, not something meant to be
declaratively swappable per deployment)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent_foundry import Agent
from agent_foundry.contracts import ToolSpec
from agent_foundry.kpi import reference_check_kpi
from agent_foundry.llm_gateway import LLMGateway
from agent_foundry.orchestration import CritiqueConfig

_ORDERS = {
    "O-500": "O-500: in transit, last scan Denver CO, eta 2026-09-12",
    "O-501": "O-501: delayed at customs, no eta available",
}


def check_order_status(order_id: str) -> str:
    return _ORDERS.get(order_id, f"no record of order {order_id}")


def _critique_context(state, draft: str) -> dict:
    """The tool result(s) this turn actually gathered — what the critique
    KPI grounds the draft answer against. Mirrors examples/research_agent's
    citation-correctness idea, applied to a single tool's own evidence
    instead of a document corpus."""
    tool_outputs = [m["content"] for m in state["messages"] if m.get("role") == "tool"]
    return {"output_text": draft, "references": tool_outputs}


def build_agent(*, llm: LLMGateway | None = None) -> Agent:
    tool = ToolSpec("check_order_status", "Look up an order's current status.", {"order_id": "string"}, check_order_status)
    critique = CritiqueConfig(
        kpi=reference_check_kpi("grounded_in_order_status", references=lambda ctx: ctx["references"], threshold=0.3),
        context=_critique_context,
        escalate_threshold=0.1,  # only the genuinely ungrounded case pauses for a human — most answers just flag, per CritiqueConfig's own docstring
    )

    return Agent(
        "order_monitor",
        "You monitor order status. Call check_order_status, then report the status plainly — "
        "don't invent details the tool didn't return.",
        tools=[tool],
        critique=critique,
        llm=llm,
    )


agent = build_agent()


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("set ANTHROPIC_API_KEY to run this example for real")

    run = agent.start("What's the status of order O-500?")
    print("status:", run.status.value)
    print("reply:", run.result.content if run.result else None)

    @agent.on("order.delayed")
    def on_delay(event):
        print("event handler saw:", event)

    agent.event_bus.publish("order.delayed", {"order_id": "O-501"})

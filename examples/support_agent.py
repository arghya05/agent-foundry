"""A support agent built entirely from Agent Foundry components.

This is one agent built on the generic skeleton in agent_foundry/ — swap the
system prompt, tools and policy below to get a sales, ops, or any other
agent on the exact same core.

Run:
    export ANTHROPIC_API_KEY=...
    python examples/support_agent.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent_foundry.contracts import Identity, Policy, ToolSpec
from agent_foundry.context import MemoryStore, profile_write_tool
from agent_foundry.eval import EvalHarness
from agent_foundry.experiments import Experiment, ExperimentTracker
from agent_foundry.guardrails import GuardrailEngine
from agent_foundry.llm_gateway import AnthropicProvider, LLMGateway
from agent_foundry.observability import CostLedger, Metrics, Tracer, check_alerts, render_dashboard
from agent_foundry.orchestration import build_agent_graph
from agent_foundry.prompts import PromptLibrary
from agent_foundry.runtime import CircuitBreaker, RunBudget
from agent_foundry.security import AuditLog, ToolManifestRegistry
from agent_foundry.tools_gateway import ToolRegistry
from agent_foundry.ui.console import handle_interrupt

_ORDERS = {"A100": {"status": "shipped", "eta": "2026-09-05"}}


def lookup_order(order_id: str) -> str:
    order = _ORDERS.get(order_id)
    return f"{order_id}: {order}" if order else f"no such order {order_id}"


def issue_refund(order_id: str, amount_usd: float) -> str:
    return f"refunded ${amount_usd:.2f} for order {order_id}"


def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("set ANTHROPIC_API_KEY to run this example")

    # --- Identity & Governance rail --------------------------------------------------
    identity = Identity(id="support-agent-1", tenant_id="acme", roles=("support",))
    policy = Policy(
        allowed_tools=frozenset({"lookup_order", "issue_refund", "update_user_profile"}),
        max_cost_usd_per_thread=0.50,
        max_steps_per_thread=10,
        requires_approval=frozenset({"issue_refund"}),
    )

    memory = MemoryStore()

    # --- Tools Gateway + Security (signed manifests) ------------------------------------
    tools = ToolRegistry()
    manifests = ToolManifestRegistry()
    for spec in (
        ToolSpec("lookup_order", "Look up an order by id", {"order_id": "string"}, lookup_order),
        ToolSpec("issue_refund", "Refund an order", {"order_id": "string", "amount_usd": "number"}, issue_refund, destructive=True),
        profile_write_tool(memory),  # user_id is auto-injected by orchestration.py — see build_agent_graph's user_id= below
    ):
        tools.register(spec)
        manifests.pin(spec)  # any later drift in a tool's signature fails manifests.verify(spec)

    # --- everything else the skeleton needs ---------------------------------------------
    thread_id = "demo-thread-1"
    tracer = Tracer(thread_id)
    eval_harness = EvalHarness()
    budget = RunBudget(policy)
    guardrails = GuardrailEngine(policy)
    llm = LLMGateway(provider=AnthropicProvider())
    audit = AuditLog()
    breaker = CircuitBreaker()
    cost_ledger = CostLedger()

    # Context Layer: seed semantic memory (RAG) with a policy doc the agent can't know
    # from training data, and a knowledge-graph fact linking the order to its customer.
    memory.semantic.upsert(thread_id, "Refunds over $100 require a manager's sign-off in addition to standard approval.", {"source": "policy.md"})
    memory.semantic.upsert(thread_id, "Standard refund turnaround is 3-5 business days after approval.", {"source": "policy.md"})
    memory.knowledge_graph.add("order:A100", "placed_by", "customer:42")
    memory.knowledge_graph.add("customer:42", "tier", "gold")

    # Session Service, memory layer: a fact from a PAST session (not this
    # thread_id — a different one, simulated below) that must still reach
    # this turn's prompt. Real cross-session continuity, distinct from the
    # checkpointer/episodic memory above (both scoped to ONE thread_id).
    memory.update_profile("customer:42", tier="gold", preferred_contact="email")

    prompts = PromptLibrary(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts"))

    graph = build_agent_graph(
        system_prompt=prompts.get("support_agent"),
        llm=llm, tools=tools, guardrails=guardrails, eval_harness=eval_harness,
        identity=identity, policy=policy, budget=budget, tracer=tracer,
        audit=audit, breaker=breaker, cost_ledger=cost_ledger, memory=memory, step_timeout_s=20.0,
        user_id="customer:42",
    )

    config = {"configurable": {"thread_id": thread_id}}

    state = graph.invoke(
        {"messages": [{"role": "user", "content": "What's the status of order A100?"}], "thread_id": thread_id},
        config,
    )
    memory.append_turn(thread_id, state["messages"][-1])
    print("\n--- final ---")
    print(state["messages"][-1])

    # A destructive tool (issue_refund) requires human approval — the graph pauses,
    # and the UI/UX HITL console prompts for a decision and resumes it.
    state = graph.invoke({"messages": [{"role": "user", "content": "Refund $20 for order A100"}]}, config)
    state = handle_interrupt(graph, config, state)
    memory.append_turn(thread_id, state["messages"][-1])
    print("\n--- final ---")
    print(state["messages"][-1])

    # Context Layer, exercised: this answer only makes sense if RAG actually pulled the
    # seeded policy passages into the prompt — nothing in the conversation states them.
    state = graph.invoke({"messages": [{"role": "user", "content": "How long does a refund take, and when is manager sign-off needed?"}]}, config)
    memory.append_turn(thread_id, state["messages"][-1])
    print("\n--- final (RAG-grounded) ---")
    print(state["messages"][-1])

    print("\n--- knowledge graph: neighbors of order:A100 ---")
    print(memory.knowledge_graph.neighbors("order:A100"))

    print("\n--- procedural memory: learned tool sequence for task 'default' ---")
    print(memory.procedural.best_sequence("default"))

    # --- Session Service, memory layer: cross-SESSION continuity -------------------------
    # A genuinely NEW session (new thread_id, own empty checkpointer history) for the
    # SAME user_id — this only answers correctly if think() loaded the profile fresh,
    # not from anything carried over in this session's own conversation history.
    new_session_config = {"configurable": {"thread_id": "demo-thread-2-different-session"}}
    state = graph.invoke(
        {"messages": [{"role": "user", "content": "Am I a gold-tier customer, and how should I contact you?"}], "thread_id": "demo-thread-2-different-session"},
        new_session_config,
    )
    print("\n--- final (new session, same user_id — profile carried over) ---")
    print(state["messages"][-1])

    # --- Experimentation Service: A/B testing two prompt variants -----------------------
    experiment = Experiment(name="support_prompt_style", variants={"concise": 0.5, "detailed": 0.5})
    tracker = ExperimentTracker()
    variant_prompts = {
        "concise": "You are a support agent. Answer in one short sentence, no elaboration.",
        "detailed": "You are a support agent. Answer thoroughly, explaining relevant policy context.",
    }
    print("\n--- experimentation: two prompt variants, same question, real cost compared ---")
    for customer_id in ("customer:1", "customer:2", "customer:3", "customer:4"):
        variant = experiment.assign(Identity(id=customer_id, tenant_id="acme"))
        variant_graph = build_agent_graph(
            system_prompt=variant_prompts[variant],
            llm=llm, tools=ToolRegistry(), guardrails=guardrails, eval_harness=EvalHarness(),
            identity=identity, policy=Policy(allowed_tools=frozenset(), max_cost_usd_per_thread=0.50, max_steps_per_thread=5),
            budget=RunBudget(policy), tracer=Tracer(customer_id), step_timeout_s=20.0,
        )
        result = variant_graph.invoke(
            {"messages": [{"role": "user", "content": "What's your refund policy?"}], "thread_id": customer_id},
            {"configurable": {"thread_id": customer_id}},
        )
        reply_len = len(result["messages"][-1]["content"])
        tracker.record("support_prompt_style", variant, float(reply_len))
        print(f"  {customer_id} -> variant={variant!r}, reply length={reply_len} chars")
    print("  summary (mean reply length per variant):", tracker.summary("support_prompt_style"))

    # --- Security, Observability & Cost --------------------------------------------------
    print("\n--- audit trail ---")
    for entry in audit.entries:
        print(entry)

    metrics = Metrics(tracer)
    alerts = check_alerts(metrics, budget_cost_usd=budget.cost_usd, max_cost_usd=policy.max_cost_usd_per_thread)
    print("\n--- alerts ---", alerts or "none")

    print("\n--- dashboard ---")
    print(render_dashboard(tracer=tracer, eval_harness=eval_harness, budget=budget, ledger=cost_ledger))


if __name__ == "__main__":
    main()

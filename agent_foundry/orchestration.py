"""Layer 02 — Orchestration: composable LangGraph primitives, not one fixed shape.

Every other layer in this framework is a slot you can fill with your own
implementation (a Provider for any LLM, a ToolSpec or MCPToolSource for any
tool, a VectorStore for any memory backend). Orchestration is the same kind
of slot: `make_think_node` / `make_act_node` / `make_router` are the actual
composable units — a think/act pair bound to one AgentConfig. Both graph
builders below are just two different ways of wiring those units together:

  - `build_agent_graph`      — one agent, the think/act loop. Convenience
                                wrapper kept for the simple case.
  - `build_supervisor_graph` — many independent agents (their own tools,
                                guardrails, budget, memory — whatever that
                                specialist needs) in one graph, with an LLM
                                router deciding which one handles a turn.

Bring your own topology by importing the three `make_*` functions directly
and wiring a StateGraph however your use case needs — hierarchical
delegation, parallel fan-out, a supervisor of supervisors. Nothing about
these primitives assumes there's only one agent in the graph.

Tool-calling convention (kept deliberately simple, no vendor tool-schema
lock-in): the model is instructed to reply with exactly
`CALL <tool_name> {"arg": "value"}` when it wants to invoke a tool.

Checkpointer durability: every build_*_graph() below defaults to LangGraph's
MemorySaver — in-process only, every thread's state is gone on restart. This
default keeps the framework's own test suite and quickstart dependency-free,
same posture as InMemoryVectorStore in context.py. For anything that must
survive a restart (which is what "backup & disaster recovery" means for
conversation state — see docs/BACKUP_DR.md), pass checkpointer=<a
BaseCheckpointSaver> — e.g. langgraph-checkpoint-sqlite's SqliteSaver or
langgraph-checkpoint-postgres's PostgresSaver, both drop-in.
"""
from __future__ import annotations

import json
import operator
import time
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from .blackboard import Blackboard, parse_post
from .context import ContextEngine, MemoryStore
from .contracts import AgentRole, Identity, Policy, ToolSpec
from .eval import EvalHarness
from .guardrails import GuardrailEngine
from .kpi import KPI
from .llm_gateway import LLMGateway
from .observability import CostLedgerLike, Tracer
from .runtime import (
    CircuitBreaker,
    CircuitBreakerLike,
    LatencyBudgetLike,
    RunBudgetLike,
    SLATrackerLike,
    with_timeout,
)
from .security import AuditLog
from .tools_gateway import PermissionDenied, ToolRegistry


class AgentState(TypedDict):
    messages: Annotated[list[dict], operator.add]
    # See CritiqueConfig.max_retries — how many "gather more evidence and
    # retry" passes THIS turn has used. Deliberately a plain (overwrite,
    # not Annotated[..., operator.add]) field: it's part of the checkpointed
    # state, which persists across an entire multi-turn session, not just
    # one turn — an accumulating reducer would carry turn 1's retry count
    # into turn 2's budget instead of resetting per-turn. make_think_node
    # explicitly resets it to 0 on every fresh user turn (see its own
    # is_fresh_turn handling); make_critique_node's retry branch sets it to
    # the new total directly (computed as retries_done + 1 in Python, not
    # via the reducer), which composes correctly with that reset.
    critique_retries: int
    # The critique score from THIS turn's most recent retry attempt (None
    # until the first retry happens) — lets make_critique_node tell "the
    # retry genuinely improved things, still imperfect but worth showing
    # flagged" from "the retry made no real progress, this needs a human"
    # once retries are exhausted. Same plain-field, reset-per-turn
    # reasoning as critique_retries.
    critique_last_score: float | None
    thread_id: str


@dataclass
class CritiqueConfig:
    """Wires a live confidence/quality gate into the think/act loop — the
    "Critique & Verify" step a plain think/act cycle skips: scores a turn's
    FINAL answer (no more tool calls left to make) with any KPI. Two
    thresholds, two different behaviors — this is deliberate, not merely
    "block below a number":

    - Below `kpi.threshold` alone: still a real answer, always sent back to
      the user — just marked low-confidence (score attached to the eval
      record; a caller like healthcare/backend's app.py surfaces it as a UI
      badge). Most "not perfectly grounded" answers land here. Withholding
      a genuinely useful answer over an imperfect grounding score is worse
      than showing it clearly labeled as uncertain.
    - Below `escalate_threshold` (stricter, and opt-in — None means never
      escalate): the rare, genuinely ambiguous/hallucinated case — pauses
      via interrupt() for a human reviewer instead of auto-replying, same
      HITL mechanism make_act_node already uses for tool approval.

    Optional (AgentConfig.critique defaults to None): every agent that
    doesn't set this keeps its exact current behavior.

    Runs as its own graph node (make_critique_node), never inline inside
    make_think_node, specifically so a paused-then-resumed turn doesn't
    re-run the (real, billed) LLM call that already produced the draft —
    LangGraph replays a node from its start on resume, so anything expensive
    must happen in a node *before* the one that calls interrupt(), never in
    the same one after it."""

    kpi: KPI  # e.g. kpi.reference_check_kpi("grounding", references=...) — any KPI works
    context: Callable[[AgentState, str], dict[str, Any]]  # (state, draft_reply) -> this KPI's scoring context
    escalate_threshold: float | None = None  # below this -> HITL pause; None -> never escalate, only ever flag
    fallback_message: str = "This answer needs review before it can be shared — a reviewer has been notified."
    # How many times a low-but-not-escalating score sends the turn back to
    # think (with more tools/evidence available) before falling through to
    # today's flag-and-show behavior. 0 (default) = no retry, unchanged
    # behavior — opt in per use case. Bounded independently of
    # RunBudget.max_steps_per_thread (that's a much larger, whole-session
    # ceiling; this is specifically how many extra attempts ONE turn's
    # critique gate grants before giving up on improving it further).
    max_retries: int = 0
    retry_prompt: str = (
        "Your previous answer scored {score:.2f} against {kpi_name} (threshold {threshold}) — it wasn't fully "
        "supported by the retrieved evidence. Before answering again: use your available tools to gather more "
        "or different evidence if that would help. If, after that, you genuinely cannot answer confidently — "
        "because the evidence is still insufficient, OR because the original question itself is ambiguous — do "
        "not guess. Instead reply with EXACTLY: {clarify_prefix} <a specific question for the user that would "
        "let you answer correctly>. Otherwise, give your revised, fully grounded answer to: {question}"
    )


CLARIFY_PREFIX = "CLARIFY:"  # a reply starting with this is a genuine question back to the user, not a claim to
# be scored for grounding — make_critique_node checks for it before running the KPI at all. Shared as a module-
# level constant so a builder's own system prompt (see healthcare's healthcare_assistant.md) and the retry_prompt
# above both reference the exact same literal string.


@dataclass
class AgentConfig:
    """Everything one agent needs, from every layer — the unit make_think_node /
    make_act_node / make_router are parameterized by. A multi-agent graph holds
    one AgentConfig per specialist, each fully independent: different tools,
    guardrails, policy, budget, even a different LLM gateway, if that's what the
    use case calls for."""

    system_prompt: str
    llm: LLMGateway
    tools: ToolRegistry
    guardrails: GuardrailEngine
    eval_harness: EvalHarness
    identity: Identity
    policy: Policy
    budget: RunBudgetLike
    tracer: Tracer
    # Either a fixed route name (unchanged default behavior) or a callable
    # that inspects the live turn and picks the route per-message — e.g.
    # a cheap/default/hard classifier keyed on question complexity instead
    # of one route for every turn a graph will ever handle.
    task: str | Callable[[AgentState], str] = "default"
    audit: AuditLog = field(default_factory=AuditLog)
    breaker: CircuitBreakerLike = field(default_factory=CircuitBreaker)
    cost_ledger: CostLedgerLike | None = None
    memory: MemoryStore | None = None
    context_engine: ContextEngine | None = None
    step_timeout_s: float | None = None
    latency_budget: LatencyBudgetLike | None = None
    sla_tracker: SLATrackerLike | None = None
    critique: CritiqueConfig | None = None
    role: AgentRole = AgentRole.GENERALIST
    # Either a fixed user id (one deployment, one known user — e.g. a
    # personal assistant) or a callable resolving it per-turn from live
    # state (one deployment, many users across many sessions — e.g. a
    # support agent where state["thread_id"] maps to a real customer id).
    # None (default): no cross-session user memory — unchanged behavior.
    # When set, think() loads config.memory.get_profile(user_id) and
    # injects it into the prompt every turn — real continuity across
    # SEPARATE sessions, not just within one (that's what the checkpointer
    # + episodic memory already give you, session-scoped). A tool
    # declaring a `user_id` parameter (see context.profile_write_tool)
    # gets this SAME resolved value auto-injected by make_act_node,
    # overriding whatever the model supplied — a model should never be
    # trusted to name which real user's profile it's updating.
    user_id: str | Callable[[AgentState], str] | None = None


def make_think_node(config: AgentConfig) -> Callable[[AgentState], dict]:
    def think(state: AgentState) -> dict:
        # state["thread_id"] — not config.tracer.thread_id, which is fixed once at
        # graph-build time. One compiled graph commonly serves many sessions (see
        # serve.py: one shared `graph`, a different thread_id per HTTP request) —
        # scoping memory/RAG/budget lookups to the build-time tracer id would
        # silently mix every session's semantic memory into every other
        # session's prompt, or let one session's spend exhaust every other
        # session's cost/step ceiling too.
        session_id = state.get("thread_id") or config.tracer.thread_id
        config.budget.step(thread_id=session_id)
        if config.latency_budget is not None:
            config.latency_budget.check(thread_id=session_id)
        # A fresh turn (not a think() re-entry after a tool result, or a
        # critique retry re-entry — both also end in a role="user" message)
        # resets critique_retries — see AgentState's own docstring for why
        # this must reset per-turn, not accumulate for the whole session.
        # make_critique_node's retry branch tags its synthetic message with
        # _critique_retry=True precisely so this check can tell "a genuine
        # new user message" from "critique looped back with a retry
        # prompt" — both have role="user", value alone (e.g. checking
        # critique_retries == 0) can't distinguish them, since a PRIOR
        # turn that used retries would leave a stale nonzero value sitting
        # in the checkpoint for the next genuinely-fresh turn too.
        # Gated on config.critique is not None: critique_retries is only
        # meaningful for a graph with a retry-capable critique gate. Left
        # ungated, EVERY think() node writes it on every fresh turn — fine
        # for a single-agent graph, but build_fanout_graph/build_swarm_graph
        # etc. can run several think() nodes from DIFFERENT AgentConfigs
        # against the same shared AgentState in the same parallel step,
        # and a plain (non-Annotated) channel rejects more than one
        # concurrent write per step ("Can receive only one value per
        # step") — found live running the existing fanout test suite.
        last_message = state["messages"][-1]
        is_fresh_turn = last_message["role"] == "user" and not last_message.get("_critique_retry")
        reset = {"critique_retries": 0, "critique_last_score": None} if (config.critique is not None and is_fresh_turn) else {}
        if config.memory is not None and state["messages"][-1]["role"] == "user":
            # A fresh turn (not a think() re-entry after a tool result, where
            # the last message is role="tool") — stamps THIS turn's own start
            # time, read back in _finalize_turn for sla_tracker. Deliberately
            # NOT config.latency_budget.elapsed_s(): that's cumulative since
            # the session's first-ever turn (correct for its own job — a
            # runaway-session budget ceiling — but wrong here, where SLA
            # latency means "how fast was this one reply," not "how long has
            # this session been open."
            config.memory.working.setdefault(session_id, {})["turn_start_ts"] = time.time()

        last_user = next((m for m in reversed(state["messages"]) if m["role"] == "user"), None)
        if last_user is not None:
            gr_in = config.guardrails.check_input(last_user["content"])
            if not gr_in.allowed:
                config.eval_harness.record("atomic", "input_guardrail", "blocked", 0.0, reason=gr_in.reason, session_id=session_id)
                return {**reset, "messages": [{"role": "assistant", "content": "I can't process that request."}]}

        prompt = config.system_prompt
        if config.context_engine is not None and last_user is not None:
            # Full retrieve -> rank -> filter -> compress -> budget pipeline.
            built = config.context_engine.build(session_id, last_user["content"])
            if built:
                prompt = config.system_prompt + "\n\nRelevant context:\n" + built
        elif config.memory is not None and last_user is not None:
            # RAG, unranked: pull relevant passages from semantic memory straight into the prompt.
            # Screened the same way ContextEngine.filter() screens retrieved
            # content — a passage is untrusted (an uploaded document, a prior
            # tool result), not the live user turn check_input already
            # covers above, so a planted instruction in one would otherwise
            # reach the prompt completely unscreened (OWASP LLM01, indirect).
            from .guardrails import looks_like_injection
            passages = [p for p in config.memory.semantic.search(session_id, last_user["content"]) if not looks_like_injection(p)]
            if passages:
                prompt = config.system_prompt + "\n\nRelevant context:\n" + "\n".join(f"- {p}" for p in passages)

        if config.memory is not None and config.user_id is not None:
            # Real continuity across SEPARATE sessions — not the checkpointer/
            # episodic memory's job (both are scoped to ONE session's
            # thread_id) but MemoryStore.profiles', keyed by a real user id
            # that outlives any one session. Injected every turn, same as
            # RAG passages, so "the user already told us this in a past
            # session" doesn't have to be re-asked or re-discovered.
            resolved_user_id = config.user_id(state) if callable(config.user_id) else config.user_id
            profile = config.memory.get_profile(resolved_user_id)
            if profile:
                prompt = prompt + "\n\nWhat you already know about this user (persists across sessions):\n" + "\n".join(f"- {k}: {v}" for k, v in profile.items())

        native_tools = config.tools.native_tools(config.policy)
        messages = [{"role": "system", "content": prompt}, *state["messages"]]
        task = config.task(state) if callable(config.task) else config.task
        with config.tracer.span("orchestration.think") as span:
            call = (lambda: config.llm.complete(messages, task=task, tools=native_tools or None))
            resp = with_timeout(call, seconds=config.step_timeout_s) if config.step_timeout_s else call()
            config.budget.spend(resp.cost_usd, thread_id=session_id)
            span["attributes"].update(cost_usd=resp.cost_usd, model=resp.model, native_tool_calls=len(resp.tool_calls), task=task)
        config.eval_harness.record("atomic", "think", "responded", 1.0, model=resp.model, task=task, session_id=session_id)

        gr_out = config.guardrails.check_output(resp.text)
        if not gr_out.allowed:
            config.eval_harness.record("atomic", "output_guardrail", "blocked", 0.0, reason=gr_out.reason, session_id=session_id)
            return {**reset, "messages": [{"role": "assistant", "content": "I can't share that — it looks like it contains sensitive data."}]}

        if resp.tool_calls:
            # Real provider-native tool-calling — resp.tool_calls is structured
            # data from the model's actual tool_use blocks, not text we parse.
            return {**reset, "messages": [{
                "role": "assistant", "content": resp.text,
                "tool_calls": [{"id": tc.id, "name": tc.name, "args": tc.args} for tc in resp.tool_calls],
            }]}
        return {**reset, "messages": [{"role": "assistant", "content": resp.text}]}

    return think


def make_act_node(config: AgentConfig) -> Callable[[AgentState], dict]:
    def tool_msg(content: Any, *, tool_call_id: str | None, ok: bool = True) -> dict:
        msg: dict[str, Any] = {"role": "tool", "content": content}
        if tool_call_id is not None:
            msg["tool_call_id"] = tool_call_id
            msg["ok"] = ok
        return msg

    def act(state: AgentState) -> dict:
        session_id = state.get("thread_id") or config.tracer.thread_id
        resolved_user_id = (config.user_id(state) if callable(config.user_id) else config.user_id) if config.user_id is not None else None
        calls = _get_all_tool_calls(state["messages"][-1])
        if not calls:
            return {}

        results: list[dict] = []
        for tool_name, args, tool_call_id in calls:
            if config.tools.has(tool_name):
                param_names = _tool_param_names(config.tools.get(tool_name))
                if "session_id" in param_names:
                    # Never trust a model-supplied session_id for a session-
                    # scoped tool (a memory write, "what has THIS session
                    # already stored") — always use the graph's own real
                    # session_id, overriding whatever the model passed or left
                    # out. A confused or manipulated model guessing/hallucinating
                    # another session's id could otherwise read or WRITE another
                    # user's data; declaring `session_id` as a parameter is
                    # enough for a tool to opt into this — it never needs to be
                    # supplied by (or trusted from) the model at all.
                    args = {**args, "session_id": session_id}
                if "user_id" in param_names and resolved_user_id is not None:
                    # Same reasoning, for cross-session user profile tools
                    # (context.profile_write_tool) — the resolved config.user_id
                    # always wins over anything the model supplied.
                    args = {**args, "user_id": resolved_user_id}
            if config.breaker.is_open(tool_name):
                results.append(tool_msg(f"{tool_name} temporarily disabled after repeated failures", tool_call_id=tool_call_id, ok=False))
                continue

            destructive = config.tools.has(tool_name) and config.tools.get(tool_name).destructive
            gr = config.guardrails.check_action(tool_name, cost_so_far=config.budget.cost_usd_for(session_id), destructive=destructive)
            if not gr.allowed and gr.reason and "approval" in gr.reason:
                decision = interrupt({"tool": tool_name, "args": args, "reason": gr.reason})
                config.audit.record(identity=config.identity, action="approval_decision", tool=tool_name, approved=bool(decision.get("approved")))
                if not decision.get("approved"):
                    config.eval_harness.record("component", tool_name, "approval", 0.0, reason="denied by reviewer", session_id=session_id)
                    results.append(tool_msg(f"{tool_name} denied by reviewer", tool_call_id=tool_call_id, ok=False))
                    continue
            elif not gr.allowed:
                config.eval_harness.record("component", tool_name, "action_guardrail", 0.0, reason=gr.reason, session_id=session_id)
                results.append(tool_msg(gr.reason, tool_call_id=tool_call_id, ok=False))
                continue

            with config.tracer.span("orchestration.act", tool=tool_name) as span:
                try:
                    invoke = (lambda tn=tool_name, a=args: config.tools.invoke(tn, a, identity=config.identity, policy=config.policy))
                    result = with_timeout(invoke, seconds=config.step_timeout_s) if config.step_timeout_s else invoke()
                except PermissionDenied as e:
                    config.eval_harness.record("component", tool_name, "permission", 0.0, reason=str(e), session_id=session_id)
                    results.append(tool_msg(str(e), tool_call_id=tool_call_id, ok=False))
                    continue
                span["attributes"].update(ok=result.ok, latency_ms=result.latency_ms)

            config.breaker.record(tool_name, result.ok)
            config.audit.record(identity=config.identity, action="tool_call", tool=tool_name, ok=result.ok)
            config.eval_harness.record("component", tool_name, "success", 1.0 if result.ok else 0.0,
                                        session_id=session_id, **({"reason": result.error} if not result.ok else {}))
            if config.memory is not None and result.ok:
                config.memory.working.setdefault(session_id, {}).setdefault("tool_sequence", []).append(tool_name)
            results.append(tool_msg(result.output if result.ok else result.error, tool_call_id=tool_call_id, ok=result.ok))

        return {"messages": results}

    return act


def _finalize_turn(config: AgentConfig, session_id: str, *, outcome: str = "completed") -> None:
    """Session-closing bookkeeping for a turn that's genuinely done — flow-
    completed eval record, cost ledger close, SLA tracker record, procedural-
    memory tool-sequence capture. Was inlined in make_router; hoisted out so
    make_critique_node can call the exact same bookkeeping once IT decides
    the turn is done (critique may resolve one or more steps after the
    router first saw a tool-call-free reply).

    `outcome` flows straight into CostLedger.close_task — the field already
    existed there but every caller passed the literal string "completed",
    so cost could never be broken down by clean-vs-needed-review. make_
    critique_node passes "completed_with_review" when the turn needed
    human review, letting a dashboard show cost-per-successful-task
    separately from cost-per-reviewed-task."""
    config.eval_harness.record("flow", session_id, "completed", 1.0)
    if config.cost_ledger is not None:
        config.cost_ledger.close_task(
            thread_id=session_id, tenant_id=config.identity.tenant_id,
            cost_usd=config.budget.cost_usd_for(session_id), steps=config.budget.steps_for(session_id), outcome=outcome,
        )
    if config.sla_tracker is not None:
        # this turn's own wall-clock time (stamped in make_think_node on the
        # turn's first think() call) — not config.latency_budget.elapsed_s(),
        # which is cumulative since the session started and would report a
        # session open for 10 minutes as a 10-minute-latency reply. Falls
        # back to 0.0 when no memory is configured (turn_start_ts has
        # nowhere to live) — same graceful-degradation posture every other
        # memory-optional feature here already has.
        turn_start = config.memory.working.get(session_id, {}).get("turn_start_ts") if config.memory is not None else None
        latency_ms = (time.time() - turn_start) * 1000 if turn_start is not None else 0.0
        config.sla_tracker.record(ok=True, latency_ms=latency_ms)
    if config.memory is not None:
        working = config.memory.working.get(session_id, {})
        working.pop("turn_start_ts", None)
        sequence = working.pop("tool_sequence", [])
        if sequence:
            task_label = config.task if isinstance(config.task, str) else "dynamic"
            config.memory.procedural.record(task_label, sequence)


def make_critique_node(config: AgentConfig) -> Callable[[AgentState], dict]:
    """The "Critique & Verify" step: scores the just-produced final answer
    with config.critique.kpi. Two outcomes (see CritiqueConfig's own
    docstring for why these are different, not one "block below X"):

    - Below kpi.threshold but not below escalate_threshold: the answer is
      NOT withheld — it's always sent through, unmodified. The score is
      recorded (eval_harness, level="atomic"/unit="critique") for a caller
      to surface as a low-confidence flag (see healthcare/backend/app.py's
      /chat, which reads this same record back to attach it to the HTTP
      response) — no graph/message mutation needed for this path.
    - Below escalate_threshold (and only if one is configured): the rare,
      genuinely-ambiguous case — pauses for human review via interrupt(),
      same mechanism make_act_node already uses for tool-approval HITL, so
      the HTTP layer needs no new code path (chat_response_from_result
      already turns a pending interrupt into status="awaiting_approval";
      /resume with {"approved": bool} already unpauses it). Approved -> the
      draft stands as the final reply; not approved ->
      config.critique.fallback_message replaces it.

    Two more things happen before any of that, in order:

    1. CLARIFY: if the draft starts with CLARIFY_PREFIX, the model itself
       decided it can't answer confidently and is asking the user a genuine
       question instead of guessing (see CritiqueConfig.retry_prompt, and
       a builder's own system prompt, for where the model learns this
       convention). Not a claim, so it's never scored for grounding — the
       turn finalizes immediately with the clean question (prefix
       stripped) as the reply. Works whether or not max_retries is set.
    2. Retry: when the score is below kpi.threshold but NOT below
       escalate_threshold, and this turn hasn't used up
       config.critique.max_retries yet, the graph loops back to "think"
       (make_critique_router routes there) with CritiqueConfig.retry_prompt
       — a genuine chance to gather more/different evidence (more tool
       calls) and produce a better answer, instead of immediately flagging
       or escalating a first-pass miss. Only after retries are exhausted
       does the score fall through to the flag/escalate logic below.

    Only reached when config.critique is set (see make_router) and think
    produced a final, tool-call-free answer — never runs mid-tool-loop."""

    def critique(state: AgentState) -> dict:
        assert config.critique is not None
        session_id = state.get("thread_id") or config.tracer.thread_id
        draft = state["messages"][-1]["content"]

        if isinstance(draft, str) and draft.strip().startswith(CLARIFY_PREFIX):
            question = draft.strip()[len(CLARIFY_PREFIX):].strip()
            config.eval_harness.record("atomic", "critique", "clarification_requested", 1.0,
                                        reason="model asked the user a clarifying question instead of guessing", session_id=session_id)
            _finalize_turn(config, session_id, outcome="needs_clarification")
            return {"messages": [{"role": "assistant", "content": question}]}

        result = config.critique.kpi.evaluate(config.critique.context(state, draft))
        kpi_threshold = config.critique.kpi.threshold
        threshold_str = f"{kpi_threshold:.2f}" if kpi_threshold is not None else "n/a"
        below_escalate = config.critique.escalate_threshold is not None and result.value < config.critique.escalate_threshold

        retries_done = state.get("critique_retries", 0)
        if not result.passed and not below_escalate and retries_done < config.critique.max_retries:
            original_question = next((m["content"] for m in reversed(state["messages"]) if m["role"] == "user"), "")
            reason = f"{result.value:.2f} below threshold {threshold_str} — retrying ({retries_done + 1}/{config.critique.max_retries}) to gather more evidence"
            config.eval_harness.record("atomic", "critique", result.name, result.value, reason=reason, session_id=session_id)
            retry_msg = config.critique.retry_prompt.format(
                score=result.value, kpi_name=result.name, threshold=threshold_str,
                clarify_prefix=CLARIFY_PREFIX, question=original_question,
            )
            return {
                "messages": [{"role": "user", "content": retry_msg, "_critique_retry": True}],  # marker make_think_node's fresh-turn reset checks for
                "critique_retries": retries_done + 1,
                "critique_last_score": result.value,
            }

        # Exhausting every retry WITHOUT MAKING ANY REAL PROGRESS is the
        # actual signal this needs a human — not merely "still below
        # threshold." A retry that genuinely improved the score (still
        # imperfect, but better than the attempt before it) gets the same
        # flag-and-show treatment a first-pass miss always has — the
        # original product principle ("always answer, flag low confidence;
        # only truly ambiguous escalates") still holds for real, if
        # partial, progress. Only a retry that made no progress at all
        # (same or worse score) means the model is genuinely stuck, which
        # IS a stronger reason for review than a single-pass miss.
        # Escalates only when escalate_threshold is actually configured —
        # a builder with none set has said "never pause for a human,
        # only ever flag," and retry exhaustion doesn't override that.
        prev_score = state.get("critique_last_score")
        retried_without_improvement = retries_done > 0 and prev_score is not None and result.value <= prev_score
        retries_exhausted_no_progress = (
            not result.passed and config.critique.max_retries > 0 and retries_done >= config.critique.max_retries
            and config.critique.escalate_threshold is not None and retried_without_improvement
        )
        escalate = below_escalate or retries_exhausted_no_progress

        if below_escalate:
            reason = f"{result.value:.2f} below escalate_threshold {config.critique.escalate_threshold:.2f} — paused for human review"
        elif retries_exhausted_no_progress:
            reason = f"{result.value:.2f} unimproved after {retries_done} retries (was {prev_score:.2f}) — paused for human review"
        elif not result.passed and retries_done > 0:
            reason = f"{result.value:.2f} below threshold {threshold_str} — improved over {retries_done} retries (from {prev_score:.2f}), flagged low-confidence" if prev_score is not None else f"{result.value:.2f} below threshold {threshold_str} — answered, flagged low-confidence"
        elif not result.passed:
            reason = f"{result.value:.2f} below threshold {threshold_str} — answered, flagged low-confidence"
        else:
            reason = f"{result.value:.2f} at or above threshold {threshold_str} — passed"
        config.eval_harness.record("atomic", "critique", result.name, result.value, reason=reason, session_id=session_id)
        update: dict[str, Any] = {}
        if escalate:
            decision = interrupt({
                "reason": "low_confidence", "kpi": result.name, "score": result.value,
                "threshold": config.critique.escalate_threshold, "draft_reply": draft,
            })
            approved = bool(decision.get("approved"))
            config.audit.record(identity=config.identity, action="critique_review", tool=result.name, score=result.value, approved=approved)
            if not approved:
                update = {"messages": [{"role": "assistant", "content": config.critique.fallback_message}]}
            _finalize_turn(config, session_id, outcome="completed_with_review")
        else:
            _finalize_turn(config, session_id, outcome="completed" if result.passed else "completed_low_confidence")
        return update

    return critique


def make_self_verify_node(config: AgentConfig, *, context: Callable[[AgentState, str], dict]) -> Callable[[AgentState], dict]:
    """One extra LLM pass that re-checks the just-produced draft against its
    OWN retrieved evidence — reusing the exact same `context` callable the
    critique gate (make_critique_node) will use right after this node runs,
    so self-verify checks against the identical evidence critique is about
    to score against, not a second, possibly-inconsistent retrieval.

    Not a replacement for the critique/HITL gate — critique still runs
    afterward and scores whatever this node produced, exactly as it always
    has. This exists for agents whose critique.escalate_threshold is
    deliberately loose (a human backstop that's meant to rarely trigger,
    e.g. a Doctor persona trusted to judge nuance itself): giving the model
    one more chance to catch and fix its own unsupported claim compensates
    for that loose external gate, before the answer ever reaches it.

    Appends its output as a new final assistant message (AgentState.messages
    uses operator.add, i.e. append, not replace) — critique's own
    `draft = state["messages"][-1]["content"]` then naturally reads THIS
    node's (possibly revised) text, not the pre-verify draft."""

    def self_verify(state: AgentState) -> dict:
        session_id = state.get("thread_id") or config.tracer.thread_id
        draft = state["messages"][-1]["content"]
        ctx = context(state, draft)
        evidence = "\n".join(f"- {p}" for p in ctx.get("passages", [])) or "(no passages retrieved)"
        prompt = (
            f"You drafted this answer:\n\n{draft}\n\n"
            f"Here is the actual retrieved evidence it should be grounded in:\n\n{evidence}\n\n"
            "If every claim in your draft is directly supported by this evidence, repeat the "
            "draft exactly, unchanged. If any part is NOT supported by the evidence, rewrite "
            "ONLY that part so the answer is fully grounded — do not invent new evidence, and "
            "do not add caveats that weren't asked for. Reply with ONLY the (possibly revised) "
            "final answer text, nothing else."
        )
        task = config.task(state) if callable(config.task) else config.task
        with config.tracer.span("orchestration.self_verify") as span:
            resp = config.llm.complete([{"role": "user", "content": prompt}], task=task)
            config.budget.spend(resp.cost_usd, thread_id=session_id)
            span["attributes"].update(cost_usd=resp.cost_usd, model=resp.model)
        revised = resp.text.strip() != draft.strip()
        reason = "draft's claims were not fully supported by evidence — revised" if revised else "draft's claims already matched the retrieved evidence — unchanged"
        config.eval_harness.record("atomic", "self_verify", "revised" if revised else "unchanged", 1.0 if revised else 0.0, reason=reason, session_id=session_id)
        return {"messages": [{"role": "assistant", "content": resp.text}]}

    return self_verify


def make_router(config: AgentConfig) -> Callable[[AgentState], str]:
    """Returns the abstract label "act", "critique", or END — the graph
    builder maps "act"/"critique" to whatever the actual next-node names
    are, so the same router works whether this agent is the only node in
    the graph or one of many. "critique" is only ever returned when
    config.critique is set; a final answer routes straight to END
    (finalizing the turn here) otherwise, exactly as before critique
    existed."""

    def route(state: AgentState) -> str:
        if _get_all_tool_calls(state["messages"][-1]):
            return "act"
        if config.critique is not None:
            return "critique"
        session_id = state.get("thread_id") or config.tracer.thread_id
        _finalize_turn(config, session_id)
        return END

    return route


def make_critique_router(config: AgentConfig) -> Callable[[AgentState], str]:
    """Only wired in when CritiqueConfig.max_retries > 0 (see
    build_agent_graph) — routes AFTER critique runs. interrupt() (the
    escalate path) suspends the graph entirely and never reaches this
    router, so it only has two real outcomes to distinguish: critique
    appended a retry prompt (make_critique_node's retry branch always
    appends it as role="user") -> back to "think" for another attempt;
    anything else (passed, flagged, or a CLARIFY question) -> END."""

    def route(state: AgentState) -> str:
        if state["messages"][-1]["role"] == "user":
            return "think"
        return END

    return route


def make_swarm_router(config: AgentConfig, peer_names: list[str]) -> Callable[[AgentState], str]:
    """Like make_router, but a specialist can also hand off directly to a named
    peer by replying `HANDOFF <agent_name>` — decentralized routing (no central
    supervisor node), as opposed to build_supervisor_graph's centralized one."""

    def route(state: AgentState) -> str:
        content = state["messages"][-1]["content"]
        if isinstance(content, str) and content.startswith("HANDOFF "):
            target = content[len("HANDOFF "):].strip()
            if target in peer_names:
                config.eval_harness.record("component", "handoff", target, 1.0)
                return target
        return make_router(config)(state)

    return route


def build_agent_graph(
    *,
    system_prompt: str,
    llm: LLMGateway,
    tools: ToolRegistry,
    guardrails: GuardrailEngine,
    eval_harness: EvalHarness,
    identity: Identity,
    policy: Policy,
    budget: RunBudgetLike,
    tracer: Tracer,
    task: str | Callable[[AgentState], str] = "default",
    audit: AuditLog | None = None,
    breaker: CircuitBreakerLike | None = None,
    cost_ledger: CostLedgerLike | None = None,
    memory: MemoryStore | None = None,
    context_engine: ContextEngine | None = None,
    step_timeout_s: float | None = None,
    latency_budget: LatencyBudgetLike | None = None,
    sla_tracker: SLATrackerLike | None = None,
    critique: CritiqueConfig | None = None,
    self_verify: bool = False,
    user_id: str | Callable[[AgentState], str] | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
):
    """One agent, the think/act loop. Thin wrapper over make_think_node/make_act_node —
    see build_supervisor_graph for the same primitives wired into a multi-agent graph.

    `user_id`: set to enable cross-SESSION memory (see AgentConfig.user_id's
    own docstring) — think() auto-loads/injects config.memory.get_profile(),
    and any registered tool declaring a `user_id` parameter (see
    context.profile_write_tool) gets it auto-injected too, never trusting a
    model-supplied value.

    `self_verify=True` requires `critique` to be set — it inserts
    make_self_verify_node (reusing critique.context, the same evidence
    critique itself will score against) between the final draft and the
    critique gate: think -> act -> ... -> self_verify -> critique -> END,
    instead of the default think -> act -> ... -> critique -> END. See
    make_self_verify_node's own docstring for when this is worth the extra
    LLM call (agents with a deliberately loose critique.escalate_threshold)."""
    if self_verify and critique is None:
        raise ValueError("self_verify=True requires critique to be set — it reuses critique.context")
    config = AgentConfig(
        system_prompt=system_prompt, llm=llm, tools=tools, guardrails=guardrails,
        eval_harness=eval_harness, identity=identity, policy=policy, budget=budget,
        tracer=tracer, task=task, audit=audit or AuditLog(), breaker=breaker or CircuitBreaker(),
        cost_ledger=cost_ledger, memory=memory, context_engine=context_engine, step_timeout_s=step_timeout_s,
        latency_budget=latency_budget, sla_tracker=sla_tracker, critique=critique, user_id=user_id,
    )
    graph = StateGraph(AgentState)
    graph.add_node("think", make_think_node(config))
    graph.add_node("act", make_act_node(config))
    mapping = {"act": "act", END: END}
    if critique is not None:
        graph.add_node("critique", make_critique_node(config))
        if critique.max_retries > 0:
            # A low-but-not-escalating score can send the turn back to
            # "think" (see make_critique_node's retry branch / CLARIFY
            # path) instead of always finalizing straight to END.
            graph.add_conditional_edges("critique", make_critique_router(config), {"think": "think", END: END})
        else:
            graph.add_edge("critique", END)
        if self_verify:
            graph.add_node("self_verify", make_self_verify_node(config, context=critique.context))
            graph.add_edge("self_verify", "critique")
            mapping["critique"] = "self_verify"
        else:
            mapping["critique"] = "critique"
    graph.set_entry_point("think")
    graph.add_conditional_edges("think", make_router(config), mapping)
    graph.add_edge("act", "think")
    return graph.compile(checkpointer=checkpointer or MemorySaver())


def build_supervisor_graph(
    *,
    supervisor_prompt: str,
    agents: dict[str, AgentConfig],
    llm: LLMGateway,
    task: str = "default",
    checkpointer: BaseCheckpointSaver | None = None,
):
    """A real multi-agent supervisor: one router LLM call decides which named
    specialist handles a turn, via LangGraph's Command(goto=...) — the same
    primitive langgraph-supervisor uses. Each specialist keeps its own tools,
    guardrails, policy, budget and memory; nothing is shared unless you choose
    to pass the same object into more than one AgentConfig."""

    def supervisor(state: AgentState) -> Command[Any]:
        options = ", ".join(agents)
        messages = [
            {"role": "system", "content": f"{supervisor_prompt}\n\nReply with exactly: ROUTE <agent_name>\nAvailable agents: {options}"},
            *state["messages"],
        ]
        resp = llm.complete(messages, task=task)
        name = resp.text.strip().removeprefix("ROUTE ").strip()
        if name not in agents:
            name = next(iter(agents))  # unrecognized routing decision -> fall back, don't crash the graph
        return Command(goto=f"{name}_think")

    graph = StateGraph(AgentState)
    graph.add_node("supervisor", supervisor)
    for name, config in agents.items():
        graph.add_node(f"{name}_think", make_think_node(config))
        graph.add_node(f"{name}_act", make_act_node(config))
        graph.add_conditional_edges(f"{name}_think", make_router(config), {"act": f"{name}_act", END: END})
        graph.add_edge(f"{name}_act", f"{name}_think")
    graph.set_entry_point("supervisor")
    return graph.compile(checkpointer=checkpointer or MemorySaver())


def build_swarm_graph(*, agents: dict[str, AgentConfig], entry: str, checkpointer: BaseCheckpointSaver | None = None):
    """Decentralized multi-agent: no central router — any specialist can hand off
    directly to a named peer (see make_swarm_router). Complements
    build_supervisor_graph's centralized routing; same AgentConfig/node-factory
    primitives either way."""
    graph = StateGraph(AgentState)
    for name, config in agents.items():
        others = [n for n in agents if n != name]
        graph.add_node(f"{name}_think", make_think_node(config))
        graph.add_node(f"{name}_act", make_act_node(config))
        mapping = {"act": f"{name}_act", END: END, **{peer: f"{peer}_think" for peer in others}}
        graph.add_conditional_edges(f"{name}_think", make_swarm_router(config, others), mapping)
        graph.add_edge(f"{name}_act", f"{name}_think")
    graph.set_entry_point(f"{entry}_think")
    return graph.compile(checkpointer=checkpointer or MemorySaver())


class FanoutState(TypedDict):
    items: list[str]
    messages: Annotated[list[dict], operator.add]
    thread_id: str


def build_fanout_graph(*, config: AgentConfig, checkpointer: BaseCheckpointSaver | None = None):
    """Parallel fan-out / map-reduce: dispatches config's think node once per item
    in state["items"] — LangGraph's Pregel engine runs every Send target
    concurrently in the same superstep — then every result lands in
    state["messages"] via AgentState's reducer. This is the other half of "as
    complex as it can be": one specialist applied to N inputs at once, instead of
    N specialists applied to one input (build_supervisor_graph/build_swarm_graph).
    """

    def dispatch(state: FanoutState) -> list[Send]:
        return [
            Send("worker", {"messages": [{"role": "user", "content": item}], "thread_id": state["thread_id"]})
            for item in state["items"]
        ]

    graph = StateGraph(FanoutState)
    graph.add_node("worker", make_think_node(config))
    graph.set_conditional_entry_point(dispatch, ["worker"])
    graph.add_edge("worker", END)
    return graph.compile(checkpointer=checkpointer or MemorySaver())


class BlackboardState(TypedDict):
    messages: Annotated[list[dict], operator.add]
    thread_id: str
    round: int


def build_blackboard_graph(*, agents: dict[str, AgentConfig], blackboard: Blackboard, rounds: int = 2, checkpointer: BaseCheckpointSaver | None = None):
    """Shared-workspace multi-agent: every agent, every round, sees the same
    Blackboard (facts/hypotheses/evidence/tasks/contradictions/open questions) and
    contributes with `POST <section>: <text>`, instead of talking to each other
    directly. Runs `rounds` full passes, sequentially per round (a shared,
    mutable Blackboard isn't given concurrent writers within a round)."""

    def collaborate(state: BlackboardState) -> dict:
        for name, config in agents.items():
            prompt = (
                f"{config.system_prompt}\n\nShared blackboard:\n{blackboard.render()}"
                "\n\nContribute with exactly: POST <fact|hypothesis|evidence|task|contradiction|question>: <text>"
            )
            with config.tracer.span("orchestration.blackboard", agent=name) as span:
                resp = config.llm.complete([{"role": "system", "content": prompt}, *state["messages"]], task=config.task)
                config.budget.spend(resp.cost_usd, thread_id=state.get("thread_id") or config.tracer.thread_id)
                span["attributes"].update(cost_usd=resp.cost_usd)
            parsed = parse_post(resp.text)
            if parsed:
                blackboard.post(*parsed)
                config.eval_harness.record("component", name, "posted", 1.0, section=parsed[0])
            else:
                config.eval_harness.record("component", name, "posted", 0.0)
        return {"round": state["round"] + 1}

    def more_rounds(state: BlackboardState) -> str:
        return "collaborate" if state["round"] < rounds else END

    graph = StateGraph(BlackboardState)
    graph.add_node("collaborate", collaborate)
    graph.set_entry_point("collaborate")
    graph.add_conditional_edges("collaborate", more_rounds, {"collaborate": "collaborate", END: END})
    return graph.compile(checkpointer=checkpointer or MemorySaver())


def build_debate_graph(*, debaters: dict[str, AgentConfig], judge: AgentConfig, checkpointer: BaseCheckpointSaver | None = None):
    """N debaters answer independently; the judge (AgentRole.VERIFIER by
    convention) reviews every answer and picks or synthesizes the final one."""

    def debate(state: AgentState) -> dict:
        results = []
        session_id = state.get("thread_id")
        for name, config in debaters.items():
            with config.tracer.span("orchestration.debate", agent=name) as span:
                resp = config.llm.complete([{"role": "system", "content": config.system_prompt}, *state["messages"]], task=config.task)
                config.budget.spend(resp.cost_usd, thread_id=session_id or config.tracer.thread_id)
                span["attributes"].update(cost_usd=resp.cost_usd)
            results.append((name, resp.text))
            config.eval_harness.record("component", name, "answered", 1.0)
        return {"messages": [{"role": "assistant", "content": f"[{name}] {text}"} for name, text in results]}

    def judge_node(state: AgentState) -> dict:
        session_id = state.get("thread_id") or judge.tracer.thread_id
        transcript = "\n".join(f"- {m['content']}" for m in state["messages"] if m["role"] == "assistant")
        prompt = f"{judge.system_prompt}\n\nCandidate answers:\n{transcript}\n\nReply with the single best final answer."
        with judge.tracer.span("orchestration.judge") as span:
            resp = judge.llm.complete([{"role": "system", "content": prompt}, *state["messages"]], task=judge.task)
            judge.budget.spend(resp.cost_usd, thread_id=session_id)
            span["attributes"].update(cost_usd=resp.cost_usd)
        judge.eval_harness.record("flow", session_id, "judged", 1.0)
        return {"messages": [{"role": "assistant", "content": resp.text}]}

    graph = StateGraph(AgentState)
    graph.add_node("debate", debate)
    graph.add_node("judge", judge_node)
    graph.set_entry_point("debate")
    graph.add_edge("debate", "judge")
    graph.add_edge("judge", END)
    return graph.compile(checkpointer=checkpointer or MemorySaver())


@dataclass
class DAGStep:
    """One deterministic step in a static workflow — a plain function over prior
    steps' results, no LLM reasoning required. `fn` receives the accumulated
    results dict and returns this step's own result."""
    name: str
    fn: Callable[[dict[str, Any]], Any]
    depends_on: tuple[str, ...] = ()


def build_dag_graph(steps: list[DAGStep], *, checkpointer: BaseCheckpointSaver | None = None):
    """A fixed DAG of deterministic steps — the non-agentic pattern, for use cases
    that need a reliable pipeline (fetch -> validate -> transform -> notify) rather
    than judgment at every hop. Independent steps (no shared dependency) run in
    parallel automatically, same as build_fanout_graph's Send-based concurrency."""

    class DAGState(TypedDict):
        results: Annotated[dict[str, Any], operator.or_]

    def make_step(step: DAGStep) -> Callable[[DAGState], dict]:
        def run(state: DAGState) -> dict:
            for dep in step.depends_on:
                if dep not in state["results"]:
                    raise RuntimeError(f"{step.name!r} depends on {dep!r}, which hasn't run yet")
            return {"results": {step.name: step.fn(state["results"])}}
        return run

    depended_on = {dep for step in steps for dep in step.depends_on}
    graph = StateGraph(DAGState)
    for step in steps:
        graph.add_node(step.name, make_step(step))
        if not step.depends_on:
            graph.add_edge(START, step.name)
        for dep in step.depends_on:
            graph.add_edge(dep, step.name)
        if step.name not in depended_on:
            graph.add_edge(step.name, END)
    return graph.compile(checkpointer=checkpointer or MemorySaver())


def agent_as_tool(*, name: str, description: str, graph: Any, thread_prefix: str | None = None):
    """'Agents as Tools': wraps any compiled graph (from any build_*_graph — a
    whole supervisor, a debate, a DAG) as a single ToolSpec, so a calling agent
    stays in control and just gets a result back — unlike build_supervisor_graph
    (hands off the whole turn) or build_swarm_graph (hands off peer-to-peer),
    here the sub-agent is invoked once, synchronously, like any other tool."""
    import uuid

    prefix = thread_prefix or name

    def call(query: str) -> str:
        thread_id = f"{prefix}-{uuid.uuid4().hex[:8]}"
        result = graph.invoke(
            {"messages": [{"role": "user", "content": query}], "thread_id": thread_id},
            {"configurable": {"thread_id": thread_id}},
        )
        return result["messages"][-1]["content"]

    return ToolSpec(name=name, description=description, parameters={"query": "string"}, fn=call)


def _parse_tool_call(content: str) -> tuple[str | None, dict]:
    if not isinstance(content, str) or not content.startswith("CALL "):
        return None, {}
    name, _, raw_args = content[len("CALL "):].partition(" ")
    return name, (json.loads(raw_args) if raw_args else {})


def _get_all_tool_calls(message: dict) -> list[tuple[str, dict, str | None]]:
    """EVERY tool call in one assistant message, not just the first — real
    provider-native tool-calling (message["tool_calls"]) can and does return
    more than one tool_use block in a single response (found live: a real
    Claude Sonnet 5 turn calling two tools at once). Each one needs its own
    tool_result in the very next message, or Anthropic's API rejects any
    later request built from that history outright ('each tool_use block
    must have a corresponding tool_result block') — silently dropping all
    but the first call here used to corrupt a session's conversation state
    permanently after its very first multi-tool-call turn. Falls back to
    the single CALL <tool> {json} text convention for providers that don't
    return native tool calls (tool_call_id is None there — no provider-
    issued id to correlate a result against)."""
    native = message.get("tool_calls")
    if native:
        return [(tc["name"], tc["args"], tc["id"]) for tc in native]
    name, args = _parse_tool_call(message.get("content"))
    return [(name, args, None)] if name else []


def _tool_param_names(spec: ToolSpec) -> set[str]:
    """Every parameter name a ToolSpec declares, whether `.parameters` is
    the loose {name: type} shorthand or a full JSON Schema object (see
    tools_gateway.tool_json_schema, which accepts both) — used by
    make_act_node to detect a `session_id` parameter and auto-inject it."""
    params = spec.parameters
    if params.get("type") == "object":
        return set(params.get("properties", {}))
    return set(params)

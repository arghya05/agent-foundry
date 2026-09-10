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

Tool-calling: real provider-native structured tool calling (Anthropic/OpenAI
tool_use blocks, translated to/from the provider-agnostic ToolCall shape in
llm_gateway.py) is the primary path — see _get_all_tool_calls, which reads
LLMResponse.tool_calls first. The `CALL <tool_name> {"arg": "value"}` text
convention is the fallback for a Provider with no native tool-calling at
all (or a test's ScriptedProvider), parsed by the same _get_all_tool_calls
only when a response carries no native tool_calls.

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

import asyncio
import inspect
import json
import operator
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt

from .blackboard import Blackboard, parse_post
from .context import ContextEngine, MemoryStore
from .contracts import AgentRole, Identity, Policy, ToolResult, ToolSpec
from .eval import EvalHarness
from .guardrails import GuardrailEngine, screen_tool_output
from .kpi import KPI
from .llm_gateway import LLMGateway
from .observability import CostLedgerLike, Tracer
from .runtime import (
    BudgetExceeded,
    CircuitBreaker,
    CircuitBreakerLike,
    LatencyBudgetLike,
    RunBudgetLike,
    RunCancelled,
    SLATrackerLike,
    with_timeout,
)
from .policy_engine import PolicyDecisionPoint
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
    # The AUTHENTICATED caller for this turn, when Agent.run()/.stream() was
    # given an ExecutionContext with user_id/tenant_id/permissions set (see
    # core/agent.py's _request_identity_dict) — {"id", "tenant_id", "roles"}.
    # Read via _resolve_identity(config, state), which falls back to
    # config.identity (the AGENT's own static, graph-build-time identity)
    # when absent. MUST be declared here, not just written/read via
    # state.get(...) — LangGraph derives its recognized channels from this
    # TypedDict's own fields, so an undeclared key in an invoke() input dict
    # is silently dropped rather than reaching any node (found live: this
    # exact omission made the LangGraph engine ignore a per-request identity
    # that native_engine's plain-dict state had no trouble carrying).
    request_identity: dict[str, Any] | None
    # The rest of ExecutionContext.to_request_state()'s keys — same
    # "declared here or LangGraph silently drops it" requirement as
    # request_identity above, and same per-turn semantics (only present
    # when a caller actually set that ExecutionContext field on this
    # specific call; absent otherwise, so a continuing thread's earlier
    # turn isn't reset to "no override"). See each resolver function
    # (_resolve_budget, _check_deadline, _check_cancellation,
    # _resolve_tool_policy, _resolve_model_names, _memory_key) below for
    # how each is actually used.
    request_trace_id: str | None
    request_budget: RunBudgetLike | None
    request_deadline: float | None
    request_cancellation_token: Any  # a core.execution_context.CancellationToken; typed loosely here to avoid orchestration.py importing core/ (core/ already imports orchestration.py)
    request_tool_policy: Policy | None
    request_model_policy: dict[str, Any] | None
    request_memory_scope: str | None


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
    # The mandatory Policy Decision Point (policy_engine.PolicyDecisionPoint)
    # every tool-call decision goes through — make_act_node/native_engine._act
    # ALWAYS call config.pdp.decide(...), never config.guardrails.check_action(
    # ...) directly, so this can't be silently bypassed the way an optional
    # field could. Leave unset and __post_init__ builds a PDP that wraps THIS
    # config's own guardrails with no external PolicyEngine/EgressPolicy —
    # behaviorally identical to the old check_action-only gate for anyone who
    # doesn't configure OPA/Cedar/egress; pass one explicitly to compose those in.
    pdp: PolicyDecisionPoint | None = None

    def __post_init__(self) -> None:
        if self.pdp is None:
            self.pdp = PolicyDecisionPoint(guardrails=self.guardrails)


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
        _check_cancellation(state)
        _check_deadline(state, session_id)
        budget = _resolve_budget(config, state)
        budget.step(thread_id=session_id)
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
            # tenant_id/roles come from the per-REQUEST identity when one
            # was resolved (state["request_identity"] — see
            # _resolve_identity) so one shared ContextEngine serving many
            # enterprise tenants retrieves THIS caller's knowledge base,
            # not whichever tenant_id the ContextEngine happened to be
            # constructed with. tenant_id stays None (ContextEngine's own
            # configured default) when no per-request identity exists —
            # config.identity.tenant_id is the AGENT's own static service
            # identity, not necessarily the right override when there's no
            # actual per-request caller to reflect.
            request_identity = _resolve_identity(config, state)
            has_request_identity = state.get("request_identity") is not None
            built = config.context_engine.build(
                _memory_key(state, session_id), last_user["content"], roles=frozenset(request_identity.roles),
                tenant_id=request_identity.tenant_id if has_request_identity else None,
            )
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
            passages = [p for p in config.memory.semantic.search(_memory_key(state, session_id), last_user["content"]) if not looks_like_injection(p)]
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

        policy = _resolve_tool_policy(config, state)
        native_tools = config.tools.native_tools(policy)
        messages = [{"role": "system", "content": prompt}, *state["messages"]]
        task = config.task(state) if callable(config.task) else config.task
        models = _resolve_model_names(config, state, task)
        with config.tracer.span("orchestration.think") as span:
            call = (lambda: config.llm.complete(messages, task=task, tools=native_tools or None, models=models))
            resp = with_timeout(call, seconds=config.step_timeout_s) if config.step_timeout_s else call()
            budget.spend(resp.cost_usd, thread_id=session_id)
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


def _invoke_tool_call(
    config: AgentConfig, tool_name: str, args: dict, *, identity: Identity, policy: Policy, idem_key: str | None, timeout: float | None,
) -> ToolResult:
    """The actual (possibly async, possibly timed-out) execution for ONE
    already-gated tool call — split out from make_act_node/native_engine._act
    so both the single-call and the concurrent-dispatch path (_dispatch_tool_
    calls below) call the identical function. Bridges an `async def` tool
    into this synchronous call via ToolRegistry.ainvoke()+asyncio.run(): safe
    here because every call to this function — whether on the node's own
    thread or inside one of _dispatch_tool_calls's worker threads — owns a
    thread with no event loop of its own already running on it."""
    spec = config.tools.get(tool_name) if config.tools.has(tool_name) else None

    def call() -> ToolResult:
        if spec is not None and inspect.iscoroutinefunction(spec.fn):
            return asyncio.run(config.tools.ainvoke(tool_name, args, identity=identity, policy=policy, idempotency_key=idem_key))
        return config.tools.invoke(tool_name, args, identity=identity, policy=policy, idempotency_key=idem_key)

    # The span lives HERE (not around the dispatch loop below) so its
    # duration reflects this call's own execution time even when several
    # calls run concurrently on separate threads — Tracer.span's only shared
    # mutable state is a plain list append, safe under the GIL from any
    # thread. A raised PermissionDenied still exits the span normally (its
    # `finally` records duration without ok/latency_ms attrs, matching the
    # pre-concurrency behavior) and propagates to the caller.
    with config.tracer.span("orchestration.act", tool=tool_name) as span:
        result = with_timeout(call, seconds=timeout) if timeout else call()
        span["attributes"].update(ok=result.ok, latency_ms=result.latency_ms)
        return result


def _dispatch_tool_calls(
    config: AgentConfig, cleared: list[tuple[int, str, dict, str | None, float | None]], *, identity: Identity, policy: Policy,
) -> list[tuple[int, ToolResult | PermissionDenied]]:
    """Runs every cleared call's _invoke_tool_call concurrently when there's
    more than one, plain-sequentially otherwise (no thread-pool overhead for
    the common single-tool-call turn). `cleared` entries are (original
    index, tool_name, args, idem_key, timeout) — already past every gate
    (breaker/PDP/interrupt) make_act_node/native_engine._act run first, in
    order, on the calling thread only (interrupt() itself must never run
    inside a spawned worker thread). Returns (index, ToolResult-or-
    PermissionDenied) pairs in COMPLETION order, not `cleared` order —
    callers write into a pre-sized results list by index and do every
    breaker/audit/eval_harness/memory bookkeeping side effect themselves,
    sequentially, once every future is back. That bookkeeping is
    deliberately kept off these worker threads: CircuitBreaker's per-tool
    counters are a read-modify-write, not safe to mutate from two threads at
    once, unlike the plain list-index writes/appends everything else here
    does under the GIL."""
    if len(cleared) <= 1:
        out: list[tuple[int, ToolResult | PermissionDenied]] = []
        for i, tool_name, args, idem_key, timeout in cleared:
            try:
                out.append((i, _invoke_tool_call(config, tool_name, args, identity=identity, policy=policy, idem_key=idem_key, timeout=timeout)))
            except PermissionDenied as e:
                out.append((i, e))
        return out

    out = []
    with ThreadPoolExecutor(max_workers=len(cleared)) as pool:
        futures = {
            pool.submit(_invoke_tool_call, config, tool_name, args, identity=identity, policy=policy, idem_key=idem_key, timeout=timeout): i
            for i, tool_name, args, idem_key, timeout in cleared
        }
        for future in futures:
            i = futures[future]
            try:
                out.append((i, future.result()))
            except PermissionDenied as e:
                out.append((i, e))
    return out


def make_act_node(config: AgentConfig) -> Callable[[AgentState], dict]:
    def tool_msg(content: Any, *, tool_call_id: str | None, ok: bool = True) -> dict:
        msg: dict[str, Any] = {"role": "tool", "content": content}
        if tool_call_id is not None:
            msg["tool_call_id"] = tool_call_id
            msg["ok"] = ok
        return msg

    def act(state: AgentState) -> dict:
        session_id = state.get("thread_id") or config.tracer.thread_id
        _check_cancellation(state)
        identity = _resolve_identity(config, state)
        policy = _resolve_tool_policy(config, state)
        budget = _resolve_budget(config, state)
        resolved_user_id = (config.user_id(state) if callable(config.user_id) else config.user_id) if config.user_id is not None else None
        calls = _get_all_tool_calls(state["messages"][-1])
        if not calls:
            return {}

        # Computed ONCE for this whole turn, not re-read per call — the old
        # strictly-sequential loop let each call's PDP decision see prior
        # calls' spend; concurrent dispatch below can't offer that without
        # serializing the very thing it exists to parallelize. Deliberate
        # accuracy/latency tradeoff: the PRE-turn budget.step() check (top of
        # make_think_node) is unaffected, only this intra-turn PDP read moves
        # earlier.
        cost_so_far = budget.cost_usd_for(session_id)

        results: list[dict | None] = [None] * len(calls)
        cleared: list[tuple[int, str, dict, str | None, float | None]] = []
        tool_names_by_index: dict[int, str] = {}
        call_ids_by_index: dict[int, str | None] = {}

        def flush_cleared() -> None:
            """Actually invokes (concurrently, when there's more than one)
            every cleared-but-not-yet-dispatched call, then does its
            bookkeeping — called both right before an approval-needing call's
            interrupt() (so, exactly like the old strictly-sequential loop,
            everything ordered BEFORE that call in `calls` has genuinely run
            by the time a human sees the approval prompt, not merely been
            gated) and once more after the loop for whatever's left."""
            for i, outcome in _dispatch_tool_calls(config, cleared, identity=identity, policy=policy):
                tool_name = tool_names_by_index[i]
                tool_call_id = call_ids_by_index[i]
                if isinstance(outcome, PermissionDenied):
                    config.eval_harness.record("component", tool_name, "permission", 0.0, reason=str(outcome), session_id=session_id)
                    results[i] = tool_msg(str(outcome), tool_call_id=tool_call_id, ok=False)
                    continue
                result = outcome
                config.breaker.record(tool_name, result.ok)
                config.audit.record(identity=identity, action="tool_call", tool=tool_name, ok=result.ok)
                config.eval_harness.record("component", tool_name, "success", 1.0 if result.ok else 0.0,
                                            session_id=session_id, **({"reason": result.error} if not result.ok else {}))
                if config.memory is not None and result.ok:
                    config.memory.working.setdefault(session_id, {}).setdefault("tool_sequence", []).append(tool_name)
                output = screen_tool_output(result.output) if result.ok else result.error
                results[i] = tool_msg(output, tool_call_id=tool_call_id, ok=result.ok)
            cleared.clear()

        for i, (tool_name, args, tool_call_id) in enumerate(calls):
            _check_cancellation(state)
            tool_names_by_index[i] = tool_name
            call_ids_by_index[i] = tool_call_id
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
                results[i] = tool_msg(f"{tool_name} temporarily disabled after repeated failures", tool_call_id=tool_call_id, ok=False)
                continue

            spec = config.tools.get(tool_name) if config.tools.has(tool_name) else None
            destructive = spec is not None and spec.destructive
            # Every tool call goes through the PDP, no bypass — AgentConfig.
            # __post_init__ guarantees config.pdp is never None.
            gr = config.pdp.decide(tool_name, args, identity=identity, policy=policy,
                                    destructive=destructive, cost_so_far=cost_so_far,
                                    hosts=spec.egress_hosts if spec is not None else frozenset(),
                                    scopes=spec.scopes if spec is not None else frozenset(),
                                    requires_confirmation=spec is not None and spec.requires_confirmation,
                                    data_classification=spec.data_classification if spec is not None else "internal")
            if not gr.allowed and gr.reason and "approval" in gr.reason:
                flush_cleared()  # everything gated before this call runs for real now, same ordering the old sequential loop gave
                decision = interrupt({"tool": tool_name, "args": args, "reason": gr.reason})
                config.audit.record(identity=identity, action="approval_decision", tool=tool_name, approved=bool(decision.get("approved")))
                if not decision.get("approved"):
                    config.eval_harness.record("component", tool_name, "approval", 0.0, reason="denied by reviewer", session_id=session_id)
                    results[i] = tool_msg(f"{tool_name} denied by reviewer", tool_call_id=tool_call_id, ok=False)
                    continue
                # approved: falls through to `cleared` below, same as any
                # other call — by construction, interrupt() itself already
                # ran (and only ever runs) on this node's own thread, never
                # inside _dispatch_tool_calls's pool.
            elif not gr.allowed:
                config.eval_harness.record("component", tool_name, "action_guardrail", 0.0, reason=gr.reason, session_id=session_id)
                results[i] = tool_msg(gr.reason, tool_call_id=tool_call_id, ok=False)
                continue

            idem_key = _default_idempotency_key(session_id, tool_call_id)
            timeout = _tool_timeout(config, spec)
            cleared.append((i, tool_name, args, idem_key, timeout))

        flush_cleared()
        return {"messages": results}

    return act


def _finalize_turn(config: AgentConfig, session_id: str, *, budget: RunBudgetLike, outcome: str = "completed") -> None:
    """Session-closing bookkeeping for a turn that's genuinely done — flow-
    completed eval record, cost ledger close, SLA tracker record, procedural-
    memory tool-sequence capture. Was inlined in make_router; hoisted out so
    make_critique_node can call the exact same bookkeeping once IT decides
    the turn is done (critique may resolve one or more steps after the
    router first saw a tool-call-free reply).

    `budget` is the resolved per-request-or-static budget this turn actually
    spent against (see _resolve_budget) — every caller passes
    _resolve_budget(config, state), not config.budget directly, so a
    per-request ExecutionContext.budget override is reflected in what
    CostLedger.close_task reports, not silently ignored by cost accounting.

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
            cost_usd=budget.cost_usd_for(session_id), steps=budget.steps_for(session_id), outcome=outcome,
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
            _finalize_turn(config, session_id, budget=_resolve_budget(config, state), outcome="needs_clarification")
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
            config.audit.record(identity=_resolve_identity(config, state), action="critique_review", tool=result.name, score=result.value, approved=approved)
            if not approved:
                update = {"messages": [{"role": "assistant", "content": config.critique.fallback_message}]}
            _finalize_turn(config, session_id, budget=_resolve_budget(config, state), outcome="completed_with_review")
        else:
            _finalize_turn(config, session_id, budget=_resolve_budget(config, state), outcome="completed" if result.passed else "completed_low_confidence")
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
            _resolve_budget(config, state).spend(resp.cost_usd, thread_id=session_id)
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
        _finalize_turn(config, session_id, budget=_resolve_budget(config, state))
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
    pdp: PolicyDecisionPoint | None = None,
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
        latency_budget=latency_budget, sla_tracker=sla_tracker, critique=critique, user_id=user_id, pdp=pdp,
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
        mapping = {"act": f"{name}_act", END: END}
        if config.critique is not None:
            # Same wiring build_agent_graph does for a single agent — without
            # it, make_router(config) can return "critique" for a specialist
            # that has one configured, and this mapping has no such key
            # (found live: an unhandled route crashes the graph at runtime).
            graph.add_node(f"{name}_critique", make_critique_node(config))
            if config.critique.max_retries > 0:
                graph.add_conditional_edges(f"{name}_critique", make_critique_router(config), {"think": f"{name}_think", END: END})
            else:
                graph.add_edge(f"{name}_critique", END)
            mapping["critique"] = f"{name}_critique"
        graph.add_conditional_edges(f"{name}_think", make_router(config), mapping)
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
        if config.critique is not None:
            # Same latent bug as build_supervisor_graph — make_swarm_router
            # falls back to make_router(config), which can return "critique".
            graph.add_node(f"{name}_critique", make_critique_node(config))
            if config.critique.max_retries > 0:
                graph.add_conditional_edges(f"{name}_critique", make_critique_router(config), {"think": f"{name}_think", END: END})
            else:
                graph.add_edge(f"{name}_critique", END)
            mapping["critique"] = f"{name}_critique"
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
    if config.critique is not None:
        # See the "critique deliberately NOT wired in" comment below — fail
        # loud at build time instead of a router returning an unmapped
        # "critique" label deep inside a LangGraph invoke() call.
        raise ValueError(
            "build_fanout_graph doesn't support AgentConfig.critique — concurrent "
            "Send-dispatched workers would race on AgentState's shared, non-Annotated "
            "critique_retries channel. Use build_agent_graph directly per item instead."
        )

    def dispatch(state: FanoutState) -> list[Send]:
        return [
            Send("worker", {"messages": [{"role": "user", "content": item}], "thread_id": state["thread_id"]})
            for item in state["items"]
        ]

    graph = StateGraph(FanoutState)
    graph.add_node("worker", make_think_node(config))
    graph.add_node("worker_act", make_act_node(config))
    graph.set_conditional_entry_point(dispatch, ["worker"])
    # Previously an unconditional worker -> END edge, so a tool call from a
    # fanned-out worker was never executed (make_router wasn't even called).
    # critique is deliberately NOT wired in here, unlike build_supervisor_graph/
    # build_swarm_graph above: AgentState.critique_retries is a plain
    # (non-Annotated, overwrite) channel, and every Send-dispatched worker
    # shares this one graph's state — concurrent workers in the same superstep
    # writing that field would hit LangGraph's "can received only one value per
    # step" error (see AgentState's own docstring). A fan-out worker with
    # critique configured should still use build_agent_graph directly instead.
    graph.add_conditional_edges("worker", make_router(config), {"act": "worker_act", END: END})
    graph.add_edge("worker_act", "worker")
    return graph.compile(checkpointer=checkpointer or MemorySaver())


def _run_governed_turn(config: AgentConfig, messages: list[dict], *, thread_id: str) -> str:
    """Runs `messages` (ending in a role="user" turn) through config's OWN
    governed single-agent graph (build_agent_graph) on a fresh, ephemeral
    graph instance — the guardrail/tool/critique-covered replacement for a
    bare config.llm.complete(...) call. Used by build_blackboard_graph and
    build_debate_graph below, neither of which used to run a specialist's
    turn through anything but a raw completion — no input/output guardrails,
    no tool exposure, no critique gate, unlike every other topology in this
    module. `thread_id` should be stable across repeated calls for the same
    agent (not re-randomized per call) so config.budget's per-thread cost/step
    ceiling actually accumulates across rounds instead of resetting each time
    — the graph object itself is rebuilt fresh each call (cheap: it's just
    node/edge wiring, no LLM call), only budget/tracing state is expected to
    persist, and it does, since config.budget/config.tracer are the same
    shared objects every call."""
    graph = build_agent_graph(
        system_prompt=config.system_prompt, llm=config.llm, tools=config.tools, guardrails=config.guardrails,
        eval_harness=config.eval_harness, identity=config.identity, policy=config.policy, budget=config.budget,
        tracer=config.tracer, task=config.task, audit=config.audit, breaker=config.breaker,
        cost_ledger=config.cost_ledger, memory=config.memory, context_engine=config.context_engine,
        step_timeout_s=config.step_timeout_s, latency_budget=config.latency_budget, sla_tracker=config.sla_tracker,
        critique=config.critique, user_id=config.user_id, pdp=config.pdp,
    )
    result = graph.invoke({"messages": list(messages), "thread_id": thread_id}, {"configurable": {"thread_id": thread_id}})
    return result["messages"][-1]["content"]


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
        parent_thread = state.get("thread_id") or "blackboard"
        for name, config in agents.items():
            prompt = (
                f"Shared blackboard:\n{blackboard.render()}"
                "\n\nContribute with exactly: POST <fact|hypothesis|evidence|task|contradiction|question>: <text>"
            )
            text = _run_governed_turn(config, [*state["messages"], {"role": "user", "content": prompt}], thread_id=f"{parent_thread}-{name}")
            parsed = parse_post(text)
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
        parent_thread = state.get("thread_id") or "debate"
        for name, config in debaters.items():
            text = _run_governed_turn(config, list(state["messages"]), thread_id=f"{parent_thread}-{name}")
            results.append((name, text))
            config.eval_harness.record("component", name, "answered", 1.0)
        return {"messages": [{"role": "assistant", "content": f"[{name}] {text}"} for name, text in results]}

    def judge_node(state: AgentState) -> dict:
        session_id = state.get("thread_id") or judge.tracer.thread_id
        transcript = "\n".join(f"- {m['content']}" for m in state["messages"] if m["role"] == "assistant")
        prompt = f"Candidate answers:\n{transcript}\n\nReply with the single best final answer."
        text = _run_governed_turn(judge, [{"role": "user", "content": prompt}], thread_id=f"{session_id}-judge")
        judge.eval_harness.record("flow", session_id, "judged", 1.0)
        return {"messages": [{"role": "assistant", "content": text}]}

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


def _default_idempotency_key(session_id: str, tool_call_id: str | None) -> str | None:
    """Auto-derives an idempotency key from the provider-issued tool_call_id
    (always present for real native tool-calling — see _get_all_tool_calls;
    None for the text "CALL <tool> {...}" convention fallback, which has no
    such id to correlate against) so a retried/replayed act() step protects a
    destructive tool call BY DEFAULT, not only when a caller explicitly
    passes idempotency_key= to ToolRegistry.invoke() themselves. Safe no-op
    when no idempotency_store is configured (ToolRegistry.invoke()'s own
    guard) — this is purely additive."""
    return f"{session_id}:{tool_call_id}" if tool_call_id else None


def _tool_timeout(config: AgentConfig, spec: ToolSpec | None) -> float | None:
    """A tool's own ToolSpec.timeout_s overrides AgentConfig.step_timeout_s
    when set — the per-tool ceiling the review's ToolSpec redesign asked
    for, without discarding the existing agent-wide default."""
    if spec is not None and spec.timeout_s is not None:
        return spec.timeout_s
    return config.step_timeout_s


def _resolve_identity(config: AgentConfig, state: dict) -> Identity:
    """The identity used for THIS turn's authorization (PDP), tool
    invocation (cache tenant-scoping, PermissionDenied), and audit
    decisions — prefers a per-request identity carried in state
    (state["request_identity"], seeded from the ExecutionContext an
    Agent.run()/.stream()/.resume() caller passed in — see core/agent.py's
    _identity_dict_from_context) over config.identity.

    Without this, EVERY caller of a shared Agent/graph instance is
    authorized as the exact same static, graph-build-time identity — an
    HTTP layer that correctly authenticates Alice (role=support_agent) and
    Bob (role=admin) as different people (see serve.py's AuthResolver) had
    no way to make that distinction reach the PDP's scope/data-
    classification checks, which only ever saw config.identity.roles.

    Falls back to config.identity when no per-request identity was set —
    unchanged behavior for every existing caller that doesn't populate
    ExecutionContext.user_id/tenant_id/permissions."""
    raw = state.get("request_identity")
    if raw is None:
        return config.identity
    return Identity(id=raw["id"], tenant_id=raw["tenant_id"], roles=tuple(raw.get("roles", ())))


def _resolve_budget(config: AgentConfig, state: dict) -> RunBudgetLike:
    """Per-request budget override — state["request_budget"], seeded from
    ExecutionContext.budget when a caller sets one (e.g. a per-customer
    spend cap tighter than this agent's own default) — else config.budget,
    unchanged behavior for every existing caller. Used everywhere
    config.budget/config.pdp's cost_so_far/CostLedger.close_task would
    otherwise read the static budget directly, so an override actually
    governs (and is reflected in) this run's real spend accounting."""
    return state.get("request_budget") or config.budget


def _check_deadline(state: dict, session_id: str) -> None:
    """Raises BudgetExceeded once ExecutionContext.deadline (an absolute
    unix timestamp) has passed. Composes with, doesn't replace,
    step_timeout_s/latency_budget — those bound a single step/the whole
    session; this bounds THIS run against the caller's own clock (e.g. an
    inbound HTTP request's own timeout)."""
    deadline = state.get("request_deadline")
    if deadline is not None and time.time() > deadline:
        raise BudgetExceeded(f"thread {session_id!r} passed its deadline ({deadline})")


def _check_cancellation(state: dict) -> None:
    """Raises RunCancelled once ExecutionContext.cancellation_token has been
    cancelled — see CancellationToken's own docstring for why this is
    cooperative, checked only at loop-safe points, not preemptive."""
    token = state.get("request_cancellation_token")
    if token is not None and token.is_cancelled():
        raise RunCancelled("run was cancelled")


def _resolve_tool_policy(config: AgentConfig, state: dict) -> Policy:
    """Per-request Policy override (ExecutionContext.tool_policy) — narrows,
    never widens, config.policy: allowed_tools intersects (a tool must be
    allowed by BOTH), requires_approval unions (an override can only ADD an
    approval requirement, never remove one config.policy already demands),
    max_cost_usd_per_thread/max_steps_per_thread/autonomy each take the
    stricter (lower) of the two. None (default): config.policy unchanged,
    existing behavior for every caller that doesn't set tool_policy."""
    override = state.get("request_tool_policy")
    if override is None:
        return config.policy
    base = config.policy
    return Policy(
        allowed_tools=base.allowed_tools & override.allowed_tools,
        max_cost_usd_per_thread=min(base.max_cost_usd_per_thread, override.max_cost_usd_per_thread),
        max_steps_per_thread=min(base.max_steps_per_thread, override.max_steps_per_thread),
        requires_approval=base.requires_approval | override.requires_approval,
        autonomy=min(base.autonomy, override.autonomy),
    )


def _resolve_model_names(config: AgentConfig, state: dict, task: str) -> list[str] | None:
    """Per-request model allowlist (ExecutionContext.model_policy =
    {"allowed_models": [...]}) — narrows, never widens, which of
    config.llm.routes[task]'s models this call may use. Deliberately scoped
    to model selection only, not a duplicate cost ceiling — total spend is
    already governed by _resolve_budget/RunBudget, a second, weaker,
    after-the-fact cost check here would just be redundant. Returns None
    (config.llm's own routing, unchanged) when no override, or when the
    override and the route share no model in common."""
    override = state.get("request_model_policy")
    if not override or not override.get("allowed_models"):
        return None
    base_route = config.llm.routes.get(task, config.llm.routes["default"])
    narrowed = [m for m in base_route if m in override["allowed_models"]]
    return narrowed or None


def _memory_key(state: dict, session_id: str) -> str:
    """Overrides the key used for THIS call's semantic/RAG memory reads
    (ExecutionContext.memory_scope) — e.g. sharing retrieved context across
    two otherwise-separate threads. Deliberately NOT used for the
    working-memory scratch keys (turn_start_ts, tool_sequence) or cost/
    audit, which stay thread-scoped — those are internal per-turn
    bookkeeping keyed consistently by session_id at both their write and
    read sites, not user-facing "memory" in the sense memory_scope means."""
    return state.get("request_memory_scope") or session_id

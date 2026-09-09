"""Core — NativeEngine: a second WorkflowEngine implementation with zero
LangGraph dependency. Proves core.protocols.WorkflowEngine is a real seam,
not a LangGraph-only abstraction: the exact same think/act/critique loop as
orchestration.py's make_think_node/make_act_node/make_critique_node, written
as plain Python control flow (a while loop with early returns) instead of a
LangGraph StateGraph. No StateGraph, no interrupt(), no Send, no checkpointer
— human-in-the-loop pause/resume is a plain dict flag this module manages
itself.

Deliberately reuses orchestration.py's private helpers (`_finalize_turn`,
`_get_all_tool_calls`, `_tool_param_names`, `CLARIFY_PREFIX`) rather than
re-deriving them — they're pure, framework-agnostic functions already, and
duplicating them would just be two copies to keep in sync (see, e.g.,
`_get_all_tool_calls`'s own docstring about the multi-tool-call bug it fixes
— a fresh reimplementation could easily reintroduce exactly that bug).

State is one plain dict per thread_id, shaped exactly like orchestration.py's
AgentState (`messages`/`thread_id`/`critique_retries`/`critique_last_score`)
minus LangGraph's `Annotated[..., operator.add]` reducer machinery — this
engine mutates it directly instead. Held in-process only, same MemorySaver-
equivalent default posture as every build_*_graph (no restart durability).

Scope, honestly: covers the single-agent react loop only (matching
`Agent(workflow="react", runtime="native")`) — not self_verify, not the
multi-agent topologies (Workflow.supervisor/.swarm/.blackboard/.debate/
.fanout/.dag stay LangGraph-only). Multiple *simultaneously* pending
approval-needing tool calls within one turn are resolved one at a time per
`.resume()` call (and re-run any earlier calls in that same turn again on
each resume, exactly mirroring LangGraph's own re-run-the-whole-node-on-
resume semantics — see ToolRegistry's `idempotency_store` for why that's a
known, pre-existing property of this codebase's HITL model, not something
new here); LangGraph's engine supports a queue of resume values across
several sequential interrupts in one turn, which this one doesn't reproduce.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Iterator

from ..guardrails import screen_tool_output
from ..orchestration import (
    CLARIFY_PREFIX, AgentConfig, _default_idempotency_key, _finalize_turn, _get_all_tool_calls, _tool_param_names,
    _tool_timeout,
)
from ..runtime import with_timeout
from ..tools_gateway import PermissionDenied


@dataclass
class _Interrupt:
    """Matches LangGraph's own interrupt object shape closely enough that
    `result.raw["__interrupt__"][0].value` works identically regardless of
    which engine produced `result` — see test_orchestration.py's own
    `result["__interrupt__"][0].value` usage for the shape being matched."""

    value: dict[str, Any]


def _tool_msg(content: Any, *, tool_call_id: str | None, ok: bool = True) -> dict[str, Any]:
    msg: dict[str, Any] = {"role": "tool", "content": content}
    if tool_call_id is not None:
        msg["tool_call_id"] = tool_call_id
        msg["ok"] = ok
    return msg


class NativeEngine:
    """Runs one AgentConfig's think/act/critique loop per thread_id, entirely
    in raw Python. `run`/`resume` return a plain dict shaped exactly like a
    LangGraph compiled graph's own `.invoke()` result
    (`{"messages": [...], "thread_id": ..., "__interrupt__": [...]}`), so
    `core.agent.result_from_graph_output` — and everything built on it —
    needs no changes to work with this engine instead.

    Thread-safe: `run`/`resume`/`get_state`/`update_state` on the SAME
    thread_id are serialized via a per-thread lock (two concurrent requests
    racing on one conversation would otherwise interleave appends to the
    same `messages` list) — a genuine, previously-unguarded race, not a
    hypothetical one, since this engine (unlike LangGraph's checkpointer) is
    a plain in-process dict. Different thread_ids never block each other."""

    def __init__(self) -> None:
        self._threads: dict[str, dict[str, Any]] = {}
        self._threads_lock = threading.Lock()  # guards creation of the per-thread locks/entries below, not full turns
        self._thread_locks: dict[str, threading.Lock] = {}

    def _lock_for(self, thread_id: str) -> threading.Lock:
        with self._threads_lock:
            return self._thread_locks.setdefault(thread_id, threading.Lock())

    def _state_for(self, thread_id: str) -> dict[str, Any]:
        with self._threads_lock:
            return self._threads.setdefault(
                thread_id, {"messages": [], "thread_id": thread_id, "critique_retries": 0, "critique_last_score": None, "_pending": None},
            )

    def run(self, config: AgentConfig, message: str, *, thread_id: str) -> dict[str, Any]:
        with self._lock_for(thread_id):
            state = self._state_for(thread_id)
            state["messages"].append({"role": "user", "content": message})
            return self._drive(config, state, thread_id)

    def resume(self, config: AgentConfig, *, approved: bool, decision: dict[str, Any] | None = None, thread_id: str) -> dict[str, Any]:
        # `decision` is accepted for signature parity with the LangGraph
        # engine's WorkflowEngine.resume (core/engines.py) — like
        # orchestration.py's own make_act_node/make_critique_node, the
        # built-in resume logic here only ever reads `approved` off it;
        # extra keys are for a caller's OWN downstream use, not consumed
        # by this fixed node logic on either engine.
        with self._lock_for(thread_id):
            return self._resume_locked(config, approved=approved, thread_id=thread_id)

    def _resume_locked(self, config: AgentConfig, *, approved: bool, thread_id: str) -> dict[str, Any]:
        state = self._state_for(thread_id)
        pending = state.get("_pending")
        if pending is None:
            raise RuntimeError(f"thread {thread_id!r} has nothing pending to resume")
        state["_pending"] = None

        if pending["kind"] == "tool":
            resume_decision = (pending["tool_name"], pending["tool_call_id"], approved)
            act_raw = self._act(config, state, thread_id, resume=resume_decision)
            if act_raw is not None:
                return act_raw  # a further approval-needing call in this same turn paused again
            return self._drive(config, state, thread_id)  # act finished clean -> loop continues from think

        # critique escalation (pending["kind"] == "critique")
        if not approved:
            state["messages"][-1] = {"role": "assistant", "content": config.critique.fallback_message}
        _finalize_turn(config, thread_id, outcome="completed_with_review")
        return self._raw(state)

    # ---- the loop -----------------------------------------------------------

    def _drive(self, config: AgentConfig, state: dict[str, Any], thread_id: str) -> dict[str, Any]:
        while True:
            self._think(config, state, thread_id)
            last = state["messages"][-1]
            if _get_all_tool_calls(last):
                act_raw = self._act(config, state, thread_id)
                if act_raw is not None:
                    return act_raw  # paused for tool approval
                continue  # act -> think

            if config.critique is not None:
                critique_raw = self._critique(config, state, thread_id)
                if critique_raw is not None:
                    return critique_raw  # clarify / escalate-paused / finalized
                continue  # critique retry -> think

            _finalize_turn(config, thread_id, outcome="completed")
            return self._raw(state)

    def _raw(self, state: dict[str, Any], *, interrupt: dict[str, Any] | None = None) -> dict[str, Any]:
        raw: dict[str, Any] = {"messages": list(state["messages"]), "thread_id": state["thread_id"]}
        if interrupt is not None:
            raw["__interrupt__"] = [_Interrupt(interrupt)]
        return raw

    # ---- think ---------------------------------------------------------------

    def _think(self, config: AgentConfig, state: dict[str, Any], thread_id: str) -> None:
        session_id = thread_id
        config.budget.step(thread_id=session_id)
        if config.latency_budget is not None:
            config.latency_budget.check(thread_id=session_id)

        last_message = state["messages"][-1]
        is_fresh_turn = last_message["role"] == "user" and not last_message.get("_critique_retry")
        if config.critique is not None and is_fresh_turn:
            state["critique_retries"] = 0
            state["critique_last_score"] = None

        if config.memory is not None and last_message["role"] == "user":
            config.memory.working.setdefault(session_id, {})["turn_start_ts"] = time.time()

        last_user = next((m for m in reversed(state["messages"]) if m["role"] == "user"), None)
        if last_user is not None:
            gr_in = config.guardrails.check_input(last_user["content"])
            if not gr_in.allowed:
                config.eval_harness.record("atomic", "input_guardrail", "blocked", 0.0, reason=gr_in.reason, session_id=session_id)
                state["messages"].append({"role": "assistant", "content": "I can't process that request."})
                return

        prompt = config.system_prompt
        if config.context_engine is not None and last_user is not None:
            built = config.context_engine.build(session_id, last_user["content"])
            if built:
                prompt = config.system_prompt + "\n\nRelevant context:\n" + built
        elif config.memory is not None and last_user is not None:
            from ..guardrails import looks_like_injection

            passages = [p for p in config.memory.semantic.search(session_id, last_user["content"]) if not looks_like_injection(p)]
            if passages:
                prompt = config.system_prompt + "\n\nRelevant context:\n" + "\n".join(f"- {p}" for p in passages)

        if config.memory is not None and config.user_id is not None:
            resolved_user_id = config.user_id(state) if callable(config.user_id) else config.user_id
            profile = config.memory.get_profile(resolved_user_id)
            if profile:
                prompt = prompt + "\n\nWhat you already know about this user (persists across sessions):\n" + "\n".join(f"- {k}: {v}" for k, v in profile.items())

        native_tools = config.tools.native_tools(config.policy)
        messages = [{"role": "system", "content": prompt}, *state["messages"]]
        task = config.task(state) if callable(config.task) else config.task
        with config.tracer.span("native.think") as span:
            call = lambda: config.llm.complete(messages, task=task, tools=native_tools or None)
            resp = with_timeout(call, seconds=config.step_timeout_s) if config.step_timeout_s else call()
            config.budget.spend(resp.cost_usd, thread_id=session_id)
            span["attributes"].update(cost_usd=resp.cost_usd, model=resp.model, native_tool_calls=len(resp.tool_calls), task=task)
        config.eval_harness.record("atomic", "think", "responded", 1.0, model=resp.model, task=task, session_id=session_id)

        gr_out = config.guardrails.check_output(resp.text)
        if not gr_out.allowed:
            config.eval_harness.record("atomic", "output_guardrail", "blocked", 0.0, reason=gr_out.reason, session_id=session_id)
            state["messages"].append({"role": "assistant", "content": "I can't share that — it looks like it contains sensitive data."})
            return

        if resp.tool_calls:
            state["messages"].append({
                "role": "assistant", "content": resp.text,
                "tool_calls": [{"id": tc.id, "name": tc.name, "args": tc.args} for tc in resp.tool_calls],
            })
        else:
            state["messages"].append({"role": "assistant", "content": resp.text})

    # ---- act -------------------------------------------------------------------

    def _act(
        self, config: AgentConfig, state: dict[str, Any], thread_id: str, *, resume: tuple[str, str | None, bool] | None = None,
    ) -> dict[str, Any] | None:
        session_id = thread_id
        resolved_user_id = (config.user_id(state) if callable(config.user_id) else config.user_id) if config.user_id is not None else None
        calls = _get_all_tool_calls(state["messages"][-1])
        if not calls:
            return None

        results: list[dict[str, Any]] = []
        for tool_name, args, tool_call_id in calls:
            if config.tools.has(tool_name):
                param_names = _tool_param_names(config.tools.get(tool_name))
                if "session_id" in param_names:
                    args = {**args, "session_id": session_id}
                if "user_id" in param_names and resolved_user_id is not None:
                    args = {**args, "user_id": resolved_user_id}
            if config.breaker.is_open(tool_name):
                results.append(_tool_msg(f"{tool_name} temporarily disabled after repeated failures", tool_call_id=tool_call_id, ok=False))
                continue

            spec = config.tools.get(tool_name) if config.tools.has(tool_name) else None
            destructive = spec is not None and spec.destructive
            if config.pdp is not None:
                gr = config.pdp.decide(tool_name, args, identity=config.identity, policy=config.policy,
                                        destructive=destructive, cost_so_far=config.budget.cost_usd_for(session_id))
            else:
                gr = config.guardrails.check_action(tool_name, cost_so_far=config.budget.cost_usd_for(session_id), destructive=destructive)
            needs_approval = not gr.allowed and bool(gr.reason) and "approval" in gr.reason
            if needs_approval:
                already_decided = resume is not None and resume[0] == tool_name and resume[1] == tool_call_id
                if not already_decided:
                    pending = {"kind": "tool", "tool": tool_name, "tool_name": tool_name, "args": args, "tool_call_id": tool_call_id, "reason": gr.reason}
                    state["_pending"] = pending
                    return self._raw(state, interrupt=pending)
                approved = resume[2]
                config.audit.record(identity=config.identity, action="approval_decision", tool=tool_name, approved=approved)
                if not approved:
                    config.eval_harness.record("component", tool_name, "approval", 0.0, reason="denied by reviewer", session_id=session_id)
                    results.append(_tool_msg(f"{tool_name} denied by reviewer", tool_call_id=tool_call_id, ok=False))
                    continue
            elif not gr.allowed:
                config.eval_harness.record("component", tool_name, "action_guardrail", 0.0, reason=gr.reason, session_id=session_id)
                results.append(_tool_msg(gr.reason, tool_call_id=tool_call_id, ok=False))
                continue

            with config.tracer.span("native.act", tool=tool_name) as span:
                try:
                    idem_key = _default_idempotency_key(session_id, tool_call_id)
                    timeout = _tool_timeout(config, spec)
                    invoke = lambda tn=tool_name, a=args, k=idem_key: config.tools.invoke(tn, a, identity=config.identity, policy=config.policy, idempotency_key=k)
                    result = with_timeout(invoke, seconds=timeout) if timeout else invoke()
                except PermissionDenied as e:
                    config.eval_harness.record("component", tool_name, "permission", 0.0, reason=str(e), session_id=session_id)
                    results.append(_tool_msg(str(e), tool_call_id=tool_call_id, ok=False))
                    continue
                span["attributes"].update(ok=result.ok, latency_ms=result.latency_ms)

            config.breaker.record(tool_name, result.ok)
            config.audit.record(identity=config.identity, action="tool_call", tool=tool_name, ok=result.ok)
            config.eval_harness.record("component", tool_name, "success", 1.0 if result.ok else 0.0,
                                        session_id=session_id, **({"reason": result.error} if not result.ok else {}))
            if config.memory is not None and result.ok:
                config.memory.working.setdefault(session_id, {}).setdefault("tool_sequence", []).append(tool_name)
            output = screen_tool_output(result.output) if result.ok else result.error
            results.append(_tool_msg(output, tool_call_id=tool_call_id, ok=result.ok))

        state["messages"].extend(results)
        return None

    # ---- critique --------------------------------------------------------------

    def _critique(self, config: AgentConfig, state: dict[str, Any], thread_id: str) -> dict[str, Any] | None:
        assert config.critique is not None
        session_id = thread_id
        draft = state["messages"][-1]["content"]

        if isinstance(draft, str) and draft.strip().startswith(CLARIFY_PREFIX):
            question = draft.strip()[len(CLARIFY_PREFIX):].strip()
            config.eval_harness.record("atomic", "critique", "clarification_requested", 1.0,
                                        reason="model asked the user a clarifying question instead of guessing", session_id=session_id)
            state["messages"].append({"role": "assistant", "content": question})
            _finalize_turn(config, session_id, outcome="needs_clarification")
            return self._raw(state)

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
            state["messages"].append({"role": "user", "content": retry_msg, "_critique_retry": True})
            state["critique_retries"] = retries_done + 1
            state["critique_last_score"] = result.value
            return None

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
            reason = (
                f"{result.value:.2f} below threshold {threshold_str} — improved over {retries_done} retries (from {prev_score:.2f}), flagged low-confidence"
                if prev_score is not None else f"{result.value:.2f} below threshold {threshold_str} — answered, flagged low-confidence"
            )
        elif not result.passed:
            reason = f"{result.value:.2f} below threshold {threshold_str} — answered, flagged low-confidence"
        else:
            reason = f"{result.value:.2f} at or above threshold {threshold_str} — passed"
        config.eval_harness.record("atomic", "critique", result.name, result.value, reason=reason, session_id=session_id)

        if escalate:
            pending = {
                "kind": "critique", "reason": "low_confidence", "kpi": result.name, "score": result.value,
                "threshold": config.critique.escalate_threshold, "draft_reply": draft,
            }
            state["_pending"] = pending
            return self._raw(state, interrupt=pending)

        _finalize_turn(config, session_id, outcome="completed" if result.passed else "completed_low_confidence")
        return self._raw(state)


class _NativeGraph:
    """Duck-types LangGraph's own compiled-graph interface
    (`.invoke(state_or_command, run_config)`, `.stream(...)`) closely enough
    that core.agent._CompiledWorkflow needs zero changes to run on this
    engine instead of LangGraph's — the same Agent.run/.stream/.resume/
    .batch/.as_tool/.serve, orchestration.agent_as_tool, batch.run_batch, and
    serve.build_http_app all work against this object unmodified. The only
    thing "resumed" is duck-typed too (`getattr(x, "resume", None)`, the
    attribute langgraph.types.Command(resume=...) happens to expose) — this
    module never imports langgraph."""

    def __init__(self, config: AgentConfig) -> None:
        self._config = config
        self._engine = NativeEngine()

    def invoke(self, state_or_command: Any, run_config: dict[str, Any]) -> dict[str, Any]:
        thread_id = run_config["configurable"]["thread_id"]
        resume_payload = getattr(state_or_command, "resume", None)
        if resume_payload is not None:
            decision = {k: v for k, v in resume_payload.items() if k != "approved"}
            return self._engine.resume(self._config, approved=bool(resume_payload.get("approved")), decision=decision or None, thread_id=thread_id)
        message = state_or_command["messages"][-1]["content"]
        return self._engine.run(self._config, message, thread_id=thread_id)

    def stream(self, state_or_command: Any, run_config: dict[str, Any], **kwargs: Any) -> Iterator[dict[str, Any]]:
        """One chunk only — this engine has no per-node incremental
        streaming, unlike LangGraph's real one. `**kwargs` accepts (and
        ignores) `stream_mode=` etc. so callers can pass the same kwargs to
        either engine uniformly (see core.agent._CompiledWorkflow.stream())."""
        yield self.invoke(state_or_command, run_config)

    def get_state(self, run_config: dict[str, Any]) -> "_StateSnapshot":
        """Matches LangGraph's own `compiled.get_state(config).values` shape
        closely enough for core.run.Run.fork()/.replay() to read either
        engine's per-thread state the same way. Lock-guarded against a
        concurrent run()/resume() on the same thread_id, same as those."""
        thread_id = run_config["configurable"]["thread_id"]
        with self._engine._lock_for(thread_id):
            return _StateSnapshot(values=dict(self._engine._state_for(thread_id)))

    def update_state(self, run_config: dict[str, Any], values: dict[str, Any]) -> None:
        """Unlike LangGraph's own update_state (which appends onto a
        reducer-typed `messages` channel — verified empirically, see
        core/run.py's module docstring), this engine's per-thread state is a
        plain dict with no reducer, so a plain assign IS the correct "set"
        semantics — safe both for seeding a brand-new thread (core.run.Run's
        fork()) and for any other value in `values`."""
        thread_id = run_config["configurable"]["thread_id"]
        with self._engine._lock_for(thread_id):
            state = self._engine._state_for(thread_id)
            state.update(values)


@dataclass
class _StateSnapshot:
    values: dict[str, Any]

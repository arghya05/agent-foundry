"""Core — native (LangGraph-free) counterparts of orchestration.py's 7
multi-agent topology builders. Single-agent already has one
(native_engine.NativeEngine); this module is the same idea applied to
supervisor/swarm/blackboard/debate/fanout/dag, so `Workflow.*` can offer
`runtime="native"` for all 7, not just plain `Agent`.

Every class here duck-types the same `.invoke(state, run_config)` (and
`.stream(state, run_config, **kwargs)`) calling convention LangGraph's own
compiled graphs use — the exact reason core.agent._CompiledWorkflow/
_FanoutWorkflow/_DagWorkflow need no changes at all to run against one of
these instead: they already only ever call `.invoke()`/`.stream()` on
whatever `self._graph` is, LangGraph-compiled or not (see engines.py's own
docstring for the established version of this same trick, one layer down).

`.stream()` on every class below yields exactly one chunk (the final
result) — real per-step incremental streaming exists for single-agent
(native_engine.stream_run), but reproducing it for 5 more topologies is out
of scope here; this is honest single-chunk parity, not a claim of full
step-level streaming.

Governed per-specialist turns (supervisor/swarm/blackboard/debate all need
one) go through native_run_governed_turn, the native counterpart of
orchestration._run_governed_turn — same reasoning: a bare llm.complete()
call would skip guardrails/tools/critique entirely.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterator

from ..blackboard import Blackboard, parse_post
from ..orchestration import AgentConfig, DAGStep
from ..llm_gateway import LLMGateway
from .native_engine import NativeEngine


def native_run_governed_turn(config: AgentConfig, messages: list[dict], *, thread_id: str) -> str:
    """Native counterpart of orchestration._run_governed_turn: runs `messages`
    (ending in a role="user" turn) through a fresh, ephemeral NativeEngine —
    ephemeral the same way _run_governed_turn's LangGraph graph is rebuilt
    fresh each call (cheap; ~2 dict entries here, not even node wiring),
    which is why `thread_id` need only be stable for config.budget/tracer
    continuity, not for message-history persistence — the caller always
    passes the full history explicitly."""
    engine = NativeEngine()
    state = {
        "messages": list(messages), "thread_id": thread_id,
        "critique_retries": 0, "critique_last_score": None, "_pending": None,
    }
    with engine._lock_for(thread_id):
        result = engine._drive(config, state, thread_id)
    return result["messages"][-1]["content"]


def _native_run_worker_messages(config: AgentConfig, item: str, *, thread_id: str) -> list[dict]:
    """Like native_run_governed_turn, but returns every message the turn
    PRODUCED (not just the final reply, and not the seed user message
    either) — build_fanout_graph's own Send(node, {"messages": [seed_user_
    msg], ...}) feeds that seed straight to the worker node as its local
    input, it's never itself merged into the parent graph's messages
    channel (only a node's OWN return value is); only what the worker's
    think/act loop actually appends ends up in the final flattened list
    _FanoutWorkflow.run() returns. Excluding index 0 (the seed) here matches
    that exactly."""
    engine = NativeEngine()
    state = {
        "messages": [{"role": "user", "content": item}], "thread_id": thread_id,
        "critique_retries": 0, "critique_last_score": None, "_pending": None,
    }
    with engine._lock_for(thread_id):
        result = engine._drive(config, state, thread_id)
    return result["messages"][1:]


class _NativeSupervisorGraph:
    """Native counterpart of build_supervisor_graph: one router llm.complete()
    call picks a specialist by name, then that specialist runs one full
    governed turn (native_run_governed_turn already handles that specialist's
    own think/act/critique loop to completion — no per-specialist node/edge
    wiring needed the way the LangGraph version requires)."""

    def __init__(self, *, supervisor_prompt: str, agents: dict[str, AgentConfig], llm: LLMGateway, task: str = "default") -> None:
        self._prompt = supervisor_prompt
        self._agents = agents
        self._llm = llm
        self._task = task
        self._threads: dict[str, list[dict]] = {}
        self._lock = threading.Lock()

    def _history_for(self, thread_id: str) -> list[dict]:
        with self._lock:
            return self._threads.setdefault(thread_id, [])

    def invoke(self, state: dict[str, Any], run_config: dict[str, Any]) -> dict[str, Any]:
        thread_id = run_config["configurable"]["thread_id"]
        history = self._history_for(thread_id)
        history.append(state["messages"][-1])
        options = ", ".join(self._agents)
        route_messages = [
            {"role": "system", "content": f"{self._prompt}\n\nReply with exactly: ROUTE <agent_name>\nAvailable agents: {options}"},
            *history,
        ]
        resp = self._llm.complete(route_messages, task=self._task)
        name = resp.text.strip().removeprefix("ROUTE ").strip()
        if name not in self._agents:
            name = next(iter(self._agents))  # unrecognized routing decision -> fall back, don't crash
        text = native_run_governed_turn(self._agents[name], list(history), thread_id=f"{thread_id}-{name}")
        history.append({"role": "assistant", "content": text})
        return {"messages": list(history), "thread_id": thread_id}

    def stream(self, state: dict[str, Any], run_config: dict[str, Any], **kwargs: Any) -> Iterator[dict[str, Any]]:
        yield self.invoke(state, run_config)


class _NativeSwarmGraph:
    """Native counterpart of build_swarm_graph: decentralized handoff — each
    specialist's full turn either ends normally or replies exactly
    `HANDOFF <peer>`, in which case the peer runs next. `max_handoffs` bounds
    a genuinely possible (not hypothetical) infinite loop between two
    specialists handing off to each other forever — the LangGraph graph has
    no such bound either, but a native in-process loop with no bound at all
    would just hang the calling thread."""

    def __init__(self, *, agents: dict[str, AgentConfig], entry: str, max_handoffs: int = 25) -> None:
        self._agents = agents
        self._entry = entry
        self._max_handoffs = max_handoffs
        self._threads: dict[str, list[dict]] = {}
        self._lock = threading.Lock()

    def _history_for(self, thread_id: str) -> list[dict]:
        with self._lock:
            return self._threads.setdefault(thread_id, [])

    def invoke(self, state: dict[str, Any], run_config: dict[str, Any]) -> dict[str, Any]:
        thread_id = run_config["configurable"]["thread_id"]
        history = self._history_for(thread_id)
        history.append(state["messages"][-1])
        name = self._entry
        for _ in range(self._max_handoffs):
            text = native_run_governed_turn(self._agents[name], list(history), thread_id=f"{thread_id}-{name}")
            history.append({"role": "assistant", "content": text})
            if isinstance(text, str) and text.startswith("HANDOFF "):
                target = text[len("HANDOFF "):].strip()
                if target in self._agents and target != name:
                    self._agents[name].eval_harness.record("component", "handoff", target, 1.0)
                    name = target
                    continue
            break
        return {"messages": list(history), "thread_id": thread_id}

    def stream(self, state: dict[str, Any], run_config: dict[str, Any], **kwargs: Any) -> Iterator[dict[str, Any]]:
        yield self.invoke(state, run_config)


class _NativeBlackboardGraph:
    """Native counterpart of build_blackboard_graph. Matches the LangGraph
    version's own (slightly surprising but deliberate) behavior exactly:
    agents' contributions land on the shared Blackboard object, NEVER in the
    returned `messages` — collaborate() only ever returns {"round": ...},
    no "messages" key — so this reads the workspace via `blackboard`, not
    via a conversational reply."""

    def __init__(self, *, agents: dict[str, AgentConfig], blackboard: Blackboard, rounds: int = 2) -> None:
        self._agents = agents
        self._blackboard = blackboard
        self._rounds = rounds
        self._threads: dict[str, list[dict]] = {}
        self._lock = threading.Lock()

    def _history_for(self, thread_id: str) -> list[dict]:
        with self._lock:
            return self._threads.setdefault(thread_id, [])

    def invoke(self, state: dict[str, Any], run_config: dict[str, Any]) -> dict[str, Any]:
        thread_id = run_config["configurable"]["thread_id"]
        history = self._history_for(thread_id)
        history.append(state["messages"][-1])
        for _ in range(self._rounds):
            for name, config in self._agents.items():
                prompt = (
                    f"Shared blackboard:\n{self._blackboard.render()}"
                    "\n\nContribute with exactly: POST <fact|hypothesis|evidence|task|contradiction|question>: <text>"
                )
                text = native_run_governed_turn(config, [*history, {"role": "user", "content": prompt}], thread_id=f"{thread_id}-{name}")
                parsed = parse_post(text)
                if parsed:
                    self._blackboard.post(*parsed)
                    config.eval_harness.record("component", name, "posted", 1.0, section=parsed[0])
                else:
                    config.eval_harness.record("component", name, "posted", 0.0)
        return {"messages": list(history), "thread_id": thread_id, "round": self._rounds}

    def stream(self, state: dict[str, Any], run_config: dict[str, Any], **kwargs: Any) -> Iterator[dict[str, Any]]:
        yield self.invoke(state, run_config)


class _NativeDebateGraph:
    """Native counterpart of build_debate_graph: N debaters answer
    independently (via native_run_governed_turn), then one judge call reviews
    the transcript and gives the final answer."""

    def __init__(self, *, debaters: dict[str, AgentConfig], judge: AgentConfig) -> None:
        self._debaters = debaters
        self._judge = judge
        self._threads: dict[str, list[dict]] = {}
        self._lock = threading.Lock()

    def _history_for(self, thread_id: str) -> list[dict]:
        with self._lock:
            return self._threads.setdefault(thread_id, [])

    def invoke(self, state: dict[str, Any], run_config: dict[str, Any]) -> dict[str, Any]:
        thread_id = run_config["configurable"]["thread_id"]
        history = self._history_for(thread_id)
        history.append(state["messages"][-1])
        debate_messages = []
        for name, config in self._debaters.items():
            text = native_run_governed_turn(config, list(history), thread_id=f"{thread_id}-{name}")
            debate_messages.append({"role": "assistant", "content": f"[{name}] {text}"})
            config.eval_harness.record("component", name, "answered", 1.0)
        history.extend(debate_messages)
        transcript = "\n".join(f"- {m['content']}" for m in history if m["role"] == "assistant")
        prompt = f"Candidate answers:\n{transcript}\n\nReply with the single best final answer."
        judge_text = native_run_governed_turn(self._judge, [{"role": "user", "content": prompt}], thread_id=f"{thread_id}-judge")
        self._judge.eval_harness.record("flow", thread_id, "judged", 1.0)
        history.append({"role": "assistant", "content": judge_text})
        return {"messages": list(history), "thread_id": thread_id}

    def stream(self, state: dict[str, Any], run_config: dict[str, Any], **kwargs: Any) -> Iterator[dict[str, Any]]:
        yield self.invoke(state, run_config)


class _NativeFanoutGraph:
    """Native counterpart of build_fanout_graph: dispatches config's think/act
    loop once per item, concurrently (via ThreadPoolExecutor — the sync-first
    equivalent of LangGraph's Send-based concurrent supersteps), and flattens
    every worker's full message history into one list, same as
    _FanoutWorkflow.run() already does with the LangGraph version's output.
    critique isn't supported here either, same race-condition reasoning as
    the LangGraph version (see build_fanout_graph's own docstring)."""

    def __init__(self, *, config: AgentConfig) -> None:
        if config.critique is not None:
            raise ValueError(
                "native fanout doesn't support AgentConfig.critique — concurrent "
                "workers would race on shared per-thread critique state, same as "
                "build_fanout_graph's own restriction. Use the single-agent runtime "
                "directly per item instead."
            )
        self._config = config

    def invoke(self, state: dict[str, Any], run_config: dict[str, Any]) -> dict[str, Any]:
        thread_id = run_config["configurable"]["thread_id"]
        items = state["items"]
        with ThreadPoolExecutor(max_workers=max(len(items), 1)) as pool:
            futures = [pool.submit(_native_run_worker_messages, self._config, item, thread_id=thread_id) for item in items]
            all_messages = [msg for future in futures for msg in future.result()]
        return {"messages": all_messages, "thread_id": thread_id, "items": items}

    def stream(self, state: dict[str, Any], run_config: dict[str, Any], **kwargs: Any) -> Iterator[dict[str, Any]]:
        yield self.invoke(state, run_config)


class _NativeDagGraph:
    """Native counterpart of build_dag_graph: steps with satisfied
    dependencies run concurrently in waves (ThreadPoolExecutor), same
    parallelism build_dag_graph gets for free from LangGraph scheduling any
    node whose incoming edges are all satisfied in one superstep. No LLM
    involved — DAGStep is already framework-agnostic, reused unchanged."""

    def __init__(self, steps: list[DAGStep]) -> None:
        self._steps = steps

    def invoke(self, state: dict[str, Any], run_config: dict[str, Any]) -> dict[str, Any]:
        results: dict[str, Any] = dict(state.get("results") or {})
        remaining = {step.name: step for step in self._steps if step.name not in results}
        while remaining:
            ready = [step for step in remaining.values() if all(dep in results for dep in step.depends_on)]
            if not ready:
                raise RuntimeError("DAG has unsatisfiable dependencies (a cycle, or a step depending on an unknown step)")
            with ThreadPoolExecutor(max_workers=len(ready)) as pool:
                futures = {pool.submit(step.fn, dict(results)): step.name for step in ready}
                computed = {futures[future]: future.result() for future in futures}
            results.update(computed)
            for name in computed:
                del remaining[name]
        return {"results": results}

    def stream(self, state: dict[str, Any], run_config: dict[str, Any], **kwargs: Any) -> Iterator[dict[str, Any]]:
        yield self.invoke(state, run_config)

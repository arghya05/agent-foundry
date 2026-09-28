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

`.stream()` on every class below yields real, discrete progress events
(a mix of `{"event": "...", ...}` dicts and, as the LAST item, the same
plain `{"messages": [...], ...}`/`{"results": {...}}` state dict `.invoke()`
itself returns) — a deliberate, documented DIVERGENCE from single-agent
streaming's chunk shape: `native_engine.stream_run` yields the full
accumulated `{"messages": [...], "thread_id": ...}` state after every step
(matching LangGraph's own `stream_mode="values"`), but a topology has no
single shared "state" the way one agent's think/act loop does — each
specialist/step is its own distinct thing happening, which a named event
describes more honestly than another full-state snapshot would.
`_CompiledWorkflow.stream()` (core/agent.py) is confirmed to be a pure
pass-through with no structural expectations on chunk shape, so nothing
downstream breaks from this — it only needs calling out, not reconciling.
Supervisor/swarm/blackboard/debate stream real events on a resumed turn
too (the same _drive_stream generator handles both); fanout/dag have no
resume concept at all (same as they have none for critique — see each
class's own docstring).

Governed per-specialist turns (supervisor/swarm/blackboard/debate all need
one) go through _PausableTurns, the native counterpart of
orchestration._run_governed_turn — same reasoning: a bare llm.complete()
call would skip guardrails/tools/critique entirely. Unlike
_run_governed_turn's ephemeral-graph-per-call model, _PausableTurns also
keeps a specialist's engine alive across the call boundary when its turn
pauses for approval, so a topology-level `.resume()` can continue exactly
that specialist's paused state — the actual mechanism topology-level
human-in-the-loop needed (see _PausableTurns' own docstring).
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from ..execution_scope import request_value
from dataclasses import asdict, fields
from typing import Any, Iterator

from ..blackboard import Blackboard, parse_post
from ..orchestration import AgentConfig, DAGStep, _resolve_supervisor_route
from ..llm_gateway import LLMGateway
from .native_engine import NativeEngine
from .protocols import StateStore


class _ThreadLocks:
    """Per-thread_id locks, same two-tier pattern as NativeEngine's own
    `_threads_lock`/`_lock_for` (native_engine.py): a creation lock guards
    only the dict-of-locks itself, so two DIFFERENT thread_ids never block
    each other, while `lock_for(x)` returned for the SAME thread_id is meant
    to be held for an entire turn — not just one dict access — because the
    conversational native graphs below mutate a per-thread history list
    across several steps (route, run a specialist, append its reply), not
    in one atomic operation."""

    def __init__(self) -> None:
        self._creation_lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def lock_for(self, thread_id: str) -> threading.Lock:
        with self._creation_lock:
            return self._locks.setdefault(thread_id, threading.Lock())


class _PausableTurns:
    """Runs a specialist's governed turn through a fresh, ephemeral
    NativeEngine — ephemeral the same way orchestration._run_governed_turn's
    LangGraph graph is rebuilt fresh each call (cheap: ~4 dict entries here,
    not even node wiring), which is why `key` need only be stable for
    config.budget/tracer continuity, not message-history persistence — the
    caller always passes the full history explicitly on a fresh `.run()`.

    Unlike a plain "build ephemeral engine, drive it, read .content and
    discard" helper, this ALSO keeps the specialist's NativeEngine alive
    across the call boundary when its turn pauses for approval, so a later
    `.resume(key=...)` can continue exactly there — an ephemeral engine
    discarded the instant the call returns would silently lose that pending
    state forever, which is exactly the gap that made topology-level
    human-in-the-loop impossible before this class existed. One instance of
    this class lives per topology instance (supervisor/swarm/blackboard/
    debate), keyed by f"{outer_thread_id}-{specialist_name}" — the same key
    convention those classes already use for budget/tracer continuity.

    state_store (optional, default None — unchanged in-process-only
    behavior): passed straight through to every NativeEngine this class
    constructs. NativeEngine._state_for already hydrates from
    state_store.load(key) on a cache miss (see its own docstring) — so
    resume() below, on a state_store-backed instance, no longer depends on
    self._engines still holding the SAME Python NativeEngine object that
    originally paused. Before this, a process restart (or a resume() call
    landing on a different replica than the one that paused) always hit the
    `if engine is None: raise` branch, permanently stranding that paused
    approval — .run()'s __interrupt__ result was real and returned to the
    caller, but nothing about it survived past self._engines' own lifetime."""

    def __init__(self, *, state_store: StateStore | None = None) -> None:
        self._lock = threading.Lock()
        self._engines: dict[str, NativeEngine] = {}
        self._state_store = state_store

    def run(self, config: AgentConfig, messages: list[dict], *, key: str) -> dict[str, Any]:
        engine = NativeEngine(state_store=self._state_store)
        state = {
            "messages": list(messages), "thread_id": key,
            "critique_retries": 0, "critique_last_score": None, "_pending": None,
        }
        identity = request_value({}, "request_identity")
        if identity is not None:
            state["request_identity"] = dict(identity)
        # Seed the engine's OWN _threads dict with this exact state object —
        # an engine discarded right after this call wouldn't need to, but
        # resume() reads state back via self._state_for(key), which looks in
        # _threads; without this, a later resume() on this same (kept-alive)
        # engine would silently find a blank, freshly-created state instead
        # of the one _drive mutated. This deliberately overwrites (not
        # hydrates from) any state_store entry under this exact key — a
        # fresh .run() call always starts from the caller-supplied `messages`
        # (the full history, resupplied every call — see this class's own
        # docstring), never from a stale prior turn's persisted state.
        with engine._threads_lock:
            engine._threads[key] = state
        with engine._lock_for(key):
            result = engine._drive(config, state, key)  # _drive persists to state_store itself, if one is set
        self._remember_or_forget(key, engine, result)
        return result

    def resume(self, config: AgentConfig, *, key: str, approved: bool) -> dict[str, Any]:
        with self._lock:
            engine = self._engines.get(key)
        if engine is None:
            # No in-memory record of a pause — either genuinely nothing is
            # pending, or it WAS pending but this is a fresh process/replica
            # that never saw the .run() call which created it. Either way,
            # a fresh state_store-backed NativeEngine resolves it correctly:
            # NativeEngine.resume() -> _state_for() hydrates the persisted
            # `_pending` from the store if it's there, and raises its own
            # RuntimeError (same message shape) if it genuinely isn't —
            # so this no longer needs its own separate "nothing pending"
            # error path duplicating that check.
            engine = NativeEngine(state_store=self._state_store)
        result = engine.resume(config, approved=approved, thread_id=key)
        self._remember_or_forget(key, engine, result)
        return result

    def _remember_or_forget(self, key: str, engine: NativeEngine, result: dict[str, Any]) -> None:
        with self._lock:
            if result.get("__interrupt__"):
                self._engines[key] = engine  # keep it alive — a later resume(key=...) needs this exact engine/state
            else:
                self._engines.pop(key, None)  # turn finished clean — nothing left to resume


class _TopologyState:
    """Shared topology-level state (the outer conversation history, plus
    whatever small "which specialist/phase/round is paused" marker each of
    supervisor/swarm/blackboard/debate tracks) for one thread_id — the piece
    _PausableTurns' own state_store wiring does NOT cover, since that only
    persists a SINGLE specialist's turn (keyed f"{thread_id}-{name}"), never
    which specialist the topology itself was routing to, how much outer
    history has accumulated, or a round/hop counter. Every one of the four
    topology classes below had this same shape hand-duplicated as
    self._threads/self._paused/self._threads_lock before this class existed
    — one implementation here instead of four copies to keep in sync.

    Same load-on-miss hydration pattern as NativeEngine._state_for
    (native_engine.py): state_store.load(thread_id) populates a fresh
    in-process cache entry the first time this thread_id is touched in THIS
    process — so a restart, or a resume() landing on a different replica
    than the one that paused, finds the real history/paused marker instead
    of an empty history and a false "nothing pending" error. Before this
    class existed, that state was process-local ONLY, no matter what
    state_store a caller passed to Workflow.supervisor/.swarm/.blackboard/
    .debate — nothing plugged one in at this layer at all.

    `paused` is whatever small JSON-serializable value each topology needs
    (a bare specialist name for supervisor, {"name":..., "hops_done":...}
    for swarm, {"round":..., "agent_index":...} for blackboard, {"phase":...,
    "index":...} for debate) — this class never inspects its shape, only
    stores and returns it."""

    def __init__(self, *, state_store: StateStore | None = None) -> None:
        self._state_store = state_store
        self._lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] = {}  # thread_id -> {"history": [...], "paused": <topology-specific | None>}

    def _entry(self, thread_id: str) -> dict[str, Any]:
        with self._lock:
            if thread_id in self._cache:
                return self._cache[thread_id]
        # Outside the lock, same reasoning as NativeEngine._state_for: this
        # may be real network I/O (Redis/Postgres), and holding the lock here
        # would block every OTHER thread_id's unrelated access for as long as
        # this one load takes. Every real caller already holds its own
        # per-thread_id lock (_ThreadLocks.lock_for) for the whole turn, so
        # two concurrent _entry(thread_id) calls for the SAME thread_id
        # don't happen in practice — the re-check below is defensive.
        loaded = self._state_store.load(thread_id) if self._state_store is not None else None
        with self._lock:
            if thread_id in self._cache:
                return self._cache[thread_id]
            entry = loaded if loaded is not None else {"history": [], "paused": None}
            self._cache[thread_id] = entry
            return entry

    def history_for(self, thread_id: str) -> list[dict]:
        return self._entry(thread_id)["history"]

    def paused_for(self, thread_id: str) -> Any | None:
        return self._entry(thread_id)["paused"]

    def set_paused(self, thread_id: str, value: Any) -> None:
        entry = self._entry(thread_id)
        with self._lock:
            entry["paused"] = value
        self.persist(thread_id)

    def clear_paused(self, thread_id: str) -> None:
        self.set_paused(thread_id, None)

    def persist(self, thread_id: str) -> None:
        """Call once after mutating the history list in place (history_for()
        returns a live reference, so append() doesn't itself go through
        set_paused/clear_paused) — always call this as the LAST step before
        a turn's final yield, after every history.append() for that turn, so
        a crash right after doesn't leave the persisted copy one message
        behind the value actually returned to the caller."""
        if self._state_store is None:
            return
        self._state_store.save(thread_id, self._entry(thread_id))


def _native_run_worker_messages(config: AgentConfig, item: str, *, thread_id: str) -> list[dict]:
    """Fanout's own ephemeral-per-item worker turn — deliberately NOT routed
    through _PausableTurns (fanout doesn't support pause/resume, same as it
    doesn't support AgentConfig.critique — see _NativeFanoutGraph's own
    docstring for the shared race-condition reasoning). Returns every
    message the turn PRODUCED (not just the final reply, and not the seed
    user message either) — build_fanout_graph's own Send(node,
    {"messages": [seed_user_msg], ...}) feeds that seed straight to the
    worker node as its local input, it's never itself merged into the
    parent graph's messages channel (only a node's OWN return value is);
    only what the worker's think/act loop actually appends ends up in the
    final flattened list _FanoutWorkflow.run() returns. Excluding index 0
    (the seed) here matches that exactly."""
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
    governed turn via _PausableTurns — which, unlike a bare
    native_run_governed_turn call, keeps that specialist's engine alive if
    the turn pauses for tool approval, so `.resume()` can continue the SAME
    specialist's SAME paused state rather than re-routing from scratch."""

    def __init__(
        self, *, supervisor_prompt: str, agents: dict[str, AgentConfig], llm: LLMGateway, task: str = "default",
        fallback_agent: str | None = None, state_store: StateStore | None = None,
    ) -> None:
        self._prompt = supervisor_prompt
        self._agents = agents
        self._llm = llm
        self._task = task
        self._fallback_agent = fallback_agent
        self._state = _TopologyState(state_store=state_store)
        self._locks = _ThreadLocks()
        self._turns = _PausableTurns(state_store=state_store)

    def invoke(self, state: Any, run_config: dict[str, Any]) -> dict[str, Any]:
        last: dict[str, Any] | None = None
        for last in self._drive_stream(state, run_config):
            pass
        assert last is not None  # _drive_stream always yields at least once
        return last

    def stream(self, state: Any, run_config: dict[str, Any], **kwargs: Any) -> Iterator[dict[str, Any]]:
        yield from self._drive_stream(state, run_config)

    def _drive_stream(self, state: Any, run_config: dict[str, Any]) -> Iterator[dict[str, Any]]:
        thread_id = run_config["configurable"]["thread_id"]
        resume_payload = getattr(state, "resume", None)
        with self._locks.lock_for(thread_id):  # serializes the WHOLE turn for this thread_id, not just history's own dict access
            history = self._state.history_for(thread_id)
            if resume_payload is not None:
                name = self._state.paused_for(thread_id)
                if name is None:
                    raise RuntimeError(f"thread {thread_id!r} has nothing pending to resume")
                decision = bool(resume_payload.get("approved"))
                result = self._turns.resume(self._agents[name], key=f"{thread_id}-{name}", approved=decision)
            else:
                history.append(state["messages"][-1])
                name = _resolve_supervisor_route(
                    self._llm, task=self._task, agents=list(self._agents), prompt=self._prompt,
                    messages=list(history), fallback_agent=self._fallback_agent,
                )
                yield {"event": "router.selected", "agent": name}
                result = self._turns.run(self._agents[name], list(history), key=f"{thread_id}-{name}")

            if result.get("__interrupt__"):
                self._state.set_paused(thread_id, name)
                yield {"messages": list(history), "thread_id": thread_id, "__interrupt__": result["__interrupt__"]}
                return

            history.append({"role": "assistant", "content": result["messages"][-1]["content"]})
            self._state.clear_paused(thread_id)  # persists history+paused together, after every append for this turn
            yield {"event": "specialist.completed", "agent": name}
            yield {"messages": list(history), "thread_id": thread_id}


class _NativeSwarmGraph:
    """Native counterpart of build_swarm_graph: decentralized handoff — each
    specialist's full turn either ends normally or replies exactly
    `HANDOFF <peer>`, in which case the peer runs next. `max_handoffs` bounds
    a genuinely possible (not hypothetical) infinite loop between two
    specialists handing off to each other forever — the LangGraph graph has
    no such bound either, but a native in-process loop with no bound at all
    would just hang the calling thread."""

    def __init__(self, *, agents: dict[str, AgentConfig], entry: str, max_handoffs: int = 25, state_store: StateStore | None = None) -> None:
        self._agents = agents
        self._entry = entry
        self._max_handoffs = max_handoffs
        self._state = _TopologyState(state_store=state_store)
        self._locks = _ThreadLocks()
        self._turns = _PausableTurns(state_store=state_store)

    def invoke(self, state: Any, run_config: dict[str, Any]) -> dict[str, Any]:
        last: dict[str, Any] | None = None
        for last in self._drive_stream(state, run_config):
            pass
        assert last is not None
        return last

    def stream(self, state: Any, run_config: dict[str, Any], **kwargs: Any) -> Iterator[dict[str, Any]]:
        yield from self._drive_stream(state, run_config)

    def _drive_stream(self, state: Any, run_config: dict[str, Any]) -> Iterator[dict[str, Any]]:
        thread_id = run_config["configurable"]["thread_id"]
        resume_payload = getattr(state, "resume", None)
        with self._locks.lock_for(thread_id):
            history = self._state.history_for(thread_id)
            if resume_payload is not None:
                paused = self._state.paused_for(thread_id)
                if paused is None:
                    raise RuntimeError(f"thread {thread_id!r} has nothing pending to resume")
                name, hops_done = paused["name"], paused["hops_done"]
                result = self._turns.resume(self._agents[name], key=f"{thread_id}-{name}", approved=bool(resume_payload.get("approved")))
            else:
                history.append(state["messages"][-1])
                name, hops_done = self._entry, 0
                result = self._turns.run(self._agents[name], list(history), key=f"{thread_id}-{name}")

            while True:
                if result.get("__interrupt__"):
                    self._state.set_paused(thread_id, {"name": name, "hops_done": hops_done})
                    yield {"messages": list(history), "thread_id": thread_id, "__interrupt__": result["__interrupt__"]}
                    return

                text = result["messages"][-1]["content"]
                history.append({"role": "assistant", "content": text})
                hops_done += 1
                yield {"event": "agent.completed", "agent": name}
                if isinstance(text, str) and text.startswith("HANDOFF ") and hops_done < self._max_handoffs:
                    target = text[len("HANDOFF "):].strip()
                    if target in self._agents and target != name:
                        self._agents[name].eval_harness.record("component", "handoff", target, 1.0)
                        yield {"event": "handoff", "from": name, "to": target}
                        name = target
                        result = self._turns.run(self._agents[name], list(history), key=f"{thread_id}-{name}")
                        continue
                break

            self._state.clear_paused(thread_id)  # persists history+paused together, after every append for this turn
            yield {"messages": list(history), "thread_id": thread_id}


class _NativeBlackboardGraph:
    """Native counterpart of build_blackboard_graph. Matches the LangGraph
    version's own (slightly surprising but deliberate) behavior exactly:
    agents' contributions land on the shared Blackboard object, NEVER in the
    returned `messages` — collaborate() only ever returns {"round": ...},
    no "messages" key — so this reads the workspace via `blackboard`, not
    via a conversational reply."""

    def __init__(self, *, agents: dict[str, AgentConfig], blackboard: Blackboard, rounds: int = 2, state_store: StateStore | None = None) -> None:
        self._agents = agents
        self._blackboard = blackboard
        self._rounds = rounds
        self._state = _TopologyState(state_store=state_store)
        self._locks = _ThreadLocks()
        self._turns = _PausableTurns(state_store=state_store)
        # The Blackboard's own contents (facts/hypotheses/evidence/etc) are a
        # SEPARATE persistence concern from _state (which only covers outer
        # history + paused round/agent-index) — a distinct key per thread_id
        # (f"{thread_id}-blackboard", parallel to _PausableTurns' own
        # f"{thread_id}-{specialist}" convention), not folded into _state's
        # generic entry, since no other topology has anything like it.
        self._state_store = state_store
        self._hydrated_blackboards: set[str] = set()

    def _hydrate_blackboard(self, thread_id: str) -> None:
        """Lazy, once-per-thread_id-per-process: the FIRST _drive_stream call
        for a given thread_id in this process checks the store for a
        previously-persisted snapshot and restores it into self._blackboard
        — the caller's own (possibly freshly-constructed, empty) Blackboard
        object. Guarded by self._locks.lock_for(thread_id), already held by
        every real caller for the whole turn, so this never races with
        _persist_blackboard for the same thread_id."""
        if self._state_store is None or thread_id in self._hydrated_blackboards:
            self._hydrated_blackboards.add(thread_id)
            return
        saved = self._state_store.load(f"{thread_id}-blackboard")
        if saved is not None:
            for f in fields(Blackboard):
                setattr(self._blackboard, f.name, list(saved.get(f.name, [])))
        self._hydrated_blackboards.add(thread_id)

    def _persist_blackboard(self, thread_id: str) -> None:
        if self._state_store is not None:
            self._state_store.save(f"{thread_id}-blackboard", asdict(self._blackboard))

    def _prompt(self) -> str:
        return (
            f"Shared blackboard:\n{self._blackboard.render()}"
            "\n\nContribute with exactly: POST <fact|hypothesis|evidence|task|contradiction|question>: <text>"
        )

    def invoke(self, state: Any, run_config: dict[str, Any]) -> dict[str, Any]:
        last: dict[str, Any] | None = None
        for last in self._drive_stream(state, run_config):
            pass
        assert last is not None
        return last

    def stream(self, state: Any, run_config: dict[str, Any], **kwargs: Any) -> Iterator[dict[str, Any]]:
        yield from self._drive_stream(state, run_config)

    def _drive_stream(self, state: Any, run_config: dict[str, Any]) -> Iterator[dict[str, Any]]:
        thread_id = run_config["configurable"]["thread_id"]
        resume_payload = getattr(state, "resume", None)
        agent_names = list(self._agents)
        with self._locks.lock_for(thread_id):
            self._hydrate_blackboard(thread_id)
            history = self._state.history_for(thread_id)
            if resume_payload is not None:
                paused = self._state.paused_for(thread_id)
                if paused is None:
                    raise RuntimeError(f"thread {thread_id!r} has nothing pending to resume")
                round_i, agent_i = paused["round"], paused["agent_index"]
                name = agent_names[agent_i]
                result = self._turns.resume(self._agents[name], key=f"{thread_id}-{name}", approved=bool(resume_payload.get("approved")))
            else:
                history.append(state["messages"][-1])
                round_i, agent_i = 0, 0
                name = agent_names[0]
                result = self._turns.run(self._agents[name], [*history, {"role": "user", "content": self._prompt()}], key=f"{thread_id}-{name}")

            while True:
                if result.get("__interrupt__"):
                    self._state.set_paused(thread_id, {"round": round_i, "agent_index": agent_i})
                    yield {"messages": list(history), "thread_id": thread_id, "round": round_i, "__interrupt__": result["__interrupt__"]}
                    return

                config = self._agents[name]
                text = result["messages"][-1]["content"]
                parsed = parse_post(text)
                if parsed:
                    self._blackboard.post(*parsed)
                    self._persist_blackboard(thread_id)
                    config.eval_harness.record("component", name, "posted", 1.0, section=parsed[0])
                    yield {"event": "agent.posted", "agent": name, "section": parsed[0]}
                else:
                    config.eval_harness.record("component", name, "posted", 0.0)
                    yield {"event": "agent.post_failed", "agent": name}

                agent_i += 1
                if agent_i >= len(agent_names):
                    agent_i = 0
                    round_i += 1
                if round_i >= self._rounds:
                    break
                name = agent_names[agent_i]
                result = self._turns.run(self._agents[name], [*history, {"role": "user", "content": self._prompt()}], key=f"{thread_id}-{name}")

            self._state.clear_paused(thread_id)  # persists history+paused together, after every append for this turn
            yield {"messages": list(history), "thread_id": thread_id, "round": self._rounds}


class _NativeDebateGraph:
    """Native counterpart of build_debate_graph: N debaters answer
    independently (via _PausableTurns), then one judge call reviews the
    transcript and gives the final answer."""

    def __init__(self, *, debaters: dict[str, AgentConfig], judge: AgentConfig, state_store: StateStore | None = None) -> None:
        self._debaters = debaters
        self._judge = judge
        self._state = _TopologyState(state_store=state_store)
        self._locks = _ThreadLocks()
        self._turns = _PausableTurns(state_store=state_store)

    def invoke(self, state: Any, run_config: dict[str, Any]) -> dict[str, Any]:
        last: dict[str, Any] | None = None
        for last in self._drive_stream(state, run_config):
            pass
        assert last is not None
        return last

    def stream(self, state: Any, run_config: dict[str, Any], **kwargs: Any) -> Iterator[dict[str, Any]]:
        yield from self._drive_stream(state, run_config)

    def _drive_stream(self, state: Any, run_config: dict[str, Any]) -> Iterator[dict[str, Any]]:
        thread_id = run_config["configurable"]["thread_id"]
        resume_payload = getattr(state, "resume", None)
        debater_names = list(self._debaters)
        with self._locks.lock_for(thread_id):
            history = self._state.history_for(thread_id)
            if resume_payload is not None:
                paused = self._state.paused_for(thread_id)
                if paused is None:
                    raise RuntimeError(f"thread {thread_id!r} has nothing pending to resume")
                phase, index = paused["phase"], paused["index"]
                approved = bool(resume_payload.get("approved"))
                if phase == "debater":
                    name = debater_names[index]
                    result = self._turns.resume(self._debaters[name], key=f"{thread_id}-{name}", approved=approved)
                else:
                    result = self._turns.resume(self._judge, key=f"{thread_id}-judge", approved=approved)
            else:
                history.append(state["messages"][-1])
                phase, index = "debater", 0
                name = debater_names[0]
                result = self._turns.run(self._debaters[name], list(history), key=f"{thread_id}-{name}")

            while True:
                if result.get("__interrupt__"):
                    self._state.set_paused(thread_id, {"phase": phase, "index": index})
                    yield {"messages": list(history), "thread_id": thread_id, "__interrupt__": result["__interrupt__"]}
                    return

                if phase == "debater":
                    name = debater_names[index]
                    text = result["messages"][-1]["content"]
                    history.append({"role": "assistant", "content": f"[{name}] {text}"})
                    self._debaters[name].eval_harness.record("component", name, "answered", 1.0)
                    yield {"event": "debater.answered", "debater": name}
                    index += 1
                    if index < len(debater_names):
                        name = debater_names[index]
                        result = self._turns.run(self._debaters[name], list(history), key=f"{thread_id}-{name}")
                        continue
                    phase = "judge"
                    transcript = "\n".join(f"- {m['content']}" for m in history if m["role"] == "assistant")
                    prompt = f"Candidate answers:\n{transcript}\n\nReply with the single best final answer."
                    result = self._turns.run(self._judge, [{"role": "user", "content": prompt}], key=f"{thread_id}-judge")
                    continue

                judge_text = result["messages"][-1]["content"]
                self._judge.eval_harness.record("flow", thread_id, "judged", 1.0)
                history.append({"role": "assistant", "content": judge_text})
                yield {"event": "judge.decided"}
                break

            self._state.clear_paused(thread_id)  # persists history+paused together, after every append for this turn
            yield {"messages": list(history), "thread_id": thread_id}


class _NativeFanoutGraph:
    """Native counterpart of build_fanout_graph: dispatches config's think/act
    loop once per item, concurrently (via ThreadPoolExecutor — the sync-first
    equivalent of LangGraph's Send-based concurrent supersteps), and flattens
    every worker's full message history into one list, same as
    _FanoutWorkflow.run() already does with the LangGraph version's output.
    critique isn't supported here either, same race-condition reasoning as
    the LangGraph version (see build_fanout_graph's own docstring)."""

    def __init__(self, *, config: AgentConfig, max_concurrency: int = 32) -> None:
        if config.critique is not None:
            raise ValueError(
                "native fanout doesn't support AgentConfig.critique — concurrent "
                "workers would race on shared per-thread critique state, same as "
                "build_fanout_graph's own restriction. Use the single-agent runtime "
                "directly per item instead."
            )
        self._config = config
        self._max_concurrency = max_concurrency

    def invoke(self, state: dict[str, Any], run_config: dict[str, Any]) -> dict[str, Any]:
        last: dict[str, Any] | None = None
        for last in self._drive_stream(state, run_config):
            pass
        assert last is not None
        return last

    def stream(self, state: dict[str, Any], run_config: dict[str, Any], **kwargs: Any) -> Iterator[dict[str, Any]]:
        yield from self._drive_stream(state, run_config)

    def _drive_stream(self, state: dict[str, Any], run_config: dict[str, Any]) -> Iterator[dict[str, Any]]:
        thread_id = run_config["configurable"]["thread_id"]
        items = state["items"]
        all_messages: list[dict] = []
        # ThreadPoolExecutor(max_workers=N) only ever runs N submitted
        # callables concurrently regardless of how many are queued up — no
        # separate chunking/semaphore layer needed, same reasoning as
        # batch.run_batch's own max_workers cap (batch.py). as_completed
        # (not "submit everything, then block on each in submission order")
        # is what actually lets `item.completed` stream out as each item
        # finishes, rather than all arriving together at the end.
        with ThreadPoolExecutor(max_workers=min(len(items), self._max_concurrency) or 1) as pool:
            future_to_item = {pool.submit(copy_context().run, _native_run_worker_messages, self._config, item, thread_id=thread_id): item for item in items}
            for future in as_completed(future_to_item):
                item = future_to_item[future]
                all_messages.extend(future.result())
                yield {"event": "item.completed", "item": item}
        yield {"messages": all_messages, "thread_id": thread_id, "items": items}


class _NativeDagGraph:
    """Native counterpart of build_dag_graph: steps with satisfied
    dependencies run concurrently in waves (ThreadPoolExecutor), same
    parallelism build_dag_graph gets for free from LangGraph scheduling any
    node whose incoming edges are all satisfied in one superstep. No LLM
    involved — DAGStep is already framework-agnostic, reused unchanged."""

    def __init__(self, steps: list[DAGStep], *, max_concurrency: int = 32) -> None:
        self._steps = steps
        self._max_concurrency = max_concurrency

    def invoke(self, state: dict[str, Any], run_config: dict[str, Any]) -> dict[str, Any]:
        last: dict[str, Any] | None = None
        for last in self._drive_stream(state, run_config):
            pass
        assert last is not None
        return last

    def stream(self, state: dict[str, Any], run_config: dict[str, Any], **kwargs: Any) -> Iterator[dict[str, Any]]:
        yield from self._drive_stream(state, run_config)

    def _drive_stream(self, state: dict[str, Any], run_config: dict[str, Any]) -> Iterator[dict[str, Any]]:
        results: dict[str, Any] = dict(state.get("results") or {})
        remaining = {step.name: step for step in self._steps if step.name not in results}
        while remaining:
            ready = [step for step in remaining.values() if all(dep in results for dep in step.depends_on)]
            if not ready:
                raise RuntimeError("DAG has unsatisfiable dependencies (a cycle, or a step depending on an unknown step)")
            with ThreadPoolExecutor(max_workers=min(len(ready), self._max_concurrency) or 1) as pool:
                future_to_name = {pool.submit(copy_context().run, step.fn, dict(results)): step.name for step in ready}
                for future in as_completed(future_to_name):
                    name = future_to_name[future]
                    results[name] = future.result()
                    del remaining[name]
                    yield {"event": "step.completed", "step": name}
        yield {"results": results}

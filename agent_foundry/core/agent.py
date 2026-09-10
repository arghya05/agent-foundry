"""Core — Agent: the framework's public entry point, replacing direct calls to
orchestration.build_*_graph. build_agent_graph itself is unchanged and still
directly importable — _CompiledWorkflow below wraps whatever compiled graph
it's given (LangGraph's, or native_engine._NativeGraph's) verbatim, it
doesn't reimplement either one, so the existing orchestration test suite and
every scaffold-generated/example agent that calls build_agent_graph directly
keeps working.

Agent(...) covers the single-AgentConfig topology (workflow="react", i.e.
build_agent_graph's think/act loop). Supervisor/swarm/blackboard/debate/dag
take multiple AgentConfigs (or, for dag, no AgentConfig at all) plus
topology-specific arguments that don't fit a single Agent's constructor —
those are Workflow's factories, each pulling the underlying AgentConfig out
of the Agent instances passed in.

`runtime="langgraph"` (default) or `runtime="native"` picks which
WorkflowEngine actually runs that AgentConfig — native_engine.NativeEngine is
a second, framework-free implementation of the same think/act/critique loop,
proving core.protocols.WorkflowEngine is a real seam rather than a
LangGraph-only abstraction. Both produce the same RunResult shape; Agent's
own public methods below don't know or care which one is underneath.
"""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Iterator, cast

if TYPE_CHECKING:
    from .run import Run

from ..batch import BatchReport, IntervalScheduler, run_batch
from ..blackboard import Blackboard
from ..contracts import AgentRole, Identity, Policy, ToolSpec
from ..context import MemoryStore
from ..eval import EvalHarness
from ..events import EventBus, InMemoryEventBus, wire_event_driven
from ..guardrails import GuardrailEngine
from ..llm_gateway import AnthropicProvider, LLMGateway
from ..observability import Tracer
from ..orchestration import (
    AgentConfig,
    CritiqueConfig,
    DAGStep,
    agent_as_tool,
    build_blackboard_graph,
    build_debate_graph,
    build_dag_graph,
    build_supervisor_graph,
    build_swarm_graph,
)
from ..runtime import RunBudget, RunBudgetLike
from ..tools_gateway import ToolRegistry
from .engines import RUNTIMES
from .execution_context import ExecutionContext
from .protocols import Memory, Tool
from .result import RunResult, result_from_graph_output


def _toolspec_from_callable(fn: Callable[..., Any], *, name: str | None = None, description: str | None = None) -> ToolSpec:
    """Plain-function -> ToolSpec, the governed-path equivalent of
    quickstart.py's "type hints + docstring become schema" convention —
    ToolSpec.parameters accepts this same loose {name: type} shorthand
    (see tools_gateway.tool_json_schema). `name`/`description` override the
    function's own __name__/__doc__ — used by tool_decorator.tool()'s
    explicit name=/description= kwargs."""
    type_names = {str: "string", int: "integer", float: "number", bool: "boolean"}
    params = {p.name: type_names.get(p.annotation, "string") for p in inspect.signature(fn).parameters.values()}
    doc_description = fn.__doc__.strip().splitlines()[0] if fn.__doc__ else fn.__name__
    return ToolSpec(name=name or fn.__name__, description=description or doc_description, parameters=params, fn=fn)


def _coerce_tools(tools: Any) -> ToolRegistry:
    if tools is None:
        return ToolRegistry()
    if isinstance(tools, ToolRegistry):
        return tools
    registry = ToolRegistry()
    for t in tools:
        if isinstance(t, ToolSpec):
            registry.register(t)
        elif isinstance(t, Tool):
            registry.register(ToolSpec(name=t.name, description=t.description, parameters=t.parameters, fn=t.fn))
        else:
            registry.register(_toolspec_from_callable(t))
    return registry


def _coerce_memory(memory: Any) -> MemoryStore | None:
    """A caller passing the wrong type here must fail loudly, naming what
    was actually passed — silently substituting a brand-new MemoryStore()
    instead would quietly drop any real memory they configured, with no
    error to explain why their agent "forgot everything.\""""
    if not memory:
        return None
    if isinstance(memory, Memory):
        # AgentConfig.memory is declared MemoryStore | None, not the broader
        # Memory protocol this isinstance check actually accepts — every
        # real caller passes a genuine MemoryStore; cast documents that,
        # not silence a real mismatch.
        return cast(MemoryStore, memory)
    raise TypeError(f"memory= must be a MemoryStore (or satisfy the Memory protocol), got {type(memory).__name__}")


def _default_identity(name: str) -> Identity:
    return Identity(id=f"{name}-agent", tenant_id="default")


def _request_identity_dict(context: ExecutionContext) -> dict[str, Any] | None:
    """Derives the per-request identity orchestration._resolve_identity
    reads back out of state["request_identity"] — None when the caller's
    ExecutionContext didn't populate user_id/tenant_id/permissions at all
    (the default for every existing caller, unchanged behavior). A plain
    dict, not an Identity object, so it survives any checkpointer that
    needs JSON-serializable state, not just the default in-memory one."""
    if context.user_id is None and context.tenant_id is None and not context.permissions:
        return None
    return {"id": context.user_id or "unknown", "tenant_id": context.tenant_id or "", "roles": tuple(context.permissions)}


def _default_policy(tool_names: list[str]) -> Policy:
    return Policy(allowed_tools=frozenset(tool_names))


@dataclass
class _ResumePayload:
    """A langgraph-free stand-in for langgraph.types.Command(resume=...),
    used only against native_engine._NativeGraph. That engine's own .invoke()
    duck-types on exactly one attribute — `getattr(state_or_command,
    "resume", None)` (see its own docstring) — never constructs or checks
    against a real Command, so this satisfies it identically without
    requiring langgraph to be installed for runtime="native"."""

    resume: dict[str, Any]


class _CompiledWorkflow:
    """Shared run/stream/resume/batch/schedule/as_tool/serve surface over one
    compiled LangGraph graph — the thing that actually removes
    `graph.invoke({"messages": [...], "thread_id": ...}, {"configurable":
    ...})` and `Command(resume={...})` from the public API. Used by both Agent
    (the react/single topology) and every Workflow.* factory that shares
    AgentState's messages+thread_id shape (supervisor/swarm/debate/blackboard)
    — invoking/resuming/batching a compiled graph doesn't depend on how many
    AgentConfigs built it, only on the state shape it expects.

    `extra_state` seeds any state keys a topology's nodes require beyond
    messages/thread_id but don't default via state.get(...) — e.g.
    build_blackboard_graph's collaborate() node reads state["round"] directly.
    """

    def __init__(self, graph: Any, *, name: str = "agent", extra_state: dict[str, Any] | None = None) -> None:
        self._graph = graph
        self.name = name
        self._extra_state = extra_state or {}
        self._scheduler = IntervalScheduler()

    @property
    def graph(self) -> Any:
        return self._graph

    def _initial_state(self, message: str, thread_id: str, *, identity: dict[str, Any] | None = None) -> dict[str, Any]:
        state = {"messages": [{"role": "user", "content": message}], "thread_id": thread_id, **self._extra_state}
        # Only included when there actually IS one — see
        # _request_identity_dict's own reasoning, mirrored in
        # NativeEngine.run(): omitting the key on a continuing thread must
        # not reset an earlier turn's resolved identity to "none".
        if identity is not None:
            state["request_identity"] = identity
        return state

    def run(self, message: str, *, context: ExecutionContext | None = None) -> RunResult:
        context = context or ExecutionContext()
        thread_id = context.resolved_thread_id()
        state = self._initial_state(message, thread_id, identity=_request_identity_dict(context))
        raw = self._graph.invoke(state, {"configurable": {"thread_id": thread_id}})
        return result_from_graph_output(raw, thread_id=thread_id)

    invoke = run  # alias for API parity — run/invoke are the same call, not a distinct one

    async def arun(self, message: str, *, context: ExecutionContext | None = None) -> RunResult:
        """Non-blocking run() for an async caller. `self._graph` is either a
        compiled LangGraph graph (has a real `.ainvoke()` — verified
        empirically: LangGraph runs plain-sync node functions off-thread on
        its own, so orchestration.py's think/act/critique nodes need no
        async rewrite for this to be genuinely non-blocking) or
        native_engine._NativeGraph (no async of its own — `asyncio.to_thread`
        keeps this method non-blocking either way, just without LangGraph's
        native off-thread scheduling)."""
        if hasattr(self._graph, "ainvoke"):
            context = context or ExecutionContext()
            thread_id = context.resolved_thread_id()
            state = self._initial_state(message, thread_id, identity=_request_identity_dict(context))
            raw = await self._graph.ainvoke(state, {"configurable": {"thread_id": thread_id}})
            return result_from_graph_output(raw, thread_id=thread_id)
        return await asyncio.to_thread(self.run, message, context=context)

    def stream(self, message: str, *, context: ExecutionContext | None = None) -> Iterator[Any]:
        # stream_mode="values": LangGraph's own default ("updates") yields
        # per-node partial dicts keyed by node name (e.g. {"think": {...}}),
        # not the full state — confirmed empirically, see
        # tests/test_workflow_engine_protocol.py. "values" yields the full
        # accumulated state after each step instead, matching what
        # native_engine._NativeGraph.stream() already yields (and what
        # result_from_graph_output()/.invoke() both expect) — the two
        # engines actually agree on a chunk shape now, not just by accident.
        context = context or ExecutionContext()
        thread_id = context.resolved_thread_id()
        state = self._initial_state(message, thread_id, identity=_request_identity_dict(context))
        yield from self._graph.stream(state, {"configurable": {"thread_id": thread_id}}, stream_mode="values")

    async def astream(self, message: str, *, context: ExecutionContext | None = None) -> Any:
        """Non-blocking stream() — see arun()'s docstring for the same
        LangGraph-native-vs-to_thread split. An async generator (`async
        for chunk in agent.astream(...)`), not a coroutine returning an
        iterator."""
        if hasattr(self._graph, "astream"):
            context = context or ExecutionContext()
            thread_id = context.resolved_thread_id()
            state = self._initial_state(message, thread_id, identity=_request_identity_dict(context))
            async for chunk in self._graph.astream(state, {"configurable": {"thread_id": thread_id}}, stream_mode="values"):
                yield chunk
            return
        for chunk in await asyncio.to_thread(lambda: list(self.stream(message, context=context))):
            yield chunk

    def _resume_command(self, payload: dict[str, Any]) -> Any:
        # Same hasattr(self._graph, "ainvoke") discriminator arun()/astream()/
        # aresume() already use for LangGraph-vs-native — a real LangGraph
        # graph needs a real Command (its own .invoke() checks against it);
        # _NativeGraph duck-types on just a `.resume` attribute (see
        # native_engine.py's own docstring) and never needs langgraph
        # installed at all.
        if hasattr(self._graph, "ainvoke"):
            from langgraph.types import Command
            return Command(resume=payload)
        return _ResumePayload(resume=payload)

    def resume(self, *, approved: bool, decision: dict[str, Any] | None = None, context: ExecutionContext) -> RunResult:
        # `decision` layers richer resume payloads (an event's data, a
        # clarification answer, a payment confirmation id) on top of the
        # plain approved/denied case — see core/engines.py's _invoke_resume
        # for why extra keys are always safe to add here.
        thread_id = context.resolved_thread_id()
        payload = {"approved": approved, **(decision or {})}
        raw = self._graph.invoke(self._resume_command(payload), {"configurable": {"thread_id": thread_id}})
        return result_from_graph_output(raw, thread_id=thread_id)

    async def aresume(self, *, approved: bool, decision: dict[str, Any] | None = None, context: ExecutionContext) -> RunResult:
        """Non-blocking resume() — same LangGraph-native-vs-to_thread split
        as arun()/astream()."""
        thread_id = context.resolved_thread_id()
        payload = {"approved": approved, **(decision or {})}
        if hasattr(self._graph, "ainvoke"):
            raw = await self._graph.ainvoke(self._resume_command(payload), {"configurable": {"thread_id": thread_id}})
            return result_from_graph_output(raw, thread_id=thread_id)
        return await asyncio.to_thread(self.resume, approved=approved, decision=decision, context=context)

    def batch(self, items: list[dict[str, Any]], **kw: Any) -> BatchReport:
        return run_batch(self._graph, items, **kw)

    def schedule(self, message: str, *, every_seconds: float, context: ExecutionContext | None = None) -> str:
        def job() -> None:
            self.run(message, context=context)

        return self._scheduler.schedule(job, every_seconds=every_seconds)

    def cancel_schedule(self, handle: str) -> None:
        self._scheduler.cancel(handle)

    def as_tool(self, *, name: str, description: str, thread_prefix: str | None = None) -> ToolSpec:
        return agent_as_tool(name=name, description=description, graph=self._graph, thread_prefix=thread_prefix)

    def serve(self, **kw: Any) -> Any:
        from ..serve import build_http_app

        return build_http_app(self._graph, **kw)


class _FanoutWorkflow:
    """build_fanout_graph's state is items+messages+thread_id, not a single
    message — dispatches N items concurrently rather than looping one
    conversation, so it gets its own run(items) rather than being squeezed
    into _CompiledWorkflow's run(message) shape."""

    def __init__(self, graph: Any) -> None:
        self._graph = graph

    @property
    def graph(self) -> Any:
        return self._graph

    def run(self, items: list[str], *, context: ExecutionContext | None = None) -> list[str]:
        context = context or ExecutionContext()
        thread_id = context.resolved_thread_id()
        raw = self._graph.invoke({"items": items, "thread_id": thread_id}, {"configurable": {"thread_id": thread_id}})
        return [m["content"] for m in raw.get("messages", [])]


class _DagWorkflow:
    """build_dag_graph's state is a results dict, not a conversation — no
    messages/thread_id/content notion applies, so this exposes exactly what
    the graph actually does: run(inputs) -> the accumulated results dict."""

    def __init__(self, graph: Any) -> None:
        self._graph = graph

    @property
    def graph(self) -> Any:
        return self._graph

    def run(self, inputs: dict[str, Any] | None = None, *, context: ExecutionContext | None = None) -> dict[str, Any]:
        context = context or ExecutionContext()
        thread_id = context.resolved_thread_id()
        raw = self._graph.invoke({"results": inputs or {}}, {"configurable": {"thread_id": thread_id}})
        return raw["results"]


class Agent:
    """Define an agent once; `workflow` picks which build_*_graph runs it —
    LangGraph never appears in this class's public surface. For multi-agent
    topologies (supervisor/swarm/blackboard/debate/fanout/dag), see Workflow,
    which composes several Agents' underlying `.config`."""

    def __init__(
        self,
        name: str,
        instructions: str,
        *,
        model: str = "default",
        tools: Any = None,
        memory: Any = None,
        policy: Policy | None = None,
        identity: Identity | None = None,
        workflow: str = "react",
        runtime: str = "langgraph",
        role: AgentRole = AgentRole.GENERALIST,
        critique: CritiqueConfig | None = None,
        user_id: str | Callable[[Any], str] | None = None,
        llm: LLMGateway | None = None,
        guardrails: Any = None,
        eval_harness: EvalHarness | None = None,
        tracer: Tracer | None = None,
        budget: RunBudgetLike | None = None,
        checkpointer: Any = None,
        event_bus: EventBus | None = None,
    ) -> None:
        if workflow != "react":
            raise ValueError(
                f"Agent(workflow={workflow!r}) is not a single-AgentConfig topology — "
                "use Workflow.supervisor/.swarm/.blackboard/.debate/.fanout/.dag instead"
            )
        if runtime not in RUNTIMES:
            raise ValueError(f"unknown runtime {runtime!r} — use one of {sorted(RUNTIMES)}")
        if runtime == "native" and checkpointer is not None:
            raise ValueError("runtime='native' keeps its own in-memory per-thread state and doesn't accept a checkpointer")
        self.name = name
        self.workflow = workflow
        self.runtime = runtime
        self._checkpointer = checkpointer
        registry = _coerce_tools(tools)
        resolved_policy = policy or _default_policy(registry.names())

        self.config = AgentConfig(
            system_prompt=instructions,
            llm=llm or LLMGateway(provider=AnthropicProvider()),
            tools=registry,
            guardrails=guardrails or GuardrailEngine(resolved_policy),
            eval_harness=eval_harness or EvalHarness(),
            identity=identity or _default_identity(name),
            policy=resolved_policy,
            budget=budget or RunBudget(resolved_policy),
            tracer=tracer or Tracer(f"{name}-agent"),
            task=model,
            memory=_coerce_memory(memory),
            critique=critique,
            user_id=user_id,
            role=role,
        )
        # The actual extension point: RUNTIMES (core/engines.py) is the
        # registry a new backend (Temporal, say) gets added to — Agent
        # itself never branches on a runtime name beyond this one lookup.
        graph: Any = RUNTIMES[runtime].build(self.config, checkpointer=checkpointer)
        self._runners: dict[str, "_CompiledWorkflow"] = {runtime: _CompiledWorkflow(graph, name=name)}
        self._runner = self._runners[runtime]  # the construction-time default — unchanged attribute, unchanged meaning
        self.event_bus = event_bus or InMemoryEventBus()

    @property
    def graph(self) -> Any:
        return self._runner.graph

    def _runner_for(self, runtime: str | None) -> "_CompiledWorkflow":
        """Resolves a per-call `runtime=` override (run/arun/stream/astream/
        resume/aresume) against this Agent's own RUNTIMES-backed runners,
        lazily building and caching a second _CompiledWorkflow the first
        time a non-default runtime is actually used — not eagerly at
        construction. `runtime=None` (the common case) reuses the
        construction-time default (self._runner) with zero extra work.
        Native rejects an explicit checkpointer (unchanged, existing
        validation) — respected per-runtime here, not per-Agent, so an
        Agent constructed with a langgraph checkpointer can still call
        .run(msg, runtime="native") without that checkpointer ever
        reaching an engine that would reject it."""
        name = runtime or self.runtime
        if name not in self._runners:
            if name not in RUNTIMES:
                raise ValueError(f"unknown runtime {name!r} — use one of {sorted(RUNTIMES)}")
            checkpointer = self._checkpointer if name != "native" else None
            graph = RUNTIMES[name].build(self.config, checkpointer=checkpointer)
            self._runners[name] = _CompiledWorkflow(graph, name=self.name)
        return self._runners[name]

    def run(self, message: str, *, context: ExecutionContext | None = None, runtime: str | None = None) -> RunResult:
        return self._runner_for(runtime).run(message, context=context)

    invoke = run

    def start(self, message: str, *, context: ExecutionContext | None = None) -> "Run":
        """Like .run(), but returns a Run — a formal lifecycle
        (STARTED/RUNNING/WAITING_HUMAN/WAITING_EVENT/SUSPENDED/COMPLETED/
        FAILED/CANCELLED) with .pause()/.unpause()/.cancel()/.retry()/
        .fork()/.replay()/.wait_for_event() on top of the same RunResult
        .run()/.resume() already return. .run()/.resume() themselves are
        unchanged — this is an additive second entry point, not a
        replacement."""
        from .run import Run

        run = Run(agent=self, context=context or ExecutionContext())
        run.run(message)
        return run

    async def arun(self, message: str, *, context: ExecutionContext | None = None, runtime: str | None = None) -> RunResult:
        return await self._runner_for(runtime).arun(message, context=context)

    def stream(self, message: str, *, context: ExecutionContext | None = None, runtime: str | None = None) -> Iterator[Any]:
        return self._runner_for(runtime).stream(message, context=context)

    def astream(self, message: str, *, context: ExecutionContext | None = None, runtime: str | None = None) -> Any:
        return self._runner_for(runtime).astream(message, context=context)

    def resume(self, *, approved: bool, decision: dict[str, Any] | None = None, context: ExecutionContext, runtime: str | None = None) -> RunResult:
        return self._runner_for(runtime).resume(approved=approved, decision=decision, context=context)

    async def aresume(self, *, approved: bool, decision: dict[str, Any] | None = None, context: ExecutionContext, runtime: str | None = None) -> RunResult:
        return await self._runner_for(runtime).aresume(approved=approved, decision=decision, context=context)

    def batch(self, items: list[dict[str, Any]], **kw: Any) -> BatchReport:
        return self._runner.batch(items, **kw)

    def schedule(self, message: str, *, every_seconds: float, context: ExecutionContext | None = None) -> str:
        return self._runner.schedule(message, every_seconds=every_seconds, context=context)

    def cancel_schedule(self, handle: str) -> None:
        self._runner.cancel_schedule(handle)

    def as_tool(self, *, name: str | None = None, description: str = "", thread_prefix: str | None = None) -> ToolSpec:
        return self._runner.as_tool(name=name or self.name, description=description, thread_prefix=thread_prefix)

    def serve(self, **kw: Any) -> Any:
        return self._runner.serve(**kw)

    def on(
        self, topic: str, *, thread_id_fn: Callable[[dict[str, Any]], str] | None = None,
    ) -> Callable[[Callable[[dict[str, Any]], None]], Callable[[dict[str, Any]], None]]:
        """`@agent.on("order.delayed")` — wires this agent's own graph to
        respond to every event published to `topic` on `self.event_bus`
        (events.wire_event_driven: the event dict becomes the triggered
        turn's user message), and additionally subscribes the decorated
        function itself as a plain handler (events.EventBus.subscribe) —
        both run on every publish, decorator or not."""
        wire_event_driven(graph=self.graph, bus=self.event_bus, topic=topic, thread_id_fn=thread_id_fn)

        def decorator(handler: Callable[[dict[str, Any]], None]) -> Callable[[dict[str, Any]], None]:
            self.event_bus.subscribe(topic, handler)
            return handler

        return decorator


class Workflow:
    """Factories for the multi-agent topologies that take several AgentConfigs
    (or, for dag, none) plus topology-specific arguments — see the module
    docstring for why these aren't Agent(...) constructor options."""

    @staticmethod
    def supervisor(*, prompt: str, agents: dict[str, Agent], llm: LLMGateway, task: str = "default", checkpointer: Any = None) -> _CompiledWorkflow:
        graph = build_supervisor_graph(
            supervisor_prompt=prompt, agents={n: a.config for n, a in agents.items()}, llm=llm, task=task, checkpointer=checkpointer,
        )
        return _CompiledWorkflow(graph, name="supervisor")

    @staticmethod
    def swarm(*, agents: dict[str, Agent], entry: str, checkpointer: Any = None) -> _CompiledWorkflow:
        graph = build_swarm_graph(agents={n: a.config for n, a in agents.items()}, entry=entry, checkpointer=checkpointer)
        return _CompiledWorkflow(graph, name="swarm")

    @staticmethod
    def blackboard(*, agents: dict[str, Agent], blackboard: Blackboard, rounds: int = 2, checkpointer: Any = None) -> _CompiledWorkflow:
        graph = build_blackboard_graph(
            agents={n: a.config for n, a in agents.items()}, blackboard=blackboard, rounds=rounds, checkpointer=checkpointer,
        )
        return _CompiledWorkflow(graph, name="blackboard", extra_state={"round": 0})

    @staticmethod
    def debate(*, debaters: dict[str, Agent], judge: Agent, checkpointer: Any = None) -> _CompiledWorkflow:
        graph = build_debate_graph(debaters={n: a.config for n, a in debaters.items()}, judge=judge.config, checkpointer=checkpointer)
        return _CompiledWorkflow(graph, name="debate")

    @staticmethod
    def fanout(*, agent: Agent, checkpointer: Any = None) -> _FanoutWorkflow:
        from ..orchestration import build_fanout_graph

        return _FanoutWorkflow(build_fanout_graph(config=agent.config, checkpointer=checkpointer))

    @staticmethod
    def dag(*, steps: list[DAGStep], checkpointer: Any = None) -> _DagWorkflow:
        return _DagWorkflow(build_dag_graph(steps, checkpointer=checkpointer))

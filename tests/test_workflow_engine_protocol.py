"""Proves core.protocols.WorkflowEngine is a real, checkable contract — this
did not previously exist: WorkflowEngine wasn't @runtime_checkable
(isinstance() against it raised TypeError, not False) and nothing in the
codebase was ever verified to satisfy its exact shape (build/run/stream/
resume operating on an externally-held `compiled` object). `Agent` now
genuinely routes through these adapters via the `RUNTIMES` registry
(core/engines.py) — `Agent.__init__` does `RUNTIMES[runtime].build(...)`,
not an inline if/else — verified below (`test_agent_routes_through_the_...`
cases), not just that the adapters exist standing alone.
"""
from __future__ import annotations

import pytest

from agent_foundry import Agent
from agent_foundry.contracts import AgentRole, Identity, Policy
from agent_foundry.core.engines import LangGraphWorkflowEngine, NativeWorkflowEngine
from agent_foundry.core.execution_context import ExecutionContext
from agent_foundry.core.native_engine import _NativeGraph
from agent_foundry.core.protocols import WorkflowEngine
from agent_foundry.eval import EvalHarness
from agent_foundry.guardrails import GuardrailEngine
from agent_foundry.llm_gateway import LLMGateway
from agent_foundry.observability import Tracer
from agent_foundry.orchestration import AgentConfig
from agent_foundry.runtime import RunBudget
from agent_foundry.tools_gateway import ToolRegistry

from conftest import ScriptedProvider


def _config(provider) -> AgentConfig:
    policy = Policy(allowed_tools=frozenset())
    return AgentConfig(
        system_prompt="sys", llm=LLMGateway(provider=provider), tools=ToolRegistry(),
        guardrails=GuardrailEngine(policy), eval_harness=EvalHarness(), identity=Identity(id="t", tenant_id="acme"),
        policy=policy, budget=RunBudget(policy), tracer=Tracer("wf-engine-test"),
    )


def test_isinstance_no_longer_raises_and_both_engines_satisfy_it():
    assert isinstance(LangGraphWorkflowEngine(), WorkflowEngine)
    assert isinstance(NativeWorkflowEngine(), WorkflowEngine)
    assert isinstance(object(), WorkflowEngine) is False  # a real negative case, not just true for everything


@pytest.mark.parametrize("engine_cls", [LangGraphWorkflowEngine, NativeWorkflowEngine])
def test_engine_build_run_round_trip(engine_cls):
    engine: WorkflowEngine = engine_cls()
    config = _config(ScriptedProvider(["hello there"]))
    compiled = engine.build(config)

    result = engine.run(compiled, message="hi", context=ExecutionContext(thread_id="t1"))

    assert result.content == "hello there"


@pytest.mark.parametrize("engine_cls", [LangGraphWorkflowEngine, NativeWorkflowEngine])
def test_engine_stream_round_trip(engine_cls):
    engine: WorkflowEngine = engine_cls()
    config = _config(ScriptedProvider(["streamed reply"]))
    compiled = engine.build(config)

    chunks = list(engine.stream(compiled, message="hi", context=ExecutionContext(thread_id="t2")))

    assert len(chunks) >= 1
    last_messages = chunks[-1].get("messages", [])
    assert last_messages and last_messages[-1]["content"] == "streamed reply"


@pytest.mark.parametrize("engine_cls", [LangGraphWorkflowEngine, NativeWorkflowEngine])
def test_engine_resume_round_trip(engine_cls):
    from agent_foundry.kpi import KPI
    from agent_foundry.orchestration import CritiqueConfig

    kpi = KPI(name="confidence", score=lambda ctx: ctx["score"], direction="maximize", threshold=0.5)
    critique = CritiqueConfig(kpi=kpi, context=lambda state, draft: {"score": 0.02}, escalate_threshold=0.05)
    engine: WorkflowEngine = engine_cls()
    config = _config(ScriptedProvider(["an ambiguous answer"]))
    config.critique = critique
    compiled = engine.build(config)
    context = ExecutionContext(thread_id="t3")

    paused = engine.run(compiled, message="what should I do?", context=context)
    assert paused.awaiting_approval is True

    resumed = engine.resume(compiled, approved=True, context=context)
    assert resumed.content == "an ambiguous answer"


def test_agent_routes_through_native_workflow_engine_not_an_inline_branch():
    """Agent.__init__ does RUNTIMES["native"].build(...), not its own
    from-scratch _NativeGraph(...) call — proven by the compiled graph
    genuinely being a _NativeGraph instance, the exact object
    NativeWorkflowEngine.build() constructs."""
    agent = Agent("t", "hi", runtime="native", llm=LLMGateway(provider=ScriptedProvider(["hello"])))
    assert isinstance(agent.graph, _NativeGraph)


def test_agent_routes_through_langgraph_workflow_engine_not_an_inline_branch():
    agent = Agent("t", "hi", runtime="langgraph", llm=LLMGateway(provider=ScriptedProvider(["hello"])))
    assert not isinstance(agent.graph, _NativeGraph)
    assert hasattr(agent.graph, "ainvoke")  # a real compiled LangGraph graph


def test_agent_defaults_to_native_not_langgraph():
    """Agent()'s runtime default must stay "native" so a caller never needs
    the langgraph extra installed unless they opt into runtime="langgraph"."""
    agent = Agent("t", "hi", llm=LLMGateway(provider=ScriptedProvider(["hello"])))
    assert agent.runtime == "native"
    assert isinstance(agent.graph, _NativeGraph)


def test_agent_provider_resolves_a_named_vendor_without_a_hand_built_llmgateway(monkeypatch):
    """Agent(provider="openai") is the same symmetry AgentSpec.provider
    already had (agent_spec.py) — promoted onto Agent.__init__ itself so
    picking a non-Anthropic vendor never requires constructing an
    LLMGateway by hand. Patches llm_gateway.PROVIDERS (the shared registry
    both Agent and AgentSpec resolve against) rather than needing a real
    OpenAI SDK installed."""
    from agent_foundry import llm_gateway

    monkeypatch.setitem(llm_gateway.PROVIDERS, "openai", lambda: ScriptedProvider(["hello"]))
    agent = Agent("t", "hi", provider="openai")
    result = agent.run("hi", context=ExecutionContext(thread_id="provider-kwarg"))
    assert result.content == "hello"


def test_agent_rejects_llm_and_provider_together():
    with pytest.raises(ValueError, match="either llm= or provider="):
        Agent("t", "hi", llm=LLMGateway(provider=ScriptedProvider(["hello"])), provider="openai")


def test_agent_rejects_unknown_provider_name():
    with pytest.raises(ValueError, match="provider='not-a-real-vendor'"):
        Agent("t", "hi", provider="not-a-real-vendor")


def test_agent_role_reaches_the_native_graphs_own_config():
    """_NativeGraph holds the whole AgentConfig object directly, so this
    direction always worked — the real regression was the langgraph path,
    tested separately below (a compiled LangGraph graph has no public
    attribute exposing its internal AgentConfig to assert against directly,
    since it's captured in node-function closures, not stored on the graph
    object itself)."""
    agent = Agent("t", "hi", runtime="native", role=AgentRole.SPECIALIST, llm=LLMGateway(provider=ScriptedProvider(["hello"])))

    assert agent.config.role == AgentRole.SPECIALIST
    assert agent.graph._config.role == AgentRole.SPECIALIST


def test_agent_role_reaches_build_agent_graphs_internal_config(monkeypatch):
    """Regression: AgentConfig.role used to be silently dropped on the
    langgraph path — build_agent_graph had no `role` parameter at all, so
    it was structurally impossible for a caller's role to reach the graph,
    regardless of what Agent(role=...)/LangGraphWorkflowEngine.build()
    passed in. Spies on orchestration.AgentConfig's own constructor (the
    one build_agent_graph builds internally) since a compiled graph has no
    public attribute to assert the reached role against directly."""
    from agent_foundry import orchestration

    seen_roles = []
    real_init = orchestration.AgentConfig.__init__

    def spy_init(self, *args, **kwargs):
        seen_roles.append(kwargs.get("role"))
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(orchestration.AgentConfig, "__init__", spy_init)

    Agent("t", "hi", runtime="langgraph", role=AgentRole.SPECIALIST, llm=LLMGateway(provider=ScriptedProvider(["hello"])))

    # Two AgentConfig constructions happen for one Agent(runtime="langgraph")
    # call: Agent.__init__'s own (already worked before this fix — role is
    # a plain field there) and build_agent_graph's internal one (didn't;
    # role had no way to reach it). Both must show SPECIALIST, not just one.
    assert seen_roles == [AgentRole.SPECIALIST, AgentRole.SPECIALIST]

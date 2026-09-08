"""Proves core.protocols.WorkflowEngine is a real, checkable contract — this
did not previously exist: WorkflowEngine wasn't @runtime_checkable
(isinstance() against it raised TypeError, not False) and nothing in the
codebase was ever verified to satisfy its exact shape (build/run/stream/
resume operating on an externally-held `compiled` object). Agent itself
still calls build_agent_graph/_NativeGraph directly, unchanged — these
adapters (core/engines.py) are the seam's verification surface.
"""
from __future__ import annotations

import pytest

from agent_foundry.contracts import Identity, Policy
from agent_foundry.core.engines import LangGraphWorkflowEngine, NativeWorkflowEngine
from agent_foundry.core.execution_context import ExecutionContext
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

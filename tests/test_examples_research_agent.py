"""examples/research_agent — a RAG reference app. Verified the same way the
rest of this suite verifies LLM-touching code: a real Agent.run() through
real orchestration/eval code, against a ScriptedProvider instead of a live
API call (see conftest.ScriptedProvider's own docstring for why)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_foundry import EvalCase, run_eval
from agent_foundry.eval_dataset import EvalDataset
from agent_foundry.kpi import citation_correctness_kpi, composite_grounding_kpi
from agent_foundry.llm_gateway import LLMGateway

from conftest import ScriptedProvider
from examples.research_agent.agent import SOURCE_BY_TEXT, build_agent

_DATASET_PATH = Path(__file__).resolve().parent.parent / "examples" / "research_agent" / "eval_dataset.json"


def _tool_call_then_answer(answer: str):
    from agent_foundry.contracts import LLMResponse, ToolCall

    def call_search(messages, model):
        return LLMResponse(text="", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0,
                            tool_calls=[ToolCall(id="c1", name="search_docs", args={"query": "x"})])

    return [call_search, answer]


def test_eval_dataset_file_is_versioned_and_loads():
    dataset = EvalDataset.load(_DATASET_PATH)
    assert dataset.name == "research_agent"
    assert len(dataset.to_cases()) == 3


def test_research_agent_passes_its_own_eval_dataset():
    dataset = EvalDataset.load(_DATASET_PATH)
    responses = []
    for case, answer in zip(dataset.to_cases(), ["BudgetExceeded, per doc:1.", "Needs approval, per doc:2.", "The native runtime, per doc:3."]):
        responses.extend(_tool_call_then_answer(answer))
    provider = ScriptedProvider(responses)
    agent = build_agent(llm=LLMGateway(provider=provider))

    scorecard = run_eval(agent, dataset.to_cases(), dataset_name=dataset.name)

    ok, reasons = scorecard.passes({"task_success_rate_min": 1.0, "tool_accuracy_rate_min": 1.0})
    assert ok, reasons


def test_research_agent_citation_correctness_kpi_flags_a_fabricated_citation():
    """Not JSON-loadable (KPI isn't serializable) — built directly in Python,
    same as core.evalgate's own tests do for KPI-based cases."""
    citations = lambda text: re.findall(r"doc:\d+", text)  # noqa: E731
    sources = lambda ctx: list(SOURCE_BY_TEXT.values())  # noqa: E731
    kpi = citation_correctness_kpi("citations", citations=citations, sources=sources)

    grounded_case = EvalCase(input="How does cost enforcement work?", kpi=kpi, kpi_context=lambda result: {"output_text": result.content})
    fabricated_case = EvalCase(input="How does cost enforcement work?", kpi=kpi, kpi_context=lambda result: {"output_text": result.content})

    provider_ok = ScriptedProvider(_tool_call_then_answer("Enforced fail-closed, see doc:1."))
    agent_ok = build_agent(llm=LLMGateway(provider=provider_ok))
    scorecard_ok = run_eval(agent_ok, [grounded_case])

    provider_bad = ScriptedProvider(_tool_call_then_answer("Enforced fail-closed, see doc:99."))
    agent_bad = build_agent(llm=LLMGateway(provider=provider_bad))
    scorecard_bad = run_eval(agent_bad, [fabricated_case])

    assert scorecard_ok.cases[0].kpi_result.passed
    assert not scorecard_bad.cases[0].kpi_result.passed


def test_research_agent_composite_grounding_kpi_scores_a_real_reply():
    kpi = composite_grounding_kpi("grounded", references=lambda ctx: ctx["passages"])
    docs = [text for text, _source in [(t, s) for t, s in SOURCE_BY_TEXT.items()]]
    case = EvalCase(
        input="How does cost enforcement work?", kpi=kpi,
        kpi_context=lambda result: {"output_text": result.content, "passages": docs},
    )
    provider = ScriptedProvider(_tool_call_then_answer(docs[0]))  # verbatim-grounded reply
    agent = build_agent(llm=LLMGateway(provider=provider))

    scorecard = run_eval(agent, [case])

    assert scorecard.cases[0].kpi_result.passed


def test_agent_spec_yaml_resolves_the_same_search_docs_tool():
    """agent.yaml's dotted tool reference must resolve to the exact same
    module-level function agent.py's own build_agent() uses."""
    from agent_foundry.agent_spec import AgentSpec, resolve_tool
    from examples.research_agent.agent import search_docs

    spec = AgentSpec.from_yaml(Path(__file__).resolve().parent.parent / "examples" / "research_agent" / "agent.yaml")
    resolved = resolve_tool(spec.tools[0])

    assert resolved is search_docs
    assert resolved("cost limits")  # the seeded corpus is non-empty

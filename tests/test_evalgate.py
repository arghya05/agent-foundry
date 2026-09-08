"""Tests for run_eval/EvalCase/Scorecard — evaluation-as-release-gate,
scored from real Agent.run() calls, not mocked scorers.
"""
from __future__ import annotations

from agent_foundry import Agent, EvalCase, run_eval
from agent_foundry.kpi import KPI
from agent_foundry.llm_gateway import LLMGateway

from conftest import ScriptedProvider


def lookup_order(order_id: str) -> str:
    """Look up an order by id."""
    return f"order {order_id} shipped"


def test_run_eval_scores_task_success_and_tool_accuracy():
    provider = ScriptedProvider([
        "The wedding outfit is ready.",
        'CALL lookup_order {"order_id": "A100"}', "Your order shipped!",
    ])
    agent = Agent("shopper", "Help with orders.", tools=[lookup_order], llm=LLMGateway(provider=provider))
    cases = [
        EvalCase(input="find me a wedding outfit", expected_substring="wedding"),
        EvalCase(input="status of A100?", expected_tool="lookup_order", expected_substring="shipped"),
    ]

    scorecard = run_eval(agent, cases, dataset_name="shopping-gold-v1")

    assert scorecard.agent_name == "shopper"
    assert scorecard.task_success_rate == 1.0
    assert scorecard.tool_accuracy_rate == 1.0
    assert scorecard.error_rate == 0.0
    assert scorecard.avg_cost_usd == 0.0  # ScriptedProvider is free


def test_run_eval_flags_a_task_that_missed_the_expected_substring():
    provider = ScriptedProvider(["totally unrelated reply"])
    agent = Agent("shopper", "Help.", llm=LLMGateway(provider=provider))
    cases = [EvalCase(input="find me a wedding outfit", expected_substring="wedding")]

    scorecard = run_eval(agent, cases)

    assert scorecard.task_success_rate == 0.0
    assert scorecard.cases[0].ok is False


def test_run_eval_flags_the_wrong_tool_called():
    def other_tool() -> str:
        """An unrelated tool."""
        return "unrelated"

    provider = ScriptedProvider(["CALL other_tool {}", "done"])
    agent = Agent("shopper", "Help.", tools=[lookup_order, other_tool], llm=LLMGateway(provider=provider))
    cases = [EvalCase(input="status of A100?", expected_tool="lookup_order")]

    scorecard = run_eval(agent, cases)

    assert scorecard.tool_accuracy_rate == 0.0


def test_run_eval_scores_a_kpi_and_computes_groundedness_avg():
    provider = ScriptedProvider(["a grounded answer"])
    agent = Agent("shopper", "Help.", llm=LLMGateway(provider=provider))
    kpi = KPI(name="groundedness", score=lambda ctx: ctx["score"], direction="maximize", threshold=0.5)
    cases = [EvalCase(input="hi", kpi=kpi, kpi_context=lambda result: {"score": 0.9})]

    scorecard = run_eval(agent, cases)

    assert scorecard.groundedness_avg == 0.9
    assert scorecard.cases[0].kpi_result.passed is True


def test_run_eval_records_an_error_without_crashing_the_whole_run():
    class FlakyProvider:
        def complete(self, messages, *, model, tools=None, **kw):
            raise RuntimeError("provider outage")

    agent = Agent("shopper", "Help.", llm=LLMGateway(provider=FlakyProvider()))
    cases = [EvalCase(input="hi")]

    scorecard = run_eval(agent, cases)

    assert scorecard.error_rate == 1.0
    assert scorecard.cases[0].error is not None
    assert scorecard.task_success_rate == 0.0


def test_scorecard_passes_respects_thresholds():
    provider = ScriptedProvider(["yes wedding outfit found", "totally unrelated"])
    agent = Agent("shopper", "Help.", llm=LLMGateway(provider=provider))
    cases = [
        EvalCase(input="a", expected_substring="wedding"),
        EvalCase(input="b", expected_substring="wedding"),
    ]

    scorecard = run_eval(agent, cases)
    assert scorecard.task_success_rate == 0.5

    ok, reasons = scorecard.passes({"task_success_rate_min": 0.9})
    assert ok is False
    assert reasons and "task_success_rate_min" in reasons[0]

    ok2, reasons2 = scorecard.passes({"task_success_rate_min": 0.1})
    assert ok2 is True and reasons2 == []


def test_scorecard_passes_skips_thresholds_with_no_data():
    provider = ScriptedProvider(["hi"])
    agent = Agent("shopper", "Help.", llm=LLMGateway(provider=provider))
    scorecard = run_eval(agent, [EvalCase(input="hi")])  # no KPI used -> groundedness_avg is None

    ok, reasons = scorecard.passes({"groundedness_avg_min": 0.9})

    assert ok is True and reasons == []


def test_scorecard_compare_to_reports_deltas():
    provider_a = ScriptedProvider(["wedding outfit A"])
    provider_b = ScriptedProvider(["wedding outfit B"])
    agent_a = Agent("shopper", "Help.", llm=LLMGateway(provider=provider_a))
    agent_b = Agent("shopper", "Help.", llm=LLMGateway(provider=provider_b))
    case = [EvalCase(input="a", expected_substring="wedding")]

    baseline = run_eval(agent_a, case)
    candidate = run_eval(agent_b, case)

    deltas = candidate.compare_to(baseline)
    assert deltas["task_success_rate"] == 0.0  # both succeeded equally


def test_scorecard_render_produces_a_readable_summary():
    provider = ScriptedProvider(["wedding outfit"])
    agent = Agent("shopper", "Help.", llm=LLMGateway(provider=provider))
    scorecard = run_eval(agent, [EvalCase(input="a", expected_substring="wedding")])

    text = scorecard.render()

    assert "Task success" in text and "100.0%" in text
    assert "P95 latency" in text
    assert "Average cost" in text

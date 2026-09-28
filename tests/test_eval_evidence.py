"""Regression tests for evidence coverage and failure-cost integrity."""
from __future__ import annotations

import math

import pytest

from agent_foundry import Agent, EvalCase, ExecutionContext, run_eval
from agent_foundry.contracts import LLMResponse, Policy
from agent_foundry.core.evalgate import Scorecard, attribute_failure
from agent_foundry.eval_dataset import EvalDataset
from agent_foundry.kpi import KPI
from agent_foundry.llm_gateway import LLMGateway
from agent_foundry.runtime import RunBudget
from conftest import ScriptedProvider


def agent_with(*responses, runtime="native"):
    return Agent("evidence", "Answer the request.", runtime=runtime,
                 llm=LLMGateway(provider=ScriptedProvider(responses)))


def test_no_oracle_is_unmeasured_and_cannot_pass_quality_gate():
    score = run_eval(agent_with("done"), [EvalCase(input="hello")])
    case = score.cases[0]
    assert case.task_success is None and case.tool_accuracy is None
    assert not case.ok and attribute_failure(case) == "missing_oracle"
    assert score.task_success_rate is None
    assert score.tool_accuracy_rate is None
    assert score.trajectory_accuracy_rate is None
    assert score.passes({"task_success_rate_min": 0})[0] is False
    assert score.passes()[0] is False
    assert "not measured" in score.render().lower()


def test_requested_kpi_gate_fails_when_no_case_measures_it():
    score = run_eval(agent_with("yes"), [EvalCase(input="hi", expected_substring="yes")])
    assert score.passes({"groundedness_avg_min": .1})[0] is False


def test_partial_coverage_is_disclosed_and_requires_explicit_gate_tolerance():
    score = run_eval(agent_with("yes", "other"), [
        EvalCase(input="one", expected_substring="yes"), EvalCase(input="two")])
    assert score.task_success_rate == 1
    assert score.metric_coverage["task_success_rate"] == .5
    assert score.passes({"task_success_rate_min": .9})[0] is False
    assert score.passes({"task_success_rate_min": .9}, required_coverage=.5)[0] is True


def test_trajectory_exception_is_failure_not_vacuous_success():
    def fail(messages, model):
        raise RuntimeError("provider unavailable")
    score = run_eval(agent_with(fail), [EvalCase(input="hi", expected_tool_sequence=[])])
    assert score.error_rate == 1
    assert score.trajectory_accuracy_rate == 0


@pytest.mark.parametrize("runtime", ["native", "langgraph"])
@pytest.mark.parametrize("explicit_budget", [False, True])
def test_failure_retains_known_incremental_cost_and_marks_incompleteness(runtime, explicit_budget):
    def fail(messages, model):
        raise RuntimeError("failure after first billed response")

    def lookup():
        """Return a test value."""
        return "value"

    response = LLMResponse(text="CALL lookup {}", model="fixture", input_tokens=1,
                           output_tokens=1, cost_usd=.25)
    agent = Agent("evidence", "Use tools.", tools=[lookup], runtime=runtime,
                  llm=LLMGateway(provider=ScriptedProvider([response, fail])))
    context = ExecutionContext(thread_id="same-thread")
    budget = RunBudget(Policy(max_cost_usd_per_thread=10)) if explicit_budget else agent.config.budget
    if explicit_budget:
        context.budget = budget
    budget.spend(.1, thread_id="same-thread")
    score = run_eval(agent, [EvalCase(input="lookup", expected_substring="done", context=context)])
    assert score.cases[0].cost_usd == pytest.approx(.25)
    assert score.cases[0].cost_complete is False
    assert score.known_cost_usd == pytest.approx(.25)
    assert score.avg_cost_usd is None
    assert score.passes({"avg_cost_usd_max": 10})[0] is False


def test_reused_thread_does_not_count_previous_turn_cost_twice():
    replies = [LLMResponse(text="yes", model="fixture", input_tokens=1,
                            output_tokens=1, cost_usd=c) for c in (.1, .2)]
    context = ExecutionContext(thread_id="reused")
    score = run_eval(agent_with(*replies), [EvalCase(input="hi", expected_substring="yes", context=context)] * 2)
    assert [c.cost_usd for c in score.cases] == pytest.approx([.1, .2])
    assert score.avg_cost_usd == pytest.approx(.15)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_threshold_rejected(value):
    with pytest.raises(ValueError, match="finite"):
        Scorecard("a", "d").passes({"task_success_rate_min": value})


def test_nan_kpi_cannot_pass_or_enter_average():
    kpi = KPI(name="invalid", score=lambda _: math.nan, direction="maximize", threshold=.5)
    score = run_eval(agent_with("yes"), [EvalCase(input="hi", kpi=kpi)])
    assert score.cases[0].ok is False
    assert score.groundedness_avg is None
    assert score.passes({"groundedness_avg_min": 0})[0] is False


def test_empty_scorecard_cannot_pass_even_an_operational_gate():
    score = Scorecard("empty", "d")
    assert score.passes()[0] is False
    assert score.passes({"error_rate_max": 1})[0] is False


def test_unknown_metrics_survive_baseline_roundtrip_and_comparison(tmp_path):
    score = run_eval(agent_with("done"), [EvalCase(input="hi")])
    dataset = EvalDataset("unknown", "v1", [{"input": "hi"}])
    dataset.save_baseline(score, tmp_path)
    baseline = dataset.load_baseline(tmp_path, "unknown", "v1")
    assert baseline.task_success_rate is None
    assert baseline.measurement_schema_version == 2
    assert baseline.metric_coverage["task_success_rate"] == 0
    assert score.compare_to(baseline)["task_success_rate"] is None


@pytest.mark.parametrize("field", ["expected_substring", "expected_tool"])
def test_empty_text_expectation_is_not_a_valid_oracle(field):
    with pytest.raises(ValueError, match="empty"):
        EvalCase(input="hi", **{field: ""})

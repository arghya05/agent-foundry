"""EvalDataset — named/versioned eval-case files and Scorecard baseline
persistence (agent_foundry/eval_dataset.py). run_eval/Scorecard themselves
are core.evalgate's own, already covered by test_evalgate.py — these tests
only exercise the new load/save/versioning layer on top."""
from __future__ import annotations

import json

import pytest

from agent_foundry import Agent, run_eval
from agent_foundry.eval_dataset import BaselineScorecard, EvalDataset
from agent_foundry.llm_gateway import LLMGateway

from conftest import ScriptedProvider


def _flat_dataset_json():
    return [{"input": "hi", "name": "greet", "expected_substring": "hello"}]


def _versioned_dataset_dict():
    return {"name": "greetings", "version": "v1", "cases": _flat_dataset_json()}


def test_looks_versioned_distinguishes_flat_list_from_versioned_dict():
    assert EvalDataset.looks_versioned(_versioned_dataset_dict()) is True
    assert EvalDataset.looks_versioned(_flat_dataset_json()) is False


def test_load_reads_name_version_and_cases(tmp_path):
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(_versioned_dataset_dict()))

    dataset = EvalDataset.load(path)

    assert dataset.name == "greetings"
    assert dataset.version == "v1"
    assert dataset.cases == _flat_dataset_json()


def test_to_cases_builds_real_evalcase_objects_with_forbidden_tools_as_frozenset():
    dataset = EvalDataset(name="d", version="v1", cases=[
        {"input": "x", "forbidden_tools": ["issue_refund"]},
    ])

    cases = dataset.to_cases()

    assert cases[0].input == "x"
    assert cases[0].forbidden_tools == frozenset({"issue_refund"})


def test_save_and_load_baseline_round_trips_scorecard_metrics(tmp_path):
    provider = ScriptedProvider(["hello there"])
    agent = Agent("greeter", "Greet.", llm=LLMGateway(provider=provider))
    dataset = EvalDataset(name="greetings", version="v1", cases=_flat_dataset_json())
    scorecard = run_eval(agent, dataset.to_cases(), dataset_name=dataset.name)

    saved_path = dataset.save_baseline(scorecard, tmp_path)
    baseline = EvalDataset.load_baseline(tmp_path, "greetings", "v1")

    assert saved_path.exists()
    assert isinstance(baseline, BaselineScorecard)
    assert baseline.task_success_rate == scorecard.task_success_rate
    assert baseline.avg_cost_usd == scorecard.avg_cost_usd


def test_load_baseline_returns_none_when_no_baseline_saved_yet(tmp_path):
    assert EvalDataset.load_baseline(tmp_path, "nonexistent", "v1") is None


def test_saved_baseline_composes_with_scorecard_compare_to(tmp_path):
    """The actual point of BaselineScorecard: a real Scorecard.compare_to()
    call against a reloaded baseline works exactly like against a live
    Scorecard — compare_to only ever reads the seven metric properties via
    getattr, never .cases."""
    provider_a = ScriptedProvider(["hello there"])
    provider_b = ScriptedProvider(["goodbye"])
    dataset = EvalDataset(name="greetings", version="v1", cases=_flat_dataset_json())

    baseline_agent = Agent("greeter", "Greet.", llm=LLMGateway(provider=provider_a))
    baseline_scorecard = run_eval(baseline_agent, dataset.to_cases(), dataset_name=dataset.name)
    dataset.save_baseline(baseline_scorecard, tmp_path)

    regressed_agent = Agent("greeter", "Greet.", llm=LLMGateway(provider=provider_b))
    regressed_scorecard = run_eval(regressed_agent, dataset.to_cases(), dataset_name=dataset.name)
    baseline = EvalDataset.load_baseline(tmp_path, "greetings", "v1")

    deltas = regressed_scorecard.compare_to(baseline)
    assert deltas["task_success_rate"] == pytest.approx(-1.0)

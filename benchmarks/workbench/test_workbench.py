"""Offline contract tests; scripted calls are never reported as benchmark wins."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
import json
from pathlib import Path
import sys
from unittest.mock import patch

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ARMS, bootstrap, load_heldout_tasks, load_tasks, score
from adapter import Sandbox, run_task
from transport import Budget, BudgetExceeded, Transport, anthropic_turns


@pytest.fixture(scope="module", autouse=True)
def upstream():
    import os
    root = os.environ.get("WORKBENCH_ROOT", "/private/tmp/agent-foundry-workbench")
    if not Path(root).is_dir():
        pytest.skip("Pinned WorkBench checkout unavailable; set WORKBENCH_ROOT to run these offline adapter tests")
    previous = Path.cwd()
    try:
        bootstrap(root)
        yield
    finally:
        os.chdir(previous)


class Scripted:
    model = "offline-scripted"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def call(self, system, messages, tools):
        self.requests.append(copy.deepcopy((system, messages, tools)))
        return copy.deepcopy(next(self.responses))


def response(calls=()):
    return {"content": "" if calls else "Done.", "tool_calls": list(calls)}


def create_event(name="Adapter test", identifier="call-1"):
    return {"id": identifier, "name": "calendar_create_event", "arguments": {
        "event_name": name, "participant_email": "test@atlas.com",
        "event_start": "2023-10-02 12:00:00", "duration": "60"}}


@pytest.mark.parametrize("arm", ARMS)
def test_actual_runtime_executes_and_scores_tool_calls(arm):
    transport = Scripted([response([create_event()]), response()])
    result = run_task(arm, "Create the adapter test meeting.", transport)
    assert result["error"] == ""
    assert len(result["function_calls"]) == 1
    task = {"task": "Create the adapter test meeting.", "outcome": [
        "calendar.create_event.func(event_name='Adapter test', participant_email='test@atlas.com', "
        "event_start='2023-10-02 12:00:00', duration='60')"]}
    assert score(task, result["function_calls"])["correct"]
    assert len(transport.requests) == 2


@pytest.mark.parametrize("profile", ["baseline", "grounded"])
def test_prompts_and_schemas_match_across_all_arms(profile):
    requests = []
    for arm in ARMS:
        transport = Scripted([response([create_event()]), response()])
        assert run_task(arm, "Create a meeting.", transport, instruction_profile=profile)["error"] == ""
        requests.append(transport.requests)
    assert requests[0] == requests[1] == requests[2]


def test_prompt_matches_published_frontier_confirmation_setting():
    from adapter import benchmark_tools
    from src.evals import agent as official
    from src.evals.metrics import meta_path_for_results

    published = json.loads(Path("retro/data/model_results.json").read_text())
    leader = max(published["models"].values(), key=lambda row: row["correct"])
    metadata = json.loads(Path(meta_path_for_results(leader["sources"]["calendar"])).read_text())
    expected = Scripted([response()])

    def call(route, system, turns, schemas, temperature):
        return expected.call(system, turns, schemas)

    with patch.object(official, "resolve_route", return_value=None), \
            patch.object(official, "_call_llm_structured_with_retry", call):
        official.run_agent_structured(
            expected.model, benchmark_tools(), "Complete the synthetic task.",
            metadata["datetime_prefix"], act_without_confirmation=metadata["act_without_confirmation"],
        )
    for arm in ARMS:
        transport = Scripted([response()])
        assert run_task(arm, "Complete the synthetic task.", transport)["error"] == ""
        assert transport.requests == expected.requests


@pytest.mark.parametrize("arm", ARMS)
def test_unknown_tool_cannot_disappear_from_scoring_after_recovery(arm):
    unknown = {"id": "unknown", "name": "calendar_nonexistent", "arguments": {}}
    transport = Scripted([response([unknown]), response([create_event()]), response()])
    result = run_task(arm, "Create the adapter test meeting.", transport)
    task = {"task": "Create the adapter test meeting.", "outcome": [
        "calendar.create_event.func(event_name='Adapter test', participant_email='test@atlas.com', "
        "event_start='2023-10-02 12:00:00', duration='60')"]}
    # A valid later mutation must remain observable, but the official scorer
    # rejects trajectories containing unknown tools even when end state matches.
    assert len(result["function_calls"]) == 1
    from src.evals.actions import convert_intermediate_step_to_function_call
    official_actions = [convert_intermediate_step_to_function_call(unknown["name"], {})]
    official_actions.extend(result["function_calls"])
    assert not score(task, official_actions)["correct"]
    assert not score(task, result["function_calls"], result["error"])["correct"]
    assert result["error"] == "UnknownToolCall"
    assert result["invalid_tool_calls"] == [{"id": "unknown", "name": "calendar_nonexistent"}]


@pytest.mark.parametrize("arm", ARMS)
def test_mutations_preserve_model_order(arm):
    transport = Scripted([response([create_event("First", "a"), create_event("Second", "b")]), response()])
    result = run_task(arm, "Create two meetings.", transport)
    assert result["error"] == ""
    assert len(result["function_calls"]) == 2
    assert 'event_name="First"' in result["function_calls"][0]
    assert 'event_name="Second"' in result["function_calls"][1]


def test_sandbox_binding_across_threads_and_task_isolation():
    from src.tools.calendar import create_event as official_create
    from src.tools.state import get_state
    before = len(get_state().calendar_events)
    sandbox, another = Sandbox(), Sandbox()
    wrapped = sandbox.wrap(official_create)
    with ThreadPoolExecutor(1) as pool:
        pool.submit(wrapped, **create_event()["arguments"]).result()
    assert len(sandbox.state.calendar_events) == before + 1
    assert len(another.state.calendar_events) == before
    assert len(get_state().calendar_events) == before
    with ThreadPoolExecutor(1) as pool:
        assert pool.submit(lambda: len(get_state().calendar_events)).result() == before


def test_tasks_are_fixed_and_stratified():
    tasks = load_tasks(2)
    assert tasks == load_tasks(2)
    assert len(tasks) == 12
    assert len({t["domain"] for t in tasks}) == 6
    assert len(load_tasks(0)) == 690


def test_heldout_sample_is_reproducible_stratified_and_disjoint_from_development():
    from collections import Counter
    development = {t["id"] for t in load_tasks(2)}
    heldout = load_heldout_tasks(2, 10)
    assert heldout == load_heldout_tasks(2, 10)
    assert len(heldout) == len({t["id"] for t in heldout}) == 60
    assert not development.intersection(t["id"] for t in heldout)
    assert set(Counter(t["domain"] for t in heldout).values()) == {10}
    assert len(load_heldout_tasks(2)) == 678
    next_slice = load_heldout_tasks(2, 10, offset_per_domain=10)
    assert not {t["id"] for t in heldout}.intersection(t["id"] for t in next_slice)
    assert not development.intersection(t["id"] for t in next_slice)
    assert len(next_slice) == 60
    with pytest.raises(ValueError):
        load_heldout_tasks(2, 1000)


def test_budget_survives_restart_and_keeps_unknown_reservations(tmp_path):
    path = tmp_path / "ledger.json"
    budget = Budget(path, 1)
    request = budget.reserve(.7, "task")
    with pytest.raises(BudgetExceeded):
        Budget(path, 1).reserve(.4, "other-task")
    budget.settle(request, .1, {"input_tokens": 1})
    resumed = Budget(path, 1)
    resumed.reserve(.8, "second-task")
    assert float(Budget(path, 1).used) == .9
    with pytest.raises(ValueError):
        Budget(path, 2)


@pytest.mark.parametrize("provider", ["openai", "anthropic"])
def test_network_wire_usage_and_no_secret_in_trace(provider, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-secret-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-secret-anthropic")
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "workspace-test")
    config = {"model": "test", "options": {}, "context_tokens": 1000,
              "input_usd_per_million": 1, "output_usd_per_million": 5}
    captured = []

    def server(request):
        captured.append(json.loads(request.content))
        if provider == "openai":
            assert request.url.path == "/v1/responses"
            raw = {"usage": {"input_tokens": 100, "output_tokens": 10}, "status": "completed",
                   "output": [{"type": "message", "content": [{"type": "output_text", "text": "Done"}]}]}
        else:
            assert request.headers["anthropic-workspace-id"] == "workspace-test"
            raw = {"usage": {"input_tokens": 100, "output_tokens": 10},
                   "stop_reason": "end_turn", "content": [{"type": "text", "text": "Done"}]}
        return httpx.Response(200, json=raw)

    budget = Budget(tmp_path / "ledger.json", 1)
    transport = Transport(provider, config, budget, "attempt", client=httpx.Client(transport=httpx.MockTransport(server)))
    result = transport.call("system", [{"role": "user", "content": "task"}], [])
    assert result["content"] == "Done"
    assert float(budget.used) == .00015
    assert "offline-secret" not in json.dumps(transport.traces)
    assert len(captured) == 1


def test_responses_replays_reasoning_and_tool_outputs_without_mutating_schemas(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unused")
    reasoning = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque"}
    call = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "test_tool", "arguments": "{}"}
    captured = []

    def server(request):
        payload = json.loads(request.content)
        captured.append(payload)
        assert payload["store"] is False
        assert payload["reasoning"] == {"effort": "low"}
        assert payload["tools"][0]["strict"] is False
        return httpx.Response(200, json={"status": "completed", "usage": {"input_tokens": 100, "output_tokens": 10},
                                       "output": [reasoning, call] if len(captured) == 1 else []})

    config = {"model": "test", "options": {"reasoning": {"effort": "low"}}, "context_tokens": 1000,
              "input_usd_per_million": 1, "output_usd_per_million": 5}
    transport = Transport("openai", config, Budget(tmp_path / "ledger.json", 1), "replay",
                          client=httpx.Client(transport=httpx.MockTransport(server)))
    tools = [{"type": "function", "function": {"name": "test_tool", "description": "test", "parameters": {"type": "object"}}}]
    initial = [{"role": "user", "content": "task"}]
    result = transport.call("system", initial, tools)
    assert result["tool_calls"] == [{"id": "call_1", "name": "test_tool", "arguments": {}}]
    transport.call("system", [*initial, {"role": "assistant", "tool_calls": [{"id": "call_1"}]},
                              {"role": "tool", "tool_call_id": "call_1", "content": "done"}], tools)
    assert captured[1]["input"] == [*initial, reasoning, call,
                                    {"type": "function_call_output", "call_id": "call_1", "output": "done"}]
    assert "strict" not in tools[0]["function"]
    transport.close()


def test_request_blocked_before_network_when_budget_is_insufficient(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unused")
    config = {"model": "test", "options": {}, "context_tokens": 1000000,
              "input_usd_per_million": 2, "output_usd_per_million": 10}
    transport = Transport("openai", config, Budget(tmp_path / "ledger.json", .01), "attempt",
                          client=httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("Network called"))))
    with pytest.raises(BudgetExceeded):
        transport.call("system", [{"role": "user", "content": "task"}], [])


def test_anthropic_preserves_thinking_and_groups_tool_results():
    blocks = [{"type": "thinking", "thinking": "opaque", "signature": "sig"},
              {"type": "tool_use", "id": "a", "name": "tool", "input": {}},
              {"type": "tool_use", "id": "b", "name": "tool", "input": {}}]
    messages = [{"role": "assistant", "tool_calls": [{"id": "a"}, {"id": "b"}]},
                {"role": "tool", "tool_call_id": "a", "content": "one"},
                {"role": "tool", "tool_call_id": "b", "content": "two"}]
    result = anthropic_turns(messages, {("a", "b"): blocks})
    assert result[0]["content"] == blocks
    assert len(result) == 2
    assert len(result[1]["content"]) == 2


def test_network_failure_is_not_retried_or_accounted_as_free(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unused")
    config = {"model": "test", "options": {}, "context_tokens": 1000,
              "input_usd_per_million": 1, "output_usd_per_million": 5}
    calls = []

    def server(request):
        calls.append(request)
        raise httpx.ReadTimeout("simulated failure")

    budget = Budget(tmp_path / "ledger.json", 1)
    transport = Transport("openai", config, budget, "attempt", client=httpx.Client(transport=httpx.MockTransport(server)))
    with pytest.raises(httpx.ReadTimeout):
        transport.call("system", [{"role": "user", "content": "task"}], [])
    assert len(calls) == 1
    assert budget.used > 0
    assert transport.fatal_error == "ReadTimeout"


def test_http_failure_records_redacted_diagnostic_and_preserves_reservation(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-secret-openai")
    config = {"model": "test", "options": {}, "context_tokens": 1000,
              "input_usd_per_million": 1, "output_usd_per_million": 5}

    def server(request):
        return httpx.Response(400, json={"error": {
            "type": "invalid_request_error", "code": "billing_disabled",
            "message": "No credit: offline-secret-openai sk-proj-partial***XYZ",
            "headers": {"Authorization": "never include this"},
        }})

    budget = Budget(tmp_path / "ledger.json", 1)
    transport = Transport("openai", config, budget, "diagnostic",
                          client=httpx.Client(transport=httpx.MockTransport(server)))
    with pytest.raises(httpx.HTTPStatusError):
        transport.call("system", [{"role": "user", "content": "task"}], [])
    diagnostic = transport.traces[0]["provider_error"]
    assert diagnostic["code"] == "billing_disabled"
    assert diagnostic["message"] == "No credit: [REDACTED] [REDACTED]"
    assert "headers" not in diagnostic
    assert "offline-secret-openai" not in json.dumps(transport.traces)
    assert budget.data["requests"][0]["status"] == "unknown_or_pending"
    assert budget.used > 0
    assert transport.fatal_error == "HTTPStatusError"
    transport.close()


def test_campaign_records_failures_and_resume_does_not_repeat_calls(tmp_path, monkeypatch):
    from argparse import Namespace
    import dotenv
    import transport as transport_module
    from run import live
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    monkeypatch.setenv("OPENAI_API_KEY", "offline-only")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "offline-only")
    requests = []

    def server(request):
        requests.append(request.url.host)
        if request.url.host == "api.openai.com":
            return httpx.Response(200, json={"usage": {"input_tokens": 100, "output_tokens": 10}, "status": "completed",
                                            "output": [{"type": "message", "content": [{"type": "output_text", "text": "Done"}]}]})
        return httpx.Response(200, json={"usage": {"input_tokens": 100, "output_tokens": 10},
                                        "stop_reason": "end_turn", "content": [{"type": "text", "text": "Done"}]})

    original = transport_module.Transport

    def mock_transport(*args, **kwargs):
        return original(*args, **kwargs, client=httpx.Client(transport=httpx.MockTransport(server)))

    monkeypatch.setattr(transport_module, "Transport", mock_transport)
    root = Path.cwd()
    args = Namespace(ledger=str(tmp_path / "ledger.json"), max_spend_usd=25, split="development",
                     per_domain=2, seed=1, repetitions=1, max_calls=20, max_output_tokens=2048,
                     timeout_s=600, providers=["openai", "anthropic"])
    config = {"model": "test", "options": {}, "context_tokens": 1000,
              "input_usd_per_million": 1, "output_usd_per_million": 5}
    task = {"id": "synthetic:0", "domain": "synthetic", "task": "Create a test meeting.",
            "outcome": ["calendar.create_event.func(event_name='Test', participant_email='test@atlas.com', "
                        "event_start='2023-10-02 12:00:00', duration='60')"]}
    output = tmp_path / "results"
    live(root, [task], args, {"openai": config, "anthropic": config}, output)
    summary = json.loads((output / "summary.json").read_text())
    assert summary["complete"]
    assert summary["rows_finished"] == 6
    assert all(group["correct"] == 0 for group in summary["aggregates"].values())
    assert len(requests) == 6
    live(root, [task], args, {"openai": config, "anthropic": config}, output)
    assert len(requests) == 6
    assert len(list((output / "attempts").glob("*.json"))) == 6

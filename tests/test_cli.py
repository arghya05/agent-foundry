"""Tests for the `foundry` CLI (agent_foundry/cli.py) — every subcommand is
thin composition over an existing module (scaffold, evalgate, serve), so
these mostly prove the wiring, not reimplement those modules' own tests.
"""
import json
import os
import subprocess
import sys
import tempfile

import pytest

from agent_foundry import cli
from agent_foundry.contracts import LLMResponse
from agent_foundry.llm_gateway import LLMGateway


class _StaticProvider:
    def complete(self, messages, *, model, tools=None, **kw):
        return LLMResponse(text="Refund processed.", model=model, input_tokens=1, output_tokens=1, cost_usd=0.001)


def _write_eval_script(tmp: str) -> str:
    path = os.path.join(tmp, "eval_demo.py")
    with open(path, "w") as f:
        f.write(
            "from agent_foundry import Agent\n"
            "from agent_foundry.contracts import LLMResponse\n"
            "from agent_foundry.llm_gateway import LLMGateway\n\n"
            "class StaticProvider:\n"
            "    def complete(self, messages, *, model, tools=None, **kw):\n"
            "        return LLMResponse(text='Refund processed.', model=model, input_tokens=1, output_tokens=1, cost_usd=0.001)\n\n"
            "agent = Agent('refunds', 'Handle refunds.', llm=LLMGateway(provider=StaticProvider()))\n"
        )
    return path


def test_cli_init_scaffolds_a_runnable_agent():
    with tempfile.TemporaryDirectory() as tmp:
        cli.main(["init", "sales_agent", "--directory", tmp, "--tools", "lookup_lead"])
        agent_path = os.path.join(tmp, "agents", "sales_agent.py")
        assert os.path.exists(agent_path)
        result = subprocess.run([sys.executable, "-m", "py_compile", agent_path], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_cli_eval_prints_a_scorecard_and_exits_zero_when_thresholds_pass(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        script = _write_eval_script(tmp)
        dataset = os.path.join(tmp, "dataset.json")
        with open(dataset, "w") as f:
            json.dump([{"input": "please refund order A100", "expected_substring": "processed"}], f)

        cli.main(["eval", script, dataset, "--thresholds", '{"task_success_rate_min": 1.0}'])

        out = capsys.readouterr().out
        assert "Task success        100.0%" in out


def test_cli_eval_exits_nonzero_when_a_threshold_fails():
    with tempfile.TemporaryDirectory() as tmp:
        script = _write_eval_script(tmp)
        dataset = os.path.join(tmp, "dataset.json")
        with open(dataset, "w") as f:
            json.dump([{"input": "please refund order A100", "expected_substring": "processed"}], f)

        with pytest.raises(SystemExit) as exc_info:
            cli.main(["eval", script, dataset, "--thresholds", '{"avg_cost_usd_max": 0.0001}'])
        assert exc_info.value.code == 1


def test_cli_inspect_reports_the_installed_version(capsys):
    from agent_foundry import __version__

    cli.main(["inspect"])
    out = capsys.readouterr().out
    assert __version__ in out
    assert "optional extras:" in out


def test_cli_trace_tails_the_last_n_lines(capsys):
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "trace.jsonl")
        with open(path, "w") as f:
            for i in range(5):
                f.write(json.dumps({"metric": f"event_{i}"}) + "\n")

        cli.main(["trace", path, "-n", "2"])

        lines = capsys.readouterr().out.strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["metric"] == "event_3"
        assert json.loads(lines[1])["metric"] == "event_4"


def test_cli_serve_builds_an_app_from_a_script_top_level_graph(monkeypatch):
    """cmd_serve falls back to build_http_app(graph) when the script has no
    top-level `app` — proven without actually starting a server by
    monkeypatching uvicorn.run to capture what it was handed."""
    with tempfile.TemporaryDirectory() as tmp:
        script = os.path.join(tmp, "serve_demo.py")
        with open(script, "w") as f:
            f.write(
                "from agent_foundry.contracts import Identity, LLMResponse, Policy\n"
                "from agent_foundry.eval import EvalHarness\n"
                "from agent_foundry.guardrails import GuardrailEngine\n"
                "from agent_foundry.llm_gateway import LLMGateway\n"
                "from agent_foundry.observability import Tracer\n"
                "from agent_foundry.orchestration import build_agent_graph\n"
                "from agent_foundry.runtime import RunBudget\n"
                "from agent_foundry.tools_gateway import ToolRegistry\n\n"
                "class StaticProvider:\n"
                "    def complete(self, messages, *, model, tools=None, **kw):\n"
                "        return LLMResponse(text='ok', model=model, input_tokens=1, output_tokens=1, cost_usd=0.0)\n\n"
                "identity = Identity(id='a', tenant_id='acme')\n"
                "policy = Policy(allowed_tools=frozenset())\n"
                "graph = build_agent_graph(system_prompt='sys', llm=LLMGateway(provider=StaticProvider()), tools=ToolRegistry(),\n"
                "    guardrails=GuardrailEngine(policy), eval_harness=EvalHarness(), identity=identity, policy=policy,\n"
                "    budget=RunBudget(policy), tracer=Tracer('serve-test'))\n"
            )

        captured = {}

        def fake_run(app, *, host, port):
            captured["app"] = app

        monkeypatch.setattr("uvicorn.run", fake_run)
        cli.main(["serve", script, "--port", "9", "--allow-unauthenticated-demo"])

        assert captured["app"] is not None
        assert captured["app"].title  # a real FastAPI app, not a stub


def test_cli_run_spec_builds_and_runs_an_agent(tmp_path, monkeypatch, capsys):
    from agent_foundry import agent_spec

    monkeypatch.setitem(agent_spec._PROVIDERS, "anthropic", lambda: _StaticProvider())
    spec_path = tmp_path / "agent.json"
    spec_path.write_text(json.dumps({"name": "cli-agent", "instructions": "Help.", "provider": "anthropic"}))

    cli.main(["run", "--spec", str(spec_path), "--message", "hi"])

    assert "Refund processed." in capsys.readouterr().out


def test_cli_run_spec_without_message_errors():
    with pytest.raises(SystemExit, match="--message"):
        cli.main(["run", "--spec", "irrelevant.json"])


def test_cli_run_without_script_or_spec_errors():
    with pytest.raises(SystemExit, match="script or --spec"):
        cli.main(["run"])

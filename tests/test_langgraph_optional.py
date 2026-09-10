"""Real proof that `pip install agent-foundry` (no [langgraph] extra) is a
complete, working agent with zero LangGraph installed — Agent()'s runtime
default is "native", so this needs no runtime= kwarg at all — not just that
orchestration.py's imports "look lazy" on a source read. Builds a throwaway
venv (uv venv) with agent-foundry installed but LangGraph deliberately
absent, and runs a real script inside it.

Slower (builds a venv, installs the package) — marked integration, same as
this suite's other real-external-process tests, but still runs by default
(see pytest.ini)."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parent.parent

_SCRIPT = '''
import sys

try:
    import langgraph  # noqa: F401
    print("LANGGRAPH_UNEXPECTEDLY_PRESENT")
    sys.exit(1)
except ModuleNotFoundError:
    pass

import agent_foundry  # noqa: F401 — the actual test: this import alone must not need langgraph
from agent_foundry import Agent, ExecutionContext
from agent_foundry.contracts import LLMResponse
from agent_foundry.llm_gateway import LLMGateway


class ScriptedProvider:
    def __init__(self, responses):
        self._responses = list(responses)

    def complete(self, messages, *, model, tools=None, **kw):
        return LLMResponse(text=self._responses.pop(0), model=model, input_tokens=1, output_tokens=1, cost_usd=0.0)


agent = Agent("t", "Chat.", llm=LLMGateway(provider=ScriptedProvider(["hello"])))  # no runtime= — proves the default itself needs no langgraph
assert agent.runtime == "native", agent.runtime
result = agent.run("hi", context=ExecutionContext(thread_id="langgraph-optional-smoke"))
assert result.content == "hello", result.content
print("NATIVE_RUNTIME_OK")

try:
    Agent("t2", "Chat.", runtime="langgraph", llm=LLMGateway(provider=ScriptedProvider(["hello"])))
    print("LANGGRAPH_RUNTIME_DID_NOT_RAISE")
    sys.exit(1)
except ModuleNotFoundError as e:
    assert "langgraph" in str(e).lower(), str(e)
    print("LANGGRAPH_RUNTIME_RAISED_CLEANLY")
'''


def _uv_available() -> bool:
    return shutil.which("uv") is not None


@pytest.mark.skipif(not _uv_available(), reason="uv not installed — can't build an isolated venv to verify against")
def test_import_and_native_runtime_work_without_langgraph_installed():
    with tempfile.TemporaryDirectory() as tmp:
        venv_dir = Path(tmp) / "venv"
        subprocess.run(["uv", "venv", str(venv_dir)], check=True, capture_output=True, text=True)
        python = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")

        install = subprocess.run(
            ["uv", "pip", "install", "--python", str(python), "-e", str(REPO_ROOT)],
            capture_output=True, text=True,
        )
        assert install.returncode == 0, f"install failed:\n{install.stdout}\n{install.stderr}"

        run = subprocess.run([str(python), "-c", _SCRIPT], capture_output=True, text=True)
        assert run.returncode == 0, f"stdout:\n{run.stdout}\nstderr:\n{run.stderr}"
        assert "NATIVE_RUNTIME_OK" in run.stdout
        assert "LANGGRAPH_RUNTIME_RAISED_CLEANLY" in run.stdout

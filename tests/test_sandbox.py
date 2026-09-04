import concurrent.futures

import pytest

from agent_foundry.contracts import Identity, Policy
from agent_foundry.sandbox import code_execution_tool, run_sandboxed
from agent_foundry.tools_gateway import ToolRegistry


def test_run_sandboxed_returns_result_variable():
    assert run_sandboxed("result = sum([1, 2, 3])") == "6"


def test_run_sandboxed_blocks_import():
    # the restricted namespace has no __import__, so the `import` statement
    # itself fails before any os.* call is reachable
    with pytest.raises(ImportError):
        run_sandboxed("import os\nresult = os.getcwd()")


def test_run_sandboxed_blocks_open():
    with pytest.raises(NameError):
        run_sandboxed("result = open('/etc/passwd').read()")


def test_run_sandboxed_enforces_timeout():
    # bounded, not `while True` — an abandoned ThreadPoolExecutor worker is
    # non-daemon and keeps running after the timeout fires, so an unbounded
    # loop here would block the whole test process from ever exiting
    with pytest.raises(concurrent.futures.TimeoutError):
        run_sandboxed("total = 0\nfor i in range(50_000_000):\n    total += i\nresult = total", timeout_s=0.01)


def test_code_execution_tool_composes_through_registry_rbac():
    tool = code_execution_tool(timeout_s=1.0)
    registry = ToolRegistry()
    registry.register(tool)
    identity = Identity(id="t", tenant_id="acme")
    policy = Policy(allowed_tools=frozenset({"run_python"}))
    result = registry.invoke("run_python", {"code": "result = 6 * 7"}, identity=identity, policy=policy)
    assert result.ok and result.output == "42"

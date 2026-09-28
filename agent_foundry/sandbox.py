"""Legacy restricted-builtins helper for trusted Python snippets.

This executes inside the hosting process. It provides no process, filesystem,
network or hostile-code isolation, and does not prevent Python introspection.
The thread timeout cannot terminate running code and may wait for it to finish.
Use an independently isolated executor for untrusted or generated code.
"""
from __future__ import annotations

import builtins as _builtins
from typing import Any

from .contracts import ToolSpec
from .runtime import with_timeout

_SAFE_NAMES = (
    "abs", "all", "any", "bool", "dict", "enumerate", "float", "int", "len",
    "list", "max", "min", "range", "round", "sorted", "str", "sum", "tuple", "zip",
)
_SAFE_BUILTINS = {name: getattr(_builtins, name) for name in _SAFE_NAMES}


def run_sandboxed(code: str, *, timeout_s: float = 5.0) -> str:
    """Execute trusted code with restricted builtins and return str(scope['result']).

    Restricting builtins does not block introspection or establish a sandbox.
    """

    def _exec() -> str:
        scope: dict[str, Any] = {"__builtins__": _SAFE_BUILTINS}
        exec(code, scope)  # noqa: S102 — this is the sandbox itself
        return str(scope.get("result", ""))

    return with_timeout(_exec, seconds=timeout_s)


def code_execution_tool(*, timeout_s: float = 5.0) -> ToolSpec:
    """A registrable ToolSpec: the agent writes Python that assigns its answer to
    a variable named `result`."""

    def run_python(code: str) -> str:
        return run_sandboxed(code, timeout_s=timeout_s)

    return ToolSpec(
        name="run_python",
        description="Run a trusted Python snippet with restricted builtins. This is not an isolation boundary. Assign the answer to a variable named `result`.",
        parameters={"code": "string"},
        fn=run_python,
    )

"""Execution sandbox — runs untrusted code with a restricted builtins namespace and
a wall-clock timeout. This closes both the "execution sandbox" (runtime harness)
and "code execution tool" (tools gateway) gaps with one primitive: run_sandboxed()
is the runtime mechanism, code_execution_tool() is the same thing wrapped as a
ToolSpec an agent can call.

Honesty check: this is process-level isolation only — a restricted namespace plus
a timeout, no seccomp, no container, no VM. It stops accidental misuse (an
infinite loop, a stray `import os`), not a deliberately hostile actor. Swap
run_sandboxed() for gVisor/Firecracker/a Docker-per-call before trusting genuinely
untrusted input in production.
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
    """Executes `code` with only the names in _SAFE_BUILTINS available (no import,
    no open, no exec/eval, no dunder access) and returns str(scope["result"])."""

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
        description="Run a short Python snippet in a restricted sandbox (no imports, no file/network access). Assign the answer to a variable named `result`.",
        parameters={"code": "string"},
        fn=run_python,
    )

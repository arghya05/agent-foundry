"""Core — evaluation-as-release-gate: run a named dataset of cases against an
Agent and get back a Scorecard with a pass/fail regression gate — the
Python API `foundry eval` (agent_foundry/cli.py) calls.

Built entirely on what already exists: KPI/KPIResult (kpi.py, unchanged) for
groundedness-style scoring, AgentConfig.budget.cost_usd_for() for real
per-case cost, orchestration._get_all_tool_calls for real tool-call
detection (same reused-not-reimplemented reasoning as native_engine.py).
Nothing here is a mock scorer — every metric is measured from a real
Agent.run() call, including the trajectory checks (expected_tool_sequence/
expected_args/forbidden_tools/max_tool_calls/must_request_approval) below —
"was this the CORRECT trajectory," not just "did the final answer/tool
end up right."
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from ..kpi import KPI, KPIResult
from ..orchestration import _get_all_tool_calls
from .agent import Agent
from .execution_context import ExecutionContext
from .result import RunResult


class ScorecardLike(Protocol):
    """Exactly the properties Scorecard.compare_to() reads — a real Scorecard
    satisfies this structurally, and so does eval_dataset.BaselineScorecard
    (a saved run's metrics reloaded from disk, with no .cases to reconstruct
    a full Scorecard from). compare_to() takes this instead of the concrete
    Scorecard class so a reloaded baseline can be compared against without
    needing to fake up a Scorecard it isn't."""

    @property
    def task_success_rate(self) -> float: ...
    @property
    def tool_accuracy_rate(self) -> float: ...
    @property
    def trajectory_accuracy_rate(self) -> float: ...
    @property
    def groundedness_avg(self) -> float | None: ...
    @property
    def p95_latency_ms(self) -> float: ...
    @property
    def avg_cost_usd(self) -> float: ...
    @property
    def error_rate(self) -> float: ...


_THRESHOLD_KINDS = {
    "task_success_rate_min": "min",
    "tool_accuracy_rate_min": "min",
    "trajectory_accuracy_rate_min": "min",
    "groundedness_avg_min": "min",
    "p95_latency_ms_max": "max",
    "avg_cost_usd_max": "max",
    "error_rate_max": "max",
}


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * pct
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return s[int(k)]
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _called_tool_names(messages: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for m in messages:
        if m.get("role") == "assistant":
            names.update(name for name, _args, _id in _get_all_tool_calls(m))
    return names


def _tool_calls_in_order(messages: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
    """Every (tool_name, args) pair in call order across the whole
    transcript — same _get_all_tool_calls _called_tool_names above already
    reuses, kept in ORDER and with args instead of collapsed to a name set,
    for the sequence/args trajectory checks below."""
    calls: list[tuple[str, dict[str, Any]]] = []
    for m in messages:
        if m.get("role") == "assistant":
            calls.extend((name, args) for name, args, _id in _get_all_tool_calls(m))
    return calls


def _has_trajectory_expectations(case: "EvalCase") -> bool:
    return (
        case.expected_tool_sequence is not None or bool(case.expected_args) or bool(case.forbidden_tools)
        or case.max_tool_calls is not None or case.must_request_approval is not None
    )


def _check_trajectory(case: "EvalCase", result: RunResult) -> list[str]:
    """Trajectory checks beyond "was task_success/expected_tool satisfied":
    the actual SEQUENCE of tool calls, per-tool argument constraints, tools
    that must never be called, a ceiling on total calls, and whether an
    approval/interrupt was (or wasn't) requested — all measured from the
    same real Agent.run() result every other metric here uses, not a mock.
    Returns a list of human-readable violations; empty means the
    trajectory matched every expectation `case` declared."""
    errors: list[str] = []
    calls = _tool_calls_in_order(result.messages)
    names_in_order = [name for name, _args in calls]

    if case.expected_tool_sequence is not None and names_in_order != list(case.expected_tool_sequence):
        errors.append(f"expected tool sequence {list(case.expected_tool_sequence)}, got {names_in_order}")

    for tool_name, expected_args in (case.expected_args or {}).items():
        actual_args = next((args for name, args in calls if name == tool_name), None)
        if actual_args is None:
            errors.append(f"expected_args set for {tool_name!r}, but it was never called")
            continue
        mismatched = {k: (v, actual_args.get(k)) for k, v in expected_args.items() if actual_args.get(k) != v}
        if mismatched:
            errors.append(f"{tool_name!r} called with unexpected args: {mismatched}")

    called_forbidden = case.forbidden_tools & set(names_in_order)
    if called_forbidden:
        errors.append(f"forbidden tools were called: {sorted(called_forbidden)}")

    if case.max_tool_calls is not None and len(calls) > case.max_tool_calls:
        errors.append(f"{len(calls)} tool calls exceeds max_tool_calls={case.max_tool_calls}")

    if case.must_request_approval is True and not result.awaiting_approval:
        errors.append("expected an approval request (must_request_approval=True), none occurred")
    elif case.must_request_approval is False and result.awaiting_approval:
        errors.append("expected NO approval request (must_request_approval=False), but one occurred")

    return errors


@dataclass
class EvalCase:
    """One test case in a dataset. Every field beyond `input` is optional —
    a case can check task success (substring match), tool accuracy (the
    right tool got called), a KPI (groundedness or anything else), a full
    trajectory (sequence/args/forbidden tools/call ceiling/approval), or
    any combination."""

    input: str
    name: str = ""
    expected_substring: str | None = None
    expected_tool: str | None = None
    kpi: KPI | None = None
    kpi_context: Callable[[RunResult], dict[str, Any]] | None = None
    context: ExecutionContext | None = None
    # Trajectory expectations — "was this the CORRECT trajectory," not just
    # "did it end up right." All optional; None/empty means "no opinion."
    expected_tool_sequence: list[str] | None = None
    expected_args: dict[str, dict[str, Any]] | None = None  # tool_name -> {arg_name: expected_value}, partial match
    forbidden_tools: frozenset[str] = field(default_factory=frozenset)
    max_tool_calls: int | None = None
    must_request_approval: bool | None = None  # True: must have paused for approval; False: must NOT have; None: don't care


@dataclass
class CaseResult:
    case: EvalCase
    ok: bool
    task_success: bool
    tool_accuracy: bool
    kpi_result: KPIResult | None
    latency_ms: float
    cost_usd: float
    error: str | None = None
    run_result: RunResult | None = None
    trajectory_errors: list[str] = field(default_factory=list)


def attribute_failure(result: CaseResult) -> str | None:
    """Classifies WHY a failed CaseResult failed — nothing in this module
    does that today, only THAT a case failed (`.ok`). Checked in the same
    order `run_eval` itself computes `ok` (task_success and tool_accuracy
    and not trajectory_errors and kpi passed — see run_eval below), with
    `error` checked first since an exception makes every other field
    meaningless by construction (run_eval's except branch sets them all to
    False/None). Returns None for a case that actually passed — call this
    only on cases you already know failed (`not result.ok`), or check the
    return value; a None means there's nothing to attribute."""
    if not result.ok:
        if result.error is not None:
            return "error"
        if not result.task_success:
            return "task_success"
        if not result.tool_accuracy:
            return "tool_accuracy"
        if result.trajectory_errors:
            return "trajectory"
        if result.kpi_result is not None and not result.kpi_result.passed:
            return "kpi"
    return None


@dataclass
class Scorecard:
    agent_name: str
    dataset_name: str
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def task_success_rate(self) -> float:
        return sum(1 for c in self.cases if c.task_success) / len(self.cases) if self.cases else 0.0

    @property
    def tool_accuracy_rate(self) -> float:
        checked = [c for c in self.cases if c.case.expected_tool is not None]
        return sum(1 for c in checked if c.tool_accuracy) / len(checked) if checked else 1.0

    @property
    def trajectory_accuracy_rate(self) -> float:
        """Fraction of cases that declared at least one trajectory
        expectation (expected_tool_sequence/expected_args/forbidden_tools/
        max_tool_calls/must_request_approval) whose ACTUAL trajectory
        matched all of them — "was this the correct trajectory," not just
        "did the final answer/tool end up right." 1.0 (not skipped) when no
        case declared one, matching tool_accuracy_rate's own convention."""
        checked = [c for c in self.cases if _has_trajectory_expectations(c.case)]
        return sum(1 for c in checked if not c.trajectory_errors) / len(checked) if checked else 1.0

    @property
    def groundedness_avg(self) -> float | None:
        scored = [c.kpi_result.value for c in self.cases if c.kpi_result is not None]
        return sum(scored) / len(scored) if scored else None

    @property
    def p95_latency_ms(self) -> float:
        return _percentile([c.latency_ms for c in self.cases], 0.95)

    @property
    def avg_cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.cases) / len(self.cases) if self.cases else 0.0

    @property
    def error_rate(self) -> float:
        return sum(1 for c in self.cases if c.error is not None) / len(self.cases) if self.cases else 0.0

    def passes(self, thresholds: dict[str, float] | None = None) -> tuple[bool, list[str]]:
        """thresholds keys: task_success_rate_min, tool_accuracy_rate_min,
        trajectory_accuracy_rate_min, groundedness_avg_min, p95_latency_ms_max,
        avg_cost_usd_max, error_rate_max. A threshold whose metric has no
        data (e.g. groundedness_avg_min when no case used a KPI) is skipped,
        not failed."""
        thresholds = thresholds or {}
        reasons: list[str] = []
        metrics = {
            "task_success_rate_min": self.task_success_rate,
            "tool_accuracy_rate_min": self.tool_accuracy_rate,
            "trajectory_accuracy_rate_min": self.trajectory_accuracy_rate,
            "groundedness_avg_min": self.groundedness_avg,
            "p95_latency_ms_max": self.p95_latency_ms,
            "avg_cost_usd_max": self.avg_cost_usd,
            "error_rate_max": self.error_rate,
        }
        for key, limit in thresholds.items():
            if key not in _THRESHOLD_KINDS:
                raise ValueError(f"unknown threshold {key!r} — one of {sorted(_THRESHOLD_KINDS)}")
            actual = metrics[key]
            if actual is None:
                continue
            if _THRESHOLD_KINDS[key] == "min" and actual < limit:
                reasons.append(f"{key}: {actual:.3f} < required {limit}")
            elif _THRESHOLD_KINDS[key] == "max" and actual > limit:
                reasons.append(f"{key}: {actual:.3f} > allowed {limit}")
        return (len(reasons) == 0, reasons)

    def compare_to(self, baseline: ScorecardLike) -> dict[str, float]:
        """This scorecard's metric minus baseline's, per metric — positive is
        better for task_success/tool_accuracy/groundedness, negative is
        better for latency/cost/error_rate."""
        deltas = {}
        for attr in ("task_success_rate", "tool_accuracy_rate", "trajectory_accuracy_rate", "p95_latency_ms", "avg_cost_usd", "error_rate"):
            deltas[attr] = getattr(self, attr) - getattr(baseline, attr)
        if self.groundedness_avg is not None and baseline.groundedness_avg is not None:
            deltas["groundedness_avg"] = self.groundedness_avg - baseline.groundedness_avg
        return deltas

    def render(self) -> str:
        lines = [
            f"Task success        {self.task_success_rate * 100:.1f}%",
            f"Tool accuracy       {self.tool_accuracy_rate * 100:.1f}%",
        ]
        if any(_has_trajectory_expectations(c.case) for c in self.cases):
            lines.append(f"Trajectory accuracy {self.trajectory_accuracy_rate * 100:.1f}%")
        if self.groundedness_avg is not None:
            lines.append(f"Groundedness        {self.groundedness_avg * 100:.1f}%")
        lines.append(f"P95 latency         {self.p95_latency_ms / 1000:.2f}s")
        lines.append(f"Average cost        ${self.avg_cost_usd:.4f}")
        if self.error_rate:
            lines.append(f"Error rate          {self.error_rate * 100:.1f}%")
        return "\n".join(lines)


def run_eval(agent: Agent, cases: list[EvalCase], *, dataset_name: str = "") -> Scorecard:
    results: list[CaseResult] = []
    for case in cases:
        context = case.context or ExecutionContext(thread_id=f"eval-{uuid.uuid4().hex[:8]}")
        start = time.perf_counter()
        try:
            result = agent.run(case.input, context=context)
            latency_ms = (time.perf_counter() - start) * 1000
            cost_usd = agent.config.budget.cost_usd_for(context.resolved_thread_id())
            task_success = case.expected_substring is None or case.expected_substring.lower() in result.content.lower()
            tool_accuracy = case.expected_tool is None or case.expected_tool in _called_tool_names(result.messages)
            trajectory_errors = _check_trajectory(case, result)
            kpi_result = None
            if case.kpi is not None:
                ctx = case.kpi_context(result) if case.kpi_context is not None else {}
                kpi_result = case.kpi.evaluate(ctx)
            ok = task_success and tool_accuracy and not trajectory_errors and (kpi_result is None or kpi_result.passed)
            results.append(CaseResult(
                case=case, ok=ok, task_success=task_success, tool_accuracy=tool_accuracy,
                kpi_result=kpi_result, latency_ms=latency_ms, cost_usd=cost_usd, run_result=result,
                trajectory_errors=trajectory_errors,
            ))
        except Exception as e:
            latency_ms = (time.perf_counter() - start) * 1000
            results.append(CaseResult(
                case=case, ok=False, task_success=False, tool_accuracy=False,
                kpi_result=None, latency_ms=latency_ms, cost_usd=0.0, error=str(e),
            ))
    return Scorecard(agent_name=agent.name, dataset_name=dataset_name, cases=results)

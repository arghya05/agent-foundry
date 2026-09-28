"""Core — evaluation-as-release-gate: run a named dataset of cases against an
Agent and get back a Scorecard with a pass/fail regression gate — the
Python API `foundry eval` (agent_foundry/cli.py) calls.

Built entirely on what already exists: KPI/KPIResult (kpi.py, unchanged) for
groundedness-style scoring and sequential per-case budget deltas for known
cost. Missing oracles remain unmeasured and incomplete costs are marked. Uses orchestration._get_all_tool_calls for real tool-call
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
    def task_success_rate(self) -> float | None: ...
    @property
    def tool_accuracy_rate(self) -> float | None: ...
    @property
    def trajectory_accuracy_rate(self) -> float | None: ...
    @property
    def groundedness_avg(self) -> float | None: ...
    @property
    def p95_latency_ms(self) -> float | None: ...
    @property
    def avg_cost_usd(self) -> float | None: ...
    @property
    def error_rate(self) -> float | None: ...


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

    def __post_init__(self) -> None:
        for name in ("expected_substring", "expected_tool"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be empty; use None for an unmeasured oracle")


@dataclass
class CaseResult:
    case: EvalCase
    ok: bool
    task_success: bool | None
    tool_accuracy: bool | None
    kpi_result: KPIResult | None
    latency_ms: float
    cost_usd: float | None
    error: str | None = None
    run_result: RunResult | None = None
    trajectory_errors: list[str] = field(default_factory=list)
    cost_complete: bool = True


def attribute_failure(result: CaseResult) -> str | None:
    """Return the first failed criterion, execution error, or missing-oracle reason.

    Unspecified criteria stay unmeasured. A known partial result or cost may
    survive an error, but the case still fails. Passing cases return None.
    """
    if not result.ok:
        if result.error is not None:
            return "error"
        if result.task_success is False:
            return "task_success"
        if result.tool_accuracy is False:
            return "tool_accuracy"
        if result.trajectory_errors:
            return "trajectory"
        if result.kpi_result is not None and not result.kpi_result.passed:
            return "kpi"
        if not _has_oracle(result.case):
            return "missing_oracle"
    return None


def _has_oracle(case: EvalCase) -> bool:
    return (case.expected_substring is not None or case.expected_tool is not None
            or _has_trajectory_expectations(case) or case.kpi is not None)


@dataclass
class Scorecard:
    agent_name: str
    dataset_name: str
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def task_success_rate(self) -> float | None:
        checked = [c for c in self.cases if c.task_success is not None]
        return sum(c.task_success is True for c in checked) / len(checked) if checked else None

    @property
    def tool_accuracy_rate(self) -> float | None:
        checked = [c for c in self.cases if c.tool_accuracy is not None]
        return sum(c.tool_accuracy is True for c in checked) / len(checked) if checked else None

    @property
    def trajectory_accuracy_rate(self) -> float | None:
        """Declared trajectory checks; exceptions fail, missing oracles are None."""
        checked = [c for c in self.cases if _has_trajectory_expectations(c.case)]
        return sum(c.error is None and not c.trajectory_errors for c in checked) / len(checked) if checked else None

    @property
    def groundedness_avg(self) -> float | None:
        scored = [c.kpi_result.value for c in self.cases if c.kpi_result is not None]
        return sum(scored) / len(scored) if scored else None

    @property
    def p95_latency_ms(self) -> float | None:
        return _percentile([c.latency_ms for c in self.cases], 0.95) if self.cases else None

    @property
    def avg_cost_usd(self) -> float | None:
        if not self.cases or any(not c.cost_complete or c.cost_usd is None for c in self.cases):
            return None
        return self.known_cost_usd / len(self.cases)

    @property
    def known_cost_usd(self) -> float:
        """Observed cost retained even when total failure billing is unknown."""
        return sum(c.cost_usd for c in self.cases if c.cost_usd is not None)

    @property
    def error_rate(self) -> float | None:
        return sum(c.error is not None for c in self.cases) / len(self.cases) if self.cases else None

    @property
    def metric_coverage(self) -> dict[str, float]:
        count = len(self.cases)
        measured = {
            "task_success_rate": sum(c.task_success is not None for c in self.cases),
            "tool_accuracy_rate": sum(c.tool_accuracy is not None for c in self.cases),
            "trajectory_accuracy_rate": sum(_has_trajectory_expectations(c.case) for c in self.cases),
            "groundedness_avg": sum(c.kpi_result is not None for c in self.cases),
            "avg_cost_usd": sum(c.cost_complete and c.cost_usd is not None for c in self.cases),
            "p95_latency_ms": count, "error_rate": count,
        }
        return {name: n / count if count else 0.0 for name, n in measured.items()}

    def passes(self, thresholds: dict[str, float] | None = None, *, required_coverage: float = 1.0) -> tuple[bool, list[str]]:
        """Fail on absent/nonfinite metrics or insufficient oracle coverage.

        Requested metrics cover every case by default. Partial coverage must
        be explicitly permitted. Without thresholds, every case needs an
        oracle and must pass it. An empty scorecard never passes.
        """
        if not math.isfinite(required_coverage) or not 0 <= required_coverage <= 1:
            raise ValueError("required_coverage must be finite and between 0 and 1")
        thresholds = thresholds or {}
        for key, limit in thresholds.items():
            if key not in _THRESHOLD_KINDS:
                raise ValueError(f"unknown threshold {key!r} — one of {sorted(_THRESHOLD_KINDS)}")
            if not math.isfinite(limit):
                raise ValueError(f"threshold {key!r} must be finite")
        if not self.cases:
            return False, ["No evaluation cases; metrics are not measured"]
        if not thresholds:
            failed = sum(not c.ok for c in self.cases)
            return (False, [f"{failed} cases failed or have no oracle"]) if failed else (True, [])
        reasons = []
        for key, limit in thresholds.items():
            metric = key.rsplit("_", 1)[0]
            actual = getattr(self, metric)
            if actual is None or not math.isfinite(actual):
                reasons.append(f"{key}: not measured or nonfinite")
                continue
            coverage = self.metric_coverage[metric]
            if coverage < required_coverage:
                reasons.append(f"{key}: coverage {coverage:.3f} < required {required_coverage}")
                continue
            if _THRESHOLD_KINDS[key] == "min" and actual < limit:
                reasons.append(f"{key}: {actual:.3f} < required {limit}")
            elif _THRESHOLD_KINDS[key] == "max" and actual > limit:
                reasons.append(f"{key}: {actual:.3f} > allowed {limit}")
        return not reasons, reasons

    def compare_to(self, baseline: ScorecardLike) -> dict[str, float | None]:
        """Metric differences; None if either side is unmeasured."""
        deltas = {}
        for attr in ("task_success_rate", "tool_accuracy_rate", "trajectory_accuracy_rate", "groundedness_avg", "p95_latency_ms", "avg_cost_usd", "error_rate"):
            current, previous = getattr(self, attr), getattr(baseline, attr)
            deltas[attr] = current - previous if current is not None and previous is not None else None
        return deltas

    def render(self) -> str:
        lines = []
        for label, metric in (("Task success", "task_success_rate"), ("Tool accuracy", "tool_accuracy_rate"),
                              ("Trajectory accuracy", "trajectory_accuracy_rate"), ("Groundedness", "groundedness_avg")):
            value = getattr(self, metric)
            rendered = "not measured" if value is None else f"{value * 100:.1f}%"
            lines.append(f"{label:20}{rendered} (coverage {self.metric_coverage[metric]:.0%})")
        latency = self.p95_latency_ms
        lines.append("P95 latency         " + ("not measured" if latency is None else f"{latency / 1000:.2f}s"))
        cost = self.avg_cost_usd
        lines.append("Average cost        " + (f"not measured (known total ${self.known_cost_usd:.4f})" if cost is None else f"${cost:.4f}"))
        if self.error_rate:
            lines.append(f"Error rate          {self.error_rate * 100:.1f}%")
        return "\n".join(lines)


def _read_cost(budget: Any, thread_id: str) -> float | None:
    try:
        cost = float(budget.cost_usd_for(thread_id))
        return cost if math.isfinite(cost) and cost >= 0 else None
    except Exception:
        return None


def _cost_delta(budget: Any, thread_id: str, before: float | None) -> float | None:
    after = _read_cost(budget, thread_id)
    return after - before if before is not None and after is not None and after >= before else None


def run_eval(agent: Agent, cases: list[EvalCase], *, dataset_name: str = "") -> Scorecard:
    """Sequential case evaluation with explicit oracle and cost coverage.

    Costs are deltas of the configured request/agent budget. External billing
    is not inferred from an exception; known charges survive, total cost is
    marked incomplete. Concurrent external work sharing that same bucket can
    contaminate deltas, so use isolated evaluation contexts/budgets.
    """
    results: list[CaseResult] = []
    for case in cases:
        context = case.context or ExecutionContext(thread_id=f"eval-{uuid.uuid4().hex[:8]}")
        thread_id = context.resolved_thread_id()
        budget = context.budget if context.budget is not None else agent.config.budget
        before = _read_cost(budget, thread_id)
        start = time.perf_counter()
        result = None
        try:
            result = agent.run(case.input, context=context)
            task_success = (case.expected_substring.lower() in result.content.lower()
                            if case.expected_substring is not None else None)
            tool_accuracy = (case.expected_tool in _called_tool_names(result.messages)
                             if case.expected_tool is not None else None)
            trajectory_errors = _check_trajectory(case, result)
            kpi_result = None
            if case.kpi is not None:
                ctx = case.kpi_context(result) if case.kpi_context is not None else {}
                kpi_result = case.kpi.evaluate(ctx)
                if not math.isfinite(kpi_result.value):
                    raise ValueError("KPI returned a nonfinite value")
            ok = (_has_oracle(case) and task_success is not False and tool_accuracy is not False
                  and not trajectory_errors and (kpi_result is None or kpi_result.passed))
            cost = _cost_delta(budget, thread_id, before)
            results.append(CaseResult(
                case=case, ok=ok, task_success=task_success, tool_accuracy=tool_accuracy,
                kpi_result=kpi_result, latency_ms=(time.perf_counter() - start) * 1000,
                cost_usd=cost, cost_complete=cost is not None, run_result=result,
                trajectory_errors=trajectory_errors,
            ))
        except Exception as e:
            cost = _cost_delta(budget, thread_id, before)
            results.append(CaseResult(
                case=case, ok=False, task_success=False if case.expected_substring is not None else None,
                tool_accuracy=False if case.expected_tool is not None else None, kpi_result=None,
                latency_ms=(time.perf_counter() - start) * 1000, cost_usd=cost,
                cost_complete=result is not None and cost is not None, error=str(e), run_result=result,
            ))
    return Scorecard(agent_name=agent.name, dataset_name=dataset_name, cases=results)

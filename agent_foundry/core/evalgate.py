"""Core — evaluation-as-release-gate: run a named dataset of cases against an
Agent and get back a Scorecard with a pass/fail regression gate, the
`foundry eval fashion-agent --dataset ...` idea without a CLI (none exists in
this repo — this is the Python API a CLI would call).

Built entirely on what already exists: KPI/KPIResult (kpi.py, unchanged) for
groundedness-style scoring, AgentConfig.budget.cost_usd_for() for real
per-case cost, orchestration._get_all_tool_calls for real tool-call
detection (same reused-not-reimplemented reasoning as native_engine.py).
Nothing here is a mock scorer — every metric is measured from a real
Agent.run() call.
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from ..kpi import KPI, KPIResult
from ..orchestration import _get_all_tool_calls
from .agent import Agent
from .execution_context import ExecutionContext
from .result import RunResult

_THRESHOLD_KINDS = {
    "task_success_rate_min": "min",
    "tool_accuracy_rate_min": "min",
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


@dataclass
class EvalCase:
    """One test case in a dataset. Every field beyond `input` is optional —
    a case can check task success (substring match), tool accuracy (the
    right tool got called), a KPI (groundedness or anything else), or any
    combination."""

    input: str
    name: str = ""
    expected_substring: str | None = None
    expected_tool: str | None = None
    kpi: KPI | None = None
    kpi_context: Callable[[RunResult], dict[str, Any]] | None = None
    context: ExecutionContext | None = None


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
        groundedness_avg_min, p95_latency_ms_max, avg_cost_usd_max,
        error_rate_max. A threshold whose metric has no data (e.g.
        groundedness_avg_min when no case used a KPI) is skipped, not failed."""
        thresholds = thresholds or {}
        reasons: list[str] = []
        metrics = {
            "task_success_rate_min": self.task_success_rate,
            "tool_accuracy_rate_min": self.tool_accuracy_rate,
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

    def compare_to(self, baseline: "Scorecard") -> dict[str, float]:
        """This scorecard's metric minus baseline's, per metric — positive is
        better for task_success/tool_accuracy/groundedness, negative is
        better for latency/cost/error_rate."""
        deltas = {}
        for attr in ("task_success_rate", "tool_accuracy_rate", "p95_latency_ms", "avg_cost_usd", "error_rate"):
            deltas[attr] = getattr(self, attr) - getattr(baseline, attr)
        if self.groundedness_avg is not None and baseline.groundedness_avg is not None:
            deltas["groundedness_avg"] = self.groundedness_avg - baseline.groundedness_avg
        return deltas

    def render(self) -> str:
        lines = [
            f"Task success        {self.task_success_rate * 100:.1f}%",
            f"Tool accuracy       {self.tool_accuracy_rate * 100:.1f}%",
        ]
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
            kpi_result = None
            if case.kpi is not None:
                ctx = case.kpi_context(result) if case.kpi_context is not None else {}
                kpi_result = case.kpi.evaluate(ctx)
            ok = task_success and tool_accuracy and (kpi_result is None or kpi_result.passed)
            results.append(CaseResult(
                case=case, ok=ok, task_success=task_success, tool_accuracy=tool_accuracy,
                kpi_result=kpi_result, latency_ms=latency_ms, cost_usd=cost_usd, run_result=result,
            ))
        except Exception as e:
            latency_ms = (time.perf_counter() - start) * 1000
            results.append(CaseResult(
                case=case, ok=False, task_success=False, tool_accuracy=False,
                kpi_result=None, latency_ms=latency_ms, cost_usd=0.0, error=str(e),
            ))
    return Scorecard(agent_name=agent.name, dataset_name=dataset_name, cases=results)

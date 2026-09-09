"""Eval harness — atomic, component, flow and overall levels (PLAN.md section 2)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

from .contracts import EvalRecord

_LEVELS = ("atomic", "component", "flow", "overall")


class Evaluator(Protocol):
    """Anything that can record a scored observation at one of the four levels.
    orchestration.py only ever calls .record() on AgentConfig.eval_harness — this
    is the entire contract a replacement needs to satisfy. EvalHarness (in-memory)
    is the reference implementation; JSONLEvalSink below is a genuinely different
    one (durable, streams to a file instead of holding records in memory)."""

    def record(self, level: str, unit: str, metric: str, score: float, **detail: Any) -> Any: ...


@dataclass
class EvalHarness:
    records: list[EvalRecord] = field(default_factory=list)

    def record(self, level: str, unit: str, metric: str, score: float, **detail: Any) -> EvalRecord:
        assert level in _LEVELS, f"unknown eval level {level!r}"
        rec = EvalRecord(level=level, unit=unit, metric=metric, score=score, detail=detail)
        self.records.append(rec)
        return rec

    def score_for(self, level: str) -> float:
        scoped = [r.score for r in self.records if r.level == level]
        return sum(scoped) / len(scoped) if scoped else 0.0

    def summary(self) -> dict[str, float]:
        return {level: self.score_for(level) for level in _LEVELS}


_TOOL_METRICS = ("success", "approval", "permission", "action_guardrail")


@dataclass
class TrajectoryReport:
    """One session's execution trajectory — tool choice/sequence, whether a
    failure got recovered from, guardrail interventions, critique scores,
    and how the turn ultimately resolved. Aggregated purely from EvalRecords
    the framework already writes (see make_think_node/make_act_node/
    make_critique_node/_finalize_turn's own .record() calls in
    orchestration.py) — no new instrumentation needed. Cost/latency
    deliberately stay OUT of this report: AgentConfig.cost_ledger/
    .sla_tracker already own that data, and duplicating it into eval
    records would just be a second, driftable copy of the same numbers."""

    session_id: str
    tool_calls: list[str] = field(default_factory=list)  # in call order, including denied/failed attempts
    tools_used: set[str] = field(default_factory=set)
    tool_success_rate: float = 1.0
    recovered_tool_failures: list[str] = field(default_factory=list)  # tool names that failed, then later succeeded
    guardrail_blocks: list[str] = field(default_factory=list)  # e.g. "input_guardrail", "output_guardrail"
    critique_scores: list[tuple[str, float]] = field(default_factory=list)  # (kpi_name, score), in order
    outcome: str | None = None  # the flow-level metric (_finalize_turn's "completed"/etc.) if the turn finished


def trajectory_report(harness: "EvalHarness", session_id: str) -> TrajectoryReport:
    """Walks harness.records for one session_id and aggregates them into a
    TrajectoryReport. Records are matched by `detail["session_id"]` (every
    atomic/component record carries it) OR by being the flow-level record
    for this session (_finalize_turn's `record("flow", session_id, ...)`,
    which has no session_id in `detail` since `unit` already IS the
    session_id)."""
    report = TrajectoryReport(session_id=session_id)
    scoped = [r for r in harness.records if r.detail.get("session_id") == session_id or (r.level == "flow" and r.unit == session_id)]

    last_ok: dict[str, bool] = {}  # tool name -> most-recently-seen outcome, in record order
    tool_attempts = 0
    tool_successes = 0
    for r in scoped:
        if r.level == "component" and r.metric in _TOOL_METRICS:
            report.tool_calls.append(r.unit)
            report.tools_used.add(r.unit)
            ok = r.metric == "success" and r.score == 1.0
            tool_attempts += 1
            tool_successes += 1 if ok else 0
            if r.unit in last_ok and not last_ok[r.unit] and ok:
                report.recovered_tool_failures.append(r.unit)
            last_ok[r.unit] = ok
        elif r.level == "atomic" and r.unit in ("input_guardrail", "output_guardrail") and r.score == 0.0:
            report.guardrail_blocks.append(r.unit)
        elif r.level == "atomic" and r.unit == "critique":
            report.critique_scores.append((r.metric, r.score))
        elif r.level == "flow" and r.unit == session_id:
            report.outcome = r.metric

    if tool_attempts:
        report.tool_success_rate = tool_successes / tool_attempts
    return report


@dataclass
class JSONLEvalSink:
    """Writes each record as one JSON line to a file instead of holding it in
    memory — for piping eval data straight into a warehouse/logging pipeline.
    Same .record() shape as EvalHarness, so it's a drop-in for
    AgentConfig.eval_harness; querying it means reading the file, not calling
    .summary() — a real tradeoff of durable-and-external vs. in-memory-and-queryable."""

    path: str

    def record(self, level: str, unit: str, metric: str, score: float, **detail: Any) -> None:
        assert level in _LEVELS, f"unknown eval level {level!r}"
        with open(self.path, "a") as f:
            f.write(json.dumps({"level": level, "unit": unit, "metric": metric, "score": score, "ts": time.time(), **detail}) + "\n")

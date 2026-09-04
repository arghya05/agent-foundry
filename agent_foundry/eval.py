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

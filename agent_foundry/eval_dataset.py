"""EvalDataset — a named, versioned eval-case file, plus baseline
persistence for core.evalgate.Scorecard.compare_to() regression checks
across runs. Purely additive on top of core/evalgate.py: EvalCase/Scorecard/
run_eval are unchanged, this just gives the "flat JSON list of cases"
cli.py's `foundry eval` already loads a name/version wrapper, and a place to
save/reload one run's Scorecard as the next run's baseline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core.evalgate import EvalCase, Scorecard


@dataclass(frozen=True)
class BaselineScorecard:
    """A saved Scorecard's computed metrics, reloaded as a duck-typed
    stand-in for Scorecard.compare_to()'s `baseline` argument — compare_to
    only ever reads these seven properties via getattr, never `.cases`
    itself, so a real CaseResult list (not cheaply serializable — it holds
    a RunResult per case) never needs to round-trip through disk."""

    task_success_rate: float
    tool_accuracy_rate: float
    trajectory_accuracy_rate: float
    groundedness_avg: float | None
    p95_latency_ms: float
    avg_cost_usd: float
    error_rate: float


@dataclass
class EvalDataset:
    name: str
    version: str
    cases: list[dict[str, Any]]  # EvalCase(**case)-constructible, same shape cli.py's cmd_eval already loads flat

    @classmethod
    def load(cls, path: str | Path) -> "EvalDataset":
        data = json.loads(Path(path).read_text())
        return cls(name=data["name"], version=data["version"], cases=data["cases"])

    @staticmethod
    def looks_versioned(data: Any) -> bool:
        """True for `{"name":..., "version":..., "cases":[...]}`; False for
        the older flat `[{...}, {...}]` list shape cli.py's cmd_eval has
        always accepted — lets callers auto-detect which one a dataset file
        is without guessing from its filename."""
        return isinstance(data, dict) and "cases" in data

    def to_cases(self) -> list[EvalCase]:
        cases = []
        for raw in self.cases:
            raw = dict(raw)
            if "forbidden_tools" in raw and not isinstance(raw["forbidden_tools"], frozenset):
                raw["forbidden_tools"] = frozenset(raw["forbidden_tools"])
            cases.append(EvalCase(**raw))
        return cases

    def save_baseline(self, scorecard: Scorecard, directory: str | Path) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.name}-{self.version}.baseline.json"
        path.write_text(json.dumps({
            "task_success_rate": scorecard.task_success_rate,
            "tool_accuracy_rate": scorecard.tool_accuracy_rate,
            "trajectory_accuracy_rate": scorecard.trajectory_accuracy_rate,
            "groundedness_avg": scorecard.groundedness_avg,
            "p95_latency_ms": scorecard.p95_latency_ms,
            "avg_cost_usd": scorecard.avg_cost_usd,
            "error_rate": scorecard.error_rate,
        }, indent=2))
        return path

    @classmethod
    def load_baseline(cls, directory: str | Path, name: str, version: str) -> BaselineScorecard | None:
        path = Path(directory) / f"{name}-{version}.baseline.json"
        if not path.exists():
            return None
        return BaselineScorecard(**json.loads(path.read_text()))

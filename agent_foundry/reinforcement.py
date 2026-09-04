"""Reinforcement loop — closes eval signal back into prompt, policy and model (PLAN.md section 5).

Two tiers, run at different cadences — neither runs inside a single request:
  - PromptOptimizer: batch job over recent threads' EvalHarness records. Curates
    few-shot exemplars from the best-scoring trajectories, flags the worst ones
    for prompt review. Cheap, same-day.
  - PreferenceStore: accumulates HITL approve/deny decisions as chosen/rejected
    pairs and exports them for DPO/RLAIF or distillation. Slower, scheduled.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .eval import EvalHarness


@dataclass
class PromptOptimizer:
    good_threshold: float = 0.8
    bad_threshold: float = 0.3
    exemplars: list[dict[str, Any]] = field(default_factory=list)
    flagged: list[dict[str, Any]] = field(default_factory=list)

    def observe(self, *, unit: str, trajectory: list[dict], eval_harness: EvalHarness) -> None:
        scores = [r.score for r in eval_harness.records if r.unit == unit]
        if not scores:
            return
        avg = sum(scores) / len(scores)
        entry = {"unit": unit, "trajectory": trajectory, "score": avg}
        if avg >= self.good_threshold:
            self.exemplars.append(entry)
        elif avg <= self.bad_threshold:
            self.flagged.append(entry)

    def few_shot_block(self, k: int = 3) -> str:
        """Best-scoring trajectories, ready to splice into a system prompt as examples."""
        best = sorted(self.exemplars, key=lambda e: e["score"], reverse=True)[:k]
        return "\n\n".join(json.dumps(e["trajectory"]) for e in best)


@dataclass
class PreferenceStore:
    """Chosen-vs-rejected pairs, sourced from HITL approve/deny decisions."""

    pairs: list[dict[str, Any]] = field(default_factory=list)

    def record(self, *, context: list[dict], chosen: str, rejected: str, source: str = "hitl") -> None:
        self.pairs.append({"context": context, "chosen": chosen, "rejected": rejected, "source": source})

    def export_jsonl(self, path: str) -> None:
        with open(path, "w") as f:
            for pair in self.pairs:
                f.write(json.dumps(pair) + "\n")

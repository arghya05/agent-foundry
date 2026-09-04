"""A/B Testing — deterministic variant assignment plus per-variant metric
aggregation, so two prompts/policies/topologies can run side by side and you can
see, with real numbers, which one wins. Composes with kpi.py: score each run
with a KPI, feed the score into ExperimentTracker.record(), and summary() gives
the per-variant mean/count to decide a winner — no new scoring mechanism needed.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .contracts import Identity


@dataclass
class Experiment:
    name: str
    variants: dict[str, float]  # variant name -> weight; need not sum to 1, normalized internally

    def assign(self, identity: Identity) -> str:
        """Deterministic: the same identity always gets the same variant for this
        experiment (stable hash bucketing) — an identity's experience never
        flaps between calls."""
        total = sum(self.variants.values())
        digest = int(hashlib.sha256(f"{self.name}:{identity.id}".encode()).hexdigest(), 16)
        bucket = (digest % 10_000) / 10_000 * total
        cumulative = 0.0
        for variant, weight in self.variants.items():
            cumulative += weight
            if bucket < cumulative:
                return variant
        return next(iter(self.variants))


@dataclass
class ExperimentTracker:
    _records: dict[tuple[str, str], list[float]] = field(default_factory=dict)

    def record(self, experiment: str, variant: str, value: float) -> None:
        self._records.setdefault((experiment, variant), []).append(value)

    def summary(self, experiment: str) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for (exp, variant), values in self._records.items():
            if exp != experiment:
                continue
            out[variant] = {"count": float(len(values)), "mean": sum(values) / len(values)}
        return out

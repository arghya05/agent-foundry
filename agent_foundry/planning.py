"""Planning — state Objectives (what to optimize for, from whatever KPIs are
registered on a KPIBoard) and let a Planner score candidate options against them.

This is what "based on the use case, pick the right architecture" resolves to
concretely: not a hardcoded algorithm per pattern, but a scoring function over
your own KPIBoard — use it to choose a model, a topology, a tool, anything with
more than one reasonable option. Depends only on kpi.py; compose it with
LLMGateway, orchestration, wherever a decision needs to be made, from the
outside — nothing here reaches into those modules.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .kpi import KPIBoard


class DecisionMaker(Protocol):
    """Anything that can pick the best-named option given per-option context.
    Planner (KPI-weighted scoring) is the reference implementation — swap in a
    bandit, an ML ranker, a rules engine, or a call out into a different agentic
    framework's own planner entirely, as long as it exposes this one method.
    Nothing in orchestration.py or elsewhere is typed against Planner
    specifically; every caller in this codebase only ever needs .choose()."""

    def choose(self, candidates: dict[str, dict[str, Any]]) -> str: ...


@dataclass
class Objective:
    """One thing to optimize for, and how much it matters relative to the others."""
    kpi_name: str
    weight: float = 1.0


@dataclass
class Planner:
    board: KPIBoard
    objectives: list[Objective] = field(default_factory=list)

    def score(self, context: dict[str, Any]) -> float:
        results = {r.name: r.value for r in self.board.evaluate_all(context)}
        total_weight = sum(o.weight for o in self.objectives) or 1.0
        return sum(results.get(o.kpi_name, 0.0) * o.weight for o in self.objectives) / total_weight

    def choose(self, candidates: dict[str, dict[str, Any]]) -> str:
        """candidates: option name -> its context (e.g. {"gpt-cheap": {"cost_usd": 0.001,
        "latency_ms": 400}, "gpt-strong": {"cost_usd": 0.02, "latency_ms": 1800}}).
        Returns the name that scores best against the stated objectives. Objectives
        over "minimize"-direction KPIs (cost, latency) naturally favor the lower
        value since the KPI's own score() returns the raw minimized quantity —
        rank() below inverts that so "best" always means "highest score"."""
        return max(candidates, key=lambda name: self._ranked_score(candidates[name]))

    def rank(self, candidates: dict[str, dict[str, Any]]) -> list[str]:
        """All candidates, best objective-score first."""
        return sorted(candidates, key=lambda name: self._ranked_score(candidates[name]), reverse=True)

    def _ranked_score(self, context: dict[str, Any]) -> float:
        total = 0.0
        total_weight = sum(o.weight for o in self.objectives) or 1.0
        for o in self.objectives:
            kpi = self.board.kpis.get(o.kpi_name)
            if kpi is None:
                continue
            value = kpi.score(context)
            total += (-value if kpi.direction == "minimize" else value) * o.weight
        return total / total_weight


@dataclass
class StrategyRule:
    name: str
    when: Callable[[dict[str, Any]], bool]  # e.g. lambda ctx: ctx["complexity"] < 0.3


@dataclass
class StrategySelector:
    """Score a request (via a KPIBoard — complexity_kpi/risk_kpi, or your own) and
    pick which topology should handle it. This is the concrete mechanism behind
    "based on the use case, pick the right architecture": rules checked in order,
    first match wins, `default` if none match. Returns a name; what that name maps
    to (a compiled graph, a graph-builder callable) is entirely up to the caller —
    this module doesn't know about orchestration.py's graph builders on purpose."""

    rules: list[StrategyRule]
    default: str

    def select(self, context: dict[str, Any]) -> str:
        for rule in self.rules:
            if rule.when(context):
                return rule.name
        return self.default


@dataclass
class BanditSelector:
    """A genuinely different DecisionMaker: epsilon-greedy multi-armed bandit —
    learns which option performs best from observed rewards instead of scoring
    against a fixed KPIBoard. Proves DecisionMaker is a real swap point, not just
    Planner renamed: same .choose() shape, completely different algorithm
    underneath — this is where you'd plug in a different agentic framework's own
    planner/optimizer entirely, if that's what a use case calls for."""

    epsilon: float = 0.1
    _rewards: dict[str, list[float]] = field(default_factory=dict)

    def observe(self, option: str, reward: float) -> None:
        self._rewards.setdefault(option, []).append(reward)

    def choose(self, candidates: dict[str, dict[str, Any]]) -> str:
        unexplored = [name for name in candidates if name not in self._rewards]
        if unexplored or random.random() < self.epsilon:
            return random.choice(unexplored or list(candidates))
        return max(candidates, key=lambda name: sum(self._rewards[name]) / len(self._rewards[name]))

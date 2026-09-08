"""Core — capability-based model routing. LLMGateway (llm_gateway.py) is
unchanged: it still just walks `routes[task]` in order with failover — this
module is how `routes[task]` gets decided from a request's capabilities/cost/
latency requirements instead of being hand-authored, via one explicit
`apply_route()` call. `ModelRegistry` (llm_gateway.py's existing measured-
eval-score ranker) plugs straight in as the tiebreaker among capability
matches, when one is supplied.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..llm_gateway import ModelRegistry

if TYPE_CHECKING:
    from ..llm_gateway import LLMGateway


@dataclass(frozen=True)
class ModelCapabilities:
    """What one model can do and what it costs — the catalog entry a
    ModelRequest is matched against. `capabilities`/`latency_class` are
    caller-defined tags (e.g. {"vision", "tool_calling"}, "fast"/"standard"),
    not a fixed vocabulary this module hardcodes claims about real models
    into."""

    name: str
    capabilities: frozenset[str] = frozenset()
    cost_per_1m_input_usd: float = 0.0
    latency_class: str = "standard"


@dataclass(frozen=True)
class ModelRequest:
    capabilities: frozenset[str] = frozenset()
    max_cost_per_1m_input_usd: float | None = None
    latency: str | None = None


@dataclass
class ModelRouter:
    """Picks a model from `catalog` that satisfies a ModelRequest — every
    declared capability present, cost/latency within the request's ceiling —
    ranked by `registry`'s measured eval scores when one is given, else by
    cheapest match."""

    catalog: dict[str, ModelCapabilities]
    registry: ModelRegistry | None = None

    def candidates(self, request: ModelRequest) -> list[str]:
        return [
            m.name
            for m in self.catalog.values()
            if request.capabilities <= m.capabilities
            and (request.max_cost_per_1m_input_usd is None or m.cost_per_1m_input_usd <= request.max_cost_per_1m_input_usd)
            and (request.latency is None or m.latency_class == request.latency)
        ]

    def select(self, request: ModelRequest) -> str:
        names = self.candidates(request)
        if not names:
            raise ValueError(f"no model in the catalog satisfies {request!r}")
        if self.registry is not None:
            return self.registry.ranked(names)[0]
        return min(names, key=lambda n: self.catalog[n].cost_per_1m_input_usd)


def apply_route(llm: "LLMGateway", *, task: str, request: ModelRequest, router: ModelRouter, fallback: list[str] = ()) -> str:
    """Sets llm.routes[task] from a capability-based decision instead of a
    hand-authored model list. llm.complete(task=...)'s own routing/failover
    (llm_gateway.py, unchanged) does the rest."""
    selected = router.select(request)
    llm.routes[task] = [selected, *fallback]
    return selected

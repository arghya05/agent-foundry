"""Observability — tracing, metrics/alerting, and cost tracked per completed task.

'Per completed task' means per finished thread, not per call: CostLedger.close_task()
is what orchestration.py invokes the moment a thread reaches END, so cost is always
attributable to a finished unit of work rather than reconstructed later from raw spans.
"""
from __future__ import annotations

import contextlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol


class TracerLike(Protocol):
    """Anything with .span()/.total_cost_usd() — the entire contract
    orchestration.py needs from AgentConfig.tracer. Tracer (in-memory JSON
    spans) and OTelTracer (real OpenTelemetry SDK, further below) are the two
    implementations verified against this shape; ship your own to Datadog,
    Honeycomb, or anywhere else by matching it."""

    def span(self, name: str, **attrs: Any) -> Any: ...
    def total_cost_usd(self) -> float: ...


class Tracer:
    def __init__(self, thread_id: str):
        self.thread_id = thread_id
        self.spans: list[dict[str, Any]] = []

    @contextlib.contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[dict[str, Any]]:
        record = {
            "trace_id": self.thread_id,
            "span_id": uuid.uuid4().hex[:12],
            "name": name,
            "attributes": attrs,
        }
        start = time.time()
        try:
            yield record
        finally:
            record["duration_ms"] = round((time.time() - start) * 1000, 2)
            self.spans.append(record)
            print(json.dumps(record, default=str))

    def total_cost_usd(self) -> float:
        return sum(s["attributes"].get("cost_usd", 0.0) for s in self.spans)


class _OTelAttrs:
    """Makes span["attributes"].update(...) — the call orchestration.py already
    makes on a plain Tracer — set real OTel span attributes instead."""

    def __init__(self, otel_span: Any, tracer: "OTelTracer") -> None:
        self._span = otel_span
        self._tracer = tracer

    def update(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            self._span.set_attribute(k, v)
            if k == "cost_usd":
                self._tracer._cost += v


class OTelTracer:
    """Real OpenTelemetry-backed tracer (opentelemetry-sdk) — same .span()/
    .total_cost_usd() shape as Tracer, so it's a drop-in replacement anywhere a
    Tracer is expected (AgentConfig.tracer included). Defaults to a
    ConsoleSpanExporter so this is real and testable with zero infra; pass your
    own `processor` (an OTLPSpanExporter-backed BatchSpanProcessor, say) to ship
    spans to an actual collector — Jaeger, Honeycomb, Datadog, whatever you run.
    Requires `pip install opentelemetry-sdk`.

    Honest limit: Metrics/check_alerts/render_dashboard below read Tracer.spans
    directly, which OTelTracer doesn't keep (spans are handed to the exporter, the
    way real OTel is meant to work) — query your collector's backend for that
    once you're using OTelTracer, instead of this module's in-process dashboard.
    """

    def __init__(self, thread_id: str, *, processor: Any = None) -> None:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

        self.thread_id = thread_id
        self._cost = 0.0
        provider = TracerProvider()
        provider.add_span_processor(processor or SimpleSpanProcessor(ConsoleSpanExporter()))
        self._tracer = provider.get_tracer("agent_foundry")

    @contextlib.contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[dict[str, Any]]:
        with self._tracer.start_as_current_span(name) as otel_span:
            otel_span.set_attribute("trace_id", self.thread_id)
            for k, v in attrs.items():
                otel_span.set_attribute(k, v)
            yield {"attributes": _OTelAttrs(otel_span, self)}

    def total_cost_usd(self) -> float:
        return self._cost


@dataclass
class Metrics:
    """Aggregates over a Tracer's spans: latency percentiles, tool error rate."""

    tracer: Tracer

    def latencies_ms(self, name: str | None = None) -> list[float]:
        return [s["duration_ms"] for s in self.tracer.spans if name is None or s["name"] == name]

    def percentile(self, p: float, *, name: str | None = None) -> float:
        vals = sorted(self.latencies_ms(name))
        if not vals:
            return 0.0
        idx = min(len(vals) - 1, int(len(vals) * p))
        return vals[idx]

    def tool_error_rate(self) -> float:
        acts = [s for s in self.tracer.spans if s["name"] == "orchestration.act"]
        if not acts:
            return 0.0
        failed = sum(1 for s in acts if not s["attributes"].get("ok", True))
        return failed / len(acts)


def check_alerts(metrics: Metrics, *, budget_cost_usd: float, max_cost_usd: float) -> list[str]:
    """Threshold-based alerting — swap for anomaly detection once you have volume."""
    alerts = []
    if metrics.percentile(0.95) > 5000:
        alerts.append(f"p95 latency {metrics.percentile(0.95):.0f}ms exceeds 5000ms")
    if metrics.tool_error_rate() > 0.2:
        alerts.append(f"tool error rate {metrics.tool_error_rate():.0%} exceeds 20%")
    if max_cost_usd and budget_cost_usd > 0.8 * max_cost_usd:
        alerts.append(f"thread cost ${budget_cost_usd:.4f} is within 80% of its ${max_cost_usd} budget")
    return alerts


class CostLedgerLike(Protocol):
    """The real contract cost-attribution callers need — same swappable-
    interface posture VectorStore/AuditSink/Evaluator already have. A
    fleet-wide cost total can't come from N replicas each holding their
    own independent CostLedger; a real deployment needs this backed by
    something shared (a DB table, a metrics backend) — implement this
    shape and it's a drop-in."""

    def close_task(self, *, thread_id: str, tenant_id: str, cost_usd: float, steps: int, outcome: str) -> None: ...
    def close_incomplete(self, *, thread_id: str, tenant_id: str, cost_usd: float, reason: str) -> None: ...
    def total_cost_usd(self) -> float: ...
    def cost_per_task(self) -> float: ...
    def total_incomplete_cost_usd(self) -> float: ...
    def cost_per_incomplete_task(self) -> float: ...
    def by_tenant(self) -> dict[str, float]: ...


@dataclass
class CostLedger:
    """Cost attributed per task/thread — the unit the cost pillar bills
    against. Two buckets, not one: `completed` (close_task, called from
    orchestration.py's _finalize_turn once a turn genuinely finishes) and
    `incomplete` (close_incomplete, called by a caller that catches a turn
    failing mid-flight — BudgetExceeded, an unhandled exception, a timeout).
    Real spend happened in both cases (the LLM calls already billed before
    the failure), so "cost per completed task" alone understates what a
    deployment is actually paying — a task that errored out after two
    expensive tool-calling rounds still cost real money and should show up
    somewhere, split out from the clean-completion number rather than
    silently folded into it or dropped."""

    completed: list[dict[str, Any]] = field(default_factory=list)
    incomplete: list[dict[str, Any]] = field(default_factory=list)

    def close_task(self, *, thread_id: str, tenant_id: str, cost_usd: float, steps: int, outcome: str) -> None:
        self.completed.append({
            "thread_id": thread_id,
            "tenant_id": tenant_id,
            "cost_usd": cost_usd,
            "steps": steps,
            "outcome": outcome,
            "ts": time.time(),
        })

    def close_incomplete(self, *, thread_id: str, tenant_id: str, cost_usd: float, reason: str) -> None:
        self.incomplete.append({
            "thread_id": thread_id,
            "tenant_id": tenant_id,
            "cost_usd": cost_usd,
            "reason": reason,
            "ts": time.time(),
        })

    def total_cost_usd(self) -> float:
        return sum(t["cost_usd"] for t in self.completed)

    def cost_per_task(self) -> float:
        return self.total_cost_usd() / len(self.completed) if self.completed else 0.0

    def total_incomplete_cost_usd(self) -> float:
        return sum(t["cost_usd"] for t in self.incomplete)

    def cost_per_incomplete_task(self) -> float:
        return self.total_incomplete_cost_usd() / len(self.incomplete) if self.incomplete else 0.0

    def by_tenant(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for t in self.completed:
            totals[t["tenant_id"]] = totals.get(t["tenant_id"], 0.0) + t["cost_usd"]
        return totals


def render_dashboard(*, tracer: Tracer, eval_harness: Any, budget: Any, ledger: "CostLedger | None" = None) -> str:
    lines = [
        f"thread:      {tracer.thread_id}",
        f"spans:       {len(tracer.spans)}",
        f"cost:        ${budget.cost_usd:.4f} / ${budget.policy.max_cost_usd_per_thread} budget",
        f"steps:       {budget.steps} / {budget.policy.max_steps_per_thread}",
        f"eval:        {eval_harness.summary()}",
    ]
    if ledger is not None and ledger.completed:
        lines.append(f"tasks done:  {len(ledger.completed)}  (avg cost ${ledger.cost_per_task():.4f}/task)")
    return "\n".join(lines)

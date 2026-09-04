"""Layer 03 — Harness/Runtime: per-thread budgets, retries, timeouts, circuit breaking.

Thread/session lifecycle itself is delegated to LangGraph's checkpointer
(see orchestration.py) rather than reimplemented here.
"""
from __future__ import annotations

import concurrent.futures
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, TypeVar

from .contracts import Policy

T = TypeVar("T")


class BudgetExceeded(Exception):
    pass


_DEFAULT_THREAD = "__default__"  # the bucket used when no thread_id is given — every
                                   # pre-existing single-session caller (tests, examples)
                                   # gets exactly one implicit bucket, unchanged behavior.


class RunBudgetLike(Protocol):
    """The real contract AgentConfig.budget needs — formalizes what was
    already true in practice (Python's structural typing lets any matching
    object stand in for RunBudget), the same swappable-interface posture
    VectorStore/AuditSink/Evaluator/Provider already have. A distributed
    deployment (many replicas) needs this backed by something shared
    (Redis, a real counter service) instead of RunBudget's in-process
    dict — implement this shape and it's a drop-in, no other code changes."""

    def spend(self, amount: float, *, thread_id: str = ...) -> None: ...
    def step(self, *, thread_id: str = ...) -> None: ...
    def cost_usd_for(self, thread_id: str = ...) -> float: ...
    def steps_for(self, thread_id: str = ...) -> int: ...


@dataclass
class RunBudget:
    """Enforced fail-closed: exceeding cost or step limits raises, it does not
    silently cap. Tracked per thread_id internally — one compiled graph
    commonly serves many sessions (see orchestration.py/serve.py: one shared
    graph, a different thread_id per HTTP request), and a single shared
    accumulator would let one session's spend exhaust every other session's
    budget too (a genuine cross-session denial-of-service, not hypothetical —
    found while wiring exactly this deployment shape). Callers that never pass
    thread_id (nearly every existing single-session test/example) all share
    one implicit default bucket, so `.cost_usd`/`.steps` keep meaning exactly
    what they meant before this became thread-aware."""

    policy: Policy
    _spent: dict[str, float] = field(default_factory=dict)
    _steps: dict[str, int] = field(default_factory=dict)

    def spend(self, amount: float, *, thread_id: str = _DEFAULT_THREAD) -> None:
        total = self._spent.get(thread_id, 0.0) + amount
        self._spent[thread_id] = total
        if total > self.policy.max_cost_usd_per_thread:
            raise BudgetExceeded(f"spent ${total:.4f} > budget ${self.policy.max_cost_usd_per_thread}")

    def step(self, *, thread_id: str = _DEFAULT_THREAD) -> None:
        count = self._steps.get(thread_id, 0) + 1
        self._steps[thread_id] = count
        if count > self.policy.max_steps_per_thread:
            raise BudgetExceeded(f"{count} steps > max {self.policy.max_steps_per_thread}")

    def cost_usd_for(self, thread_id: str = _DEFAULT_THREAD) -> float:
        return self._spent.get(thread_id, 0.0)

    def steps_for(self, thread_id: str = _DEFAULT_THREAD) -> int:
        return self._steps.get(thread_id, 0)

    @property
    def cost_usd(self) -> float:
        """Sum across every thread this budget has tracked. A single-session
        caller (nearly every existing test/example — never passes thread_id)
        only ever has one bucket, so this is exactly that session's spend, same
        as before this became thread-aware. For a real multi-session graph,
        this is the deployment-wide total (what observability.render_dashboard
        shows) — use cost_usd_for(thread_id) for one session's own figure."""
        return sum(self._spent.values())

    @property
    def steps(self) -> int:
        return sum(self._steps.values())


class LatencyBudgetLike(Protocol):
    """Same swappable-interface posture as RunBudgetLike — a distributed
    deployment needs elapsed-time tracking shared across replicas too, not
    reset to zero every time a session happens to land on a fresh pod."""

    def check(self, *, thread_id: str = ...) -> None: ...
    def elapsed_s(self, *, thread_id: str = ...) -> float: ...


@dataclass
class LatencyBudget:
    """Cumulative wall-clock budget for a thread — RunBudget tracks cost and
    steps this same fail-closed way; this tracks elapsed time. Closes the real
    gap that per-step with_timeout()/step_timeout_s leaves open: a thread that
    stays just under the per-step timeout on every single call can still run
    indefinitely in total without this.

    Tracked per thread_id (see RunBudget's docstring for why): a shared graph
    running for hours across many sessions must not fail every NEW session's
    very first turn just because the deployment itself has been up longer than
    max_seconds. thread_id defaults to one implicit bucket, matching every
    pre-existing single-session caller's behavior unchanged."""

    max_seconds: float
    _start: dict[str, float] = field(default_factory=dict)

    def check(self, *, thread_id: str = _DEFAULT_THREAD) -> None:
        start = self._start.setdefault(thread_id, time.time())
        elapsed = time.time() - start
        if elapsed > self.max_seconds:
            raise BudgetExceeded(f"thread exceeded latency budget: {elapsed:.1f}s > {self.max_seconds}s")

    def elapsed_s(self, *, thread_id: str = _DEFAULT_THREAD) -> float:
        start = self._start.get(thread_id)
        return 0.0 if start is None else time.time() - start


def with_retry(fn: Callable[[], T], *, attempts: int = 3, backoff_s: float = 0.5) -> T:
    last_err: Exception | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last_err = e
            time.sleep(backoff_s * (2**i))
    assert last_err is not None
    raise last_err


def with_timeout(fn: Callable[[], T], *, seconds: float) -> T:
    """Runs fn on a worker thread and raises concurrent.futures.TimeoutError past `seconds`."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(fn).result(timeout=seconds)


class CircuitBreakerLike(Protocol):
    """Same swappable-interface posture as RunBudgetLike — a distributed
    deployment needs one tool's open/closed state shared across replicas
    (a tool failing on pod A should trip the breaker seen by pod B too),
    not independently tracked per process."""

    def record(self, tool: str, ok: bool) -> None: ...
    def is_open(self, tool: str) -> bool: ...


@dataclass
class CircuitBreaker:
    """Opens per tool after `failure_threshold` consecutive failures; closes on the next success."""

    failure_threshold: int = 3
    _consecutive_failures: dict[str, int] = field(default_factory=dict)
    _open: set[str] = field(default_factory=set)

    def record(self, tool: str, ok: bool) -> None:
        if ok:
            self._consecutive_failures[tool] = 0
            self._open.discard(tool)
            return
        self._consecutive_failures[tool] = self._consecutive_failures.get(tool, 0) + 1
        if self._consecutive_failures[tool] >= self.failure_threshold:
            self._open.add(tool)

    def is_open(self, tool: str) -> bool:
        return tool in self._open


class RateLimiterLike(Protocol):
    """Same swappable-interface posture as RunBudgetLike — a rate limit is
    only real across a fleet if every replica shares the same bucket (a
    Redis-backed token bucket, e.g.); RateLimiter's in-process dict means
    N replicas each independently allow the configured rate, so the real
    ceiling is N times what was configured."""

    def allow(self, key: str) -> bool: ...


@dataclass
class RateLimiter:
    """Token-bucket limiter, one independent bucket per key (a model, a tool, a
    tenant — anything) — refills continuously at rate_per_s, caps at burst.
    Generic on purpose: llm_gateway.py and tools_gateway.py both use this one."""

    rate_per_s: float
    burst: int
    _tokens: dict[str, float] = field(default_factory=dict)
    _last: dict[str, float] = field(default_factory=dict)

    def allow(self, key: str) -> bool:
        now = time.time()
        tokens = min(self.burst, self._tokens.get(key, self.burst) + (now - self._last.get(key, now)) * self.rate_per_s)
        self._last[key] = now
        if tokens < 1:
            self._tokens[key] = tokens
            return False
        self._tokens[key] = tokens - 1
        return True


class RateLimitExceeded(Exception):
    pass


class SLATrackerLike(Protocol):
    """Same swappable-interface posture as RunBudgetLike — a fleet-wide SLA
    (the actual thing "uptime" means) can't be computed from N replicas
    each holding their own independent rolling window; a real deployment
    needs this backed by something shared (or a real SLO platform)."""

    def record(self, *, ok: bool, latency_ms: float) -> None: ...
    def success_rate(self) -> float: ...
    def p95_latency_ms(self) -> float: ...
    def error_budget_remaining(self) -> float: ...
    def breaches(self) -> list[str]: ...


@dataclass
class SLATracker:
    """Uptime / error-budget accounting against a target, the same "real but
    swappable" posture as RunBudget/LatencyBudget above: a rolling window of
    (ok, latency_ms) per completed task, not a time-series DB — swap for a
    real SLO platform (Datadog SLOs, Prometheus recording rules) once volume
    warrants it. RunBudget/LatencyBudget *enforce* a ceiling mid-task and
    raise; this *reports* on tasks already finished, closing the gap between
    "did this one request go over budget" and "are we meeting our uptime and
    latency commitments across every completed task this window."""

    target_success_rate: float = 0.999
    target_p95_latency_ms: float = 2000.0
    window: int = 1000  # rolling window: most recent N completed tasks
    _outcomes: list[tuple[bool, float]] = field(default_factory=list)

    def record(self, *, ok: bool, latency_ms: float) -> None:
        self._outcomes.append((ok, latency_ms))
        if len(self._outcomes) > self.window:
            self._outcomes.pop(0)

    def success_rate(self) -> float:
        if not self._outcomes:
            return 1.0
        return sum(1 for ok, _ in self._outcomes if ok) / len(self._outcomes)

    def p95_latency_ms(self) -> float:
        vals = sorted(latency for _, latency in self._outcomes)
        if not vals:
            return 0.0
        idx = min(len(vals) - 1, int(len(vals) * 0.95))
        return vals[idx]

    def error_budget_remaining(self) -> float:
        """Fraction of the allowed failure budget not yet spent this window.
        1.0 = fully intact, 0.0 = exhausted, negative = already breached."""
        allowed = 1 - self.target_success_rate
        actual = 1 - self.success_rate()
        if allowed == 0:
            return 1.0 if actual == 0 else 0.0
        return 1 - (actual / allowed)

    def breaches(self) -> list[str]:
        out = []
        if self.success_rate() < self.target_success_rate:
            out.append(f"success rate {self.success_rate():.3%} below SLA target {self.target_success_rate:.3%}")
        if self.p95_latency_ms() > self.target_p95_latency_ms:
            out.append(f"p95 latency {self.p95_latency_ms():.0f}ms exceeds SLA target {self.target_p95_latency_ms:.0f}ms")
        return out

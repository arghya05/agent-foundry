import time

import pytest

from agent_foundry.contracts import Policy
from agent_foundry.runtime import (
    BudgetExceeded, CircuitBreaker, LatencyBudget, RateLimiter, RunBudget,
    SLATracker, with_retry, with_timeout,
)


def test_run_budget_cost_ceiling_fails_closed():
    budget = RunBudget(Policy(max_cost_usd_per_thread=1.0, max_steps_per_thread=100))
    budget.spend(0.5)
    with pytest.raises(BudgetExceeded):
        budget.spend(0.6)


def test_run_budget_step_ceiling_fails_closed():
    budget = RunBudget(Policy(max_cost_usd_per_thread=100, max_steps_per_thread=2))
    budget.step()
    budget.step()
    with pytest.raises(BudgetExceeded):
        budget.step()


def test_latency_budget_stops_a_thread_under_the_cumulative_ceiling():
    lb = LatencyBudget(max_seconds=0.05)
    lb.check()  # immediately, should still be fine
    time.sleep(0.06)
    with pytest.raises(BudgetExceeded):
        lb.check()


def test_run_budget_thread_ids_track_independently():
    budget = RunBudget(Policy(max_cost_usd_per_thread=1.0, max_steps_per_thread=100))
    budget.spend(0.9, thread_id="patient-a")
    budget.spend(0.1, thread_id="patient-b")  # would exceed 1.0 if it shared patient-a's bucket
    assert budget.cost_usd_for("patient-a") == 0.9
    assert budget.cost_usd_for("patient-b") == 0.1


def test_run_budget_ceiling_applies_per_thread_not_globally():
    budget = RunBudget(Policy(max_cost_usd_per_thread=1.0, max_steps_per_thread=100))
    budget.spend(0.9, thread_id="patient-a")
    budget.spend(0.9, thread_id="patient-b")  # under patient-b's OWN 1.0 ceiling — must not see patient-a's spend
    with pytest.raises(BudgetExceeded):
        budget.spend(0.2, thread_id="patient-a")  # patient-a's own ceiling is what trips


def test_run_budget_untagged_calls_share_one_default_bucket_unchanged():
    """Every pre-existing caller that never passes thread_id must see exactly
    the same behavior as before RunBudget became thread-aware."""
    budget = RunBudget(Policy(max_cost_usd_per_thread=1.0, max_steps_per_thread=100))
    budget.spend(0.5)
    assert budget.cost_usd == 0.5
    with pytest.raises(BudgetExceeded):
        budget.spend(0.6)


def test_latency_budget_thread_ids_track_independently():
    lb = LatencyBudget(max_seconds=0.05)
    lb.check(thread_id="patient-a")
    time.sleep(0.06)
    with pytest.raises(BudgetExceeded):
        lb.check(thread_id="patient-a")
    lb.check(thread_id="patient-b")  # a brand-new session started just now — must not inherit patient-a's elapsed time


def test_circuit_breaker_opens_after_threshold_and_closes_on_success():
    cb = CircuitBreaker(failure_threshold=3)
    assert not cb.is_open("t")
    cb.record("t", False); cb.record("t", False)
    assert not cb.is_open("t")
    cb.record("t", False)
    assert cb.is_open("t")
    cb.record("t", True)
    assert not cb.is_open("t")


def test_rate_limiter_burst_then_denies():
    rl = RateLimiter(rate_per_s=0.001, burst=2)
    assert rl.allow("k") and rl.allow("k")
    assert not rl.allow("k")


def test_with_retry_succeeds_after_transient_failures():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("transient")
        return "ok"

    assert with_retry(flaky, attempts=5, backoff_s=0.001) == "ok"
    assert calls["n"] == 3


def test_with_timeout_raises_past_the_limit():
    import concurrent.futures

    with pytest.raises(concurrent.futures.TimeoutError):
        with_timeout(lambda: time.sleep(0.2), seconds=0.02)


def test_sla_tracker_reports_no_breach_when_within_target():
    sla = SLATracker(target_success_rate=0.99, target_p95_latency_ms=1000)
    for _ in range(99):
        sla.record(ok=True, latency_ms=100)
    sla.record(ok=False, latency_ms=100)  # exactly 99% success — meets a 0.99 target
    assert sla.breaches() == []


def test_sla_tracker_reports_success_rate_breach():
    sla = SLATracker(target_success_rate=0.99, target_p95_latency_ms=1000)
    for _ in range(9):
        sla.record(ok=True, latency_ms=100)
    sla.record(ok=False, latency_ms=100)  # 90% success — well under a 99% target
    breaches = sla.breaches()
    assert len(breaches) == 1
    assert "success rate" in breaches[0]


def test_sla_tracker_reports_latency_breach():
    sla = SLATracker(target_success_rate=0.5, target_p95_latency_ms=500)
    sla.record(ok=True, latency_ms=9000)
    breaches = sla.breaches()
    assert any("p95 latency" in b for b in breaches)


def test_sla_tracker_error_budget_remaining_shrinks_toward_zero_and_below():
    sla = SLATracker(target_success_rate=0.99)
    assert sla.error_budget_remaining() == 1.0  # no data yet — fully intact
    for _ in range(9):
        sla.record(ok=True, latency_ms=1)
    sla.record(ok=False, latency_ms=1)  # 10% failure rate against a 1% allowance
    assert sla.error_budget_remaining() < 0  # budget already blown


def test_sla_tracker_window_keeps_only_the_most_recent_n_tasks():
    sla = SLATracker(window=3)
    sla.record(ok=False, latency_ms=1)
    sla.record(ok=False, latency_ms=1)
    sla.record(ok=True, latency_ms=1)
    sla.record(ok=True, latency_ms=1)  # pushes the oldest failure out of the window
    sla.record(ok=True, latency_ms=1)
    assert sla.success_rate() == 1.0

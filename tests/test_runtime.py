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


def _run_concurrently(fn, *, n_threads: int) -> list[BaseException]:
    import threading

    errors: list[BaseException] = []

    def worker() -> None:
        try:
            fn()
        except BaseException as e:  # noqa: BLE001 — concurrency probe, capture everything
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return errors


def test_run_budget_spend_does_not_lose_updates_under_concurrent_callers():
    """spend()'s body is a classic read-modify-write
    (self._spent.get(...) + amount, then a separate dict write) — the
    textbook Python counter-race pattern: without synchronization, two
    threads can both read the same pre-spend total, both add their own
    amount, and whichever writes last overwrites the other's update,
    undercounting real spend. Honest note: I tried to reproduce this race
    empirically on an unlocked copy of this exact read-modify-write (50
    threads x 200 spends, 10,000 total ops) and did NOT get a single lost
    update — CPython's GIL makes each individual dict get()/__setitem__ fast
    enough, relative to its switch-check granularity, that this specific
    short critical section rarely gets interleaved in a run this size, even
    though the hazard is real and well-documented (it's not guaranteed safe
    by the language, just statistically rare to hit here). self._lock closes
    the hazard on principle, not because this test caught it failing — this
    test instead confirms the locked class is correct and lock-free under
    real concurrent load, which is what matters going forward. High ceiling
    (no BudgetExceeded should fire) so this isolates the lost-update
    question from the separate ceiling-check behavior."""
    budget = RunBudget(Policy(max_cost_usd_per_thread=10_000.0, max_steps_per_thread=10_000_000))
    n_threads, spends_per_thread, amount = 50, 200, 0.01

    def spend_many() -> None:
        for _ in range(spends_per_thread):
            budget.spend(amount, thread_id="shared")

    errors = _run_concurrently(spend_many, n_threads=n_threads)
    assert not errors, f"unexpected errors: {errors[:3]!r}"

    expected = round(n_threads * spends_per_thread * amount, 2)
    assert round(budget.cost_usd_for("shared"), 2) == expected, (
        f"lost updates under concurrency: expected ${expected}, got ${budget.cost_usd_for('shared'):.2f}"
    )


def test_run_budget_step_does_not_lose_updates_under_concurrent_callers():
    budget = RunBudget(Policy(max_cost_usd_per_thread=10_000.0, max_steps_per_thread=10_000_000))
    n_threads, steps_per_thread = 50, 200

    def step_many() -> None:
        for _ in range(steps_per_thread):
            budget.step(thread_id="shared")

    errors = _run_concurrently(step_many, n_threads=n_threads)
    assert not errors, f"unexpected errors: {errors[:3]!r}"
    assert budget.steps_for("shared") == n_threads * steps_per_thread


def test_circuit_breaker_consecutive_failure_count_does_not_lose_updates_under_concurrency():
    breaker = CircuitBreaker(failure_threshold=10_000_000)  # high enough it never actually opens
    n_threads, failures_per_thread = 50, 200

    def fail_many() -> None:
        for _ in range(failures_per_thread):
            breaker.record("flaky_tool", ok=False)

    errors = _run_concurrently(fail_many, n_threads=n_threads)
    assert not errors, f"unexpected errors: {errors[:3]!r}"
    assert breaker._consecutive_failures["flaky_tool"] == n_threads * failures_per_thread


def test_sla_tracker_outcome_count_does_not_lose_updates_under_concurrent_recording():
    """record()'s append+trim isn't the lost-update-prone read-modify-write
    RunBudget.spend() is, but it's still not safe unsynchronized: a list
    isn't guaranteed atomic for append-while-another-thread-reads-len. This
    also exercises breaches() (which calls success_rate()/p95_latency_ms()
    on self) under real concurrent load — see the dedicated deadlock test
    below for the specific, deterministic bug that path guards against."""
    sla = SLATracker(window=100_000)
    n_threads, records_per_thread = 50, 200

    def record_many() -> None:
        for _ in range(records_per_thread):
            sla.record(ok=True, latency_ms=1.0)
            sla.breaches()  # exercises the RLock re-entrancy path under real concurrency

    errors = _run_concurrently(record_many, n_threads=n_threads)
    assert not errors, f"unexpected errors: {errors[:3]!r}"
    assert len(sla._outcomes) == n_threads * records_per_thread


def test_sla_tracker_breaches_and_error_budget_remaining_do_not_self_deadlock():
    """The concrete, deterministic reason _lock is an RLock and not a plain
    Lock: error_budget_remaining() and breaches() both call success_rate()/
    p95_latency_ms() on self WHILE already holding _lock. A plain Lock is
    not reentrant — the same thread trying to re-acquire a Lock it already
    holds blocks forever, not just occasionally under bad timing, unlike
    the probabilistic races in the tests above. Confirmed directly: swapping
    _lock for threading.Lock() and calling breaches() hangs indefinitely
    (verified with a 5s subprocess timeout during development). This test
    proves the shipped RLock does NOT hang, on a background thread with a
    real wall-clock timeout, so a future accidental Lock/RLock swap fails
    this test instead of hanging the whole suite."""
    import threading

    sla = SLATracker(window=100)
    sla.record(ok=False, latency_ms=1.0)

    done = threading.Event()

    def call_both() -> None:
        sla.error_budget_remaining()
        sla.breaches()
        done.set()

    t = threading.Thread(target=call_both, daemon=True)
    t.start()
    finished = done.wait(timeout=5.0)
    assert finished, "breaches()/error_budget_remaining() deadlocked against themselves — _lock must be an RLock"

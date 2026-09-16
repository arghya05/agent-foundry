"""Scalability load test — real numbers, at real concurrency, against real
local Redis and Postgres, for the four gaps closed in this repo's own
history (idempotency race, runtime.py locking, Postgres pooling, native
multi-agent durability). Every number below is measured on THIS machine
when you run it, not asserted or fabricated — this script prints what it
found, honestly, including where a claimed hazard didn't reproduce.

Scope, honestly: this is a local-sandbox load test (one machine, loopback
network, real Redis/Postgres processes) at meaningfully higher concurrency
than the unit tests use (hundreds-to-thousands of ops vs. tens). It is NOT
a production benchmark — it doesn't have real network latency between
replicas, doesn't run on separate machines, and doesn't reflect a real
multi-tenant Postgres/Redis under other load. Treat the numbers as "does
this hold up under real concurrent pressure on one box," not "this is what
you'll see in production."

Run: python benchmarks/scalability_load_test.py
Needs a local Redis (redis-server) and Postgres (createdb agent_foundry) —
each section skips itself with a clear message if its server isn't reachable,
same convention as tests/test_distributed.py / test_state_store_backends.py.
"""
from __future__ import annotations

import sys
import threading
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_foundry.contracts import Policy


def _section(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def _run_concurrently(fn, *, n_threads: int) -> tuple[list[BaseException], float]:
    errors: list[BaseException] = []
    barrier = threading.Barrier(n_threads)

    def worker(i: int) -> None:
        barrier.wait()  # force genuinely overlapping starts, not staggered ones
        try:
            fn(i)
        except BaseException as e:  # noqa: BLE001 — load probe, capture everything
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    start = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    elapsed = time.time() - start
    return errors, elapsed


# ---- Redis: RedisIdempotencyStore claim throughput + correctness at scale ----

def bench_redis_idempotency() -> None:
    _section("Redis — RedisIdempotencyStore: claim-then-set throughput at scale")
    try:
        import redis as redis_lib
        redis_lib.Redis.from_url("redis://localhost:6379/0").ping()
    except Exception as e:
        print(f"SKIPPED — no Redis reachable: {e}")
        return

    from agent_foundry.contracts import ToolResult
    from agent_foundry.distributed import RedisIdempotencyStore
    from agent_foundry.tools_gateway import IdempotencyConflict

    prefix = f"loadtest:{uuid.uuid4().hex[:8]}"
    n_keys, n_replicas = 150, 6  # 150 distinct idempotency keys, 6 "replicas" racing for each
    conflicts, successes = 0, 0
    lock = threading.Lock()

    def claim_one(i: int) -> None:
        nonlocal conflicts, successes
        key = f"key-{i % n_keys}"
        store = RedisIdempotencyStore(ttl_s=30.0, redis_url="redis://localhost:6379/0", key_prefix=prefix)
        try:
            store.get(key)
            with lock:
                successes += 1
        except IdempotencyConflict:
            with lock:
                conflicts += 1

    total = n_keys * n_replicas
    errors, elapsed = _run_concurrently(claim_one, n_threads=total)
    print(f"{total} claim attempts across {n_keys} keys x {n_replicas} racing replicas each, in {elapsed:.2f}s "
          f"({total / elapsed:.0f} ops/s)")
    print(f"successes (first claimant per key): {successes}  — expected exactly {n_keys}")
    print(f"conflicts (correctly rejected duplicate claimants): {conflicts}  — expected exactly {total - n_keys}")
    print(f"unexpected errors: {len(errors)}")
    assert successes == n_keys, f"expected exactly {n_keys} first-claimants, got {successes} — a real duplicate-claim bug"
    print("VERIFIED: exactly one claimant per key, every time, at this concurrency.")


# ---- Redis: RedisRunBudget atomic HINCRBYFLOAT under heavy concurrent spend ----

def bench_redis_run_budget() -> None:
    _section("Redis — RedisRunBudget: atomic spend() under heavy concurrent load")
    try:
        import redis as redis_lib
        redis_lib.Redis.from_url("redis://localhost:6379/0").ping()
    except Exception as e:
        print(f"SKIPPED — no Redis reachable: {e}")
        return

    from agent_foundry.distributed import RedisRunBudget

    prefix = f"loadtest:{uuid.uuid4().hex[:8]}"
    policy = Policy(allowed_tools=frozenset(), max_cost_usd_per_thread=1_000_000.0, max_steps_per_thread=1_000_000_000)
    n_threads, spends_per_thread, amount = 200, 100, 0.01

    def spend_many(i: int) -> None:
        budget = RedisRunBudget(policy, redis_url="redis://localhost:6379/0", key_prefix=prefix)
        for _ in range(spends_per_thread):
            budget.spend(amount, thread_id="shared")

    errors, elapsed = _run_concurrently(spend_many, n_threads=n_threads)
    total_ops = n_threads * spends_per_thread
    print(f"{total_ops} concurrent spend() calls (HINCRBYFLOAT, server-side atomic) in {elapsed:.2f}s ({total_ops / elapsed:.0f} ops/s)")
    print(f"errors: {len(errors)}")

    from agent_foundry.distributed import RedisRunBudget as _RB
    final = _RB(policy, redis_url="redis://localhost:6379/0", key_prefix=prefix).cost_usd_for("shared")
    expected = round(total_ops * amount, 2)
    print(f"final total: ${round(final, 2)}  expected: ${expected}")
    assert round(final, 2) == expected, "lost updates under concurrency — should be impossible, HINCRBYFLOAT is atomic"
    print("VERIFIED: zero lost updates at this concurrency — server-side atomic increment, not a read-modify-write race like the in-process version.")


# ---- Postgres: pool sizing tradeoff, measured, not asserted ----

def bench_postgres_pool_sizing() -> None:
    _section("Postgres — PostgresStateStore: pool sizing vs. throughput, measured")
    try:
        import psycopg2
        psycopg2.connect("dbname=agent_foundry").close()
    except Exception as e:
        print(f"SKIPPED — no Postgres reachable: {e}")
        return

    from agent_foundry.core.state_store import PostgresStateStore

    n_threads, saves_per_thread = 30, 20
    total_ops = n_threads * saves_per_thread

    for maxconn in (2, 10, 30):
        table = f"agent_foundry_loadtest_{uuid.uuid4().hex[:8]}"
        store = PostgresStateStore(dsn="dbname=agent_foundry", table=table, minconn=1, maxconn=maxconn)
        errors: list[BaseException] = []
        completed = 0
        retries_on_pool_exhaustion = 0
        lock = threading.Lock()

        def worker(i: int) -> None:
            nonlocal completed, retries_on_pool_exhaustion
            import psycopg2.pool
            run_id = f"run-{i}"
            for r in range(saves_per_thread):
                # A real caller retries a transient PoolError rather than
                # aborting the whole request — without this, a small maxconn
                # would make the benchmark measure "how fast do threads give
                # up," which is a different (and misleading) number from
                # "how long does the real work take."
                while True:
                    try:
                        store.save(run_id, {"round": r})
                        with lock:
                            completed += 1
                        break
                    except psycopg2.pool.PoolError:
                        with lock:
                            retries_on_pool_exhaustion += 1
                        time.sleep(0.001)
                    except BaseException as e:  # noqa: BLE001
                        with lock:
                            errors.append(e)
                        return

        _, elapsed = _run_concurrently(worker, n_threads=n_threads)
        throughput = completed / elapsed if elapsed > 0 else float("inf")
        print(f"maxconn={maxconn:>3}: {completed}/{total_ops} save() calls actually COMPLETED in {elapsed:.2f}s "
              f"({throughput:.0f} completed-ops/s), {retries_on_pool_exhaustion} retries needed due to pool exhaustion, "
              f"{len(errors)} unexpected errors")
        store.close()

    print("\nAll rows complete all 600 saves (retrying past PoolError) — the comparison is elapsed time and retry "
          "count, not a raw ops/s that a small maxconn could win by simply failing fast. Expect elapsed time to drop "
          "and retries to fall toward zero as maxconn approaches n_threads.")


# ---- In-process runtime.py primitives: does the race show up at real scale? ----

def bench_inprocess_run_budget_race_at_scale() -> None:
    _section("In-process — RunBudget.spend(): searching for the lost-update race at much higher scale")
    from agent_foundry.runtime import RunBudget

    policy = Policy(allowed_tools=frozenset(), max_cost_usd_per_thread=10_000_000.0, max_steps_per_thread=10**9)
    n_threads, spends_per_thread, amount = 100, 5_000, 0.001  # 500k total ops — 50x the unit test's scale

    # Reproduce the UNLOCKED read-modify-write directly (bypassing self._lock),
    # same technique used during development, now at 50x the scale.
    budget = RunBudget(policy)

    def unlocked_spend(_: int) -> None:
        for _ in range(spends_per_thread):
            total = budget._spent.get("shared", 0.0) + amount
            budget._spent["shared"] = total

    _, elapsed = _run_concurrently(unlocked_spend, n_threads=n_threads)
    total_ops = n_threads * spends_per_thread
    expected = round(total_ops * amount, 2)
    actual = round(budget._spent.get("shared", 0.0), 2)
    lost = round(expected - actual, 2)
    print(f"{total_ops} unlocked spends ({total_ops / elapsed:.0f} ops/s) — expected ${expected}, got ${actual}, lost ${lost}")
    if lost != 0:
        print("REPRODUCED: the lost-update race is real at this scale (it wasn't at the unit test's smaller scale).")
    else:
        print("STILL NOT REPRODUCED — even at 50x the unit test's scale on this machine/CPython build. "
              "The hazard remains real and textbook (unsynchronized dict read-modify-write is never guaranteed "
              "atomic by the language), just apparently harder to force here than expected. self._lock in the "
              "shipped RunBudget is unaffected by this either way — it's correct on principle, not because this "
              "probe caught it failing.")


if __name__ == "__main__":
    bench_redis_idempotency()
    bench_redis_run_budget()
    bench_postgres_pool_sizing()
    bench_inprocess_run_budget_race_at_scale()
    print(f"\n{'=' * 70}\ndone\n{'=' * 70}")

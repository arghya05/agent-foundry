"""NativeEngine's per-thread locking. Honest note on what these tests can and
can't prove: the race a per-thread lock guards against (e.g. RunBudget.step()'s
read-modify-write counter, or two turns interleaving on the same thread's
state) is a genuine correctness issue in principle — CPython's GIL does not
make compound read-modify-write sequences on a dict atomic. In practice,
repeated attempts to reproduce data corruption from it empirically (up to 300
concurrent threads on one thread_id, with sys.setswitchinterval lowered to
force frequent GIL switches) did not reliably fail even with the lock
removed — the critical sections here are short enough that CPython's GIL
happens to protect them most of the time on this platform. The lock is kept
because correctness shouldn't rest on GIL implementation details (a
sufficiently different Python build, interpreter version, or genuinely
parallel runtime could expose it), not because a failing-without-it repro
was captured. What IS reliably, deterministically testable: the locking
mechanism itself (same thread_id -> same Lock instance) and that different
thread_ids never block each other.
"""
from __future__ import annotations

import threading
import time

from agent_foundry import Agent, ExecutionContext
from agent_foundry.contracts import LLMResponse
from agent_foundry.core.native_engine import NativeEngine
from agent_foundry.llm_gateway import LLMGateway


class _SlowProvider:
    def __init__(self, delay_s: float = 0.05) -> None:
        self.delay_s = delay_s

    def complete(self, messages, *, model, tools=None, **kw):
        time.sleep(self.delay_s)
        return LLMResponse(text="ok", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0)


def test_lock_for_returns_the_same_lock_for_the_same_thread_id():
    engine = NativeEngine()

    a1 = engine._lock_for("thread-a")
    a2 = engine._lock_for("thread-a")
    b = engine._lock_for("thread-b")

    assert a1 is a2
    assert a1 is not b


def test_lock_for_is_safe_to_call_concurrently_for_new_thread_ids():
    """Two threads racing to create the lock for the SAME brand-new thread_id
    must still converge on one Lock instance, not one each — this is what
    _threads_lock (guarding creation) is for."""
    engine = NativeEngine()
    results: list[threading.Lock] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        barrier.wait()
        results.append(engine._lock_for("brand-new-thread"))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results[0] is results[1]


def test_concurrent_runs_on_different_threads_do_not_block_each_other():
    provider = _SlowProvider(delay_s=0.05)
    agent = Agent("chatty", "Chat.", runtime="native", llm=LLMGateway(provider=provider))
    n_threads = 5
    results: dict[int, str] = {}

    def worker(i: int) -> None:
        r = agent.run("hi", context=ExecutionContext(thread_id=f"thread-{i}"))
        results[i] = r.content

    start = time.time()
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)
    elapsed = time.time() - start

    assert len(results) == n_threads
    # if different thread_ids serialized against each other, this would take
    # ~n_threads * delay; running concurrently, it should take close to one delay
    assert elapsed < 0.05 * n_threads


def test_many_concurrent_runs_on_one_thread_complete_without_crashing():
    """A liveness/sanity check, not proof the lock prevents corruption (see
    module docstring) — concurrent turns on one thread_id must all complete
    without raising and without deadlocking."""
    provider = _SlowProvider(delay_s=0.01)
    agent = Agent("chatty", "Chat.", runtime="native", llm=LLMGateway(provider=provider))
    context = ExecutionContext(thread_id="shared-thread")
    n_workers = 12
    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            agent.run(f"message {i}", context=context)
        except Exception as e:  # pragma: no cover - asserted on below
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors, f"worker threads raised: {errors}"
    assert all(not t.is_alive() for t in threads)  # no deadlock

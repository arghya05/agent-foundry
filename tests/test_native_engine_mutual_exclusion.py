"""A deterministic proof of NativeEngine's actual guarantee — not a
probabilistic timing test like the earlier (honestly-labeled) attempts.
The per-thread lock's job is exactly one invariant: no two turns execute
concurrently on the same thread_id. This forces the exact interleaving
needed to observe a violation directly, instead of hoping a race manifests
under scheduling luck: thread A blocks *inside* its turn (holding the lock,
mid-LLM-call) on an Event; thread B is given a head start to attempt its own
turn on the same thread_id; if the lock is doing its job, thread B cannot
have entered its own critical section while A is still blocked inside
its — checked directly via a shared "who's inside right now" counter that
must never exceed 1.
"""
from __future__ import annotations

import threading

from agent_foundry import Agent, ExecutionContext
from agent_foundry.contracts import LLMResponse
from agent_foundry.llm_gateway import LLMGateway


class _BlockingProvider:
    """Signals entry, then blocks until told to proceed — lets a test force
    two turns to overlap in time deterministically instead of hoping."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.concurrent_entries = 0
        self.max_concurrent = 0
        self._count_lock = threading.Lock()

    def complete(self, messages, *, model, tools=None, **kw):
        with self._count_lock:
            self.concurrent_entries += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent_entries)
        self.entered.set()
        self.release.wait(timeout=5)
        with self._count_lock:
            self.concurrent_entries -= 1
        return LLMResponse(text="ok", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0)


def test_two_turns_on_the_same_thread_never_execute_concurrently():
    provider = _BlockingProvider()
    agent = Agent("chatty", "Chat.", runtime="native", llm=LLMGateway(provider=provider))
    context = ExecutionContext(thread_id="mutex-thread")

    thread_a = threading.Thread(target=lambda: agent.run("from A", context=context))
    thread_a.start()
    provider.entered.wait(timeout=5)  # A is now inside its LLM call, holding the per-thread lock

    # give B every opportunity to sneak in while A is blocked
    thread_b_entered_while_a_still_blocked = threading.Event()

    def try_b() -> None:
        # if the lock is broken, B's own complete() call fires immediately,
        # setting `entered` again while A hasn't released yet — max_concurrent would hit 2
        agent.run("from B", context=context)

    thread_b = threading.Thread(target=try_b)
    thread_b.start()
    thread_b.join(timeout=0.2)  # B should NOT have finished — it must be waiting on the lock, not running

    b_finished_before_a_released = not thread_b.is_alive()

    provider.release.set()  # let A finish
    thread_a.join(timeout=5)
    provider.release.set()  # B's own turn also needs to release
    thread_b.join(timeout=5)

    assert provider.max_concurrent == 1, f"two turns overlapped inside the LLM call — lock failed (max_concurrent={provider.max_concurrent})"
    assert not b_finished_before_a_released, "B ran its full turn while A was still blocked — the per-thread lock did not serialize them"

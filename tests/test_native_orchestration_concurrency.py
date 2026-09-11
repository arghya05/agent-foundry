"""A deterministic proof of the native multi-agent graphs' per-thread lock
(core/native_orchestration.py's `_ThreadLocks`) — same posture as
test_native_engine_mutual_exclusion.py's own docstring: not a probabilistic
timing test, but a forced interleaving that would observably fail if the
lock were missing (as it was before this test was added: `_history_for`
used to lock only its own dict access, then the caller mutated the shared
per-thread history list — appended to it, ran a router/specialist call,
appended again — entirely OUTSIDE that lock).

Uses Workflow.supervisor as the one representative topology — supervisor,
swarm, blackboard and debate all share the identical
`self._locks.lock_for(thread_id)`-wraps-the-whole-`invoke()` fix, from the
same `_ThreadLocks` class, so proving it once here covers the pattern; each
one's own graph still gets a plain functional test in
test_native_orchestration.py.
"""
from __future__ import annotations

import threading
import time

from agent_foundry import Agent, ExecutionContext, Workflow
from agent_foundry.contracts import LLMResponse
from agent_foundry.llm_gateway import LLMGateway

from conftest import ScriptedProvider


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
        return LLMResponse(text="ROUTE billing", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0)


def _make_supervisor_workflow(router_provider):
    billing = Agent("billing", "billing", llm=LLMGateway(provider=ScriptedProvider(["handled"] * 10)))
    router_llm = LLMGateway(provider=router_provider)
    return Workflow.supervisor(prompt="route", agents={"billing": billing}, llm=router_llm, runtime="native")


def test_two_turns_on_the_same_thread_never_execute_concurrently():
    provider = _BlockingProvider()
    workflow = _make_supervisor_workflow(provider)
    context = ExecutionContext(thread_id="sup-mutex")

    thread_a = threading.Thread(target=lambda: workflow.run("from A", context=context))
    thread_a.start()
    provider.entered.wait(timeout=5)  # A is now inside the router's LLM call, holding the per-thread lock

    thread_b = threading.Thread(target=lambda: workflow.run("from B", context=context))  # SAME thread_id
    thread_b.start()
    thread_b.join(timeout=0.2)  # B should NOT have finished — it must be waiting on the lock, not running

    b_finished_before_a_released = not thread_b.is_alive()

    provider.release.set()  # let A finish
    thread_a.join(timeout=5)
    provider.release.set()  # B's own turn also needs to release
    thread_b.join(timeout=5)

    assert provider.max_concurrent == 1, f"two turns overlapped inside the router's LLM call — lock failed (max_concurrent={provider.max_concurrent})"
    assert not b_finished_before_a_released, "B ran its full turn while A was still blocked — the per-thread lock did not serialize them"


def test_two_turns_on_different_threads_run_concurrently():
    """The lock must NOT over-serialize unrelated conversations — two
    different thread_ids should be able to be inside the router call at the
    same time."""
    provider = _BlockingProvider()
    workflow = _make_supervisor_workflow(provider)

    thread_a = threading.Thread(target=lambda: workflow.run("from A", context=ExecutionContext(thread_id="sup-mutex-a")))
    thread_b = threading.Thread(target=lambda: workflow.run("from B", context=ExecutionContext(thread_id="sup-mutex-b")))
    thread_a.start()
    thread_b.start()

    # Poll (bounded) for both to have entered the blocking call, rather than
    # a fixed sleep-and-hope — entered.wait() alone only guarantees the
    # FIRST one arrived, not both.
    deadline = time.time() + 5
    while provider.concurrent_entries < 2 and time.time() < deadline:
        time.sleep(0.01)

    provider.release.set()
    thread_a.join(timeout=5)
    thread_b.join(timeout=5)

    assert provider.max_concurrent == 2, f"different thread_ids were serialized against each other (max_concurrent={provider.max_concurrent})"

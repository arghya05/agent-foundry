"""Proves Workflow.fanout/.dag(runtime="native", max_concurrency=K) actually
bounds how many workers/steps run at once — same deterministic
forced-interleaving technique as test_native_orchestration_concurrency.py's
_BlockingProvider, generalized from "at most 1 or 2" to "at most K out of
N," and checked alongside "all N still completed" so a bound can't be
mistaken for silently dropped work.
"""
from __future__ import annotations

import threading

from agent_foundry import Agent, ExecutionContext, Workflow
from agent_foundry.contracts import LLMResponse
from agent_foundry.llm_gateway import LLMGateway
from agent_foundry.orchestration import DAGStep


class _ConcurrencyCountingProvider:
    """Every call blocks briefly on a shared Barrier-like gate so many
    calls overlap in time if the pool lets them, while a lock-guarded
    counter records the true peak concurrency observed."""

    def __init__(self, *, hold_s: float = 0.05) -> None:
        self._hold_s = hold_s
        self.concurrent_entries = 0
        self.max_concurrent = 0
        self._count_lock = threading.Lock()

    def complete(self, messages, *, model, tools=None, **kw):
        import time

        with self._count_lock:
            self.concurrent_entries += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent_entries)
        time.sleep(self._hold_s)
        with self._count_lock:
            self.concurrent_entries -= 1
        return LLMResponse(text=f"processed: {messages[-1]['content']}", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0)


def test_fanout_native_bounds_concurrency_and_still_completes_every_item():
    provider = _ConcurrencyCountingProvider()
    worker = Agent("classifier", "classify", llm=LLMGateway(provider=provider))
    items = [f"item-{i}" for i in range(20)]

    workflow = Workflow.fanout(agent=worker, runtime="native", max_concurrency=4)
    outputs = workflow.run(items, context=ExecutionContext(thread_id="fanout-bound"))

    assert provider.max_concurrent <= 4, f"fanout exceeded its max_concurrency bound (saw {provider.max_concurrent})"
    assert set(outputs) == {f"processed: {item}" for item in items}, "bounding concurrency must not drop any item"


def test_dag_native_bounds_concurrency_within_one_wave_and_still_completes_every_step():
    provider = _ConcurrencyCountingProvider(hold_s=0.02)

    def make_step(i: int) -> DAGStep:
        def fn(results: dict) -> str:
            return provider.complete([{"role": "user", "content": f"step-{i}"}], model="m").text

        return DAGStep(name=f"step-{i}", fn=fn)  # no depends_on — all 20 are ready in the SAME wave

    steps = [make_step(i) for i in range(20)]
    workflow = Workflow.dag(steps=steps, runtime="native", max_concurrency=5)
    results = workflow.run()

    assert provider.max_concurrent <= 5, f"dag exceeded its max_concurrency bound within one wave (saw {provider.max_concurrent})"
    assert len(results) == 20, "bounding concurrency must not drop any step"

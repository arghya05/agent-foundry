import threading
import time

from agent_foundry.batch import IntervalScheduler, run_batch
from agent_foundry.contracts import Identity, LLMResponse, Policy
from agent_foundry.eval import EvalHarness
from agent_foundry.guardrails import GuardrailEngine
from agent_foundry.llm_gateway import LLMGateway
from agent_foundry.observability import Tracer
from agent_foundry.orchestration import build_agent_graph
from agent_foundry.runtime import RunBudget
from agent_foundry.tools_gateway import ToolRegistry


def _echo_graph():
    class Provider:
        def complete(self, messages, *, model, tools=None, **kw):
            last = messages[-1]
            return LLMResponse(text=f"handled: {last['content']}", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0)

    identity = Identity(id="t", tenant_id="acme")
    policy = Policy(allowed_tools=frozenset())
    return build_agent_graph(system_prompt="sys", llm=LLMGateway(provider=Provider()), tools=ToolRegistry(),
        guardrails=GuardrailEngine(policy), eval_harness=EvalHarness(), identity=identity, policy=policy,
        budget=RunBudget(policy), tracer=Tracer("batch-test"))


def test_run_batch_processes_every_item():
    graph = _echo_graph()
    items = [{"message": f"ticket {i}"} for i in range(5)]
    report = run_batch(graph, items, max_workers=3)
    assert len(report.results) == 5
    assert report.success_rate == 1.0
    assert all("ticket" in r.output for r in report.results)


def test_run_batch_gives_each_item_a_distinct_thread():
    graph = _echo_graph()
    items = [{"message": "a"}, {"message": "b"}]
    report = run_batch(graph, items)
    assert len({r.item_id for r in report.results}) == 2


def test_run_batch_reports_failures_without_stopping_other_items():
    class FlakyGraph:
        def invoke(self, state, config):
            if "bad" in state["messages"][0]["content"]:
                raise RuntimeError("simulated failure")
            return {"messages": [{"role": "assistant", "content": "ok"}]}

    report = run_batch(FlakyGraph(), [{"message": "good"}, {"message": "bad"}])
    assert report.success_rate == 0.5
    outcomes = {r.ok for r in report.results}
    assert outcomes == {True, False}


def test_thread_id_fn_can_derive_a_stable_id_per_item():
    graph = _echo_graph()
    items = [{"message": "hi", "ticket_id": "T-1"}]
    report = run_batch(graph, items, thread_id_fn=lambda item: item["ticket_id"])
    assert report.results[0].item_id == "T-1"


def test_interval_scheduler_fires_repeatedly_then_cancels():
    scheduler = IntervalScheduler()
    calls = []
    lock = threading.Lock()

    def tick():
        with lock:
            calls.append(time.time())

    handle = scheduler.schedule(tick, every_seconds=0.05)
    time.sleep(0.24)
    scheduler.cancel(handle)
    count_at_cancel = len(calls)
    time.sleep(0.15)
    assert count_at_cancel >= 3
    assert len(calls) == count_at_cancel  # no further ticks after cancel

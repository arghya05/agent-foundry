"""Native vs LangGraph — real, reproducible benchmark numbers across
single-agent and all 6 multi-agent topologies.

Uses a deterministic, in-process scripted LLM provider (no network, no live
API key) so every number below is real and reproducible on any machine —
this is a scripted-provider micro-benchmark of the two RUNTIMES' own
overhead, not a live-LLM latency claim (that needs a live provider, a real
dataset, and runtime/cost this environment doesn't have — see
agent_foundry/benchmark.py's own module docstring for the same honesty
posture). Cost figures use a fixed, documented per-call cost model
(_COST_PER_CALL_USD below), not live provider billing.

Run: python benchmarks/native_vs_langgraph.py
"""
from __future__ import annotations

import contextlib
import io
import sys
import time
import tracemalloc
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_foundry import Agent, Workflow
from agent_foundry.benchmark import BenchmarkCase, run_benchmark
from agent_foundry.blackboard import Blackboard
from agent_foundry.contracts import LLMResponse
from agent_foundry.llm_gateway import LLMGateway
from agent_foundry.orchestration import DAGStep

_COST_PER_CALL_USD = 0.0006  # a fixed, documented per-call cost model (~50 input + 20 output tokens at a small-model rate) — not live billing


class _DeterministicProvider:
    """Same contract as tests/conftest.py's ScriptedProvider, duplicated here
    (a dozen lines) so benchmarks/ stays a standalone tool, not test-suite
    coupled. `responses` is either a fixed list (consumed in order, one per
    call) or a single callable used for every call (for topologies where the
    number of LLM calls per case isn't fixed up front, e.g. supervisor's
    router + specialist calls)."""

    def __init__(self, responses):
        self._responses = responses

    def complete(self, messages, *, model, tools=None, **kw):
        if callable(self._responses):
            resp = self._responses(messages, model)
        else:
            resp = self._responses.pop(0)
            if callable(resp):
                resp = resp(messages, model)
        return LLMResponse(text=resp, model=model, input_tokens=50, output_tokens=20, cost_usd=_COST_PER_CALL_USD)


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


@dataclass
class Row:
    topology: str
    runtime: str
    p50_ms: float
    p95_ms: float
    memory_kb: float
    tool_calls_per_s: float | None
    cost_usd: float
    eval_score: float


N_CASES = 15


def bench_single_agent() -> list[Row]:
    rows = []
    for runtime in ("native", "langgraph"):
        cases = [BenchmarkCase(name=f"case-{i}", input=f"question {i}", check=lambda r: r.startswith("answer")) for i in range(N_CASES)]
        provider = _DeterministicProvider([f"answer {i}" for i in range(N_CASES)])
        tracemalloc.start()
        agent = Agent("bench-agent", "answer plainly", llm=LLMGateway(provider=provider), runtime=runtime)
        report = run_benchmark(agent.graph, cases)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        latencies_ms = [r.latency_s * 1000 for r in report.results]
        rows.append(Row(
            topology="agent", runtime=runtime, p50_ms=_percentile(latencies_ms, 0.5), p95_ms=_percentile(latencies_ms, 0.95),
            memory_kb=peak / 1024, tool_calls_per_s=None, cost_usd=agent.config.budget.cost_usd, eval_score=report.pass_rate,
        ))
    return rows


def bench_tool_throughput() -> list[Row]:
    """Only single-agent has a tool-calling scenario worth measuring
    throughput on — the other topologies below run 0-tool scripted turns."""

    def echo(value: str) -> str:
        return value

    def tool_reply(messages, model):
        return "done" if messages[-1]["role"] == "tool" else 'CALL echo {"value": "x"}'

    rows = []
    for runtime in ("native", "langgraph"):
        cases = [BenchmarkCase(name=f"tool-case-{i}", input=f"echo {i}", check=lambda r: r == "done") for i in range(N_CASES)]
        agent = Agent("bench-tool-agent", "use the echo tool then reply done", tools=[echo], llm=LLMGateway(provider=_DeterministicProvider(tool_reply)), runtime=runtime)
        start = time.time()
        tracemalloc.start()
        report = run_benchmark(agent.graph, cases)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        elapsed = time.time() - start
        latencies_ms = [r.latency_s * 1000 for r in report.results]
        rows.append(Row(
            topology="agent (tool-calling)", runtime=runtime, p50_ms=_percentile(latencies_ms, 0.5), p95_ms=_percentile(latencies_ms, 0.95),
            memory_kb=peak / 1024, tool_calls_per_s=N_CASES / elapsed if elapsed > 0 else 0.0,
            cost_usd=agent.config.budget.cost_usd, eval_score=report.pass_rate,
        ))
    return rows


def bench_supervisor() -> list[Row]:
    def supervisor_reply(messages, model):
        system = messages[0]["content"]
        if "ROUTE" in system:
            return "ROUTE billing"
        return f"handled: {messages[-1]['content']}"

    rows = []
    for runtime in ("native", "langgraph"):
        llm = LLMGateway(provider=_DeterministicProvider(supervisor_reply))
        billing = Agent("billing", "You are the billing agent.", llm=llm)
        cases = [BenchmarkCase(name=f"case-{i}", input=f"refund {i}", check=lambda r: r.startswith("handled:")) for i in range(N_CASES)]
        tracemalloc.start()
        workflow = Workflow.supervisor(prompt="route", agents={"billing": billing}, llm=llm, runtime=runtime)
        report = run_benchmark(workflow.graph, cases)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        latencies_ms = [r.latency_s * 1000 for r in report.results]
        rows.append(Row(
            topology="supervisor", runtime=runtime, p50_ms=_percentile(latencies_ms, 0.5), p95_ms=_percentile(latencies_ms, 0.95),
            memory_kb=peak / 1024, tool_calls_per_s=None, cost_usd=billing.config.budget.cost_usd, eval_score=report.pass_rate,
        ))
    return rows


def bench_swarm() -> list[Row]:
    def swarm_reply(messages, model):
        return "handled" if messages[-1]["role"] == "assistant" else "HANDOFF billing"

    rows = []
    for runtime in ("native", "langgraph"):
        llm = LLMGateway(provider=_DeterministicProvider(swarm_reply))
        triage = Agent("triage", "triage", llm=llm)
        billing = Agent("billing", "billing", llm=llm)
        cases = [BenchmarkCase(name=f"case-{i}", input=f"issue {i}", check=lambda r: r == "handled") for i in range(N_CASES)]
        tracemalloc.start()
        workflow = Workflow.swarm(agents={"triage": triage, "billing": billing}, entry="triage", runtime=runtime)
        report = run_benchmark(workflow.graph, cases)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        latencies_ms = [r.latency_s * 1000 for r in report.results]
        rows.append(Row(
            topology="swarm", runtime=runtime, p50_ms=_percentile(latencies_ms, 0.5), p95_ms=_percentile(latencies_ms, 0.95),
            memory_kb=peak / 1024, tool_calls_per_s=None, cost_usd=triage.config.budget.cost_usd, eval_score=report.pass_rate,
        ))
    return rows


def bench_blackboard() -> list[Row]:
    """Not run_benchmark-based like the other messages-shaped topologies:
    build_blackboard_graph's state has a third field ("round") that
    _CompiledWorkflow.run() seeds via extra_state={"round": 0} (see
    core/agent.py's Workflow.blackboard) — calling .graph.invoke() directly,
    as run_benchmark does, needs that seeded explicitly too."""
    rows = []
    for runtime in ("native", "langgraph"):
        researcher = Agent("researcher", "researcher", llm=LLMGateway(provider=_DeterministicProvider(lambda m, model: "POST fact: revenue grew 12%")))
        skeptic = Agent("skeptic", "skeptic", llm=LLMGateway(provider=_DeterministicProvider(lambda m, model: "POST contradiction: may be one-time")))
        bb = Blackboard()
        tracemalloc.start()
        workflow = Workflow.blackboard(agents={"researcher": researcher, "skeptic": skeptic}, blackboard=bb, rounds=2, runtime=runtime)
        latencies_ms = []
        for i in range(N_CASES):
            thread_id = f"bb-bench-{i}"
            start = time.time()
            workflow.graph.invoke(
                {"messages": [{"role": "user", "content": f"assess {i}"}], "thread_id": thread_id, "round": 0},
                {"configurable": {"thread_id": thread_id}},
            )
            latencies_ms.append((time.time() - start) * 1000)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        rows.append(Row(
            topology="blackboard", runtime=runtime, p50_ms=_percentile(latencies_ms, 0.5), p95_ms=_percentile(latencies_ms, 0.95),
            memory_kb=peak / 1024, tool_calls_per_s=None, cost_usd=researcher.config.budget.cost_usd, eval_score=1.0,
        ))
    return rows


def bench_debate() -> list[Row]:
    rows = []
    for runtime in ("native", "langgraph"):
        optimist = Agent("optimist", "bullish", llm=LLMGateway(provider=_DeterministicProvider(lambda m, model: "Buy — strong fundamentals.")))
        pessimist = Agent("pessimist", "bearish", llm=LLMGateway(provider=_DeterministicProvider(lambda m, model: "Sell — overvalued.")))
        judge = Agent("judge", "judge", llm=LLMGateway(provider=_DeterministicProvider(lambda m, model: "Verdict: Hold.")))
        cases = [BenchmarkCase(name=f"case-{i}", input=f"invest in {i}?", check=lambda r: r == "Verdict: Hold.") for i in range(N_CASES)]
        tracemalloc.start()
        workflow = Workflow.debate(debaters={"optimist": optimist, "pessimist": pessimist}, judge=judge, runtime=runtime)
        report = run_benchmark(workflow.graph, cases)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        latencies_ms = [r.latency_s * 1000 for r in report.results]
        rows.append(Row(
            topology="debate", runtime=runtime, p50_ms=_percentile(latencies_ms, 0.5), p95_ms=_percentile(latencies_ms, 0.95),
            memory_kb=peak / 1024, tool_calls_per_s=None, cost_usd=judge.config.budget.cost_usd, eval_score=report.pass_rate,
        ))
    return rows


def bench_fanout() -> list[Row]:
    rows = []
    for runtime in ("native", "langgraph"):
        provider = _DeterministicProvider(lambda m, model: f"processed: {m[-1]['content']}")
        worker = Agent("classifier", "classify", llm=LLMGateway(provider=provider))
        items = [f"item-{i}" for i in range(N_CASES)]
        tracemalloc.start()
        workflow = Workflow.fanout(agent=worker, runtime=runtime)
        latencies_ms = []
        outputs: list[str] = []
        for _ in range(5):  # 5 repeated fanout calls of N_CASES items each, for a stable percentile spread
            start = time.time()
            outputs = workflow.run(items, context=None)  # type: ignore[arg-type]
            latencies_ms.append((time.time() - start) * 1000)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        ok = set(outputs) == {f"processed: {item}" for item in items}
        rows.append(Row(
            topology="fanout", runtime=runtime, p50_ms=_percentile(latencies_ms, 0.5), p95_ms=_percentile(latencies_ms, 0.95),
            memory_kb=peak / 1024, tool_calls_per_s=None, cost_usd=worker.config.budget.cost_usd, eval_score=1.0 if ok else 0.0,
        ))
    return rows


def bench_dag() -> list[Row]:
    rows = []
    for runtime in ("native", "langgraph"):
        steps = [
            DAGStep(name="fetch", fn=lambda r: 10),
            DAGStep(name="double", fn=lambda r: r["fetch"] * 2, depends_on=("fetch",)),
            DAGStep(name="square", fn=lambda r: r["fetch"] ** 2, depends_on=("fetch",)),
            DAGStep(name="merge", fn=lambda r: r["double"] + r["square"], depends_on=("double", "square")),
        ]
        tracemalloc.start()
        workflow = Workflow.dag(steps=steps, runtime=runtime)
        latencies_ms = []
        result: dict = {}
        for _ in range(N_CASES):
            start = time.time()
            result = workflow.run()
            latencies_ms.append((time.time() - start) * 1000)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        ok = result.get("merge") == 120
        rows.append(Row(
            topology="dag", runtime=runtime, p50_ms=_percentile(latencies_ms, 0.5), p95_ms=_percentile(latencies_ms, 0.95),
            memory_kb=peak / 1024, tool_calls_per_s=None, cost_usd=0.0, eval_score=1.0 if ok else 0.0,
        ))
    return rows


def render_table(rows: list[Row]) -> str:
    header = "| Topology | Runtime | P50 (ms) | P95 (ms) | Memory (KB) | Tool throughput (calls/s) | Cost (USD) | Eval score |"
    sep = "|---|---|---|---|---|---|---|---|"
    lines = [header, sep]
    for row in rows:
        throughput = f"{row.tool_calls_per_s:.1f}" if row.tool_calls_per_s is not None else "n/a"
        lines.append(
            f"| {row.topology} | {row.runtime} | {row.p50_ms:.2f} | {row.p95_ms:.2f} | {row.memory_kb:.1f} | "
            f"{throughput} | {row.cost_usd:.4f} | {row.eval_score:.0%} |"
        )
    return "\n".join(lines)


def main() -> None:
    rows: list[Row] = []
    # Tracer.span() prints one JSON line per LLM call (observability.py) —
    # real, useful in production, just noise for this script's own table;
    # suppressed here, not disabled in Tracer itself.
    with contextlib.redirect_stdout(io.StringIO()):
        for bench in (bench_single_agent, bench_tool_throughput, bench_supervisor, bench_swarm, bench_blackboard, bench_debate, bench_fanout, bench_dag):
            rows.extend(bench())
    print(render_table(rows))


if __name__ == "__main__":
    main()

"""Benchmark harness — run a suite of cases against any compiled graph and get
a real pass/fail report with latency numbers. This is the mechanism a team
needs to validate against a published benchmark (GAIA, WebArena, SWE-bench) or
an internal regression suite; it does not ship a claimed score against any
specific published benchmark, since running one for real needs live API
access, the actual benchmark dataset, and runtime/cost this environment
doesn't have. What's here is proven against a real graph with real cases below.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class BenchmarkCase:
    name: str
    input: str
    check: Callable[[str], bool]  # given the agent's final reply, pass or fail
    thread_id: str | None = None


@dataclass
class CaseResult:
    name: str
    passed: bool
    reply: str
    latency_s: float
    error: str | None = None


@dataclass
class BenchmarkReport:
    results: list[CaseResult] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return sum(r.passed for r in self.results) / len(self.results) if self.results else 0.0

    @property
    def avg_latency_s(self) -> float:
        return sum(r.latency_s for r in self.results) / len(self.results) if self.results else 0.0

    def summary(self) -> str:
        lines = [
            f"{r.name}: {'PASS' if r.passed else 'FAIL'} ({r.latency_s:.2f}s)" + (f" — {r.error}" if r.error else "")
            for r in self.results
        ]
        passed = sum(1 for r in self.results if r.passed)
        lines.append(f"\n{passed}/{len(self.results)} passed ({self.pass_rate:.0%}), avg latency {self.avg_latency_s:.2f}s")
        return "\n".join(lines)


def run_benchmark(graph: Any, cases: list[BenchmarkCase]) -> BenchmarkReport:
    report = BenchmarkReport()
    for i, case in enumerate(cases):
        thread_id = case.thread_id or f"bench-{i}-{case.name}"
        config = {"configurable": {"thread_id": thread_id}}
        start = time.time()
        try:
            result = graph.invoke(
                {"messages": [{"role": "user", "content": case.input}], "thread_id": thread_id}, config
            )
            reply = result["messages"][-1]["content"]
            report.results.append(CaseResult(name=case.name, passed=case.check(reply), reply=reply, latency_s=time.time() - start))
        except Exception as e:
            report.results.append(CaseResult(name=case.name, passed=False, reply="", latency_s=time.time() - start, error=str(e)))
    return report

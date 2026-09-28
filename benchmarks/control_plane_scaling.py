"""Concurrency scaling of the control plane (single process, no model).

For T in {1,2,4,8,16,32} threads, P principals across 4 tenants issue calls
through ControlPlane -> GovernedToolGateway. Reports throughput, per-call
p50/p95/p99 latency, and two safety invariants under contention:
  limit_exact - every principal is admitted exactly min(L, attempts) calls
                (never more: safety; never fewer: liveness)
  audit_exact - one decision record per attempted call
CPython's GIL bounds parallel speedup; this measures correctness and
contention cost, not multi-core scaling.
"""
from __future__ import annotations

import contextlib
import io
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_foundry import Agent  # noqa: E402
from agent_foundry.contracts import AutonomyLevel, Policy, ToolSpec  # noqa: E402
from agent_foundry.control_plane import (ControlPlane, InMemoryPolicyStore, Principal, Tenant,  # noqa: E402
                                         TierRule, WorkspacePolicy)
from agent_foundry.governed_tools import GovernedToolGateway  # noqa: E402
from agent_foundry.llm_gateway import LLMGateway  # noqa: E402

TENANTS, USERS_PER_TENANT, CALLS_PER_USER, LIMIT = 4, 8, 150, 100


class _NoModel:
    def complete(self, *a, **k):
        raise RuntimeError


def build():
    spec = ToolSpec("read", "read", {"type": "object", "additionalProperties": True}, lambda **k: {"ok": True},
                    scopes=frozenset({"r"}), cacheable=False)
    agent = Agent("scale", "x", tools=[spec], llm=LLMGateway(_NoModel()),
                  policy=Policy(allowed_tools=frozenset({"read"}), autonomy=AutonomyLevel.L4_POLICY_BOUND,
                                max_steps_per_thread=10**9, max_cost_usd_per_thread=1e12))
    store = InMemoryPolicyStore()
    for t in range(TENANTS):
        store.put(Tenant(f"t{t}", {f"u{u}": Principal(f"u{u}", None, frozenset({"r"})) for u in range(USERS_PER_TENANT)},
                         WorkspacePolicy(defaults={"subagent": TierRule("allow", LIMIT)})))
    return ControlPlane(GovernedToolGateway(agent.config), store, required_scopes={"read": frozenset({"r"})}), agent


def run(threads: int) -> dict:
    plane, agent = build()
    jobs = [(f"t{t}", f"u{u}") for t in range(TENANTS) for u in range(USERS_PER_TENANT) for _ in range(CALLS_PER_USER)]
    lat: list[float] = []

    def call(job):
        t0 = time.perf_counter()
        d = plane.invoke("read", {}, tenant_id=job[0], user_id=job[1], tier="subagent")
        lat.append((time.perf_counter() - t0) * 1e3)
        return job, d.allowed

    t0 = time.perf_counter()
    with ThreadPoolExecutor(threads) as pool:
        results = list(pool.map(call, jobs))
    wall = time.perf_counter() - t0
    admitted: dict = {}
    for job, ok in results:
        admitted[job] = admitted.get(job, 0) + ok
    lat.sort()
    decisions = sum(1 for e in agent.config.audit.entries if e["action"] == "governance_decision")
    return dict(threads=threads, calls=len(jobs), throughput_per_s=len(jobs) / wall,
                p50_ms=lat[len(lat) // 2], p95_ms=lat[int(0.95 * len(lat)) - 1], p99_ms=lat[int(0.99 * len(lat)) - 1],
                limit_exact=all(v == min(LIMIT, CALLS_PER_USER) for v in admitted.values()),
                admitted_per_principal=sorted(set(admitted.values())), audit_exact=decisions == len(jobs))


def main():
    reps = 3
    out = []
    with contextlib.redirect_stdout(io.StringIO()):
        for threads in (1, 2, 4, 8, 16, 32):
            runs = [run(threads) for _ in range(reps)]
            out.append(dict(threads=threads, reps=reps, calls=runs[0]["calls"],
                            throughput_per_s=statistics.median(r["throughput_per_s"] for r in runs),
                            p50_ms=statistics.median(r["p50_ms"] for r in runs),
                            p95_ms=statistics.median(r["p95_ms"] for r in runs),
                            p99_ms=statistics.median(r["p99_ms"] for r in runs),
                            limit_exact_all_reps=all(r["limit_exact"] for r in runs),
                            audit_exact_all_reps=all(r["audit_exact"] for r in runs)))
    print(json.dumps({"tenants": TENANTS, "principals": TENANTS * USERS_PER_TENANT, "calls_per_principal": CALLS_PER_USER,
                      "limit_per_minute": LIMIT, "python": sys.version.split()[0], "results": out}, indent=1))


if __name__ == "__main__":
    main()

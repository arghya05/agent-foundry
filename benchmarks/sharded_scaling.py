"""Horizontal scaling of admission with principal-affinity sharding.

Rate-limit and policy state are keyed by (tenant, principal, tier), so
routing each principal to one shard needs no cross-shard coordination.
We run K worker processes (K = 1, 2, 4, 8), each owning a ControlPlane for
its hash partition of 256 principals, and measure aggregate governed calls
per second plus the exact-limit invariant per principal. One machine; this
measures the partitioned design, not a network deployment.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import time
import zlib
from multiprocessing import get_context
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PRINCIPALS, CALLS, LIMIT = 256, 60, 40


def shard_of(uid: str, k: int) -> int:
    return zlib.crc32(uid.encode()) % k


def worker(args):
    shard, k = args
    with contextlib.redirect_stdout(io.StringIO()):
        from agent_foundry import Agent
        from agent_foundry.contracts import AutonomyLevel, Policy, ToolSpec
        from agent_foundry.control_plane import (ControlPlane, InMemoryPolicyStore, Principal, Tenant, TierRule,
                                                 WorkspacePolicy)
        from agent_foundry.governed_tools import GovernedToolGateway
        from agent_foundry.llm_gateway import LLMGateway

        class NoModel:
            def complete(self, *a, **kw):
                raise RuntimeError

        mine = [f"u{i}" for i in range(PRINCIPALS) if shard_of(f"u{i}", k) == shard]
        spec = ToolSpec("read", "read", {"type": "object", "additionalProperties": True}, lambda **kw: {"ok": True},
                        scopes=frozenset({"r"}), cacheable=False)
        agent = Agent("s", "x", tools=[spec], llm=LLMGateway(NoModel()),
                      policy=Policy(allowed_tools=frozenset({"read"}), autonomy=AutonomyLevel.L4_POLICY_BOUND,
                                    max_steps_per_thread=10**9, max_cost_usd_per_thread=1e12))
        store = InMemoryPolicyStore()
        store.put(Tenant("t", {u: Principal(u, None, frozenset({"r"})) for u in mine},
                         WorkspacePolicy(defaults={"subagent": TierRule("allow", LIMIT)})))
        plane = ControlPlane(GovernedToolGateway(agent.config), store, required_scopes={"read": frozenset({"r"})})
        admitted = {u: 0 for u in mine}
        t0 = time.perf_counter()
        for _ in range(CALLS):
            for u in mine:
                admitted[u] += plane.invoke("read", {}, tenant_id="t", user_id=u, tier="subagent").allowed
        wall = time.perf_counter() - t0
    return len(mine) * CALLS, wall, all(v == LIMIT for v in admitted.values())


def main():
    ctx = get_context("spawn")
    out = []
    for k in (1, 2, 4, 8):
        with ctx.Pool(k) as pool:
            t0 = time.perf_counter()
            parts = pool.map(worker, [(s, k) for s in range(k)])
            wall = time.perf_counter() - t0
        calls = sum(p[0] for p in parts)
        busiest = max(p[1] for p in parts)
        out.append(dict(processes=k, calls=calls, aggregate_throughput_per_s=calls / busiest,
                        wall_incl_startup_s=wall, limit_exact=all(p[2] for p in parts)))
    print(json.dumps({"principals": PRINCIPALS, "calls_per_principal": CALLS, "limit": LIMIT,
                      "cpu_count": os.cpu_count(), "results": out}, indent=1))


if __name__ == "__main__":
    main()

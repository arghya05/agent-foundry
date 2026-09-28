"""Per-call cost of each governance layer, with no model and a no-op tool.

Layers: bare function; ToolRegistry.invoke (allowlist + validation);
GovernedToolGateway.invoke (+ identity, PDP, budget, audit, scope);
ControlPlane.invoke (+ tenant policy, tier rule, rate limiter, decision audit);
ControlPlane.invoke inside a three-hop delegation. Order is randomized per
repetition; results are per-call microseconds (median and p95 over calls)
plus the median across repetitions. Output: JSON on stdout.
"""
from __future__ import annotations

import contextlib
import io
import json
import platform
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_foundry import Agent  # noqa: E402
from agent_foundry.contracts import AutonomyLevel, Identity, Policy, ToolSpec
from agent_foundry.control_plane import (ControlPlane, InMemoryPolicyStore, Principal, Tenant, TierRule,
                                         WorkspacePolicy, delegation)
from agent_foundry.core.execution_context import ExecutionContext
from agent_foundry.governed_tools import GovernedToolGateway
from agent_foundry.llm_gateway import LLMGateway

CALLS, REPS = 2000, 7


class _NoModel:
    def complete(self, *a, **k):
        raise RuntimeError


def noop(**kwargs):
    return {"ok": True}


def setup():
    spec = ToolSpec("read", "read", {"type": "object", "additionalProperties": True}, noop,
                    scopes=frozenset({"r"}), cacheable=False)
    policy = Policy(allowed_tools=frozenset({"read"}), autonomy=AutonomyLevel.L4_POLICY_BOUND,
                    max_steps_per_thread=10**9, max_cost_usd_per_thread=1e12)
    agent = Agent("bench", "x", tools=[spec], llm=LLMGateway(_NoModel()), policy=policy)
    gateway = GovernedToolGateway(agent.config)
    store = InMemoryPolicyStore()
    store.put(Tenant("t", {"u": Principal("u", "u@x", frozenset({"r"}))},
                     WorkspacePolicy(defaults={"interactive": TierRule("allow"), "subagent": TierRule("allow")})))
    plane = ControlPlane(gateway, store, required_scopes={"read": frozenset({"r"})})
    ident = Identity("u", "t", ("r",))
    ctx = ExecutionContext(user_id="u", tenant_id="t", thread_id="bench", permissions=frozenset({"r"}))

    def nested():
        with delegation("orchestrator"), delegation("specialist", ["r"]), delegation("worker", ["r"]):
            return plane.invoke("read", {}, tenant_id="t", user_id="u", tier="subagent")

    return {
        "bare_function": lambda: noop(),
        "tool_registry": lambda: agent.config.tools.invoke("read", {}, identity=ident, policy=policy),
        "governed_gateway": lambda: gateway.invoke("read", {}, context=ctx),
        "control_plane": lambda: plane.invoke("read", {}, tenant_id="t", user_id="u"),
        "control_plane_3hop_delegation": nested,
    }, agent


def measure(fn):
    samples = []
    for _ in range(CALLS):
        t0 = time.perf_counter_ns()
        fn()
        samples.append((time.perf_counter_ns() - t0) / 1000)
    samples.sort()
    return statistics.median(samples), samples[int(0.95 * len(samples)) - 1]


def main():
    random.seed(20260928)
    with contextlib.redirect_stdout(io.StringIO()):
        layers, agent = setup()
        for fn in layers.values():  # warm-up
            for _ in range(200):
                fn()
        runs = {k: [] for k in layers}
        for _ in range(REPS):
            order = list(layers)
            random.shuffle(order)
            for name in order:
                agent.config.audit.entries.clear()
                runs[name].append(measure(layers[name]))
    out = {"calls_per_rep": CALLS, "reps": REPS, "python": sys.version.split()[0], "platform": platform.platform(),
           "layers": {k: {"median_us": statistics.median(m for m, _ in v), "p95_us": statistics.median(p for _, p in v),
                          "median_us_per_rep": [round(m, 3) for m, _ in v]} for k, v in runs.items()}}
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()

"""Validate the independent scenarios with the UNMODIFIED upstream loader/scorer.

For every scenario, four synthetic RunOutcomes are scored:
  empty      - no tool outcomes, no audit entries
  all_allow  - every attempted call allowed, one plausible AuditEntry each
  all_deny   - every attempted call denied, one plausible AuditEntry each
  oracle     - decisions derived from the scenario's own assertions (sanity:
               proves the assertion set is satisfiable / not self-contradictory)
The three trivial outcomes must FAIL every scenario; the oracle must PASS.

Usage:  <python> validate_trivial.py [/path/to/upstream/agentgovbench]
"""
from __future__ import annotations

import sys
from pathlib import Path

UPSTREAM = sys.argv[1] if len(sys.argv) > 1 else "/private/tmp/agent-foundry-comparisons/agentgovbench"
sys.path.insert(0, UPSTREAM)

from benchmark.loader import load_all  # noqa: E402
from benchmark.scorer import score_scenario, _filter_tool_outcomes  # noqa: E402
from benchmark.types import (  # noqa: E402
    AuditEntry, Delegation, DirectToolCall, ParallelFanOut, RunOutcome, ToolOutcome,
)

HERE = Path(__file__).resolve().parent


def _calls(s):
    """Expand scenario actions into (ToolOutcome-template, chain) pairs."""
    default_tenant = s.setup.tenants[0].id if s.setup.tenants else None
    chains: dict[tuple[str, str], list[str]] = {}
    out = []
    for a in s.actions:
        if isinstance(a, Delegation):
            parent = chains.get((a.as_user, a.from_agent), [a.from_agent])
            chains[(a.as_user, a.to_agent)] = parent + [a.to_agent]
        elif isinstance(a, DirectToolCall):
            t = ToolOutcome(tool=a.tool, input=a.input, as_user=a.as_user,
                            as_tenant=a.as_tenant or default_tenant, allowed=True,
                            agent_tier=a.agent_tier, agent_name=a.agent_name)
            out.append((t, list(chains.get((a.as_user, a.agent_name or ""), []))))
        elif isinstance(a, ParallelFanOut):
            for w in range(a.worker_count):
                for _ in range(a.calls_per_worker):
                    t = ToolOutcome(tool=a.tool, input=a.input, as_user=a.as_user,
                                    as_tenant=a.as_tenant or default_tenant, allowed=True,
                                    agent_tier="subagent", agent_name=f"fanout-worker-{w}")
                    out.append((t, []))
    return out


def _email(s, tenant, uid):
    for t in s.setup.tenants:
        if t.id == tenant:
            for u in t.users:
                if u.uid == uid:
                    return u.email
    return None


def _outcome(s, decide):
    calls = _calls(s)
    tos, aes = [], []
    for i, (t, chain) in enumerate(calls):
        t.allowed = decide(i, t, calls)
        t.reason = None if t.allowed else "policy_denied"
        tos.append(t)
        aes.append(AuditEntry(
            timestamp=f"2026-09-28T00:00:{i:02d}Z", tenant=t.as_tenant,
            actor_uid=t.as_user, actor_email=_email(s, t.as_tenant, t.as_user),
            tool=t.tool, decision="allow" if t.allowed else "deny",
            reason="allowed" if t.allowed else "policy_denied",
            trace_id=f"trace-{i}", delegation_chain=chain,
            extra={"agent_tier": t.agent_tier, "agent_name": t.agent_name}))
    return RunOutcome(tool_outcomes=tos, audit_entries=aes)


def _oracle(s):
    calls = [t for t, _ in _calls(s)]
    deny_f = [a.params for a in s.expected if a.kind == "tool_denied"]
    allow_f = [a.params for a in s.expected if a.kind == "tool_allowed"]
    rate_f = [(a.params["filter"], a.params["max_allowed"]) for a in s.expected
              if a.kind == "rate_limited_count"]
    decisions = []
    for t in calls:
        if any(_filter_tool_outcomes([t], f) for f in deny_f):
            decisions.append(False)
        elif any(_filter_tool_outcomes([t], f) for f in allow_f):
            decisions.append(True)
        else:  # unconstrained: allow unless a rate ceiling is already reached
            ok = True
            for f, mx in rate_f:
                if _filter_tool_outcomes([t], f):
                    used = sum(1 for c, d in zip(calls, decisions)
                               if d and _filter_tool_outcomes([c], f))
                    ok = ok and used < mx
            decisions.append(ok)
    return lambda i, t, c: decisions[i]


def main():
    scenarios = load_all(HERE / "scenarios")
    rows, bad = [], []
    for s in scenarios:
        res = {
            "empty": score_scenario(s, RunOutcome(), "empty", 0),
            "all_allow": score_scenario(s, _outcome(s, lambda *_: True), "all_allow", 0),
            "all_deny": score_scenario(s, _outcome(s, lambda *_: False), "all_deny", 0),
            "oracle": score_scenario(s, _outcome(s, _oracle(s)), "oracle", 0),
        }
        cells = {}
        for k, r in res.items():
            failing = [a.assertion.kind for a in r.assertion_results if not a.passed]
            cells[k] = ("PASS" if r.passed else "FAIL") + (f" ({len(failing)} failed)" if failing else "")
            if (k == "oracle") != r.passed:
                bad.append((s.id, k, failing))
        rows.append((s.category, s.id, cells))
    print(f"loaded {len(scenarios)} scenarios")
    print("| scenario | empty | all-allowed | all-denied | oracle |")
    print("|---|---|---|---|---|")
    for cat, sid, c in rows:
        print(f"| `{sid.split('.', 1)[1]}` | {c['empty']} | {c['all_allow']} | {c['all_deny']} | {c['oracle']} |")
    if bad:
        print("\nUNEXPECTED:", bad)
        sys.exit(1)
    print("\nOK: every trivial outcome fails every scenario; oracle passes every scenario.")


if __name__ == "__main__":
    main()

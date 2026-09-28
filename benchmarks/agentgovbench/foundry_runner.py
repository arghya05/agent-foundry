"""AgentGovBench runner for Agent Foundry's control plane.

The runner translates each scenario into Foundry objects and replays its
actions. It makes no governance decision itself: every allow/deny, reason,
rate-limit count, fail-mode choice and audit record comes from
`agent_foundry.control_plane.ControlPlane`, which executes admitted calls
through `GovernedToolGateway`. The reported audit log is read back from the
agent's `AuditLog`.

Deviations from the upstream harness, all disclosed in results metadata:
  * A virtual clock replaces sleeping. A gateway failure of <=10 s advances
    the clock past the outage, matching the upstream ACP runner's
    "sleep the outage, then continue" treatment; longer failures stay active.
  * ParallelFanOut runs its workers on real threads (the upstream reference
    runners loop sequentially).
Delegations are replayed as nested `control_plane.delegation` scopes along
the declared chain whenever a later call names a delegated agent.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional

from benchmark.runner import RunnerMetadata, StatefulRunner
from benchmark.types import (Action, AuditEntry, Delegation, DirectToolCall, GatewayFailure,
                             ParallelFanOut, PolicyChange, Scenario, ToolOutcome)

from agent_foundry import Agent
from agent_foundry.contracts import AutonomyLevel, Policy, ToolSpec
from agent_foundry.control_plane import (ControlPlane, InMemoryPolicyStore, Principal, Tenant, TierRule,
                                         WorkspacePolicy, delegation)
from agent_foundry.governed_tools import GovernedToolGateway
from agent_foundry.llm_gateway import LLMGateway


class VirtualClock:
    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.now = start
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self.now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.now += seconds


class _NoModel:
    """The benchmark replays tool calls; any model call is a harness bug."""

    def complete(self, *args: Any, **kwargs: Any):
        raise RuntimeError("AgentGovBench scenarios do not call a model")


def _rule(tp: Any) -> TierRule:
    return TierRule(permission=tp.permission, rate_limit_per_minute=tp.rate_limit_per_minute)


def _policy(p: Any) -> WorkspacePolicy:
    return WorkspacePolicy(
        defaults={tier: _rule(r) for tier, r in p.defaults.items()},
        tools={tool: {tier: _rule(r) for tier, r in tiers.items()} for tool, tiers in p.tools.items()},
        users={uid: {tier: _rule(r) for tier, r in tiers.items()} for uid, tiers in p.users.items()},
        user_tools={uid: {tool: {tier: _rule(r) for tier, r in tiers.items()} for tool, tiers in tools.items()}
                    for uid, tools in p.user_tools.items()},
        fail_mode=p.fail_mode,
    )


class Runner(StatefulRunner):
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()

    @property
    def metadata(self) -> RunnerMetadata:
        return RunnerMetadata(
            name="agent_foundry",
            version="0.2.0",
            product="agent-foundry control_plane + GovernedToolGateway (in-process)",
            vendor=None,
            notes=("Decisions and audit come from agent_foundry.control_plane; admitted calls execute "
                   "through GovernedToolGateway. Virtual clock: outages <=10s elapse before the next "
                   "action (as in the upstream ACP runner). Fan-out workers run concurrently."),
        )

    # -- lifecycle -------------------------------------------------------
    def setup(self, scenario: Scenario) -> None:
        super().setup(scenario)
        self.clock = VirtualClock()
        self.store = InMemoryPolicyStore(clock=self.clock)
        self.effects: list[tuple[str, dict]] = []
        specs = []
        for tool in scenario.setup.tools:
            specs.append(ToolSpec(
                tool.name, tool.description or tool.name,
                {"type": "object", "additionalProperties": True},
                self._effect(tool.name), scopes=frozenset(tool.required_scopes), cacheable=False))
        agent = Agent("agentgovbench", "Replay governed tool calls.", runtime="native", tools=specs,
                      llm=LLMGateway(_NoModel()),
                      policy=Policy(allowed_tools=frozenset(t.name for t in scenario.setup.tools),
                                    autonomy=AutonomyLevel.L4_POLICY_BOUND, max_steps_per_thread=1_000_000,
                                    max_cost_usd_per_thread=1e9))
        self.plane = ControlPlane(GovernedToolGateway(agent.config), self.store,
                                  required_scopes={t.name: frozenset(t.required_scopes) for t in scenario.setup.tools},
                                  fail_modes={t.id: t.policy.fail_mode for t in scenario.setup.tenants},
                                  clock=self.clock)
        for t in scenario.setup.tenants:
            self.store.put(Tenant(t.id, {u.uid: Principal(u.uid, u.email, frozenset(u.scopes)) for u in t.users},
                                  _policy(t.policy)))
        self._default_tenant = scenario.setup.tenants[0].id if scenario.setup.tenants else "tenant-a"
        self._parent: dict[str, str] = {}
        self._delegated: dict[str, list[str]] = {}

    def _effect(self, name: str):
        def fn(**kwargs: Any) -> dict:
            with self._lock:
                self.effects.append((name, kwargs))
            return {"ok": True}
        return fn

    # -- actions ---------------------------------------------------------
    def execute_action(self, action: Action) -> Optional[ToolOutcome]:
        if isinstance(action, Delegation):
            self._parent[action.to_agent] = action.from_agent
            self._delegated[action.to_agent] = list(action.delegated_scopes)
            return None
        if isinstance(action, GatewayFailure):
            self.store.outage(action.duration_seconds)
            if action.duration_seconds <= 10:
                self.clock.advance(action.duration_seconds + 0.2)
            return None
        if isinstance(action, PolicyChange):
            self._apply_policy_change(action)
            return None
        if isinstance(action, DirectToolCall):
            return self._call(action.tool, action.input, action.as_user, action.as_tenant,
                              action.agent_tier, action.agent_name)
        if isinstance(action, ParallelFanOut):
            def worker(i: int) -> Optional[ToolOutcome]:
                last = None
                for _ in range(action.calls_per_worker):
                    last = self._call(action.tool, action.input, action.as_user, action.as_tenant,
                                      "subagent", f"worker-{i}")
                return last
            with ThreadPoolExecutor(max_workers=max(1, action.worker_count)) as pool:
                outcomes = list(pool.map(worker, range(action.worker_count)))
            return outcomes[-1] if outcomes else None
        return None

    def _lineage(self, agent: Optional[str]) -> list[str]:
        if not agent or agent not in self._parent:
            return []
        chain = [agent]
        while chain[-1] in self._parent and len(chain) < 64:
            chain.append(self._parent[chain[-1]])
        return list(reversed(chain))

    def _call(self, tool: str, tool_input: dict, uid: str, tenant: Optional[str],
              tier: Optional[str], agent_name: Optional[str]) -> ToolOutcome:
        tenant_id = tenant or self._default_tenant
        lineage = self._lineage(agent_name)

        def invoke(depth: int = 0):
            if depth == len(lineage):
                return self.plane.invoke(tool, tool_input, tenant_id=tenant_id, user_id=uid,
                                         tier=tier or "interactive", agent_name=agent_name)
            hop = lineage[depth]
            with delegation(hop, self._delegated.get(hop) if depth else None):
                return invoke(depth + 1)

        decision = invoke()
        outcome = ToolOutcome(tool=tool, input=tool_input, as_user=uid, as_tenant=tenant_id,
                              allowed=decision.allowed, reason=decision.reason,
                              agent_tier=tier, agent_name=agent_name)
        with self._lock:
            self._tool_outcomes.append(outcome)
            self._gateway_reachable = self.plane.policy_source_reachable
        return outcome

    def _apply_policy_change(self, pc: PolicyChange) -> None:
        tier = pc.tier or "interactive"

        def change(policy: WorkspacePolicy) -> None:
            if pc.user and pc.tool:
                table = policy.user_tools.setdefault(pc.user, {}).setdefault(pc.tool, {})
            elif pc.user:
                table = policy.users.setdefault(pc.user, {})
            elif pc.tool:
                table = policy.tools.setdefault(pc.tool, {})
            else:
                table = policy.defaults
            old = table.get(tier, TierRule())
            table[tier] = TierRule(permission=pc.set_permission or old.permission,
                                   rate_limit_per_minute=pc.set_rate_limit if pc.set_rate_limit is not None
                                   else old.rate_limit_per_minute)
        self.store.update(pc.tenant or self._default_tenant, change)

    # -- observations ----------------------------------------------------
    def audit_log(self) -> list[AuditEntry]:
        entries = []
        for e in self.plane.audit.entries:
            if e.get("action") != "governance_decision":
                continue
            entries.append(AuditEntry(
                timestamp=datetime.fromtimestamp(e["ts"], tz=timezone.utc).isoformat(),
                tenant=e["tenant"] or None, actor_uid=e["identity"], actor_email=e.get("actor_email"),
                tool=e["tool"], decision=e["decision"], reason=e.get("reason"), trace_id=e.get("trace_id"),
                delegation_chain=list(e.get("delegation_chain", [])),
                extra={"agent_tier": e.get("agent_tier"), "agent_name": e.get("agent_name"),
                       "policy_source_reachable": e.get("policy_source_reachable"),
                       "source": "agent_foundry.control_plane"},
            ))
        return entries

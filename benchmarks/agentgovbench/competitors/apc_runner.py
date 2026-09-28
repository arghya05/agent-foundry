"""AgentGovBench runner for Bounded Agents / Agentic Principal Chain (APC).

Reference implementation: github.com/xmuruaga/bounded-agents @ d31a1ea
(arXiv 2608.15888), package ``apc`` (zero dependencies). Put the checkout on
sys.path via APC_PATH (default /private/tmp/agent-foundry-comparisons/bounded-agents).

The runner is a host. Every allow/deny comes from apc.pdp.PolicyDecisionPoint.
evaluate(); every audit record is an evidence package read back from
apc.pdp.EvidenceSink.packages.

Mapping (file:line refs in the APC checkout):
  * Tenant -> one APC deployment: its own signing key, EvidenceSink,
    RevocationRegistry and PDP(s). Resources are "<tenant>:<tool>"; a tenant's
    envelopes carry resource pattern "<tenant>:*" (core.py resource_matches).
  * User -> originating Principal (ExecutionRole.AS_USER) whose role scope's
    action set is the concrete tools the user's scopes cover. APC scopes are
    finite sets of concrete identifiers expanded "at session initialization
    before the scope is sealed" (core.py Scope docstring); that expansion is
    the only computation the runner does. The root AuthorizationEnvelope is
    signed with the tenant key.
  * Delegation -> AuthorizationEnvelope.narrow(delegate): effective scope =
    parent scope MEET delegate role scope (intersection; core.py narrow). The
    delegate's role scope is the tools covered by delegated_scopes. Fan-out
    workers are narrowed envelopes with the user's scope.
  * Rate limit -> apc.pdp.RateLimiter (per actor principal, sliding window).
    APC's PDP takes one limiter, so the runner builds one PDP per (tenant,
    tier) configured with that tier's default limit; all share the tenant's
    sink/registry.
  * Gateway failure -> EvidenceSink.set_available(False) (Rule 7 fail closed);
    outages <= 10 s are slept through, as the upstream ACP runner does.
  * Per-user revoke (PolicyChange user + deny) -> RevocationRegistry.revoke for
    that user's envelopes (closest feature; not tier-specific).
Not mappable (APC has no such concept; left unenforced, scored as run): tier
permissions, workspace/user/tool allow-deny-flag policy layers, tool-level
PolicyChange, fail-open, e-mail in evidence, per-call trace id.
"""
from __future__ import annotations

import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from benchmark.runner import RunnerMetadata, StatefulRunner
from benchmark.types import (Action, AuditEntry, Delegation, DirectToolCall, GatewayFailure,
                             ParallelFanOut, PolicyChange, Scenario, ToolOutcome)

sys.path.insert(0, os.environ.get("APC_PATH", "/private/tmp/agent-foundry-comparisons/bounded-agents"))
from apc.approval import ApprovalStore  # noqa: E402
from apc.budget import BudgetState  # noqa: E402
from apc.calibrate import ImpactWeights  # noqa: E402
from apc.compose import CompositionChecker  # noqa: E402
from apc.core import AuthorizationEnvelope, DelegationBudgetSpec, ExecutionRole, Principal, Scope  # noqa: E402
from apc.pdp import EvidenceSink, PolicyDecisionPoint, ProposedAction, RateLimiter, RevocationRegistry  # noqa: E402

import logging  # noqa: E402
logging.getLogger("apc").setLevel(logging.CRITICAL)

APC_COMMIT = "d31a1ea"
TIERS = ("interactive", "subagent", "api", "background")
POLICY_VERSION = "1.0"
# Generous budget: the benchmark has no cost/blast semantics.
BUDGET = DelegationBudgetSpec(max_delegation_depth=10, max_blast_radius=1.0, max_irreversible_effects=10**9,
                              max_sensitivity_class="regulated", cross_domain_composition=True, max_cost=1e12)


class _Tenant:
    def __init__(self, tenant: Any, tools: list[Any]) -> None:
        self.id = tenant.id
        self.key = os.urandom(32)
        self.sink = EvidenceSink()
        self.revocations = RevocationRegistry()
        self.session = f"agb-{tenant.id}-{uuid.uuid4().hex[:8]}"
        self.budget = BudgetState(spec=BUDGET)
        self.composition = CompositionChecker(restrictions=frozenset())
        self.pdp: dict[str, PolicyDecisionPoint] = {}
        for tier in TIERS:
            tp = tenant.policy.defaults.get(tier)
            limit = tp.rate_limit_per_minute if tp else None
            self.pdp[tier] = PolicyDecisionPoint(
                signing_key=self.key, impact_weights=ImpactWeights(0.4, 0.3, 0.3), approval_threshold=0.5,
                approval_store=ApprovalStore(), evidence_sink=self.sink, revocation_registry=self.revocations,
                rate_limiter=RateLimiter(max_evaluations=limit, window_seconds=60.0) if limit else None)
        self.tools = tools
        self.envelopes: dict[tuple[str, Optional[str]], AuthorizationEnvelope] = {}  # (uid, agent) -> env
        for u in tenant.users:
            p = Principal(u.uid, ExecutionRole.AS_USER, self.scope_for(u.scopes))
            env = AuthorizationEnvelope(f"env-{tenant.id}-{u.uid}", self.session, p, p.role_scope, BUDGET,
                                        expires_at=time.time() + 3600, policy_version=POLICY_VERSION)
            env.sign(self.key)
            self.envelopes[(u.uid, None)] = env

    def scope_for(self, scopes: list[str]) -> Scope:
        have = set(scopes)
        actions = frozenset(t.name for t in self.tools if set(t.required_scopes) <= have)
        return Scope(resources=frozenset({f"{self.id}:*"}), actions=actions, data_classifications=frozenset({"public"}))

    def narrow(self, uid: str, parent_agent: Optional[str], name: str, role: ExecutionRole,
               scope: Scope) -> Optional[AuthorizationEnvelope]:
        parent = self.envelopes.get((uid, parent_agent))
        if parent is None:
            return None
        env = parent.narrow(Principal(name, role, scope), self.key)
        self.envelopes[(uid, name)] = env
        return env


class Runner(StatefulRunner):
    @property
    def metadata(self) -> RunnerMetadata:
        return RunnerMetadata(
            name="apc", version=f"bounded-agents apc 1.0.0 @ {APC_COMMIT}",
            product="Bounded Agents / Agentic Principal Chain reference implementation (arXiv 2608.15888)",
            vendor="Xabier Muruaga (research artifact)",
            notes=("Decisions from apc.pdp.PolicyDecisionPoint.evaluate; audit = EvidenceSink packages. One APC "
                   "deployment (key, sink, revocation registry) per tenant; one PDP per tier to carry that tier's "
                   "default rate limit. Delegation = AuthorizationEnvelope.narrow (scope meet)."),
            declined_categories={
                "per_user_policy_enforcement": ("APC has no workspace/user/tool allow-deny policy layers or tiers; "
                                                "only scope, revocation and rate limit. Overrides are not enforced."),
                "tier_policies": "APC has no agent-tier concept; tier permissions (e.g. background deny) are not enforced.",
                "fail_mode_discipline.02_fail_open_honored": "APC fails closed only (Rule 7, evidence sink unavailable).",
                "audit fields": ("Evidence packages carry actor and chain but no e-mail or per-call trace id; "
                                 "rate-limit, revocation, missing-envelope and sink-outage denials return before "
                                 "evidence commit (pdp.py evaluate early returns) and are therefore unaudited."),
                "flag": "APC has no allow-and-mark decision.",
            },
        )

    def setup(self, scenario: Scenario) -> None:
        super().setup(scenario)
        self.effects: list[tuple[str, dict]] = []
        self.tenants = {t.id: _Tenant(t, scenario.setup.tools) for t in scenario.setup.tenants}
        self.default_tenant = scenario.setup.tenants[0].id if scenario.setup.tenants else "tenant-a"
        self.outage_until = 0.0

    def execute_action(self, action: Action) -> Optional[ToolOutcome]:
        if isinstance(action, Delegation):
            t = self.tenants.get(action.as_tenant or self.default_tenant)
            if t is None:
                return None
            uid = action.as_user
            if (uid, action.from_agent) not in t.envelopes:
                root = t.envelopes.get((uid, None))
                if root is not None:  # top-level orchestrator acting on the user's behalf
                    t.narrow(uid, None, action.from_agent, ExecutionRole.ON_BEHALF_OF, root.effective_scope)
            t.narrow(uid, action.from_agent, action.to_agent, ExecutionRole.AS_AGENT,
                     t.scope_for(action.delegated_scopes))
            return None
        if isinstance(action, GatewayFailure):
            self.outage_until = time.time() + action.duration_seconds
            for t in self.tenants.values():
                t.sink.set_available(False)
            if action.duration_seconds <= 10:
                time.sleep(action.duration_seconds + 0.2)
            return None
        if isinstance(action, PolicyChange):
            t = self.tenants.get(action.tenant or self.default_tenant)
            if t is not None and action.user and not action.tool and action.set_permission == "deny":
                for (uid, _agent), env in t.envelopes.items():
                    if uid == action.user:
                        t.revocations.revoke(env.envelope_id, "policy change")
            return None
        if isinstance(action, DirectToolCall):
            return self._call(action.tool, action.input, action.as_user, action.as_tenant,
                              action.agent_tier, action.agent_name)
        if isinstance(action, ParallelFanOut):
            t = self.tenants.get(action.as_tenant or self.default_tenant)
            last = None
            for w in range(action.worker_count):
                name = f"worker-{w}"
                root = t.envelopes.get((action.as_user, None)) if t else None
                if root is not None and (action.as_user, name) not in t.envelopes:
                    t.narrow(action.as_user, None, name, ExecutionRole.AS_AGENT, root.effective_scope)
                for _ in range(action.calls_per_worker):
                    last = self._call(action.tool, action.input, action.as_user, action.as_tenant, "subagent", name)
            return last
        return None

    def _envelope(self, t: _Tenant, uid: str, agent: Optional[str]) -> tuple[Optional[AuthorizationEnvelope], str]:
        if not uid:
            return None, ""
        for key in ((uid, agent), (uid, None)):
            if key in t.envelopes:
                env = t.envelopes[key]
                return env, env.chain[-1].principal_id
        for other in self.tenants.values():  # a non-member presents its home-tenant envelope
            if (uid, None) in other.envelopes:
                return other.envelopes[(uid, None)], uid
        return None, uid

    def _call(self, tool: str, tool_input: dict, uid: str, tenant: Optional[str], tier: Optional[str],
              agent_name: Optional[str]) -> ToolOutcome:
        tenant_id = tenant or self.default_tenant
        tier = tier or "interactive"
        t = self.tenants[tenant_id]
        if time.time() >= self.outage_until and not t.sink.is_available:
            t.sink.set_available(True)
        self._gateway_reachable = t.sink.is_available
        env, actor = self._envelope(t, uid, agent_name)
        action = ProposedAction(action_type=tool, target_resource=f"{tenant_id}:{tool}", parameters=dict(tool_input),
                                actor_principal_id=actor, task_session_id=t.session, policy_version=POLICY_VERSION)
        decision = t.pdp[tier].evaluate(action, env, t.budget, t.composition)
        if decision.admitted:
            self.effects.append((tool, dict(tool_input)))
        out = ToolOutcome(tool=tool, input=tool_input, as_user=uid, as_tenant=tenant_id, allowed=decision.admitted,
                          reason=decision.summary, agent_tier=tier, agent_name=agent_name)
        self._tool_outcomes.append(out)
        return out

    def audit_log(self) -> list[AuditEntry]:
        out = []
        for t in self.tenants.values():
            for p in t.sink.packages:
                chain = list(p.get("chain", []))
                out.append(AuditEntry(
                    timestamp=datetime.fromtimestamp(p["committed_at"], tz=timezone.utc).isoformat(),
                    tenant=t.id, actor_uid=chain[0] if chain else None, actor_email=None, tool=p["action_type"],
                    decision="allow" if p["admitted"] else "deny",
                    reason="; ".join(p.get("denial_reasons", [])) or None, trace_id=None,
                    delegation_chain=chain[1:],
                    extra={"apc_actor": p.get("actor"), "envelope_id": p.get("envelope_id"),
                           "content_hash": p.get("content_hash"), "sequence_number": p.get("sequence_number"),
                           "source": "apc.pdp.EvidenceSink"}))
        out.sort(key=lambda a: a.timestamp)
        return out

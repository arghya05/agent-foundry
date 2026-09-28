"""AgentGovBench runner for Microsoft's Agent Governance Toolkit (AGT), Python.

AGT checkout: microsoft/agent-governance-toolkit @ 6b64456 (agent-governance-
python, distribution agent-governance-toolkit-core 5.0.0; import package
``agentmesh``). The runner is a *host*: it builds AGT objects from the scenario
and replays actions through them. It makes no allow/deny decision itself.

AGT components used (paths relative to agent-governance-python/agent-mesh/src):
  * agentmesh/identity/agent_id.py  AgentIdentity.create / .delegate /
    .get_effective_capabilities / .verify_delegation_chain, IdentityRegistry.
    Each scenario user becomes a root AgentIdentity (sponsor = the user's
    email, capabilities = the user's scopes, organization = tenant id).
    Delegations and fan-out workers are AgentIdentity.delegate() children;
    AGT refuses (ValueError) a delegation that widens capabilities.
  * agentmesh/trust/capability.py   capability_scope_matches (AGT's single
    authoritative scope matcher) decides whether the caller's effective
    capabilities cover the tool's required scopes.
  * agentmesh/governance/policy.py  PolicyEngine / Policy / PolicyRule. The
    tenant's tier policy compiles to prioritized rules (priority_first_match,
    the engine default): user+tool 400 > tool 300 > user 200 > default 100.
    Identity and capability facts are injected into the evaluation context
    (as govern() injects ring.* facts) and two fail-closed deny rules act on
    them. Rate limits are PolicyRule.limit ("N/minute"). No rule -> AGT's
    default_action (deny).
  * agentmesh/governance/audit.py   AuditLog.log / .query. Every evaluation is
    logged the way AgentMeshClient.execute_with_governance does it
    (client.py: evaluate -> AuditLog.log(data=context)); the harness audit
    records are read back from AuditLog.query().

Tenancy: AGT has no tenant field in PolicyEngine or AuditLog; its documented
multi-tenant model is one isolated deployment (trust store, policy, audit
stream) per tenant (docs/security/tenant-isolation.md). The runner therefore
instantiates one (IdentityRegistry, PolicyEngine, AuditLog) stack per tenant and
routes a call to the stack of the tenant it names. A caller presents the DID of
its identity in that tenant, or else its home-tenant identity; whether that DID
is trusted is IdentityRegistry.is_trusted of the receiving tenant.

Gateway failure: the policy engine is in-process, so there is no remote PDP to
lose. The outage is simulated as the tenant policy being unavailable (removed
from the PolicyEngine) for the outage window; the decision is then AGT's own
fail-closed default ("No policies loaded (deny by default)"). AGT has no
fail-open configuration (ADR 0013). Outages <= 10 s are slept through, as the
upstream ACP reference runner does.
"""
from __future__ import annotations

import copy
import logging
import time
import uuid
import warnings
from typing import Any, Optional

from benchmark.runner import RunnerMetadata, StatefulRunner
from benchmark.types import (Action, AuditEntry, Delegation, DirectToolCall, GatewayFailure,
                             ParallelFanOut, PolicyChange, Scenario, TierPolicy, ToolOutcome)

warnings.filterwarnings("ignore", category=DeprecationWarning)
from agentmesh.governance.audit import AuditLog  # noqa: E402
from agentmesh.governance.policy import Policy, PolicyEngine, PolicyRule  # noqa: E402
from agentmesh.identity.agent_id import AgentIdentity, IdentityRegistry  # noqa: E402
from agentmesh.identity.delegation import UserContext  # noqa: E402
from agentmesh.trust.capability import capability_scope_matches  # noqa: E402

logging.getLogger("agentmesh").setLevel(logging.ERROR)  # rule-evaluation warnings are expected

AGT_COMMIT = "6b64456"
PERMISSION_TO_AGT = {"allow": "allow", "deny": "deny", "flag": "warn"}  # AGT has no allow-and-mark action
LAYER_PRIORITY = {"user_tools": 400, "tools": 300, "users": 200, "defaults": 100}
POLICY_NAME = "agentgovbench-tenant-policy"


def _q(value: str) -> str:
    if "'" in value or '"' in value:
        raise ValueError(f"value cannot be expressed in the AGT condition DSL: {value!r}")
    return f"'{value}'"


def compile_policy(tenant_id: str, pol: Any) -> Policy:
    """Scenario Policy -> one AGT Policy. Pure configuration translation."""
    rules = [
        PolicyRule(name="identity-not-trusted", condition="identity.status != 'active'", action="deny",
                   priority=10_000, description="caller has no active, trusted AGT identity in this tenant"),
        PolicyRule(name="capability-missing", condition="capability.status != 'granted'", action="deny",
                   priority=9_000, description="caller's effective capabilities do not cover the tool's scopes"),
    ]

    def add(layer: str, tp: TierPolicy, tier: str, uid: str | None = None, tool: str | None = None) -> None:
        parts = [f"agent.tier == {_q(tier)}"]
        if uid is not None:
            parts.append(f"user.id == {_q(uid)}")
        if tool is not None:
            parts.append(f"action.type == {_q(tool)}")
        name = ":".join(x for x in (layer, uid, tool, tier) if x)
        rules.append(PolicyRule(
            name=name, condition=" and ".join(parts), action=PERMISSION_TO_AGT[tp.permission],
            priority=LAYER_PRIORITY[layer],
            description=f"{name}: scenario permission {tp.permission} -> AGT action {PERMISSION_TO_AGT[tp.permission]}",
            limit=f"{tp.rate_limit_per_minute}/minute" if tp.rate_limit_per_minute is not None else None))

    for tier, tp in pol.defaults.items():
        add("defaults", tp, tier)
    for uid, tiers in pol.users.items():
        for tier, tp in tiers.items():
            add("users", tp, tier, uid=uid)
    for tool, tiers in pol.tools.items():
        for tier, tp in tiers.items():
            add("tools", tp, tier, tool=tool)
    for uid, tools in pol.user_tools.items():
        for tool, tiers in tools.items():
            for tier, tp in tiers.items():
                add("user_tools", tp, tier, uid=uid, tool=tool)
    # default_action left at AGT's default ("deny"); scope "tenant".
    return Policy(name=POLICY_NAME, agents=["*"], scope="tenant", rules=rules,
                  description=f"AgentGovBench policy for {tenant_id}")


class _TenantStack:
    """One isolated AGT deployment per tenant (registry, policy engine, audit stream)."""

    def __init__(self, tenant: Any) -> None:
        self.id = tenant.id
        self.policy_doc = copy.deepcopy(tenant.policy)
        self.registry = IdentityRegistry()
        self.engine = PolicyEngine(conflict_strategy="priority_first_match")
        self.audit = AuditLog()
        self.users: dict[str, AgentIdentity] = {}
        self.user_ctx: dict[str, UserContext] = {}
        self.real_email: dict[str, Optional[str]] = {}
        self.agents: dict[tuple[str, str], AgentIdentity] = {}      # (uid, agent_name) -> identity
        self.refused: dict[tuple[str, str], str] = {}               # (uid, agent_name) -> AGT error
        self.policy_loaded = False
        for u in tenant.users:
            # AGT requires a human sponsor e-mail; for users the scenario gives
            # none we use a placeholder (as AgentMeshClient does) and never
            # report it as the actor's e-mail.
            sponsor = u.email or f"{u.uid}@{tenant.id}.agentgovbench.invalid"
            ident = AgentIdentity.create(name=u.uid, sponsor=sponsor, capabilities=list(u.scopes),
                                         organization=tenant.id)
            self.registry.register(ident)
            self.users[u.uid] = ident
            self.real_email[u.uid] = u.email
            self.user_ctx[u.uid] = UserContext.create(user_id=u.uid, user_email=u.email, roles=[u.role],
                                                      permissions=list(u.scopes))
        self.load_policy()

    def load_policy(self) -> None:
        self.engine.load_policy(compile_policy(self.id, self.policy_doc))
        self.policy_loaded = True

    def unload_policy(self) -> None:
        self.engine.remove_policy(POLICY_NAME)
        self.policy_loaded = False


class Runner(StatefulRunner):
    def __init__(self) -> None:
        super().__init__()
        self.effects: list[tuple[str, dict]] = []

    @property
    def metadata(self) -> RunnerMetadata:
        return RunnerMetadata(
            name="agt",
            version=f"agent-governance-toolkit-core 5.0.0 @ {AGT_COMMIT}",
            product="Microsoft Agent Governance Toolkit (Python: agentmesh PolicyEngine + AuditLog + AgentIdentity)",
            vendor="Microsoft",
            notes=("In-process AGT stack per tenant. Decisions: agentmesh.governance.PolicyEngine over rules compiled "
                   "from the scenario policy plus AGT identity/capability facts; delegation: AgentIdentity.delegate; "
                   "audit: agentmesh.governance.AuditLog read back via query(). Gateway outage = tenant policy "
                   "unavailable to the engine (AGT fail-closed default). Fan-out workers are delegated identities."),
            declined_categories={
                "fail_mode_discipline.02_fail_open_honored": (
                    "AGT has no fail-open mode: ADR 0013 'There are no configuration flags to switch to fail-open "
                    "behavior'. Scored as run (fails)."),
                "rate_limit_cascade.01_per_user_not_per_agent": (
                    "PolicyEngine rate-limit counters are keyed (agent_did, policy, rule) by design "
                    "(policy.py _rate_limit_key); there is no per-sponsor/per-user aggregation across delegated "
                    "agents. Scored as run."),
                "supplemental.08_flag_is_allowed_and_marked": (
                    "AGT has no allow-and-mark decision: 'warn' yields allowed=False (policy.py PolicyDecision "
                    "allowed = action == 'allow'); advisory flag_for_review is for non-deterministic classifiers. "
                    "flag is mapped to warn. Scored as run."),
                "cross_tenant_isolation": (
                    "Not declined, but note: AGT has no tenant concept in PolicyEngine/AuditLog; isolation comes from "
                    "running one AGT stack per tenant, which is AGT's documented deployment model."),
            },
        )

    # -- lifecycle -------------------------------------------------------
    def setup(self, scenario: Scenario) -> None:
        super().setup(scenario)
        self.effects = []
        self.stacks = {t.id: _TenantStack(t) for t in scenario.setup.tenants}
        self.default_tenant = scenario.setup.tenants[0].id if scenario.setup.tenants else "tenant-a"
        self.required = {t.name: list(t.required_scopes) for t in scenario.setup.tools}
        self.outage_until = 0.0

    # -- actions ---------------------------------------------------------
    def execute_action(self, action: Action) -> Optional[ToolOutcome]:
        if isinstance(action, Delegation):
            self._delegate(action)
            return None
        if isinstance(action, GatewayFailure):
            self.outage_until = time.time() + action.duration_seconds
            for st in self.stacks.values():
                st.unload_policy()
            if action.duration_seconds <= 10:
                time.sleep(action.duration_seconds + 0.2)
            return None
        if isinstance(action, PolicyChange):
            self._policy_change(action)
            return None
        if isinstance(action, DirectToolCall):
            return self._call(action.tool, action.input, action.as_user, action.as_tenant,
                              action.agent_tier, action.agent_name)
        if isinstance(action, ParallelFanOut):
            st = self.stacks.get(action.as_tenant or self.default_tenant)
            last = None
            for w in range(action.worker_count):
                name = f"worker-{w}"
                if st is not None and action.as_user in st.users and (action.as_user, name) not in st.agents:
                    root = st.users[action.as_user]
                    child = root.delegate(name, list(root.capabilities))  # K spawned subagents
                    st.registry.register(child)
                    st.agents[(action.as_user, name)] = child
                for _ in range(action.calls_per_worker):
                    last = self._call(action.tool, action.input, action.as_user, action.as_tenant,
                                      "subagent", name)
            return last
        return None

    def _delegate(self, d: Delegation) -> None:
        st = self.stacks.get(d.as_tenant or self.default_tenant)
        if st is None or d.as_user not in st.users:
            return
        key_from, key_to = (d.as_user, d.from_agent), (d.as_user, d.to_agent)
        if key_from in st.refused:
            st.refused[key_to] = f"parent {d.from_agent!r} has no identity"
            return
        parent = st.agents.get(key_from)
        if parent is None:  # top-level orchestrator acting with the user's authority
            root = st.users[d.as_user]
            parent = root.delegate(d.from_agent, list(root.capabilities))
            st.registry.register(parent)
            st.agents[key_from] = parent
        try:
            child = parent.delegate(d.to_agent, list(d.delegated_scopes))
        except ValueError as exc:  # AGT refuses capability widening
            st.refused[key_to] = str(exc)
            st.audit.log(event_type="delegation", agent_did=str(parent.did), action="delegate",
                         outcome="denied", data={"to_agent": d.to_agent, "requested": list(d.delegated_scopes),
                                                 "error": str(exc)})
            return
        st.registry.register(child)
        st.agents[key_to] = child
        st.audit.log(event_type="delegation", agent_did=str(parent.did), action="delegate",
                     outcome="success", data={"to_agent": d.to_agent, "child_did": str(child.did),
                                              "capabilities": list(child.capabilities)})

    def _policy_change(self, pc: PolicyChange) -> None:
        st = self.stacks.get(pc.tenant or self.default_tenant)
        if st is None:
            return
        tier = pc.tier or "interactive"
        p = st.policy_doc
        if pc.user and pc.tool:
            table = p.user_tools.setdefault(pc.user, {}).setdefault(pc.tool, {})
        elif pc.user:
            table = p.users.setdefault(pc.user, {})
        elif pc.tool:
            table = p.tools.setdefault(pc.tool, {})
        else:
            table = p.defaults
        old = table.get(tier, TierPolicy())
        table[tier] = TierPolicy(permission=pc.set_permission or old.permission,
                                 rate_limit_per_minute=(pc.set_rate_limit if pc.set_rate_limit is not None
                                                        else old.rate_limit_per_minute),
                                 post_transform=old.post_transform)
        if st.policy_loaded:
            st.load_policy()  # same name -> replaces the loaded policy

    # -- a governed call -------------------------------------------------
    def _presented_identity(self, st: _TenantStack, uid: str, agent_name: Optional[str]) -> Optional[AgentIdentity]:
        if not uid:
            return None
        if agent_name and (uid, agent_name) in st.refused:
            return None
        if agent_name and (uid, agent_name) in st.agents:
            return st.agents[(uid, agent_name)]
        if uid in st.users:
            return st.users[uid]
        for other in self.stacks.values():  # a non-member presents its home-tenant identity
            if uid in other.users:
                return other.users[uid]
        return None

    def _lineage(self, ident: AgentIdentity) -> list[AgentIdentity]:
        registries = [s.registry for s in self.stacks.values()]
        chain, cur = [ident], ident
        while cur.parent_did and len(chain) < 64:
            parent = next((r.get(cur.parent_did) for r in registries if r.get(cur.parent_did)), None)
            if parent is None:
                break
            chain.append(parent)
            cur = parent
        return list(reversed(chain))  # root (user identity) first

    def _call(self, tool: str, tool_input: dict, uid: str, tenant: Optional[str],
              tier: Optional[str], agent_name: Optional[str]) -> ToolOutcome:
        tenant_id = tenant or self.default_tenant
        tier = tier or "interactive"
        st = self.stacks.get(tenant_id)
        if st is None:
            self._errors.append(f"no AGT deployment for tenant {tenant_id!r}")
            out = ToolOutcome(tool=tool, input=tool_input, as_user=uid, as_tenant=tenant_id, allowed=False,
                              reason="no AGT deployment for tenant", agent_tier=tier, agent_name=agent_name)
            self._tool_outcomes.append(out)
            return out
        outage = time.time() < self.outage_until
        if outage and st.policy_loaded:
            st.unload_policy()
        elif not outage and not st.policy_loaded:
            st.load_policy()
        self._gateway_reachable = not outage

        ident = self._presented_identity(st, uid, agent_name)
        lineage = self._lineage(ident) if ident else []
        root = lineage[0] if lineage else None
        trusted = bool(ident and st.registry.is_trusted(str(ident.did)) and ident.is_active()
                       and AgentIdentity.verify_delegation_chain(ident, st.registry))
        required = self.required.get(tool, [])
        effective = ident.get_effective_capabilities(st.registry) if ident else []
        granted = all(any(capability_scope_matches(c, r) for c in effective) for r in required)
        user_id = root.name if root else None
        uctx = st.user_ctx.get(user_id) if user_id else None
        context: dict[str, Any] = {
            "action": {"type": tool}, "tool_name": tool,
            "tool": {"name": tool, "required_scopes": required},
            "agent": {"tier": tier, "name": agent_name, "did": str(ident.did) if ident else None},
            "identity": {"status": "active"} if trusted else {},
            "capability": {"status": "granted" if granted else "missing", "effective": effective},
            "user": {"id": user_id or "", "email": uctx.user_email if uctx else None,
                     "roles": uctx.roles if uctx else []},
            "tenant": {"id": tenant_id},
            "input": dict(tool_input),
        }
        agent_did = str(ident.did) if ident else "anonymous"
        decision = st.engine.evaluate(agent_did, context)
        trace_id = uuid.uuid4().hex
        chain = [i.name for i in lineage[1:]]
        st.audit.log(
            event_type="policy_evaluation", agent_did=agent_did, action=tool,
            outcome="success" if decision.allowed else "denied", policy_decision=decision.action,
            trace_id=trace_id,
            data={"context": context, "rule": decision.matched_rule or "", "reason": decision.reason or "",
                  "rate_limited": decision.rate_limited, "delegation_chain": chain,
                  "user_id": user_id, "user_email": st.real_email.get(user_id) if user_id else None,
                  "tenant": tenant_id, "policy_source_available": st.policy_loaded},
        )
        if decision.allowed:
            self.effects.append((tool, dict(tool_input)))  # the tool body runs only after an AGT allow
        out = ToolOutcome(tool=tool, input=tool_input, as_user=uid, as_tenant=tenant_id,
                          allowed=decision.allowed, reason=decision.reason, agent_tier=tier, agent_name=agent_name)
        self._tool_outcomes.append(out)
        return out

    # -- observations ----------------------------------------------------
    def audit_log(self) -> list[AuditEntry]:
        out: list[AuditEntry] = []
        for st in self.stacks.values():
            for e in st.audit.query(event_type="policy_evaluation", limit=None):
                d = e.data
                out.append(AuditEntry(
                    timestamp=e.timestamp.isoformat(), tenant=st.id,  # the per-tenant audit stream it lives in
                    actor_uid=d.get("user_id"), actor_email=d.get("user_email"), tool=e.action,
                    decision="allow" if e.outcome == "success" else "deny", reason=d.get("reason") or None,
                    trace_id=e.trace_id, delegation_chain=list(d.get("delegation_chain", [])),
                    extra={"agt_entry_id": e.entry_id, "agt_entry_hash": e.entry_hash, "agent_did": e.agent_did,
                           "agt_policy_decision": e.policy_decision, "matched_rule": d.get("rule"),
                           "agent_tier": d["context"]["agent"]["tier"], "agent_name": d["context"]["agent"]["name"],
                           "rate_limited": d.get("rate_limited"), "source": "agentmesh.governance.AuditLog"},
                ))
        out.sort(key=lambda a: a.timestamp)
        return out


class RunnerSharedIdentity(Runner):
    """Sensitivity variant: fan-out workers act under the user's own AGT
    identity instead of delegated child identities. Rate-limit counters are
    keyed per agent DID, so this is the configuration most favourable to AGT
    on the cascade scenarios; delegation semantics are unchanged elsewhere."""

    @property
    def metadata(self) -> RunnerMetadata:
        base = super().metadata
        return RunnerMetadata(name="agt_shared_identity", version=base.version, product=base.product,
                              vendor=base.vendor, notes=base.notes + " Variant: fan-out workers use the user's "
                              "identity (no delegated child per worker).",
                              declined_categories=base.declined_categories)

    def execute_action(self, action: Action) -> Optional[ToolOutcome]:
        if isinstance(action, ParallelFanOut):
            last = None
            for w in range(action.worker_count):
                for _ in range(action.calls_per_worker):
                    last = self._call(action.tool, action.input, action.as_user, action.as_tenant,
                                      "subagent", f"worker-{w}")
            return last
        return super().execute_action(action)

"""Single-control ablations of the Foundry AgentGovBench runner.

Each ablation removes or weakens exactly one control after the normal setup.
They are experiment instruments, not supported configurations: they patch
runner-owned objects so the production control plane has no ablation flags.
"""
from __future__ import annotations

import copy
import dataclasses

import agent_foundry.control_plane as cp
from agent_foundry.control_plane import WorkspacePolicy

ABLATIONS = {
    "no_attenuation": "delegated scopes ignored; subagents hold the user's full scopes",
    "no_chain": "delegation chain not recorded in the audit",
    "no_rate_limit": "per-principal rate limiter disabled",
    "fail_mode_learned_only": "fail mode only learned from a successful policy read",
    "fail_open_always": "policy-source outage always fails open",
    "no_tenant_membership": "principal accepted in any tenant that knows the uid anywhere",
    "no_deny_overrides": "user-wide rule silently wins over a tool-wide rule",
    "stale_policy": "policy snapshot taken at setup; runtime changes ignored",
    "no_audit": "decisions are not written to the audit log",
    "no_scope_check_plane": "control-plane required-scope check removed (gateway PDP still checks)",
    "no_scope_check_both": "required-scope checks removed in the plane and the gateway PDP",
    "no_tier_rules": "tier permissions ignored (every tier allowed)",
    "anonymous_allowed": "empty principal treated as a member",
    "deny_all": "vacuity probe: every call denied, decisions audited",
    "allow_all": "vacuity probe: every call allowed and audited, no enforcement",
}


def apply(runner, name: str):
    if name not in ABLATIONS:
        raise SystemExit(f"unknown ablation {name!r}; choose from {sorted(ABLATIONS)}")
    original_setup = runner.setup

    def setup(scenario):
        original_setup(scenario)
        plane, store = runner.plane, runner.store
        if name == "no_attenuation":
            real = cp.current_delegation
            runner._restore = lambda: setattr(cp, "current_delegation", real)
            cp.current_delegation = lambda: (real()[0], None)
        elif name == "no_chain":
            real = cp.current_delegation
            runner._restore = lambda: setattr(cp, "current_delegation", real)
            cp.current_delegation = lambda: ((), real()[1])
        elif name == "no_rate_limit":
            plane.limiter.try_acquire = lambda key, limit: True
        elif name == "fail_mode_learned_only":
            plane._fail_modes = {}
        elif name == "fail_open_always":
            plane._fail_modes = {t: "fail_open" for t in plane._fail_modes}
            real_get = store.get
            def get(tid):
                tenant = real_get(tid)
                if tenant is not None:
                    tenant.policy.fail_mode = "fail_open"
                return tenant
            store.get = get
        elif name == "no_tenant_membership":
            real_get = store.get
            def get(tid):
                tenant = real_get(tid)
                if tenant is None:
                    return None
                merged = copy.copy(tenant)
                merged.principals = {uid: p for t in store._tenants.values() for uid, p in t.principals.items()}
                return merged
            store.get = get
        elif name == "no_deny_overrides":
            def resolve(self, uid, tool, tier):
                for rule in (self.user_tools.get(uid, {}).get(tool, {}).get(tier),
                             self.users.get(uid, {}).get(tier),
                             self.tools.get(tool, {}).get(tier), self.defaults.get(tier)):
                    if rule is not None:
                        return rule
                return None
            real = WorkspacePolicy.resolve
            runner._restore = lambda: setattr(WorkspacePolicy, "resolve", real)
            WorkspacePolicy.resolve = resolve
        elif name == "stale_policy":
            snapshot = copy.deepcopy(store._tenants)
            real_get = store.get
            def get(tid):
                real_get(tid)  # keeps outage semantics
                return snapshot.get(tid)
            store.get = get
        elif name == "no_audit":
            plane.gateway.config.audit.record = lambda **kw: None
        elif name == "no_scope_check_plane":
            plane.required_scopes = {}
        elif name == "no_scope_check_both":
            plane.required_scopes = {}
            for tool in plane.gateway.config.tools.names():
                spec = plane.gateway.config.tools.get(tool)
                plane.gateway.config.tools.register(dataclasses.replace(spec, scopes=frozenset()))
        elif name == "no_tier_rules":
            real = WorkspacePolicy.resolve
            def resolve(self, uid, tool, tier):
                rule = real(self, uid, tool, tier)
                return cp.TierRule("allow", rule.rate_limit_per_minute if rule else None)
            runner._restore = lambda: setattr(WorkspacePolicy, "resolve", real)
            WorkspacePolicy.resolve = resolve
        elif name == "anonymous_allowed":
            real_get = store.get
            real_invoke = plane.invoke

            # An empty uid is rejected before lookup; substitute a member principal.
            def invoke_anon(tool, args, *, user_id, **kw):
                if user_id:
                    return real_invoke(tool, args, user_id=user_id, **kw)
                t = real_get(kw["tenant_id"])
                t.principals["anonymous"] = cp.Principal("anonymous", None, frozenset(
                    s for req in plane.required_scopes.values() for s in req))
                return real_invoke(tool, args, user_id="anonymous", **kw)
            plane.invoke = invoke_anon
        elif name in ("deny_all", "allow_all"):
            allowed = name == "allow_all"
            def invoke(tool, args, *, tenant_id, user_id, tier="interactive", agent_name=None, trace_id=None):
                chain, _ = cp.current_delegation()
                base = dict(tool=tool, args=args, tenant=tenant_id, uid=user_id, tier=tier,
                            agent_name=agent_name, trace_id=trace_id or "probe", chain=chain)
                return plane._record(base, None, allowed, "allow" if allowed else "deny", "vacuity probe")
            plane.invoke = invoke

    def teardown():
        restore = getattr(runner, "_restore", None)
        if restore:
            restore()
            runner._restore = None

    runner.setup = setup
    runner.teardown = teardown
    return runner


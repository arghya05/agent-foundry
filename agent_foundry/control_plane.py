"""Multi-tenant control plane in front of the governed tool gateway.

`GovernedToolGateway` enforces one agent's configured policy for one call.
A platform serving many tenants also needs per-tenant workspace policy that
can change while agents run, tier-aware rules (interactive, subagent, api,
background), per-principal rate limits that subagents cannot multiply,
delegation that only narrows authority, a declared behavior when the policy
source is unavailable, and one audit record per decision. This module adds
those controls without replacing the gateway: every admitted call still
executes through `GovernedToolGateway`, so its PDP, budget, schema and audit
checks continue to apply.

Decision order is fixed and deny-first: authentication, policy availability,
tenant membership, tool registration, tier rule, required scopes after
delegation, and finally the rate limit. Denied calls do not consume rate-limit
capacity. Policy is re-read from the store on every call, so a revocation
applies to the next call. Rules at incomparable specificity (a user-wide tier
rule and a tool-wide tier rule) combine with deny-overrides; only a rule that
names both the user and the tool supersedes them.

Limits: the in-memory store and limiter are process-local. A fleet needs a
shared policy store and limiter behind the same interfaces. Fail-open is an
explicit per-tenant choice; it still writes a local audit record and still
runs the gateway's local checks. No guarantee covers tools that bypass this
entry point.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Iterator, Literal

from .contracts import Identity, ToolResult
from .core.execution_context import ExecutionContext
from .execution_scope import _CURRENT
from .tools_gateway import PermissionDenied

Permission = Literal["allow", "flag", "deny"]
FailMode = Literal["fail_closed", "fail_open"]
TIERS = ("interactive", "subagent", "api", "background")
_PERMISSION_ORDER = {"allow": 0, "flag": 1, "deny": 2}


@dataclass(frozen=True)
class TierRule:
    permission: Permission = "allow"
    rate_limit_per_minute: int | None = None


@dataclass(frozen=True)
class Principal:
    uid: str
    email: str | None = None
    scopes: frozenset[str] = frozenset()


@dataclass
class WorkspacePolicy:
    """One tenant's policy document. Keys are tier names."""

    defaults: dict[str, TierRule] = field(default_factory=dict)
    tools: dict[str, dict[str, TierRule]] = field(default_factory=dict)
    users: dict[str, dict[str, TierRule]] = field(default_factory=dict)
    user_tools: dict[str, dict[str, dict[str, TierRule]]] = field(default_factory=dict)
    fail_mode: FailMode = "fail_closed"

    def resolve(self, uid: str, tool: str, tier: str) -> TierRule | None:
        """Effective rule, or None when no rule covers this tier (deny).

        Most specific wins: user+tool, then the deny-overrides combination of
        the user-wide and tool-wide rules, then the tenant default. The rate
        limit is the tightest limit declared by any applicable rule."""
        exact = self.user_tools.get(uid, {}).get(tool, {}).get(tier)
        partial = [r for r in (self.users.get(uid, {}).get(tier), self.tools.get(tool, {}).get(tier)) if r]
        default = self.defaults.get(tier)
        if exact is not None:
            chosen = exact
        elif partial:
            chosen = max(partial, key=lambda r: _PERMISSION_ORDER[r.permission])
        elif default is not None:
            chosen = default
        else:
            return None
        limits = [r.rate_limit_per_minute for r in (exact, *partial, default)
                  if r is not None and r.rate_limit_per_minute is not None]
        return replace(chosen, rate_limit_per_minute=min(limits) if limits else None)


@dataclass
class Tenant:
    id: str
    principals: dict[str, Principal] = field(default_factory=dict)
    policy: WorkspacePolicy = field(default_factory=WorkspacePolicy)


class PolicySourceUnavailable(Exception):
    """The policy source could not be read; the tenant's fail mode decides."""


class InMemoryPolicyStore:
    """Reference policy source. `outage()` simulates an unreachable source
    for fault testing; a production store raises the same exception."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._tenants: dict[str, Tenant] = {}
        self._lock = threading.Lock()
        self._down_until = 0.0
        self._clock = clock

    def put(self, tenant: Tenant) -> None:
        with self._lock:
            self._tenants[tenant.id] = tenant

    def get(self, tenant_id: str) -> Tenant | None:
        if self._clock() < self._down_until:
            raise PolicySourceUnavailable("policy source unreachable")
        with self._lock:
            return self._tenants.get(tenant_id)

    def outage(self, seconds: float) -> None:
        self._down_until = self._clock() + seconds

    def update(self, tenant_id: str, fn: Callable[[WorkspacePolicy], None]) -> None:
        with self._lock:
            fn(self._tenants[tenant_id].policy)


class SlidingWindowLimiter:
    """Counts admitted calls per key over a trailing window.

    The default clock is monotonic: a wall-clock adjustment must not shrink
    or extend a window."""

    def __init__(self, window_s: float = 60.0, clock: Callable[[], float] = time.monotonic) -> None:
        self.window_s = window_s
        self._clock = clock
        self._events: dict[tuple, deque] = {}
        self._lock = threading.Lock()

    def try_acquire(self, key: tuple, limit: int) -> bool:
        with self._lock:
            now = self._clock()
            events = self._events.setdefault(key, deque())
            while events and events[0] <= now - self.window_s:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True


@contextmanager
def delegation(agent: str, scopes: Iterable[str] | None = None) -> Iterator[None]:
    """Hand work to `agent` inside the current request scope.

    The delegation chain grows by one hop. Declared scopes can only narrow
    what the parent hop held: effective = parent ∩ declared. `None` keeps the
    parent's scopes (the root orchestrator declares none)."""
    parent = _CURRENT.get()
    chain = tuple(parent.get("request_delegation_chain", ())) + (agent,)
    values: dict[str, Any] = {"request_delegation_chain": chain}
    inherited = parent.get("request_delegated_scopes")
    if scopes is not None:
        declared = frozenset(scopes)
        values["request_delegated_scopes"] = declared if inherited is None else inherited & declared
    token = _CURRENT.set({**parent, **values})
    try:
        yield
    finally:
        _CURRENT.reset(token)


def current_delegation() -> tuple[tuple[str, ...], frozenset[str] | None]:
    scope = _CURRENT.get()
    return tuple(scope.get("request_delegation_chain", ())), scope.get("request_delegated_scopes")


@dataclass
class Decision:
    allowed: bool
    decision: Literal["allow", "flag", "deny"]
    reason: str | None
    trace_id: str
    result: ToolResult | None = None
    policy_source_reachable: bool = True


class ControlPlane:
    """Admit, audit and execute tool calls for many tenants.

    `gateway` is a `GovernedToolGateway`; `required_scopes` maps tool names to
    the scopes a principal must hold (all of them) after delegation."""

    def __init__(self, gateway: Any, store: InMemoryPolicyStore, *,
                 required_scopes: dict[str, frozenset[str]] | None = None,
                 limiter: SlidingWindowLimiter | None = None,
                 fail_modes: dict[str, FailMode] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.gateway = gateway
        self.store = store
        self.required_scopes = dict(required_scopes or {})
        self.limiter = limiter or SlidingWindowLimiter(clock=time.monotonic if clock is time.time else clock)
        self._clock = clock
        # The fail mode is deployment configuration, not a remote fact: it is
        # needed precisely when the policy source cannot be read. Declared
        # modes seed this table; successful reads refresh it. An undeclared
        # tenant fails closed.
        self._fail_modes: dict[str, FailMode] = dict(fail_modes or {})
        self.policy_source_reachable = True

    @property
    def audit(self):
        return self.gateway.config.audit

    def invoke(self, tool: str, args: dict[str, Any], *, tenant_id: str, user_id: str,
               tier: str = "interactive", agent_name: str | None = None,
               trace_id: str | None = None) -> Decision:
        trace_id = trace_id or uuid.uuid4().hex
        chain, delegated = current_delegation()
        base = dict(tool=tool, args=dict(args), tenant=tenant_id, uid=user_id, tier=tier,
                    agent_name=agent_name, trace_id=trace_id, chain=chain)

        if not user_id:
            return self._record(base, None, False, "deny", "unauthenticated principal")
        try:
            tenant = self.store.get(tenant_id)
            self.policy_source_reachable = True
        except PolicySourceUnavailable:
            self.policy_source_reachable = False
            mode = self._fail_modes.get(tenant_id, "fail_closed")
            if mode == "fail_closed":
                return self._record(base, None, False, "deny", "policy source unavailable (fail_closed)",
                                    reachable=False)
            # Fail-open is the tenant's explicit choice. Remote facts (scopes,
            # tier rules) are unknown and treated as satisfied; the call is
            # audited locally and the gateway's local allowlist still applies.
            return self._execute(base, None, frozenset(), "allow", "policy source unavailable (fail_open)",
                                 reachable=False)
        if tenant is None:
            return self._record(base, None, False, "deny", f"unknown tenant {tenant_id!r}")
        self._fail_modes[tenant_id] = tenant.policy.fail_mode
        principal = tenant.principals.get(user_id)
        if principal is None:
            return self._record(base, None, False, "deny", f"principal {user_id!r} is not a member of tenant {tenant_id!r}")
        if not self.gateway.config.tools.has(tool):
            return self._record(base, principal, False, "deny", f"unknown tool {tool!r}")
        rule = tenant.policy.resolve(user_id, tool, tier)
        if rule is None:
            return self._record(base, principal, False, "deny", f"no policy rule for tier {tier!r}")
        effective = principal.scopes if delegated is None else principal.scopes & delegated
        missing = self.required_scopes.get(tool, frozenset()) - effective
        if missing:
            return self._record(base, principal, False, "deny", f"missing scopes {sorted(missing)}")
        if rule.permission == "deny":
            return self._record(base, principal, False, "deny", f"policy denies {tool!r} at tier {tier!r}")
        if rule.rate_limit_per_minute is not None and not self.limiter.try_acquire(
                (tenant_id, user_id, tier), rule.rate_limit_per_minute):
            return self._record(base, principal, False, "deny",
                                f"rate limit {rule.rate_limit_per_minute}/min exceeded")
        return self._execute(base, principal, effective, rule.permission,
                             "flagged by policy" if rule.permission == "flag" else "policy allows")

    def _execute(self, base: dict, principal: Principal | None, scopes: frozenset[str],
                 decision: Literal["allow", "flag"], reason: str, *, reachable: bool = True) -> Decision:
        context = ExecutionContext(user_id=base["uid"], tenant_id=base["tenant"], trace_id=base["trace_id"],
                                   thread_id=f"cp-{base['trace_id']}",
                                   permissions=scopes if principal is not None else frozenset(
                                       self.required_scopes.get(base["tool"], frozenset())))
        try:
            result = self.gateway.invoke(base["tool"], base["args"], context=context)
        except PermissionDenied as exc:
            return self._record(base, principal, False, "deny", f"gateway: {exc}", reachable=reachable)
        return self._record(base, principal, True, decision, reason, result=result, reachable=reachable)

    def _record(self, base: dict, principal: Principal | None, allowed: bool, decision: str,
                reason: str | None, *, result: ToolResult | None = None, reachable: bool = True) -> Decision:
        identity = Identity(id=base["uid"], tenant_id=base["tenant"], roles=tuple(sorted(principal.scopes)) if principal else ())
        self.audit.record(identity=identity, action="governance_decision", tool=base["tool"],
                          decision=decision, allowed=allowed, reason=reason, trace_id=base["trace_id"],
                          actor_email=principal.email if principal else None,
                          delegation_chain=list(base["chain"]), agent_tier=base["tier"],
                          agent_name=base["agent_name"], policy_source_reachable=reachable,
                          tool_ok=None if result is None else result.ok)
        return Decision(allowed, decision, reason, base["trace_id"], result, reachable)  # type: ignore[arg-type]

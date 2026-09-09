"""Policy Engine — real policy-as-code via OPA (Open Policy Agent), for teams that
want Rego-based governance instead of (or alongside) the plain Policy dataclass in
contracts.py. PolicyEngine is a Protocol; Policy stays the zero-dependency default,
OPAPolicyEngine is the real alternative — queries a running `opa run --server`
instance's REST API over stdlib urllib, no new dependency required.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from .contracts import GuardrailResult, Identity, Policy

if TYPE_CHECKING:
    from .guardrails import GuardrailChecks
    from .security import EgressPolicy


class PolicyEngine(Protocol):
    def allow(self, input: dict[str, Any]) -> bool: ...


# data_classification values that gate on a matching identity role — see
# PolicyDecisionPoint.decide()'s own comment. "public"/"internal" (the
# ToolSpec default) are unrestricted, matching how EgressPolicy/scopes both
# default to "no restriction declared."
_RESTRICTED_CLASSIFICATIONS = ("confidential", "restricted")


@dataclass
class PolicyDecisionPoint:
    """The single mandatory call site the review asked for, instead of
    Policy.allowed_tools/EgressPolicy/an external PolicyEngine being loosely
    related utilities a caller has to remember to wire together itself.
    Composes (in order) a ToolSpec-scope check, a data-classification role
    check, the deterministic action guardrail, an optional external
    PolicyEngine (OPAPolicyEngine/CedarPolicyEngine above), and an optional
    EgressPolicy host allowlist — the same delegate-don't-replace
    composition guardrails.LLMGuardrails.check_action already uses for
    GuardrailEngine. Each piece stays independently usable/testable; this is
    just the thing that calls all of them for one tool-call decision.

    Wire it in via AgentConfig.pdp — make_act_node/native_engine._act ALWAYS
    call `.decide(...)`, never `config.guardrails.check_action(...)`
    directly (AgentConfig.__post_init__ guarantees `pdp` is never None), so
    this is a real invariant, not an opt-in a caller could forget."""

    guardrails: "GuardrailChecks"
    policy_engine: PolicyEngine | None = None
    egress: "EgressPolicy | None" = None

    def decide(
        self, tool_name: str, args: dict[str, Any], *, identity: Identity, policy: Policy,
        destructive: bool = False, cost_so_far: float = 0.0, hosts: frozenset[str] = frozenset(),
        scopes: frozenset[str] = frozenset(), requires_confirmation: bool = False,
        data_classification: str = "internal",
    ) -> GuardrailResult:
        # ToolSpec.scopes: the identity must carry at least one of the
        # tool's declared scopes — previously this field was pure metadata,
        # never checked against Identity.roles at all.
        if scopes and not (scopes & set(identity.roles)):
            return GuardrailResult(False, f"{tool_name!r} requires one of scopes {sorted(scopes)} — identity {identity.id!r} has none of them", "action")
        # ToolSpec.data_classification: confidential/restricted tools need a
        # matching "data:<classification>" role — same never-enforced-before gap.
        if data_classification in _RESTRICTED_CLASSIFICATIONS:
            required_role = f"data:{data_classification}"
            if required_role not in identity.roles:
                return GuardrailResult(False, f"{tool_name!r} handles {data_classification!r} data — identity {identity.id!r} lacks the {required_role!r} role", "action")
        gr = self.guardrails.check_action(tool_name, cost_so_far=cost_so_far, destructive=destructive)
        if not gr.allowed:
            return gr
        # ToolSpec.requires_confirmation: forces the SAME "needs human
        # approval" signal make_act_node already knows how to interrupt()
        # on, independent of Policy.requires_approval/autonomy — a tool can
        # demand confirmation on its own terms, not only via the policy.
        if requires_confirmation:
            return GuardrailResult(False, f"{tool_name!r} requires human approval before executing (ToolSpec.requires_confirmation=True)", "action")
        if self.policy_engine is not None:
            allowed = self.policy_engine.allow({
                "identity_id": identity.id, "tool": tool_name, "allowed_tools": list(policy.allowed_tools),
                "cost_so_far": cost_so_far, "max_cost": policy.max_cost_usd_per_thread,
            })
            if not allowed:
                return GuardrailResult(False, f"{tool_name!r} denied by external policy engine", "action")
        if self.egress is not None:
            # `hosts` comes from ToolSpec.egress_hosts (the tool's OWN
            # declared destinations, known at registration time — see
            # http_tool()) — not derived from `args`, which vary per call
            # and have no generically reliable "this is the host" field.
            for host in hosts:
                if not self.egress.check(tool_name, host):
                    return GuardrailResult(False, f"{tool_name!r} denied: {host!r} not in egress allowlist for this tool", "action")
        return GuardrailResult(True, stage="action")


@dataclass
class OPAPolicyEngine:
    """`path` is the Rego decision path — e.g. "agent_foundry/allow" for a policy
    file starting `package agent_foundry` with an `allow` rule."""

    base_url: str
    path: str = "agent_foundry/allow"
    timeout_s: float = 5.0

    def allow(self, input: dict[str, Any]) -> bool:
        body = json.dumps({"input": input}).encode()
        req = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/v1/data/{self.path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            return bool(json.loads(resp.read()).get("result", False))


DEFAULT_CEDAR_POLICY = """
permit (
  principal,
  action == Action::"CallTool",
  resource
)
when {
  resource in principal.allowed_tools &&
  context.cost_so_far.lessThan(context.max_cost)
};
"""


@dataclass
class CedarPolicyEngine:
    """Real Cedar (AWS's open policy language) authorization via the embedded
    `cedarpy` engine — no server to run, unlike OPA, since Cedar's Rust engine
    executes in-process. Verified against the real cedarpy package: entity-set
    membership (`resource in principal.allowed_tools`) and a genuine Cedar
    `decimal` extension comparison for the cost ceiling — not just a type-shape
    check. Requires `pip install cedarpy`.

    `allow()` takes the identical convenience input shape as OPAPolicyEngine —
    {"identity_id", "tool", "allowed_tools", "cost_so_far", "max_cost"} — so a
    team can point Policy enforcement at OPA or Cedar interchangeably. `policy`
    is raw Cedar policy text; DEFAULT_CEDAR_POLICY matches OPAPolicyEngine's own
    reference semantics (tool must be in allowed_tools, cost must stay under
    the ceiling) so the two engines are drop-in equivalents out of the box.
    """

    policy: str = DEFAULT_CEDAR_POLICY
    action_id: str = "CallTool"

    def allow(self, input: dict[str, Any]) -> bool:
        import cedarpy

        identity_id = input.get("identity_id", "unknown")
        tool = input.get("tool", "")
        allowed_tools = input.get("allowed_tools", [])
        cost_so_far = float(input.get("cost_so_far", 0.0))
        max_cost = float(input.get("max_cost", 1_000_000.0))

        request = {
            "principal": {"type": "Agent", "id": identity_id},
            "action": {"type": "Action", "id": self.action_id},
            "resource": {"type": "Tool", "id": tool},
            "context": {
                "cost_so_far": {"__extn": {"fn": "decimal", "arg": f"{cost_so_far:.4f}"}},
                "max_cost": {"__extn": {"fn": "decimal", "arg": f"{max_cost:.4f}"}},
            },
        }
        entities = [
            {
                "uid": {"type": "Agent", "id": identity_id},
                "attrs": {"allowed_tools": [{"__entity": {"type": "Tool", "id": t}} for t in allowed_tools]},
                "parents": [],
            },
            {"uid": {"type": "Tool", "id": tool}, "attrs": {}, "parents": []},
        ]
        result = cedarpy.is_authorized(request, self.policy, entities)
        return result.decision == cedarpy.Decision.Allow

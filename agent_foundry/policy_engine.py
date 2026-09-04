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
from typing import Any, Protocol


class PolicyEngine(Protocol):
    def allow(self, input: dict[str, Any]) -> bool: ...


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

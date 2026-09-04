import pytest

pytest.importorskip("cedarpy")

from agent_foundry.policy_engine import CedarPolicyEngine


def test_cedar_allows_a_tool_in_the_allowed_set_under_budget():
    engine = CedarPolicyEngine()
    assert engine.allow({
        "identity_id": "support-agent-1",
        "tool": "lookup_order",
        "allowed_tools": ["lookup_order", "issue_refund"],
        "cost_so_far": 0.10,
        "max_cost": 1.0,
    })


def test_cedar_denies_a_tool_not_in_the_allowed_set():
    engine = CedarPolicyEngine()
    assert not engine.allow({
        "identity_id": "support-agent-1",
        "tool": "delete_account",
        "allowed_tools": ["lookup_order", "issue_refund"],
        "cost_so_far": 0.10,
        "max_cost": 1.0,
    })


def test_cedar_denies_when_cost_ceiling_exceeded():
    engine = CedarPolicyEngine()
    assert not engine.allow({
        "identity_id": "support-agent-1",
        "tool": "lookup_order",
        "allowed_tools": ["lookup_order"],
        "cost_so_far": 1.5,
        "max_cost": 1.0,
    })


def test_cedar_denies_when_allowed_tools_is_empty():
    engine = CedarPolicyEngine()
    assert not engine.allow({
        "identity_id": "support-agent-1",
        "tool": "lookup_order",
        "allowed_tools": [],
        "cost_so_far": 0.0,
        "max_cost": 1.0,
    })


def test_cedar_and_opa_style_input_shape_are_interchangeable():
    """Same convenience input dict a team would pass to OPAPolicyEngine.allow()
    works unchanged against CedarPolicyEngine — swapping policy engines doesn't
    require rewriting the call sites."""
    engine = CedarPolicyEngine()
    shared_input = {
        "identity_id": "agent-1",
        "tool": "lookup_order",
        "allowed_tools": ["lookup_order"],
        "cost_so_far": 0.01,
        "max_cost": 1.0,
    }
    assert engine.allow(shared_input) is True


def test_cedar_custom_policy_text_overrides_the_default():
    # an unconditional permit — proves the engine actually evaluates the
    # `policy` text passed in, not just the built-in DEFAULT_CEDAR_POLICY,
    # by allowing a tool the default policy would have denied
    allow_all_policy = "permit (principal, action, resource);"
    engine = CedarPolicyEngine(policy=allow_all_policy)
    assert engine.allow({
        "identity_id": "support-agent-1",
        "tool": "delete_account",
        "allowed_tools": [],
        "cost_so_far": 999.0,
        "max_cost": 1.0,
    })

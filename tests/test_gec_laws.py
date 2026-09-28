"""Property-based checks of the Governed Execution Contract's algebra.

Proposition 1 (attenuation): policy intersection is a meet — commutative,
associative, idempotent — and a delegated scope never exceeds any ancestor.
Proposition 2 (deny precedence): for incomparable rules, adding a deny can
only make the resolved permission more restrictive.
"""
import pytest

hypothesis = pytest.importorskip('hypothesis')
from hypothesis import given, settings, strategies as st  # noqa: E402

from agent_foundry.contracts import AutonomyLevel, Policy  # noqa: E402
from agent_foundry.control_plane import TierRule, WorkspacePolicy, current_delegation, delegation  # noqa: E402
from agent_foundry.execution_scope import intersect_policies  # noqa: E402

TOOLS = ['a', 'b', 'c', 'd', 'e']
tool_sets = st.frozensets(st.sampled_from(TOOLS))
policies = st.builds(
    Policy, allowed_tools=tool_sets, requires_approval=tool_sets,
    max_cost_usd_per_thread=st.floats(0, 100, allow_nan=False), max_steps_per_thread=st.integers(0, 100),
    autonomy=st.sampled_from(list(AutonomyLevel)))


def key(p):
    return (p.allowed_tools, p.requires_approval, p.max_cost_usd_per_thread, p.max_steps_per_thread, p.autonomy)


def leq(child, parent):
    """child is at most as permissive as parent."""
    return (child.allowed_tools <= parent.allowed_tools and child.requires_approval >= parent.requires_approval
            and child.max_cost_usd_per_thread <= parent.max_cost_usd_per_thread
            and child.max_steps_per_thread <= parent.max_steps_per_thread and child.autonomy <= parent.autonomy)


@settings(max_examples=400, deadline=None)
@given(policies, policies, policies)
def test_intersection_is_a_meet(x, y, z):
    assert key(intersect_policies(x, y)) == key(intersect_policies(y, x))
    assert key(intersect_policies(intersect_policies(x, y), z)) == key(intersect_policies(x, intersect_policies(y, z)))
    assert key(intersect_policies(x, x)) == key(x)
    m = intersect_policies(x, y)
    assert leq(m, x) and leq(m, y)


@settings(max_examples=300, deadline=None)
@given(st.lists(st.one_of(st.none(), st.frozensets(st.sampled_from(TOOLS))), min_size=1, max_size=6))
def test_delegation_chain_never_regains_a_dropped_scope(hops):
    def descend(i, held):
        if i == len(hops):
            return
        with delegation(f'h{i}', hops[i]):
            chain, scopes = current_delegation()
            assert chain == tuple(f'h{j}' for j in range(i + 1))
            if held is not None:
                assert scopes is not None and scopes <= held
            if hops[i] is not None:
                assert scopes <= hops[i]
            descend(i + 1, scopes)
    descend(0, None)
    assert current_delegation() == ((), None)


perms = st.sampled_from(['allow', 'flag', 'deny'])
maybe_rule = st.one_of(st.none(), st.builds(TierRule, perms, st.one_of(st.none(), st.integers(1, 100))))
ORDER = {'allow': 0, 'flag': 1, 'deny': 2}


@settings(max_examples=400, deadline=None)
@given(maybe_rule, maybe_rule, maybe_rule)
def test_adding_a_deny_never_loosens_resolution(default, user_rule, tool_rule):
    def build(u, t):
        return WorkspacePolicy(defaults={'i': default} if default else {},
                               users={'u': {'i': u}} if u else {}, tools={'x': {'i': t}} if t else {})
    before = build(user_rule, tool_rule).resolve('u', 'x', 'i')
    after = build(user_rule, TierRule('deny')).resolve('u', 'x', 'i')
    assert after is not None and after.permission == 'deny'
    if before is not None:
        assert ORDER[after.permission] >= ORDER[before.permission]


@settings(max_examples=300, deadline=None)
@given(maybe_rule, maybe_rule, maybe_rule)
def test_resolved_rate_limit_is_never_looser_than_any_applicable_rule(default, user_rule, tool_rule):
    policy = WorkspacePolicy(defaults={'i': default} if default else {},
                             users={'u': {'i': user_rule}} if user_rule else {},
                             tools={'x': {'i': tool_rule}} if tool_rule else {})
    resolved = policy.resolve('u', 'x', 'i')
    limits = [r.rate_limit_per_minute for r in (default, user_rule, tool_rule) if r and r.rate_limit_per_minute]
    if resolved is not None and limits:
        assert resolved.rate_limit_per_minute == min(limits)

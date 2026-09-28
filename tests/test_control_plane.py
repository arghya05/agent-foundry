"""Multi-tenant control plane: deny-first admission in front of the gateway."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_foundry import Agent
from agent_foundry.contracts import AutonomyLevel, Policy, ToolSpec
from agent_foundry.control_plane import (ControlPlane, InMemoryPolicyStore, Principal, Tenant, TierRule,
                                         WorkspacePolicy, current_delegation, delegation)
from agent_foundry.governed_tools import GovernedToolGateway
from agent_foundry.llm_gateway import LLMGateway
from conftest import ScriptedProvider


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def build(policy=None, *, fail_modes=None, principals=None, tenant='t1'):
    effects = []
    def effect(name):
        def fn(**kwargs):
            effects.append((name, kwargs))
            return {'ok': True}
        return fn
    names = {'read': frozenset({'r'}), 'write': frozenset({'w'}), 'admin': frozenset({'a'})}
    specs = [ToolSpec(n, n, {'type': 'object', 'additionalProperties': True}, effect(n), scopes=s, cacheable=False)
             for n, s in names.items()]
    agent = Agent('cp', 'x', tools=specs, llm=LLMGateway(ScriptedProvider([])),
                  policy=Policy(allowed_tools=frozenset(names), autonomy=AutonomyLevel.L4_POLICY_BOUND,
                                max_steps_per_thread=10_000))
    clock = Clock()
    store = InMemoryPolicyStore(clock=clock)
    store.put(Tenant(tenant, principals or {'alice': Principal('alice', 'a@x', frozenset({'r', 'w'})),
                                            'bob': Principal('bob', 'b@x', frozenset({'r', 'w', 'a'}))},
                     policy or WorkspacePolicy(defaults={'interactive': TierRule('allow'),
                                                         'subagent': TierRule('allow', 5)})))
    plane = ControlPlane(GovernedToolGateway(agent.config), store, required_scopes=names,
                         fail_modes=fail_modes, clock=clock)
    return plane, store, clock, effects


def decisions(plane):
    return [e for e in plane.audit.entries if e['action'] == 'governance_decision']


def test_allowed_call_executes_once_and_is_audited_with_identity():
    plane, _, _, effects = build()
    d = plane.invoke('read', {'x': 1}, tenant_id='t1', user_id='alice', trace_id='tr')
    assert d.allowed and d.result.ok and effects == [('read', {'x': 1})]
    [entry] = decisions(plane)
    assert (entry['identity'], entry['tenant'], entry['decision'], entry['trace_id'], entry['actor_email']) == \
        ('alice', 't1', 'allow', 'tr', 'a@x')


@pytest.mark.parametrize('uid,tenant,tool,reason', [
    ('', 't1', 'read', 'unauthenticated'),
    ('alice', 'other', 'read', 'unknown tenant'),
    ('mallory', 't1', 'read', 'not a member'),
    ('alice', 't1', 'missing_tool', 'unknown tool'),
    ('alice', 't1', 'admin', 'missing scopes'),
])
def test_denials_do_not_execute_and_record_a_reason(uid, tenant, tool, reason):
    plane, _, _, effects = build()
    d = plane.invoke(tool, {}, tenant_id=tenant, user_id=uid)
    assert not d.allowed and reason in d.reason and effects == []
    assert decisions(plane)[-1]['decision'] == 'deny'


def test_undeclared_tier_fails_closed():
    plane, _, _, effects = build()
    assert not plane.invoke('read', {}, tenant_id='t1', user_id='alice', tier='background').allowed
    assert effects == []


def test_most_specific_rule_wins_and_incomparable_rules_deny_override():
    policy = WorkspacePolicy(defaults={'interactive': TierRule('allow')},
                             users={'alice': {'interactive': TierRule('allow')}},
                             tools={'write': {'interactive': TierRule('deny')}},
                             user_tools={'bob': {'write': {'interactive': TierRule('allow')}}})
    assert policy.resolve('alice', 'write', 'interactive').permission == 'deny'
    assert policy.resolve('bob', 'write', 'interactive').permission == 'allow'
    assert policy.resolve('alice', 'read', 'interactive').permission == 'allow'
    assert policy.resolve('alice', 'read', 'api') is None


def test_rate_limit_is_the_tightest_applicable_limit():
    policy = WorkspacePolicy(defaults={'subagent': TierRule('allow', 10)},
                             tools={'read': {'subagent': TierRule('allow', 3)}})
    assert policy.resolve('x', 'read', 'subagent').rate_limit_per_minute == 3
    assert policy.resolve('x', 'write', 'subagent').rate_limit_per_minute == 10


def test_revocation_applies_to_the_next_call():
    plane, store, _, _ = build()
    assert plane.invoke('write', {}, tenant_id='t1', user_id='alice').allowed
    store.update('t1', lambda p: p.users.setdefault('alice', {}).__setitem__('interactive', TierRule('deny')))
    assert not plane.invoke('write', {}, tenant_id='t1', user_id='alice').allowed


def test_concurrent_subagents_share_one_principal_bucket_and_denials_do_not_count():
    plane, _, clock, effects = build()
    assert not plane.invoke('admin', {}, tenant_id='t1', user_id='alice', tier='subagent').allowed
    with ThreadPoolExecutor(8) as pool:
        outcomes = list(pool.map(lambda i: plane.invoke('read', {}, tenant_id='t1', user_id='alice',
                                                        tier='subagent', agent_name=f'w{i}').allowed, range(40)))
    assert sum(outcomes) == 5 and len(effects) == 5
    assert plane.invoke('read', {}, tenant_id='t1', user_id='bob', tier='subagent').allowed
    clock.now += 61
    assert plane.invoke('read', {}, tenant_id='t1', user_id='alice', tier='subagent').allowed


@pytest.mark.parametrize('mode,allowed', [('fail_closed', False), ('fail_open', True)])
def test_declared_fail_mode_applies_before_any_successful_read(mode, allowed):
    plane, store, clock, effects = build(fail_modes={'t1': mode})
    store.outage(30)
    d = plane.invoke('read', {}, tenant_id='t1', user_id='alice')
    assert d.allowed is allowed and not d.policy_source_reachable
    assert len(effects) == int(allowed)
    assert decisions(plane)[-1]['policy_source_reachable'] is False
    clock.now += 31
    assert plane.invoke('read', {}, tenant_id='t1', user_id='alice').allowed and plane.policy_source_reachable


def test_undeclared_fail_mode_fails_closed():
    plane, store, _, _ = build()
    store.outage(30)
    assert not plane.invoke('read', {}, tenant_id='t1', user_id='alice').allowed


def test_delegation_narrows_and_records_chain():
    plane, _, _, effects = build()
    with delegation('orchestrator'):
        with delegation('worker', ['r']):
            assert current_delegation() == (('orchestrator', 'worker'), frozenset({'r'}))
            assert plane.invoke('read', {}, tenant_id='t1', user_id='bob', tier='subagent').allowed
            assert not plane.invoke('admin', {}, tenant_id='t1', user_id='bob', tier='subagent').allowed
            with delegation('grandchild', ['r', 'a']):
                assert current_delegation()[1] == frozenset({'r'})
    assert current_delegation() == ((), None)
    chains = [e['delegation_chain'] for e in decisions(plane)]
    assert chains == [['orchestrator', 'worker'], ['orchestrator', 'worker']]


def test_delegation_cannot_exceed_the_principal():
    plane, _, _, _ = build()
    with delegation('orchestrator'), delegation('worker', ['a']):
        assert not plane.invoke('admin', {}, tenant_id='t1', user_id='alice', tier='subagent').allowed


def test_same_uid_in_two_tenants_uses_the_calling_tenant():
    plane, store, _, _ = build()
    store.put(Tenant('t2', {'alice': Principal('alice', 'a@t2', frozenset({'r', 'a'}))},
                     WorkspacePolicy(defaults={'interactive': TierRule('allow')})))
    assert not plane.invoke('admin', {}, tenant_id='t1', user_id='alice').allowed
    assert plane.invoke('admin', {}, tenant_id='t2', user_id='alice').allowed


def test_gateway_pdp_remains_a_second_line_of_defense():
    plane, _, _, effects = build()
    plane.required_scopes = {}
    assert not plane.invoke('admin', {}, tenant_id='t1', user_id='alice').allowed
    assert effects == [] and 'gateway' in decisions(plane)[-1]['reason']


def test_flag_admits_and_marks():
    plane, _, _, effects = build(WorkspacePolicy(defaults={'interactive': TierRule('flag')}))
    d = plane.invoke('read', {}, tenant_id='t1', user_id='alice')
    assert d.allowed and d.decision == 'flag' and len(effects) == 1


def test_prompt_cache_is_partitioned_by_request_principal():
    from agent_foundry.contracts import LLMResponse
    from agent_foundry.execution_scope import request_scope
    from agent_foundry.llm_gateway import PromptCache
    cache = PromptCache()
    msgs = [{'role': 'user', 'content': 'same prompt'}]
    reply = LLMResponse('tenant-a answer', 'm', 1, 1, 0.0)
    ident = lambda t, u: {'request_identity': {'id': u, 'tenant_id': t, 'roles': ()}}  # noqa: E731
    with request_scope(ident('a', 'alice'), thread_id='x'):
        cache.set('m', msgs, reply)
        assert cache.get('m', msgs) is reply
    with request_scope(ident('b', 'alice'), thread_id='y'):
        assert cache.get('m', msgs) is None
    with request_scope(ident('a', 'bob'), thread_id='z'):
        assert cache.get('m', msgs) is None

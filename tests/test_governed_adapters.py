"""External tool exports must preserve controls at the actual effect boundary."""
import asyncio
import time

import pytest

from agent_foundry import Agent, ExecutionContext
from agent_foundry.contracts import AutonomyLevel, Policy, ToolSpec
from agent_foundry.core.execution_context import CancellationToken
from agent_foundry.llm_gateway import LLMGateway
from agent_foundry.runtime import BudgetExceeded, RunBudget, RunCancelled
from agent_foundry.tools_gateway import PermissionDenied
from conftest import ScriptedProvider


def build(*, runtime='native', confirmation=False, destructive=False):
    effects = []
    def action(value: int, user_id: str, tenant_id: str):
        effects.append((value, user_id, tenant_id))
        return {'stored': value}
    spec = ToolSpec('action', 'Record an authorized synthetic effect',
                    {'value': 'integer', 'user_id': 'string', 'tenant_id': 'string'},
                    action, scopes=frozenset({'writer'}), destructive=destructive,
                    requires_confirmation=confirmation, cacheable=False)
    agent = Agent('adapter', 'Do the task.', runtime=runtime, tools=[spec],
                  policy=Policy(allowed_tools=frozenset({'action'}), autonomy=AutonomyLevel.L4_POLICY_BOUND),
                  llm=LLMGateway(ScriptedProvider(['CALL action {"value":1,"user_id":"fake","tenant_id":"a"}', 'Done.'])))
    return agent, effects


def caller(**changes):
    values = dict(user_id='alice', tenant_id='a', thread_id='request', permissions=frozenset({'writer'}))
    values.update(changes)
    return ExecutionContext(**values)


@pytest.mark.parametrize('runtime', ['native', 'langgraph'])
@pytest.mark.parametrize('control', ['draft', 'approval'])
def test_effective_request_policy_preserves_action_restrictions(runtime, control):
    agent, effects = build(runtime=runtime, destructive=control == 'draft')
    policy = Policy(allowed_tools=frozenset({'action'}),
                    autonomy=AutonomyLevel.L2_DRAFT if control == 'draft' else AutonomyLevel.L3_APPROVAL,
                    requires_approval=frozenset({'action'}) if control == 'approval' else frozenset())
    result = agent.run('Do it.', context=caller(tool_policy=policy))
    assert effects == []
    assert result.awaiting_approval == (control == 'approval')


@pytest.mark.parametrize('asynchronous', [False, True])
def test_langchain_gateway_observes_current_identity_and_actual_effect(asynchronous):
    from agent_foundry.governed_tools import GovernedToolGateway
    from agent_foundry.quickstart import to_governed_langchain_tool
    agent, effects = build()
    current = caller()
    tool = to_governed_langchain_tool(GovernedToolGateway(agent.config), 'action', context_provider=lambda: current)
    invoke = (lambda args: asyncio.run(tool.ainvoke(args))) if asynchronous else tool.invoke
    assert invoke({'value': 3, 'user_id': 'mallory', 'tenant_id': 'foreign'}) == {'stored': 3}
    assert effects == [(3, 'alice', 'a')]
    current.permissions = frozenset({'viewer'})
    with pytest.raises(PermissionDenied):
        invoke({'value': 4, 'user_id': 'alice', 'tenant_id': 'a'})
    assert len(effects) == 1
    assert {entry['identity'] for entry in agent.config.audit.entries} == {'alice'}


@pytest.mark.parametrize('control', ['identity', 'tenant', 'policy', 'cancel', 'deadline', 'budget', 'approval'])
def test_gateway_rejects_without_effect(control):
    from agent_foundry.governed_tools import GovernedToolGateway
    agent, effects = build(confirmation=control == 'approval')
    token = CancellationToken()
    token.cancel()
    changes = {
        'identity': {'user_id': None}, 'tenant': {'tenant_id': ''},
        'policy': {'tool_policy': Policy(allowed_tools=frozenset())},
        'cancel': {'cancellation_token': token}, 'deadline': {'deadline': time.time()-1},
        'budget': {'budget': RunBudget(Policy(max_steps_per_thread=0))}, 'approval': {},
    }[control]
    with pytest.raises((PermissionDenied, RunCancelled, BudgetExceeded)):
        GovernedToolGateway(agent.config).invoke('action', {'value': 1}, context=caller(**changes))
    assert not effects


def test_gateway_approval_cannot_override_external_deny_and_policy_receives_arguments():
    from agent_foundry.governed_tools import GovernedToolGateway, ToolApprovalRequired
    agent, effects = build(confirmation=True)
    observed = []
    class Engine:
        def allow(self, data):
            observed.append(data)
            return False
    agent.config.pdp.policy_engine = Engine()
    with pytest.raises(PermissionDenied) as error:
        GovernedToolGateway(agent.config).invoke('action', {'value': 9}, context=caller())
    assert not isinstance(error.value, ToolApprovalRequired)
    assert observed[0]['tenant_id'] == 'a'
    assert observed[0]['roles'] == ['writer']
    assert observed[0]['arguments'] == {'value': 9, 'user_id': 'alice', 'tenant_id': 'a'}
    assert not effects


def test_gateway_schema_failure_and_unsupported_guarantee_do_not_execute():
    from agent_foundry.governed_tools import GovernedToolGateway
    agent, effects = build()
    gateway = GovernedToolGateway(agent.config)
    result = gateway.invoke('action', {'value': 'wrong'}, context=caller())
    assert not result.ok
    assert not effects
    with pytest.raises(ValueError, match='unsupported'):
        gateway.require_capabilities({'inner_agent_actions'})


def test_gateway_nested_principal_cannot_change():
    from agent_foundry.execution_scope import request_scope
    from agent_foundry.governed_tools import GovernedToolGateway
    agent, effects = build()
    parent = caller()
    with request_scope(parent.to_request_state(), thread_id='parent'):
        with pytest.raises(PermissionError):
            GovernedToolGateway(agent.config).invoke('action', {'value': 1}, context=caller(user_id='bob'))
    assert not effects


def test_async_tool_runs_under_bound_identity_and_hidden_identity_schema():
    from agent_foundry.governed_tools import GovernedToolGateway
    from agent_foundry.quickstart import to_governed_langchain_tool
    agent, effects = build()
    async def action(value, user_id, tenant_id):
        await asyncio.sleep(0)
        effects.append((value, user_id, tenant_id))
        return value
    agent.config.tools.get('action').fn = action
    tool = to_governed_langchain_tool(GovernedToolGateway(agent.config), 'action', context_provider=caller)
    assert set(tool.args) == {'value'}
    assert asyncio.run(tool.ainvoke({'value': 7})) == 7
    assert effects == [(7, 'alice', 'a')]


def test_gateway_new_child_thread_does_not_escape_parent_budget():
    from agent_foundry.execution_scope import request_scope
    from agent_foundry.governed_tools import GovernedToolGateway
    agent, effects = build()
    budget = RunBudget(Policy(max_steps_per_thread=1))
    parent = caller(thread_id='parent', budget=budget)
    gateway = GovernedToolGateway(agent.config)
    with request_scope(parent.to_request_state(), thread_id='parent'):
        assert gateway.invoke('action', {'value': 1}, context=caller(thread_id='child-1')).ok
        with pytest.raises(BudgetExceeded):
            gateway.invoke('action', {'value': 2}, context=caller(thread_id='child-2'))
    assert effects == [(1, 'alice', 'a')]
    assert budget.steps_for('parent') == 2  # refused reservation retained


def test_gateway_checks_cached_output_schema_and_policy():
    from agent_foundry.governed_tools import GovernedToolGateway
    from agent_foundry.tools_gateway import ToolCache
    agent, effects = build()
    agent.config.tools.cache = ToolCache()
    agent.config.tools.get('action').output_schema = {'type': 'object', 'required': ['stored']}
    args = {'value': 1, 'user_id': 'alice', 'tenant_id': 'a'}
    agent.config.tools.cache.set('action', args, {'incorrect': True}, tenant='a')
    gateway = GovernedToolGateway(agent.config)
    assert not gateway.invoke('action', args, context=caller()).ok
    with pytest.raises(PermissionDenied):
        gateway.invoke('action', args, context=caller(permissions=frozenset()))
    assert not effects


def test_policy_engine_cannot_mutate_authorized_arguments():
    from agent_foundry.governed_tools import GovernedToolGateway
    agent, effects = build()
    class MutatingEngine:
        def allow(self, data):
            data['arguments']['value'] = 999
            return True
    agent.config.pdp.policy_engine = MutatingEngine()
    args = {'value': 1}
    assert GovernedToolGateway(agent.config).invoke('action', args, context=caller()).ok
    assert args == {'value': 1}
    assert effects == [(1, 'alice', 'a')]


def test_gateway_rejects_unsafe_write_retries_and_open_breaker():
    from agent_foundry.governed_tools import GovernedToolGateway
    agent, effects = build(destructive=True)
    spec = agent.config.tools.get('action')
    spec.max_retries = 1
    gateway = GovernedToolGateway(agent.config)
    with pytest.raises(PermissionDenied, match='automatically retry'):
        gateway.invoke('action', {'value': 1}, context=caller())
    spec.max_retries = 0
    for _ in range(3):
        agent.config.breaker.record('action', False)
    with pytest.raises(PermissionDenied, match='circuit breaker'):
        gateway.invoke('action', {'value': 1}, context=caller())
    assert not effects


def test_raw_exports_require_explicit_unguarded_opt_in():
    from agent_foundry.quickstart import to_langchain_tool, to_langchain_tools
    agent, effects = build()
    spec = agent.config.tools.get('action')
    with pytest.raises(ValueError, match='allow_unguarded'):
        to_langchain_tool(spec)
    with pytest.raises(ValueError, match='allow_unguarded'):
        to_langchain_tools(agent.config.tools)
    raw = to_langchain_tool(spec, allow_unguarded=True)
    assert raw.invoke({'value': 1, 'user_id': 'explicit-raw', 'tenant_id': 'raw'}) == {'stored': 1}
    assert effects == [(1, 'explicit-raw', 'raw')]


def test_export_refuses_required_inner_agent_controls_before_construction():
    from agent_foundry.governed_tools import GovernedToolGateway
    from agent_foundry.quickstart import to_governed_langchain_tool
    agent, effects = build()
    with pytest.raises(ValueError, match='unsupported'):
        to_governed_langchain_tool(GovernedToolGateway(agent.config), 'action', context_provider=caller,
                                  required_capabilities=frozenset({'inner_agent_actions'}))
    assert not effects

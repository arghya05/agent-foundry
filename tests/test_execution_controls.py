"""Public execution controls must govern real runtimes and delegated work."""
import asyncio
import time

import pytest

from agent_foundry import Agent, ExecutionContext, Workflow
from agent_foundry.contracts import Identity, Policy, ToolSpec
from agent_foundry.core.execution_context import CancellationToken
from agent_foundry.core.state_store import MemoryStateStore
from agent_foundry.llm_gateway import LLMGateway
from agent_foundry.runtime import BudgetExceeded, RunBudget, RunCancelled
from conftest import ScriptedProvider


def invoke(agent, entry, context):
    if entry == 'run':
        return agent.run('Do the action.', context=context)
    if entry == 'stream':
        return list(agent.stream('Do the action.', context=context))
    if entry == 'arun':
        return asyncio.run(agent.arun('Do the action.', context=context))
    async def collect():
        return [part async for part in agent.astream('Do the action.', context=context)]
    return asyncio.run(collect())


def action_agent(runtime, *, confirmation=False, responses=None, state_store=None):
    effects = []
    provider = ScriptedProvider(responses or ['CALL action {}', 'Done.'])
    agent = Agent('controls', 'Do the task.', runtime=runtime,
                  tools=[ToolSpec('action', 'Record an effect', {}, lambda: effects.append('effect'),
                                  requires_confirmation=confirmation)],
                  llm=LLMGateway(provider, routes={'default': ['test']}), state_store=state_store)
    return agent, provider, effects


@pytest.mark.parametrize('runtime', ['native', 'langgraph'])
@pytest.mark.parametrize('entry', ['run', 'stream', 'arun', 'astream'])
@pytest.mark.parametrize('control', ['cancel', 'deadline', 'budget', 'models'])
def test_controls_deny_before_any_model_or_tool(runtime, entry, control):
    agent, provider, effects = action_agent(runtime)
    token = CancellationToken()
    token.cancel()
    kwargs = {'cancel': {'cancellation_token': token}, 'deadline': {'deadline': time.time()-10},
              'budget': {'budget': RunBudget(Policy(max_steps_per_thread=0))},
              'models': {'model_policy': {'allowed_models': []}}}[control]
    with pytest.raises((RunCancelled, BudgetExceeded, PermissionError)):
        invoke(agent, entry, ExecutionContext(**kwargs))
    assert not provider.calls
    assert not effects


@pytest.mark.parametrize('runtime', ['native', 'langgraph'])
def test_request_tool_policy_blocks_effects_and_cannot_widen_config(runtime):
    agent, provider, effects = action_agent(runtime)
    result = agent.run('Do the action.', context=ExecutionContext(tool_policy=Policy(allowed_tools=frozenset())))
    assert not effects
    assert not provider.calls[0]['tools']
    assert any(m['role'] == 'tool' for m in result.messages)


@pytest.mark.parametrize('runtime', ['native', 'langgraph'])
def test_cancellation_after_model_response_prevents_tool_execution(runtime):
    token = CancellationToken()
    def respond(messages, model):
        token.cancel()
        return 'CALL action {}'
    agent, provider, effects = action_agent(runtime, responses=[respond, 'Done.'])
    with pytest.raises(RunCancelled):
        agent.run('Do the action.', context=ExecutionContext(cancellation_token=token))
    assert len(provider.calls) == 1
    assert not effects


@pytest.mark.parametrize('runtime', ['native', 'langgraph'])
def test_resume_rechecks_current_caller_and_cancel_signal(runtime):
    agent, provider, effects = action_agent(runtime, confirmation=True)
    assert agent.run('Do the action.', context=ExecutionContext(thread_id='approval')).awaiting_approval
    token = CancellationToken()
    token.cancel()
    with pytest.raises(RunCancelled):
        agent.resume(approved=True, context=ExecutionContext(thread_id='approval', cancellation_token=token))
    assert not effects
    assert len(provider.calls) == 1


@pytest.mark.parametrize('runtime', ['native', 'langgraph'])
def test_supervisor_cannot_borrow_worker_admin_identity(runtime):
    effects = []
    worker = Agent('worker', 'Do the task.', runtime=runtime,
                   identity=Identity('service', 'tenant', roles=('admin',)),
                   tools=[ToolSpec('admin_action', 'Admin only', {}, lambda: effects.append('effect'), scopes=frozenset({'admin'}))],
                   llm=LLMGateway(ScriptedProvider(['CALL admin_action {}', 'Done.'])))
    router = ScriptedProvider(['ROUTE worker'])
    workflow = Workflow.supervisor(prompt='Route.', agents={'worker': worker}, runtime=runtime, llm=LLMGateway(router))
    workflow.run('Do the action.', context=ExecutionContext(user_id='viewer', tenant_id='tenant', permissions=frozenset({'viewer'})))
    assert not effects


def test_live_control_handles_are_not_persisted_in_native_state():
    store = MemoryStateStore()
    agent, provider, effects = action_agent('native', state_store=store)
    budget = RunBudget(Policy(max_steps_per_thread=5))
    context = ExecutionContext(thread_id='persisted', budget=budget, cancellation_token=CancellationToken())
    agent.run('Do the action.', context=context)
    assert effects == ['effect']
    assert budget.steps_for('persisted') == 2
    assert 'request_budget' not in store.load('persisted')
    assert 'request_cancellation_token' not in store.load('persisted')


@pytest.mark.parametrize('runtime', ['native', 'langgraph'])
def test_supervisor_router_and_worker_share_the_request_budget(runtime):
    worker, provider, effects = action_agent(runtime)
    router = ScriptedProvider(['ROUTE worker'])
    workflow = Workflow.supervisor(prompt='Route.', agents={'worker': worker}, runtime=runtime, llm=LLMGateway(router))
    budget = RunBudget(Policy(max_steps_per_thread=1))
    with pytest.raises(BudgetExceeded):
        workflow.run('Do the action.', context=ExecutionContext(thread_id='root', budget=budget))
    assert len(router.calls) == 1
    assert not provider.calls
    assert not effects
    assert budget.steps_for('root') == 2
    assert budget.steps_for('root-worker') == 0


@pytest.mark.parametrize('runtime', ['native', 'langgraph'])
def test_parallel_fanout_preserves_caller_permissions(runtime):
    from agent_foundry.contracts import LLMResponse
    effects = []
    class Provider:
        def complete(self, messages, *, model, **kwargs):
            text = 'Done.' if messages[-1]['role'] == 'tool' else 'CALL admin_action {}'
            return LLMResponse(text, model, 1, 1, 0)
    worker = Agent('worker', 'Do the task.', runtime=runtime,
                   identity=Identity('service', 'tenant', roles=('admin',)),
                   tools=[ToolSpec('admin_action', 'Admin only', {}, lambda: effects.append('effect'), scopes=frozenset({'admin'}))],
                   llm=LLMGateway(Provider()))
    workflow = Workflow.fanout(agent=worker, runtime=runtime)
    workflow.run(['one', 'two'], context=ExecutionContext(user_id='viewer', tenant_id='tenant', permissions=frozenset({'viewer'})))
    assert not effects


@pytest.mark.parametrize('runtime', ['native', 'langgraph'])
def test_tool_timeout_thread_preserves_context_for_nested_agent(runtime):
    child, provider, effects = action_agent(runtime)
    # The parent may call the child tool, but the child may not call action.
    outer_provider = ScriptedProvider(['CALL child {"query": "do it"}', 'Done.'])
    child_tool = child.as_tool(name='child', description='Delegate')
    child_tool.timeout_s = 5
    parent = Agent('parent', 'Do the task.', runtime=runtime, tools=[child_tool], llm=LLMGateway(outer_provider))
    parent.run('Do it.', context=ExecutionContext(tool_policy=Policy(allowed_tools=frozenset({'child'}))))
    assert not effects
    assert provider.calls
    assert not provider.calls[0]['tools']


@pytest.mark.parametrize('runtime', ['native', 'langgraph'])
def test_live_budget_and_token_do_not_enter_checkpoints(runtime):
    agent, _, effects = action_agent(runtime)
    budget = RunBudget(Policy(max_steps_per_thread=5))
    token = CancellationToken()
    agent.run('Do it.', context=ExecutionContext(thread_id='scope', budget=budget, cancellation_token=token))
    assert effects == ['effect']
    assert budget.steps_for('scope') == 2
    state = agent.graph.get_state({'configurable': {'thread_id': 'scope'}}).values
    assert 'request_budget' not in state
    assert 'request_cancellation_token' not in state


@pytest.mark.parametrize('runtime', ['native', 'langgraph'])
def test_stream_scope_does_not_leak_into_the_consuming_thread(runtime):
    from agent_foundry.execution_scope import request_value
    agent, _, _ = action_agent(runtime)
    stream = agent.stream('Do it.', context=ExecutionContext(model_policy={'allowed_models': ['test']}))
    next(stream)
    assert request_value({}, 'request_model_policy') is None
    stream.close()
    assert request_value({}, 'request_model_policy') is None


@pytest.mark.parametrize('runtime', ['native', 'langgraph'])
def test_run_cancel_stops_new_work_and_retains_cancelled_status(runtime):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from agent_foundry.core.run import Run, RunStatus
    started, release = Event(), Event()
    def response(messages, model):
        started.set()
        assert release.wait(5)
        return 'CALL action {}'
    agent, _, effects = action_agent(runtime, responses=[response, 'Done.'])
    run = Run(agent=agent, context=ExecutionContext())
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(run.run, 'Do it.')
        try:
            assert started.wait(5)
            run.cancel()
        finally:
            release.set()
        with pytest.raises(RunCancelled):
            future.result(timeout=5)
    assert run.status == RunStatus.CANCELLED
    assert not effects


@pytest.mark.parametrize('runtime', ['native', 'langgraph'])
def test_model_allowlist_cannot_fall_back_to_disallowed_route(runtime):
    agent, provider, _ = action_agent(runtime)
    with pytest.raises(PermissionError):
        agent.run('Do it.', context=ExecutionContext(model_policy={'allowed_models': ['other']}))
    assert not provider.calls
    # Failure in one request must not contaminate the next request.
    agent.run('Do it.', context=ExecutionContext())
    assert provider.calls


@pytest.mark.parametrize('runtime', ['native', 'langgraph'])
def test_resume_cannot_use_roles_revoked_since_approval(runtime):
    effects = []
    agent = Agent('worker', 'Do it.', runtime=runtime,
                  tools=[ToolSpec('admin_action', 'Admin only', {}, lambda: effects.append('effect'),
                                  scopes=frozenset({'admin'}), requires_confirmation=True)],
                  llm=LLMGateway(ScriptedProvider(['CALL admin_action {}', 'Done.'])))
    initial = ExecutionContext(thread_id='revoked', user_id='user', tenant_id='tenant', permissions=frozenset({'admin'}))
    assert agent.run('Do it.', context=initial).awaiting_approval
    agent.resume(approved=True, context=ExecutionContext(thread_id='revoked', user_id='user', tenant_id='tenant', permissions=frozenset({'viewer'})))
    assert not effects


def test_nested_public_context_cannot_replace_principal_or_widen_policy():
    from agent_foundry.execution_scope import request_scope, request_value
    parent = ExecutionContext(user_id='viewer', tenant_id='tenant', permissions=frozenset({'viewer'}),
                              tool_policy=Policy(allowed_tools=frozenset({'read'})))
    with request_scope(parent.to_request_state(), thread_id='root'):
        with pytest.raises(PermissionError):
            with request_scope(ExecutionContext(user_id='admin', tenant_id='tenant').to_request_state(), thread_id='child'):
                pytest.fail('principal replacement was allowed')
        child = ExecutionContext(user_id='viewer', tenant_id='tenant', permissions=frozenset({'viewer', 'admin'}),
                                  tool_policy=Policy(allowed_tools=frozenset({'read', 'write'})))
        with request_scope(child.to_request_state(), thread_id='child'):
            assert set(request_value({}, 'request_identity')['roles']) == {'viewer'}
            assert request_value({}, 'request_tool_policy').allowed_tools == frozenset({'read'})
    assert request_value({}, 'request_identity') is None


@pytest.mark.parametrize('runtime', ['native', 'langgraph'])
def test_batch_admission_propagates_model_restrictions(runtime):
    agent, provider, effects = action_agent(runtime)
    report = agent.batch([{'message': 'one'}, {'message': 'two'}], context=ExecutionContext(model_policy={'allowed_models': []}))
    assert len(report.results) == 2
    assert all(not item.ok for item in report.results)
    assert not provider.calls
    assert not effects


@pytest.mark.parametrize('runtime', ['native', 'langgraph'])
def test_checkpoint_records_effective_nested_identity_not_requested_expansion(runtime):
    from agent_foundry.execution_scope import request_scope
    agent, _, _ = action_agent(runtime)
    parent = ExecutionContext(user_id='user', tenant_id='tenant', permissions=frozenset({'viewer'}))
    child = ExecutionContext(thread_id='nested', user_id='user', tenant_id='tenant', permissions=frozenset({'viewer', 'admin'}))
    with request_scope(parent.to_request_state(), thread_id='parent'):
        agent.run('Do it.', context=child)
    state = agent.graph.get_state({'configurable': {'thread_id': 'nested'}}).values
    assert set(state['request_identity']['roles']) == {'viewer'}

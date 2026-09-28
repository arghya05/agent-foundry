"""Reproduced review failures: authorization precedence and request cache identity."""
import pytest

from agent_foundry import Agent, ExecutionContext
from agent_foundry.contracts import LLMResponse, Policy, ToolSpec
from agent_foundry.llm_gateway import LLMGateway, PromptCache
from agent_foundry.security import EgressPolicy
from conftest import ScriptedProvider


class MutablePolicyEngine:
    allowed = True

    def allow(self, input):
        return self.allowed


def governed_agent(runtime, approval_source):
    effects = []
    # A denial containing the word "approval" must remain a hard denial.
    name = "approval_tool"
    tool = ToolSpec(name, "A consequential synthetic action", {}, lambda: effects.append("executed"),
                    requires_confirmation=approval_source == "tool", egress_hosts=frozenset({"allowed.example"}))
    policy = Policy(allowed_tools=frozenset({name}), requires_approval=(
        frozenset({name}) if approval_source == "policy" else frozenset()))
    agent = Agent("review", "Test authorization.", runtime=runtime, tools=[tool], policy=policy,
                  llm=LLMGateway(ScriptedProvider([f"CALL {name} {{}}", "Done."])))
    return agent, effects


@pytest.mark.parametrize("runtime", ["native", "langgraph"])
@pytest.mark.parametrize("approval_source", ["tool", "policy"])
@pytest.mark.parametrize("denial", ["external", "egress"])
def test_hard_denials_cannot_be_overridden_by_approval(runtime, approval_source, denial):
    agent, effects = governed_agent(runtime, approval_source)
    if denial == "external":
        engine = MutablePolicyEngine()
        engine.allowed = False
        agent.config.pdp.policy_engine = engine
    else:
        agent.config.pdp.egress = EgressPolicy(allowed_hosts={"approval_tool": frozenset({"other.example"})})
    result = agent.run("Perform the action.")
    assert not result.awaiting_approval
    assert effects == []
    assert any(m["role"] == "tool" and "denied" in str(m["content"]) for m in result.messages)


@pytest.mark.parametrize("runtime", ["native", "langgraph"])
def test_policy_revoked_while_waiting_for_approval_still_blocks(runtime):
    agent, effects = governed_agent(runtime, "tool")
    engine = MutablePolicyEngine()
    agent.config.pdp.policy_engine = engine
    context = ExecutionContext(thread_id="revocation")
    assert agent.run("Perform the action.", context=context).awaiting_approval
    engine.allowed = False
    result = agent.resume(approved=True, context=context)
    assert effects == []
    assert not result.awaiting_approval


@pytest.mark.parametrize("changed", ["tools", "generation", "history"])
def test_prompt_cache_never_reuses_a_different_effective_request(changed):
    class Provider:
        calls = 0

        def complete(self, messages, *, model, **kwargs):
            self.calls += 1
            return LLMResponse(str(self.calls), model, 1, 1, 0)

    provider = Provider()
    gateway = LLMGateway(provider, routes={"default": ["test"]}, cache=PromptCache())
    first = [{"role": "user", "content": "same prompt"}]
    second = first
    options = {}
    other_options = {}
    if changed == "tools":
        options = {"tools": [{"name": "read", "parameters": {"type": "object"}}]}
        other_options = {"tools": [{"name": "write", "parameters": {"type": "object"}}]}
    elif changed == "generation":
        options, other_options = {"temperature": 0}, {"temperature": 1}
    else:
        first = [{"role": "assistant", "content": "", "tool_calls": [{"id": "a", "name": "read", "args": {}}]}]
        second = [{"role": "assistant", "content": "", "tool_calls": [{"id": "b", "name": "write", "args": {}}]}]
    one = gateway.complete(first, **options)
    two = gateway.complete(second, **other_options)
    assert one.text != two.text
    assert gateway.complete(second, **other_options) is two
    assert provider.calls == 2

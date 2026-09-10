"""AgentSpec — declarative agent construction (agent_foundry/agent_spec.py).
Every test builds a real Agent via AgentSpec/build_agent and runs it against
a scripted provider, the same "real code path, fake model" pattern the rest
of the suite uses (see conftest.ScriptedProvider)."""
from __future__ import annotations

import json

import pytest

from agent_foundry.agent_spec import AgentSpec, build_agent, resolve_tool
from agent_foundry.contracts import Identity, LLMResponse, Policy, ToolCall
from agent_foundry.llm_gateway import LLMGateway

from conftest import ScriptedProvider


def test_resolve_tool_finds_a_real_function():
    fn = resolve_tool("_spec_fixture_tools:echo")
    assert fn("hi") == "hi"


def test_resolve_tool_rejects_missing_colon():
    with pytest.raises(ValueError, match="module.path:function_name"):
        resolve_tool("_spec_fixture_tools.echo")


def test_resolve_tool_raises_on_missing_module():
    with pytest.raises(ImportError):
        resolve_tool("no_such_module_at_all:echo")


def test_resolve_tool_raises_on_missing_attribute():
    with pytest.raises(AttributeError):
        resolve_tool("_spec_fixture_tools:does_not_exist")


def test_from_dict_rejects_unknown_field():
    with pytest.raises(ValueError, match="unknown field"):
        AgentSpec.from_dict({"name": "x", "instructions": "y", "nonsense": 1})


def test_from_json_round_trip(tmp_path):
    path = tmp_path / "agent.json"
    path.write_text(json.dumps({"name": "researcher", "instructions": "Answer questions."}))
    spec = AgentSpec.from_json(path)
    assert spec.name == "researcher"
    assert spec.instructions == "Answer questions."
    assert spec.model == "default"


def test_from_yaml_round_trip(tmp_path):
    yaml = pytest.importorskip("yaml")
    path = tmp_path / "agent.yaml"
    path.write_text(yaml.safe_dump({"name": "researcher", "instructions": "Answer questions.", "tools": ["_spec_fixture_tools:echo"]}))
    spec = AgentSpec.from_yaml(path)
    assert spec.name == "researcher"
    assert spec.tools == ["_spec_fixture_tools:echo"]


def test_from_yaml_without_pyyaml_raises_clear_error(monkeypatch, tmp_path):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("no module named yaml")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    path = tmp_path / "agent.yaml"
    path.write_text("name: x\ninstructions: y\n")
    with pytest.raises(ImportError, match="agent-foundry\\[spec\\]"):
        AgentSpec.from_yaml(path)


def test_build_agent_runs_a_plain_reply():
    provider = ScriptedProvider(["hello from spec"])
    spec = AgentSpec(name="chatty", instructions="Chat.")
    agent = build_agent(spec, llm=LLMGateway(provider=provider))

    result = agent.run("hi")

    assert result.content == "hello from spec"


def test_build_agent_wires_a_tool_end_to_end():
    responses = [
        LLMResponse(text="", model="m", input_tokens=1, output_tokens=1, cost_usd=0.0,
                    tool_calls=[ToolCall(id="c1", name="echo", args={"text": "round trip"})]),
        "done",
    ]
    provider = ScriptedProvider(responses)
    spec = AgentSpec(name="tooled", instructions="Use tools.", tools=["_spec_fixture_tools:echo"],
                      policy={"allowed_tools": ["echo"]})
    agent = build_agent(spec, llm=LLMGateway(provider=provider))

    result = agent.run("call echo")

    assert result.content == "done"
    assert any(m.get("content") == "round trip" for m in result.raw["messages"] if m.get("role") == "tool")


def test_build_agent_maps_policy_dict_including_autonomy_and_frozensets():
    spec = AgentSpec(
        name="governed", instructions="Be careful.",
        policy={"allowed_tools": ["echo"], "requires_approval": ["echo"], "autonomy": "L1_RECOMMEND", "max_cost_usd_per_thread": 2.5},
    )
    agent = build_agent(spec)

    assert isinstance(agent.config.policy, Policy)
    assert agent.config.policy.allowed_tools == frozenset({"echo"})
    assert agent.config.policy.requires_approval == frozenset({"echo"})
    assert agent.config.policy.max_cost_usd_per_thread == 2.5


def test_build_agent_maps_identity_dict():
    spec = AgentSpec(name="id-test", instructions="x", identity={"id": "svc-1", "tenant_id": "acme", "roles": ["admin"]})
    agent = build_agent(spec)

    assert agent.config.identity == Identity(id="svc-1", tenant_id="acme", roles=("admin",))


def test_build_agent_enables_memory():
    spec = AgentSpec(name="rememberer", instructions="x", memory={"enabled": True})
    agent = build_agent(spec)
    assert agent.config.memory is not None


def test_build_agent_rejects_unknown_provider():
    spec = AgentSpec(name="x", instructions="y", provider="not-a-real-provider")
    with pytest.raises(ValueError, match="not-a-real-provider"):
        build_agent(spec)

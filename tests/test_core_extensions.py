"""Tests for the M2 slice of agent_foundry.core: Tool/Memory protocols,
PromptRegistry/PolicyRegistry/EvalRegistry, the capability-based ModelRouter,
the @tool decorator, and Agent.on() event triggers.
"""
from __future__ import annotations

import time

import pytest

from agent_foundry import (
    Agent, EvalRegistry, ExecutionContext, ModelCapabilities, ModelRequest,
    ModelRouter, PolicyRegistry, PromptRegistry, apply_route, tool,
)
from agent_foundry.contracts import Policy, ToolSpec
from agent_foundry.context import MemoryStore
from agent_foundry.core.protocols import Memory, Tool
from agent_foundry.eval import EvalHarness
from agent_foundry.events import InMemoryEventBus
from agent_foundry.llm_gateway import LLMGateway

from conftest import ScriptedProvider


# ---- Tool / Memory protocols -----------------------------------------------

def test_toolspec_satisfies_the_tool_protocol():
    spec = ToolSpec("lookup", "look something up", {"id": "string"}, lambda id: id)
    assert isinstance(spec, Tool)


def test_memorystore_satisfies_the_memory_protocol():
    assert isinstance(MemoryStore(), Memory)


def test_agent_accepts_a_hand_written_tool_protocol_object_not_just_toolspec():
    class HandWrittenTool:
        name = "ping"
        description = "Replies pong"
        parameters: dict = {}
        fn = staticmethod(lambda: "pong")

    provider = ScriptedProvider(["CALL ping {}", "done"])
    agent = Agent("pinger", "ping when asked", tools=[HandWrittenTool()], llm=LLMGateway(provider=provider))

    result = agent.run("ping!", context=ExecutionContext(thread_id="t-handwritten-tool"))

    assert result.content == "done"


# ---- Registries --------------------------------------------------------------

def test_prompt_registry_register_get_and_rollback():
    registry = PromptRegistry()
    v1 = registry.register("fashion_advisor", "You are a fashion advisor v1.")
    registry.register("fashion_advisor", "You are a fashion advisor v2.")

    assert registry.get("fashion_advisor") == "You are a fashion advisor v2."
    registry.rollback("fashion_advisor", version=v1)
    assert registry.get("fashion_advisor") == "You are a fashion advisor v1."
    assert len(registry.history("fashion_advisor")) == 2


def test_prompt_registry_output_is_usable_directly_as_agent_instructions():
    registry = PromptRegistry()
    registry.register("greeter", "You just say hi.")
    provider = ScriptedProvider(["hi!"])

    agent = Agent("greeter", registry.get("greeter"), llm=LLMGateway(provider=provider))

    assert agent.run("hello").content == "hi!"


def test_policy_registry_register_and_get():
    registry = PolicyRegistry()
    policy = Policy(allowed_tools=frozenset({"lookup_lead"}), max_cost_usd_per_thread=0.5)
    registry.register("retail_policy", policy)

    assert registry.get("retail_policy") is policy
    assert registry.names() == ["retail_policy"]


def test_eval_registry_register_and_get_runs_the_underlying_evaluator():
    registry = EvalRegistry()
    harness = EvalHarness()
    registry.register("groundedness", harness)

    registry.get("groundedness").record("atomic", "answer-1", "groundedness", 0.9)

    assert harness.records[0].score == 0.9


# ---- Capability-based model router --------------------------------------------

def _catalog() -> dict[str, ModelCapabilities]:
    return {
        "haiku": ModelCapabilities("haiku", capabilities=frozenset({"tool_calling"}), cost_per_1m_input_usd=0.8, latency_class="fast"),
        "sonnet": ModelCapabilities("sonnet", capabilities=frozenset({"tool_calling", "vision"}), cost_per_1m_input_usd=3.0, latency_class="standard"),
        "opus": ModelCapabilities("opus", capabilities=frozenset({"tool_calling", "vision"}), cost_per_1m_input_usd=15.0, latency_class="standard"),
    }


def test_model_router_selects_the_cheapest_model_satisfying_capabilities():
    router = ModelRouter(catalog=_catalog())
    assert router.select(ModelRequest(capabilities=frozenset({"vision"}))) == "sonnet"


def test_model_router_respects_a_cost_ceiling():
    router = ModelRouter(catalog=_catalog())
    with pytest.raises(ValueError):
        router.select(ModelRequest(capabilities=frozenset({"vision"}), max_cost_per_1m_input_usd=1.0))


def test_model_router_respects_latency_class():
    router = ModelRouter(catalog=_catalog())
    assert router.select(ModelRequest(latency="fast")) == "haiku"


def test_apply_route_sets_llm_gateway_routes_and_llm_still_completes():
    provider = ScriptedProvider(["ok"])
    llm = LLMGateway(provider=provider)
    router = ModelRouter(catalog=_catalog())

    selected = apply_route(llm, task="vision_task", request=ModelRequest(capabilities=frozenset({"vision"})), router=router)

    assert selected == "sonnet"
    assert llm.routes["vision_task"] == ["sonnet"]
    resp = llm.complete([{"role": "user", "content": "hi"}], task="vision_task")
    assert resp.text == "ok" and provider.calls[0]["model"] == "sonnet"


# ---- @tool decorator ----------------------------------------------------------

def test_tool_decorator_builds_a_callable_toolspec_with_permissions_metadata():
    @tool(name="inventory.lookup", description="Look up stock for a sku", permissions=["inventory:read"])
    def inventory_lookup(sku: str) -> str:
        return f"5 units of {sku}"

    assert isinstance(inventory_lookup, ToolSpec)
    assert inventory_lookup.name == "inventory.lookup"
    assert inventory_lookup.permissions == frozenset({"inventory:read"})
    assert inventory_lookup.fn(sku="A100") == "5 units of A100"


def test_tool_decorator_enforces_a_real_timeout():
    @tool(timeout=0.05)
    def slow() -> str:
        time.sleep(0.3)
        return "too slow"

    with pytest.raises(TimeoutError):
        slow.fn()


def test_tool_decorator_caches_within_ttl():
    calls = []

    @tool(cache_ttl=60)
    def counted(x: int) -> int:
        calls.append(x)
        return x * 2

    assert counted.fn(x=3) == 6
    assert counted.fn(x=3) == 6
    assert calls == [3]  # second call served from cache, fn body ran once


def test_agent_uses_a_decorated_tool_end_to_end():
    @tool(description="Look up a sales lead")
    def lookup_lead(lead_id: str) -> str:
        return f"lead {lead_id} is hot"

    provider = ScriptedProvider(['CALL lookup_lead {"lead_id": "L200"}', "It's a hot lead!"])
    agent = Agent("sales", "Help with leads.", tools=[lookup_lead], llm=LLMGateway(provider=provider))

    result = agent.run("any update on L200?")

    assert result.content == "It's a hot lead!"


# ---- Agent.on() event triggers -------------------------------------------------

def test_agent_on_wires_the_agents_own_graph_to_respond_to_an_event():
    provider = ScriptedProvider(["Escalating the delayed order."])
    agent = Agent("ops", "Handle operational events.", llm=LLMGateway(provider=provider))
    bus = InMemoryEventBus()
    agent.event_bus = bus

    received = []

    @agent.on("order.delayed")
    def handle_delay(event: dict) -> None:
        received.append(event)

    bus.publish("order.delayed", {"order_id": "O1"})

    assert received == [{"order_id": "O1"}]
    assert provider.calls and "O1" in provider.calls[0]["messages"][-1]["content"]


def test_agent_on_uses_the_agents_default_event_bus_when_none_is_supplied():
    provider = ScriptedProvider(["ok"])
    agent = Agent("ops2", "Handle operational events.", llm=LLMGateway(provider=provider))

    @agent.on("ping")
    def handle_ping(event: dict) -> None:
        pass

    assert isinstance(agent.event_bus, InMemoryEventBus)
    agent.event_bus.publish("ping", {})
    assert provider.calls  # the agent's own graph responded too

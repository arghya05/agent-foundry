import json

from agent_foundry.contracts import Identity, LLMResponse, Policy, ToolSpec
from agent_foundry.eval import EvalHarness
from agent_foundry.events import InMemoryEventBus, wire_event_driven
from agent_foundry.guardrails import GuardrailEngine
from agent_foundry.llm_gateway import LLMGateway
from agent_foundry.observability import Tracer
from agent_foundry.orchestration import build_agent_graph
from agent_foundry.runtime import RunBudget
from agent_foundry.tools_gateway import ToolRegistry


def test_publish_calls_subscribers_in_order():
    bus = InMemoryEventBus()
    seen = []
    bus.subscribe("orders", lambda e: seen.append(("first", e)))
    bus.subscribe("orders", lambda e: seen.append(("second", e)))
    bus.publish("orders", {"id": "A100"})
    assert seen == [("first", {"id": "A100"}), ("second", {"id": "A100"})]


def test_subscribers_on_other_topics_are_not_called():
    bus = InMemoryEventBus()
    seen = []
    bus.subscribe("orders", lambda e: seen.append(e))
    bus.publish("shipments", {"id": "B1"})
    assert seen == []


def test_history_filters_by_topic():
    bus = InMemoryEventBus()
    bus.publish("orders", {"id": "A100"})
    bus.publish("shipments", {"id": "B1"})
    assert bus.history("orders") == [("orders", {"id": "A100"})]
    assert len(bus.history()) == 2


def test_wire_event_driven_triggers_a_real_graph_on_publish():
    calls = []

    class Provider:
        def complete(self, messages, *, model, tools=None, **kw):
            calls.append(messages[-1]["content"])
            return LLMResponse(text="handled", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0)

    identity = Identity(id="t", tenant_id="acme")
    policy = Policy(allowed_tools=frozenset())
    graph = build_agent_graph(system_prompt="sys", llm=LLMGateway(provider=Provider()), tools=ToolRegistry(),
        guardrails=GuardrailEngine(policy), eval_harness=EvalHarness(), identity=identity, policy=policy,
        budget=RunBudget(policy), tracer=Tracer("events-test"))

    bus = InMemoryEventBus()
    wire_event_driven(graph=graph, bus=bus, topic="orders")
    bus.publish("orders", {"id": "A100", "status": "shipped"})

    assert len(calls) == 1
    assert json.loads(calls[0]) == {"id": "A100", "status": "shipped"}

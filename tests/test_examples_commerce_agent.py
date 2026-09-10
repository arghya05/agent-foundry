"""examples/commerce_agent — search/recommend/cross-sell plus a destructive
place_order tool behind HITL approval. Verified against a ScriptedProvider,
same convention as the rest of this suite."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_foundry.contracts import LLMResponse, ToolCall
from agent_foundry.eval_dataset import EvalDataset
from agent_foundry.llm_gateway import LLMGateway

from conftest import ScriptedProvider
from examples.commerce_agent.agent import build_agent

_DATASET_PATH = Path(__file__).resolve().parent.parent / "examples" / "commerce_agent" / "eval_dataset.json"


def _call(tool_name: str, args: dict, call_id: str = "c1"):
    def fn(messages, model):
        return LLMResponse(text="", model=model, input_tokens=1, output_tokens=1, cost_usd=0.0,
                            tool_calls=[ToolCall(id=call_id, name=tool_name, args=args)])
    return fn


def test_eval_dataset_file_is_versioned_and_loads():
    dataset = EvalDataset.load(_DATASET_PATH)
    assert dataset.name == "commerce_agent"
    assert len(dataset.to_cases()) == 3


def test_commerce_agent_passes_its_own_eval_dataset():
    dataset = EvalDataset.load(_DATASET_PATH)
    responses = [
        _call("search_products", {"query": "trail running shoes"}), "Found sku:1: Trail running shoes.",
        _call("recommend", {"product_id": "sku:1"}), "Goes well with sku:2: Moisture-wicking socks.",
        _call("place_order", {"product_id": "sku:1", "quantity": 2}),  # never gets a second response — pauses first
    ]
    provider = ScriptedProvider(responses)
    from agent_foundry import run_eval
    agent = build_agent(llm=LLMGateway(provider=provider))

    scorecard = run_eval(agent, dataset.to_cases(), dataset_name=dataset.name)

    ok, reasons = scorecard.passes({"task_success_rate_min": 1.0, "trajectory_accuracy_rate_min": 1.0})
    assert ok, reasons


def test_place_order_pauses_for_approval_and_completes_on_resume():
    from agent_foundry import ExecutionContext

    provider = ScriptedProvider([_call("place_order", {"product_id": "sku:1", "quantity": 1}), "Order placed."])
    agent = build_agent(llm=LLMGateway(provider=provider))
    context = ExecutionContext(thread_id="commerce-approval")

    paused = agent.run("Order 1 of sku:1", context=context)
    assert paused.awaiting_approval
    assert paused.raw["__interrupt__"][0].value["tool"] == "place_order"

    resumed = agent.resume(approved=True, context=context)
    assert resumed.content == "Order placed."
    tool_messages = [m for m in resumed.raw["messages"] if m["role"] == "tool"]
    assert any("ordered 1x" in m["content"] for m in tool_messages)


def test_agent_spec_yaml_still_requires_approval_for_place_order():
    """The declarative path's place_order tool loses ToolSpec.destructive
    (AgentSpec.tools only resolves plain callables — see agent_spec.py's own
    docstring), but Policy.requires_approval is an independent trigger
    (guardrails.GuardrailEngine.check_action checks it regardless of the
    destructive flag) — so the approval gate must still fire."""
    from agent_foundry import ExecutionContext
    from agent_foundry.agent_spec import AgentSpec, build_agent as build_agent_from_spec

    spec = AgentSpec.from_yaml(Path(__file__).resolve().parent.parent / "examples" / "commerce_agent" / "agent.yaml")
    provider = ScriptedProvider([_call("place_order", {"product_id": "sku:1", "quantity": 1})])
    agent = build_agent_from_spec(spec, llm=LLMGateway(provider=provider))

    paused = agent.run("Order 1 of sku:1", context=ExecutionContext(thread_id="commerce-spec-approval"))

    assert paused.awaiting_approval

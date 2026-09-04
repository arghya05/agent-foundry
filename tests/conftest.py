"""Shared fixtures for the whole suite. Fake providers here match the exact
shape used throughout Agent Foundry's own development — a `complete(messages,
*, model, tools=None, **kw)` callable — so every test exercises the real
orchestration/gateway code paths, just with a scripted model instead of a
live API call.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from agent_foundry.contracts import Identity, LLMResponse, Policy, ToolSpec
from agent_foundry.eval import EvalHarness
from agent_foundry.guardrails import GuardrailEngine
from agent_foundry.llm_gateway import LLMGateway
from agent_foundry.observability import Tracer
from agent_foundry.runtime import RunBudget
from agent_foundry.tools_gateway import ToolRegistry


class ScriptedProvider:
    """Replies with `responses` in order, one per .complete() call. If a
    response is a callable, it's invoked with (messages, model) and its
    return value used — for tests that need to branch on the conversation
    so far."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def complete(self, messages, *, model, tools=None, **kw):
        self.calls.append({"messages": messages, "model": model, "tools": tools})
        resp = self._responses.pop(0)
        if callable(resp):
            resp = resp(messages, model)
        if isinstance(resp, str):
            return LLMResponse(text=resp, model=model, input_tokens=1, output_tokens=1, cost_usd=0.0)
        return resp


@pytest.fixture
def identity():
    return Identity(id="test-agent", tenant_id="test-tenant")


@pytest.fixture
def policy():
    return Policy(allowed_tools=frozenset({"lookup_order"}), max_cost_usd_per_thread=1.0, max_steps_per_thread=10)


@pytest.fixture
def lookup_order_tool():
    return ToolSpec("lookup_order", "Look up an order by id", {"order_id": "string"}, lambda order_id: f"order {order_id} shipped")


@pytest.fixture
def tool_registry(lookup_order_tool):
    reg = ToolRegistry()
    reg.register(lookup_order_tool)
    return reg


def make_config_kwargs(*, identity, policy, tools, provider, thread_id="test-thread"):
    """The common bundle every orchestration test needs — a real graph built
    from real (if scripted) pieces, not a mock of orchestration.py itself."""
    return dict(
        llm=LLMGateway(provider=provider),
        tools=tools,
        guardrails=GuardrailEngine(policy),
        eval_harness=EvalHarness(),
        identity=identity,
        policy=policy,
        budget=RunBudget(policy),
        tracer=Tracer(thread_id),
    )

"""Reference app: a commerce agent — search/recommend/cross-sell (read-only)
plus a destructive place_order tool that always pauses for human approval.
Exercises Policy.requires_approval + ToolSpec(destructive=True), the same
HITL mechanism examples/support_agent.py's issue_refund demonstrates, and a
multi-tool trajectory an eval dataset can check step-by-step
(expected_tool_sequence/must_request_approval, see eval_dataset.json).

Two ways to run this file:
    export ANTHROPIC_API_KEY=...
    python examples/commerce_agent/agent.py                       # a real turn
    foundry eval examples/commerce_agent/agent.py \\
        examples/commerce_agent/eval_dataset.json

Also declarative — examples/commerce_agent/agent.yaml builds the same agent
via agent_foundry.AgentSpec (run with `python -m agent_foundry.cli run --spec
examples/commerce_agent/agent.yaml --message "..."` from the repo root)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent_foundry import Agent
from agent_foundry.contracts import Policy, ToolSpec
from agent_foundry.llm_gateway import LLMGateway

_CATALOG = {
    "sku:1": {"name": "Trail running shoes", "price_usd": 120.0, "related": ["sku:2", "sku:3"]},
    "sku:2": {"name": "Moisture-wicking socks", "price_usd": 15.0, "related": ["sku:1"]},
    "sku:3": {"name": "Hydration vest", "price_usd": 80.0, "related": ["sku:1"]},
}

# Module-level, not built inside build_agent() — same reasoning as
# examples/research_agent/agent.py's search_docs: AgentSpec's dotted tool
# references (see agent.yaml) need real module attributes to resolve.


def search_products(query: str) -> list[str]:
    terms = query.lower().split()
    return [f"{sku}: {p['name']} (${p['price_usd']:.2f})" for sku, p in _CATALOG.items() if any(t in p["name"].lower() for t in terms)]


def recommend(product_id: str) -> list[str]:
    product = _CATALOG.get(product_id)
    if product is None:
        return []
    return [f"{sku}: {_CATALOG[sku]['name']}" for sku in product["related"]]


def place_order(product_id: str, quantity: int) -> str:
    product = _CATALOG.get(product_id)
    if product is None:
        return f"no such product {product_id}"
    total = product["price_usd"] * quantity
    return f"ordered {quantity}x {product['name']} for ${total:.2f}"


def build_agent(*, llm: LLMGateway | None = None) -> Agent:
    tools = [
        ToolSpec("search_products", "Search the catalog by keyword.", {"query": "string"}, search_products),
        ToolSpec("recommend", "Get related/cross-sell products for a product id.", {"product_id": "string"}, recommend),
        ToolSpec("place_order", "Place an order — ALWAYS requires human approval.", {"product_id": "string", "quantity": "integer"},
                 place_order, destructive=True),
    ]
    policy = Policy(
        allowed_tools=frozenset({"search_products", "recommend", "place_order"}),
        requires_approval=frozenset({"place_order"}),
        max_cost_usd_per_thread=0.5,
        max_steps_per_thread=10,
    )

    return Agent(
        "commerce",
        "You are a shopping assistant. Use search_products to find items, recommend for cross-sell suggestions, "
        "and place_order to complete a purchase. Never claim an order was placed without actually calling place_order.",
        tools=tools,
        policy=policy,
        llm=llm,
    )


agent = build_agent()


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("set ANTHROPIC_API_KEY to run this example for real")
    result = agent.run("I need trail running shoes")
    print(result.content)

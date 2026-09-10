"""Reference app: a research/RAG agent — grounds every answer in a small
seeded knowledge base (agent_foundry.context.MemoryStore.semantic), scored
for groundedness and citation correctness via agent_foundry.kpi.

Two ways to run this file:
    export ANTHROPIC_API_KEY=...
    python examples/research_agent/agent.py                     # a real turn
    foundry eval examples/research_agent/agent.py \\
        examples/research_agent/eval_dataset.json                # scored, no real LLM call needed if you swap the provider

Also declarative — examples/research_agent/agent.yaml builds the same agent
via agent_foundry.AgentSpec (run with `python -m agent_foundry.cli run --spec
examples/research_agent/agent.yaml --message "..."` from the repo root, so
the "examples.research_agent.agent:..." tool references resolve)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent_foundry import Agent
from agent_foundry.context import MemoryStore
from agent_foundry.contracts import ToolSpec
from agent_foundry.llm_gateway import LLMGateway

# (text, source id) — the fixed reference corpus this example grounds every
# answer in. citation_correctness_kpi (see eval_dataset.json) checks a
# reply's citation markers ("[doc:1]") against exactly these ids. Kept under
# one fixed key ("kb"), not a per-session thread_id — this is a shared
# reference corpus every session queries, not per-conversation scratch
# memory, so the explicit search_docs tool below always reads that one key
# directly rather than the (per-session) thread_id a session-scoped tool
# would normally get auto-injected.
_DOCS = [
    ("Agent Foundry's RunBudget enforces cost and step ceilings fail-closed: exceeding either raises BudgetExceeded, it never silently caps spend.", "doc:1"),
    ("A destructive ToolSpec (destructive=True) or one with requires_confirmation=True always pauses for human approval via interrupt(), regardless of Policy.requires_approval.", "doc:2"),
    ("The native runtime (runtime='native') is a second, framework-free implementation of the same think/act/critique loop — it proves WorkflowEngine is a real seam, not LangGraph-only.", "doc:3"),
    ("ExecutionContext.tool_policy narrows, never widens, an agent's own Policy for one run's tool calls only.", "doc:4"),
]

SOURCE_BY_TEXT = {text: source for text, source in _DOCS}

# Module-level, not built inside build_agent() — AgentSpec's dotted tool
# reference ("examples.research_agent.agent:search_docs", see agent.yaml)
# needs a real module attribute to resolve via importlib.import_module()
# + getattr(); a closure defined inside build_agent() would be invisible to
# that. Seeding at import time also means AgentSpec's own agent.yaml path
# gets a populated corpus for free, not an empty one.
memory = MemoryStore()
for _text, _source in _DOCS:
    memory.semantic.upsert("kb", _text, {"source": _source})


def search_docs(query: str) -> list[str]:
    return memory.semantic.search("kb", query)


def build_agent(*, llm: LLMGateway | None = None) -> Agent:
    search_tool = ToolSpec("search_docs", "Search the reference corpus for passages relevant to a query.", {"query": "string"}, search_docs)

    return Agent(
        "researcher",
        "You are a research assistant for the Agent Foundry framework. Answer ONLY from search_docs results — "
        "call it before answering. Cite every source you use inline, e.g. [doc:1]. If nothing relevant is found, say so.",
        tools=[search_tool],
        memory=memory,
        llm=llm,
    )


agent = build_agent()


if __name__ == "__main__":
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("set ANTHROPIC_API_KEY to run this example for real")
    result = agent.run("How does Agent Foundry enforce cost limits?")
    print(result.content)

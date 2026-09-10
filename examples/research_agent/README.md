# Research agent (RAG reference app)

A single governed `Agent` that answers only from a small seeded knowledge
base, citing its sources — exercises `ToolRegistry`/RBAC, `MemoryStore`
semantic search, and the grounding/citation KPIs
(`agent_foundry.kpi.composite_grounding_kpi`, `citation_correctness_kpi`).

## Run it for real

```bash
export ANTHROPIC_API_KEY=...
python examples/research_agent/agent.py
```

## Run it declaratively (AgentSpec)

```bash
export ANTHROPIC_API_KEY=...
python -m agent_foundry.cli run --spec examples/research_agent/agent.yaml \
    --message "How does Agent Foundry enforce cost limits?"
```

Run from the repo root — `agent.yaml`'s tool reference
(`examples.research_agent.agent:search_docs`) is a dotted import path, same
convention as `python -m`.

## Score it

```bash
foundry eval examples/research_agent/agent.py examples/research_agent/eval_dataset.json \
    --thresholds '{"task_success_rate_min": 0.9, "tool_accuracy_rate_min": 0.9}'
```

`eval_dataset.json` is a versioned `EvalDataset`
(`agent_foundry.eval_dataset`) — `expected_substring`/`expected_tool` checks
only, since `EvalCase.kpi` holds a live `KPI` object and isn't
JSON-serializable. The citation-correctness and composite-grounding KPI
cases live in `tests/test_examples_research_agent.py` instead, built
directly in Python — the same reason `core/evalgate.py`'s own tests do that
for KPI-based cases.

## What this demonstrates

- A read-only reference corpus kept at one fixed key (`"kb"`), not a
  per-session `thread_id` — a shared knowledge base every session queries,
  not per-conversation scratch memory.
- `citation_correctness_kpi`: does every `[doc:N]` marker in a reply
  actually name a source that was available to cite?
- `composite_grounding_kpi`: is the reply's own wording actually supported
  by the retrieved passages (word-overlap + verbatim numeric-claim checks)?
- The same tool function backing both the imperative (`agent.py`) and
  declarative (`agent.yaml`) construction paths.

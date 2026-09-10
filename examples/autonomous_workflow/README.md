# Autonomous order-monitor (Run lifecycle + events + self-verification)

A single governed `Agent` driven through `core.run.Run`'s formal lifecycle
(`STARTED`/`RUNNING`/`WAITING_HUMAN`/`WAITING_EVENT`/...) instead of
`Agent.run()`'s plain `RunResult`, wired to react to events
(`Agent.on()`/`events.wire_event_driven`) and self-verify its own answers
via `CritiqueConfig` — escalating to a human only when its answer doesn't
hold up against the evidence it actually gathered, not on every action
(contrast with `examples/commerce_agent`'s destructive-tool-approval
pattern — a different HITL shape, same underlying `interrupt()` mechanism).

## Run it for real

```bash
export ANTHROPIC_API_KEY=...
python examples/autonomous_workflow/agent.py
```

## Run it declaratively (AgentSpec)

```bash
export ANTHROPIC_API_KEY=...
python -m agent_foundry.cli run --spec examples/autonomous_workflow/agent.yaml \
    --message "What's the status of order O-500?"
```

The spec's critique gate uses a *named* evaluator (`critique: {evaluator:
groundedness, ...}`, resolved by `agent_spec._named_evaluator_kpi` into a
real `composite_grounding_kpi` with `llm_gateway.make_grounding_judge(llm)`
as the judge) rather than agent.py's hand-written, fully deterministic
`reference_check_kpi` gate — a live custom `KPI` object still isn't
JSON/YAML-expressible, but a name naming one of the built-in evaluators is.
See `agent.yaml`'s own comment for exactly how the two differ (the named
one blends in an extra LLM-judge call the deterministic one doesn't make).

## Score it

```bash
foundry eval examples/autonomous_workflow/agent.py examples/autonomous_workflow/eval_dataset.json \
    --thresholds '{"task_success_rate_min": 0.9, "tool_accuracy_rate_min": 0.9}'
```

`eval_dataset.json` covers the plain task-success path only. Critique
escalation and the `Run`/event lifecycle aren't JSON-expressible (same
reason `research_agent`'s KPI cases live in Python, not the dataset file) —
see `tests/test_examples_autonomous_workflow.py` for those:
`test_critique_escalates_on_a_severely_ungrounded_answer`,
`test_agent_start_returns_a_run_with_formal_lifecycle`,
`test_event_wiring_triggers_a_turn_and_the_decorated_handler`.

## Why this isn't a `Workflow.dag`/`.supervisor`

`core.evalgate.run_eval` — and this repo's whole eval-as-release-gate
story — is built against a plain `Agent`
(`agent.run(case.input, context=...)`), not the different `run(items)`/
`run(inputs)` shapes `Workflow.fanout`/`.dag` expose. A multi-agent DAG
would have made a flashier demo but couldn't be scored the same way the
other two reference apps are — a real constraint discovered while building
this, not a simplification for its own sake.

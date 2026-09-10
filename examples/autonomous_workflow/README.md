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

The spec deliberately has no critique gate — `AgentSpec` has no `critique:`
field (a self-verify-then-escalate gate needs a `KPI` object and a context
callable, neither JSON/YAML-expressible today). It still runs the same tool
with the same instructions, just without self-verification.

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

# Commerce agent (search / recommend / cross-sell / destructive action)

A shopping assistant: `search_products` and `recommend` are read-only,
`place_order` is destructive and always pauses for human approval
(`Policy.requires_approval`) — the same HITL mechanism
`examples/support_agent.py`'s `issue_refund` demonstrates.

## Run it for real

```bash
export ANTHROPIC_API_KEY=...
python examples/commerce_agent/agent.py
```

## Run it declaratively (AgentSpec)

```bash
export ANTHROPIC_API_KEY=...
python -m agent_foundry.cli run --spec examples/commerce_agent/agent.yaml \
    --message "I need trail running shoes"
```

Note: `AgentSpec.tools` resolves plain callables (see `agent_spec.py`), so
the declarative `place_order` loses `ToolSpec.destructive=True` — but
`Policy.requires_approval` is a fully independent approval trigger
(`guardrails.GuardrailEngine.check_action` checks it regardless of the
destructive flag), so the approval pause still fires. Verified in
`tests/test_examples_commerce_agent.py::test_agent_spec_yaml_still_requires_approval_for_place_order`.

## Score it

```bash
foundry eval examples/commerce_agent/agent.py examples/commerce_agent/eval_dataset.json \
    --thresholds '{"task_success_rate_min": 0.9, "trajectory_accuracy_rate_min": 1.0}'
```

`eval_dataset.json` exercises trajectory checks
(`expected_tool_sequence`, `must_request_approval`) — "was this the correct
trajectory," not just "did the final answer end up right."

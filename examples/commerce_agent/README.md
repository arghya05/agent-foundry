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

`agent.yaml`'s `place_order` is a structured tool entry (`implementation` +
`destructive`/`requires_confirmation`), not a bare `"module:function"`
string — `AgentSpec.tools` accepts both (see `agent_spec.py`'s
`_tool_from_entry`), and the structured form is what lets the declarative
path carry `ToolSpec`-level approval metadata that a bare string never
could. `Policy.requires_approval` (below) is a second, fully independent
approval trigger (`guardrails.GuardrailEngine.check_action` checks it
regardless of the destructive/requires_confirmation flags) — either alone
is enough to pause `place_order`; this spec sets both, deliberately
redundant, the same way `agent.py`'s own `ToolSpec(..., destructive=True)`
+ `Policy.requires_approval=frozenset({"place_order"})` already are.
Verified in `tests/test_examples_commerce_agent.py::test_agent_spec_yaml_still_requires_approval_for_place_order`.

## Score it

```bash
foundry eval examples/commerce_agent/agent.py examples/commerce_agent/eval_dataset.json \
    --thresholds '{"task_success_rate_min": 0.9, "trajectory_accuracy_rate_min": 1.0}'
```

`eval_dataset.json` exercises trajectory checks
(`expected_tool_sequence`, `must_request_approval`) — "was this the correct
trajectory," not just "did the final answer end up right."

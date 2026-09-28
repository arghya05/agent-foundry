# Evaluation integrity repair and compatibility changes

Iteration I004 addresses the core scorecard portion of F09. The new 17-case
regression suite failed before the repair; the corrected evaluation/KPI/dataset
selection passes 70 cases. Three older assertions deliberately changed because
they encoded optimistic missing-data behavior. Original failures are retained.

## Changes application developers must handle

- An absent task/tool/trajectory oracle produces **`None`**, not perfect accuracy.
  A provider exception without a task oracle is an execution error, not a measured
  task-success label. Errors with a declared oracle count as failures.
- A case with no oracle does not pass. `Scorecard.passes()` without thresholds
  requires every case to have an oracle and pass it. An empty scorecard fails.
- A requested gate fails if its metric is absent, nonfinite, or has incomplete
  coverage. Default `required_coverage=1.0` requires that metric on every case;
  explicitly lower coverage only if the application accepts that limitation.
- `metric_coverage` exposes the measured denominator relative to all cases.
  `render()` displays both missing data and coverage. Deltas involving an
  unmeasured metric are `None`.
- Empty/whitespace text expectations are rejected. Use `None` when no such oracle
  exists. Nonfinite KPI values and thresholds cannot pass.
- Cost is the **increment since the case began**, read from the explicit request
  budget when supplied, otherwise the agent budget. Reusing a thread no longer
  counts its earlier charges again.
- A failed run retains observed incremental cost in `CaseResult.cost_usd` and
  `Scorecard.known_cost_usd`. Unknown total billing is marked `cost_complete=False`;
  `avg_cost_usd` remains `None` when any case lacks complete cost evidence.
- New baseline JSON includes measurement schema version 2, coverage and known
  cost. Legacy files still load with version 1 and unknown coverage; do not assume
  an old perfect score had a valid oracle.

```python
scorecard = run_eval(agent, [
    EvalCase(input="Find the order", expected_substring="Order A100")
])
ok, reasons = scorecard.passes({"task_success_rate_min": 0.95})
if scorecard.task_success_rate is None:
    print("Task success was not measured")
```

If only half the cases intentionally have that oracle, the default gate fails.
An explicit `required_coverage=0.5` permits that documented scope; it cannot turn
an entirely unmeasured metric into a pass.

## What this does not establish

Substring and tool-proposal checks are limited oracles, not business-state truth.
Many KPI helpers still need application-specific calibration and missing-data
review. A core exception may not expose a complete partial action trajectory;
the separate WorkBench harness journals actual effects and retains them.

Budget deltas report configured recorded usage, not provider invoices. External
concurrent work sharing the same budget bucket can contaminate a delta. Evaluate
sequentially with isolated contexts/budgets, and use a usage/reservation service
for distributed billing guarantees. The frozen WorkBench campaigns predate I004
and do not call this scorecard; their official task scorer remains unchanged.

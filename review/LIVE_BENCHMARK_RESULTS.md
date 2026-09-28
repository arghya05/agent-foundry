# Live WorkBench measurements

Recorded 2026-09-28. These are measured results, not estimates of a win.

All successful inference used `gpt-5.4-mini-2026-03-17`, low reasoning,
2,048 output tokens/request, at most 20 calls/task, one repetition,
serial tool dispatch and the pinned official v2 scorer. Native and
LangGraph arms retain Foundry controls with a synthetic-task L4 policy.
The reference is the upstream structured loop using the same transport.

| Campaign | Arm | Correct / attempted (planned) | Accuracy | Unwanted effects | Errors | p50 / p95 seconds | Upper cost USD |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [Development: 12 tasks](evidence/live-workbench-20260928/pilot-20260928-openai-responses/summary.json) | Reference | 10/12 (12) | 83.33% | 2 | 0 | 4.989 / 10.050 | 0.187458 |
| [Development: 12 tasks](evidence/live-workbench-20260928/pilot-20260928-openai-responses/summary.json) | Foundry native | 12/12 (12) | 100.00% | 0 | 0 | 4.754 / 8.620 | 0.173592 |
| [Development: 12 tasks](evidence/live-workbench-20260928/pilot-20260928-openai-responses/summary.json) | Foundry LangGraph | 10/12 (12) | 83.33% | 1 | 0 | 5.630 / 15.052 | 0.195986 |
| [Initial baseline: 60 tasks, subsequently inspected](evidence/live-workbench-20260928/heldout-20260928-baseline/summary.json) | Reference | 50/60 (60) | 83.33% | 6 | 0 | 5.754 / 9.670 | 0.979391 |
| [Initial baseline: 60 tasks, subsequently inspected](evidence/live-workbench-20260928/heldout-20260928-baseline/summary.json) | Foundry native | 49/60 (60) | 81.67% | 7 | 0 | 5.660 / 10.243 | 1.006474 |
| [Initial baseline: 60 tasks, subsequently inspected](evidence/live-workbench-20260928/heldout-20260928-baseline/summary.json) | Foundry LangGraph | 48/60 (60) | 80.00% | 8 | 0 | 5.689 / 10.538 | 0.999625 |
| [Guidance diagnostic: the same 60 tasks](evidence/live-workbench-20260928/diagnostic-20260928-grounded/summary.json) | Reference | 47/60 (60) | 78.33% | 8 | 1 | 5.866 / 11.273 | 1.295736 |
| [Guidance diagnostic: the same 60 tasks](evidence/live-workbench-20260928/diagnostic-20260928-grounded/summary.json) | Foundry native | 49/60 (60) | 81.67% | 6 | 1 | 5.701 / 11.050 | 1.297697 |
| [Guidance diagnostic: the same 60 tasks](evidence/live-workbench-20260928/diagnostic-20260928-grounded/summary.json) | Foundry LangGraph | 49/60 (60) | 81.67% | 5 | 1 | 5.958 / 10.002 | 1.286083 |
| [Fresh baseline: next 60 tasks, incomplete](evidence/live-workbench-20260928/confirmation-20260928-baseline/summary.json) | Reference | 45/59 (60) | 76.27% | 8 | 3 | 5.813 / 13.290 | 1.813323 |
| [Fresh baseline: next 60 tasks, incomplete](evidence/live-workbench-20260928/confirmation-20260928-baseline/summary.json) | Foundry native | 50/60 (60) | 83.33% | 8 | 1 | 5.957 / 11.228 | 1.241941 |
| [Fresh baseline: next 60 tasks, incomplete](evidence/live-workbench-20260928/confirmation-20260928-baseline/summary.json) | Foundry LangGraph | 49/60 (60) | 81.67% | 7 | 3 | 5.913 / 11.629 | 1.892195 |

## Interpretation and limits

- The initial 60 tasks were later inspected for failure diagnosis. The guidance
  diagnostic reuses them and also follows core repairs; it cannot isolate prompt
  efficacy or serve as untouched confirmation.
- Both fresh profiles were frozen before inference on the next 60 task IDs.
  Connection failures exhausted the preset continuation allowance at 179/180
  baseline attempts. The guidance profile was not run. No profile improvement
  or complete-baseline result is claimed. Missing attempts are not successes.
- Failures remain in the denominator and retain executed effects and known cost.
  Setup failures and transport errors are archived too. Nothing was rerun to
  replace a bad score. Small samples, task-template dependence and one repetition
  limit inference; paired bootstrap intervals are exploratory.
- End-to-end latency includes provider/network time and shared-workstation load.
  The p95 can change sharply when timeouts exceed 5% of a group. These timings
  do not isolate orchestration overhead or establish distributed throughput.
- Costs use reported usage at base input/output prices plus conservative full
  reservations for unknown requests. They are not provider invoices. Total ledger
  accounting is $16.71967550 against the $25 cap; $7.442336 is unknown reservations.
- Anthropic setup was rejected; the diagnostic identified a required workspace
  header. There is no successful Claude comparison or cross-provider conclusion.
- WorkBench unwanted effects are an official task-state metric, not a complete
  security, authorization, injection-resistance or compliance measurement.
- The best pinned upstream saved run is 674/690 (97.68%), with 13 unwanted-effect
  cases. It uses a different model/harness and the full distribution. Comparing
  these subset percentages to it does not establish a win.

## Paired exploratory outcomes

| Campaign | Runtime minus reference | Paired tasks | Accuracy difference | Bootstrap 95% interval |
| --- | --- | --- | --- | --- |
| Development: 12 tasks | foundry_langgraph_serial-reference | 12 | +0.00 pp | [-33.33, +33.33] pp |
| Development: 12 tasks | foundry_native_serial-reference | 12 | +16.67 pp | [+0.00, +41.67] pp |
| Initial baseline: 60 tasks, subsequently inspected | foundry_langgraph_serial-reference | 60 | -3.33 pp | [-13.33, +6.67] pp |
| Initial baseline: 60 tasks, subsequently inspected | foundry_native_serial-reference | 60 | -1.67 pp | [-10.00, +6.67] pp |
| Guidance diagnostic: the same 60 tasks | foundry_langgraph_serial-reference | 60 | +3.33 pp | [-5.00, +13.33] pp |
| Guidance diagnostic: the same 60 tasks | foundry_native_serial-reference | 60 | +3.33 pp | [-5.00, +11.67] pp |
| Fresh baseline: next 60 tasks, incomplete | foundry_langgraph_serial-reference | 59 | +6.78 pp | [-3.39, +16.95] pp |
| Fresh baseline: next 60 tasks, incomplete | foundry_native_serial-reference | 59 | +6.78 pp | [-5.08, +16.95] pp |

## Evidence and reproduction

Run `python3 review/evidence/audit_live_workbench.py` to verify archive hashes,
coverage and aggregate arithmetic, then `python3 review/evidence/render_live_report.py`.
The audit recounts stored official scores; it does not rerun the official scorer.
See [the CSV](evidence/live-workbench-20260928/derived/live_results.csv),
[per-attempt data](evidence/live-workbench-20260928/derived/per_attempt.csv),
[audit](evidence/live-workbench-20260928/derived/audit_results.json), and
[harness instructions](../benchmarks/workbench/README.md).
Each campaign directory includes its manifest, environment, summary, archive index
and original per-attempt bytes. The WorkBench license accompanies the archives.

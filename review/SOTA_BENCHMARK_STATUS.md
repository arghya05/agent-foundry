# SOTA target and benchmark status

Updated **2026-09-28**. **Foundry has no measured public task-benchmark win yet.** The completed work below establishes a reproducible comparison target and validates the pilot harness. It does not demonstrate enterprise readiness or publication novelty.

## Published WorkBench frontier reproduced

All **24 saved model runs**, comprising **16,560 task predictions**, were re-scored with the official WorkBench v2 scorer at commit `49c7dfd00c03d384ec59ea57374f50b766aa5613`. Every domain count and model total matches the pinned published summary. No model API call was made.

| Saved model run | Correct / tasks | Task accuracy | Unwanted-side-effect cases |
| --- | --- | --- | --- |
| Fable 5 | 674 / 690 | 97.68% | 13 |
| Opus 4.8 | 664 / 690 | 96.23% | 17 |
| Gemini-3.1-pro | 659 / 690 | 95.51% | 20 |
| GPT-5.5 | 655 / 690 | 94.93% | 27 |
| Gemini-3.5-flash | 635 / 690 | 92.03% | 21 |

Sources: [pinned upstream summary](https://github.com/olly-styles/WorkBench/blob/49c7dfd00c03d384ec59ea57374f50b766aa5613/retro/data/model_results.json), [our complete reproduction manifest](evidence/workbench-published-frontier/summary.json), [all 24 reproduced scores](evidence/workbench-published-frontier/scores.csv), and [execution log](evidence/workbench-published-frontier-run.txt). Each model has a separate artifact with per-domain observed/expected counts and prediction-file hashes.

This is the highest-scoring run in the **pinned published comparison**, not a claim to have found every result published worldwide as of today. These are upstream model-plus-harness results, not Foundry scores or fresh model runs. We did not reproduce inference cost or latency; rescoring time is not agent execution time.

The previously reproduced GPT-4o result, 476/690, is useful for checking the scorer but is an inadequate sole baseline for a frontier claim. Beating that older result would not establish SOTA.

## What would constitute a defensible improvement

There are only **16 remaining tasks**, or **2.32 percentage points**, between the best saved run and perfect accuracy. A three-percentage-point gain against that particular baseline is impossible. The general planning margins in the scorecard must be chosen for the actual comparator and frozen before held-out testing.

| Intended claim | Evidence required |
| --- | --- |
| Highest observed WorkBench accuracy in the declared comparison | Complete 690-task official scoring, more than 674 successes, all failures included, exact settings and unwanted effects disclosed; refresh the comparison set before submission |
| Statistically supported task-quality improvement | Matched baseline and candidate runs, paired outcomes, repeated trials, uncertainty appropriate to task/template dependence, and a predeclared meaningful effect; a one-task lead alone is insufficient |
| Better cost/quality tradeoff | Same tasks and comparable tuning budgets; measured model/tool/verifier costs and matched success/risk margins, rather than a cheaper model's price alone |
| Better framework speed | Same model, prompts, tools, controls and deployment conditions, plus isolated orchestration-overhead measurements; no timing comparison between serial pilot execution and upstream's 16-worker campaigns |
| More reliable multi-agent or enterprise execution | Equal-budget single/multi-agent comparisons, real authority propagation, tenant isolation, cancellation, restart and duplicate-effect tests; WorkBench alone cannot establish these properties |
| Publishable original method | A specific contribution beyond the [identified prior art](CLOSEST_REPOSITORIES.md), strong simple baselines, ablations and transfer evidence; benchmark rank alone is insufficient |

## Executed comparison and remaining evidence

[Live measurements](LIVE_BENCHMARK_RESULTS.md) report all attempted outcomes,
latency, effects, errors and upper cost estimates. Seven archived campaigns contain
578 finished attempts, including setup failures. The OpenAI development pilot and
two 60-task comparisons completed; the fresh baseline stopped at 179/180 attempts
at the preset transport-continuation limit. Fresh guidance was not run.

The initial 60-task baseline obtained reference 50/60, native 49/60 and LangGraph
48/60. Guidance reused those tasks after failure inspection and core repairs;
its results do not establish a causal improvement. There is no complete 690-task
Foundry score, successful Claude comparison, repeated confirmation, or standalone
external framework/research-method performance comparison.

Protocol v4 adds explicit profiles and fresh task offsets while retaining matched
prompts/tools/scoring. Earlier v2/v3 manifests remain archived. All 24 offline
adapter tests pass. The user authorized a shared $25 cap; the closed ledger records
$16.71967550 in usage estimates plus unknown reservations, including $7.442336
reserved for requests with unknown usage. These are not asserted invoice charges.

The original security findings are dated baseline observations. Four subsequent
repair iterations and 620 full-suite passes / 25 skips are recorded in
[current status](CURRENT_STATUS.md). Distributed recovery, isolated execution,
approval lifecycle and full adapter governance remain enterprise priorities.

The [expanded prior-art gate](RESEARCH_GATE_20260928.md) finds stronger overlap with
transaction admission and stateful handoffs. The current selective-revalidation
proposal is not established as novel. Conference research requires a distinct
mechanism, nearest-method reproduction, strong baselines, ablations, repeated
untouched-task testing and transfer evidence. User priority is now the validated
GitHub release and enterprise hardening; research/paper work is paused.

Reproduce saved upstream results without paid inference using
`benchmarks/workbench/reproduce_frontier.py`. Follow the
[harness setup instructions](../benchmarks/workbench/README.md), and use a fresh
output directory. Do not reset or delete a paid campaign ledger to regain budget.

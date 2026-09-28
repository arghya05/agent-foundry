# Speed, agentic quality, and multi-agent evaluation

**Follow-up status (2026-09-28):** consult [CURRENT_STATUS.md](CURRENT_STATUS.md), [live results](LIVE_BENCHMARK_RESULTS.md), and [the updated novelty gate](RESEARCH_GATE_20260928.md). Proposed requirements below are not all implemented; original measurements remain dated evidence.

These are three related but separate evaluation tracks. The same agent can be fast and wrong, correct and too expensive, or individually capable but poor at coordinating with other agents. The platform needs evidence across all three.

## 1. Speed: measure where time goes

For a run, separate admission/queue time, model inference, tools, orchestration, persistence, verification, and human waiting. Parallel calls overlap, so summing every span duration overcounts wall time; identify the execution critical path.

| Metric | Definition | Why it matters |
| --- | --- | --- |
| Construction/cold-start time | Import + build + initialize runtime/storage, measured separately | Serverless/startup experience |
| First useful event/token | Request admission to meaningful streamed progress | User-perceived responsiveness; a heartbeat is not task progress |
| End-to-end latency | Admission to terminal result, with approval-wait policy disclosed | Actual application experience |
| p50 / p95 / p99 | Median and tail of the declared workload's latency | Slow users and overload behavior |
| Framework overhead | Runtime work under controlled stub model/tool latency | Separates framework cost from model capability |
| Completed throughput | Successful completed tasks per second at a stated offered load | Capacity; failed-fast requests do not count as useful throughput |
| Coordination time | Routing, communication, merges, and barriers on the critical path | Extra cost of multiple agents |
| Recovery latency | Failure detection to safe resumption/reconciliation | Production interruption cost |
| Resource consumption | CPU, resident memory, storage/network, worker occupancy | Hosting cost and saturation |

Run matched configurations with telemetry, policies, persistence, and verification enabled equally. Do not claim a fast unsafe configuration is a fair improvement over a fully governed one. Profile before optimizing: excessive model calls, large context, serialization, storage round trips, synchronous I/O, and unconstrained fan-out require different remedies.

**Local observations already available:** the repository's scripted test measured native single-agent p50/p95 at 0.07/0.10 ms and LangGraph at 1.99/3.06 ms. With a tool, native was 1.34/21.45 ms versus 4.53/5.04 ms. These small local scenarios show both median gains and a contrary tail result; they do not establish live application speed. [Raw output](evidence/runtime-microbenchmark.txt).

For credible next measurements, use separate fresh processes, warm-up, randomized order, repeated samples large enough to estimate tails, and a hardware/dependency manifest. Publish raw durations. Run concurrency sweeps and a soak only after correctness gates pass. The current native shared-state defect makes production-scale correctness a prerequisite, not a conclusion from a local latency number.

## 2. Agentic quality: evaluate actions and outcomes

Use the [benchmark scorecard](BENCHMARK_SCORECARD.md). WorkBench covers workplace state changes; conversational tool-use suites assess interaction and policy; AgentDojo assesses attacked versus clean behavior. All must use official scoring and declared versions.

Report verified outcome, unwanted effects, correct tool arguments, required-step completion, clarification/escalation, repeated-run reliability, cost, and latency. A final answer saying “done” is not an outcome oracle. A refusal can be correct for an unauthorized task and incorrect for an authorized one.

For adaptive context, add fixed-budget tests for long histories, new information, corrections, and stale evidence. The [Meta Agents Research Environments project](https://github.com/facebookresearch/meta-agents-research-environments) is a candidate source for dynamic/asynchronous scenarios; inspect its current benchmark protocol before adding an adapter. It was not run in this review.

## 3. Multi-agent quality: does coordination actually help?

Use **[MultiAgentBench / MARBLE](https://github.com/ulab-uiuc/MARBLE)** as an explicit public benchmark candidate. Its [paper](https://arxiv.org/abs/2503.01935) evaluates collaborative/competitive scenarios and coordination milestones. Choose task environments relevant to the proposed claim; game performance alone would not demonstrate enterprise quality. No Foundry score has been produced.

The official scorer should remain unchanged. Add communication/cost/reliability instrumentation as separately labeled measurements. If using a unifying harness such as [MASEval](https://github.com/maseval/MASEval), verify scorer equivalence: its [benchmark documentation](https://github.com/maseval/MASEval/blob/main/BENCHMARKS.md) labels some integrations beta and warns that original-result parity is not yet validated. A common API is useful but is not sufficient evidence of comparable scores.

| Experiment | What stays fixed | What varies | Question |
| --- | --- | --- | --- |
| Single vs multi-agent | Model, tools, task, total token/cost budget | 1 vs 2/4/8 workers where task allows | Does coordination improve outcome per unit of compute? |
| Topology | Agent count, role information, model, budget | Supervisor, handoff, debate, blackboard, task DAG | Which coordination structure fits the task? |
| Framework | Equivalent task/roles/tools/budget | Foundry native, Foundry LangGraph, suitable external framework | Does implementation change quality, overhead, or enforcement? |
| Context sharing | Task, model, budget, authority | Full transcript, summaries, scoped evidence | Is communication useful, redundant, or harmful? |
| Adaptation | Available choices and training/dev data | Fixed rule vs adaptive controller | Does the controller outperform a strong simple policy? |
| Fault tolerance | Task and failure injection schedule | Missing/stalled worker, duplicate message, restart | Can the team recover without unsafe or duplicated effects? |

Do not equate Foundry's topology names one-to-one with another benchmark's communication structures. Document the actual graph, routing rules, shared state, stop conditions, and message visibility. Map only genuinely equivalent configurations.

## Multi-agent result columns

Publish one row per configuration with:

`benchmark revision | scenario | model | framework/runtime | topology | agents | verified success | official milestone score | harmful effects | model calls | communication tokens | total tokens | total cost | p50/p95 latency | recovery failures`

Useful derived measures include success gain over the equal-budget single agent, cost per successful task, and wall-time speedup relative to an equivalent sequential execution. Define communication-token counting precisely: duplicated broadcasts consume tokens at each receiving model and should not be counted only once.

Multi-agent success is not proof of better individual reasoning. More samples, more tokens, additional role information, or a stronger judge can explain a gain. Use ablations to separate those effects. Include a centralized agent with the same total budget and available evidence as a strong baseline.

## Correctness matrix for multi-agent systems

Every supported topology should preserve caller authority, tenant isolation, parent budget, cancellation, trace lineage, and approval semantics. Test missing workers, malformed handoffs, cycles, conflicting answers, stalled barriers, shared-state races, stale memories, and duplicate effects. Verify that handoff limits and stopping conditions actually terminate the run.

Use the [MAST failure study](https://arxiv.org/abs/2503.13657) to organize diagnostic labels, while keeping actual task scoring independent. A taxonomy helps explain failures; it is not itself a performance leaderboard. Findings on [scaling agent systems](https://arxiv.org/abs/2512.08296) also motivate measuring coordination tradeoffs rather than assuming more agents are better.

## Paper-ready claim template

“On [pinned scenarios], with [model] and [total budget], [method] changed verified task success from [measured baseline] to [measured result], with [uncertainty], [cost], [latency], and [harmful-effect rate]. The gain persisted across [held-out settings] and disappeared/changed under [ablations].”

Leave those fields empty until experiments exist. A strong result can be a better quality–cost tradeoff or a clear reliability improvement; it does not need to claim superiority across every task and runtime.

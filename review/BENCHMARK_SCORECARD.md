# Clear benchmark scorecard

**Current engineering validation:** 620 passed, 25 skipped; the selected-test counts below are historical. [Current status](CURRENT_STATUS.md) separates completed repairs from remaining work.

**Use this as the evaluation starting point.** The longer [benchmark plan](BENCHMARK_PLAN.md) explains implementation and experimental details.

A framework needs evidence for agent capability, execution correctness, operational reliability, and developer usability. A higher task score from a stronger model does not by itself show that the framework is better.

**2026-09-28 update:** all 24 published WorkBench model runs have been re-scored;
all 16,560 task-prediction counts match upstream. The highest saved accuracy is
674/690 (97.68%), with 13 unwanted-side-effect cases. These are upstream results,
not Foundry results. See [the current SOTA target](SOTA_BENCHMARK_STATUS.md).

## The five primary public benchmarks

| Benchmark | Plain-language question | Report these metrics | Current Foundry result |
| --- | --- | --- | --- |
| **[WorkBench](https://github.com/olly-styles/WorkBench)** | Can the agent perform workplace tasks and change the correct records? | Official task completion; unwanted side effects; cost per successful task; p50/p95 latency | Bounded live subsets measured; [all results](LIVE_BENCHMARK_RESULTS.md), including incomplete fresh baseline; no full-suite result |
| **[τ²-bench](https://github.com/sierra-research/tau2-bench)**, pinned text/dual-control release and domains | Can it help a user through a multi-turn business task while using tools and respecting policy? | Official success score; repeated reliability where supported; policy violations; turns; cost and latency | **Not run** |
| **[AgentDojo](https://github.com/ethz-spylab/agentdojo)** | Can malicious tool content make it perform an attacker-chosen action? | Attack success rate; clean task utility; utility under attack; cost/latency of defense | **Not run** |
| **[AgentGovBench](https://github.com/agentic-control-plane/agentgovbench)** | Does the surrounding system preserve identity, permissions, delegation, audit, and tenant boundaries? | Per-category pass/fail/unsupported; executed unauthorized effects; complete result manifest | Upstream vanilla **13/48 reproduced**; scorer evidence gaps recorded in [source audit](CLOSEST_REPOSITORIES.md). **Foundry control plane: 48/48** in 10/10 repetitions, with ablations and supplemental scenarios ([paper](../research-paper/AgentFoundry_Paper.pdf), [evidence](evidence/paper-campaign-20260928c/agentgovbench/)) |
| **[MultiAgentBench / MARBLE](https://github.com/ulab-uiuc/MARBLE)** | Do multiple agents coordinate effectively on the chosen scenarios? | Official task/milestone scores; added communication tokens, total cost, latency, and equal-budget single-agent comparison | **Not run** |

Pin the exact repository revision, scorer, and task release before execution. WorkBench has a 2026 revision; the τ-bench repository has evolved beyond the original τ² publication. AgentGovBench is vendor-maintained and requires independent inspection of its scoring assumptions. No current leaderboard numbers are adopted as Foundry results.

## Additional benchmarks when the capability exists

| Benchmark | Add when | Primary use |
| --- | --- | --- |
| [AgentBench FC](https://github.com/THUDM/AgentBench) | Environment adapters and container resources are ready | Broader function-calling/environment interaction; distinguish FC from original AgentBench |
| [WorkArena / WorkArena++](https://github.com/ServiceNow/WorkArena) | Browser agent and required enterprise test-instance access exist | Browser-based enterprise workflow execution |
| [TheAgentCompany](https://github.com/TheAgentCompany/TheAgentCompany) | Multi-application computer/tool environment is integrated | Longer workplace tasks across company applications |
| [ToolSandbox](https://github.com/apple/ToolSandbox) | Stateful conversational tool adapter exists | Tool prerequisites, interaction, and state transitions |
| [LongMemEval](https://arxiv.org/abs/2410.10813) | The memory/context subsystem is the focus | Long-term conversational memory, updates, and temporal questions |

Do not spend the initial research budget on every suite. These five cover enterprise tasks, security/governance, and multi-agent coordination; the rest answer additional capability questions. The separate [speed and multi-agent protocol](SPEED_AND_MULTIAGENT_EVALUATION.md) defines the runtime and coordination measurements.

## Required framework-specific tests

These are engineering tests or proposed experiments, not established public benchmark names.

| Test family | Required conditions | Metrics | Current evidence |
| --- | --- | --- | --- |
| Runtime overhead | Same deterministic provider and tools, randomized order, warm/cold separation | p50/p95/p99 overhead, CPU/RAM, completed throughput | One preliminary local run; see below |
| Concurrency | Multiple tenants/runs, bounded fan-out, rising offered load | Throughput, queue delay, p95/p99, errors, fairness | Selected unit tests only; no production load result |
| Recovery | Kill worker before/after effect; stale state; duplicate delivery; expired owner | Lost state, duplicate effects, recovery time, unresolved outcomes | One local probe found stale-state loss |
| Control conformance | Deadline, cancellation, role restrictions, approval plus deny, nested delegation | Pass/fail and actual prohibited effects | Multiple failing behaviors reproduced |
| Context isolation | Poisoned/revoked/deleted data; same user/thread names across tenants | Leakage, stale citations, retrieval utility, correct deletion | Source review and one context-path probe |
| Developer onboarding | Independent developers build, evaluate, and debug a starter app | Completion rate, time, mistakes, assistance needed | Not measured |

## Actual results already obtained

| Measurement | Recorded result | Interpretation |
| --- | --- | --- |
| Selected tests across 44 modules | **432 passed, 2 skipped, 5 missing-dependency failures** | Useful implementation evidence; not the full suite or a public task benchmark |
| Deterministic review probes | **16 observations** | Include ignored request controls, approval bypass, native delegated identity loss, stale state, and a correct LangGraph denial |
| Scripted single-agent runtime | Native p50/p95 **0.07/0.10 ms**; LangGraph **1.99/3.06 ms** | One local 15-case scenario; no live-model latency claim |
| Scripted tool-calling runtime | Native p50/p95 **1.34/21.45 ms**; LangGraph **4.53/5.04 ms** | Native median lower, tail worse in this run; too small for a general ranking |
| Public task-benchmark wins | **None demonstrated** | SOTA is not established |

Sources: [test logs and environment](evidence/README.md), [probe observations](evidence/reproduced-findings.json), [microbenchmark output](evidence/runtime-microbenchmark.txt).

## Proposed acceptance targets

These are **planning targets**, not results, promises, or universal enterprise thresholds. Freeze the final targets after a small pilot estimates baseline performance, variance, and cost, and before held-out testing.

| Goal | Proposed gate |
| --- | --- |
| Correct mandatory controls | Zero prohibited effects in the declared deterministic control suite, with every mandatory scenario supported; no hiding unsupported cases |
| Better task quality | A predeclared practically meaningful improvement over a strong matched baseline, with task-paired uncertainty analysis; a candidate planning margin is 3 percentage points |
| Better efficiency | As an alternative primary claim, at least 20% lower total cost at matched verified success and no meaningful increase in harmful effects; define the noninferiority margin before testing |
| Runtime efficiency | No unexplained overhead regression versus the previous release at equivalent enabled controls; publish full latency distribution, not only best-case speed |
| Recovery | No silent state loss or duplicated irreversible effects in the bounded fault suite; report every unresolved external effect explicitly |
| Startup usability | Proposed onboarding target: first working, tested, traceable agent within 30 minutes for most participants in an independent pilot |

The 3-point and 20% margins are proposed product/research goals, not values derived from papers. They may need revision after the pilot. A small gain near a saturated benchmark can still be meaningful, while a large gain on a weak baseline may not be. Security severity must be examined independently of aggregate success.

Do not use the generic 3-point accuracy target against WorkBench's reproduced
97.68% leader: only 2.32 points remain before perfect accuracy. Choose an attainable,
predeclared margin for the actual baseline, or study cost at matched success/risk.

## Fair comparison setup

For the controlled framework comparison, keep **model/version, tools, task split, initial information, generation settings, total token/cost budget, and timeout equal**. Tune on development tasks only. Run minimal Python, Foundry native, Foundry LangGraph, and well-configured relevant external frameworks. For a research claim, add the closest strong method rather than only popular SDK names.

For the separate “best complete system” experiment, allow each harness its own optimized prompts and mechanisms, disclose them, and allocate comparable tuning budgets. Plot task quality against cost, latency, and harmful effects. Do not attribute a different model's advantage to Foundry.

For repeated reliability, **pass^k** asks whether all k attempts succeed; **pass@k** asks whether at least one succeeds. Use the benchmark's official estimator and clearly state which metric is reported.

## Execution order

1. Fix failing authority/context/approval/state controls so the comparison measures a coherent system.
2. Pin and reproduce the official WorkBench scorer/baseline; implement the Foundry adapter.
3. Run the implemented 12-task development pilot for wiring and cost estimation. Expand development only if needed and funded, then freeze the revised project split before evaluation. WorkBench has no official hidden split here; the planned 678-task remainder excludes the current 12 development tasks.
4. Add conversational tool-use, injection, and governance evaluations; retain all outcomes.
5. Run repeatable overhead, load, and recovery experiments on an isolated reference deployment.
6. Evaluate the proposed research mechanism with ablations and the closest existing methods.
7. Publish raw per-task artifacts, exact configurations, uncertainty, failures, and limitations before making a bounded benchmark claim.

This sequence can prove useful, specific properties. It cannot prove that an arbitrary future agent is correct in every enterprise or industry.

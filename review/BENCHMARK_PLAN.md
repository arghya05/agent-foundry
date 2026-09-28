# How to evaluate this framework and substantiate performance claims

**Follow-up status (2026-09-28):** consult [CURRENT_STATUS.md](CURRENT_STATUS.md), [live results](LIVE_BENCHMARK_RESULTS.md), and [the updated novelty gate](RESEARCH_GATE_20260928.md). Proposed requirements below are not all implemented; original measurements remain dated evidence.

**Current status: no public task-benchmark score has been produced for Agent Foundry in this review.** Unit/regression tests and one scripted-provider runtime microbenchmark were run. They answer different questions from a live public benchmark. No score below is fabricated or inferred from a competitor's model performance.

## A practical mental model

Evaluate the framework as an execution system, then evaluate agents built with it as applications. To measure the framework's contribution, keep the model, tools, prompts where possible, tasks, and budget fixed. To measure the best complete product, allow an optimized harness but disclose every component and give baselines a comparable tuning budget. Report these as two separate experiments.

| Goal | Test | What a pass establishes |
| --- | --- | --- |
| Correct implementation | Contract/unit/integration tests | Specific promised behaviors under tested conditions |
| Fast runtime | Scripted-provider microbenchmarks | Framework overhead on the measured machine |
| Capable agent | Public task benchmarks | Task performance of the complete tested model + harness |
| Robust enterprise execution | Isolation, authorization, fault injection and recovery | Operational controls under the tested deployment/threat model |
| Useful startup product | Developer study and pilot workflows | People can build, debug, and operate representative applications |

No one of these substitutes for the others.

## Public benchmark portfolio

“Agentic workbench” was ambiguous. Include **WorkBench** and **AgentBench** as distinct candidates rather than assuming they are the same project.

| Priority | Benchmark and official source | Measures | Integration and fair-use requirements |
| --- | --- | --- | --- |
| First | [WorkBench](https://github.com/olly-styles/WorkBench) | Workplace tool tasks, final-state correctness, unintended effects | Map benchmark tools into governed tools; retain official scorer/ground truth; pin 2026 revision and tool-selection setting |
| First | [τ-bench / τ²-bench family](https://github.com/sierra-research/tau2-bench) | Multi-turn support, policy following, tool/user coordination | Match domain, simulator, trial count, and agent policy; current repository also includes newer τ³-era components, so do not conflate versions |
| First | [AgentDojo](https://github.com/ethz-spylab/agentdojo) | Prompt-injection attack outcomes and legitimate task utility | Implement a pipeline adapter; run clean and attacked pairs with held-out/adaptive attacks; isolate simulated effects |
| First | [AgentGovBench](https://github.com/agentic-control-plane/agentgovbench) | Deterministic identity, policy, delegation, audit, and tenant scenarios | Audit runner/scorer; implement a real Foundry adapter; report unsupported scenarios and add independent lifecycle tests |
| Multi-agent primary | [MultiAgentBench / MARBLE](https://github.com/ulab-uiuc/MARBLE) | Collaborative/competitive task performance and coordination milestones | Pin environment and official scorer; match model, role information, communication rules, and total budget |
| Second | [AgentBench](https://github.com/THUDM/AgentBench) | Diverse environment interaction and tool use | Current function-calling edition differs from original eight-environment edition; pin edition, containers, task split, and evaluator |
| Second | [ToolSandbox](https://github.com/apple/ToolSandbox) | Stateful conversational tool use and dependencies | Preserve state transitions and milestone scoring; task outcomes are not simple text matching |
| Second | [WorkArena / WorkArena++](https://github.com/ServiceNow/WorkArena) | Enterprise browser tasks and composed workflows | Needs browser adapter and ServiceNow instance access; follow BrowserGym/AgentLab evaluation protocol |
| Second | [TheAgentCompany](https://github.com/TheAgentCompany/TheAgentCompany) | Long-horizon work across company applications | Provision its environment and required computer/tool interfaces; report partial progress separately |
| Context track | [LongMemEval](https://arxiv.org/abs/2410.10813) | Persistent conversational memory | Pin history, context budget, retrieval policy, and scoring; pair with tenant/deletion tests |

The current WorkBench repository distinguishes ground-truth versions and documents scorer changes. Old and new results are not automatically comparable. Its inference hooks and precomputed results provide useful integration starting points; reproduce the selected scorer before measuring Foundry. [WorkBench documentation](https://github.com/olly-styles/WorkBench).

Do not begin by optimizing for every benchmark. Start with WorkBench, a pinned conversational tool-use suite, AgentDojo, and governance conformance. These align with the enterprise product and expose different failure modes. Add coding/browser/voice suites when those application capabilities are actually implemented.

## Existing local evidence

The repository's [native/LangGraph microbenchmark](../benchmarks/native_vs_langgraph.py) was executed unchanged. It uses a deterministic in-process provider, 15 cases in most scenarios (fan-out uses five timed calls of 15 items), synthetic per-call prices, `tracemalloc`, and a fixed execution order. Raw results: [runtime-microbenchmark.txt](evidence/runtime-microbenchmark.txt).

| Script scenario | Native p50 / p95 ms | LangGraph p50 / p95 ms |
| --- | --- | --- |
| Single agent | 0.07 / 0.10 | 1.99 / 3.06 |
| Agent with tool | 1.34 / 21.45 | 4.53 / 5.04 |
| Supervisor | 0.10 / 0.13 | 2.28 / 2.67 |
| Fan-out | 2.08 / 2.16 | 13.11 / 13.94 |
| DAG | 0.44 / 0.52 | 3.06 / 3.86 |

These are one-run local observations. Native has lower median overhead in these scenarios, but the tool scenario has a worse native tail. Do not remove this contrary result. Fifteen cases, initialization/import effects, fixed ordering, and memory instrumentation are inadequate for a publishable latency ranking. Synthetic “100% eval” means scripted expected outputs matched, not that agents solved a public benchmark. The cost column is a configured model, not a bill. The real-service scalability script was inspected but not executed.

## Baselines

1. Minimal direct Python agent loop using the same model/tools.
2. Deterministic workflow for tasks where one is natural.
3. Agent Foundry native and LangGraph, matched configurations.
4. A maintained LangChain/Deep Agents harness and CrewAI setup suited to the task.
5. Another independent maintained SDK, such as Pydantic AI or Google ADK, where the integration is reasonable.
6. The closest governance/memory/adaptation method for the actual research claim; include a strong external gateway baseline rather than only an unsafe “no controls” baseline.

Do not run every possible framework × model × topology combination at full scale initially. Use a pilot to find integration failures, then freeze a justified comparison set. A published framework comparison needs working, reviewed baseline implementations and transparent unsupported features.

## Controlled experiment recipe

1. Freeze benchmark revision, task split, scorer, environment images, Foundry commit, adapter versions, prompts, model IDs, and generation settings. Define the hypothesis and primary metric before viewing held-out scores.
2. Reproduce one official baseline and verify scorer behavior with known successes/failures. Confirm that Foundry cannot access expected answers or evaluator state through its tools.
3. Build thin adapters that translate the benchmark's real observations and actions. Capture every proposed/executed action and preserve official termination semantics.
4. Use a small development pilot, for example 20–50 tasks, to validate wiring and estimate cost/variance. This is not a leaderboard result. Final sample sizes follow the chosen benchmark protocol and a power/precision analysis.
5. Freeze improvements on a development split. Evaluate the official test split with the mandated repetitions; where repeated reliability is studied, predeclare multiple independent trials, such as five, and justify the count.
6. Compare paired task outcomes. Report task-clustered uncertainty intervals, failures, exclusions, timeouts, and provider incidents. Correct or transparently account for multiple comparisons.
7. Measure cost/latency/side effects alongside success. A method that gains success by spending ten times the budget belongs on a different point of the tradeoff curve.
8. Repeat across at least two suitable model families for a model-independent claim and across multiple domains for a portability claim. Vendor stochasticity and model revisions remain limitations.
9. Release per-task results, grader logs, sanitized traces, environment pins, and commands. Use official submission rules for any leaderboard submission; this review does not submit results or contact maintainers.

Treat a timed-out or budget-exhausted attempt according to the official protocol, usually as an unsuccessful attempt. Never rerun only bad outcomes until they pass. If an infrastructure incident warrants a rerun, record a rule and apply it symmetrically.

## Performance, scale, and fault experiment design

Measure two workload classes: zero/deterministic-latency stubs to isolate overhead, and realistic model/tool latency distributions to assess system behavior. Separate warm-up from measurement, randomize run order, repeat processes, and record hardware, OS, Python, dependencies, configuration, and background load.

Sweep concurrency conservatively, for example 1/10/50/100 active requests before larger loads. Use both open-loop arrival rates and bounded clients where appropriate; report offered load, completed throughput, error rate, queue time, p95/p99, and resource consumption. Check correctness under load, not merely requests per second. Add a long-running soak, noisy tenants, storage latency, provider throttling, cancellation storms, and worker restart.

Fault points include before/after action-intent persistence, during the tool call, after the external effect but before result persistence, during checkpoint update, on lease expiration, and on approval resume. Measure duplicate effects, lost state, unauthorized effects, and recovery time. This is more diagnostic of enterprise quality than a token-generation speed chart.

## Adaptation ablations

Compare no memory, fixed retrieval, context compaction, and adaptive retrieval; then single-agent, static multi-agent, and adaptive topology under matched budgets. Remove one mechanism at a time: provenance checks, action-specific verification, context selection, adaptive routing, and recovery protocol. Keep controls that protect real environments enabled; unsafe ablations run only in isolated benchmark simulators.

Explain gains by task families and failure classes. Do not make a paper from a single aggregate number or a favorable subset chosen after results are known.

## Run manifest and result ledger

A benchmark artifact should record:

```yaml
status: planned  # planned, pilot, measured, invalidated
framework_commit: 2623ea1b2fe2d93beb1976c0d4e2449891b6900a
benchmark: workbench
benchmark_commit: null  # resolve and freeze before execution
dataset_hash: null
scorer_version: null
adapter_commit: null
model_id: null
provider_and_revision: null
prompt_hash: null
tool_schema_hash: null
split: null
task_ids_hash: null
repetitions: null
token_budget_per_task: null
cost_limit_usd: null
timeout_seconds: null
hardware_and_environment: null
raw_results_path: null
```

`null` means unresolved, not zero. The current [benchmark status ledger](evidence/benchmark-status.json) explicitly records unmeasured public benchmarks. A public score requires real raw results and an independently rerunnable scorer.

## What “SOTA” could legitimately mean

A defensible claim is: “On pinned benchmark X, split Y, using model M and budget B, this configuration improves the declared metric over the specified baselines, with these uncertainty intervals and tradeoffs.” A cost/quality or safety/utility improvement can be valuable even without the highest raw success rate.

“Beats every public benchmark,” “safe for every enterprise,” and “any agent works blindly” are not testable bounded claims. The route to trust is reproducible evidence, visible limits, and an improving support matrix. No leaderboard win removes the need for customer-specific acceptance tests.

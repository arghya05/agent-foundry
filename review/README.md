# Agent Foundry: architecture, product, and research review

Reviewed **2026-09-27** against commit **`2623ea1b2fe2d93beb1976c0d4e2449891b6900a`**. The original review is preserved; subsequent implementation and experiments are tracked separately.

**Current status:** [CURRENT_STATUS.md](CURRENT_STATUS.md) records five repair iterations,
643 passing tests / 25 skips, and the remaining enterprise and research gates.
[Live results](LIVE_BENCHMARK_RESULTS.md) cover seven archived campaigns and 578
finished attempts. The fresh baseline stopped at 179/180 attempts; fresh guidance
was not run. No SOTA or original-method claim is established.

The [closest-source comparison](CLOSEST_REPOSITORIES.md) and
[research gate update](RESEARCH_GATE_20260928.md) identify prior-art overlap.
The original seven-repository comparison has been extended to Mnemosyne.
The [published frontier](SOTA_BENCHMARK_STATUS.md) is an upstream scorer
reproduction, not a Foundry result.

**Verdict: a promising, unusually broad reference framework, but the current evidence does not justify calling it state of the art or a generally enterprise-ready platform.** It already implements useful single-agent and multi-agent patterns. The largest obstacles are inconsistent security and execution semantics between entry points, incomplete distributed reliability, and evaluation metrics that can report success without measuring the intended outcome.

The ambition is feasible as a direction: a Python platform that lets startups assemble agents and gradually adopt enterprise controls. “Any agent, any industry, with no judgment required” is not a supportable guarantee. The useful product promise is **simple authoring, explicit capabilities, safe defaults, verifiable execution, and a clear route to production**. A framework should handle infrastructure; the application owner must still define business permissions, success criteria, and acceptable autonomy.

## Read in this order

| Document | Purpose |
| --- | --- |
| [REVIEW_PLAN.md](REVIEW_PLAN.md) | Detailed work plan, completed review work, remaining experiments, release gates, priorities, and adoption plan |
| [REPOSITORY_REVIEW.md](REPOSITORY_REVIEW.md) | Current capabilities, layer adequacy, evidence-backed findings, and runtime support assessment |
| [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md) | Recommended framework-neutral architecture and execution contracts |
| [SECURITY_EVALUATION_MONITORING.md](SECURITY_EVALUATION_MONITORING.md) | Threat model, guardrails, evaluation design, monitoring, and production acceptance criteria |
| [RESEARCH_AND_COMPARISON.md](RESEARCH_AND_COMPARISON.md) | Primary-source papers, similar GitHub projects, what to adopt, and limits of SOTA claims |
| [CLOSEST_REPOSITORIES.md](CLOSEST_REPOSITORIES.md) | Selected pinned implementation comparison, benchmark scorer audit, and revised novelty gate |
| [BENCHMARK_PLAN.md](BENCHMARK_PLAN.md) | Plain-language evaluation recipe, public benchmark selection, fair comparisons, and measured local overhead |
| [BENCHMARK_SCORECARD.md](BENCHMARK_SCORECARD.md) | Short, explicit benchmark list, metrics, current results, and proposed acceptance targets |
| [SOTA_BENCHMARK_STATUS.md](SOTA_BENCHMARK_STATUS.md) | Reproduced published frontier, actual comparison target, pilot readiness, and limits of possible claims |
| [SPEED_AND_MULTIAGENT_EVALUATION.md](SPEED_AND_MULTIAGENT_EVALUATION.md) | Speed measurements, MultiAgentBench comparison, topology experiments, and coordination metrics |
| [PUBLICATION_PLAN.md](PUBLICATION_PLAN.md) | A falsifiable research direction, baselines, experiments, and NeurIPS/ICML-level submission requirements |
| [PAPER_EXPERIMENT_LOG.md](PAPER_EXPERIMENT_LOG.md) | Experiment register, fixes, preserved failures, logging requirements, and live-run status |
| [RESEARCH_DESIGN_2026.md](RESEARCH_DESIGN_2026.md) | Concrete adaptive-verification research candidate, September 2026 prior art, and novelty decision gate |
| [MISSING_CAPABILITIES.md](MISSING_CAPABILITIES.md) | Consolidated implementation backlog and measurable requirements for the full platform ambition |
| [FILE_COVERAGE.md](FILE_COVERAGE.md) | Complete tracked-file inventory and honest inspection/execution coverage |
| [evidence/README.md](evidence/README.md) | Test commands, environment, reproduction instructions, and limitations |

## What the original snapshot review established

- All **225 tracked files** were inventoried: **218 text files, 3 PDFs, and 4 images**. The inventory includes hashes, sizes, line counts, and Python symbols. Source, documentation, configuration, examples, tests, and architecture diagrams were included. Risk-bearing execution paths received deeper manual inspection. This is not a claim that every integration or every line was dynamically verified.
- There are **133 Python files**, **22,151 Python lines**, and **568 test-named functions** in this snapshot. Test functions and collected/parameterized test cases are different counts.
- Two selected test runs across **44 test modules** produced **432 passes, 2 skips, and 5 failures caused by missing optional dependencies** (`anthropic` and `chromadb`). At that original review stage, the full suite and live evaluations had not been completed. Subsequent results are linked above. Logs are retained without hiding failures.
- A separate deterministic probe script produced **16 observations**, including request controls ignored in both native and LangGraph execution, approval bypassing an external deny policy, native supervisor identity loss, stale state across native instances, and a tool timeout waiting for the worker to finish. The LangGraph supervisor correctly denied the corresponding identity probe; behavior is not identical across runtimes.
- Research and competitor assessment use primary papers, official documentation, and public repositories. Competing frameworks were **not benchmarked locally**, and the literature review is broad but not exhaustive.

## Decisions to make now

1. **Repair the shared execution and authorization contract before adding more agent patterns.** Adding wrappers currently creates more paths where controls can be lost.
2. **Keep native Python and LangGraph as supported engines.** Treat LangChain integration and CrewAI integration as distinct adapters with explicitly documented guarantees. They are not four interchangeable, equally governed backends today.
3. **Concentrate the first product on two workflows:** support/service operations and internal operations with approved writes. Use those to prove onboarding, reliability, and business value, then add industry packs.
4. **Make evaluation and observability trustworthy before advertising their scores.** Missing measurements must remain missing; run completion must be separated from business success.
5. **Pursue a narrow research contribution:** investigate preservation of authority across runtime boundaries and, as a candidate algorithmic direction, selective revalidation when the evidence for an action changes. Choose one contribution after comparing close 2026 prior art. The repository is a potential experimental artifact, not yet evidence of a novel or publishable method.

“SOTA” requires a specified task, comparator set, metric, budget, and reproducible result. “Default platform” additionally requires developer adoption, reliable releases, documentation, compatibility, and support. Neither follows from having more layers or more agent topologies.

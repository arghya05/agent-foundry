# Publication plan: from framework to defensible research

**Follow-up status (2026-09-28):** consult [CURRENT_STATUS.md](CURRENT_STATUS.md), [live results](LIVE_BENCHMARK_RESULTS.md), and [the updated novelty gate](RESEARCH_GATE_20260928.md). Proposed requirements below are not all implemented; original measurements remain dated evidence.

The requested standard is NeurIPS/ICML-level rigor. This document is a research proposal and experiment plan, **not a paper with completed results**. The repository's breadth, documentation, and number of topologies are not sufficient research contributions by themselves.

The follow-up [2026 research design](RESEARCH_DESIGN_2026.md) develops an adaptive-verification candidate and incorporates additional current prior art, including ActGov, AgenTRIM, and budgeted verification. Choose one validated research question after the novelty gate; do not combine every proposed direction into one oversized paper.

## Venue bar and timing

ICML's 2026 guidance evaluates soundness, presentation, significance, and originality; it explicitly allows originality through new insights or well-motivated combinations rather than requiring an entirely new algorithm. Claims still need appropriate evidence and comparison with prior work. [ICML reviewer instructions](https://icml.cc/Conferences/2026/ReviewerInstructions).

As of 2026-09-27, the regular **ICML 2026** full-paper deadline (January 28) and **NeurIPS 2026** full-paper deadline (May 6) have passed. Treat their guidance as a quality reference, not an available submission slot. Choose a future edition or appropriate workshop only after checking its actual call; no 2027 deadline or acceptance probability is assumed here. [ICML call](https://icml.cc/Conferences/2026/CallForPapers), [NeurIPS call](https://neurips.cc/Conferences/2026/CallForPapers).

An evaluation-focused contribution may fit a dataset/evaluation track if it establishes valid new measurement or findings; the 2026 NeurIPS Evaluations & Datasets reviewer guidance explicitly discusses evaluation methods, auditing studies, and reusable executable artifacts. A product catalogue is not such a contribution. [NeurIPS E&D guidance](https://neurips.cc/Conferences/2026/EvaluationsDatasetsReviewerGuidelines).

## Stage zero: check whether the idea is actually new

The initial attractive idea—governance across multiple agent frameworks—has **directly overlapping prior art**. AgentSpec studies runtime constraints; CaMeL studies control/data separation and capabilities; SABER targets mutating actions; existing governance toolkits and AgentGovBench already address framework integrations and identity/policy/audit scenarios. These works must be compared, not cited only in passing. [AgentSpec](https://arxiv.org/abs/2503.18666), [CaMeL](https://arxiv.org/abs/2503.18813), [SABER](https://arxiv.org/abs/2512.07850), [AgentGovBench](https://github.com/agentic-control-plane/agentgovbench), [governance toolkit](https://github.com/microsoft/agent-governance-toolkit).

Before implementing a novel method, read their full methods/artifacts and construct a claim-by-claim matrix: authority propagation, attenuation, policy revocation, approval binding, restart/replay, effect deduplication, opaque adapters, measurement validity, overhead, and threat assumptions. Reproduce the closest baseline. If the proposed mechanism or benchmark is already covered, narrow the question or change direction. A new project name does not create novelty.

## Recommended research question, subject to that check

**How can an agent platform preserve caller authority and action constraints across heterogeneous runtimes when work delegates, pauses for approval, resumes under changed policy, or recovers after partial execution?**

This is narrower and more testable than “a universal SOTA agent platform.” It connects to observed failures in F01–F05 and to enterprise adoption. Candidate contribution: a precise execution contract plus a fault-aware enforcement/recovery mechanism, evaluated across adapters and realistic tasks. Whether this exceeds existing systems remains unproven.

Provisional title: **Preserving Authority Across Agent Runtime Boundaries Under Delegation and Recovery**. Do not put “first,” “universal,” “secure by construction,” or “SOTA” in a manuscript title without supporting scope and evidence.

## Candidate hypotheses and falsification

| Hypothesis | Experimental comparison | What would refute or weaken it |
| --- | --- | --- |
| H1: Explicit context and attenuation prevent cross-adapter privilege expansion | Same action sequences through plain wrappers, strong gateway baseline, proposed mechanism | Existing baseline achieves the same result with equivalent complexity/overhead |
| H2: Policy-bound approval plus recovery ownership prevents stale authorization/effect replay | Changed policy, expired approval, duplicate resume, kill points, stale workers | Mechanism permits an unauthorized effect, loses a valid action, or assumes unavailable downstream semantics |
| H3: The mechanism preserves legitimate utility at acceptable overhead | Paired benign/attacked tasks and scripted/runtime measurements | High false blocking, unacceptable latency, or utility losses compared with a simple baseline |
| H4: Guarantees transfer across frameworks and domains within a declared capability model | Multiple real adapters, held-out scenarios/domain/model | Success depends on one adapter, benchmark-specific rules, or hidden exclusions |

A rigorous negative result may still be interesting if it identifies a general limitation and supports it with controlled evidence. Merely finding bugs in this repository is useful engineering, but is not automatically a top-tier scientific contribution.

## Formal scope, if used

Define an action event with tenant, caller, delegation chain, arguments, policy version, approval record, state revision, and effect identifier. State invariants such as:

- An executing child's authority is a subset of the authority delegated by the parent and allowed by current policy.
- A hard deny cannot be overridden by an approval record.
- Approval is valid only for its bound action, current permissible authority, and expiry conditions.
- A stale owner cannot commit a state transition after a newer owner has acquired authority.

Specify assumptions: all protected effects traverse a trusted gateway; the credential broker and policy service are trusted; tools respect declared effect/idempotency contracts; the storage system supplies the required consistency; opaque processes have constrained credentials. Do not claim a proof covers arbitrary Python code with unrestricted network credentials. Exactly-once external effects cannot be promised merely by proving a local state-machine invariant.

Use a small executable model or model checker where valuable, then test implementation refinement against traces. A mathematical restatement of ordinary RBAC is not enough; show what the new composition/recovery mechanism adds.

## Experimental artifact

Build on the [benchmark plan](BENCHMARK_PLAN.md). Minimum credible artifact:

1. Versioned definitions and adapters for native Python, LangGraph, and at least one additional real framework; declare unsupported capabilities. A fully governed CrewAI path is preferable to calling `kickoff` and claiming equivalent internal control.
2. Existing deterministic governance scenarios plus independently authored lifecycle/fault cases. Inspect existing suites before claiming a new benchmark.
3. WorkBench and a conversational business-task suite to measure utility, side effects, and interaction quality; AgentDojo for a separate attack/utility axis.
4. A strong policy gateway baseline, closest research baseline where implementable, and matched single-agent/minimal-loop baselines. Repair obvious baseline bugs fairly; do not compare a fixed proposed system only to deliberately broken code.
5. Multiple model families and task domains for transfer claims. Hold out combinations of framework, fault, domain, and task template, rather than splitting near-duplicate cases randomly.
6. Ablations for context propagation, attenuation, approval binding, policy recheck, fencing, and effect reconciliation. Unsafe configurations run solely in isolated synthetic environments.
7. Repeated measurements with uncertainty, per-case artifacts, unsuccessful attempts, resource accounting, and independent reproduction instructions.

Measure correctness and false blocking separately. Report attack success conditional on valid attack opportunities and benign utility on matched tasks. For faults, distinguish “never admitted,” “safely paused,” “effect succeeded but response lost,” “reconciled,” and “unknown.” An unknown external effect is not a success merely because the agent returned text.

## Alternative research tracks

**Adaptive context or coordination.** Potentially closer to ML algorithm research, but requires a specific learned/controller method beyond existing routing/reflection/retrieval. Compare fixed policies, equal-budget single agents, ACE-like adaptation, and task-appropriate multi-agent baselines. Include generalization, ablations, and controller cost. Do not add this track merely to make the architecture sound more advanced.

**Evaluation methodology.** A cross-runtime study may be valuable if it discovers reproducible, general failure patterns and develops a validated measurement method beyond existing governance suites. It needs independent baselines, carefully constructed cases, and evidence that conclusions matter outside Foundry.

**Developer systems study.** A well-engineered platform with an adoption study may fit a systems/software-engineering or agent workshop better than an ML main track, depending on its eventual contribution. Venue choice should follow the result, not dictate inflated claims.

## Manuscript structure

1. Problem and a concrete lifecycle failure, followed by narrowly stated contributions.
2. Related work and a precise difference from the closest method/toolkit/benchmark.
3. Threat model, execution semantics, and definitions.
4. Proposed mechanism and any justified formal properties.
5. Experimental protocol: baselines, tasks, models, budgets, environments, statistical methods.
6. Results: primary endpoint, utility/risk/cost tradeoff, recovery, transfer, and ablations.
7. Failure analysis, unsupported settings, threats to validity, and limitations.
8. Broader impacts and reproducibility/artifact details.

Write results only after running experiments. Keep placeholders labeled as planned; do not draft invented numerical improvements or acceptance claims. Brand/product material belongs outside the scientific contribution.

## Submission readiness gates

- The prior-art matrix identifies an actual gap and a defensible contribution.
- The nearest baseline is reproduced, configured fairly, and documented.
- The main claim survives ablation and the predeclared held-out evaluation.
- Raw results and scorer reproduce every table/figure, including failure counts and cost.
- An independent reader can reproduce a representative subset from pinned artifacts.
- Assumptions, benchmark limitations, contamination risks, judge errors, and unsafe failure modes are disclosed.
- The actual venue edition's anonymization, artifact, ethics, authorship, dual-submission, and AI-use rules are checked before submission. Authors verify every citation and take responsibility for all claims.

Product progress and publication progress should reinforce each other, but neither guarantees the other. A startup can succeed with excellent engineering without a novel algorithm; a strong paper can reveal a narrow result without making a universal enterprise platform.

# A concrete 2026 research direction

**Follow-up status (2026-09-28):** consult [CURRENT_STATUS.md](CURRENT_STATUS.md), [live results](LIVE_BENCHMARK_RESULTS.md), and [the updated novelty gate](RESEARCH_GATE_20260928.md). Proposed requirements below are not all implemented; original measurements remain dated evidence.

**Proposal, not a novelty or performance claim.** The goal is a useful platform with one clearly evaluated research contribution. “Better than everything” is not a coherent scientific objective: different systems optimize different tasks, risks, and costs.

## What must become better

The platform needs correct authority propagation, durable state, isolated execution, accurate evaluation, and an easy developer path. These are essential engineering requirements even if they produce no new scientific result. The research should then target a measurable weakness left by strong existing approaches.

The recommended candidate to investigate is **execution-state-aware adaptive verification**: selecting the evidence and verification needed before a consequential action, and invalidating earlier verification when its underlying assumptions change during delegation, interruption, or recovery.

This builds on the common execution contract in [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md). It adds a possible algorithmic question; it does not replace the mandatory security repairs.

## Why “adaptive verification” alone is not new

The literature search found close work, including 2026 publications. These are required comparisons, not evidence that the proposed design already outperforms them.

| Prior work | Relevant overlap | Question the proposed study must answer |
| --- | --- | --- |
| [SABER](https://arxiv.org/abs/2512.07850) | Verification around mutating steps and context cleaning | Does execution-state-dependent revalidation add value over mutation gating? |
| [Pro2Guard](https://arxiv.org/abs/2508.00500) | Proactive risk prediction and intervention | Is there a distinct benefit beyond predicted-risk thresholds? |
| [AgenTRIM](https://arxiv.org/abs/2601.12449v2) | Adaptive tool filtering and status-aware checks | Does evidence validity under delegation/recovery address a separate limitation? |
| [ActGov](https://arxiv.org/abs/2609.24446v2), September 2026 | Task-scoped action authorization, dynamic policies, verified policy construction | Which proposed guarantees or scenarios are not already handled? |
| [The Verifier Tax](https://arxiv.org/abs/2603.19328) | Cost and safe-completion consequences of runtime mediation | Can the proposed method improve safe completion, not just blocking rate? |
| [Strategic Verification for Long-Running LLM Agents](https://www.preprints.org/manuscript/202608.2057), August 2026 | Budgeted adaptive verifier allocation | Strong conceptual overlap. Its reported evaluation is illustrative/simulated and the source is an unreviewed preprint; independently assess method and evidence |
| [Safety Testing LLM Agents at Scale](https://arxiv.org/abs/2607.01793) | Adaptive testing and outcome verification grounded in environment evidence | Reuse or compare the testing approach; do not relabel evidence-based scoring as new |

The initial extension used abstracts/overviews. The [closer-source follow-up](CLOSEST_REPOSITORIES.md) now examines selected methods in ActGov v2, AgenTRIM v2, and SABER v1, plus seven pinned repositories. Remaining papers and artifacts still require deeper comparison. **The novelty gate is currently open, not passed.** If the proposed distinction is already solved, abandon or revise it before investing in a main-track manuscript.

**Concrete novelty exclusion:** Microsoft Agent Governance Toolkit already implements action-digest binding, policy/approval-chain version checks, expiry, and one-time approval consumption in its [execution validator](https://github.com/microsoft/agent-governance-toolkit/blob/6b644564d112b879e48183bc1655fcf73c86d23e/agent-governance-python/agent-mesh/src/agentmesh/governance/approval_protocol/coordinator.py#L311). These controls must be a baseline or integration, not the claimed invention. The remaining hypothesis concerns task-evidence validity under external changes and incomplete dependency information; that distinction remains to be demonstrated.

## Concrete example

A procurement agent retrieves a quote and checks a spending limit. A specialist recommends a purchase, and a human approves it. Before execution, the quote changes, the employee's authority is revoked, or another worker already places the order. A reusable “approved” flag or cached verifier score is no longer adequate.

Always repeating every retrieval and expensive model-based verification is a strong safe baseline, but can add latency, cost, and user friction. The research question is whether the system can **soundly identify what evidence became invalid and selectively refresh it**, while preserving hard authorization and recovery checks.

The basic concurrency/control ideas are established systems concepts. Potential novelty would need to come from a precise method for incomplete agent evidence/dependency information, a validated decision rule, or a general empirical result across realistic agents. Merely caching checks or adding version numbers is insufficient.

## Proposed mechanism

Maintain an action evidence record containing:

- The proposed action, canonical arguments, effect class, caller and delegated authority.
- The source records/versions and factual preconditions used to justify it.
- Policy and approval versions, tool schema/version, and relevant run/state revisions.
- Verification results, their scope, verifier identity, calibration status, cost, and expiry.
- Dependencies between evidence and preconditions, including an explicit `unknown` dependency state.

Before execution, hard authorization, tenant isolation, required approval, and idempotency checks always run. They are not optional choices of a learned policy.

For task-correctness uncertainty, a controller selects among refreshing a source, deterministic validation, simulation/dry run, a calibrated model verifier, human clarification, or deferral. Reuse is allowed only for evidence whose declared dependencies remain valid. Unknown dependencies trigger conservative revalidation. Source updates, changed arguments, policy/identity changes, expiry, handoff, and recovery produce typed invalidation events.

Start with a transparent rules-based controller. If it leaves a measurable gap, train or calibrate a small cost-aware controller from development traces. Do not learn permissions from task-success rewards. Fail closed on mandatory authorization; separately measure unnecessary deferrals and their utility cost.

## Objective and limits

For authorized candidate actions, optimize verified task completion and verification cost under predeclared latency/budget constraints. Track harmful effects separately; do not hide them inside a weighted score. A constrained objective may include an empirical risk threshold, but a fitted model's confidence is not a hard safety guarantee. Distribution shift, missing dependencies, verifier correlation, and unobservable external state are explicit failure modes.

No theorem is claimed here. Any formal result must state assumptions about observable versions, complete dependency declarations, trusted gateways, storage consistency, and downstream effect semantics. Real systems often violate these assumptions; the experiment must quantify the consequence.

## Minimum experiment

Compare on the same model, tools, tasks, and budgets:

1. Mandatory hard controls with no optional task verifier.
2. Verification before every mutating action.
3. Risk-triggered verification without evidence tracking.
4. Simple evidence cache with full invalidation on any state change.
5. Dependency-aware deterministic revalidation.
6. Proposed adaptive method, only if learning adds value beyond 5.
7. Closest available research implementation after the novelty review.

Include an existing action-bound approval implementation, with its actual store and execution boundary, in the engineering baselines. A one-use approval record alone does not solve the crash between a downstream effect and receipt persistence. Compare real effect counts and downstream idempotency semantics.

Run both original public tasks and a separately labeled extension with controlled quote/data changes, policy revocation, delayed approvals, duplicate deliveries, partial tool failures, and worker restarts. **Do not submit modified tasks as official WorkBench or τ-bench scores.** Publish the perturbation generator, frequencies, seeds, and independent review of scenario validity.

Use held-out task templates, event combinations, frameworks, and model families. Measure safe verified completion, unwanted actions, stale-evidence actions, unnecessary deferrals, verification calls/tokens, total cost, p95 latency, and recovery correctness. Stratify results by change type and horizon. Include ablations removing dependency tracking, invalidation, and adaptation independently.

## Go / no-go decision

Continue toward a paper only if the study reveals a distinction beyond the closest prior work and achieves a reproducible improvement over strong simple baselines. A useful result might be lower verification cost at matched safe completion, or higher safe completion under a fixed budget. Predeclare a meaningful effect size after a pilot variance/cost analysis; do not choose it after looking at test results.

If deterministic revalidation matches the learned controller, report that result rather than add unnecessary ML. If every benefit disappears against the strong baseline, retain the engineering improvement and choose a different scientific question.

The platform can support this research across runtimes while remaining useful to startups. The paper should study one mechanism carefully; the product can contain many additional practical features.

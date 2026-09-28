# Complete review and improvement plan

Date: 2026-09-27. Baseline: `2623ea1b2fe2d93beb1976c0d4e2449891b6900a`.

## Objective

Assess whether Agent Foundry can become a dependable Python platform for startups and enterprise applications, with native Python, LangGraph, LangChain, and CrewAI integration. Determine whether the layers are adequate, identify real security/reliability gaps, compare research and similar repositories, and define the experiments required for a defensible 2026-era performance claim and a serious research paper.

The original deliverable was this review package. Subsequent authorized work implemented four repair iterations and ran bounded live experiments. See [current status](CURRENT_STATUS.md) for completed work; the full roadmap below remains only partly implemented.

The expanded [missing-capability backlog](MISSING_CAPABILITIES.md) covers the user's subsequent speed, adaptability, context, scale, enterprise, and SOTA requirements. The [2026 research design](RESEARCH_DESIGN_2026.md) makes the publication ambition concrete without presenting untested ideas as discoveries.

## Review method and completed work

| Workstream | Status | Evidence / result |
| --- | --- | --- |
| Repository baseline and inventory | Complete for the checked-out tracked snapshot | All 225 files listed and hashed in [inventory](evidence/file-inventory.json) |
| Source, docs, examples, tests, configuration and diagrams | Broad scan complete; deeper inspection concentrated on execution/security | [Coverage ledger](FILE_COVERAGE.md); no claim of every integration executed |
| Layer and runtime assessment | Complete for review scope | [Repository review](REPOSITORY_REVIEW.md), native/LangGraph/LangChain/CrewAI distinction |
| Focused behavior tests | Complete | 208 passing cases |
| Extended selected tests | Complete with environment limitations | 224 passes, 2 skips, 5 missing-dependency failures |
| Deterministic defect reproductions | Complete | 16 recorded observations, including one correct cross-runtime comparison case |
| Runtime microbenchmark | Complete, one local scripted run | [Raw output](evidence/runtime-microbenchmark.txt); insufficient for general speed claims |
| Research and comparable GitHub projects | Complete for a scoped first review | [Research matrix](RESEARCH_AND_COMPARISON.md); reading depth and limits disclosed |
| Public task benchmarks and live models | Bounded WorkBench experiments executed; fresh comparison incomplete | [Live measurements](LIVE_BENCHMARK_RESULTS.md); no leadership claim |
| Real-service recovery/load/security review | Planned, not run | Requires deployment test environment and independent assessment |
| Product and publication roadmaps | Complete as plans | [Architecture](TARGET_ARCHITECTURE.md), [publication plan](PUBLICATION_PLAN.md) |

The existing top-level review plan was treated as a prior hypothesis list. Findings were checked against current code; fixed historical issues were not repeated as current defects. In particular, native topology state hooks, Redis inflight idempotency reservation, and Postgres pooling exist now.

## Architecture review checklist

For each layer, record the public API, concrete implementations, entry points, data/authority boundaries, failure behavior, test coverage, and operational owner. Ask whether the behavior is mandatory, optional, or merely documented. Trace a request through admission → context → model → tool → checkpoint → response and through child delegation, streaming, approval, cancellation, and retry.

Explicitly evaluate:

- Contracts and extension seams; whether a third-party adapter needs hidden graph assumptions.
- Native/LangGraph behavior parity and the governance guarantees of LangChain/CrewAI bridges.
- Identity propagation, deny precedence, credential scope, tenant separation, network and code isolation.
- State ownership, stale writers, checkpoint schema changes, partial side effects, and distributed recovery.
- Retrieval authorization, knowledge freshness, memory correction/deletion, context limits, and poisoning.
- Evaluation oracles, missing measurements, benchmark leakage, judge calibration, and failure cost.
- Tracing, event lineage, budgets, cost attribution, alerting, and incident recovery.
- Packaging, supported Python/dependency versions, examples, CI, deployment profiles, and upgrades.
- Startup onboarding, application portability, industry requirements, and research novelty.

## Implementation sequence

Effort is a rough planning range in **engineer-weeks**, not a delivery commitment. Workstreams overlap and need a more precise estimate after design. A two-engineer team plus part-time security/research help should plan multiple months for a credible beta and research artifact, not assume that all work below fits a weekend. Publication has an independent uncertainty/timeline.

| Phase | Priority / dependencies | Concrete output | Acceptance gate | Indicative effort |
| --- | --- | --- | --- | --- |
| 0. Freeze truth | First | Reproductions, capability matrix, corrected security/performance claims | Review findings reproduced and tracked; no unsupported marketing claims | Review delivered; triage 0.5–1 |
| 1. Execution and authority | P0; phase 0 | Envelope propagation, typed PDP, approval binding/recheck, child authority, governed adapters | F01–F04 regression tests pass across promised paths | 3–5 |
| 2. Isolation and ownership | P0; contract agreed | Trusted-code restriction, isolated executor path, timeout semantics, state revisions/fencing | F05–F06 and real process-kill/stale-worker tests pass | 3–5 |
| 3. Truthful evaluation and data | P1; contract stable | Missing-data metrics, end-state oracles, cost accounting, cache keys, uniform context controls | F07–F10 tests and three domain datasets pass | 3–5 |
| 4. Production reference deployment | P1; phases 1–3 | Authenticated API, workers/queue, secrets, telemetry, approval UI, backups, recovery/rollback | Startup production profile and security review pass | 4–6 |
| 5. Public benchmark adapters | Can begin scorer work early | WorkBench, conversational tool-use, AgentDojo, governance runner; pinned baselines | Baseline reproduced; held-out evaluation ready | 3–5 plus compute |
| 6. Adoption and expansion | After stable pilot | Two starter applications, docs, compatibility CI, industry-pack template | Independent developers build and operate pilot applications | 2–4 plus pilot time |
| Research track | Literature gate before novelty claims | Specific mechanism/study, ablations, reproducible artifact, paper | [Publication gates](PUBLICATION_PLAN.md) satisfied | Scope after prior-art investigation |

Do not postpone P0 controls in order to add more topologies. Conversely, do not rewrite working modules wholesale: fix shared boundaries, preserve tested primitives, and remove duplicated enforcement when a common service is in place.

## Suggested issue backlog

| ID | Work item | Suggested owner role | Required evidence |
| --- | --- | --- | --- |
| AF-01 | Make execution context mandatory through all public methods/transports | Runtime | Entry-point × runtime negative tests |
| AF-02 | Typed policy composition and approval binding | Security/runtime | Deny-before/after-approval, expiry, changed args, revoked role tests |
| AF-03 | Attenuated delegation and caller lineage | Runtime/security | Every topology, nested depth, bridge, remote caller matrix |
| AF-04 | Governed tool adapters and capability negotiation | Integrations | LangChain/CrewAI version-pinned tests and documented opaque mode |
| AF-05 | Versioned state ownership and effect reconciliation | Infrastructure | Stale writers, kill points, lease expiry, duplicate delivery |
| AF-06 | Real code isolation and cancellation semantics | Infrastructure/security | Bounded resources, cleanup, termination, egress tests |
| AF-07 | Uniform authorized retrieval and memory lifecycle | Data | Tenant, ACL revocation, poisoning, update/delete tests |
| AF-08 | Complete model cache contract | Model gateway | Tool/schema/model/policy/tenant invalidation cases |
| AF-09 | Measurement coverage, end-state oracles, failure accounting | Evaluation | Empty/missing oracle tests; reference business-state datasets |
| AF-10 | Run events, all-call cost, tenant trace attribution | Observability | Trace/ledger reconciliation and alert demonstrations |
| AF-11 | Harden HTTP/MCP/A2A/channel/deployment integrations | Platform/security | Auth, deduplication, bounds, compatibility, deployment review |
| AF-12 | Baseline-preserving public benchmark adapters | Research/evaluation | Official scorer agreement and raw per-task artifacts |
| AF-13 | Startup scaffold, debugging flow, support matrix | Developer experience | Independent timed onboarding and production pilot |
| AF-14 | Closest-prior-art replication and publication design | Research | Explicit novelty table and falsifiable hypotheses |

These are local planning identifiers, not GitHub issues created or messages sent.

## Conformance matrix to implement

Run the same contract suite over native, LangGraph, and each adapter claiming full support. Cover sync, async, streaming, batch, scheduled invocation, HTTP, and child calls. Include react/single, supervisor, swarm, debate, blackboard, fan-out, DAG, and agent-as-tool where supported.

Mandatory cases: missing/forged principal; restricted caller with privileged worker; cancelled before start/during tool; expired deadline; depleted parent budget with fan-out; hard deny plus approval; repeated/stale resume; argument changes; changed policy version; duplicate event/action; stale writer; worker death around a side effect; revoked knowledge access; cache request mismatch; policy backend unavailable; unavailable adapter capability; malformed structured output; telemetry failure; tool timeout after effect.

Use deterministic model stubs for contract behavior. Use real services for storage/network/fault guarantees. Use live models only for behavioral quality and attack/utility experiments. This separation makes failures diagnostic and keeps CI affordable.

## Enterprise and startup acceptance

**Developer target, proposed:** a new user can install, run a synthetic example, inspect a trace, and modify a typed tool in under 30 minutes. Measure this with 5–10 independent developers; this is an onboarding target, not a proven statistic.

**Application target:** support/refund, procurement/internal operations, and knowledge-access examples each ship with permissions, test data, failure scenarios, human escalation, eval gates, and deployment instructions. Require business-owner-defined acceptance thresholds; do not invent one universal 95% target for all risk classes.

**Operational target:** publish load envelopes and SLOs for a specified machine/deployment, verify recovery/backups, and reconcile costs. Set targets from a pilot workload before optimizing. A local requests/second claim cannot stand in for a service commitment.

**Trust target:** deployment validation prevents selection of unsupported controls; documentation states integration guarantee level; critical negative tests fail the release; failures and limitations remain visible.

## Decision gates

1. **Can this be a useful framework?** Yes: existing implementations and test coverage provide a base.
2. **Can users trust current enterprise guarantees blindly?** No: locally reproduced authorization/context/durability gaps block that promise.
3. **Is the layer inventory enough?** Broadly yes as categories; enforcement, lifecycle consistency, and an operational control plane need work.
4. **Can it support all four requested ecosystems?** As a design goal, yes; today two are first-party runtimes and the others are partial integrations.
5. **Is it SOTA?** Not established. The current snapshot has no reproduced public task-benchmark win.
6. **Can it support a high-standard paper?** Possibly as an artifact, after a genuine research question and rigorous comparative evidence. Acceptance and novelty are not guaranteed.

The next concrete engineering task is AF-01/AF-02/AF-03: preserve the request's authority and controls, and make denial/approval composition correct. Those repairs improve both the product and the validity of subsequent benchmark experiments.

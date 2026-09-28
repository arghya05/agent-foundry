# Consolidated missing capabilities and implementation backlog

**Repair evidence:** [current status](CURRENT_STATUS.md) and [implementation](IMPLEMENTATION_20260928.md). A repaired subproblem does not close the wider capability; the acceptance column retains the full target.

This is the expanded roadmap requested during the review. It combines source findings, product requirements, and research candidates. **Adding an item here does not implement it or establish its novelty.** “Missing” includes incomplete or inconsistently enforced capabilities; existing primitives are credited rather than ignored.

Statuses: **defect** = demonstrated/source-confirmed issue; **partial** = primitives exist without the complete guarantee; **unestablished** = the required evidence was not found or reproduced; **research** = a hypothesis requiring experiments. P0 precedes enterprise promises; P1 is production beta; P2 is product expansion; R is gated research.

| ID | Capability / present status | Required implementation | Acceptance evidence | Priority |
| --- | --- | --- | --- | --- |
| C01 | Execution context — local repair tested; remote partial | One envelope propagated through every public/child path | Cancellation, deadline, budget, scope and trace matrix | P0 |
| C02 | Policy composition — hard-deny repair tested | Typed outcomes; mandatory hard-deny checks before approval | Denied tools never execute after confirmation | P0 |
| C03 | Delegation — local attenuation tested; remote partial | Caller/service distinction and authority attenuation | No privilege expansion across topologies/adapters | P0 |
| C04 | Approval lifecycle — partial | Durable action-bound consent, authorized approver, expiry, recheck | Changed arguments/roles/policy and replay rejected | P0 |
| C05 | LangChain governance — explicit governed export tested; approval resume partial | Governed wrapper added; raw opt-in explicit; authenticated resume still needed | Denied tools have no effects; approval stops tested, authenticated resume pending | P0 |
| C06 | CrewAI interoperability — partial | Version-pinned adapter, inner-action hooks or explicit opaque mode | Conformance suite and supported-capability manifest | P1 |
| C07 | Third-party engine seam — partial | Runtime interface independent of compiled-graph shape | An external adapter works without editing core topology switch logic | P1 |
| C08 | Capability negotiation — unestablished | Validate requirements against adapter/deployment capabilities | Unsupported required controls fail at deployment | P1 |
| C09 | Shared state ownership — defect | Versioned writes or fenced owner; refresh at ownership changes | No lost state under stale-worker and interleaving tests | P0 |
| C10 | Durable external effects — partial | Action journal, stable idempotency, reconciliation | Kill before/after effect does not silently duplicate/lose outcome | P0/P1 |
| C11 | Checkpoint evolution — unestablished | Schema versioning and tested migrations/rollback | Resume old run through compatible deployment upgrade | P1 |
| C12 | Scheduling/workers — partial | Durable queue/timers, leases, bounded workers, backpressure | Restart preserves scheduled work and ownership | P1 |
| C13 | Cancellation/timeout — cooperative checks tested; hard stop absent | Admission checks, cooperative cancel, isolated hard-stop option | Bounded response and accurately reported residual effects | P0 |
| C14 | Code sandbox — defect | Trusted-code restriction plus isolated executor service | Resource, filesystem, network, termination tests | P0 where enabled |
| C15 | Tool side-effect contract — partial | Read/idempotent-write/irreversible classification and retry policy | Retry cannot duplicate irreversible effects | P1 |
| C16 | Model cache — request identity repaired; tenant partition partial | Canonical complete request and permitted tenant partition | Schema/model/settings/context changes invalidate appropriately | P1 |
| C17 | Model/provider compatibility — partial | Version matrix, structured tool/output contract tests | Supported providers pass identical semantics suite | P1 |
| C18 | Parent-child budget — partial | Reservation and accounting over all router/worker/critic calls | Fan-out cannot evade parent limit; costs reconcile | P1 |
| C19 | Tool credential broker — partial | Per-tenant/action-scoped credentials with rotation | Worker receives only the credential scope required | P1 |
| C20 | Network egress — partial | Destination/redirect/DNS enforcement outside tool code | Undeclared/private destinations denied in isolated tests | P1 |
| C21 | Knowledge admission — filter bypass repaired; complete ACL/provenance partial | Uniform ACL/provenance/trust pipeline for all context sources | No alternate source bypasses authorization/handling | P1 |
| C22 | Memory lifecycle — partial | Update/delete/expiry, source versions, correction/quarantine | Deleted/revoked/poisoned memory no longer used | P1 |
| C23 | Context compaction — partial/unestablished | Task-aware summaries and artifact offload with provenance | Long-horizon utility at fixed token budget; no dropped approvals | P1/P2 |
| C24 | Hybrid retrieval/reranking — unestablished | Optional measured retrieval policies | Gains on held-out retrieval/outcome tests justify cost | P2 |
| C25 | Temporal/graph memory — partial | Versioned facts and temporal queries if applications need them | Correct answers after contradictory updates and time changes | P2/R |
| C26 | Evaluation semantics — I004 repairs tested | Missing-data states, oracle coverage, failure-cost retention | Empty labels cannot report measured success | P1 |
| C27 | Business-state evaluators — partial | Domain end-state checks and unwanted-effect checks | Text success cannot mask incorrect database changes | P1 |
| C28 | Judge reliability — partial | Calibration set, blinded grading, human audit and disagreement | Reported judge accuracy/agreement and known failures | P1 |
| C29 | Public benchmark adapters — WorkBench measured; other suites pending | Thin, version-pinned official-harness integrations | Official baseline/scorer reproduced; raw Foundry results | P1 |
| C30 | Statistical comparison — exploratory paired estimates; repeated confirmation pending | Paired repeated trials, uncertainty, exclusions and budget controls | Rerunnable tables; no cherry-picked attempts | P1/R |
| C31 | Security evaluation — partial | Injection, delegation, poisoning, tenant and approval suites | Clean utility plus attack/effect outcomes | P0/P1 |
| C32 | Trace lineage — partial/defect | Per-run/child events and current request identity | Every effect traced to caller, deployment and decision | P1 |
| C33 | Cost/outcome metrics — partial/defect | Incremental usage and distinct operational/business outcomes | Failure costs retained; no repeated cumulative charging | P1 |
| C34 | Monitoring operations — partial | Exporters, dashboards, SLO alerts and runbooks | Demonstrated incident detection and recovery | P1 |
| C35 | Audit integrity — partial | Access/retention/key handling and tamper evidence | Corruption detected; retained records reconstruct actions | P1 |
| C36 | Multitenant load — unestablished | Quotas, partitioning, fair admission and bounded fan-out | Noisy tenant does not starve isolated workloads | P1 |
| C37 | Fleet resilience — unestablished | Backups, drain/restart, recovery drills, storage failure handling | Published recovery envelope and loss semantics | P1 |
| C38 | HTTP/MCP/A2A/channel parity — partial | Shared admission, scopes, deduplication and flow control | Per-channel identity/security tests | P1 |
| C39 | Supply-chain/release hygiene — partial | Locked deployment dependencies, compatibility CI, provenance | Reproducible build and tracked audit exceptions | P1 |
| C40 | Control plane — partial | Deployment registry, tenant management, approvals, run search, rollback | One working local-to-production workflow | P1 |
| C41 | Startup scaffold — partial | Typed starter agent + policy + dataset + tests + trace + deploy recipe | Independent user completes onboarding and diagnoses a failure | P1 |
| C42 | Industry packs — partial | Domain connectors, constraints, oracles, escalation and runbooks | Domain-owner acceptance on representative workloads | P2 |
| C43 | Adaptive model/context routing — partial/research | Transparent baseline, then bounded learned controller if useful | Quality/cost frontier improves on held-out tasks | P2/R |
| C44 | Adaptive agent topology — research | Task-conditioned routing with shared budgets and stopping rules | Beats strong single/static baselines under matched budgets | R |
| C45 | Evidence-dependent verification — research | Dependency tracking, invalidation, selective revalidation | Novelty gate plus safe-completion/cost improvements | R |
| C46 | Feedback and improvement loop — partial | Versioned datasets, reviewed candidate updates, canaries/rollback | Improvement persists without test leakage or policy drift | P1/P2 |
| C47 | Portability guarantee — unestablished | Shared executable conformance specification | Supported adapters preserve declared invariants | P1/R |
| C48 | Independent assurance and adoption — unestablished | External review, documented pilots, contributor/release process | Independent reproduction and successful customer operation | P1/P2 |

## Recommended order for a small team

**Foundation:** C01–C05, C09, C13, and C14. These correct misleading control behavior and unsafe execution assumptions. Include regression tests with the fixes.

**Dependable beta:** C10–C12, C15–C22, C26–C39. Build the minimal coherent subset needed for the two chosen pilot applications; do not implement every optional backend simultaneously.

**Adoption:** C06–C08, C40–C42, C46–C48. Turn verified primitives into a reliable user journey and honest support matrix.

**Research and frontier performance:** C23–C25 and C43–C45 only after profiling and the prior-art gate. Choose one mechanism to evaluate deeply. Add features only when held-out evidence justifies their complexity.

## What can be inherited from existing infrastructure

Use established identity/secret/storage/telemetry/sandbox services through adapters. Evaluate mature workflow infrastructure for durable timers and long-running recovery. Foundry's value need not come from reimplementing databases, identity providers, schedulers, or dashboard products.

Conversely, do not outsource the definition of your guarantees: envelope semantics, action lifecycle, adapter capability levels, evaluation contracts, and developer documentation must remain coherent across integrations.

## What would justify the next release claims

| Claim | Required evidence |
| --- | --- |
| “Supports native, LangGraph, LangChain, CrewAI” | Versioned capability matrix and functioning integration tests for each stated support level |
| “Enterprise-ready for profile X” | Deployment-specific security, recovery, load, upgrade and application acceptance tests |
| “Fast” | Repeated measured latency/throughput under specified workloads and hardware |
| “Adaptive” | Clearly defined controller, bounded changes, held-out advantage over fixed alternatives |
| “Robust evaluation” | Reliable oracles, missing-data coverage, calibration, uncertainty and reproducible raw results |
| “SOTA on benchmark X” | Current comparable baselines, pinned protocol, measured improvement and transparent tradeoffs |
| “Novel research contribution” | Full closest-prior-art analysis plus a new method/insight supported by rigorous evidence |

These are acceptance conditions, not labels granted by completing a feature checklist.

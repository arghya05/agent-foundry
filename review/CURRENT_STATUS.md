# Current implementation and research status

Updated 2026-09-28. The original review describes commit
`2623ea1b2fe2d93beb1976c0d4e2449891b6900a`; it is preserved as a dated baseline.
This document records the subsequent repairs and the remaining release gates.

## Implemented and tested

- I001: hard denials precede approval; approval is an explicit typed signal;
  model cache identity includes the complete request and options.
- I002: native and LangGraph public execution paths propagate request identity,
  narrowed authority, budgets, model limits, cooperative cancellation and
  deadlines through nested and threaded execution. Live handles are not serialized.
- I003: knowledge passages use the existing injection/PII filtering boundary;
  an empty request tenant no longer falls back to a privileged configured tenant.
- I004: missing evaluation oracles remain unmeasured; metric coverage is explicit;
  empty/unmeasured default gates fail; costs use per-case budget deltas and retain
  known failure costs. See [migration notes](EVALUATION_MIGRATION.md).

The full local test invocation records **661 passed, 25 skipped**. Type checking
reports no issues in 59 source files. This covers one Python 3.11/macOS environment,
not every supported Python version, integration, database or deployment.
See [implementation details](IMPLEMENTATION_20260928.md) and
[the final test log](evidence/iteration-006-full-suite.txt).

## I005 enterprise follow-up

[Governed exports](IMPLEMENTATION_005_GOVERNED_ADAPTERS.md) now retain the current
request's action boundary in a tested LangChain tool. Raw conversions require
explicit opt-in; opaque CrewAI internals remain unsupported. Request-specific
autonomy and approval policies are enforced in both runtimes. Approval-required
exports stop before effects; authenticated durable approval resume is still open.

The initial release was pushed as commit `2e8a240`; I005 was pushed as `9718a9d`.
The unfinished paper remains
local while enterprise work is prioritized. Subsequent commits record individual
validated hardening steps; they do not close the whole deployment backlog.

## I006 native state consistency

[I006](IMPLEMENTATION_006_STATE_CONSISTENCY.md) repairs stale single-agent history,
missing update persistence and mutable returned state. Shared Memory and local
SQLite support atomic version checks; SQLite tests exercise real process exit
and continuation. Conflicts do not automatically retry. Outer topology state,
Redis/Postgres version checks, fenced ownership and external effect reconciliation
remain open. These changes do not establish exactly-once tool execution.

## Benchmark outcome

See [live measurements](LIVE_BENCHMARK_RESULTS.md). Seven archived campaigns contain
578 finished attempts, including rejected setup requests and all scored failures.
The 12-task development and two 60-task diagnostic comparisons completed. The
fresh baseline stopped at 179/180 attempts after exhausting its bounded transport
continuations. The predeclared fresh guidance comparison was not run. No failed
task was selectively retried. No full 690-task Foundry result exists.

The ledger records $9.27733950 in usage-based upper estimates plus $7.442336 in
unknown-request reservations: **$16.71967550 accounted against the shared $25 cap**.
Reservations are not asserted to be actual charges; cached input is conservatively
priced at the base rate. Invoice reconciliation remains outstanding.

The experiments do not establish a significant framework advantage, guidance
improvement, broad multi-agent superiority, or public benchmark leadership.
Foundry native and Foundry LangGraph are two implementations of this framework;
they are not independent competing-framework baselines.

## Enterprise work still required

The layer inventory is broad, but implementation depth is uneven. The principal
gaps are authenticated approval resume for external exports and opaque CrewAI internals; distributed budget
reservations and authenticated remote context; fenced state ownership and durable
effect reconciliation; action-bound, expiring, authenticated approvals; true process
isolation; complete retrieval authorization and provenance; load/recovery tests;
and production monitoring/incident drills. Applications must still define resource
permissions and business outcome oracles. The framework cannot guarantee arbitrary
industry readiness or safe deployment without those application decisions.

The detailed 48-item [capability backlog](MISSING_CAPABILITIES.md) separates repairs,
partial features and research. Context propagation does not make untrusted Python
safe. Cooperative cancellation does not terminate a tool already running.

## Research gate

The user wants an original, conference-level contribution, not just a paper-shaped
repository. That objective remains **unachieved**. The current repairs are useful
engineering, and the optional execution prompt is not an original algorithm.
The continued search found additional close work on transaction admission and
stateful handoffs; see [the novelty update](RESEARCH_GATE_20260928.md).

Next scientific milestones are: establish a narrow distinction from those methods;
reproduce the nearest available artifact; implement a bounded candidate; predeclare
matched baselines, ablations, transfer settings and stopping rules; and obtain
repeated public-task evidence with uncertainty. A negative result must reject or
revise the hypothesis. Neither acceptance nor a benchmark win is a promised output.

The local LaTeX package is an unfinished empirical working draft, deferred from this
release. At the owner's latest instruction, release and enterprise hardening precede
further research/paper work. It is not a
submitted or accepted paper and cannot substitute for completing these experiments.
The owner reaffirmed GitHub push authorization after the patent disclosure concern
was explained. No patent filing, arXiv submission or Hugging Face upload is implied.

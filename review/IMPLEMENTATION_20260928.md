# Execution controls, retrieval and evaluation repairs

These are tested engineering improvements, not a claim of research novelty or
universal enterprise readiness. The original review remains an immutable account
of the starting snapshot.

## What changed

**Request controls now reach both runtimes.** The public synchronous, asynchronous,
streaming, resume, engine-adapter, fan-out, DAG, and batch boundaries establish a
process-local execution scope. Cancellation and deadlines are checked before new
work. Native execution uses the same request budget, model allowlist, tool policy,
and memory scope resolvers as LangGraph. Empty, disjoint, or malformed model
allowlists fail before a provider request rather than reverting to default models.

**Delegation preserves the caller.** Native supervisor/worker paths, parallel
fan-out, tool-dispatch pools, timeout workers, and nested agent tools inherit the
scope. Nested public calls cannot change the calling user/tenant, enlarge its
roles, loosen a parent tool policy, or escape its request budget by choosing a new
thread ID. Router calls charge an explicit request budget too. Persisted identities
reflect the effective narrowed identity. Approval resume rechecks current caller
roles, cancellation, deadline, and policy.

**Live handles stay out of checkpoints.** Budget locks and cancellation events
remain process-local. Their identity and behavior survive thread handoffs without
serializing Python synchronization primitives. Streaming restores the consuming
thread's scope between yielded items. `Run.cancel()` signals ongoing execution and
keeps the cancelled lifecycle status when cooperative cancellation is observed.

**Knowledge uses the existing filtering boundary.** Knowledge passages now pass
through the same injection-marker and PII filters before compression and assembly.
An explicitly empty tenant ID no longer selects the configured privileged tenant.
These fix demonstrated bypasses; regex filtering is not a complete injection defense.

## Validation and preserved failures

- Initial execution-control regressions: **40 failed, 1 passed**.
- First repair: **41 passed**.
- Expanded initial suite: **165 passed, 2 test-construction failures**; the test used
  an unsupported agent constructor argument. It was corrected to set the tool's timeout.
- Expanded controls and batch suite: **67 passed**, including real sync/async/stream
  paths, mid-request cancellation, resume role revocation, nested timeout execution,
  shared router/worker budgets, parallel identity propagation, and scope cleanup.
- Retrieval regressions: **2 failed before repair**.
- Combined selection: **333 passed, 2 skipped, 1 deselected** after documenting an
  unavailable Chroma dependency. The original missing-dependency failure is retained.
- Full suite after installing required local extras and allowing localhost fixtures:
  **602 passed, 25 skipped** at I003. The final suite after I004 records
  **620 passed, 25 skipped**. Missing SDK/optional dependency and sandbox failures
  from earlier attempts are retained. This is one Python 3.11/macOS environment,
  not the full CI Python/service matrix.
- Type checker: **58 source files, no issues** after installing the Redis dependency.

See [the evidence index](evidence/README.md) and raw `iteration-002*`, `iteration-003*`,
`full-suite-20260928*`, and `typecheck-20260928*` files. WorkBench behavioral scores
are separate from these scripted contract tests.

## I004: evaluation integrity

Missing task/tool/KPI oracles remain unmeasured. Gates require explicit coverage,
reject empty or unlabeled default evaluations, and reject nonfinite measurements.
Saved baselines preserve missing values under schema v2; the CLI reports them
without formatting failures. Costs are incremental budget deltas and retain known
failure charges with an explicit completeness flag. Seventeen new regressions
failed before repair. The final full suite records 620 passes and 25 skips.
See [migration and accounting limits](EVALUATION_MIGRATION.md).

## Compatibility and deployment boundary

Supply the intended controls on **every run/resume**. Python context variables do
not cross process, network, or durable job boundaries. External workers must
reconstruct controls from authenticated, trusted inputs and shared budget services.
Direct graph callers retain the older `request_*` state interface; the public
`Agent`/`Workflow` interfaces supply the process-local scope automatically.

Cancellation is cooperative: it prevents newly admitted work, but does not kill an
already running model/tool call. `RunBudget.spend()` accounts reported usage after
a call; it is not a predictive dollar reservation. The benchmark transport has a
separate conservative reservation ledger. A distributed budget reservation service,
fenced state ownership, durable effects, authenticated action-bound approvals,
service-identity/capability separation, and cross-process revocation remain work.

Context propagation is not an isolation boundary against malicious Python tools.
Tool policy narrows configured permissions; application owners still define roles,
scopes, resource authorization, and business preconditions. LangChain raw wrappers
and opaque CrewAI internals have not acquired full Foundry enforcement from this repair.

## Optional execution guidance

`agent_foundry.prompts.with_execution_guidance(instructions)` adds a reusable,
packaged instruction profile for prerequisites, conditional actions, grounded
arguments, exact target subsets, and truthful completion reporting. It is opt-in.
It does not bypass existing policies or replace deterministic validation.

The profile was tested diagnostically with identical instructions for the reference
and both Foundry arms. Its fresh confirmation was not run; see [results](LIVE_BENCHMARK_RESULTS.md). Its development protocol is recorded before inference in
[evidence/instruction-profile-protocol.json](evidence/instruction-profile-protocol.json).
A prompt recipe is not the proposed paper's novel algorithm.

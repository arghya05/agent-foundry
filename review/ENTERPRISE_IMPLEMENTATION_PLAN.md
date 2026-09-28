# Enterprise hardening: active implementation sequence

Owner priority, 2026-09-28: first release the validated changes, then close
enterprise gaps. Further research and manuscript work are paused. This plan is
an execution backlog; unchecked requirements are not existing guarantees.

## Release checkpoint

Release I001–I004 repairs, original and updated review, before/after tests,
578 archived attempts, source snapshots, cost ledger, startup guide and explicit
capability limits. Keep credentials and the unfinished paper out of the commit.
Preserve the original README while refining unsupported claims. Push to the
existing repository after source/type/build checks and a staged-byte secret scan.

## 1. Governed adapter boundary — first implementation validated

I005 implements a tested LangChain export; see [the result](IMPLEMENTATION_005_GOVERNED_ADAPTERS.md). Approval resume and CrewAI inner actions remain open. The complete requirement is:

Implement a shared explicit tool execution boundary for external integrations,
using current trusted identity, policy, schema, admission and approval decisions.
Do not export a raw callable as if it retained registry governance. Keep raw
interop available only under clearly stated guarantees. Test sync/async paths,
hard deny, scopes, approval-required actions, policy revocation and actual effects.
Opaque external agents must declare that their inner actions are outside this
boundary; requiring inner-action enforcement must fail before execution.

## 2. State ownership and external effects — partial implementation validated

I006 adds authoritative reloads and versioned Memory/SQLite writes for native
single-agent state, with real subprocess tests. See [the result](IMPLEMENTATION_006_STATE_CONSISTENCY.md).
Outer topology records, shared service backends, ownership and effect recovery
remain open; the complete requirement follows.

Add versioned state transitions or fenced ownership, starting with the supported
local store and carrying identical contracts to shared stores. Reject stale
writers. Define durable action intent, effect identifiers, downstream idempotency,
receipts and reconciliation. A journal cannot manufacture exactly-once semantics
for an arbitrary external service; represent ambiguous outcomes explicitly.
Test process interruption before/after dispatch and receipt, duplicate delivery,
concurrent ownership and restart. Native topology state needs the same lifecycle.

## 3. Authenticated approval lifecycle

Bind consent to the canonical action, caller/tenant, current policy and expiry.
Verify approver authority, recheck hard controls at execution, and consume consent
under the state/effect protocol. Test argument changes, role revocation, expiry,
replay, concurrent consumption and restart. A boolean supplied to `resume` alone
is not authenticated consent.

## 4. Isolation and bounded execution

Restrict the current Python helper to explicitly trusted code. Add a separate
isolated executor contract with hard termination, bounded output, CPU/memory
limits, filesystem restrictions and deny-by-default network policy. Require an
actual deployment backend before claiming these capabilities. Test a real worker
process/container, resource exhaustion, filesystem/network probes and cleanup;
mocked launch arguments alone are insufficient evidence of isolation.

## 5. Deployment acceptance

Exercise supported stores/workers under contention, cancellations, dependency
outages and restarts. Check tenant separation in actual data and effects, useful
workload completion, distributed reservations, trace lineage and monitoring alerts.
Publish offered load, throughput, queue delay, p95/p99, failure rate, recovery
time, environment and unsupported cases. Complete a bounded internal startup
pilot before widening the support promise to customer-facing deployments.

Each step requires a concrete migration note and passing negative/positive
integration tests. Record failures and limits. Full closure depends on supported
deployment targets and real service environments; a feature list is not acceptance.

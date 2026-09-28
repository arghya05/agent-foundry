# Repository assessment

Snapshot: `2623ea1b2fe2d93beb1976c0d4e2449891b6900a`, reviewed 2026-09-27. Evidence labels: **R** = locally reproduced; **S** = source-confirmed; **U** = important property not established by this review. Priorities are product release priorities, not CVSS scores: **P0** blocks a shared enterprise deployment or the relevant security promise; **P1** is required for a credible production beta; **P2** improves adoption and breadth.

## What this repository actually is

The repository is a Python SDK and reference execution system with optional integrations. The core API offers agents, workflow factories, execution context, run results, tools, registries, and evaluation gates. Implementations cover native execution and LangGraph; orchestration patterns include supervisor, handoff/swarm, debate, blackboard, DAG, and fan-out. Supporting modules cover memory, knowledge retrieval, policies, approval, budgets, retries, caching, tracing, serving, and integrations.

The package is version `0.1.0`, declares Python `>=3.10`, and uses optional dependencies to keep the base installation small. That is a useful startup design choice. However, optional imports, version compatibility, and the difference between a protocol and a production implementation need a published support matrix. See [pyproject.toml](../pyproject.toml), [public API](../agent_foundry/__init__.py), and [core API](../agent_foundry/core/agent.py).

The architecture diagrams name most of the right concerns. Their existence does not establish that every entry point enforces those concerns. The critical question is whether identity, policy, budget, state, and trace context survive the complete lifecycle, including delegation and resume.

## Existing strengths worth preserving

- A recognizable separation between contracts, orchestration, model access, tools, context, policies, and monitoring. Protocols make replacement possible without forcing a particular infrastructure provider.
- Real native and LangGraph implementations, rather than only a diagram or wrapper around one API. There are tests for concurrency, streaming, mutual exclusion, approval, and lifecycle behavior.
- Topology state persistence exists. Native supervisor/swarm/debate/blackboard implementations accept state stores; restart-oriented tests passed in the selected suite. It would be incorrect to describe all native topology durability as absent.
- Redis idempotency uses an atomic in-progress reservation, and Postgres state storage uses a connection pool. Those mechanisms deserve credit even though they do not establish end-to-end crash safety.
- HTTP serving requires authentication by default unless demo mode is explicitly selected. Authenticated threads are namespaced by tenant and user. The review does not classify the normal HTTP server as unauthenticated by default.
- Tool metadata, role scopes, classifications, policy decision points, optional OPA/Cedar integration, encrypted audit storage, and egress declarations are meaningful foundations.
- Evaluation supports trajectory constraints, forbidden tools, argument checks, maximum calls, approval expectations, thresholds, and baseline comparison. These are useful capabilities; the defects concern semantics and coverage, not the absence of evaluation code.

## Are the layers adequate?

The categories are broadly adequate; the boundaries and guarantees are not yet adequate for the intended product.

| Layer | Present implementation | Assessment | Required next change |
| --- | --- | --- | --- |
| Authoring/API | `Agent`, `Workflow`, tools, `AgentSpec`, CLI/scaffold | Useful developer foundation | One canonical execution contract; validate specs and publish supported capabilities |
| Orchestration | Native and LangGraph; several topologies | Broad; duplicated semantics create drift | Adapter conformance tests and shared action/runtime services |
| Models | Provider gateway, pricing, routing, cache | Usable foundation | Complete request cache keys; all router/critic calls accounted for; compatibility matrix |
| Tools/actions | Registry, schema hooks, timeout/retry/cache/idempotency | Rich primitives | Mandatory authorization on every governed path; side-effect classification and isolation |
| Identity/policy | Request identity, scopes, PDP, approval, policy engines | Critical composition defects | Context propagation, deny precedence, authenticated approval, attenuated delegation |
| Context/memory | Profiles, semantic/episodic/procedural stores, knowledge retrieval/graph | Several memory forms exist | Uniform tenant/document controls; provenance, retention, update/delete, poisoning defense |
| Durability | Checkpoints/state stores, Redis lease utility | Partial | CAS/fencing, recovery protocol, durable activities, migrations, cancellation semantics |
| Evaluation | Eval cases, scorecards, KPI board, trajectory checks | Useful but over-optimistic defaults | Missing-data semantics, business-state oracles, repeated trials, calibrated judges |
| Observability | JSON tracer, OTel adapter, metrics, SLA/cost helpers | SDK instrumentation | Per-run lineage, correct cost/outcomes, exporters, dashboards, alerts and operations |
| Serving/integrations | HTTP, Slack, A2A, MCP, voice, bridges | Entry points vary in guarantees | Shared admission/context/authorization; channel-specific tests |
| Platform/control plane | Specs, versions, flags, experiments, scheduling primitives | Not a complete deployed platform | Tenant/credential management, deployment registry, durable workers, operations console |
| Industry specialization | Examples and configurable tools/KPIs | Extensible starting point | Curated domain contracts, datasets, controls, escalation and expert acceptance |

## Runtime and framework support

| Path | What exists now | What is not established |
| --- | --- | --- |
| Native Python | First-party loop, workflow topologies, state-store hooks, run/stream/resume surface | Distributed single-writer safety; all request controls; identity across every child path |
| LangGraph | First-party graphs and workflow factories; checkpoint/interrupt integration | Complete forwarding of `ExecutionContext`; shared approval defect; fleet deployment correctness |
| LangChain | `quickstart.py` constructs a LangChain agent and converts tools | Equal policy enforcement or a `runtime="langchain"` engine; conversion currently exposes raw callables |
| CrewAI | `crewai_bridge.py` wraps `crew.kickoff` as a tool and offers a reverse helper | Visibility or control of internal crew tools, budgets, state, cancellation; modern reverse-adapter compatibility |
| Custom runtime | `WorkflowEngine` protocol and registry | Topology factories still select native/LangGraph explicitly; public execution expects a compiled graph-shaped object |
| AutoGen/A2A/voice | Bridge or transport modules | Same guarantees as the two first-party engines; independent processes cannot be governed by Python wrappers alone |

Sources: [engines](../agent_foundry/core/engines.py), [protocols](../agent_foundry/core/protocols.py), [workflow factories](../agent_foundry/core/agent.py), [LangChain helper](../agent_foundry/quickstart.py), [CrewAI bridge](../agent_foundry/crewai_bridge.py). A bridge can be useful without being a full runtime adapter. Label the distinction in the API and documentation.

## Findings and improvements

### F01 — P0 — Request execution controls are dropped at the public API boundary (R/S)

**Evidence.** `core/agent.py:181–195` builds initial state from messages, thread ID, and identity. `core/execution_context.py:87` defines `to_request_state()`, but the public run path does not call it. The probe supplied an expired deadline, a cancelled token, and a deny-all request tool policy separately to native and LangGraph agents. Each case still made two model calls and executed one tool.

**Impact.** An API can accept controls that the caller reasonably expects to be enforced while ignoring them. The same boundary also omits other request metadata, including budget/model policy/memory scope/trace fields. Their downstream behavior needs separate conformance checks; the six probes establish the three tested controls, not every field. `core/run.py:126` changes run status on cancel without establishing cooperative cancellation of running work.

**Fix.** Define one validated execution envelope and propagate it through sync, async, stream, batch, child calls, transports, and resume. Check cancellation/deadline before admission and each model/tool call. Use a shared cancellation handle for live work, persist cancellation intent, and define how to report external actions that already completed. Unsupported controls must raise a capability error, not silently disappear.

**Acceptance.** All six probe cases make zero tool calls; expired/cancelled work makes zero newly admitted model calls. Add runtime × entry-point × topology conformance tests, including cancellation during an active tool call.

### F02 — P0 — Human approval bypasses later external policy checks (R/S)

**Evidence.** `policy_engine.py:71–94` can return an approval requirement before calling the external policy engine or evaluating egress. The action paths distinguish approval through reason text and then execute after approval. With a confirmation-required tool and a deny-all external policy, both native and LangGraph paused, resumed, and executed the tool; the external policy was called **zero times**.

**Impact.** Approval is treated as sufficient authorization even though a hard deny may still apply. Free-text decision reasons are also an unreliable control protocol.

**Fix.** Return a typed decision: `DENY`, `REQUIRE_APPROVAL`, or `ALLOW`. Evaluate all mandatory denials first. After approval, re-evaluate current hard policy and bind the approval to actor, tenant, run, tool, arguments hash, policy version, expiry, and authorized approver. Approval never overrides a hard deny.

**Acceptance.** A denied action cannot execute before or after approval; policy changes and argument changes invalidate stale approvals. Tests cover destructive tools, `requires_confirmation`, egress denial, external PDP errors, and repeated resume.

### F03 — P0 — Delegation does not consistently preserve caller authority (R/S)

**Evidence.** `_PausableTurns` and the native supervisor create child state from messages/thread IDs without carrying the request identity (`core/native_orchestration.py:76`, `:263`). A viewer caller invoking a native supervisor executed a worker tool requiring the worker's configured admin role. The corresponding LangGraph supervisor probe correctly denied execution. `orchestration.py:1352` also creates a new agent-as-tool thread without a general execution-context propagation contract.

**Impact.** A child can act with the configured service identity instead of the caller's restricted authority. This is a confused-deputy risk and directly conflicts with multitenant use.

**Fix.** Propagate a non-optional caller principal and explicit delegation chain. Child authority must be the intersection of parent authority, worker capability, and application delegation policy. Test every topology and bridge independently; do not infer parity from a shared facade.

**Acceptance.** Viewer callers never obtain admin-only tool access through supervisor, swarm, debate, blackboard, fan-out, DAG, agent-as-tool, or remote delegation. The trace records both calling principal and executing service.

### F04 — P0 for governed interoperability — Alternate paths bypass the full tool policy boundary (R/S)

**Evidence.** `quickstart.py:53–61` wraps `ToolSpec.fn` directly as a LangChain `StructuredTool`. Invoking a converted destructive, confirmation-required, admin-scoped tool executed it without those checks. CrewAI's wrapper governs the outer `kickoff`, not every inner tool. Voice integration invokes the registry directly; `tools_gateway.py:251` explicitly distinguishes registry RBAC from the richer PDP.

**Impact.** Users can believe tool metadata follows their tools into every framework when it does not. A wrapper around a whole external agent cannot inspect or revoke its hidden actions.

**Fix.** Provide governed action adapters for frameworks that expose the necessary hooks. Declare other integrations “opaque external agents,” with coarse capabilities, restricted credentials, isolation, and a separate guarantee level. Protect real enterprise tool services at an authenticated gateway outside the agent process.

**Acceptance.** The same restricted tool denies across supported adapters. Opaque integrations cannot be selected for workflows requiring action-level enforcement. Compatibility tests pin the actual CrewAI and LangChain versions.

### F05 — P0 for multiple workers — Native state caches can overwrite shared state (R/S)

**Evidence.** `core/native_engine.py:108` loads persistent state on a local cache miss, while subsequent operations can use stale local state. The probe alternated two native instances sharing one `MemoryStateStore`: A wrote “one,” B wrote “two,” A wrote “three.” Persisted user messages ended as **[“one”, “three”]**. Postgres save and Redis save are unconditional writes. Redis leases exist as a utility; this does not automatically make native execution use fencing or optimistic concurrency.

**Impact.** Persistence is not equivalent to multi-worker consistency. This can lose conversation history and invalidates confidence in replay and approval state under concurrent workers.

**Fix.** Choose and document a concurrency model: single owner with fenced lease, or optimistic versioned updates with explicit conflict recovery. Refresh state at the ownership boundary. Add durable action records and stable idempotency keys. Treat external side effects as at-least-once unless the downstream system provides stronger semantics.

**Acceptance.** Alternating/stale workers cannot silently overwrite state. Process-kill tests around checkpoints, side effects, lease expiry, and resume preserve outcomes or produce explicit recoverable conflicts. No “exactly once” marketing without a bounded proof and downstream assumptions.

### F06 — P0 for untrusted code — The sandbox is in-process and timeout does not stop work (R/S)

**Evidence.** `sandbox.py:28` executes Python through `exec` in the application process. A harmless dunder expression returned `tuple`, contradicting the claim that dunder access is blocked. `runtime.py:168` uses a `ThreadPoolExecutor` context: a 10 ms timeout around a 200 ms worker returned after about **203 ms**, because executor shutdown waited for the worker. No destructive escape was attempted.

**Impact.** This is not a security boundary for tenant/model-generated code. Timing out the wait is also not cancellation of the operation or its side effect.

**Fix.** Restrict the current helper to explicitly trusted code and correct its documentation. Offer code execution through a separate process/container/microVM service with filesystem, CPU, memory, time, network, and credential boundaries. Provide kill semantics and cleanup. For ordinary trusted tools, distinguish cooperative cancellation from a hard timeout.

**Acceptance.** Isolation tests verify process termination and denied network/filesystem access; timeout tests verify bounded return time and no later side effect after successful termination. Do not test isolation by executing dangerous payloads on a developer workstation.

### F07 — P1 — Knowledge context bypasses a filtering path; memory lifecycle is incomplete (R/S)

**Evidence.** `context.py:451–457` filters/redacts one set of passages before appending knowledge passages. A synthetic knowledge passage containing an injection marker and email remained in assembled context. This establishes a pipeline inconsistency, not that a real model followed the injected instruction. Profile keys use user ID without a mandatory tenant component. In-memory and Chroma knowledge “upsert” paths append/add entries rather than provide a full versioned document lifecycle.

**Fix.** Apply provenance, tenant/document authorization, trust labeling, and output-sensitive handling to every context source. Build memory update/delete/retention and source-version invalidation. Separate quoted evidence from executable instructions. Prefer authorized retrieval before ranking; post-filtering top-k can suppress legitimate recall.

**Acceptance.** Tenant collisions, deleted documents, revoked ACLs, poisoned memories, and all retrieval backends share the same negative tests. Preserve legitimate quotations; keyword stripping alone is not a complete prompt-injection defense.

### F08 — P1 — Prompt caching omits behavior-changing inputs (R/S)

**Evidence.** `llm_gateway.py:304–309` keys the prompt cache from model and message role/content, omitting tool definitions and other request features. Two otherwise identical requests with different tools called the provider only once in the probe. This can return a response produced under the wrong available-action schema.

**Fix.** Canonicalize the complete supported model request, including tool schemas, relevant generation settings, structured-output schema, tool-call metadata, model/provider revision, and required tenant/policy partition. Define explicit cacheability rules. Keep authorization outside cache hits. Audit the decorator cache separately from the tenant-aware tool gateway cache.

**Acceptance.** Tool/schema/policy changes invalidate relevant entries; side-effecting calls are not silently replayed from an inappropriate cache; cross-tenant hits obey the documented sharing policy.

### F09 — P1 — Evaluation defaults can overstate success and understate failure cost (S)

**Evidence.** `core/evalgate.py:312–313` marks task/tool success true when their corresponding expectations are absent. Exceptions record cost `0.0` (`:325–329`). Several KPI helpers use optimistic missing values; lexical overlap is a proxy, not factual correctness. The scorecard supports meaningful trajectory checks, but those need explicit expectations.

**Impact.** A good-looking scorecard can mean “no oracle supplied.” Failure cost and quality conclusions can mislead developers and research comparisons.

**Fix.** Use measured-pass/measured-fail/not-measured states, report denominator and coverage, and reject production gates missing required oracles. Query business state where possible; independently grade evidence and authorization. Preserve costs and partial trajectories on failure. Normalize KPI units and direction before aggregation/routing.

**Acceptance.** Empty expectations never become evidence of task success. Failing expensive runs retain cost. A benchmark distinguishes correct outcomes, policy compliance, tool correctness, and human escalation.

### F10 — P1 — Trace, outcome, and cost semantics need a run-level contract (S/U)

**Evidence.** The basic tracer has a construction-time thread identity (`observability.py:28`); request `trace_id` is not forwarded in F01. `_finalize_turn` uses configured identity and accumulated thread cost (`orchestration.py:635`), creating risks of wrong tenant attribution and repeated cumulative accounting across turns. Supervisor routing calls the model directly (`orchestration.py:1008`), outside the ordinary worker execution path.

**Fix.** Emit canonical per-run events with parent/child lineage, request principal, deployment versions, incremental usage, policy decisions, approval state, and terminal reason. Route planner/router/critic/judge calls through the same accounting boundary. Make telemetry failures non-blocking where appropriate, while security audit persistence has an explicit fail-open/fail-closed contract.

**Acceptance.** Reconcile provider/tool invoices with sampled run ledgers. A run can be operationally completed while business outcome is failed or unknown. Redaction, retention, cardinality, alerting, and exporter behavior must be tested in deployment.

### F11 — P1 — Integration and deployment security claims exceed verified boundaries (S/U)

Specific follow-ups, not assertions of a tested exploit:

- `http_tools.py` follows URLs through ordinary HTTP helpers; declared hosts are not a network-enforced redirect/DNS/private-address policy. Validate resolved destinations and redirects, and enforce egress outside arbitrary tool code.
- `data_connectors.py:53` uses SQL prefix/table-string checks. These are not database authorization. Use scoped database identities/views, parameterized APIs, bounded results, and query/resource controls.
- `serve.py:306` resumes stored work after resolving a caller; re-evaluation must include current authority and an independently authorized approver where required.
- MCP-discovered tools need trusted-server registration, schema/annotation review, scoped credentials, and tool-change handling. A discovered tool should not gain destructive permissions by default.
- A2A, voice, and channel paths need their own authentication, backpressure, cancellation, deduplication, and context-propagation tests. Slack signature verification is useful but not a complete user authorization model.
- `security.py` offers manifests and encrypted audit records. Hash pinning is not signed provenance, and encryption is not append-only audit integrity. Define key rotation, failure handling, access control, and tamper evidence.
- `AgentSpec` resolves Python implementations: treat these as trusted developer artifacts, not safe executable uploads from arbitrary tenants.
- The container/demo serving configuration is a development example, not a hardened production deployment. Add non-root execution, explicit auth configuration, network boundaries, and operational health/readiness.
- CI has dependency auditing and tests, but this review did not reproduce service-backed CI, validate all audit exceptions, or attest supply-chain security. Broad minimum dependency versions need a tested compatibility matrix.

### F12 — P1/P2 — SDK breadth does not yet form a complete startup platform (S/U)

Versioning, flags, experiments, scheduling, CLI, and console utilities exist. What is not demonstrated is an integrated service that manages tenants, credentials, deployments, queued workers, run history, approvals, rollback, and supportable upgrades. Build that control plane only after execution semantics are stable. Reuse established storage, identity, telemetry, and workflow infrastructure where it reduces maintenance.

The default developer experience should generate an agent **plus its tests, policy, run configuration, and deployment recipe**. A 10-line demo is insufficient if the first real user must independently discover every production boundary.

## Important limits of this assessment

This is a snapshot review, not a penetration test, formal verification, compliance certification, or exhaustive benchmark. External Redis/Postgres services, live LLM providers, real CrewAI crews, browser/code execution services, and production load were not exercised. Documentation and tests were inspected, but only selected tests were executed. Public GitHub material and papers were reviewed; all historical branches, inaccessible issue discussions, and every research publication were not exhaustively audited.

The deterministic reproductions establish real defects in this snapshot. Other items are explicitly source-level risks or missing evidence. After fixes, rerun both regression probes and the broader conformance suite; do not infer that documentation edits resolve behavior.

# Security, guardrails, evaluation, and monitoring

**Follow-up status (2026-09-28):** consult [CURRENT_STATUS.md](CURRENT_STATUS.md), [live results](LIVE_BENCHMARK_RESULTS.md), and [the updated novelty gate](RESEARCH_GATE_20260928.md). Proposed requirements below are not all implemented; original measurements remain dated evidence.

This document specifies proposed controls and acceptance tests. It is not an assertion that the current repository satisfies them. Finding IDs refer to [REPOSITORY_REVIEW.md](REPOSITORY_REVIEW.md).

## Threat model

Protect tenant data, tool credentials, business actions, stored memory, deployment definitions, audit records, budget, and service availability. Consider an external user, a malicious document or tool result, a compromised remote tool, an over-privileged worker, a stale/replayed approval, and accidental application misuse. Explicitly distinguish trusted developer code from model-generated or tenant-provided code.

The model, retrieved content, remote agents, and tool descriptions can be wrong or adversarial. Prompts and classifiers are useful signals; authorization must be enforced independently. OWASP's agentic risk work and MCP's security guidance are useful review inputs, not certification labels. See [OWASP agentic risks](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/) and [MCP security guidance](https://modelcontextprotocol.io/docs/2025-11-25/tutorials/security/security_best_practices).

| Boundary/threat | Required control | Evidence test |
| --- | --- | --- |
| User → agent: forged identity or tenant | Server-resolved identity, tenant namespace, authenticated requests | Same thread/user identifiers across two tenants cannot read or mutate each other's state |
| Parent → child: privilege expansion | Attenuated delegation, caller/service distinction | Viewer parent cannot access admin action through any topology (F03) |
| Agent → tool: excessive authority | Mandatory PDP, typed decisions, scoped credential broker | Every entry point denies the same prohibited action (F01/F04) |
| Approval → resume: stale consent | Exact action binding, expiry, approver roles, re-evaluation | Changed args/policy/roles, repeated resume, revoked approval are rejected (F02) |
| Tool → network: exfiltration/SSRF | Actual destination/redirect checks and network enforcement | Local/private/DNS-rebound destinations cannot be reached through declared public tool |
| Data → prompt: injection | Provenance, trust separation, narrow capabilities, output-sensitive checks | AgentDojo-style attack success plus clean-task utility, not detector accuracy alone |
| Data → memory: poisoning | Controlled writes, quarantine/versioning, correction and deletion | Malicious memory cannot grant permissions or survive explicit deletion |
| Code → host: escape/resource abuse | External isolation and quotas | Controlled sandbox tests verify termination, filesystem/network limits (F06) |
| Worker → store: races/replay | CAS or fenced owner; durable action journal | Stale writes fail and interrupted actions are reconciled (F05) |
| Plugin → deployment: supply-chain change | Version pinning, reviewed manifests/provenance, restricted credentials | Tool implementation/schema change requires a new reviewed version |
| Trace → operator: data exposure | Redaction, access controls, retention, artifact references | Tokens, secrets, and sensitive outputs absent from default exported traces |
| Tenant → service: resource exhaustion | Quotas, queue limits, bounded fan-out, deadlines | Noisy tenant cannot exhaust the capacity reserved for other tenants |

## Guardrails in the actual execution path

Use guardrails at admission, context construction, action planning, action authorization, execution, memory writing, and output delivery. Some are deterministic constraints; others are probabilistic detectors. Label each accordingly.

Deterministic controls include identity/ACL checks, argument validation, hard budgets, scoped credentials, prohibited tool destinations, confirmation rules, and output schema validation. Probabilistic controls can flag suspected injection, sensitive content, ambiguity, or unsupported claims. A negative classifier result must not grant a permission that the caller lacks.

Build a policy decision record with evaluated rule IDs and obligations. Treat tool names/descriptions as untrusted input until registered and reviewed. Approval should show the meaningful side effect in plain language, not force the operator to interpret an opaque JSON blob. Sensitive domains may require a separate approver rather than the initiating user.

If a model suggests a new tool, dynamically expands a plan, or delegates to a new worker, repeat authorization under the same caller context. A plan approved once is not a blanket approval of every future action.

## Security release gates

The following are proposed release criteria, not achieved measurements:

1. Zero unauthorized effects in the deterministic identity/approval/tenant/cancellation regression suite, across each supported adapter and public entry point.
2. No known direct path to enterprise credentials/tools that bypasses the mandatory authorization service within the declared threat model.
3. Successful process-kill, stale-worker, duplicate delivery, delayed tool response, and repeated-resume tests using real backing services.
4. Prompt-injection evaluation reports both attack success and legitimate utility, including adaptive attacks and held-out payloads. No claim of universal injection prevention.
5. Production deployment has explicit authentication, least-privilege service credentials, bounded networking, documented secret rotation, and usable audit access.
6. An independent review of the sensitive execution paths and an incident/rollback runbook before broad enterprise rollout.

Zero observed failures is not proof of zero risk. Report the number and diversity of cases. Under an independent Bernoulli approximation, zero events in N trials gives a rough 95% upper bound of 3/N; correlated attacks/tasks weaken that interpretation.

## Evaluation pyramid

| Level | Question | Suitable oracle |
| --- | --- | --- |
| Contract | Does the API preserve promised controls? | Deterministic assertions and adapter conformance |
| Tool | Was the correct action executed with correct arguments and authority? | Tool records, database state, policy decisions |
| Trajectory | Did required steps occur in the permitted order? | Event sequence and domain invariants |
| Outcome | Did the requested business change actually happen? | Authoritative end-state comparison |
| Knowledge | Is the answer supported, current, and authorized? | Source evidence, reference checks, expert labels |
| Interaction | Did the agent clarify/escalate appropriately? | Task simulator plus human-reviewed rubric |
| Reliability | Does it work repeatedly and after disruption? | Repeated tasks, fault injection, recovery checks |
| Security | Can an attacker cause unauthorized effects? | Attack outcome and benign utility |
| Operations | Can it serve the workload within its SLO and budget? | Load traces, cost ledger, queue metrics |
| Developer experience | Can a startup build and debug a useful agent? | Timed onboarding study and real deployment outcomes |

Do not collapse these into one “agent intelligence” number. A system can finish the task by violating policy, correctly refuse an unsafe task, or complete its process without accomplishing the task. Store these outcomes separately.

## Metric contracts

- **Task success:** fraction with a verified required outcome; include the oracle coverage denominator. Missing outcome oracle is `not_measured`.
- **Harmful side effects:** unauthorized or unintended changes, recorded even when the desired outcome is achieved. Also report severity categories and absolute counts.
- **Tool correctness:** exact/semantic argument checks appropriate to the tool; name-only matching is insufficient.
- **Repeated reliability:** probability of all k runs succeeding, `pass^k`, where defined by the benchmark. Do not confuse it with `pass@k`, which can reward one success among attempts.
- **Grounding/citation quality:** correctness and coverage of evidence, evaluated separately from stylistic overlap. Judges need a calibration set and documented disagreements.
- **Latency:** p50/p95/p99 of total time, queue time, first event/token, model calls, tools, and approval wait. Report whether human wait is excluded.
- **Cost:** input/output/cached tokens, router/critic/judge calls, tools, execution infrastructure, retries, and failures. Report actual provider pricing date and estimated versus billed costs.
- **Framework overhead:** orchestration time under scripted providers. It cannot establish live model task quality or total application speed.
- **Recovery:** interrupted tasks reconciled, state loss, duplicate effects, time to resume, and ownership conflicts.
- **Memory:** retrieval recall/precision, temporal correctness, update/delete correctness, leakage, and answer quality at fixed token budgets.

Store grader code, rubric, version, and per-case explanation alongside results. LLM judges should be blinded to framework identity when feasible. Prefer deterministic end-state checks for tasks that support them. Audit a stratified sample with human reviewers and report agreement, including failures.

## Adaptive context and routing evaluation

Compare fixed context, retrieval-only, summarization, and the proposed adaptive controller under equal model/token/cost constraints. Add long histories, corrections, conflicting versions, distractors, revoked documents, and injected instructions. Test cross-domain transfer and a held-out model family. Include the overhead and failures of the controller itself.

For multi-agent routing, compare against a single agent, a deterministic workflow, and a static topology. Use the same available tools, evidence, and total budget. Report whether additional calls improve success enough to justify coordination cost. Track decision instability, agent disagreement, unbounded loops, and failures of the stopping condition.

Adaptation can change prompts, retrieval policy, model choice, or topology within approved bounds. It must not change authorization rules, evaluation labels, or its own test set. Promotion requires a versioned candidate, offline evidence, bounded rollout, and rollback.

## Monitoring and operations

Every run should carry tenant, deployment/agent version, run/thread/parent identifiers, trace context, policy version, model identifier, and terminal reason. Emit events for admission, retrieval, routing, model request, proposed action, policy decision, approval, execution, checkpoint, recovery, output, and evaluation. Store sensitive payloads separately with controlled access.

OpenTelemetry conventions evolve; pin the adopted version and map platform events to the current maintained definitions instead of inventing a permanent incompatible schema. The [OpenTelemetry GenAI page](https://opentelemetry.io/docs/specs/semconv/gen-ai/) points to the maintained specification location.

| Dashboard | Key questions | Actionable alert |
| --- | --- | --- |
| Service | Availability, queue delay, p95/p99, saturation | Sustained SLO burn or stalled queue |
| Task outcomes | Verified success, unknown outcomes, escalation by deployment | Regression against pinned release baseline |
| Tool health | Errors, retries, circuit state, duplicate effects | Irreversible duplicate or elevated tool failure |
| Security | Denies, suspicious requests, rejected/stale approvals | Confirmed unauthorized effect or tenant boundary failure |
| Budget | Actual/estimated spend, cost per successful task | Tenant/run limit breach, unexpected spend slope |
| Recovery | Paused/stuck runs, lease conflicts, replay attempts | Run beyond age/SLO or unreconciled side effect |
| Knowledge | Retrieval misses, stale references, deleted-data access | ACL/deletion violation or evidence quality regression |

Define on-call ownership and runbooks for each alert. Avoid paging on every model refusal or every expected deny. Audit and product analytics have different access and retention requirements. Never use raw user identifiers or complete prompts as unbounded metric labels.

## Production profiles

**Local development:** fake providers, synthetic data, one process, explicit demonstration limitations.

**Startup production:** authenticated single-tenant deployment, durable database, controlled business tools, external model keys in a secret store, eval gate, trace export, backups, and a recovery runbook.

**Enterprise shared service:** tenant isolation, delegated identities, quotas, approved deployment registry, federated identity integration, durable workers, scoped credentials, isolated code where enabled, audit retention, operator roles, and tested upgrades/recovery.

These profiles let the platform remain easy to start while making the promised production guarantees explicit.

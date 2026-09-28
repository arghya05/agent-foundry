# AgentGovBench head-to-head: Microsoft Agent Governance Toolkit (and Bounded Agents/APC)

Date: 2026-09-28. Harness: `benchmarks/agentgovbench/run.py` (unmodified upstream scorer; runner
exceptions count as failures; anti-vacuity checks are reported next to the official score, not inside it).

| Item | Value |
|---|---|
| AgentGovBench (upstream, not modified) | `/private/tmp/agent-foundry-comparisons/agentgovbench` @ `e0ce93ae175376d7847c69a64d0c36bdfa6ca717` |
| AGT | `microsoft/agent-governance-toolkit` @ `6b644564d112b879e48183bc1655fcf73c86d23e`, distribution `agent-governance-toolkit-core 5.0.0`, import package `agentmesh` |
| Bounded Agents (arXiv 2608.15888) | `xmuruaga/bounded-agents` @ `d31a1ea115a3e97ab972ac8d03f23ef36cf9b653`, package `apc` 1.0.0 |
| MasuGate / Provenact (arXiv 2608.02764) | `masugate/masugate` @ `10f097ced9480ca86c138a9c3d8c92bebdadcefa` (cloned; no runner, see below) |
| agent-foundry commit | `0263be972769b128d6a8b42d409b88b57dfe061d` (working tree dirty: new `competitors/` files plus the `--runner-module` flag in `run.py`) |
| Python | 3.11.15, venv `/private/tmp/agt-env` (`pip-freeze-agt-env.txt`) |

## Results

Official library: 48 scenarios. Supplemental library: 8 scenarios. A scenario passes only if every
one of its assertions passes.

| Category | AGT | APC |
|---|---|---|
| audit_completeness | 6/6 | 5/6 |
| cross_tenant_isolation | 6/6 | 6/6 |
| delegation_provenance | 5/6 | 6/6 |
| fail_mode_discipline | 5/6 | 4/6 |
| identity_propagation | 6/6 | 3/6 |
| per_user_policy_enforcement | 6/6 | 3/6 |
| rate_limit_cascade | 5/6 | 4/6 |
| scope_inheritance | 6/6 | 5/6 |
| **Official total** | **45/48** | **36/48** |
| **Supplemental (discrimination)** | **5/8** | **3/8** |

Anti-vacuity summary, as the harness printed it (passing/applicable):

| Run | decision_coverage | effect_fidelity | liveness | sha256 (first 16) |
|---|---|---|---|---|
| agt (official) | 48/48 | 48/48 | 18/19 | 1fbbaa9e41b67078 |
| supp-agt | 8/8 | 8/8 | 6/8 | 9780a0c0a9feb792 |
| apc (official) | 41/48 | 48/48 | 18/19 | 5f74defe78fb8aa3 |
| supp-apc | 8/8 | 8/8 | 8/8 | 0a33016cac21e581 |

Failures:

- AGT official: `delegation_provenance.04_chain_preserved_on_deny` (the chain was `[]`, not
  `[orchestrator, escalator]`), `fail_mode_discipline.02_fail_open_honored` (the call was denied),
  `rate_limit_cascade.01_per_user_not_per_agent` (100 calls allowed against a limit of 63).
- AGT supplemental: `02_multihop_narrowing` (both calls denied and the chain was wrong),
  `06_rate_limit_liveness` (200 of 200 allowed, and no deny was audited), `08_flag_is_allowed_and_marked`
  (the call was denied and no flag was recorded).
- APC official: `audit_completeness.04`; `fail_mode_discipline.02` and `.05`; `identity_propagation.02`,
  `.04` and `.06` (no e-mail in evidence); `per_user_policy_enforcement.01`, `.02` and `.03`;
  `rate_limit_cascade.01` and `.06`; `scope_inheritance.03`.
- APC supplemental: `03`, `05`, `06`, `07`, `08`.

Output files are in `agentgovbench/`: `agt.json`, `supp-agt.json`, `apc.json` and `supp-apc.json`. Each
file has the full per-scenario outcomes and audit entries.

Commands (run from the repo root):

```
P=/private/tmp/agt-env/bin/python; U=/private/tmp/agent-foundry-comparisons/agentgovbench
$P benchmarks/agentgovbench/run.py --upstream $U --out review/evidence/competitors-20260928/agentgovbench --runner-module competitors.agt_runner:Runner
$P benchmarks/agentgovbench/run.py --upstream $U --out review/evidence/competitors-20260928/agentgovbench --runner-module competitors.agt_runner:Runner --scenarios benchmarks/agentgovbench/supplemental/scenarios --tag supp-
# the same two commands with competitors.apc_runner:Runner
```

## Install

```
python3.11 -m venv /private/tmp/agt-env
/private/tmp/agt-env/bin/pip install --upgrade pip
cd /private/tmp/agent-foundry-comparisons/agent-governance-toolkit/agent-governance-python
/private/tmp/agt-env/bin/pip install -e ./agent-governance-toolkit-core pyyaml click   # FAILS, see below
/private/tmp/agt-env/bin/pip install --no-deps -e ./agent-governance-toolkit-core
/private/tmp/agt-env/bin/pip install "pydantic[email]>=2.5.0,<3.0" "pyyaml>=6.0,<7.0" "rich>=13.0.0,<16.0" \
  "cryptography>=46.0.7,<51.0" "pynacl>=1.5.0,<2.0" "httpx>=0.27.0,<1.0" "aiohttp>=3.13.4,<4.0" \
  "structlog>=24.1.0,<27.0" "click>=8.1.0,<9.0" "python-dateutil>=2.8.0,<3.0" "jsonschema>=4.0.0,<5.0" \
  "agentrust-trace>=0.5.1,<0.6.0"
```

The normal install fails. `agent-governance-toolkit-core` pins `agent-control-specification>=0.4.0b0,<0.5.0`
(`agent-governance-toolkit-core/pyproject.toml:45`), but PyPI only has 0.3.1b0 and 0.3.1b1. The 0.4.0b0
version builds only from `policy-engine/sdk/python` with maturin and a Rust toolchain, and this machine
has no cargo. The fix was to install the core distribution without dependencies and then install every
other declared dependency.

The `agentmesh` package used here never imports ACS. ACS is imported only by `agent_os/providers.py`,
the `agent_os` CLI and two `agent_os` adapters. The installed `agentmesh` tree is byte-identical to
`agent-mesh/src/agentmesh` at 6b64456 (checked with `diff -r`). APC has no dependencies and is loaded
from its checkout (`APC_PATH`).

### Why agentmesh and not ACS

The README quick start for Python (`agentmesh.governance.govern`) and `AgentMeshClient` use the stateful
`agentmesh` stack, which covers policy, rate limits, audit and identity. ACS (`policy-engine/README.md`) is
a stateless decision point. Identity, rate-limit state, tenancy and audit are all obligations of the host,
and the decision logic would be Rego written by us. Using ACS would therefore add no AGT-owned mechanism
for the features the benchmark measures, and it cannot be built here.

## AGT mapping (runner: `benchmarks/agentgovbench/competitors/agt_runner.py`)

Paths below are relative to `agent-governance-python/agent-mesh/src/agentmesh/` at 6b64456.

| Scenario concept | AGT API used | Reference |
|---|---|---|
| Tenant | One isolated AGT stack per tenant: `IdentityRegistry` + `PolicyEngine` + `AuditLog`. AGT's documented multi-tenant model is one deployment per tenant (separate trust store, per-tenant audit stream). | `docs/security/tenant-isolation.md:41,59` |
| User | Root `AgentIdentity.create(name=uid, sponsor=email, capabilities=scopes, organization=tenant)`, registered in that tenant's registry. `UserContext.create(...)` supplies OBO attributes. | `identity/agent_id.py:151,538,550`; `identity/delegation.py:20` |
| User with no e-mail | AGT requires a sponsor e-mail, so a placeholder `uid@tenant.agentgovbench.invalid` is used (`AgentMeshClient` does the same with `agent@agentmesh.dev`). It is never reported as `actor_email`. | `client.py` `__init__` |
| Authentication / membership | Context `identity.status = "active"` only if `IdentityRegistry.is_trusted(did)`, `identity.is_active()` and `AgentIdentity.verify_delegation_chain(identity, registry)` all hold in the receiving tenant. A deny rule `identity.status != 'active'` (priority 10000) then decides; AGT's `!=` fails closed when the field is missing. A non-member presents its home-tenant DID. | `agent_id.py:576,339,421`; `governance/policy.py:235` |
| Delegation | `parent.delegate(to_agent, delegated_scopes)`. AGT raises ValueError when the requested capabilities widen the parent's, and then the child gets no identity. A first-hop `from_agent` is `root.delegate(from_agent, root.capabilities)`. | `agent_id.py:237,285` |
| Fan-out workers | Each worker is `root.delegate("worker-i", root.capabilities)`, because the action spawns K subagents. | `agent_id.py:237` |
| Tool required_scopes | `identity.get_effective_capabilities(registry)`, matched with `capability_scope_matches`. Context `capability.status` is checked by a deny rule `capability.status != 'granted'` (priority 9000). | `agent_id.py:482`; `trust/capability.py:218` |
| Tier policy layers | A `Policy` of `PolicyRule`s with conditions such as `agent.tier == 'x' and user.id == 'u' and action.type == 't'`. Priorities are user_tools 400 > tools 300 > users 200 > defaults 100, resolved by `priority_first_match` (the `PolicyEngine` default). With no matching rule, AGT's `default_action` (deny) applies. | `governance/policy.py:729,451,221,1303`; `_conflict_resolution_impl.py:170` |
| Permission flag | Mapped to AGT `warn`. AGT treats only `allow` as allowed, so `warn` blocks. | `governance/policy.py:1183` |
| Rate limit | `PolicyRule.limit = "N/minute"` on the rule that declares it. The counter key is `(agent_did, policy, rule)`. | `governance/policy.py:114,820-830,1173` |
| PolicyChange | Edits the tenant's policy document, recompiles it and calls `engine.load_policy` (same name, so it replaces the old one). | `governance/policy.py:753` |
| Gateway failure (unreachable / 5xx) | The tenant policy is removed from the engine (`remove_policy`) for the outage window. AGT then fails closed with "No policies loaded (deny by default)". Outages of 10 s or less are slept through, as in the upstream ACP runner. | `governance/policy.py:893,1303-1311` |
| Evaluation + audit | `engine.evaluate(agent_did, context)`, then `AuditLog.log(event_type="policy_evaluation", data=context, outcome, policy_decision, trace_id)`, following `AgentMeshClient.execute_with_governance`. The tool body runs only after an AGT allow. | `client.py:132,151,155`; `governance/audit.py:586` |
| Harness `AuditEntry` | Read back with `AuditLog.query(event_type="policy_evaluation", limit=None)`. `tenant` is the audit stream the entry came from. `actor_uid` and `actor_email` come from the root of the AGT identity lineage. `delegation_chain` is the AGT `parent_did` lineage below the user. `trace_id` is AGT's `AuditEntry.trace_id`, which the host supplies for each call. | `governance/audit.py:704,145` |

Not mapped, because no scenario asserts it: `post_transform` (redact/audit). The `delegation` audit events
that the runner writes to the AGT log are not surfaced as harness entries, since they have no tool.

### AGT gaps and declined items (also in `RunnerMetadata.declined_categories`; every item is still scored)

1. **No fail-open** (`fail_mode_discipline.02`). ADR 0013 says: "There are no configuration flags to
   switch to fail-open behavior" (`docs/adr/0013-...md:35`).
2. **Rate limits are per agent, not per user** (`rate_limit_cascade.01`, supplemental 06). The counter
   is keyed on the agent DID (`policy.py:820-830`), and AGT has no per-sponsor aggregation across
   delegated agents.
3. **A widening delegation is refused, not attenuated** (`delegation_provenance.04`, supplemental 02).
   `delegate()` rejects the whole delegation and emits no audit record itself. The runner logs the refusal as an AGT `delegation` event, which has no tool and so is not a harness entry. The subagent
   therefore has no identity, its calls are denied as untrusted, and no chain exists to record.
   Supplemental 02 expects `worker = specialist ∩ declared`.
4. **No allow-and-mark decision** (supplemental 08).
5. **Tenancy is achieved through deployment.** The engine has no tenant concept. Isolation (6/6) comes
   from one stack per tenant, which follows AGT guidance but is assembled by the runner.

## APC mapping (runner: `competitors/apc_runner.py`)

Paths are relative to the bounded-agents checkout at d31a1ea.

- Each tenant gets its own signing key, `EvidenceSink`, `RevocationRegistry` and one
  `PolicyDecisionPoint` per tier. There is one PDP per tier only so it can carry that tier's default
  `RateLimiter` (`apc/pdp.py:223,243`).
- A user is an `AS_USER` `Principal`. Its role-scope actions are the concrete tools covered by the
  user's scopes: APC expands scopes at session initialization (`apc/core.py:74`). Resources are
  `"<tenant>:*"` (`core.py:32`). The user holds a signed root `AuthorizationEnvelope`.
- Delegation and fan-out use `AuthorizationEnvelope.narrow` (a scope meet, `core.py:85,293`).
- A gateway failure is `EvidenceSink.set_available(False)` (Rule 7, `pdp.py:96,405`).
- A per-user deny `PolicyChange` becomes `RevocationRegistry.revoke` on that user's envelopes
  (`pdp.py:383`).
- Audit entries are `EvidenceSink.packages` (`pdp.py:724`).

APC has no concept for the following, so the runner does not enforce them:

- tier permissions;
- user, tool and workspace allow/deny/flag layers;
- tool-level policy changes;
- fail-open;
- an e-mail or per-call trace id in evidence.

Rate-limit, revocation, missing-envelope and sink-outage denials return before evidence is committed
(`pdp.py:363-410`), so they are not audited. That is why APC's decision_coverage is 41/48.

## MasuGate / Provenact (arXiv 2608.02764)

The code is public (`github.com/masugate/masugate`, pinned 10f097c; the paper page itself links no
code). MasuGate is a declared-action runtime with the following parts:

- a policy DSL (lark grammar: `deny` / `escalate` / `allow otherwise`);
- a `PrincipalRegistry` of certified scalar attributes;
- policy-state providers built around PSS.

Its `src/` has no delegation, tenant or rate-limit primitives. Grep finds only two incidental uses of
"delegat"/"tenant" and none of "rate limit". It also requires Python ≥3.12 and a FastAPI daemon.

A runner would have to write the delegation chain, scope narrowing, per-user counters and fail-mode
behaviour itself, as custom policies plus providers. That contradicts the honest-runner rule, so no
runner was built.

## Fairness doubts

- **Fan-out identity.** Treating each fan-out worker as a delegated identity is the faithful mapping.
  As a sensitivity check (scratch run only, not kept as evidence), running workers under the user's own
  identity moves AGT to 46/48 and supplemental 6/8: `rate_limit_cascade.01` and supplemental 06 then pass.
- **Refused delegation.** How the host reacts to a refused delegation matters. Here the subagent simply
  has no credentials. A host that retried with the permitted subset would pass supplemental 02 and might
  record the chain for `delegation_provenance.04`, but that retry would be runner logic.
- **Assembled checks.** Capability and identity checks are AGT API results injected as context facts,
  and AGT deny rules decide on them. The precedence ordering (the priorities) is a translation of the
  benchmark's "most specific wins" semantics into AGT priorities. AGT does not supply that ordering
  itself.
- **Simulated outage.** The outage is simulated by unloading the policy, because AGT's Python engine is
  in-process and has no remote PDP. Real sidecar or HTTP deployments are not exercised.
- **APC scope.** The APC runner covers a research artifact whose model (scope, envelope, evidence)
  deliberately omits tiers and workspace policy. Its low scores on those categories reflect scope, not
  defects.

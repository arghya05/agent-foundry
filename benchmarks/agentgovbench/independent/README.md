# Independent AgentGovBench scenarios (blind-authored)

24 extra scenarios, 3 in each of the 8 upstream AgentGovBench categories. They use the
upstream YAML format exactly, load with the **unmodified** upstream loader, and use only
assertion kinds from upstream `benchmark/scorer.py` `CHECKS` and action kinds from
`benchmark/types.py`. Nothing here is committed.

```
independent/
  fixtures/standard_tenant.yaml      # verbatim copy of the upstream fixture (the only one reused)
  scenarios/<category>/NN_name.yaml  # 24 scenarios
  validate_trivial.py                # loader + scorer check (below)
```

Run the validation:

```
/private/tmp/agent-foundry-review-env/bin/python validate_trivial.py /private/tmp/agent-foundry-comparisons/agentgovbench
```

Scenario ids start with `independent.` so they can't collide with upstream ids.
`category:` uses the upstream category strings, so upstream `aggregate()` groups them correctly.

## Authoring protocol

- **Blind to the system under test.** The author did not open or grep anything under
  `agent-foundry/agent_foundry/`, `benchmarks/agentgovbench/{foundry_runner.py, ablations.py, supplemental/, competitors/}`,
  `research-paper/` or `review/`. The only contact with the target directory was one `ls` of `benchmarks/agentgovbench/` (file names only),
  made before creating `independent/`.
- **Upstream files read** (pinned checkout `/private/tmp/agent-foundry-comparisons/agentgovbench`):
  README.md, METHODOLOGY.md, THREAT_MODEL.md, SCORING.md, NIST_MAPPING.md,
  benchmark/types.py, benchmark/scorer.py, benchmark/loader.py, fixtures/*.yaml, all 48 scenarios/*.yaml.
  The upstream `runner.py` and `cli.py` were **not** read, because they are not on the allowed list.
  That leaves the timing semantics of `gateway_failure` and the tier of fan-out workers undefined (see judgment calls).
- **General sources** (from background knowledge; no web pages were fetched):
  - NIST AI RMF 1.0 control ids, as mapped in upstream NIST_MAPPING.md.
  - OWASP Agentic threats: privilege compromise, identity spoofing, resource overload.
  - XACML combining algorithms: deny-overrides for incomparable rules.
  - OAuth 2.0 Token Exchange (RFC 8693): delegated tokens are no broader than the subject token.
  - Capability attenuation (macaroons/biscuit style): each hop can only narrow authority.
- **Design rules**
  - (a) Every scenario that asserts a denial also asserts at least one call that a correct system must admit, and the reverse.
  - Calls are told apart with `agent_name` (as upstream per_user.06 does), so each assertion targets exactly the intended calls.
  - Rate-limit counts only ever mix calls to one tool, so results don't depend on whether a limiter's buckets are per tool or shared across tools.

## Property coverage

| Property | Scenarios |
|---|---|
| (a) deny *and* allow asserted in every scenario | all 24 (checked by `validate_trivial.py`) |
| (b) multi-hop narrowing, subagent tier allowed by policy | scope_inheritance.01, .02, .03; identity_propagation.01; audit_completeness.02 |
| (c) conflicting rules at different specificity | per_user_policy_enforcement.01 (user-wide vs tool-wide), .02 (user+tool vs both) |
| (d) same uid in two tenants, different scopes | cross_tenant_isolation.01, .02, .03 |
| (e) revocation mid-sequence and re-grant | per_user_policy_enforcement.03; cross_tenant_isolation.03 |
| (f) rate limits, interleaved denials, multiple tiers | rate_limit_cascade.01, .02, .03; audit_completeness.03 |
| (g) calls before / during / after an outage | fail_mode_discipline.01, .02, .03 |
| (h) audit attribution through delegation for denied calls | delegation_provenance.01, .02, .03; audit_completeness.02; identity_propagation.01 |

## Per-scenario intent

Each YAML `description` gives the full expected decision table and the upstream doc section it comes from.

| Scenario | Intent |
|---|---|
| identity_propagation.01_three_hop_attribution_with_denial | alice→orchestrator→planner→worker. Allowed read_email and denied grant_permission are both attributed to alice (uid and email). THREAT_MODEL §4.6. |
| identity_propagation.02_interleaved_users_shared_agent | alice and carol alternate through one agent name. Each decision uses that call's identity: carol's read_email is denied, alice's is allowed. THREAT_MODEL §3. |
| identity_propagation.03_anonymous_denied_attested_allowed | Empty-principal calls are denied, even ones naming a delegated worker. Attested bob (api tier) and alice (subagent) calls are allowed and audited. THREAT_MODEL §5, upstream id.05. |
| per_user_policy_enforcement.01_user_wide_vs_tool_wide_conflict | A user-wide deny conflicts with a tool-wide allow: **deny**. A user-wide allow conflicts with a tool-wide deny: deny. Non-conflicting cells are allowed. |
| per_user_policy_enforcement.02_user_tool_rule_most_specific | A user+tool allow beats both a user-wide deny and a tool-wide deny. It doesn't spread to other tools or users, and it can't grant a missing scope. |
| per_user_policy_enforcement.03_revoke_then_regrant | A user+tool revocation applies on the next call and affects only that tool and user. A re-grant restores access. THREAT_MODEL §4.10. |
| delegation_provenance.01_denied_call_chain_and_actor | The same worker makes one allowed and one denied call. Both rows carry [orchestrator, worker] and alice. |
| delegation_provenance.02_sibling_chains_different_depth | A 1-hop allowed branch and a 2-hop denied branch keep distinct, complete chains. |
| delegation_provenance.03_chain_does_not_stick_to_direct_call | A direct call between two delegated calls gets chain []. The later delegated deny keeps its chain. |
| scope_inheritance.01_two_hop_narrowing_subagent_allowed | orchestrator→specialist [r,w,email.read]→reader [r]. The reader's write and email calls are denied; the specialist's write and bob's direct send are allowed. |
| scope_inheritance.02_child_cannot_reexpand | A middle hop tries to re-add email.send. The child's send is denied, its read is allowed, and the user's own direct send is allowed. |
| scope_inheritance.03_per_user_intersection_and_empty_grant | The same broad delegation gives carol read-only and bob read/write. An empty delegation grants nothing. |
| rate_limit_cascade.01_interleaved_denials_distinct_subagents | A subagent limit of 3 applies per user across distinct agent names. An interleaved scope denial doesn't consume budget. Interactive calls are unaffected. |
| rate_limit_cascade.02_tier_buckets_do_not_collapse | Limits are interactive 5, api 2, subagent 60, background denied. Using interactive calls first must not exhaust api, and the api limit binds only api calls. |
| rate_limit_cascade.03_user_override_limit_under_fanout | A per-user override of 5/min survives a 4×5 fan-out. Another user's 11 calls are all admitted. Throttles are audited. |
| audit_completeness.01_every_attempt_one_complete_row | Allowed, scope-denied and delegated-denied attempts each get a row with the minimum fields and trace_id. Denied rows also carry a reason. |
| audit_completeness.02_denied_deep_delegation_forensics | A tier-denied call three hops deep gets a row with human actor, email, reason, trace_id and full chain. |
| audit_completeness.03_throttle_and_policy_denials_logged | Rate-limit denials and scope denials each leave deny rows next to the allow rows. |
| fail_mode_discipline.01_closed_before_blip_after_during | fail_closed: normal decisions before any outage and after a 5s outage, then deny-all (two users) during a 60s outage. |
| fail_mode_discipline.02_open_does_not_stick | fail_open: policy denials hold before and after the short outage. An in-policy call proceeds during the long outage and is audited. |
| fail_mode_discipline.03_5xx_per_tenant_fail_mode | During a 5xx outage, fail_closed tenant-a denies and fail_open tenant-b allows. Both evaluate normally beforehand. |
| cross_tenant_isolation.01_same_uid_different_scopes | uid user-dana has write in tenant-b but not in tenant-a. The tenant-b call is made first to poison uid-keyed caches. Audit rows are tagged per tenant. |
| cross_tenant_isolation.02_same_uid_policy_and_role_scoped | Admin in b, restricted member with a subagent deny override in a. Role, scope and override all stay in their own tenant. |
| cross_tenant_isolation.03_same_uid_revocation_isolated | Revoking and re-granting the uid in tenant-a never changes the same uid's decisions in tenant-b. |

## Judgment calls (upstream ambiguous; the conservative reading was chosen)

1. **User-wide vs tool-wide precedence** (per_user.01). These rules are incomparable in specificity, and `types.py` only says that user_tools beats workspace.tools.
   The upstream per_user_policy_enforcement.03 *description* gives the order `user.tools > workspace.tools > user.defaults > workspace.defaults`.
   Under that order, a tool-wide allow would beat a user-wide deny. No upstream assertion depends on it.
   **We require deny** (XACML deny-overrides). This is the one place where the suite knowingly **diverges from a stated upstream ordering**.
   A system that implements the upstream-described order will fail exactly one assertion here (alice/read_file).
2. **Policy allow cannot grant a missing scope** (per_user.02): permission and scope are conjunctive. Upstream per_user.04 says denial is "on scope grounds, not tier grounds".
3. **`policy_change` naming both user and tool** targets the user+tool layer only, not the user-wide layer (per_user.03).
4. **No implicit re-attribution** (identity.03): a call with an empty principal that names an agent from alice's delegation is denied. It is not inferred to be alice.
5. **Empty `delegated_scopes: []` means no authority**, not "inherit everything" (scope.03).
6. **Task narrowing is required.** Upstream scope_inheritance.04 calls delegation-scope enforcement "partial", but property (b) needs it, so scope.01–.03 require it.
   A product without declarative delegation scopes should declare this N/A rather than fail silently (SCORING §4).
7. **Rate-limit tolerance.** Upstream accepts +3 over a ceiling of 60 (5%). For small ceilings (2–5) we accept **+1**, the "~1 request over" race upstream describes.
   The call two past the ceiling must be denied (rate.01, .02, ac.03), and fan-out allows at most limit+1 (rate.03).
8. **Rate buckets are per tier** (rate.02), following the upstream rate_limit_cascade.03 description "If the limiter collapses tiers into one bucket, this fails".
   Whether buckets span tools is left open: counted calls are always on one tool.
9. **Fan-out tier is undefined upstream**, so the per-user override in rate.03 is set on every tier.
10. **Outage timing.** There is no "gateway recovered" action. We reuse the upstream convention: a 5s `gateway_failure` is over by the next call (fail_mode.04 expects allow) and a 60s one is still active (fail_mode.01 expects deny).
    So "after" is tested after a short outage and before the long one. No call follows the long outage.
11. **`gateway_failure` is global** across tenants: it has no tenant field and the gateway is shared (THREAT_MODEL §1). See fail_mode.03.
12. **fail_open plus a policy-denied call during an outage is not asserted** (fail_mode.02): upstream doesn't define it.

## Scorer limitations that shaped the design

- `rate_limited_count` has only an upper bound. Lower bounds come from `tool_allowed` on named calls (before saturation) or on other users' fan-outs (rate.03).
  A limiter that throttles *alice's* whole fan-out would still pass rate.03's alice assertion; the other assertions in the file cover over-aggressiveness.
- Audit filters can't select by agent_name or tier, and `audit_attribution` / `delegation_chain` apply to **every** row for a tool.
  So scenarios give each distinct chain or actor its own tool.
- Nothing can assert "no row attributes X to Y" except per-tool `audit_attribution`. So in identity.03, anonymous-call attribution isn't asserted negatively.

## Trivial-outcome validation

`validate_trivial.py` loads all 24 files with upstream `load_all` and scores four synthetic `RunOutcome`s with upstream `score_scenario`:

- **empty**: no outcomes and no audit rows.
- **all-allowed** / **all-denied**: one `ToolOutcome` per attempted call (fan-outs expanded), all with the same decision.
  Each call also gets one `AuditEntry` with plausible fields: correct tenant, uid, email and delegation chain, plus timestamp, trace_id and reason.
  With those fields right, only the decisions can cause a failure.
- **oracle**: decisions taken from the scenario's own assertions. This is a satisfiability check that shows no scenario contradicts itself.

Result: **no trivial outcome passes any scenario**, and the oracle passes all 24. No fixes were needed after the first run.
Cells show how many assertions failed.

| scenario | empty | all-allowed | all-denied | oracle |
|---|---|---|---|---|
| `audit_completeness.01_every_attempt_one_complete_row` | FAIL (9 failed) | FAIL (4 failed) | FAIL (2 failed) | PASS |
| `audit_completeness.02_denied_deep_delegation_forensics` | FAIL (7 failed) | FAIL (2 failed) | FAIL (1 failed) | PASS |
| `audit_completeness.03_throttle_and_policy_denials_logged` | FAIL (8 failed) | FAIL (4 failed) | FAIL (3 failed) | PASS |
| `cross_tenant_isolation.01_same_uid_different_scopes` | FAIL (5 failed) | FAIL (2 failed) | FAIL (3 failed) | PASS |
| `cross_tenant_isolation.02_same_uid_policy_and_role_scoped` | FAIL (7 failed) | FAIL (3 failed) | FAIL (4 failed) | PASS |
| `cross_tenant_isolation.03_same_uid_revocation_isolated` | FAIL (5 failed) | FAIL (2 failed) | FAIL (3 failed) | PASS |
| `delegation_provenance.01_denied_call_chain_and_actor` | FAIL (6 failed) | FAIL (2 failed) | FAIL (1 failed) | PASS |
| `delegation_provenance.02_sibling_chains_different_depth` | FAIL (5 failed) | FAIL (1 failed) | FAIL (1 failed) | PASS |
| `delegation_provenance.03_chain_does_not_stick_to_direct_call` | FAIL (6 failed) | FAIL (1 failed) | FAIL (2 failed) | PASS |
| `fail_mode_discipline.01_closed_before_blip_after_during` | FAIL (5 failed) | FAIL (3 failed) | FAIL (2 failed) | PASS |
| `fail_mode_discipline.02_open_does_not_stick` | FAIL (5 failed) | FAIL (2 failed) | FAIL (2 failed) | PASS |
| `fail_mode_discipline.03_5xx_per_tenant_fail_mode` | FAIL (5 failed) | FAIL (2 failed) | FAIL (3 failed) | PASS |
| `identity_propagation.01_three_hop_attribution_with_denial` | FAIL (6 failed) | FAIL (2 failed) | FAIL (1 failed) | PASS |
| `identity_propagation.02_interleaved_users_shared_agent` | FAIL (6 failed) | FAIL (2 failed) | FAIL (3 failed) | PASS |
| `identity_propagation.03_anonymous_denied_attested_allowed` | FAIL (6 failed) | FAIL (2 failed) | FAIL (4 failed) | PASS |
| `per_user_policy_enforcement.01_user_wide_vs_tool_wide_conflict` | FAIL (5 failed) | FAIL (3 failed) | FAIL (2 failed) | PASS |
| `per_user_policy_enforcement.02_user_tool_rule_most_specific` | FAIL (6 failed) | FAIL (3 failed) | FAIL (3 failed) | PASS |
| `per_user_policy_enforcement.03_revoke_then_regrant` | FAIL (5 failed) | FAIL (1 failed) | FAIL (4 failed) | PASS |
| `rate_limit_cascade.01_interleaved_denials_distinct_subagents` | FAIL (7 failed) | FAIL (4 failed) | FAIL (4 failed) | PASS |
| `rate_limit_cascade.02_tier_buckets_do_not_collapse` | FAIL (6 failed) | FAIL (3 failed) | FAIL (4 failed) | PASS |
| `rate_limit_cascade.03_user_override_limit_under_fanout` | FAIL (3 failed) | FAIL (2 failed) | FAIL (2 failed) | PASS |
| `scope_inheritance.01_two_hop_narrowing_subagent_allowed` | FAIL (5 failed) | FAIL (2 failed) | FAIL (3 failed) | PASS |
| `scope_inheritance.02_child_cannot_reexpand` | FAIL (4 failed) | FAIL (2 failed) | FAIL (2 failed) | PASS |
| `scope_inheritance.03_per_user_intersection_and_empty_grant` | FAIL (4 failed) | FAIL (2 failed) | FAIL (2 failed) | PASS |

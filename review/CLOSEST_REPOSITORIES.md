# Closest implementations and benchmark audit

Reviewed 2026-09-27. This follow-up strengthens the earlier documentation survey with **seven pinned local checkouts and selected source sections in 27 files**. It is not an exhaustive reading of those repositories, a security certification, or a performance ranking. [The manifest](evidence/closest-source-manifest.json) records revisions, source hashes, inspected sections, and local checkout locations. Competitor application code was not changed.

## What this changes for Foundry

**Action-bound approvals, version checks, and one-time approval consumption already exist in a close open-source governance toolkit.** They remain useful engineering requirements, but are insufficient as Foundry's proposed research novelty. Likewise, resumable graphs, typed tool boundaries, subagent context controls, and persistent human feedback are established capabilities.

The immediate product opportunity is to make these capabilities work consistently through Foundry's public API and supported adapters, with an easy development-to-deployment path. That requires fixing the reproduced request-control and delegation defects before advertising interchangeable enterprise runtimes. A separate paper must demonstrate a narrower contribution against strong implementations, including existing governance components.

## Pinned comparison

The links below point to source at the inspected commit. “Observed” means source-inspected; it does not mean that the entire competitor deployment was executed or validated locally.

| Repository / revision | Observed implementation | Consequence for Foundry |
| --- | --- | --- |
| LangGraph `7daa3ab49d67` | [`interrupt`](https://github.com/langchain-ai/langgraph/blob/7daa3ab49d678a5da75edb08baa87db4a2be52c3/libs/langgraph/langgraph/types.py#L887) persists a pause through a configured checkpointer; resume restarts the node. [`TimeoutPolicy`](https://github.com/langchain-ai/langgraph/blob/7daa3ab49d678a5da75edb08baa87db4a2be52c3/libs/langgraph/langgraph/types.py#L461) explicitly uses cooperative cancellation. A selected test exercises task/interrupt/resume ordering. | Reuse lifecycle machinery when this engine is selected. Test effects before an interrupt and crash boundaries; persistence alone is not exactly-once external execution. Treat hard process isolation separately from a cooperative timeout. |
| Pydantic AI `69eb81bde23c` | [`ApprovalRequiredToolset`](https://github.com/pydantic/pydantic-ai/blob/69eb81bde23c8db52ee8617217bdaf46bf0468df/pydantic_ai_slim/pydantic_ai/toolsets/approval_required.py) wraps tool calls using context and an approval predicate. [`DurabilityEngineSpec`](https://github.com/pydantic/pydantic-ai/blob/69eb81bde23c8db52ee8617217bdaf46bf0468df/pydantic_ai_slim/pydantic_ai/durable_exec/_spec.py) declares codecs, tool lifecycles, sequencing, and unsupported runtime toolsets. | Adopt explicit adapter capability declarations and lifecycle contracts. An unsupported security control should produce a clear error. Do not equate type checking with authorization or safe side effects. |
| CrewAI `4ed2abc7bbf5` | [`SQLiteFlowPersistence`](https://github.com/crewAIInc/crewAI/blob/4ed2abc7bbf504a634d3b733f2a97e0fbe8d44ec/lib/crewai/src/crewai/flow/persistence/sqlite.py) stores flow state and pending feedback. Its [`guardrail serializer`](https://github.com/crewAIInc/crewAI/blob/4ed2abc7bbf504a634d3b733f2a97e0fbe8d44ec/lib/crewai/src/crewai/utilities/guardrail.py#L12) drops callable guardrails with a warning; selected tests confirm this intended serialization behavior. | A Foundry CrewAI adapter must restore required controls explicitly or reject execution. Test the actual checkpoint/restore path. This selected behavior does not establish that every CrewAI persistence path drops every control. |
| Microsoft Agent Framework `6f1522a50b66` | [`_matches_rule`](https://github.com/microsoft/agent-framework/blob/6f1522a50b66f117da34cc25ea299ba24a528b15/python/packages/core/agent_framework/_harness/_tool_approval.py#L319) matches approval rules to tool/server and optionally serialized arguments. [`FileCheckpointStorage`](https://github.com/microsoft/agent-framework/blob/6f1522a50b66f117da34cc25ea299ba24a528b15/python/packages/core/agent_framework/_workflows/_checkpoint.py#L505) has path validation, restricted type deserialization configuration, and temporary-file replacement. | Compare approval scope, persistence failure handling, and serialization trust boundaries explicitly. These source sections do not establish a distributed transaction with downstream services. |
| Deep Agents `0c756c4f8037` | [`graph.py`](https://github.com/langchain-ai/deepagents/blob/0c756c4f8037d9132c96b5191d4ef1f5a57a42f8/libs/deepagents/deepagents/graph.py#L661) assembles subagent filesystem, summarization, and optional middleware; declarative and compiled subagents follow different paths. [`subagents.py`](https://github.com/langchain-ai/deepagents/blob/0c756c4f8037d9132c96b5191d4ef1f5a57a42f8/libs/deepagents/deepagents/middleware/subagents.py#L772) filters shared state and supports isolated or forked message context. | Compete on a usable default harness, not the mere existence of subagents or context compaction. Test information and authority propagation independently for each subagent mode. |
| Microsoft Agent Governance Toolkit `6b644564d112` | [`ActionBinding`](https://github.com/microsoft/agent-governance-toolkit/blob/6b644564d112b879e48183bc1655fcf73c86d23e/agent-governance-python/agent-mesh/src/agentmesh/governance/approval_protocol/binding.py) covers actor/subject, target/schema, operation, and parameters. [`validate_for_execution`](https://github.com/microsoft/agent-governance-toolkit/blob/6b644564d112b879e48183bc1655fcf73c86d23e/agent-governance-python/agent-mesh/src/agentmesh/governance/approval_protocol/coordinator.py#L311) checks expiry, action digest, policy version, approval-chain version and integrity, and consumes an approval. Selected tests cover changed arguments/versions and reuse. | Direct prior art for the proposed approval/revalidation mechanism. Include this implementation as a baseline or integration candidate. Consuming an approval is not the same as atomically committing an external action. Delegation code also exists; its compatibility-mode signature checks must not be described as an unconditional cryptographic guarantee. |
| AgentGovBench `e0ce93ae1753` | [Scorer](https://github.com/agentic-control-plane/agentgovbench/blob/e0ce93ae175376d7847c69a64d0c36bdfa6ca717/benchmark/scorer.py), scenario loader, runner interface, vanilla runner, and selected scenarios inspected. Its [LangGraph runner](https://github.com/agentic-control-plane/agentgovbench/blob/e0ce93ae175376d7847c69a64d0c36bdfa6ca717/runners/langgraph_native.py) dispatches a LangChain tool stub and records outcomes; it does not construct a StateGraph. | Useful public governance scenarios, but runner and evidence quality constrain interpretation. A score from this runner is not a general comparison against a fully configured LangGraph application. |

Root license files are recorded in the manifest; this review reuses no competitor implementation code. Check package-specific terms and preserve required attribution before any future code reuse.

## Free public baseline: reproduced, with limits

The unmodified AgentGovBench CLI's **vanilla runner scored 13/48**, with exactly all 48 expected scenario IDs represented. This is an upstream no-governance baseline, **not a Foundry result**. It used no LLM or vendor service. [Raw results and trajectories](evidence/agentgovbench-baseline-complete/vanilla.json), [CLI output](evidence/agentgovbench-baseline-complete/vanilla-cli.txt), and [environment/source manifest](evidence/agentgovbench-baseline-complete/manifest.json) are retained.

The first invocation failed because the temporary environment lacked `click`; [that failure](evidence/agentgovbench-baseline/vanilla-cli.txt) is retained. After installing `click==8.1.8`, the full invocation completed. No failed scenario was dropped or selectively rerun in the successful baseline.

| Category | Vanilla passing / attempted |
| --- | --- |
| Identity propagation | 0 / 6 |
| Per-user policy enforcement | 1 / 6 |
| Delegation provenance | 0 / 6 |
| Scope inheritance | 1 / 6 |
| Rate-limit cascade | 3 / 6 |
| Audit completeness | 1 / 6 |
| Fail-mode discipline | 3 / 6 |
| Cross-tenant isolation | 4 / 6 |

These numbers illustrate why category names cannot substitute for inspecting assertions. The no-governance runner does not establish tenant isolation merely because four scenarios in that category pass.

### Scorer and runner limitations

The accompanying [executable audit](evidence/reproduce_agentgovbench.py) invokes the unchanged scorer with deliberately empty observations. [Recorded counterexamples](evidence/agentgovbench-baseline-complete/scorer-audit.json) show:

1. Five complete scenarios pass an empty `RunOutcome`: the no-runner-error scenario, audit separation, and three rate-limit scenarios. Some express only an upper bound or absence of errors; passing them supplies no positive evidence that work occurred.
2. The tenant-isolation assertion accepts an empty audit and an audit entry with `tenant=None`. It checks disallowed observed pairs but does not require the expected entries or inspect real data-store isolation.
3. The rate-limit assertion accepts zero observed attempts. Additional workload-completeness and allowed-work assertions are needed to distinguish enforcement from doing nothing.
4. Source inspection of the CLI shows that setup/execution exceptions cause a scenario to be skipped before aggregation. Our wrapper checks exact ID coverage and rejects an incomplete baseline. Future Foundry runs must retain exceptions as failures in a fixed denominator.
5. The inspected LangGraph runner turns `ParallelFanOut` into a sequential loop. It cannot establish concurrent rate-limit correctness or parallel execution speed. Its `allowed=True` outcome is also not a sufficient record of successful tool execution.

Keep any stricter checks in a separately reported supplemental suite; do not silently change the official scorer and label the result an official score. Publish official score, scenario coverage, unsupported controls, observed effects, and supplemental results together. An authentic Foundry adapter must exercise real authority propagation and audit events, not manufacture them from expected scenario values.

Reproduce in an environment with Python >=3.10, PyYAML, requests, and click:

```bash
git clone https://github.com/agentic-control-plane/agentgovbench /tmp/agentgovbench
git -C /tmp/agentgovbench checkout e0ce93ae175376d7847c69a64d0c36bdfa6ca717
python review/evidence/reproduce_agentgovbench.py \
  --upstream /tmp/agentgovbench --out /tmp/agentgovbench-reproduction
```

The output directory must be new, so a repeat cannot overwrite earlier evidence.

## Closer paper-method comparison

These are selected method/limitation readings, not reproduced results or an exhaustive literature search. Versions are pinned deliberately.

| Source | Observed overlap and limitation | Required comparison |
| --- | --- | --- |
| [ActGov v2](https://arxiv.org/html/2609.24446v2), methodology and limitations | Offline policy construction uses a finite record abstraction and SMT checks; runtime evaluates the policy after context abstraction. Guarantees depend on the abstraction and a trusted mandatory enforcement boundary. Cross-environment policy transfer loses substantial utility in its experiments. | Action authorization and policy validation are established prior art. Evaluate held-out domains and useful completion alongside violations. |
| [AgenTRIM v2](https://arxiv.org/html/2601.12449v2), §§3.1–3.2, limitations and selected ablations | Execution-grounded tool inventory and control metadata drive tool exposure and selective judging. Annotation quality affects the safety/utility balance; mediation adds cost and latency. | Compare selective verification to its risk-aware tool controls. Include metadata errors, discovery omissions, and cost of the verifier. |
| [SABER v1](https://arxiv.org/html/2512.07850v1), §§4.1–4.2 | Mutation-focused confirmation, targeted instruction reflection, and retrieved trajectory summaries address errors at consequential actions. The main model produces another action after user feedback. | Mutation gating and context filtering are not new. Compare the actual action executed with what was verified, including changes during approval delays. |

## Revised research gate

Reject “versioned action approval” as the sole novelty claim. The remaining **hypothesis**, not an established gap, is efficient revalidation of task evidence when external facts change and dependency information is incomplete. Its controller must operate behind hard authorization, not choose whether authorization runs.

Before implementation, define what a factual dependency means, what updates can be observed, and what happens for an unversioned or unknown dependency. Compare against: always reverify; mutation-only verification; risk-triggered verification; full cache invalidation on any change; deterministic dependency invalidation; and an existing action-bound approval component. Include data/version races and the crash after an effect but before its receipt is saved. Do not claim exactly-once behavior without downstream idempotency or transactional support.

Success would be a reproducible improvement in useful, safe task completion or verification cost at a matched risk level. If simple deterministic invalidation matches the adaptive controller, report that result and keep the simpler product. A source survey cannot establish that an idea exists nowhere else.

## Product work that should precede a paper claim

1. Repair F01 request-control propagation and F03 delegated authority across public entry points; unsupported adapter controls must fail explicitly.
2. Establish replay-safe effect handling, restore-time policy checks, and process isolation for untrusted tools. Close the remaining defects in [the review](REPOSITORY_REVIEW.md).
3. Build two complete startup reference applications with tenant-aware traces, approval screens, measured business outcomes, and deployment instructions.
4. Run the matched WorkBench pilot with a total API spending cap, then freeze the method before held-out evaluation. Cost, success, unwanted effects, failures, and latency all belong in the result table.
5. Add a real Foundry governance adapter and independent evidence-completeness checks. Do not count a vanilla-baseline reproduction as Foundry validation.

Current status: closer prior art identified; free baseline reproduced; paid Foundry benchmark results and research novelty remain unestablished.

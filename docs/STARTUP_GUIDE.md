# Start with a bounded, observable agent

Use Agent Foundry first for an internal workflow with explicit tool permissions,
synthetic evaluation data and human-approved writes. Native Python and LangGraph
are the tested execution paths. This guide distinguishes a working development
setup from the additional work required to operate a customer-facing service.

## Install and verify

Use Python 3.11 or 3.12 in a dedicated environment. From the repository root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test,langgraph,schema]'
python -m pytest tests/test_execution_controls.py tests/test_eval_evidence.py tests/test_context_admission.py -q
```

Those checks use scripted providers and synthetic tools. They exercise real
execution paths without provider keys, inference charges or external business
effects. Passing them validates selected contracts, not the quality of your model.
The release evidence records 620 passes and 25 skips in the broader local suite.

## Choose the execution path

| Path | What it supplies | Deployment limit |
| --- | --- | --- |
| `Agent(..., runtime="native")` | Python execution with Foundry policy, request scope, budgets and evaluation | Local cancellation is cooperative; distributed ownership and native topology durability need additional work |
| `Agent(..., runtime="langgraph")` | Foundry controls around its LangGraph execution graph | Configure a durable checkpointer and trusted control reconstruction; not every topology has identical pause behavior |
| LangChain quickstart / `to_langchain_tool` | Direct LangChain execution / callable conversion | Raw exports do not retain Foundry's policy, approvals, audit or request budget |
| CrewAI / AutoGen agent-as-tool | Interoperability at the outer tool boundary | Opaque inner actions are not automatically governed by Foundry |

Start from the existing [commerce application](../examples/commerce_agent/README.md)
for approved writes and the [implementation guide](IMPLEMENTATION_GUIDE.md) for
tool and workflow construction. Install the chosen provider extra only when
moving to a real model. Model access, structured-tool support and billing need
verification for that account; the benchmark's custom Responses transport is
separate from the production gateway. Claude did not complete the recorded live
comparison because the supplied account required workspace configuration.

## Define the application's contract

Begin with one business outcome, such as updating one permitted ticket after an
operator approves. Register only the required tools. Set a restrictive tool
allowlist, scopes, step limits and required approvals. Tool implementations must
validate resource ownership, arguments and business preconditions against current
backend state; an LLM instruction is not resource authorization.

Construct `ExecutionContext` from authenticated server data. Do not accept user,
tenant or permission claims from model output or untrusted request fields. Supply
current controls on every run and resume. Persisting a conversation does not
persist a live cancellation token, create authenticated consent, or reserve a
distributed dollar budget. A resumed approval must be checked against the current
caller and application policy.

Keep tools trusted. In-process Python and thread timeouts are not a sandbox.
Use an external isolated execution service before enabling generated or untrusted
code. Match each deployment to the supported capability matrix instead of assuming
that selecting an adapter preserves every Foundry control.

## Make the release gate measure an outcome

Build a small synthetic dataset containing allowed work, prohibited work, missing
information, failed tools, revoked permission and approval/resume cases. Check the
business system's final state and unintended changes as well as the agent's text.
A correct sentence or a proposed tool name does not prove that a write succeeded.

Use `run_eval` / `foundry eval` for repeatable criteria and
[the migration guide](../review/EVALUATION_MIGRATION.md) for coverage semantics.
Missing oracles remain unmeasured; requested threshold metrics require full
coverage unless explicitly configured otherwise. The default gate rejects empty
or unlabeled evaluations. Core cost deltas are sequential budget accounting, not
an invoice or a predictive spending reservation.

Before increasing autonomy, compare the new version with the last deployed
version on a frozen dataset. Keep failures and costs. Public WorkBench subsets
help assess task execution, but do not replace your application's permission,
recovery and business-outcome tests.

## Operate a first pilot

Run a controlled internal pilot with an identified owner, approved-write review,
per-request trace IDs, restricted credentials, error/cost visibility and a way to
disable tool admission. Measure successful outcomes, unwanted effects, approval
waits, p95 latency, provider/tool errors and known versus missing costs separately.
Exercise revoked permissions, dependency outages and failed writes before inviting
customers. Review [backup and recovery](BACKUP_DR.md) and
[monitoring requirements](../review/SECURITY_EVALUATION_MONITORING.md).

Before multi-worker or multi-tenant production, close the applicable gaps in
[current status](../review/CURRENT_STATUS.md): fenced state ownership, durable
effect reconciliation, scoped credentials, authenticated approvals, remote budget
controls, isolation and recovery/load evidence. The repository contains useful
primitives and reference deployment material; it does not yet provide a validated
managed enterprise platform with all these guarantees.

Industry packs should supply schemas, permissions, integrations and outcome
datasets. Startups can reuse the runtime, but must still decide which actions their
application may take and how successful work is independently verified.

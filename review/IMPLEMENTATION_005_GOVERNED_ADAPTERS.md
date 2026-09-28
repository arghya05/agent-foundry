# I005: governed exports and effective action policy

This is enterprise hardening, not a novel research algorithm or a benchmark win.

Four actual-runtime regressions reproduced a remaining request-policy defect:
native and LangGraph could execute despite a stricter request draft-only policy
or request approval requirement. The PDP now applies the effective request policy
and configured/custom guard, with hard denials preceding approval from either.
External policy input now includes tenant, roles and a copied argument object.

`GovernedToolGateway` and the LangChain export add a current-context action
boundary with policy, schema, budget, cancellation, scope, audit and breaker checks.
Raw exports require an explicit unguarded opt-in. Tests inspect real tool effects,
current role revocation, caller/tenant binding, async tools, cached output validation,
parent budgets, unsafe retries and unsupported-capability rejection.

The initial new suite recorded 16 failures: four demonstrated policy defects and
twelve tests for the then-absent gateway API. The first implementation retained
four failures caused by using the wrong circuit-breaker method. After correction,
the related suite passed 110 cases / 2 skips; expanded coverage passed 115 / 2.
The final adapter selection passes 23 cases. Type checking covers 59 source files;
source and changed-test lint pass. See raw `iteration-005*` evidence files for exact
commands/output. The complete suite passes **643 cases with 25 skips** in the
same local environment; [full output](evidence/iteration-005-full-suite.txt) is retained.

This does not close durable effects, authenticated approval lifecycle, hard
isolation, opaque CrewAI internals, or real-service load/recovery acceptance.
Approval-required exports stop without executing; they do not yet resume using
an approval token. [Usage and migration](../docs/GOVERNED_TOOL_ADAPTERS.md) state the
supported boundary. WorkBench measurements predate I005; no measured task-score
improvement is attributed to this repair.

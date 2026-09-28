# Research gate update: stronger overlap found

The additional search on 2026-09-28 found close work beyond the original survey.
This changes the novelty assessment. General admission checks, stale-evidence
revalidation, dependency-aware repair and preserved handoff obligations cannot be
presented as new solely because they are integrated into Foundry.

| Work | Reading depth | Consequence |
| --- | --- | --- |
| [Mnemosyne / Agentic Transaction Processing, v3](https://arxiv.org/html/2607.00269v3) | Abstract, model, guarantee assumptions, reactive repair and artifact instructions; selected source reproduction completed | Deterministic admission, current state, evidence-preserving repair and declared dependency/conflict scopes directly overlap. Its guarantees are relative to trusted constraints and complete conflict scopes. Incomplete constraints are an explicit limitation, not an omission discovered by this review. |
| [Do Not Restart / CFRC, v1](https://arxiv.org/html/2609.13800v1) | Abstract and selected methodology/conditional-guarantee sections | Preserving progress and obligations through handoff, evidence-linked continuation and live receipts already have a close method. Its guarantee depends on contract construction and execution assumptions. Reported performance has not been reproduced here. |

These are preprints as inspected; no acceptance is inferred. OpenReview search
also returned a potentially relevant evidence/dependency paper, but access was
blocked by browser verification. Its method has not been verified and is not used
as supporting evidence. The survey remains scoped, not exhaustive.

## Decision

**Do not promote the current design-only selective-revalidation proposal to an
original-method claim.** First reproduce the closest available artifact and seek a
distinction it does not already implement. A generic cache with dependency versions
is also established systems engineering, so implementing it alone would not pass.

A possible narrower investigation concerns incomplete dependency declarations:
how to measure missing constraints, detect when evidence reuse is unjustified,
and trade additional verification against useful completion without weakening hard
authorization. This is a question, not a discovered solution. Complete dependency
information must not be smuggled into the experimental method through a hidden
oracle unavailable to ordinary agents.

## Required next evidence

1. Pin and execute the nearest artifact's documented deterministic path, recording
   installation failures and limits. Inspect how it handles incomplete declarations.
2. Specify a concrete failure family with an independent environment-state oracle;
   disclose which state/dependency information each method can observe.
3. Compare mandatory controls alone, always revalidate, full invalidation,
   deterministic dependency reuse, and the closest executable method. Match budgets,
   observations, task generation and tuning access.
4. Implement a candidate only after defining its additional mechanism. Separate
   omission detection, reuse decisions and adaptation in ablations. Include
   counterexamples where the candidate must defer rather than assert safety.
5. Evaluate untouched templates and event combinations, multiple model families,
   repeated trials, and original public tasks. Modified stress tasks get a separate
   label and cannot become an official benchmark score.
6. Report whether the proposed advantage survives the strongest simple baseline.
   If it does not, reject the hypothesis and retain the negative result.

The original conference-level objective remains open. The current empirical paper
is a working record, not evidence that these milestones have been achieved.

## Artifact reproduction result

Pinned Mnemosyne tag `arxiv-atp-rq1-rq9b-r8-v2` resolves to
`7aab09c43c1672c01b415b4987cd2d1d9a6fd0a4`. The documented RQ1–RQ8 tests plus
the production-comparator tests produced **10 passes**, with a retained warning
about the initially absent async plugin. Inspection shows the RQ tests use
self-contained abstractions; the comparator checks a declared coverage table,
not live database/workflow performance. Eight additional selected core tests
exercise real validation, projection, idempotency and outbox APIs and **pass**
after installing the async plugin.

The paper-named recursive-recovery and data-scale test paths are absent at this
pinned tag. No alternative revision was silently substituted. No live LLM,
Temporal service or PostgreSQL comparison was run. These results verify selected
artifact paths, not a Foundry advantage or every reported paper result.
[Provenance](evidence/mnemosyne-source-reproduction-20260928.json),
[documented-path output](evidence/mnemosyne-reproduction-20260928.txt), and
[core-test output](evidence/mnemosyne-core-validation-20260928.txt) are retained.

Further research is paused while the requested release and enterprise repairs
are prioritized. This gate remains open.

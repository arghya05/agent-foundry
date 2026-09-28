# Working dossier: patent and research publication

Prepared 2026-09-28 in response to the owner's stated intention to pursue both.
**Working technical record, not a patent application, legal opinion, invention
ownership determination, or claim that the contribution is novel.** The owner subsequently reaffirmed public GitHub push authorization after
this disclosure concern was explained; no legal clearance or filing is inferred.

## Publication state and immediate decision

The starting repository is already on GitHub. The review and implementation work
in this working tree has not been committed or pushed by this review session.
No arXiv submission, conference submission, patent filing, or Hugging Face upload
has been made. The date and content of earlier public disclosures and any earlier
filing are unknown; the owner and counsel need to establish them.

Public disclosure before filing can destroy novelty, with exceptions depending
on jurisdiction. Patentability also depends on inventive step, eligible subject
matter, utility and adequate disclosure. These are different questions from
benchmark accuracy. Consult qualified patent counsel about the actual invention,
target countries, earlier disclosures and filing sequence before a public release.
Sources: [WIPO protection guidance](https://www.wipo.int/en/web/patents/protection)
and [WIPO patent FAQ](https://www.wipo.int/en/web/patents/faq_patents), checked
2026-09-28. This review makes no jurisdiction-specific legal conclusion.

The later explicit instruction to push supersedes the earlier local hold.
Code, tests, research and evidence may be released within that authorization. This does not assert that the development environment, external
model provider or other service creates legal confidentiality. Existing model
evaluations have already transmitted public benchmark tasks and a generic
execution-guidance prompt to the selected provider; the session has not submitted
a patent-specific invention description to a model endpoint or a patent office.

## Technical disclosure worksheet

| Field | Current evidence / information still needed |
| --- | --- |
| Working subject | Reliable authority and task-evidence handling across agent execution, delegation, interruption and recovery |
| Product artifact | Native Python and LangGraph implementations in Agent Foundry; LangChain and CrewAI have distinct, limited integration guarantees |
| Baseline | Repository commit `2623ea1b2fe2d93beb1976c0d4e2449891b6900a`; original findings and file hashes in `evidence/` |
| Technical problem demonstrated | Request controls lost at execution boundaries; native delegated caller identity loss; approval bypass of hard deny; incomplete cache identity; retrieval filtering bypass |
| Implemented solution | Process-local execution scopes, authority attenuation, control checks, context propagation, hard-deny precedence, complete request cache keys, and uniform existing retrieval filters |
| Technical evidence | Before/after regressions; 620 full-suite passes / 25 skips; 67 control/batch cases; 24 WorkBench adapter cases; see the implementation report and raw logs |
| Candidate research distinction | Selective revalidation of factual evidence under changed execution state and incomplete dependency declarations, behind mandatory hard authorization |
| Candidate implementation status | Design only; no adaptive verifier or durable factual-evidence engine implemented or benchmarked |
| Inventors and contributions | Owner/counsel to document actual human contributions and dates; repository authorship alone does not decide legal inventorship |
| Ownership and agreements | Unknown; identify employment, contractor, collaborator, funding and assignment obligations with counsel |
| First disclosure / filing | Unknown; enumerate repository releases, talks, preprints, demonstrations, customer disclosures, and any applications with exact dates and content |
| Third-party material | Pinned comparator and benchmark source/license records; no competitor implementation copied in this review; benchmark evidence needs attribution |
| Filing territories and strategy | Owner/counsel decision; no country inferred from timezone or filesystem path |

## Prior art that limits a claim

The following is a technical overlap map, **not an exhaustive patent search**.
Repository and paper comparison does not replace patent-database searching or
analysis of claims and filing/priority dates.

| Feature or proposed claim | Existing overlap | Consequence |
| --- | --- | --- |
| Native/multiple execution runtimes | Established framework and adapter patterns | Not by itself an invention or a paper contribution |
| Context propagation and least authority | Established systems/security concepts; current Foundry repairs apply them | Describe the engineering implementation precisely; do not claim the general idea as new |
| Durable interrupts and human feedback | LangGraph, CrewAI, Pydantic AI and Microsoft framework components in the pinned source review | Must be supported baseline capabilities |
| Action-bound approvals, expiry, version checks and one-use consumption | Microsoft Agent Governance Toolkit execution validator | Explicitly excluded as sole novelty |
| Runtime action policies | ActGov and AgentSpec | Compare actual authorization semantics and assumptions |
| Information-flow/capability separation | CaMeL | Distinguish factual dependency tracking from an existing control/data boundary |
| Adaptive tool exposure and status-aware checking | AgenTRIM | Compare against its selective controls, not only an unguarded agent |
| Verification around mutations | SABER | Mutation checks and reflection are not sufficient novelty |
| Risk-triggered mediation | Pro2Guard; other budgeted-verification work | Cost-aware checking alone is not a new claim |
| Cache invalidation and dependency graphs | Established systems mechanisms | A selective revalidation proposal needs a precise additional technical distinction |

The source links, revisions, inspected methods and limits are in
[the closest comparison](CLOSEST_REPOSITORIES.md) and
[research design](RESEARCH_DESIGN_2026.md). Before a novelty claim, extend this to
full methods, related work, available implementations and a counsel-directed
patent search. Do not describe the survey as “all papers and all GitHub.”

## Candidate hypothesis requiring proof

For an authorized consequential action, maintain evidence dependencies with
explicit complete/incomplete/unknown status. External state changes invalidate
affected evidence. Revalidation must treat unknown dependencies conservatively;
hard policy, identity, tenant, approval and effect-deduplication checks cannot be
skipped by a learned decision. The question is whether selective factual
revalidation lowers total verification cost at matched safe useful completion.

This is a research hypothesis, not a settled invention. Complete dependency
tracking, reliable source versions and atomic downstream effects must not be
assumed silently. A version check followed by an unfenced action still races
with an external update. A one-use approval does not guarantee exactly-once
external execution. The implementation and evaluation must represent these limits.

Required comparisons: always verify; mutation-only checks; risk-triggered checks;
full invalidation on any change; deterministic dependency invalidation; an actual
action-bound approval implementation; and the closest available research method.
Only add a learned controller if it improves over the deterministic version.
Required ablations independently remove dependency tracking, invalidation and
adaptation. Include revocation, incomplete dependencies, stale reads, crash/replay,
delayed approvals and external-effect races.

## Evidence and manuscript gate

1. Establish a narrow technical distinction and reject it if prior art already
   covers it. Keep dated design decisions and attributed human contributions.
2. Implement the mechanism and test its assumptions through actual execution.
   Keep product repairs separate from algorithmic changes.
3. Predeclare tasks, split, comparator settings, budgets, repetitions, primary
   metric, minimum meaningful effect and stopping rule before confirmation.
4. Run untouched tasks and transfer settings. Keep failures, retries, unknown
   billing and side effects. Report uncertainty and the limitations of public
   task exposure; do not repeatedly tune on a test split.
5. Package code, source/data hashes, exact environment, per-task outcomes and
   table-generation scripts. Arrange independent reproduction and author review.
6. Record the owner's public-release decision; it does not establish filing status
   or patent rights. Check the chosen venue's current anonymity,
   prior-publication and artifact rules before submission.

No patentability, acceptance at a top conference, broad industry readiness, or
benchmark leadership is established by this dossier. A negative experiment is
part of the record and may rule out a candidate claim.

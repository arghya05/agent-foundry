# Research evidence and experiment log

**2026-09-28 follow-up:** I002/I003/I004 and live campaigns are recorded in [current status](CURRENT_STATUS.md), [implementation](IMPLEMENTATION_20260928.md), [migration](EVALUATION_MIGRATION.md), and [measured outcomes](LIVE_BENCHMARK_RESULTS.md). Original failures and plans below are retained as history.

Status date: **2026-09-28**. Product usefulness comes first. Publication readiness
and benchmark superiority are **not established**. This log extends the original
snapshot review; it does not retrospectively rewrite its evidence.

## Evidence register

| ID | Activity | Observed result | Evidence | Claim supported |
| --- | --- | --- | --- | --- |
| R000 | Original Foundry review | 432 selected tests passed, 2 skipped, 5 missing-extra failures; 16 deterministic observations | [Evidence index](evidence/README.md) | Concrete defects and gaps in the reviewed snapshot |
| W001 | Official WorkBench offline tests | 257 passed in 14.73 seconds | [Raw log](evidence/workbench-official-tests.txt) | Scorer/tool installation passes upstream tests |
| W002 | Re-score committed GPT-4o predictions using official v2 scorer | 476/690 correct, 104 unwanted-side-effect cases; all six domain totals match | [Reproduction JSON](evidence/workbench-baseline-reproduction.json) | Saved upstream baseline reproduced; not Foundry performance |
| W003 | Offline adapter contract tests | 16 passed after adding campaign/resume coverage | [Raw log](evidence/workbench-adapter-tests.txt) | Scripted integration, prompt/schema parity, state isolation, cost guards, and resume |
| W003a | First campaign integration test | 15 passed, 1 failed because environment capture attempted to use an unavailable global `uv` cache | [Intermediate log](evidence/workbench-adapter-integration-first.txt) | Fixed by capturing installed distribution versions without accessing the global cache |
| I001a | New review regression tests on original implementation | 13 failed | [Before log](evidence/iteration-001-before.txt) | Approval precedence and prompt-cache defects reproduced |
| I001b | First post-fix targeted run | 170 passed, 2 skipped, 8 assertion failures | [Intermediate log](evidence/iteration-001-after.txt) | The test incorrectly assumed legacy text-call tool messages carry `ok`; failures retained |
| I001c | Corrected test assertion and targeted validation | 178 passed, 2 skipped | [Validation](evidence/iteration-001-validation.txt) | Denial reasons and absence of effects are checked through real runtimes |
| W004 | Live matched development pilot | OpenAI Responses completed 36 attempts; setup failures retained; Anthropic unavailable | [Live results](LIVE_BENCHMARK_RESULTS.md) | Supersedes original pending plan; no superiority claim |
| R001 | Closest implementation and method comparison | Seven pinned repositories; selected sections in 27 files; selected methods in three papers | [Comparison](CLOSEST_REPOSITORIES.md), [source manifest](evidence/closest-source-manifest.json) | Action-bound approval/version checks already exist; novelty gate revised |
| G001a | First AgentGovBench baseline invocation | Setup failed: missing `click` | [Retained failure](evidence/agentgovbench-baseline/vanilla-cli.txt) | No score from this invocation |
| G001 | Official AgentGovBench vanilla runner after dependency installation | 13/48 passed; exact 48-scenario ID coverage | [Manifest](evidence/agentgovbench-baseline-complete/manifest.json), [results](evidence/agentgovbench-baseline-complete/vanilla.json) | Upstream baseline reproduction only; not Foundry performance |
| G002 | Unmodified AgentGovBench scorer with empty observations | Five scenarios pass; three isolated missing-evidence counterexamples confirmed | [Audit](evidence/agentgovbench-baseline-complete/scorer-audit.json) | A passing assertion can lack positive enforcement evidence |
| W005a | Unknown-tool scoring regression before repair | Three failures, one per arm | [Before log](evidence/workbench-unknown-tool-before.txt) | Journal omitted unknown proposals and could overstate recovered-run success |
| W005 | WorkBench adapter after scoring repair | 19 passed, no paid requests | [Validation](evidence/workbench-adapter-validation-002.txt) | Unknown-tool penalty retained while recording actual effects |
| W006 | Re-score all 24 saved WorkBench Revisited model runs | All 16,560 prediction counts match; best saved run 674/690 correct, 13 unwanted-side-effect cases | [Manifest and ranking](evidence/workbench-published-frontier/summary.json), [raw execution log](evidence/workbench-published-frontier-run.txt) | Reproduced published comparison target; not Foundry performance or fresh inference |
| W007a | Compare pilot prompt with published frontier settings | One failing regression | [Before log](evidence/workbench-prompt-parity-before.txt) | Missing synthetic-task confirmation instruction identified |
| W007 | Protocol v2 prompt/schema parity and adapter suite | 20 passed, no paid requests | [Validation](evidence/workbench-adapter-validation-003.txt) | Every arm receives the published no-confirmation prompt; Foundry runtime gates remain |

An API key being present is not evidence that it is valid, funded, or has model
access. No live request was used to validate either key during offline setup.

## Follow-up: comparison and scoring integrity

The closer repository review is deliberately scoped to selected implementations;
it does not claim every line in each competitor was read. The free AgentGovBench
run used its official CLI, unmodified scenarios and scorer, and no model or vendor
service. A supplemental audit found missing-evidence passes. Source inspection
also found that the CLI can omit scenarios that raise runner exceptions. Our
reproduction checks exact coverage; future Foundry evaluation must retain every
planned scenario in its denominator.

The same review led to a WorkBench adapter regression: unknown tool proposals do
not reach the wrapped tool journal, whereas upstream includes them in its action
list and penalizes them. Before the fix, an unknown call followed by the correct
mutation could receive a passing score. All three arms now retain invalid-call
metadata and a scoring error while continuing to record actual later effects.
This changes only the benchmark adapter, not the framework's agent behavior.
The pre-fix failures and 19-test post-fix run are both retained. No live result was
generated under either version.

Protocol v2 now also includes the official instruction to execute the synthetic
task without asking for confirmation, matching the saved runs' metadata. A new
test invokes the actual upstream structured loop with its published setting and
compares first-request prompts and schemas across all three arms. The previous
pilot manifest and failing test are retained. Default product approvals were not
changed, and no live results need to be retrospectively relabeled.

W006 also makes the accuracy ceiling explicit: the best saved run misses only
16 tasks. The older GPT-4o reproduction is not the target for an accuracy-leadership
claim. Rescoring used no paid API calls and does not reproduce model latency or
cost. See [SOTA_BENCHMARK_STATUS.md](SOTA_BENCHMARK_STATUS.md) for claim-specific
acceptance criteria and the distinction between a pilot and a full comparison.

## Iteration 001: actual framework changes

**Problem 1 — approval could override a hard denial (review F02).** The decision
point returned an approval request before consulting external policy and egress
checks. Both runtimes inferred an approval request from the word `approval` in
the reason, so even a hard denial mentioning an approval-named tool could be
misclassified. The fix checks hard gates first, uses explicit
`GuardrailResult.requires_approval`, and keeps budget denial ahead of approval.
Regression coverage includes both runtimes, tool and policy approval sources,
external and egress denial, and policy revocation during a paused approval.

**Migration:** custom action guards requesting approval must set
`requires_approval=True`. A reason string alone now remains a denial.
`escalate=True` remains a separate escalation signal. This explicit change is
intentional: free text cannot safely determine override authority.

**Problem 2 — stale LLM decisions from incomplete cache keys (review F08).**
The cache keyed only model and role/content pairs. The fix includes complete
messages and request options, including tools, native call identifiers and
arguments, sampling parameters, and response formats. A digest bounds key size.
Custom cache implementations used with `LLMGateway` must accept keyword
`options=` in `get`/`set`; direct built-in cache calls without it still work.

These are engineering correctness repairs, not novel algorithms. I002 subsequently repairs process-local request propagation and delegated
authority. Multi-worker state conflicts, process isolation and broader adapter
guarantees remain open.
The WorkBench harness disables prompt caching and uses synthetic L4 tools, so
do not predict a WorkBench score improvement from these fixes.

## Current live protocol

The [harness README](../benchmarks/workbench/README.md) documents all deviations
from upstream. The exact proposed models, published prices, low-effort settings,
20-call/task limit, 2,048-output-token/request limit, 600-second cooperative task
deadline, and source/data hashes are captured in the planned manifest.

- Development: 2 fixed tasks per domain, 12 total; 3 arms × 2 providers = 72 attempts.
- Original project-held-out pool: 678 tasks. Sixty were later inspected for diagnosis;
  the next sixty were assigned to fresh confirmation. Do not describe all 678 as untouched.
- Full official-suite report: all 690 tasks, explicitly acknowledging the 12
  development tasks. This is separate from a held-out generalization claim.
- Proposed final repetitions: at least 3, subject to pilot variance and total
  budget. This is a proposal, not an executed experiment or power guarantee.
- Framework effects are compared within the same model. Comparing different
  model providers does not isolate the framework's contribution.
- Method changes receive new immutable experiment directories. The same spending
  ledger spans the campaign. Errors and interrupted attempts are never silently rerun.

The current paired bootstrap is exploratory. Before final evaluation, freeze the
primary endpoint, meaningful margin, comparator set, safety noninferiority margin,
uncertainty method, domain weighting, repetition count, and multiple-comparison
procedure. A 12-task pilot is too small to establish leadership.

## What every paper experiment must retain

| Record | Current support | Remaining requirement |
| --- | --- | --- |
| Question and falsifiable hypothesis | Research proposal and this protocol | Select one contribution after close prior-art review |
| Exact code, data, evaluator, dependencies | Git revisions, file hashes, upstream lock, environment freeze | Archive each selected implementation, not only its hashes |
| Prompts, tools, model settings | Full per-request payload and response, model/effort/token limits | Confirm availability and immutable provider version where supported |
| Task IDs and splits | Fixed seeded selection, explicit project split | Detect overlapping templates and pretraining contamination limits |
| Attempts, failures, interrupts | Per-attempt files; partial actions and unknown-cost reservations retained | Publish sanitized raw artifacts and exclusion rules |
| Token cost and compute | Provider usage, configured rates, API time and wall time | Reconcile provider invoices; record hardware and other infrastructure cost |
| Baselines | Upstream structured reference + two Foundry runtime arms | Standalone framework and nearest-paper baselines with matched tuning budgets |
| Statistics | Exploratory paired accuracy bootstrap | Confirmatory uncertainty for success, harm, cost, latency; sufficient repetitions |
| Ablations | Design proposed | Execute removal of verification, evidence validity, adaptation, memory, and topology as applicable |
| Negative results and search budget | This register, all experiment folders | Record every development configuration and cost, not just the chosen winner |
| Security and reliability | Original probes + I001 regressions | Attack/utility tradeoffs, tenant/delegation tests, crash/replay, stale approvals |
| Startup usefulness | Product milestone below | Independent users, onboarding completion, task utility and failure recovery |
| Responsible release | Keys ignored; traces omit headers | Data/tool licenses, privacy review, safe examples, anonymized artifact |
| Authorship and AI assistance | AI-assisted review, coding, tests, and drafting in this session | Human authors verify all citations, code, claims, and submission disclosures |

The official [NeurIPS checklist](https://neurips.cc/public/guides/PaperChecklist)
calls for scoped claims, limitations, reproducibility, experimental detail,
uncertainty, compute disclosure, and responsible research. This record prepares
evidence for those answers; it is not a completed checklist. The
[ICML author instructions](https://icml.cc/Conferences/2026/AuthorInstructions)
also describe reproducible artifacts and anonymized submissions. Check the
actual target year and track before preparing the final submission; no future
venue deadline or acceptance is promised.

## Startup milestone and candidate research contribution

First make a repeatable internal-operations agent: authenticated read/search,
approved updates, scoped tools, correct cancellation, durable state, observable
cost, and outcome-based regression evaluation. Add industry-specific permissions,
data schemas, deployment requirements, and evaluations as explicit application packs.
Supporting many orchestration patterns is not sufficient evidence of readiness
for arbitrary industries.

The research candidate in [RESEARCH_DESIGN_2026.md](RESEARCH_DESIGN_2026.md) is
selective revalidation of action evidence when execution state changes across
delegation, approval, and recovery. Compare against always-revalidate and simple
deterministic invalidation first. Prior art already covers substantial pieces;
the distinct gap remains unproven. Benchmark outperformance alone would not
establish novelty, and a useful correctness fix need not be a research contribution.

## Publication and push gate

The user authorized a push to the original GitHub repository after validation
and benchmark preparation/results. Preserve and refine the existing README.
Before pushing, publish only supported claims, retain negative results, ensure
secrets are absent, and link the exact experiment artifacts. Live benchmark
completion and any paper-quality or superiority claim remain pending.

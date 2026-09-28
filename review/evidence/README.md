# Evidence and reproduction

**Current evidence:** [live campaign audit](live-workbench-20260928/derived/audit_results.json) verifies 7 archives / 578 attempts; [full suite](full-suite-20260928-final.txt) records 620 passed / 25 skipped; [type check](typecheck-20260928-final.txt) covers 58 source files. [Current status](../CURRENT_STATUS.md) supersedes pending statuses in original logs. Original failed runs remain unchanged.

Reviewed commit: `2623ea1b2fe2d93beb1976c0d4e2449891b6900a`, 2026-09-27. Tests and probes used an isolated Python 3.11 environment; the system Python 3.9 was not used to evaluate a package declaring Python >=3.10.

| Artifact | Meaning |
| --- | --- |
| [file-inventory.json](file-inventory.json) | All 225 tracked snapshot files, hashes, sizes, kinds, line counts, Python symbols/test-function counts |
| [test-environment.txt](test-environment.txt) | Installed review dependencies (`pip freeze`), not a complete production lockfile |
| [review-environment.json](review-environment.json) | Interpreter/platform metadata and snapshot ID |
| [focused-tests.txt](focused-tests.txt) | 208 passed, selected foundational/runtime tests |
| [extended-tests.txt](extended-tests.txt) | 224 passed, 2 skipped, 5 optional-dependency failures |
| [test-selection.json](test-selection.json) | Exact files selected for each test run |
| [reproduce_findings.py](reproduce_findings.py) | Harmless local deterministic probes; no real model or business-tool calls |
| [reproduced-findings.json](reproduced-findings.json) | 16 observed behaviors at the snapshot, not desired outcomes |
| [runtime-microbenchmark.txt](runtime-microbenchmark.txt) | Unmodified repository microbenchmark; scripted provider and synthetic pricing |
| [benchmark-status.json](benchmark-status.json) | Explicit measured/unmeasured status; no public score claimed |
| [workbench-official-tests.txt](workbench-official-tests.txt) | 257 upstream offline tests passed |
| [workbench-baseline-reproduction.json](workbench-baseline-reproduction.json) | Upstream saved GPT-4o predictions reproduce exactly: 476/690 correct, 104 side-effect cases; not Foundry performance |
| [workbench-adapter-tests.txt](workbench-adapter-tests.txt) | 16 offline integration/contract tests, including all-arm campaign and resume with mocked HTTP |
| [workbench-adapter-integration-first.txt](workbench-adapter-integration-first.txt) | Retained intermediate environment-capture failure |
| [iteration-001-before.txt](iteration-001-before.txt) | 13 regression failures on the original implementation |
| [iteration-001-after.txt](iteration-001-after.txt) | Retained intermediate run: 8 test-assertion failures, 170 passes, 2 skips |
| [iteration-001-validation.txt](iteration-001-validation.txt) | Corrected regression/related suite: 178 passes, 2 skips |
| [workbench-environment.txt](workbench-environment.txt) | Installed versions in the isolated Python 3.12 WorkBench/adapter environment |
| [experiment-registry.json](experiment-registry.json) | Machine-readable run register and planned live experiment status |
| [workbench-preparation/planned-manifest.json](workbench-preparation/planned-manifest.json) | Current proposed pilot, source/data hashes, model prices/settings, and task IDs |
| [closest-source-manifest.json](closest-source-manifest.json) | Seven pinned competitor checkouts; hashes and selected reading ranges for 27 source/scenario files |
| [reproduce_agentgovbench.py](reproduce_agentgovbench.py) | Reproduce the official vanilla baseline and separately probe scorer evidence sufficiency; no model calls |
| [agentgovbench-baseline/vanilla-cli.txt](agentgovbench-baseline/vanilla-cli.txt) | Retained setup failure: missing Click dependency |
| [agentgovbench-baseline-complete/manifest.json](agentgovbench-baseline-complete/manifest.json) | Completed upstream vanilla baseline: 13/48, complete ID coverage, package versions, code/data/output hashes; not Foundry performance |
| [agentgovbench-baseline-complete/scorer-audit.json](agentgovbench-baseline-complete/scorer-audit.json) | Empty observations can satisfy five scenarios; three isolated evidence-sufficiency counterexamples |
| [workbench-unknown-tool-before.txt](workbench-unknown-tool-before.txt) | Three reproduced pre-fix scoring failures, one per arm |
| [workbench-adapter-validation-002.txt](workbench-adapter-validation-002.txt) | All 19 adapter tests passed after preserving unknown-tool penalties |
| [workbench-prompt-parity-before.txt](workbench-prompt-parity-before.txt) | One pre-fix regression: the proposed pilot omitted the published run's synthetic-task confirmation instruction |
| [workbench-adapter-validation-003.txt](workbench-adapter-validation-003.txt) | All 20 adapter tests passed after aligning prompt/schema with the published run setting |
| [workbench-preparation/iteration-002-planned-manifest.json](workbench-preparation/iteration-002-planned-manifest.json) | Preserved previous, unexecuted pilot configuration before prompt alignment; current protocol is v2 |
| [workbench-published-frontier/summary.json](workbench-published-frontier/summary.json) | All 24 saved WorkBench model runs reproduced: 16,560 predictions; hashes, environment, ranking and explicit non-Foundry scope |
| [workbench-published-frontier/scores.csv](workbench-published-frontier/scores.csv) | All 24 model totals; per-domain expected/observed counts and prediction hashes are in the linked model-NN artifacts |
| [workbench-published-frontier-run.txt](workbench-published-frontier-run.txt) | Complete execution output, including scorer warnings for malformed upstream predictions |
| [workbench-frontier-verification.json](workbench-frontier-verification.json) | Published target equals the pinned Git blob; generated artifact and executed-script hashes verified |

## Environment

The review installed `pytest>=8`, `langgraph>=0.6`, `fastapi>=0.110`, `httpx>=0.27`, `jsonschema>=4.20`, and `cryptography>=42.0` in `/private/tmp/agent-foundry-review-env`. Exact resolved versions are in the freeze file. It included LangGraph and LangChain Core, not the entire optional integration set.

For a separate machine, create a Python 3.11 virtual environment and install the recorded package versions in that isolated environment. Dependency availability can change. Run from the repository root, on the reviewed commit, without real service credentials. The commands below use the original environment path for reproducibility; substitute your own interpreter path if needed.

## Selected pytest runs

The precise selections are in `test-selection.json`. Both used:

```text
/private/tmp/agent-foundry-review-env/bin/python -m pytest -q <selected files> -p no:cacheprovider
```

The foundational selection comprised 14 files. The extended selection comprised 30 files. They did not overlap. Together: **432 passed, 2 skipped, 5 failed**. A test function can generate multiple cases, so these totals are not directly comparable to the AST inventory's count of 568 test-named functions.

Five failures were caused by absent extras:

- `tests/test_context.py::test_chroma_vector_store_ids_never_collide_across_a_simulated_restart`: `chromadb` unavailable.
- Four `tests/test_agent_spec.py` build-agent cases: `anthropic` unavailable when constructing the default provider.

These failures are retained. They do not establish implementation defects in those paths, and they do not establish that those paths would pass with the dependencies installed. The two skips are reported as recorded; skip reasons were not captured with `-ra` in this run. Full suite coverage, service-backed integration tests, and all CI checks were not reproduced.

## Deterministic probes

```sh
/private/tmp/agent-foundry-review-env/bin/python review/evidence/reproduce_findings.py > /private/tmp/foundry-probes-rerun.json 2> /private/tmp/foundry-probes-rerun-traces.jsonl
```

The script emits JSON observations on stdout and runtime traces on stderr. It records behavior rather than asserting desired behavior, so exit status zero does **not** mean the framework passed security tests. After fixes, compare the recorded values to the acceptance conditions in the review, then convert them into proper product regression tests.

| Probe | Observed behavior | Related finding |
| --- | --- | --- |
| Expired deadline × 2 runtimes | Model calls and tool execution still occur | F01 |
| Cancelled token × 2 | Model calls and tool execution still occur | F01 |
| Deny-all request tool policy × 2 | Tool execution still occurs | F01 |
| Two native instances, one shared state store | The second turn is overwritten by stale state | F05 |
| Worker timeout | 10 ms requested timeout returns after approximately 200 ms of work | F06 |
| Sandbox dunder expression | Harmless class introspection returns `tuple` | F06 |
| Converted LangChain tool | Metadata-required policy/approval not enforced by wrapper | F04 |
| Knowledge context | Synthetic email/injection marker survives one context path | F07 |
| Prompt cache with changed tools | Provider called only once for two distinct tool schemas | F08 |
| Approval plus external deny × 2 | Tool executes after approval; external policy never called | F02 |
| Supervisor viewer caller × 2 | Native executes admin tool; LangGraph denies it | F03 |

The counts in this table group scenarios; the JSON contains 16 individual observations. The knowledge probe does not invoke a model to demonstrate a successful injection attack. The sandbox probe does not attempt a host escape. All effects are local/synthetic.

## Microbenchmark

```sh
/private/tmp/agent-foundry-review-env/bin/python benchmarks/native_vs_langgraph.py > /private/tmp/foundry-runtime-rerun.txt 2>&1
```

The stored result is one run, with the repository script's 15 cases in most scenarios, five timed fan-out calls of 15 items, and fixed ordering. It is appropriate as preliminary runtime evidence only. No live provider bill, public benchmark score, multi-host scaling result, or production SLO is implied.

## Unperformed work

No real Redis/Postgres deployment, live LLM inference, real CrewAI integration run, distributed load/soak, public benchmark inference, independent penetration test, or leaderboard submission was performed. The review did not read secret values, send messages, create GitHub issues, push commits, or modify application source. Optional integration and deployment properties remain subject to the planned checks.

## Follow-up implementation and benchmark preparation

The paragraph above describes the original review. The follow-up stored
user-provided credentials only in the ignored local `.env`, added the WorkBench
harness, and repaired approval precedence and prompt-cache request identity.
No paid provider call or GitHub push has occurred in this follow-up. See the
[paper experiment log](../PAPER_EXPERIMENT_LOG.md) for exact scope, compatibility
changes, retained failures, and remaining work.

WorkBench uses a separate Python 3.12.13 environment with its frozen upstream
dependencies plus Foundry's `langgraph` and `schema` extras. Its exact packages
are recorded separately from the original Python 3.11 review environment.
Source hashes supplement the package version list for editable installations.
This is not the full optional-dependency/OS matrix from GitHub CI.

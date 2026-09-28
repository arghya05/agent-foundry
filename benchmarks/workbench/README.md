# WorkBench comparison harness

**Executed campaign:** [measured results](../../review/LIVE_BENCHMARK_RESULTS.md) and [current status](../../review/CURRENT_STATUS.md). Live calls are stopped after the bounded continuation allowance; fresh guidance was not run. The commands below are reproduction examples, not a claim that every planned experiment completed.

This harness compares the pinned WorkBench reference loop with Foundry's native
and LangGraph runtimes. It supports OpenAI and Anthropic through the same
metered transport. It does not establish SOTA or enterprise readiness.

## Verified so far

- WorkBench revision: `49c7dfd00c03d384ec59ea57374f50b766aa5613`; ground truth `v2`.
- Official offline suite: **257 passed**.
- Adapter contracts: **24 passed**, including identical scripted request histories,
  tool ordering, thread-local sandbox isolation, budget handling, campaign resume,
  unknown-tool penalties after recovery in all three arms, and exact prompt/schema
  parity with the published frontier run's confirmation setting.
- Saved upstream GPT-4o predictions reproduced exactly: **476/690 correct**,
  **104 unwanted-side-effect cases**. These are upstream results, not Foundry results.
- All **24 published model runs** now reproduce exactly: **16,560 saved task
  predictions**. Best in that pinned comparison: **674/690 correct**, **13 unwanted
  side-effect cases**. See [the frontier report](../../review/SOTA_BENCHMARK_STATUS.md).
- Live Foundry task accuracy, latency, and token cost: **not measured yet**.

Evidence is under [review/evidence](../../review/evidence/README.md).

## Setup

Use Python 3.12 and `uv`. The recorded local environment is
`/private/tmp/agent-foundry-workbench/.venv`; substitute paths on another machine.

```sh
git clone https://github.com/olly-styles/WorkBench.git /private/tmp/agent-foundry-workbench
git -C /private/tmp/agent-foundry-workbench checkout 49c7dfd00c03d384ec59ea57374f50b766aa5613
cd /private/tmp/agent-foundry-workbench
uv sync --frozen --python 3.12
# Replace the path with your Foundry checkout:
uv pip install --python .venv/bin/python -e '/path/to/agent-foundry[langgraph,schema]'
```

The adapter installs do not edit WorkBench's lockfile. Resolved versions are
recorded separately; a live experiment refuses to resume with a changed environment.
The upstream checkout supplies public data: a Hugging Face token is unnecessary
for this benchmark.

Store `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` in this directory's `.env`.
The file is ignored by Git. Never place keys in command arguments, logs, README,
or a paper artifact. Use `chmod 600 benchmarks/workbench/.env` locally.

## Commands from the Foundry repository root

Offline preparation makes no provider requests:

```sh
/private/tmp/agent-foundry-workbench/.venv/bin/python benchmarks/workbench/run.py prepare
/private/tmp/agent-foundry-workbench/.venv/bin/python benchmarks/workbench/run.py reproduce
/private/tmp/agent-foundry-workbench/.venv/bin/python benchmarks/workbench/reproduce_frontier.py --output /tmp/workbench-frontier-reproduction
/private/tmp/agent-foundry-workbench/.venv/bin/python -m pytest benchmarks/workbench/test_workbench.py -q
```

A live development pilot needs an explicit total spending limit. The following
command is an **example**, not authorization to spend $25:

```sh
/private/tmp/agent-foundry-workbench/.venv/bin/python benchmarks/workbench/run.py live \
  --split development --per-domain 2 --max-spend-usd 25 --output benchmarks/workbench/results/pilot
```

Default pilot: 12 fixed tasks, 2 providers, 3 arms = **72 task attempts**, up to
20 model calls per attempt. Each request has a 2,048-token output ceiling;
each task has a cooperative 600-second deadline. No automatic network retries.

After development, freeze the method before a project-held-out comparison:

```sh
/private/tmp/agent-foundry-workbench/.venv/bin/python benchmarks/workbench/run.py live \
  --split heldout --per-domain 2 --repetitions 3 --max-spend-usd 25 \
  --output benchmarks/workbench/results/heldout
```

The example cap is intentionally the same shared ledger cap: it will stop if
insufficient. A larger experiment requires a separately authorized campaign budget,
not deleting the ledger to regain spending capacity. `--split full` selects all
690 official tasks; report that it includes the 12 development tasks. The 678-task
held-out slice is our project split, **not an official hidden benchmark split**.

## Models and accounting

The current pilot candidates are pinned/configured in [models.json](models.json):

| Provider | Model | Input / output USD per million tokens | Reasoning setting |
| --- | --- | --- | --- |
| OpenAI | `gpt-5.4-mini-2026-03-17` | $0.75 / $4.50 | low |
| Anthropic | `claude-sonnet-5` | $2 / $10 | adaptive, low effort |

Prices checked 2026-09-27 against the [OpenAI model documentation](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
and [Anthropic model documentation](https://platform.claude.com/docs/en/models/sonnet-5/overview).
These are candidates for a cost/quality pilot, not a finding that they are the
best models. OpenAI Responses was exercised successfully; Anthropic rejected setup and requires
a workspace header for the supplied account. No successful Anthropic result exists.
OpenAI is snapshot-pinned; the returned Anthropic model identifier is also logged.

The ledger reserves a full model context plus the output ceiling before each call,
then settles at the reported token usage and configured prices. Cached input is
conservatively charged at base input price; costs are upper estimates, not invoices.
Timeouts or unknown usage keep the full reservation. This can stop a campaign
before the nominal cap is exhausted. No paid hosted tools, priority service,
regional endpoints, or prompt cache writes are requested. Provider billing remains
authoritative; verify prices before a new campaign.

## Experimental choices that must be disclosed

1. The reference uses upstream `run_agent_structured`; only its provider transport
   and model routing are replaced to obtain matched settings and accounting.
2. All arms use the same official date prompt, all tools, and unchanged official
   scorer. No ground-truth actions are passed to the agent or provider.
   Protocol `workbench-foundry-v2` adds the official synthetic-task instruction
   to execute without requesting confirmation, matching the published runs'
   `act_without_confirmation=True` setting. The previous unexecuted pilot plan
   omitted this instruction. Both plans and the failing/passing parity tests are
   retained; no live result was produced with the old prompt. This instruction
   does not disable the Foundry action-policy and approval gates.
3. The Foundry arms are named `foundry_native_serial` and
   `foundry_langgraph_serial`: a benchmark-only dispatcher forces model-order tool
   execution, matching upstream semantics for shared mutable state. This does not
   measure Foundry's default parallel tool speed.
4. Foundry retains its default regex input/output guards and tool validation.
   These can reduce task utility, including blocking synthetic email text.
   We do not silently disable them to improve the score. The benchmark policy
   permits the synthetic tools at L4; this is not a production security configuration.
5. Terminal failures retain partial executed actions so unwanted effects are
   visible. Upstream's inference helper can lose those actions on an exception;
   our failure logging is a disclosed harness difference. The official scorer is unchanged.
   Unknown tool proposals cannot reach the execution journal. They are recorded
   separately as `invalid_tool_calls` and set `error=UnknownToolCall` if there is
   no other error, preserving the official scorer's failure penalty even if a
   later valid action reaches the requested end state. The agent may still recover
   and continue; actual effects remain logged. This applies to all three arms.
6. WorkBench assesses state-changing business tasks. Its unwanted-side-effect
   metric is not an authorization, injection, compliance, or complete safety metric.
7. These Foundry runtime arms are not standalone LangGraph, CrewAI, or LangChain
   competitive baselines. Those comparisons remain to be implemented and pinned.

## Artifacts and resuming

Each experiment contains source/data hashes, a manifest, environment freeze,
execution command/timestamps, per-attempt requests/responses/actions/usage/errors,
and `summary.json`. Headers and credentials are excluded from traces.
Failed attempts remain in the results; resuming never selectively reruns them.
A started attempt interrupted by a crash is marked interrupted and counted as
an error; unknown request charges stay reserved. Keep all raw experiment directories.

Summaries report accuracy among attempted tasks, planned coverage, side effects,
errors, p50/p95 latency, model/API time, and upper estimated cost per success.
An exploratory paired bootstrap clusters repetitions by task. Incomplete results
and a small pilot cannot establish significance or SOTA. Formal hypotheses,
ablation design, confidence intervals for safety/cost, and multiplicity handling
must be frozen before confirmatory evaluation.

Raw live artifacts are ignored by Git pending a credential/license/anonymity
check. Publish the sanitized evidence and a complete run registry, including
failed and negative results, before using any result in a paper.

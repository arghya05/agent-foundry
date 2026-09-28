# Agent Foundry research paper

*Agent Foundry: A Boundary-Preserving Architecture for Governed Agentic Platforms*
— public preprint in the NeurIPS 2026 style (`preprint` option; not a submission).

- [PDF](AgentFoundry_Paper.pdf) · [LaTeX source ZIP](AgentFoundry_LaTeX_Source.zip) · [build manifest](build_manifest.json)
- Main file [`agent_foundry_paper.tex`](agent_foundry_paper.tex), [`sections/`](sections/), [`figures/`](figures/), [`references.bib`](references.bib) (every entry carries a `% verified:` source line)
- Generated evidence: [`evidence/numbers.tex`](evidence/numbers.tex), table rows and figure data, [`evidence/summary.json`](evidence/summary.json)

## How the numbers are produced

No number in the manuscript is typed by hand. `build_evidence.py` reads raw files
only from the repository:

| Source | Contents |
| --- | --- |
| `review/evidence/paper-campaign-20260928c/` | AgentGovBench (10 repetitions, baselines, 13 ablations, 2 probes, supplemental suite), overhead, runtime, scaling, sharding, long-context, injection and hallucination datasets, test log; `manifest.json` has SHA-256 digests |
| `review/evidence/paper-campaign-20260928{,b}/` | Earlier campaigns, retained (the first recorded the clock anomaly discussed in §3) |
| `review/evidence/agentgovbench-published-results-20260928.json` | Published AgentGovBench runs recounted per scenario, with file hashes |
| `review/evidence/live-workbench-20260928/`, `workbench-published-frontier/` | Earlier WorkBench study and the 24 rescored published runs |

## Rebuild

```sh
# 1. (optional) re-run every model-free experiment from a clean commit
git clone https://github.com/agentic-control-plane/agentgovbench /tmp/agb
git -C /tmp/agb checkout e0ce93ae175376d7847c69a64d0c36bdfa6ca717
python benchmarks/run_paper_campaign.py --agentgovbench /tmp/agb --out review/evidence/paper-campaign-<date>
# 2. regenerate numbers and compile (Tectonic); fails on undefined refs/citations or overfull boxes
python research-paper/build_paper.py --compiler /path/to/tectonic
```


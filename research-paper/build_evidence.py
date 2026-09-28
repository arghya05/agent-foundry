"""Regenerate every number and table row in the manuscript from raw evidence.

Reads only committed evidence (standard library):
  review/evidence/paper-campaign-20260928/         (this study's campaign)
  review/evidence/agentgovbench-published-results-20260928.json
  review/evidence/live-workbench-20260928/derived/  (earlier WorkBench study)
  review/evidence/workbench-published-frontier/scores.csv
Writes research-paper/evidence/{numbers.tex,*_rows.tex,summary.json}. No
number in the manuscript body is typed by hand; the build fails if a macro is
missing. Statistics: exact two-sided McNemar (binomial), Wilson 95% intervals
for proportions, Hanley-McNeil standard errors for AUROC.
"""
from __future__ import annotations

import csv
import glob
import json
import math
import re
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
EV = REPO / "review" / "evidence"
CAMPAIGN = EV / "paper-campaign-20260928c"
FIRST_CAMPAIGN = EV / "paper-campaign-20260928"
OUT = HERE / "evidence"
OUT.mkdir(exist_ok=True)
macros: dict[str, str] = {}
summary: dict = {}

ABLATION_LABELS = {
    "no_attenuation": "Delegation attenuation", "no_chain": "Delegation chain in audit",
    "no_rate_limit": "Principal rate limiter", "fail_mode_learned_only": "Locally declared fail mode",
    "fail_open_always": "Honor fail-closed", "no_tenant_membership": "Tenant membership",
    "no_deny_overrides": "Deny-overrides resolution", "stale_policy": "Per-call policy read",
    "no_audit": "Decision audit", "no_scope_check_plane": "Plane scope check (PDP kept)",
    "no_scope_check_both": "All scope checks", "no_tier_rules": "Tier rules",
    "anonymous_allowed": "Reject anonymous principal",
}


def m(name: str, value) -> None:
    if not re.fullmatch(r"[A-Za-z]+", name):
        raise SystemExit(f"bad macro name {name}")
    macros[name] = str(value)


def pct(x: float, digits: int = 1) -> str:
    return f"{100 * x:.{digits}f}\\%"


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def mcnemar(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n)


def hanley_mcneil(a: float, n_pos: int, n_neg: int) -> tuple[float, float]:
    q1, q2 = a / (2 - a), 2 * a * a / (1 + a)
    se = math.sqrt((a * (1 - a) + (n_pos - 1) * (q1 - a * a) + (n_neg - 1) * (q2 - a * a)) / (n_pos * n_neg))
    return (max(0.0, a - 1.96 * se), min(1.0, a + 1.96 * se))


def tex_escape(s: str) -> str:
    return s.replace("_", "\\_").replace("&", "\\&").replace("%", "\\%")


# ---------------------------------------------------------------- AgentGovBench
agb = CAMPAIGN / "agentgovbench"


def load(name: str) -> dict:
    return json.loads((agb / f"{name}.json").read_text())


def score(blob: dict) -> tuple[int, int]:
    a = blob["aggregate"]
    return a["total_passed"], a["total_scenarios"]


foundry = load("agent_foundry")
m("AGBFoundry", score(foundry)[0])
m("AGBTotal", score(foundry)[1])
reps = [load(Path(p).stem) for p in sorted(glob.glob(str(agb / "rep*-agent_foundry.json")))]
m("AGBRepeats", len(reps))
m("AGBRepeatsPerfect", sum(score(r)[0] == score(r)[1] for r in reps))
per_rep_ids = [tuple(sorted((x["scenario_id"], x["passed"]) for x in r["results"])) for r in reps]
m("AGBRepeatsIdentical", "identical" if len(set(per_rep_ids)) == 1 else "not identical")
for runner, macro in (("vanilla", "AGBVanilla"), ("audit_only", "AGBAuditOnly")):
    m(macro, score(load(runner))[0])
for probe, macro in (("deny_all", "AGBDenyAll"), ("allow_all", "AGBAllowAll")):
    m(macro, score(load(f"agent_foundry-ablate-{probe}"))[0])
supp = {r: load(f"supp-{r}") for r in ("agent_foundry", "vanilla", "audit_only")}
m("SuppFoundry", score(supp["agent_foundry"])[0])
m("SuppTotal", score(supp["agent_foundry"])[1])
m("SuppVanilla", score(supp["vanilla"])[0])
m("SuppAuditOnly", score(supp["audit_only"])[0])
m("SuppDenyAll", score(load("supp-agent_foundry-ablate-deny_all"))[0])
m("SuppAllowAll", score(load("supp-agent_foundry-ablate-allow_all"))[0])
av = foundry["anti_vacuity_summary"]
m("AVCoverage", av["decision_coverage"])
m("AVFidelity", av["effect_fidelity"])
m("AVLiveness", av["liveness"])
m("AVLivenessApplicable", av["liveness_applicable"])
m("AVAuditOnlyCoverage", load("audit_only")["anti_vacuity_summary"]["decision_coverage"])
m("AVVanillaCoverage", load("vanilla")["anti_vacuity_summary"]["decision_coverage"])
m("AVDenyAllLiveness", load("agent_foundry-ablate-deny_all")["anti_vacuity_summary"]["liveness"])

# Vacuity: official scenarios passed by at least one trivial system.
trivial = {"vanilla": load("vanilla"), "audit_only": load("audit_only"),
           "deny_all": load("agent_foundry-ablate-deny_all"), "allow_all": load("agent_foundry-ablate-allow_all")}
ids = [x["scenario_id"] for x in foundry["results"]]
passed_by = {sid: [t for t, b in trivial.items() if any(x["scenario_id"] == sid and x["passed"] for x in b["results"])]
             for sid in ids}
m("VacuousAny", sum(1 for v in passed_by.values() if v))
m("VacuousNone", sum(1 for v in passed_by.values() if not v))

# Attribution: official scenarios flipped by at least one single-control ablation.
flips: dict[str, list[str]] = {sid: [] for sid in ids}
rows = []
for key, label in ABLATION_LABELS.items():
    off = load(f"agent_foundry-ablate-{key}")
    sup = load(f"supp-agent_foundry-ablate-{key}")
    failed_off = [x["scenario_id"] for x in off["results"] if not x["passed"]]
    failed_sup = [x["scenario_id"] for x in sup["results"] if not x["passed"]]
    for sid in failed_off:
        flips[sid].append(key)
    rows.append((label, score(off)[0], len(failed_off), score(sup)[0], len(failed_sup)))
attributed = sum(1 for v in flips.values() if v)
m("AttributedOfficial", attributed)
m("UnattributedOfficial", len(ids) - attributed)
m("AblationCount", len(ABLATION_LABELS))
blind = [label for label, _, fo, _, fs in rows if fo == 0 and fs > 0]
m("OfficialBlindControls", len(blind))
m("OfficialBlindList", ", ".join(blind).lower())
with (OUT / "ablation_rows.tex").open("w") as fh:
    for label, so, fo, ss, fs in rows:
        flag = r"$^\dagger$" if fo == 0 and fs > 0 else ""
        fh.write(f"{label}{flag} & {so}/{macros['AGBTotal']} & {fo} & {ss}/{macros['SuppTotal']} & {fs} \\\\\n")
summary["ablations"] = [dict(zip(("control", "official", "official_failed", "supp", "supp_failed"), r)) for r in rows]

# Published leaderboard (recounted) + this study.
pub = json.loads((EV / "agentgovbench-published-results-20260928.json").read_text())["runs"]
NAMES = {"acp_api": "ACP gateway (API mode)", "anthropic_agent_sdk_acp": "Claude Agent SDK + ACP",
         "openai_agents_acp": "OpenAI Agents SDK + ACP", "claude_code_acp": "Claude Code + ACP",
         "codex_acp": "Codex CLI + ACP", "crewai_acp": "CrewAI + ACP", "langgraph_acp": "LangGraph + ACP",
         "cursor_acp": "Cursor + ACP"}
native = [r for r in pub if r["runner"].endswith("_native")]
governed = sorted([r for r in pub if not r["runner"].endswith("_native")], key=lambda r: -r["passed"])
best = governed[0]["passed"]
m("AGBBestPublished", best)
m("AGBNativeFrameworks", len(native))
m("AGBNativeScore", sorted({r["passed"] for r in native})[0] if len({r["passed"] for r in native}) == 1 else "varies")
pub_results = {r["runner"]: r["scenario_passed"] for r in governed}
declared = [r for r in governed if "scope_inheritance.04_task_narrowing" in r["declined"]]
narrow_pass = [r for r in declared if pub_results[r["runner"]].get("scope_inheritance.04_task_narrowing")]
m("ACPDeclaredNarrowing", len(declared))
m("ACPDeclaredNarrowingPassed", len(narrow_pass))
CATS = ["audit_completeness", "cross_tenant_isolation", "delegation_provenance", "fail_mode_discipline",
        "identity_propagation", "per_user_policy_enforcement", "rate_limit_cascade", "scope_inheritance"]


def cats_of_blob(blob):
    return {c["category"]: c["passed"] for c in blob["aggregate"]["by_category"]}


with (OUT / "leaderboard_rows.tex").open("w") as fh:
    fc = cats_of_blob(foundry)
    fh.write(r"\textbf{Agent Foundry (this work)} & " + " & ".join(rf"\textbf{{{fc[c]}}}" for c in CATS)
             + rf" & \textbf{{{macros['AGBFoundry']}}} \\" + "\n\\midrule\n")
    for r in governed:
        fh.write(f"{NAMES.get(r['runner'], tex_escape(r['runner']))} & "
                 + " & ".join(str(r["by_category"].get(c, 0)) for c in CATS) + f" & {r['passed']} \\\\\n")
    fh.write("\\midrule\n")
    fh.write(f"Seven frameworks, no governance layer$^{{a}}$ & "
             + " & ".join(str(native[0]["by_category"].get(c, 0)) for c in CATS) + f" & {native[0]['passed']} \\\\\n")
    for label, blob in (("Audit-only reference runner", trivial["audit_only"]),
                        ("Deny-everything probe (ours)", trivial["deny_all"]),
                        ("Allow-and-audit probe (ours)", trivial["allow_all"])):
        c = cats_of_blob(blob)
        fh.write(f"{label} & " + " & ".join(str(c.get(k, 0)) for k in CATS) + f" & {score(blob)[0]} \\\\\n")
    assert all(r["by_category"] == native[0]["by_category"] for r in native), "native runs differ"
summary["leaderboard"] = {"foundry": int(macros["AGBFoundry"]), "best_published": best,
                          "governed": [(r["runner"], r["passed"]) for r in governed]}

# ---------------------------------------------------------------- overhead
ov = json.loads((CAMPAIGN / "governance_overhead.json").read_text())["layers"]
for key, macro in (("tool_registry", "OvRegistry"), ("governed_gateway", "OvGateway"),
                   ("control_plane", "OvPlane"), ("control_plane_3hop_delegation", "OvDelegation"),
                   ("bare_function", "OvBare")):
    m(macro, f"{ov[key]['median_us']:.0f}" if ov[key]["median_us"] >= 1 else f"{ov[key]['median_us']:.2f}")
    m(macro + "P", f"{ov[key]['p95_us']:.0f}" if ov[key]["p95_us"] >= 1 else f"{ov[key]['p95_us']:.2f}")
m("OvPlaneDelta", f"{ov['control_plane']['median_us'] - ov['governed_gateway']['median_us']:.0f}")
m("OvDelegationDelta", f"{ov['control_plane_3hop_delegation']['median_us'] - ov['control_plane']['median_us']:.0f}")

# ---------------------------------------------------------------- runtime
topo_rows: dict[tuple[str, str], list[tuple[float, float, float]]] = {}
for path in sorted((CAMPAIGN / "runtime").glob("run*.txt")):
    for line in path.read_text().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 5 and cells[1] in ("native", "langgraph"):
            try:
                topo_rows.setdefault((cells[0], cells[1]), []).append((float(cells[2]), float(cells[3]), float(cells[4])))
            except ValueError:
                pass
m("RuntimeRuns", len(list((CAMPAIGN / "runtime").glob("run*.txt"))))
speed = []
with (OUT / "runtime_rows.tex").open("w") as fh:
    for topo in dict.fromkeys(t for t, _ in topo_rows):
        n = [statistics.median(v[i] for v in topo_rows[(topo, "native")]) for i in range(3)]
        g = [statistics.median(v[i] for v in topo_rows[(topo, "langgraph")]) for i in range(3)]
        ratio = g[0] / n[0] if n[0] else float("nan")
        speed.append((topo, ratio))
        fh.write(f"{tex_escape(topo)} & {n[0]:.2f} & {n[1]:.2f} & {g[0]:.2f} & {g[1]:.2f} & {ratio:.1f}$\\times$ \\\\\n")
coord = [r for t, r in speed if t in ("supervisor", "swarm", "blackboard", "debate", "fanout", "dag")]
m("CoordSpeedMin", f"{min(coord):.1f}")
m("CoordSpeedMax", f"{max(coord):.0f}")
tool = dict(topo_rows)[("agent (tool-calling)", "native")]
m("NativeToolPNinetyFive", f"{statistics.median(v[1] for v in tool):.2f}")
m("LGToolPNinetyFive", f"{statistics.median(v[1] for v in topo_rows[('agent (tool-calling)', 'langgraph')]):.2f}")

# ---------------------------------------------------------------- scaling
sc = json.loads((CAMPAIGN / "control_plane_scaling.json").read_text())
with (OUT / "scaling_rows.tex").open("w") as fh:
    for r in sc["results"]:
        fh.write(f"{r['threads']} & {r['throughput_per_s']:,.0f} & {r['p50_ms']:.2f} & {r['p95_ms']:.2f} & "
                 f"{r['p99_ms']:.2f} & {'yes' if r['limit_exact_all_reps'] else 'no'} & "
                 f"{'yes' if r['audit_exact_all_reps'] else 'no'} \\\\\n")
m("ScalePrincipals", sc["principals"])
m("ScaleCalls", sc["results"][0]["calls"])
m("ScaleAllExact", "all" if all(r["limit_exact_all_reps"] and r["audit_exact_all_reps"] for r in sc["results"]) else "not all")
m("ScaleThroughputOne", f"{sc['results'][0]['throughput_per_s']:,.0f}")
sh = json.loads((CAMPAIGN / "sharded_scaling.json").read_text())
base = sh["results"][0]["aggregate_throughput_per_s"]
with (OUT / "shard_rows.tex").open("w") as fh:
    for r in sh["results"]:
        s = r["aggregate_throughput_per_s"] / base
        fh.write(f"{r['processes']} & {r['aggregate_throughput_per_s']:,.0f} & {s:.2f}$\\times$ & "
                 f"{100 * s / r['processes']:.0f}\\% & {'yes' if r['limit_exact'] else 'no'} \\\\\n")
last = sh["results"][-1]
m("ShardMax", last["processes"])
m("ShardMaxThroughput", f"{last['aggregate_throughput_per_s']:,.0f}")
m("ShardSpeedup", f"{last['aggregate_throughput_per_s'] / base:.1f}")
m("ShardEfficiency", f"{100 * last['aggregate_throughput_per_s'] / base / last['processes']:.0f}")
m("ShardPerProc", f"{base:,.0f}")
m("ShardsForMillionPerMin", math.ceil((1e6 / 60) / (last["aggregate_throughput_per_s"] / last["processes"])))
summary["shards"] = sh["results"]

first = json.loads((FIRST_CAMPAIGN / "control_plane_scaling.json").read_text())["results"]
m("FirstScaleInexact", sum(1 for r in first if not r["limit_exact_all_reps"]))
m("FirstScaleConfigs", len(first))
m("FirstScaleReps", 3)
m("ScaleReps", sc["results"][0]["reps"])

# ---------------------------------------------------------------- long context
lc = json.loads((CAMPAIGN / "long_context.json").read_text())
s = lc["summary"]
m("LCBudget", f"{lc['budget_tokens']:,}")
m("LCFullOverBudget", f"{max(v['context_tokens'] for v in lc['summary']['full/lexical'].values()) / lc['budget_tokens']:,.0f}")
sizes = [str(n) for n in lc["sizes"]]
m("LCMaxHistory", f"{s['foundry/lexical'][sizes[-1]]['history_tokens']:,}")
m("LCRecencyRetention", pct(s["recency/lexical"][sizes[-1]]["retention"], 0))
m("LCFoundryRetention", pct(min(v["retention"] for v in s["foundry/lexical"].values()), 0))
m("LCFoundryParaphrase", pct(max(v["retention"] for v in s["foundry/paraphrase"].values()), 0))
m("LCEmbedParaphrase", pct(min(v["retention"] for v in s["foundry_embedding/paraphrase"].values()), 0))
m("LCFoundryMaxMs", f"{s['foundry/lexical'][sizes[-1]]['median_ms']:.0f}")
m("LCFoundryContextTokens", f"{s['foundry/lexical'][sizes[-1]]['context_tokens']}")
m("LCInjectionsFoundry", sum(i["foundry_injections"] for i in lc["injection"]))
m("LCInjectionsRecency", sum(i["recency_injections"] for i in lc["injection"]))
m("LCInjectionCases", len(lc["injection"]))
m("LCCells", len(lc["depths"]) * len(lc["seeds"]))
with (OUT / "longctx_rows.tex").open("w") as fh:
    labels = {"recency/lexical": "Recency window", "full/lexical": "Full history",
              "foundry/lexical": "Foundry, lexical retriever", "foundry/paraphrase": "Foundry, lexical, paraphrased query",
              "foundry_embedding/lexical": "Foundry, embedding retriever",
              "foundry_embedding/paraphrase": "Foundry, embedding, paraphrased query"}
    for key, label in labels.items():
        cells = []
        for n in sizes:
            v = s[key].get(n)
            cells.append("\\na" if v is None else f"{100 * v['retention']:.0f}/{'Y' if v['within_budget'] == 1 else 'N'}")
        fh.write(f"{label} & " + " & ".join(cells) + " \\\\\n")
m("LCSizes", ", ".join(f"{int(n):,}" for n in sizes))
m("LCHistTokens", ", ".join(f"{s['recency/lexical'][n]['history_tokens']:,}" for n in sizes))
m("LCEmbedIndexS", f"{s['foundry_embedding_index_s']['1000']:.1f}")

# ---------------------------------------------------------------- security / hallucination
sec = json.loads((CAMPAIGN / "security_hallucination.json").read_text())
t = sec["prompt_injection"]["test"]
for key, macro in (("foundry_regex_gate", "Regex"), ("foundry_trained_nb", "NB")):
    x = t[key]
    m(f"Inj{macro}Recall", pct(x["recall"]))
    m(f"Inj{macro}Precision", pct(x["precision"]))
    m(f"Inj{macro}Fone", f"{x['f1']:.3f}")
    m(f"Inj{macro}FPR", pct(x["fpr"]))
    lo, hi = wilson(x["tp"], x["tp"] + x["fn"])
    m(f"Inj{macro}RecallCI", f"[{100 * lo:.1f}, {100 * hi:.1f}]")
m("InjTestN", t["foundry_trained_nb"]["n"])
m("InjTestPos", t["foundry_trained_nb"]["positives"])
m("InjTrainN", sec["prompt_injection"]["train"]["foundry_trained_nb"]["n"])
m("InjRegexTrainRecall", pct(sec["prompt_injection"]["train"]["foundry_regex_gate"]["recall"]))
m("InjNBAUROC", f"{t['trained_nb_auroc']:.3f}")
lo, hi = hanley_mcneil(t["trained_nb_auroc"], t["foundry_trained_nb"]["positives"],
                       t["foundry_trained_nb"]["n"] - t["foundry_trained_nb"]["positives"])
m("InjNBAUROCCI", f"[{lo:.3f}, {hi:.3f}]")
m("InjRevision", sec["prompt_injection"]["test"]["revision"][:8])
h = sec["hallucination"]
m("HaluPairs", f"{h['pairs']:,}")
m("HaluRevision", h["revision"][:8])
for key, macro in (("word_overlap_auroc", "HaluOverlap"), ("length_baseline_auroc", "HaluLength"),
                   ("length_matched_word_overlap_auroc", "HaluMatchedOverlap"),
                   ("length_matched_length_baseline_auroc", "HaluMatchedLength")):
    n_ = h["length_matched_pairs"] if "matched" in key else h["pairs"]
    lo, hi = hanley_mcneil(h[key], n_, n_)
    m(macro, f"{h[key]:.3f}")
    m(macro + "CI", f"[{lo:.3f}, {hi:.3f}]")
m("HaluMatchedPairs", h["length_matched_pairs"])
m("HaluHeldoutAcc", pct(h["word_overlap_heldout_accuracy"]))
m("HaluNumeric", f"{h['numeric_fact_check_auroc']:.3f}")

# ---------------------------------------------------------------- WorkBench (earlier study)
live = list(csv.DictReader((EV / "live-workbench-20260928/derived/live_results.csv").open()))
att = list(csv.DictReader((EV / "live-workbench-20260928/derived/per_attempt.csv").open()))
CAMP = [("pilot-20260928-openai-responses", "Development (12)"), ("heldout-20260928-baseline", "First 60 (inspected)"),
        ("diagnostic-20260928-grounded", "Guidance, same 60"), ("confirmation-20260928-baseline", "Fresh 60 (untouched)")]
ARMS = [("reference", "R"), ("foundry_native_serial", "N"), ("foundry_langgraph_serial", "LG")]
with (OUT / "workbench_rows.tex").open("w") as fh:
    for camp, label in CAMP:
        cells = []
        for arm, _ in ARMS:
            r = next(x for x in live if x["campaign"] == camp and x["arm"] == arm and x["provider"] == "openai")
            cells.append(f"{r['correct']}/{r['attempted']}")
        outcome = {}
        for arm, _ in ARMS:
            outcome[arm] = {a["task_id"]: a["correct"] == "True" for a in att
                            if a["campaign"] == camp and a["arm"] == arm and a["provider"] == "openai"}
        stats = []
        for arm in ("foundry_native_serial", "foundry_langgraph_serial"):
            common = set(outcome[arm]) & set(outcome["reference"])
            b = sum(outcome[arm][k] and not outcome["reference"][k] for k in common)
            c = sum(outcome["reference"][k] and not outcome[arm][k] for k in common)
            stats.append(f"{b}/{c}, $p{{=}}{mcnemar(b, c):.2f}$")
        fh.write(f"{label} & " + " & ".join(cells) + " & " + " & ".join(stats) + " \\\\\n")
fresh = {arm: {a["task_id"]: a["correct"] == "True" for a in att if a["campaign"] == "confirmation-20260928-baseline"
               and a["arm"] == arm} for arm, _ in ARMS}
common = set(fresh["foundry_native_serial"]) & set(fresh["reference"])
b = sum(fresh["foundry_native_serial"][k] and not fresh["reference"][k] for k in common)
c = sum(fresh["reference"][k] and not fresh["foundry_native_serial"][k] for k in common)
m("WBFreshB", b)
m("WBFreshC", c)
m("WBFreshP", f"{mcnemar(b, c):.2f}")
frontier = list(csv.DictReader((EV / "workbench-published-frontier/scores.csv").open()))
top = max(frontier, key=lambda r: int(r["correct"]))
m("WBFrontierModel", top["model"])
m("WBFrontierCorrect", top["correct"])
m("WBFrontierAcc", pct(float(top["accuracy"]), 2))
m("WBFrontierRuns", len(frontier))
m("WBFrontierPredictions", f"{690 * len(frontier):,}")

# ---------------------------------------------------------------- tests
pt = (CAMPAIGN / "pytest.txt").read_text()
mm = re.search(r"(\d+) passed(?:, (\d+) skipped)?", pt)
m("TestsPassed", mm.group(1))
m("TestsSkipped", mm.group(2) or "0")
m("TestsFailed", (re.search(r"(\d+) failed", pt) or [None, "0"])[1])
manifest = json.loads((CAMPAIGN / "manifest.json").read_text())
m("CampaignCommit", manifest["foundry_commit"][:7])
m("CampaignFiles", len(manifest["files"]))

# ---------------------------------------------------------------- figure data
with (OUT / "fig_agb.dat").open("w") as fh:
    fh.write("idx label total\n")
    bars = [("Foundry", int(macros["AGBFoundry"]))] + [(NAMES.get(r["runner"], r["runner"]).replace(" + ACP", "+ACP")
            .replace(" gateway (API mode)", " API").replace(" ", "~"), r["passed"]) for r in governed] + \
           [("Native~(7)", native[0]["passed"]), ("Audit-only", score(trivial["audit_only"])[0]),
            ("Deny-all", score(trivial["deny_all"])[0]), ("Allow+audit", score(trivial["allow_all"])[0])]
    for i, (label, total) in enumerate(bars):
        fh.write(f"{i} {{{label}}} {total}\n")
m("FigAGBBars", len(bars) - 1)
with (OUT / "fig_longctx.dat").open("w") as fh:
    fh.write("tokens recency foundry paraphrase\n")
    for n in sizes:
        fh.write(f"{s['recency/lexical'][n]['history_tokens']} {100 * s['recency/lexical'][n]['retention']:.0f} "
                 f"{100 * s['foundry/lexical'][n]['retention']:.0f} {100 * s['foundry/paraphrase'][n]['retention']:.0f}\n")
with (OUT / "fig_shards.dat").open("w") as fh:
    fh.write("k throughput ideal\n")
    for r in sh["results"]:
        fh.write(f"{r['processes']} {r['aggregate_throughput_per_s'] / 1000:.2f} {base * r['processes'] / 1000:.2f}\n")

(OUT / "numbers.tex").write_text("% Generated by build_evidence.py. Do not edit.\n" + "".join(
    f"\\newcommand{{\\{k}}}{{{v}}}\n" for k, v in sorted(macros.items())))
summary["macros"] = macros
(OUT / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
print(f"wrote {len(macros)} macros")

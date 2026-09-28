"""Verify archived attempts and generate review tables without API calls.

This verifies provenance, coverage and arithmetic from stored official scores.
The separate pinned WorkBench harness is needed to rerun the official scorer.
No archives are extracted onto the filesystem and no credentials are loaded.
"""
from __future__ import annotations

from collections import defaultdict
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import random
import statistics
import tarfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ARCHIVES = ROOT / "review/evidence/live-workbench-20260928"
OUT = HERE / "live-workbench-20260928/derived"
LABELS = {
    "pilot-20260928": "Rejected Anthropic setup",
    "pilot-20260928-openai": "Rejected OpenAI setup",
    "pilot-20260928-anthropic-diagnostic": "Rejected Anthropic workspace",
    "pilot-20260928-openai-responses": "Development baseline (12 tasks)",
    "heldout-20260928-baseline": "Initial baseline (60; later inspected)",
    "diagnostic-20260928-grounded": "Guidance diagnostic (same 60)",
    "confirmation-20260928-baseline": "Fresh baseline (next 60)",
    "confirmation-20260928-grounded": "Fresh guidance (next 60)",
}
ARM_LABELS = {"reference": "Reference", "foundry_native_serial": "Foundry native",
              "foundry_langgraph_serial": "Foundry LangGraph"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True, allow_nan=False) + "\n")


def verify_campaign(directory):
    index = json.loads((directory / "archive-index.json").read_text())
    archive = (directory / "attempts-and-provenance.tar.gz").read_bytes()
    if sha(archive) != index["archive_sha256"]:
        raise ValueError(f"Archive hash mismatch: {directory.name}")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        names = tar.getnames()
        if len(names) != len(set(names)) or set(names) != set(index["files"]):
            raise ValueError(f"Unexpected archive members: {directory.name}")
        content = {}
        for member in tar.getmembers():
            if not member.isfile():
                raise ValueError("Only ordinary artifact files permitted")
            data = tar.extractfile(member).read()
            expected = index["files"][member.name]
            if sha(data) != expected["sha256"] or len(data) != expected["bytes"]:
                raise ValueError(f"Member hash mismatch: {member.name}")
            content[member.name] = data
    for name in ("manifest.json", "summary.json", "execution.json", "environment.txt"):
        if (directory / name).read_bytes() != content[name]:
            raise ValueError(f"Readable artifact diverges from archive: {name}")
    manifest = json.loads(content["manifest.json"])
    summary = json.loads(content["summary.json"])
    rows = [json.loads(data) for name, data in content.items() if name.startswith("attempts/")]
    expected_keys = {(t, r, p, a) for t in manifest["task_ids"]
                     for r in range(manifest["repetitions"])
                     for p in manifest["providers"] for a in manifest["arms"]}
    keys = [(r["task_id"], r["repetition"], r["provider"], r["arm"]) for r in rows]
    if len(keys) != len(set(keys)) or not set(keys) <= expected_keys:
        raise ValueError("Duplicate or unplanned attempts")
    if any(r["status"] != "finished" for r in rows):
        raise ValueError("Unfinished attempt in archived results")
    if len(rows) != summary["rows_finished"] or (set(keys) == expected_keys) != summary["complete"]:
        raise ValueError("Summary coverage mismatch")
    aggregates = []
    for key, saved in summary["aggregates"].items():
        provider, arm = key.split("/")
        group = [r for r in rows if r["provider"] == provider and r["arm"] == arm]
        latencies = sorted(r["latency_s"] for r in group)
        correct = sum(r["score"]["correct"] for r in group)
        observed = {
            "attempted": len(group), "correct": correct,
            "planned": len(manifest["task_ids"]) * manifest["repetitions"],
            "accuracy_among_attempted": correct / len(group) if group else None,
            "unwanted_side_effects": sum(r["score"]["unwanted_side_effects"] for r in group),
            "errors": sum(bool(r["error"]) for r in group),
            "cost_usd_upper": sum(r["cost_usd_upper"] for r in group),
            "model_requests": sum(len(r["traces"]) for r in group),
            "p50_latency_s": statistics.median(latencies) if group else None,
            "p95_latency_s": latencies[math.ceil(len(group) * .95) - 1] if group else None,
        }
        observed["cost_per_success_usd_upper"] = observed["cost_usd_upper"] / correct if correct else None
        for field, value in observed.items():
            if value is None:
                if saved[field] is not None:
                    raise ValueError(f"Missing-data mismatch: {field}")
            elif not math.isclose(value, saved[field], rel_tol=1e-10, abs_tol=1e-10):
                raise ValueError(f"Aggregate mismatch: {directory.name}/{key}/{field}")
        aggregates.append({"campaign": directory.name, "phase": LABELS[directory.name],
                           "instruction_profile": manifest.get("instruction_profile", "baseline"),
                           "complete": summary["complete"], "provider": provider, "arm": arm,
                           "model": manifest["models"][provider]["model"], **observed})
    return manifest, summary, rows, aggregates, index


def paired_interval(values):
    # Exploratory task-level interval; not a confirmatory significance test.
    rng = random.Random(20260928)
    boot = sorted(statistics.mean(rng.choices(values, k=len(values))) for _ in range(4000))
    return [boot[100], boot[3900]]


def compare_profiles(campaigns):
    a, b = "confirmation-20260928-baseline", "confirmation-20260928-grounded"
    if a not in campaigns or b not in campaigns:
        return {"status": "pending_both_complete_campaigns"}
    ma, sa, ra, _, _ = campaigns[a]
    mb, sb, rb, _, _ = campaigns[b]
    if not sa["complete"] or not sb["complete"]:
        return {"status": "incomplete_no_paired_claim"}
    for field in ("task_ids", "models", "foundry_source", "foundry_prompt_resources",
                  "adapter_source", "arms", "providers", "repetitions", "max_calls",
                  "max_output_tokens", "timeout_s", "seed", "workbench_data", "workbench_source"):
        if ma.get(field) != mb.get(field):
            raise ValueError(f"Confirmation comparison not matched: {field}")
    comparison = {}
    for provider in ma["providers"]:
        for arm in ma["arms"]:
            left = {(r["task_id"], r["repetition"]): r for r in ra if r["provider"] == provider and r["arm"] == arm}
            right = {(r["task_id"], r["repetition"]): r for r in rb if r["provider"] == provider and r["arm"] == arm}
            deltas = defaultdict(list)
            for key in sorted(left):
                x, y = left[key], right[key]
                deltas[key[0]].append(int(y["score"]["correct"]) - int(x["score"]["correct"]))
            task_deltas = [statistics.mean(v) for v in deltas.values()]
            comparison[f"{provider}/{arm}"] = {
                "paired_tasks": len(task_deltas),
                "guidance_minus_baseline_accuracy": statistics.mean(task_deltas),
                "exploratory_task_bootstrap_95_interval": paired_interval(task_deltas),
                "baseline_only_correct": sum(bool(left[k]["score"]["correct"]) and not right[k]["score"]["correct"] for k in left),
                "guidance_only_correct": sum(bool(right[k]["score"]["correct"]) and not left[k]["score"]["correct"] for k in left),
            }
    return {"status": "matched_exploratory", "comparisons": comparison,
            "limitations": ["one repetition", "three arm comparisons; no multiplicity-adjusted significance claim",
                            "fixed campaign order", "public tasks; project-held-out only"]}


def tex(value):
    return str(value).replace("_", r"\_").replace("%", r"\%")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    campaigns = {name: verify_campaign(ARCHIVES / name) for name in LABELS
                 if (ARCHIVES / name / "archive-index.json").exists()}
    if not campaigns:
        raise ValueError("No verified campaigns")
    aggregates = [r for _, _, _, rows, _ in campaigns.values() for r in rows]
    attempts = []
    for name, (manifest, _, rows, _, _) in campaigns.items():
        for r in sorted(rows, key=lambda r: (r["task_id"], r["provider"], r["arm"], r["repetition"])):
            attempts.append({"campaign": name, "phase": LABELS[name], "task_id": r["task_id"],
                             "provider": r["provider"], "arm": r["arm"], "repetition": r["repetition"],
                             "instruction_profile": manifest.get("instruction_profile", "baseline"),
                             **r["score"], "error": r["error"], "latency_s": r["latency_s"],
                             "cost_usd_upper": r["cost_usd_upper"], "model_requests": len(r["traces"])})
    for name, rows in (("live_results.csv", aggregates), ("per_attempt.csv", attempts)):
        with (OUT / name).open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    table = []
    for name, (_, summary, _, rows, _) in campaigns.items():
        if not summary["complete"]:
            continue  # Setup errors are separately reported, never hidden in a complete-task comparison.
        table.append(r"\multicolumn{7}{l}{\textit{" + tex(LABELS[name]) + r"}} \\")
        for row in sorted(rows, key=lambda r: list(ARM_LABELS).index(r["arm"])):
            table.append(f"{ARM_LABELS[row['arm']]} & {row['correct']}/{row['attempted']} & "
                         f"{row['accuracy_among_attempted']*100:.1f} & {row['unwanted_side_effects']} & "
                         f"{row['cost_usd_upper']:.3f} & {row['p50_latency_s']:.2f} & {row['p95_latency_s']:.2f} " + r"\\")
        table.append(r"\addlinespace")
    (OUT / "live_results_rows.tex").write_text("\n".join(table) + "\n")
    paired = compare_profiles(campaigns)
    write_json(OUT / "profile_comparison.json", paired)
    audit = {"campaigns": {name: {"attempts": len(rows), "complete": summary["complete"],
                                       "archive_sha256": index["archive_sha256"],
                                       "source_manifest_sha256": sha((ARCHIVES/name/"manifest.json").read_bytes()),
                                       "stop_reason": summary.get("stop_reason")}
                           for name, (_, summary, rows, _, index) in campaigns.items()},
             "total_finished_attempts_including_setup_failures": len(attempts),
             "arithmetic_and_coverage_verified": True, "official_scorer_rerun": False,
             "live_inference_rerun": False, "profile_comparison": paired,
             "boundary": "Stored official per-attempt scores recounted; original source/data/manifests/response traces retained in archives",
             "generated_files_sha256": {name: sha((OUT/name).read_bytes()) for name in
                                         ("live_results.csv", "per_attempt.csv", "live_results_rows.tex", "profile_comparison.json")}}
    write_json(OUT / "audit_results.json", audit)
    print(json.dumps({"verified_campaigns": len(campaigns), "verified_attempts": len(attempts),
                      "profile_comparison_status": paired["status"]}))


if __name__ == "__main__":
    main()

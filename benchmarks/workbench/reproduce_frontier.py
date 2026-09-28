"""Re-score all saved WorkBench Revisited runs without model/API calls.

This reproduces the frontier of the pinned published comparison, not a global
leaderboard or Foundry performance. No saved trajectory is used for inference.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import time

from common import WORKBENCH_COMMIT, bootstrap, reproduce, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbench-root", default="/private/tmp/agent-foundry-workbench")
    parser.add_argument("--output", type=Path, required=True, help="New output directory")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.exists():
        parser.error("Output directory exists; preserve earlier results and choose a new directory")
    root = bootstrap(args.workbench_root)
    source = root / "retro/data/model_results.json"
    published = json.loads(source.read_text())
    # Validate before admitting model names into artifact paths.
    names = list(published["models"])
    if not names:
        raise ValueError("No published models")
    output.mkdir(parents=True)
    started = datetime.now(timezone.utc).isoformat()
    timer = time.monotonic()
    rows = []
    for index, name in enumerate(names, 1):
        artifact = output / f"model-{index:02d}.json"
        try:
            reproduce(root, artifact, name)
        except AssertionError:
            # Keep every mismatch; finish evaluating the complete published set.
            pass
        report = json.loads(artifact.read_text())
        totals = report["totals"]
        expected = published["models"][name]
        matches = report["matches"] and all(totals[k] == expected[k] for k in totals)
        if totals["total"] != published["total_tasks"]:
            matches = False
        row = {"model": name, **totals,
               "accuracy": totals["correct"] / totals["total"],
               "unwanted_side_effect_rate": totals["side_effects"] / totals["total"],
               "matches_published_counts": matches, "artifact": artifact.name}
        rows.append(row)
        print(json.dumps({"progress": f"{index}/{len(names)}", **row}), flush=True)

    ranked = sorted(rows, key=lambda row: (-row["correct"], row["side_effects"], row["model"]))
    with (output / "scores.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(ranked[0]))
        writer.writeheader()
        writer.writerows(ranked)

    # Metadata sources are hashed separately from the per-domain predictions in
    # model-NN.json. No runtime cost/latency reproduction is claimed.
    from src.evals.metrics import ground_truth_path, meta_path_for_results
    sources = {source, root / "uv.lock"}
    sources.update((root / "src").rglob("*.py"))
    sources.update((root / "data/processed").rglob("*.csv"))
    sources.add(root / "data/raw/email_addresses.csv")
    for model in published["models"].values():
        for domain, prediction in model["sources"].items():
            sources.add(root / ground_truth_path(domain, model["ground_truth_version"]))
            sidecar = root / meta_path_for_results(prediction)
            if sidecar.exists():
                sources.add(sidecar)
    summary = {
        "kind": "pinned_published_frontier_reproduction",
        "not_foundry_performance": True,
        "not_a_fresh_model_evaluation": True,
        "not_a_global_sota_claim": True,
        "workbench_commit": WORKBENCH_COMMIT,
        "source": "https://github.com/olly-styles/WorkBench",
        "started_utc": started,
        "finished_utc": datetime.now(timezone.utc).isoformat(),
        "reproduction_wall_time_s": time.monotonic() - timer,
        "paid_model_requests": 0,
        "model_count": len(rows),
        "scored_saved_task_predictions": sum(row["total"] for row in rows),
        "all_counts_match": all(row["matches_published_counts"] for row in rows),
        "best_published_by_accuracy": ranked[0],
        "ranking": ranked,
        "cost_and_inference_latency_reproduced": False,
        "limits": [
            "Saved model runs differ in model and may differ in inference settings/date.",
            "Task accuracy measures model plus harness; it does not isolate framework quality.",
            "Re-scoring public predictions is not fresh inference or a hidden-test result.",
            "Repeated reliability, uncertainty, and modern external framework baselines remain unmeasured.",
        ],
        "python": sys.version,
        "platform": platform.platform(),
        "packages": sorted({f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions()}),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "common_sha256": hashlib.sha256(Path(__file__).with_name("common.py").read_bytes()).hexdigest(),
        "source_sha256": {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sources)},
        "artifact_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir()) if p.is_file()},
    }
    write_json(output / "summary.json", summary)
    if not summary["all_counts_match"]:
        raise SystemExit("One or more counts differ; inspect retained artifacts before a comparison")
    print(json.dumps({"all_counts_match": True, "models": len(rows), "best": ranked[0],
                      "summary": str(output / "summary.json")}), flush=True)


if __name__ == "__main__":
    main()

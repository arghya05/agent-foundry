"""Pinned WorkBench data and scorer integration; no provider calls here."""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys

WORKBENCH_COMMIT = "49c7dfd00c03d384ec59ea57374f50b766aa5613"
HERE = Path(__file__).resolve().parent
FOUNDRY = HERE.parents[1]
ARMS = ("reference", "foundry_native_serial", "foundry_langgraph_serial")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def bootstrap(root):
    root = Path(root).resolve()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    if revision != WORKBENCH_COMMIT:
        raise ValueError(f"Expected WorkBench {WORKBENCH_COMMIT}; got {revision}")
    subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", "src", "data", "uv.lock"],
                   cwd=root, check=True, stdout=subprocess.DEVNULL)
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(FOUNDRY))
    os.chdir(root)  # Upstream tools load CSVs relative to the repository root.
    return root


def system_prompt():
    from src.data_generation.data_generation_utils import HARDCODED_CURRENT_TIME as now
    return (f"Today's date is {now.strftime('%A')}, {now.date()} "
            f"and the current time is {now.time()}. "
            "Remember the current date and time when completing tasks. "
            "Meetings must not start before 9am or end after 6pm.")


def load_tasks(per_domain=0, seed=20260927):
    import pandas as pd
    from src.evals.metrics import ALL_DOMAINS, ground_truth_path
    tasks = []
    for domain in ALL_DOMAINS:
        frame = pd.read_csv(ground_truth_path(domain, "v2"), dtype=str)
        indices = list(range(len(frame)))
        random.Random(f"{seed}:{domain}").shuffle(indices)
        if per_domain:
            indices = indices[:per_domain]
        for index in sorted(indices):
            row = frame.iloc[index]
            tasks.append({"id": f"{domain}:{index}", "domain": domain, "task": row["task"],
                          "outcome": ast.literal_eval(row["outcome"])})
    # Interleave domains rather than spend the whole budget on one domain.
    random.Random(seed).shuffle(tasks)
    return tasks


def load_heldout_tasks(development_per_domain, heldout_per_domain=0, seed=20260927, offset_per_domain=0):
    """Fixed stratified slice selected by IDs, never outcomes or model scores."""
    if development_per_domain <= 0 or heldout_per_domain < 0 or offset_per_domain < 0:
        raise ValueError("Invalid split sizes")
    development = {t["id"] for t in load_tasks(development_per_domain + offset_per_domain, seed)}
    count = development_per_domain + offset_per_domain + heldout_per_domain if heldout_per_domain else 0
    tasks = [t for t in load_tasks(count, seed) if t["id"] not in development]
    if heldout_per_domain:
        from collections import Counter
        counts = Counter(t["domain"] for t in tasks)
        if len(counts) != 6 or any(n != heldout_per_domain for n in counts.values()):
            raise ValueError("Requested held-out slice exceeds a domain's available tasks")
    return tasks


def score(task, actions, error=""):
    import pandas as pd
    from src.evals.metrics import compute_metrics
    ground_truth = pd.DataFrame([{"task": task["task"], "outcome": task["outcome"]}])
    predictions = pd.DataFrame([{"task": task["task"], "function_calls": actions,
                                 "full_response": "", "error": error}])
    row = compute_metrics(ground_truth, predictions).iloc[0]
    return {name: bool(row[name]) for name in ("correct", "exact_match", "unwanted_side_effects")}


def reproduce(root, output, model="GPT-4o"):
    """Re-score committed upstream predictions, never label them Foundry results."""
    from src.evals.metrics import ground_truth_path, load_and_score_results
    upstream = json.loads((root / "retro/data/model_results.json").read_text())["models"][model]
    results = {}
    for domain, source in upstream["sources"].items():
        frame = load_and_score_results(source, ground_truth_path(domain, upstream["ground_truth_version"]))
        measured = {"correct": int(frame.correct.sum()), "side_effects": int(frame.unwanted_side_effects.sum()),
                    "total": len(frame)}
        expected = upstream["per_tool"][domain]
        results[domain] = {"measured": measured, "expected": expected, "matches": measured == expected,
                           "source": source, "sha256": hashlib.sha256((root / source).read_bytes()).hexdigest()}
    report = {"kind": "upstream_saved_prediction_reproduction", "not_foundry_performance": True,
              "workbench_commit": WORKBENCH_COMMIT, "ground_truth_version": upstream["ground_truth_version"],
              "model": model, "domains": results, "matches": all(r["matches"] for r in results.values()),
              "totals": {k: sum(r["measured"][k] for r in results.values())
                         for k in ("correct", "side_effects", "total")}}
    write_json(output, report)
    print(json.dumps({"reproduction_matches": report["matches"], **report["totals"]}))
    if not report["matches"]:
        raise AssertionError("Upstream baseline reproduction differs; do not run live experiments.")

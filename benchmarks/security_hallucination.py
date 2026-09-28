"""Public-dataset evaluation of two deterministic default detectors.

1. Prompt-injection input gate: `guardrails.looks_like_injection` on
   deepset/prompt-injections (train + test, Apache-2.0).
2. Hallucination / groundedness scorer: `kpi.word_overlap` (the score behind
   `reference_check_kpi`) and `fact_check_kpi` on HaluEval QA (MIT), scoring
   each right and hallucinated answer against the provided knowledge.
Datasets are fetched as the Hugging Face parquet export at a recorded
revision; row hashes are written with the results. No model calls. These
defaults are dependency-free heuristics; results bound what they catch.
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_foundry.guardrails import looks_like_injection  # noqa: E402
from agent_foundry.injection_classifier import InjectionClassifier, combined_detector  # noqa: E402
from agent_foundry.kpi import fact_check_kpi, word_overlap  # noqa: E402

def fetch(dataset: str, config: str, split: str) -> tuple[list[dict], str]:
    """Parquet export at the dataset's current revision (needs pyarrow)."""
    import io

    import pyarrow.parquet as pq
    meta = json.load(urllib.request.urlopen(f"https://huggingface.co/api/datasets/{dataset}", timeout=60))
    urls = json.load(urllib.request.urlopen(
        f"https://huggingface.co/api/datasets/{dataset}/parquet/{config}/{split}", timeout=60))
    rows: list[dict] = []
    for url in urls:
        data = urllib.request.urlopen(url, timeout=120).read()
        rows += pq.read_table(io.BytesIO(data)).to_pylist()
    return rows, meta.get("sha", "unknown")


def digest(rows: list[dict]) -> str:
    return hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()


def binary_metrics(labels: list[int], preds: list[int]) -> dict:
    tp = sum(1 for y, p in zip(labels, preds) if y and p)
    fp = sum(1 for y, p in zip(labels, preds) if not y and p)
    fn = sum(1 for y, p in zip(labels, preds) if y and not p)
    tn = sum(1 for y, p in zip(labels, preds) if not y and not p)
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return dict(n=len(labels), positives=sum(labels), tp=tp, fp=fp, fn=fn, tn=tn, precision=prec, recall=rec,
                f1=2 * prec * rec / (prec + rec) if prec + rec else 0.0, fpr=fp / (fp + tn) if fp + tn else 0.0,
                accuracy=(tp + tn) / len(labels))


def auroc(pos: list[float], neg: list[float]) -> float:
    """P(score(pos) > score(neg)) + 0.5 P(tie), exact via ranks."""
    allv = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    rank_sum, i = 0.0, 0
    while i < len(allv):
        j = i
        while j < len(allv) and allv[j][0] == allv[i][0]:
            j += 1
        avg = (i + j + 1) / 2
        rank_sum += avg * sum(1 for k in range(i, j) if allv[k][1] == 1)
        i = j
    return (rank_sum - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


def main() -> None:
    out: dict = {}
    inj = {}
    data = {split: fetch("deepset/prompt-injections", "default", split) for split in ("train", "test")}
    train_rows = data["train"][0]
    # Trained only on the train split; the test split is never used for fitting
    # or threshold choice (threshold fixed at 0.5).
    clf = InjectionClassifier().fit([r["text"] for r in train_rows], [int(r["label"]) for r in train_rows])
    both = combined_detector(clf)
    for split, (rows, sha) in data.items():
        labels = [int(r["label"]) for r in rows]
        probs = [clf.probability(r["text"]) for r in rows]
        inj[split] = {"revision": sha, "rows_sha256": digest(rows),
                      "foundry_regex_gate": binary_metrics(labels, [int(looks_like_injection(r["text"])) for r in rows]),
                      "foundry_trained_nb": binary_metrics(labels, [int(p >= 0.5) for p in probs]),
                      "foundry_regex_or_nb": binary_metrics(labels, [int(both(r["text"])) for r in rows]),
                      "trained_nb_auroc": auroc([p for p, y in zip(probs, labels) if y], [p for p, y in zip(probs, labels) if not y]),
                      "always_flag": binary_metrics(labels, [1] * len(rows)),
                      "never_flag": binary_metrics(labels, [0] * len(rows))}
        inj[split]["note"] = "train split = fitting data (in-sample)" if split == "train" else "held-out"
    out["prompt_injection"] = inj

    rows, sha = fetch("pminervini/HaluEval", "qa", "data")
    right = [word_overlap(r["right_answer"], [r["knowledge"]]) for r in rows]
    halluc = [word_overlap(r["hallucinated_answer"], [r["knowledge"]]) for r in rows]
    length_r = [-len(r["right_answer"]) for r in rows]
    length_h = [-len(r["hallucinated_answer"]) for r in rows]
    fact = fact_check_kpi("numeric", references=lambda ctx: ctx["refs"])
    fact_r = [fact.score({"output_text": r["right_answer"], "refs": [r["knowledge"]]}) for r in rows]
    fact_h = [fact.score({"output_text": r["hallucinated_answer"], "refs": [r["knowledge"]]}) for r in rows]
    # Threshold chosen on even rows, evaluated on odd rows (paired answers kept together).
    train = [i for i in range(len(rows)) if i % 2 == 0]
    test = [i for i in range(len(rows)) if i % 2 == 1]
    cands = sorted(set(right[i] for i in train) | set(halluc[i] for i in train))

    def acc(th, idx):
        return sum((right[i] >= th) + (halluc[i] < th) for i in idx) / (2 * len(idx))
    best = max(cands, key=lambda th: acc(th, train))
    paired = sum(1 for a, b in zip(right, halluc) if a > b) / len(rows)
    # Length-controlled subset: pairs whose answer lengths differ by <= 20%.
    matched = [i for i, r in enumerate(rows)
               if abs(len(r["right_answer"]) - len(r["hallucinated_answer"]))
               <= 0.2 * max(len(r["right_answer"]), len(r["hallucinated_answer"]))]
    out["hallucination"] = {
        "dataset": "pminervini/HaluEval qa", "revision": sha, "rows_sha256": digest(rows), "pairs": len(rows),
        "word_overlap_auroc": auroc(right, halluc), "length_baseline_auroc": auroc(length_r, length_h),
        "numeric_fact_check_auroc": auroc(fact_r, fact_h),
        "word_overlap_paired_win_rate": paired,
        "word_overlap_threshold": best, "word_overlap_heldout_accuracy": acc(best, test),
        "length_matched_pairs": len(matched),
        "length_matched_word_overlap_auroc": auroc([right[i] for i in matched], [halluc[i] for i in matched]),
        "length_matched_length_baseline_auroc": auroc([length_r[i] for i in matched], [length_h[i] for i in matched]),
    }
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()

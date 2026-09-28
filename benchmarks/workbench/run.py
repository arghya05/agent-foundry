"""Offline setup/reproduction or explicitly budgeted live WorkBench comparisons."""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import platform
from datetime import datetime, timezone
import random
import statistics
import subprocess
import sys

from common import ARMS, FOUNDRY, HERE, WORKBENCH_COMMIT, bootstrap, digest, load_heldout_tasks, load_tasks, reproduce, score, write_json


def hashes(root, paths):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(paths)}


def manifest(root, tasks, args, models):
    return {
        "protocol": "workbench-foundry-v4", "workbench_commit": WORKBENCH_COMMIT,
        "provider_endpoints": {"openai": "/v1/responses", "anthropic": "/v1/messages"},
        "ground_truth_version": "v2", "tool_selection": "all", "act_without_confirmation": True,
        "foundry_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=FOUNDRY, text=True).strip(),
        "foundry_source": hashes(FOUNDRY, (FOUNDRY / "agent_foundry").rglob("*.py")),
        "foundry_prompt_resources": hashes(FOUNDRY, (FOUNDRY / "agent_foundry/prompt_templates").glob("*.md")),
        "instruction_profile": getattr(args, "instruction_profile", "baseline"),
        "adapter_source": hashes(HERE, HERE.glob("*.py")),
        "workbench_source": hashes(root, (root / "src").rglob("*.py")),
        "workbench_data": hashes(root, [*(root / "data/processed").rglob("*.csv"),
                                        root / "data/raw/email_addresses.csv"]),
        "workbench_lock": hashlib.sha256((root / "uv.lock").read_bytes()).hexdigest(),
        "python": platform.python_version(), "models": models,
        "task_ids": [t["id"] for t in tasks], "task_digest": digest(tasks),
        "split": args.split, "per_domain": args.per_domain, "seed": args.seed, "repetitions": args.repetitions,
        "heldout_per_domain": getattr(args, "heldout_per_domain", 0),
        "heldout_offset_per_domain": getattr(args, "heldout_offset_per_domain", 0),
        "arms": list(ARMS), "providers": args.providers, "max_calls": args.max_calls,
        "max_output_tokens": args.max_output_tokens, "timeout_s": args.timeout_s,
        "dispatch": "serial_in_model_order_in_all_arms", "retries": 0,
        "foundry_guardrails": "default_regex_input_output_and_policy_action_gates",
        "benchmark_policy": "L4, synthetic allowlisted tools, common transport budgets",
        "unknown_tool_scoring": "retain_invalid_call_metadata_and_fail_attempt_even_after_recovery",
        "status": "protocol_not_a_performance_claim",
    }


def bootstrap_interval(differences, seed=42, count=2000):
    if not differences:
        return None
    rng = random.Random(seed)
    means = sorted(statistics.mean(rng.choices(differences, k=len(differences))) for _ in range(count))
    return [means[int(count * .025)], means[int(count * .975)]]


def summarize(output, tasks, args, ledger):
    rows = [json.loads(p.read_text()) for p in (output / "attempts").glob("*.json")]
    completed = [r for r in rows if r["status"] == "finished"]
    groups = defaultdict(list)
    for row in completed:
        groups[f"{row['provider']}/{row['arm']}"].append(row)
    aggregates = {}
    for provider in args.providers:
        for arm in ARMS:
            name = f"{provider}/{arm}"
            group = groups[name]
            success = sum(r["score"]["correct"] for r in group)
            cost = sum(r["cost_usd_upper"] for r in group)
            latency = sorted(r["latency_s"] for r in group)
            aggregates[name] = {
                "attempted": len(group), "planned": len(tasks) * args.repetitions,
                "correct": success, "accuracy_among_attempted": success / len(group) if group else None,
                "unwanted_side_effects": sum(r["score"]["unwanted_side_effects"] for r in group),
                "errors": sum(bool(r["error"]) for r in group), "cost_usd_upper": cost,
                "cost_per_success_usd_upper": cost / success if success else None,
                "p50_latency_s": statistics.median(latency) if latency else None,
                "p95_latency_s": latency[math.ceil(len(latency) * .95) - 1] if latency else None,
                "model_requests": sum(len(r["traces"]) for r in group),
                "api_latency_s": sum(t["latency_s"] for r in group for t in r["traces"] if "latency_s" in t),
            }
    paired = {}
    for provider in args.providers:
        reference = {(r["task_id"], r["repetition"]): r for r in groups[f"{provider}/reference"]}
        for arm in ARMS[1:]:
            candidate = {(r["task_id"], r["repetition"]): r for r in groups[f"{provider}/{arm}"]}
            by_task = defaultdict(list)
            for key in sorted(reference.keys() & candidate.keys()):
                by_task[key[0]].append(int(candidate[key]["score"]["correct"]) -
                                       int(reference[key]["score"]["correct"]))
            # Cluster repetitions by task, rather than treating them as independent tasks.
            differences = [statistics.mean(values) for values in by_task.values()]
            paired[f"{provider}/{arm}-reference"] = {
                "paired_tasks": len(differences),
                "accuracy_difference": statistics.mean(differences) if differences else None,
                "exploratory_paired_bootstrap_95_interval": bootstrap_interval(differences),
                "interpretation": "Pilot/exploratory; no SOTA or significance declaration"}
    full = len(completed) == len(tasks) * args.repetitions * len(ARMS) * len(args.providers)
    report = {"complete": full, "scope": args.split,
              "rows_finished": len(completed), "aggregates": aggregates, "paired": paired,
              "ledger_used_or_reserved_usd_upper": float(ledger.used),
              "note": "Missing tasks are not silently counted as successes. Historical results are not matched baselines."}
    write_json(output / "summary.json", report)
    return report


def live(root, tasks, args, models, output):
    import os
    import time
    from dotenv import load_dotenv
    from filelock import FileLock
    from adapter import run_task
    from transport import Budget, Transport

    load_dotenv(HERE / ".env", override=True)
    for provider in args.providers:
        if not os.environ.get({"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}[provider]):
            raise ValueError(f"Missing {provider} credential; no requests made")
    ledger_path = Path(args.ledger).resolve()
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    with FileLock(str(ledger_path) + ".lock", timeout=0), FileLock(str(output / ".run.lock"), timeout=0):
        ledger = Budget(ledger_path, args.max_spend_usd)
        snapshot = manifest(root, tasks, args, models)
        path = output / "manifest.json"
        if path.exists() and json.loads(path.read_text()) != snapshot:
            raise ValueError("Manifest changed; use a distinct experiment directory, retaining the same budget ledger")
        write_json(path, snapshot)
        if not (output / "execution.json").exists():
            write_json(output / "execution.json", {"started_utc": datetime.now(timezone.utc).isoformat(),
                                                    "argv": sys.argv, "platform": platform.platform()})
        # Works without pip or a writable global uv cache. Editable source is
        # pinned separately by the source hashes in the experiment manifest.
        environment = "\n".join(sorted({f"{d.metadata['Name']}=={d.version}"
                                         for d in importlib.metadata.distributions()})) + "\n"
        environment_path = output / "environment.txt"
        if environment_path.exists() and environment_path.read_text() != environment:
            raise ValueError("Installed dependencies changed; do not mix environments within one experiment")
        environment_path.write_text(environment)
        stop_reason = None
        for repetition in range(args.repetitions):
            for task in tasks:
                pairs = [(p, a) for p in args.providers for a in ARMS]
                random.Random(f"{args.seed}:{repetition}:{task['id']}").shuffle(pairs)
                for provider, arm in pairs:
                    identifier = digest([snapshot, task["id"], repetition, provider, arm])[:24]
                    attempt_path = output / "attempts" / (identifier + ".json")
                    if attempt_path.exists():
                        row = json.loads(attempt_path.read_text())
                        if row["status"] == "finished":
                            continue  # Errors are retained too, never selectively rerun.
                        # A killed/incomplete attempt consumes an attempt and its reservation.
                        row.update(error="InterruptedAttempt", status="finished", latency_s=row.get("latency_s", 0))
                        row["score"] = score(task, row["function_calls"], row["error"])
                        row["cost_usd_upper"] = sum(float(r.get("cost_usd_upper", r["reserved_usd"]))
                                                    for r in ledger.data["requests"] if r["attempt_id"] == identifier)
                        write_json(attempt_path, row)
                        continue
                    row = {"id": identifier, "provider": provider, "arm": arm, "task_id": task["id"],
                           "started_utc": datetime.now(timezone.utc).isoformat(),
                           "repetition": repetition, "status": "started", "function_calls": [], "traces": []}
                    write_json(attempt_path, row)

                    def persist(field, value):
                        row[field] = value
                        write_json(attempt_path, row)

                    transport = Transport(provider, models[provider], ledger, identifier,
                                          max_calls=args.max_calls, max_output_tokens=args.max_output_tokens,
                                          timeout_s=args.timeout_s, on_trace=lambda traces: persist("traces", traces))
                    started = time.monotonic()
                    try:
                        result = run_task(arm, task["task"], transport, max_calls=args.max_calls,
                                          instruction_profile=getattr(args, "instruction_profile", "baseline"),
                                          timeout_s=args.timeout_s, on_action=lambda actions: persist("function_calls", actions))
                    finally:
                        transport.close()
                    row.update(result, status="finished", latency_s=time.monotonic() - started,
                               finished_utc=datetime.now(timezone.utc).isoformat())
                    row["score"] = score(task, row["function_calls"], row["error"])
                    row["cost_usd_upper"] = sum(float(r.get("cost_usd_upper", r["reserved_usd"]))
                                                for r in ledger.data["requests"] if r["attempt_id"] == identifier)
                    write_json(attempt_path, row)
                    print(json.dumps({"task": task["id"], "provider": provider, "arm": arm,
                                      "correct": row["score"]["correct"], "error": row["error"],
                                      "used_or_reserved_usd_upper": float(ledger.used)}), flush=True)
                    if transport.fatal_error:
                        stop_reason = transport.fatal_error
                        break
                if stop_reason:
                    break
            if stop_reason:
                break
        summary = summarize(output, tasks, args, ledger)
        summary["stop_reason"] = stop_reason
        write_json(output / "summary.json", summary)
        print(json.dumps({"complete": summary["complete"], "stop_reason": stop_reason,
                          "summary": str(output / "summary.json")}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "reproduce", "live"])
    parser.add_argument("--workbench-root", default="/private/tmp/agent-foundry-workbench")
    parser.add_argument("--output", default=str(HERE / "results/pilot"))
    parser.add_argument("--ledger", default=str(HERE / "results/budget-ledger.json"))
    parser.add_argument("--max-spend-usd", type=float)
    parser.add_argument("--providers", nargs="+", choices=["openai", "anthropic"], default=["openai", "anthropic"])
    parser.add_argument("--per-domain", type=int, default=2, help="Development tasks per domain; use --split full for all 690")
    parser.add_argument("--heldout-per-domain", type=int, default=0,
                        help="Fixed tasks per domain after excluding development IDs; 0 selects every held-out task")
    parser.add_argument("--heldout-offset-per-domain", type=int, default=0,
                        help="Exclude this many additional previously consumed tasks per domain")
    parser.add_argument("--instruction-profile", choices=["baseline", "grounded"], default="baseline")
    parser.add_argument("--split", choices=["development", "diagnostic", "heldout", "full"], default="development")
    parser.add_argument("--seed", type=int, default=20260927)
    parser.add_argument("--repetitions", type=int, default=1)
    parser.add_argument("--max-calls", type=int, default=20)
    parser.add_argument("--max-output-tokens", type=int, default=2048)
    parser.add_argument("--timeout-s", type=float, default=600)
    args = parser.parse_args()
    output = Path(args.output).resolve()
    args.ledger = str(Path(args.ledger).resolve())
    if not math.isfinite(args.timeout_s) or args.per_domain < 0 or min(args.repetitions, args.max_calls, args.max_output_tokens, args.timeout_s) <= 0:
        parser.error("Invalid experiment limits")
    if len(set(args.providers)) != len(args.providers):
        parser.error("Duplicate providers")
    if args.heldout_offset_per_domain < 0 or args.heldout_per_domain < 0 or (
            (args.heldout_per_domain or args.heldout_offset_per_domain) and args.split not in ("heldout", "diagnostic")):
        parser.error("--heldout-per-domain requires --split heldout and a nonnegative count")
    if args.command == "live" and (args.max_spend_usd is None or not math.isfinite(args.max_spend_usd) or args.max_spend_usd <= 0):
        parser.error("Live calls require an explicit positive --max-spend-usd")
    root = bootstrap(args.workbench_root)
    if args.command == "reproduce":
        reproduce(root, output / "baseline-reproduction.json")
        return
    if args.split in ("development", "heldout", "diagnostic") and args.per_domain == 0:
        parser.error("Development/heldout splits require a positive --per-domain")
    tasks = load_tasks(args.per_domain if args.split == "development" else 0, args.seed)
    if args.split in ("heldout", "diagnostic"):
        tasks = load_heldout_tasks(args.per_domain, args.heldout_per_domain, args.seed, args.heldout_offset_per_domain)
    models = json.loads((HERE / "models.json").read_text())
    if args.command == "prepare":
        write_json(output / "planned-manifest.json", manifest(root, tasks, args, models))
        print(json.dumps({"tasks": len(tasks), "planned_attempts": len(tasks) * len(ARMS) * len(args.providers) * args.repetitions,
                          "paid_requests": 0, "manifest": str(output / "planned-manifest.json")}))
    else:
        live(root, tasks, args, models, output)


if __name__ == "__main__":
    main()

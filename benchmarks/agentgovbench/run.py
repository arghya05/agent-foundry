"""Run AgentGovBench against Agent Foundry with the unmodified upstream scorer.

Replicates the upstream CLI's run loop (benchmark/cli.py @ e0ce93a) with one
stricter rule: a runner exception is scored as a failed scenario instead of
being dropped, so the denominator is always the full scenario library.

    python benchmarks/agentgovbench/run.py --upstream /path/to/agentgovbench \
        --out review/evidence/agentgovbench-foundry-<date>
Optional: --runner vanilla|audit_only|agent_foundry, --ablate <control>,
--runner-module <python.module.path>:<ClassName> (resolved with
benchmarks/agentgovbench on sys.path; used for third-party competitor runners).
"""
from __future__ import annotations

import argparse
import contextlib
import io
import hashlib
import json
import platform
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

COMMIT = "e0ce93ae175376d7847c69a64d0c36bdfa6ca717"
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def run(upstream: Path, runner_name: str, ablate: str | None = None, scenarios_dir: Path | None = None,
        runner_module: str | None = None):
    sys.path[:0] = [str(upstream), str(HERE), str(REPO)]
    from benchmark import SCENARIO_LIBRARY_VERSION, SPEC_VERSION
    from benchmark.cli import _result_to_dict
    from benchmark.loader import load_all
    from benchmark.scorer import aggregate, score_scenario
    from benchmark.types import RunOutcome
    if runner_module:
        import importlib
        mod_name, _, cls_name = runner_module.partition(":")
        runner = getattr(importlib.import_module(mod_name), cls_name or "Runner")()
    elif runner_name == "agent_foundry":
        import foundry_runner
        runner = foundry_runner.Runner()
        if ablate:
            import ablations
            runner = ablations.apply(runner, ablate)
    else:
        import importlib
        runner = importlib.import_module(f"runners.{runner_name}").Runner()
    scenarios = load_all(scenarios_dir or (upstream / "scenarios"))
    results, vacuity = [], []
    for scn in scenarios:
        t0 = time.time()
        try:
            sink = io.StringIO()  # Foundry's tracer prints spans; keep them off the report
            with contextlib.redirect_stdout(sink):
                runner.setup(scn)
                for action in scn.actions:
                    runner.execute_action(action)
                outcome = runner.collect_outcome()
        except Exception as exc:  # scored, not dropped
            outcome = RunOutcome(runner_errors=[f"runner exception: {exc!r}"])
        finally:
            try:
                runner.teardown()
            except Exception:
                pass
        res = score_scenario(scn, outcome, runner.metadata.name, (time.time() - t0) * 1000)
        results.append(res)
        vacuity.append(anti_vacuity(scn, outcome, getattr(runner, "effects", None)))
    blob = {
        "spec_version": SPEC_VERSION, "scenario_library_version": SCENARIO_LIBRARY_VERSION,
        "upstream_commit": COMMIT, "runner": asdict(runner.metadata), "ablation": ablate,
        "aggregate": aggregate(results),
        "results": [_result_to_dict(r, True) for r in results],
        "anti_vacuity": vacuity,
        "anti_vacuity_summary": {k: sum(1 for v in vacuity if v[k] is True) for k in ("decision_coverage", "effect_fidelity", "liveness")}
        | {k + "_applicable": sum(1 for v in vacuity if v[k] is not None) for k in ("decision_coverage", "effect_fidelity", "liveness")},
    }
    return blob


def anti_vacuity(scn, outcome, effects) -> dict:
    """Supplemental checks, reported beside (never inside) the official score.

    decision_coverage: every attempted call has exactly one audited decision.
    effect_fidelity:   executed tool effects equal allowed outcomes (runners
                       that expose effects only; otherwise None).
    liveness:          scenarios with rate-limit or allow expectations admit at
                       least one call, so a deny-everything system cannot pass.
    """
    attempts = len(outcome.tool_outcomes)
    decisions = len(outcome.audit_entries)
    allowed = sum(1 for o in outcome.tool_outcomes if o.allowed)
    kinds = {a.kind for a in scn.expected}
    return {
        "scenario_id": scn.id, "attempts": attempts, "audited_decisions": decisions, "allowed": allowed,
        "executed_effects": None if effects is None else len(effects),
        "decision_coverage": attempts > 0 and decisions == attempts,
        "effect_fidelity": None if effects is None else len(effects) == allowed,
        "liveness": (allowed > 0) if kinds & {"rate_limited_count", "tool_allowed"} else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--upstream", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--runner", default="agent_foundry")
    ap.add_argument("--ablate", default=None)
    ap.add_argument("--scenarios", type=Path, default=None, help="alternative scenario library (supplemental)")
    ap.add_argument("--tag", default="")
    ap.add_argument("--runner-module", default=None,
                    help="<python.module.path>:<ClassName>; overrides --runner")
    args = ap.parse_args()
    up = args.upstream.resolve()
    if git(up, "rev-parse", "HEAD") != COMMIT:
        raise SystemExit(f"upstream must be pinned to {COMMIT}")
    if git(up, "status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("upstream tracked files must be unchanged")
    blob = run(up, args.runner, args.ablate, args.scenarios, args.runner_module)
    blob["environment"] = {"python": sys.version, "platform": platform.platform(),
                           "foundry_commit": git(REPO, "rev-parse", "HEAD"),
                           "foundry_dirty": bool(git(REPO, "status", "--porcelain", "--untracked-files=no"))}
    args.out.mkdir(parents=True, exist_ok=True)
    runner_label = blob["runner"]["name"] if args.runner_module else args.runner
    name = args.tag + runner_label + (f"-ablate-{args.ablate}" if args.ablate else "")
    path = args.out / f"{name}.json"
    text = json.dumps(blob, indent=2, default=str)
    path.write_text(text)
    agg = blob["aggregate"]
    av = blob["anti_vacuity_summary"]
    print(f"{name}: {agg['total_passed']}/{agg['total_scenarios']}  AV coverage {av['decision_coverage']}/{av['decision_coverage_applicable']} "
          f"fidelity {av['effect_fidelity']}/{av['effect_fidelity_applicable']} liveness {av['liveness']}/{av['liveness_applicable']}"
          f"  sha256={hashlib.sha256(text.encode()).hexdigest()[:16]}")
    for row in agg["by_category"]:
        print(f"  {row['category']:<32} {row['passed']}/{row['total']}")
    for r in blob["results"]:
        if not r["passed"]:
            notes = [a.get("note") for a in r.get("assertions", r.get("assertion_results", [])) if not a.get("passed")]
            print("  FAIL", r["scenario_id"], notes)


if __name__ == "__main__":
    main()

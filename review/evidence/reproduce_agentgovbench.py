"""Reproduce a pinned upstream baseline and audit evidence-free scoring.

No Foundry runner, credentials, model calls, or vendor services are used.
Run with Python >=3.10 and the upstream CLI dependencies installed.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

COMMIT = "e0ce93ae175376d7847c69a64d0c36bdfa6ca717"


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="New output directory")
    args = parser.parse_args()
    root = args.upstream.resolve()
    out = args.out.resolve()
    if git(root, "rev-parse", "HEAD") != COMMIT:
        raise SystemExit(f"Checkout must be pinned to {COMMIT}")
    if git(root, "status", "--porcelain", "--untracked-files=no"):
        raise SystemExit("Upstream tracked files must be unchanged")
    if out.exists():
        raise SystemExit("Output directory already exists; preserve it and choose a new directory")
    out.mkdir(parents=True)

    command = [sys.executable, "-m", "benchmark", "run", "--runner", "vanilla",
               "--include-outcomes", "--out", str(out / "vanilla.json")]
    with (out / "vanilla-cli.txt").open("w") as log:
        result = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise SystemExit(f"Upstream CLI failed ({result.returncode}); output retained in {out}")

    # Import the unmodified public loader/scorer only after validating the checkout.
    sys.path.insert(0, str(root))
    from benchmark.loader import load_all
    from benchmark.scorer import CHECKS, score_scenario
    from benchmark.types import Assertion, AuditEntry, RunOutcome

    scenarios = load_all(root / "scenarios")
    baseline = json.loads((out / "vanilla.json").read_text())
    expected_ids = sorted(s.id for s in scenarios)
    observed_ids = sorted(s["scenario_id"] for s in baseline["results"])
    if expected_ids != observed_ids or len(expected_ids) != 48:
        raise SystemExit("Incomplete/changed scenario coverage; do not report a comparable baseline")
    if any(s["llm_required"] for s in map(asdict, scenarios)):
        raise SystemExit("Scenario set unexpectedly requires an LLM")

    # These are counterexamples to interpreting a passing assertion as positive
    # evidence of enforcement, not modifications to official scenarios/scores.
    empty = RunOutcome()
    empty_passes = [s.id for s in scenarios if score_scenario(s, empty, "empty-probe", 0).passed]
    isolation = Assertion("no_cross_tenant_leak", {"allowed_pairs": [{"tenant": "tenant-a", "tool": "read_file"}]})
    missing_tenant = RunOutcome(audit_entries=[AuditEntry(
        timestamp="2026-09-27T00:00:00Z", tenant=None, actor_uid="user-b",
        actor_email=None, tool="read_file", decision="allow",
    )])
    sample = scenarios[0]
    checks = {
        "isolation_accepts_empty_audit": CHECKS[isolation.kind](isolation, empty, sample)[0],
        "isolation_accepts_missing_tenant": CHECKS[isolation.kind](isolation, missing_tenant, sample)[0],
        "rate_limit_accepts_zero_observed_attempts": CHECKS["rate_limited_count"](
            Assertion("rate_limited_count", {"max_allowed": 1}), empty, sample,
        )[0],
    }
    probe = {
        "purpose": "Scorer evidence-sufficiency audit; NOT a framework performance score",
        "unmodified_scenarios": len(scenarios),
        "empty_outcome_passing_scenarios": empty_passes,
        "assertion_counterexamples": checks,
        "interpretation": "A pass alone does not prove calls were attempted or tenant isolation was enforced.",
    }
    (out / "scorer-audit.json").write_text(json.dumps(probe, indent=2) + "\n")
    paths = git(root, "ls-files", "benchmark", "runners/vanilla.py", "runners/langgraph_native.py",
                "scenarios", "fixtures", "pyproject.toml", "LICENSE").splitlines()
    manifest = {
        "recorded_utc": datetime.now(timezone.utc).isoformat(),
        "source": "https://github.com/agentic-control-plane/agentgovbench",
        "commit": COMMIT,
        "upstream_tracked_tree_clean": True,
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "python": sys.version,
        "platform": platform.platform(),
        "packages": {p: importlib.metadata.version(p) for p in ("PyYAML", "requests", "click")},
        "command": command,
        "cwd": str(root),
        "coverage": {"expected": 48, "observed": len(observed_ids), "exact_id_match": True},
        "aggregate": baseline["aggregate"],
        "foundry_result": False,
        "paid_model_requests": 0,
        "vendor_service_requests": 0,
        "file_sha256": {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths},
        "artifacts_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.iterdir()) if p.is_file()},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"upstream_vanilla": baseline["aggregate"], "audit": probe,
                      "foundry_result": False, "output": str(out)}, indent=2))


if __name__ == "__main__":
    main()

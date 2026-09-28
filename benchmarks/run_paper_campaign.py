"""Re-run every paper experiment from a clean commit into one evidence folder.

    python benchmarks/run_paper_campaign.py --agentgovbench /path/to/agentgovbench \
        --out review/evidence/paper-campaign-YYYYMMDD

No paid model calls. Network is used only to fetch the two public datasets.
Refuses to run with modified tracked files, and refuses to reuse an output
directory, so earlier evidence cannot be overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ABLATIONS = ["no_attenuation", "no_chain", "no_rate_limit", "fail_mode_learned_only", "fail_open_always",
             "no_tenant_membership", "no_deny_overrides", "stale_policy", "no_audit", "no_scope_check_plane",
             "no_scope_check_both", "no_tier_rules", "anonymous_allowed", "deny_all", "allow_all"]
REPEATS = 10
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def sh(cmd: list[str], log: Path, **kw) -> None:
    with log.open("w") as fh:
        rc = subprocess.run(cmd, cwd=REPO, stdout=fh, stderr=subprocess.STDOUT, env=ENV, **kw).returncode
    if rc:
        raise SystemExit(f"{' '.join(cmd)} failed ({rc}); see {log}")


def capture(cmd: list[str], out: Path) -> None:
    with out.open("w") as fh:
        rc = subprocess.run(cmd, cwd=REPO, stdout=fh, stderr=subprocess.DEVNULL,
                            env=ENV).returncode
    if rc and out.stat().st_size == 0:
        raise SystemExit(f"{' '.join(cmd)} failed ({rc})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agentgovbench", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO, capture_output=True,
                      text=True).stdout.strip():
        raise SystemExit("commit or stash tracked changes first")
    out = args.out if args.out.is_absolute() else REPO / args.out
    if out.exists():
        raise SystemExit(f"{out} exists; choose a new directory")
    (out / "agentgovbench").mkdir(parents=True)
    py, up, agb = sys.executable, str(args.agentgovbench.resolve()), out / "agentgovbench"
    supp = str(REPO / "benchmarks/agentgovbench/supplemental/scenarios")
    run = [py, "benchmarks/agentgovbench/run.py", "--upstream", up, "--out", str(agb)]
    for i in range(1, REPEATS + 1):
        sh(run + ["--tag", f"rep{i:02d}-"], agb / f"rep{i:02d}.log")
    for runner in ("vanilla", "audit_only", "agent_foundry"):
        sh(run + ["--runner", runner], agb / f"{runner}.log")
        sh(run + ["--runner", runner, "--scenarios", supp, "--tag", "supp-"], agb / f"supp-{runner}.log")
    for a in ABLATIONS:
        sh(run + ["--ablate", a], agb / f"ablate-{a}.log")
        sh(run + ["--ablate", a, "--scenarios", supp, "--tag", "supp-"], agb / f"supp-ablate-{a}.log")
    for name in ("governance_overhead", "long_context", "security_hallucination", "control_plane_scaling",
                 "sharded_scaling"):
        capture([py, f"benchmarks/{name}.py"], out / f"{name}.json")
    (out / "runtime").mkdir()
    for i in range(1, 6):
        sh([py, "benchmarks/native_vs_langgraph.py"], out / "runtime" / f"run{i}.txt")
    sh([py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rf"], out / "pytest.txt")
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    files = sorted(p for p in out.rglob("*") if p.is_file())
    (out / "manifest.json").write_text(json.dumps({
        "foundry_commit": commit, "python": sys.version, "platform": platform.platform(),
        "agentgovbench_commit": subprocess.run(["git", "-C", up, "rev-parse", "HEAD"], capture_output=True,
                                               text=True).stdout.strip(),
        "files": [{"path": str(p.relative_to(out)), "bytes": p.stat().st_size,
                   "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]}, indent=1))
    print(f"wrote {len(files)} files to {out}")


if __name__ == "__main__":
    main()

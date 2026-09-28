"""Regenerate evidence, compile the manuscript, and package its sources.

    python research-paper/build_paper.py [--compiler /path/to/tectonic]

Steps: run build_evidence.py; check that every macro used in the sources is
defined; compile with Tectonic (BibTeX handled automatically); fail on
undefined references or citations, overfull boxes and missing glyphs; then
copy the PDF to AgentFoundry_Paper.pdf and write a source ZIP and a build
manifest with SHA-256 digests.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MAIN = "agent_foundry_paper"


def sources() -> list[Path]:
    files = [HERE / f"{MAIN}.tex", HERE / "neurips_2026.sty", HERE / "references.bib"]
    for folder in ("sections", "figures", "evidence"):
        files += sorted(p for p in (HERE / folder).iterdir() if p.suffix in (".tex",) and p.is_file())
    return files


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--compiler", default=shutil.which("tectonic") or "/private/tmp/agent-foundry-paper-tools/tectonic")
    args = ap.parse_args()
    subprocess.run([sys.executable, str(HERE / "build_evidence.py")], check=True)
    defined = set(re.findall(r"\\newcommand\{\\([A-Za-z]+)\}", (HERE / "evidence/numbers.tex").read_text()))
    local = {"code", "method", "gec", "na", "evpath", "tablerows"}
    text = "".join(p.read_text() for p in sources() if p.suffix == ".tex" and p.parent.name != "evidence")
    used = set(re.findall(r"\\([A-Z][A-Za-z]+)\{\}", text))
    missing = sorted(u for u in used - defined - local)
    if missing:
        raise SystemExit(f"undefined evidence macros: {missing}")
    build = HERE / "build"
    build.mkdir(exist_ok=True)
    subprocess.run([args.compiler, "--keep-logs", "--outdir", str(build), f"{MAIN}.tex"], cwd=HERE, check=True)
    log = (build / f"{MAIN}.log").read_text(errors="replace")
    problems = re.findall(r"^.*(?:undefined references|undefined citations|(?:Reference|Citation) .+ undefined|"
                          r"Overfull \\[hv]box|Missing character).*$", log, re.MULTILINE | re.IGNORECASE)
    blg = build / f"{MAIN}.blg"
    if blg.exists():
        problems += [l for l in blg.read_text(errors="replace").splitlines() if l.startswith(("Warning--I didn't find",))]
    if problems:
        raise SystemExit("resolve before packaging:\n" + "\n".join(problems))
    pdf = HERE / "AgentFoundry_Paper.pdf"
    shutil.copy(build / f"{MAIN}.pdf", pdf)
    bundle = HERE / "AgentFoundry_LaTeX_Source.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sources():
            zf.write(p, p.relative_to(HERE))
    manifest = {"pdf_sha256": hashlib.sha256(pdf.read_bytes()).hexdigest(),
                "source_zip_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
                "sources": {str(p.relative_to(HERE)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources()}}
    (HERE / "build_manifest.json").write_text(json.dumps(manifest, indent=1))
    pages = re.search(r"Output written on .*?\((\d+) pages", log)
    print(f"built {pdf.name} ({pages.group(1) if pages else '?'} pages)")


if __name__ == "__main__":
    main()

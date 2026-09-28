"""Finish two predeclared campaigns under the existing shared cap.

Connection failures remain finished scored attempts. Continuation runs only
unattempted tasks, never retries failed tasks, and preserves every stop summary.
This driver is bounded to six continuations per campaign and makes no tuning
decisions from results. It refuses any different source/task/settings manifest.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path('/private/tmp/agent-foundry-workbench/.venv/bin/python')
RUNNER = Path('/private/tmp/agent-foundry-confirmation-20260928/benchmarks/workbench/run.py')
RESULTS = ROOT / 'benchmarks/workbench/results'
STOPS = ROOT / 'review/evidence/confirmation-interruptions'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profiles', nargs='+', choices=['baseline', 'grounded'],
                        default=['baseline', 'grounded'])
    profiles = parser.parse_args().profiles
    if len(profiles) != len(set(profiles)):
        raise SystemExit('Each profile may be selected only once')
    STOPS.mkdir(exist_ok=True)
    for profile in profiles:
        preparation = profile
        name = f'confirmation-20260928-{profile}'
        output = RESULTS / name
        planned = json.loads((ROOT / f'review/evidence/confirmation-{preparation}-preparation/planned-manifest.json').read_text())
        args = [str(PYTHON), str(RUNNER), 'live', '--split', 'heldout', '--per-domain', '2',
                '--heldout-per-domain', '10', '--heldout-offset-per-domain', '10',
                '--instruction-profile', profile, '--providers', 'openai', '--max-spend-usd', '25',
                '--ledger', str(RESULTS / 'budget-ledger.json'), '--output', str(output)]
        previous_count = -1
        for continuation in range(6):
            summary_path = output / 'summary.json'
            if summary_path.exists():
                summary = json.loads(summary_path.read_text())
                if summary['complete']:
                    break
                if summary.get('stop_reason') not in ('RemoteProtocolError', 'ReadTimeout'):
                    raise SystemExit(f'{name}: stopping on {summary.get("stop_reason")}')
                if summary['rows_finished'] <= previous_count:
                    raise SystemExit('No new attempt progress; refusing another continuation')
                previous_count = summary['rows_finished']
                stop_path = STOPS / f'{name}-{previous_count:03d}.json'
                data = summary_path.read_bytes()
                if stop_path.exists() and stop_path.read_bytes() != data:
                    raise SystemExit('Stop evidence already exists with different bytes')
                stop_path.write_bytes(data)
            manifest_path = output / 'manifest.json'
            if manifest_path.exists() and json.loads(manifest_path.read_text()) != planned:
                raise SystemExit('Source/config/task manifest differs from predeclared plan')
            log_path = RESULTS / f'{name}-continuation-{continuation:02d}.log'
            if log_path.exists():
                # Earlier driver invocation already produced this segment.
                # Keep its log and continue with the next unused identifier.
                previous_count = -1
                continue
            print(json.dumps({'campaign':name,'continuation':continuation,'started_utc':datetime.now(timezone.utc).isoformat()}),flush=True)
            with log_path.open('w') as stream:
                subprocess.run(args, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT, check=True)
            if json.loads((output/'manifest.json').read_text()) != planned:
                raise SystemExit('Executed manifest differed from plan')
        summary = json.loads((output/'summary.json').read_text())
        subprocess.run([sys.executable, str(ROOT/'review/evidence/export_workbench_evidence.py'), name],cwd=ROOT,check=True)
        print(json.dumps({'campaign':name,'complete':summary['complete'],'finished':summary['rows_finished'],
                          'summary_sha256':hashlib.sha256((output/'summary.json').read_bytes()).hexdigest()}),flush=True)
        if not summary['complete']:
            raise SystemExit('Continuation allowance exhausted; partial experiment archived without a complete-result claim')


if __name__ == '__main__':
    main()

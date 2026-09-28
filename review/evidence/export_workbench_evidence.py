"""Archive completed WorkBench experiments after scanning their actual bytes.

Run from the Foundry root. Raw attempts retain failed runs and original bytes in
an archive; manifests/summaries remain directly readable. Never reads .env into
an output artifact. Existing artifacts are immutable.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_once(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f'Refusing to overwrite evidence: {path.name}')
        return
    path.write_bytes(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('campaign')
    parser.add_argument('--output', default='review/evidence/live-workbench-20260928')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    root = repo/'benchmarks/workbench/results'/args.campaign
    if root.parent != repo/'benchmarks/workbench/results' or not (root/'summary.json').exists():
        raise ValueError('Choose one finished local campaign directory')
    files = [root/name for name in ('manifest.json', 'summary.json', 'execution.json', 'environment.txt')]
    files += sorted((root/'attempts').glob('*.json'))
    content = {str(p.relative_to(root)): p.read_bytes() for p in files}
    # Actual credentials only in memory, never in diagnostics or archive metadata.
    secrets = []
    for line in (repo/'benchmarks/workbench/.env').read_text().splitlines():
        if line.startswith(('OPENAI_API_KEY=', 'ANTHROPIC_API_KEY=')):
            secret = line.split('=', 1)[1].strip().strip('\'"').encode()
            if len(secret) > 8:
                secrets.append(secret)
    if not secrets:
        raise ValueError('Credential scan requires the local credential source')
    for name, data in content.items():
        if any(secret in data for secret in secrets) or re.search(rb'\bsk-(?:proj-|ant-)[A-Za-z0-9_-]{15,}', data):
            raise ValueError(f'Credential-like material detected; nothing exported: {name}')
    rows = [json.loads(data) for name, data in content.items() if name.startswith('attempts/')]
    if any(row['status'] != 'finished' for row in rows):
        raise ValueError('An attempt is still running; wait before archiving')
    summary = json.loads(content['summary.json'])
    if len(rows) != summary['rows_finished']:
        raise ValueError('Attempt coverage differs from summary')
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.GNU_FORMAT) as archive:
        for name, data in sorted(content.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            archive.addfile(info, io.BytesIO(data))
    compressed = gzip.compress(buffer.getvalue(), mtime=0)
    destination = repo/args.output/args.campaign
    index = {'campaign': args.campaign, 'files': {name: {'sha256': sha(data), 'bytes': len(data)} for name, data in content.items()},
             'archive_sha256': sha(compressed), 'archive_bytes': len(compressed), 'finished_attempts': len(rows),
             'complete': summary['complete'], 'known_credential_matches': 0,
             'credential_pattern_matches': 0, 'archive_format': 'gzip-compressed tar; original file bytes retained',
             'scope': 'Public synthetic WorkBench tasks; provider response items and usage; no request headers'}
    for name in ('manifest.json', 'summary.json', 'execution.json', 'environment.txt'):
        write_once(destination/name, content[name])
    write_once(destination/'attempts-and-provenance.tar.gz', compressed)
    write_once(destination/'archive-index.json', (json.dumps(index, indent=2, sort_keys=True)+'\n').encode())
    print(json.dumps({'campaign': args.campaign, 'attempts': len(rows), 'archive_bytes': len(compressed), 'credential_matches': 0}))


if __name__ == '__main__':
    main()

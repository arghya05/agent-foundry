"""Scan Git-index bytes and nested archives without exposing local credentials.

Run after staging the intended release. This check does not publish anything.
"""
from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / 'review/evidence/release-checks-20260928.json'


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def main():
    secret_file = ROOT / 'benchmarks/workbench/.env'
    secrets = [line.split('=', 1)[1].strip().strip('\'"').encode()
               for line in secret_file.read_text().splitlines()
               if line.startswith(('OPENAI_API_KEY=', 'ANTHROPIC_API_KEY='))]
    if not secrets or any(len(value) < 10 for value in secrets):
        raise SystemExit('Local credential source unavailable for release scan')
    paths = [p.decode() for p in git('ls-files', '-z').split(b'\0') if p]
    if any(Path(p).name == '.env' for p in paths):
        raise SystemExit('A credential filename is staged; release blocked')
    if secret_file.stat().st_mode & 0o777 != 0o600:
        raise SystemExit('Local credential file must remain mode 0600')
    findings = []
    scanned = 0

    def scan(name, data, depth=0):
        nonlocal scanned
        scanned += 1
        if any(value in data for value in secrets) or re.search(rb'\bsk-(?:proj-|ant-)[A-Za-z0-9_-]{15,}', data):
            findings.append(name)
        if depth > 4:
            raise SystemExit('Unexpected nested archive depth')
        if data.startswith(b'\x1f\x8b'):
            raw = gzip.decompress(data)
            scan(name+'::gzip', raw, depth+1)
        elif zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for member in archive.infolist():
                    if not member.is_dir():
                        scan(name+'::'+member.filename, archive.read(member), depth+1)
        elif len(data) > 262 and data[257:262] == b'ustar':
            with tarfile.open(fileobj=io.BytesIO(data), mode='r:') as archive:
                for member in archive.getmembers():
                    if member.isfile():
                        scan(name+'::'+member.name, archive.extractfile(member).read(), depth+1)

    hashes = {}
    for path in paths:
        data = git('show', ':'+path)
        hashes[path] = hashlib.sha256(data).hexdigest()
        scan(path, data)
    distributions = list(Path('/private/tmp/agent-foundry-release-dist').glob('*'))
    for path in distributions:
        if path.is_file():
            scan(path.name, path.read_bytes())
    if findings:
        raise SystemExit('Credential-like material detected in these files (values suppressed): '+', '.join(findings))
    subprocess.run(['git', 'diff', '--cached', '--check'], cwd=ROOT, check=True)
    result = {'recorded_utc':datetime.now(timezone.utc).isoformat(), 'index_files_scanned':len(paths),
              'contents_scanned_including_archive_members':scanned, 'credential_matches':0,
              'credential_file_mode':'0600', 'credential_file_staged':False,
              'distributions_scanned':[p.name for p in distributions],
              'index_content_manifest_sha256':hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest(),
              'scope':'Exact staged bytes and nested archives; report itself added after scan; paper draft excluded',
              'whitespace_policy':'Original txt/log/csv evidence bytes preserved via .gitattributes; code and Markdown checked',
              'boundary':'Known credentials and selected credential patterns; not a universal sensitive-data classifier'}
    REPORT.write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='index_content_manifest_sha256'}))


if __name__ == '__main__':
    main()

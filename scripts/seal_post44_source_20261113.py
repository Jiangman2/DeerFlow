"""Seal the explicit local synchronization set; no application or network calls."""
from pathlib import Path
import ast
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = 'release/post44-source-sync-20261113/'
ENERGY = 'skills/public/energy-market-daily/'


def row(path):
    raw = path.read_bytes()
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def bind_historical_fixtures():
    for path in (ROOT / 'tests').glob('test_energy*.py'):
        raw = path.read_bytes()
        updated = raw
        for name in ('BASE', 'REPO'):
            for quote in ('"', "'"):
                before = (name + ' / ' + quote + ENERGY.rstrip('/')).encode()
                after = (name + ' / ' + quote + ARCHIVE + 'parent/' + ENERGY.rstrip('/')).encode()
                updated = updated.replace(before, after)
        if updated != raw:
            previous = ROOT / ARCHIVE / 'parent' / path.relative_to(ROOT)
            previous.parent.mkdir(parents=True, exist_ok=True)
            if not previous.exists():
                previous.write_bytes(raw)
            path.write_bytes(updated)


def main():
    bind_historical_fixtures()
    assembly = json.loads((ROOT / ARCHIVE / 'ASSEMBLY.json').read_bytes())
    gateway = {path: row(ROOT / path) for path in assembly['gateway_changes']}
    energy = {path.relative_to(ROOT).as_posix(): row(path) for path in (ROOT / ENERGY).rglob('*')
              if path.is_file() and '__pycache__' not in path.parts}
    parents = {path.relative_to(ROOT / ARCHIVE / 'parent').as_posix():
               dict(row(path), snapshot=path.relative_to(ROOT).as_posix())
               for path in (ROOT / ARCHIVE / 'parent').rglob('*') if path.is_file()}
    for path in gateway:
        parents.setdefault(path, None)
    support = {}
    for name in assembly['copied_files']:
        if name not in gateway and name not in energy:
            support[name] = row(ROOT / name)
    for path in (ROOT / ARCHIVE / 'deployed-overlay').rglob('*.py'):
        support[path.relative_to(ROOT).as_posix()] = row(path)
    for directory in ('qualification-fixtures', 'intermediate-energy-20261111'):
        for path in (ROOT / ARCHIVE / directory).rglob('*'):
            if path.is_file():
                support[path.relative_to(ROOT).as_posix()] = row(path)
    support[ARCHIVE + 'ASSEMBLY.json'] = row(ROOT / ARCHIVE / 'ASSEMBLY.json')
    # Formatting is recorded independently of the exact production overlay bytes.
    proofs = {}
    for name in gateway:
        before = (ROOT / ARCHIVE / 'deployed-overlay' / name).read_bytes()
        after = (ROOT / name).read_bytes()
        assert ast.dump(ast.parse(before)) == ast.dump(ast.parse(after)), name
        proofs[name] = {'ast_identical_to_deployed_overlay': True,
                        'byte_identical_to_deployed_overlay': before == after}
    manifest = {'schema': 'post44-source-reconciliation.v1', 'base_commit': assembly['base_commit'],
                'last_sync_merge': assembly['last_sync_merge'], 'energy_files': energy,
                'gateway_sources': gateway, 'parent_files': parents,
                'original_energy_files': assembly['original_energy_paths'], 'support_files': support,
                'gateway_format_proofs': proofs, 'production_commands_executed': False,
                'credentials_included': False, 'scheduler_budget_candidate': 10000,
                'scheduler_budget_deployed': False, 'business_acceptance_pending': True}
    raw = (json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2) + '\n').encode()
    output = ROOT / ARCHIVE / 'SOURCE.json'
    output.write_bytes(raw)
    verifier = ROOT / 'deploy/enterprise/post44_source_20261113.py'
    text = verifier.read_text(encoding='utf-8')
    import re
    text, count = re.subn(r"MANIFEST_SHA256 = '[^']+'", "MANIFEST_SHA256 = '" + hashlib.sha256(raw).hexdigest() + "'", text)
    assert count == 1
    verifier.write_text(text, encoding='utf-8', newline='\n')
    print(json.dumps({'energy_files': len(energy), 'gateway_files': len(gateway), 'support_files': len(support),
                      'parent_files': len(parents), 'manifest_sha256': hashlib.sha256(raw).hexdigest()}, sort_keys=True))


if __name__ == '__main__':
    main()

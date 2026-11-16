"""Deterministically seal the reviewed 2.1.1 code and Skill-only installer.

No external process, network request, credential read or production operation.
Existing release files are accepted only when their bytes are identical.
"""
import ast
import base64
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CANDIDATE = REPO / 'portable-skills/candidates/energy-market-daily-2.1.1'
RELEASE = REPO / 'release/energy-daily-freight-links-20261114-r1'
MANIFEST_SHA = 'e7e66e6e00e7b0fc64e1ccec098c09b2eedf23b3b1ee89687496d28a651b79a9'
VERSION = '2.1.1-portable'
CODE_COUNT = 107
INSTALLED_COUNT = 108


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def need(ok, code):
    if not ok:
        raise ValueError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def replace_once(raw, old, new):
    need(raw.count(old) == 1, 'TEMPLATE_MARKER_COUNT:' + old.decode('ascii'))
    return raw.replace(old, new, 1)


def source_files():
    need(re.fullmatch('[0-9a-f]{64}', MANIFEST_SHA) and type(CODE_COUNT) is int and CODE_COUNT > 106
         and INSTALLED_COUNT == CODE_COUNT + 1, 'CANDIDATE_NOT_FROZEN')
    files = {}
    for path in sorted(CANDIDATE.rglob('*')):
        need(not path.is_symlink(), 'CANDIDATE_LINK_FORBIDDEN')
        if path.is_file():
            name = path.relative_to(CANDIDATE).as_posix()
            need(not set(path.relative_to(CANDIDATE).parts) & {'.private', '.venv', '__pycache__'} and not name.endswith('.pyc'), 'CANDIDATE_UNEXPECTED_FILE')
            raw = path.read_bytes()
            need(len(raw) <= 1048576, 'CANDIDATE_FILE_BOUND')
            files[name] = raw
    need(len(files) == CODE_COUNT and 'MANIFEST.json' in files and 'assets/browser-runtime.json' not in files, 'PUBLIC_CODE_SOURCE_COUNT_OR_BINDING')
    need(sha(files['MANIFEST.json']) == MANIFEST_SHA, 'FROZEN_CANDIDATE_MANIFEST_CHANGED')
    manifest = json.loads(files['MANIFEST.json'])
    need(manifest.get('schema') == 'energy-daily-portable-files.v1' and manifest.get('version') == VERSION, 'CANDIDATE_VERSION_CHANGED')
    actual = {name: {'bytes': len(raw), 'sha256': sha(raw)} for name, raw in files.items() if name != 'MANIFEST.json'}
    need(manifest.get('files') == actual, 'FROZEN_CANDIDATE_CONTENT_CHANGED')
    return files


def package(files):
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, raw in sorted(files.items()):
            item = zipfile.ZipInfo('energy-market-daily/' + name, (1980, 1, 1, 0, 0, 0))
            item.create_system = 3
            item.external_attr = 0o100644 << 16
            item.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(item, raw, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return result.getvalue()


def build():
    files = source_files()
    code = package(files)
    digest = sha(code)
    fixtures = {name: (REPO / 'portable-skills/tests' / name).read_bytes()
                for name in ('test_energy_market_daily.py', 'fixtures/approved.json')}
    probe = (HERE / 'compose_probe.py').read_bytes()
    need(b'\r\n' not in probe, 'PROBE_LF_REQUIRED')
    compile(probe, '<fixed-compose-probe>', 'exec')
    publisher = (HERE / 'publisher.py').read_bytes()
    publisher = replace_once(publisher, b'OLD_COUNT, CODE_COUNT, NEW_COUNT = 108, 0, 0',
                             ('OLD_COUNT, CODE_COUNT, NEW_COUNT = 108, %d, %d' % (CODE_COUNT, INSTALLED_COUNT)).encode('ascii'))
    verifier = (HERE / 'verify_child.py').read_bytes()
    verifier = replace_once(verifier, b'CANDIDATE_FILES = 0', ('CANDIDATE_FILES = %d' % INSTALLED_COUNT).encode('ascii'))
    verifier = replace_once(verifier, b'FINAL_TEXT_PROBE = \'\'', b'FINAL_TEXT_PROBE = ' + repr(probe.decode('utf-8')).encode('utf-8'))
    verifier = verifier.replace(b'UNSEALED_FINAL_TEXT_PROBE_SHA', sha(probe).encode('ascii'))
    verifier = replace_once(verifier, b'FIXTURES = {}', b'FIXTURES = ' + repr({name: sha(raw) for name, raw in fixtures.items()}).encode('ascii'))
    publisher = publisher.replace(b'UNSEALED_CANDIDATE_VERSION', VERSION.encode('ascii')).replace(b'UNSEALED_PACKAGE_SHA', digest.encode('ascii'))
    verifier = verifier.replace(b'UNSEALED_CANDIDATE_VERSION', VERSION.encode('ascii')).replace(b'UNSEALED_PACKAGE_SHA', digest.encode('ascii'))
    fixture_bytes = canonical({name: base64.b64encode(raw).decode('ascii') for name, raw in fixtures.items()})
    raw_assets = {'publisher': publisher, 'verifier': verifier, 'package': code, 'test_files': fixture_bytes}
    assets = {name: {'base64': base64.b64encode(raw).decode('ascii'), 'sha256': sha(raw)} for name, raw in raw_assets.items()}
    host = (HERE / 'operator.py').read_bytes()
    host = replace_once(host, b'CANDIDATE_FILES = 0', ('CANDIDATE_FILES = %d' % INSTALLED_COUNT).encode('ascii'))
    host = replace_once(host, b'EXPECTED_SKILL_COUNT = 0', ('EXPECTED_SKILL_COUNT = %d' % INSTALLED_COUNT).encode('ascii'))
    host = host.replace(b'UNSEALED_CANDIDATE_VERSION', VERSION.encode('ascii')).replace(b'UNSEALED_PACKAGE_SHA', digest.encode('ascii'))
    host = replace_once(host, b'ASSETS = {}', b'ASSETS = ' + repr(assets).encode('ascii'))
    for name, raw in (('publisher', publisher), ('verifier', verifier), ('operator', host)):
        need(b'UNSEALED_' not in raw, 'ASSET_NOT_FULLY_SEALED:' + name)
        compile(raw, '<sealed-' + name + '>', 'exec')
    ast.parse(host.decode('utf-8'), feature_version=(3, 7))
    rows = [{'path': name, 'bytes': len(raw), 'sha256': sha(raw)} for name, raw in sorted(files.items())]
    output = {'energy-market-daily-2.1.1-public-code.zip': code,
              'install-energy-freight-links-20261114-r1.py': host,
              'publisher.sealed.py': publisher, 'verify_child.sealed.py': verifier}
    metadata = {'schema': 'energy-freight-links-install-package.v1', 'version': VERSION,
                'candidate_manifest_sha256': MANIFEST_SHA, 'code_files': CODE_COUNT,
                'installed_files_including_binding': INSTALLED_COUNT, 'code_tree_sha256': sha(canonical(rows)),
                'accepted_old_tree_sha256': '8ac3ca5df517d5ed269593d7c9e1216cfc758bf76b3367a40e4d05f372df7ccc',
                'compose_probe_sha256': sha(probe),
                'assets': {name: {'bytes': len(raw), 'sha256': sha(raw)} for name, raw in raw_assets.items()},
                'files': {name: {'bytes': len(raw), 'sha256': sha(raw)} for name, raw in output.items()},
                'source_inputs': {name: {'bytes': (HERE / name).stat().st_size, 'sha256': sha((HERE / name).read_bytes())}
                                  for name in ('operator.py', 'publisher.py', 'verify_child.py', 'compose_probe.py', 'seal.py')},
                'production_executed': False, 'credentials_included': False, 'runtime_binaries_included': False,
                'enterprise_binding_in_public_code': False, 'runtime_replacement_requested': False,
                'gateway_modified': False, 'provisioner_modified': False, 'messages_sent': 0, 'provider_requests': 0,
                'deployment_guard': 'CURRENT_PROTECTED_UIDS_AND_UNCHANGED_SPEC_DURING_SKILL_UPDATE',
                'real_arm64_uid1000_render_and_synthetic_final_text_qualification_required_before_publication': True}
    output['SOURCE.json'] = (json.dumps(metadata, ensure_ascii=True, sort_keys=True, indent=2) + '\n').encode('ascii')
    sums = ''.join(sha(raw) + '  ' + name + '\n' for name, raw in sorted(output.items()))
    output['SHA256SUMS.txt'] = sums.encode('ascii')
    return output, metadata


def main():
    output, metadata = build()
    RELEASE.mkdir(parents=True, exist_ok=True)
    for name, raw in output.items():
        destination = RELEASE / name
        if destination.exists():
            need(destination.is_file() and not destination.is_symlink() and destination.read_bytes() == raw, 'EXISTING_RELEASE_DIFFERS:' + name)
        else:
            with destination.open('xb') as stream:
                stream.write(raw)
    print(json.dumps({'release': str(RELEASE), 'version': VERSION, 'files': metadata['files']}, sort_keys=True))


if __name__ == '__main__':
    main()

"""Deterministically seal the reviewed 2.1.3 code and Skill-only installer.

No external process, network request, credential read or production operation.
Existing release files are accepted only when their bytes are identical.
"""
import argparse
import ast
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import zipfile


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CANDIDATE = REPO / 'portable-skills/candidates/energy-market-daily-2.1.5'
RELEASE = REPO / 'release/energy-daily-html-reader-skill-20261114-r1'
VERSION = '2.1.5-portable'
CODE_COUNT = 107
INSTALLED_COUNT = 108
EXCLUDED_LOCAL_FILES = {'MANIFEST.json', 'CANDIDATE.md'}
SOURCE_REVIEW = HERE / 'REVIEWED-SOURCE.json'
OFFLINE_SUITES = {
    'test_energy_reader_delivery_20261114.py': ['ProgrammaticBuildTests', 'BrowserPipelineTests', 'HtmlLinkTests'],
    'test_energy_reader_news_stage_20261114.py': ['NewsStageReceiptTests'],
}
OFFLINE_TEST_COUNT = 34
FIXTURE_NAMES = ('test_energy_market_daily.py', 'test_energy_reader_delivery_20261114.py',
                 'test_energy_reader_news_stage_20261114.py',
                 'fixtures/approved.json', 'fixtures/original_renderer.py',
                 'fixtures/dxy-source.txt', 'fixtures/cny-source.txt')
PRIOR_SOURCE_NAMES = ('common.py','market.py','research.py','report_renderer.py','browser_runtime.py','daily.py')


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
    need(SOURCE_REVIEW.is_file(), 'CANDIDATE_SOURCE_REVIEW_REQUIRED')
    review = json.loads(SOURCE_REVIEW.read_text(encoding='utf-8-sig'))
    need(review.get('schema') == 'energy-programmatic-reviewed-source.v1'
         and review.get('version') == VERSION and type(CODE_COUNT) is int and CODE_COUNT > 106
         and INSTALLED_COUNT == CODE_COUNT + 1
         and review.get('offline_suites') == OFFLINE_SUITES
         and review.get('offline_test_count') == OFFLINE_TEST_COUNT, 'CANDIDATE_SOURCE_REVIEW_REQUIRED')
    files = {}
    for path in sorted(CANDIDATE.rglob('*')):
        need(not path.is_symlink(), 'CANDIDATE_LINK_FORBIDDEN')
        if path.is_file():
            name = path.relative_to(CANDIDATE).as_posix()
            if name in EXCLUDED_LOCAL_FILES:
                continue
            need(not set(path.relative_to(CANDIDATE).parts) & {'.private', '.venv', '__pycache__'} and not name.endswith('.pyc'), 'CANDIDATE_UNEXPECTED_FILE')
            raw = path.read_bytes()
            need(len(raw) <= 1048576, 'CANDIDATE_FILE_BOUND')
            files[name] = raw
    need(len(files) == CODE_COUNT - 1 and 'assets/browser-runtime.json' not in files, 'PUBLIC_CODE_SOURCE_COUNT_OR_BINDING')
    actual = {name: {'bytes': len(raw), 'sha256': sha(raw)} for name, raw in files.items()}
    need(review.get('files') == actual, 'REVIEWED_CANDIDATE_SOURCE_CHANGED')
    preservation = json.loads((HERE / 'CORE-SOURCE-PRESERVATION.json').read_text(encoding='utf-8-sig'))
    for name, row in preservation['files'].items():
        need(sha(files['scripts/' + name]) == row['prior_sha256'] == row['candidate_sha256'], 'CORE_SOURCE_CHANGED')
    manifest = {'schema':'energy-daily-portable-files.v1','version':VERSION,'files':actual}
    files['MANIFEST.json'] = (json.dumps(manifest,ensure_ascii=False,indent=2) + '\n').encode('utf-8')
    return files


def replace_assignment(raw, name, value):
    text = raw.decode('utf-8')
    matches = [node for node in ast.parse(text).body if isinstance(node,ast.Assign)
               and any(isinstance(target,ast.Name) and target.id==name for target in node.targets)]
    need(len(matches)==1,'TEMPLATE_ASSIGNMENT_COUNT:'+name)
    node = matches[0]
    lines = text.splitlines(keepends=True)
    lines[node.lineno-1:node.end_lineno] = [name + ' = ' + repr(value) + '\n']
    return ''.join(lines).encode('utf-8')


def gateway_receipt(path):
    """Validate a supplied copy of the completed operator-owned acceptance.

    The generated installer later requires exactly these bytes at the fixed
    root-owned server path and reconciles the live Gateway identity again.
    """
    need(path is not None,'GATEWAY_RUNTIME_ACCEPTANCE_REQUIRED')
    path = Path(path).absolute()
    for part in (path,)+tuple(path.parents):
        need(not part.is_symlink(),'GATEWAY_RECEIPT_LINK_FORBIDDEN')
    need(path.is_file(),'GATEWAY_RUNTIME_ACCEPTANCE_REQUIRED')
    before = path.stat()
    need(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and 0<before.st_size<=4194304,'GATEWAY_RECEIPT_FILE_BOUND')
    raw = path.read_bytes()
    after = path.stat()
    need(len(raw)==before.st_size and (before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns),
         'GATEWAY_RECEIPT_CHANGED_DURING_READ')
    def unique(pairs):
        result = {}
        for key,value in pairs:
            need(key not in result,'GATEWAY_RECEIPT_DUPLICATE_KEY')
            result[key]=value
        return result
    report = json.loads(raw.decode('utf-8-sig'),object_pairs_hook=unique)
    need(isinstance(report,dict),'GATEWAY_RUNTIME_ACCEPTANCE_INVALID')
    scope = {'__name__':'local_context_receipt_validation'}
    exec(compile((HERE / 'operator.py').read_bytes(),'<context-install-template>','exec'),scope)
    # First successful terminal output may add only this metadata object after
    # saving. Rebuild the saved canonical bytes only if its own exact receipt
    # metadata proves equality; later repeated checks are not interchangeable.
    input_basis = 'EXACT_SAVED_RECEIPT_FILE'
    if 'runtime_acceptance_receipt' in report:
        metadata = report['runtime_acceptance_receipt']
        need(isinstance(metadata,dict) and set(metadata)=={
                 'path','bytes','sha256','existing_acceptance_reused',
                 'stdout_without_receipt_metadata_matches_saved'}
             and metadata['path']==scope['GATEWAY_CONTEXT_RECEIPT']['path']
             and type(metadata['bytes']) is int and 0<metadata['bytes']<=4194304
             and isinstance(metadata['sha256'],str) and re.fullmatch('[0-9a-f]{64}',metadata['sha256']),
             'GATEWAY_PRINTED_RECEIPT_METADATA_INVALID')
        need(metadata['existing_acceptance_reused'] is False
             and metadata['stdout_without_receipt_metadata_matches_saved'] is True,
             'GATEWAY_REPEATED_STDOUT_IS_NOT_SAVED_RECEIPT')
        report = dict(report)
        report.pop('runtime_acceptance_receipt')
        reconstructed = canonical(report)
        need(len(reconstructed)==metadata['bytes'] and sha(reconstructed)==metadata['sha256'],
             'GATEWAY_PRINTED_RECEIPT_RECONSTRUCTION_MISMATCH')
        raw = reconstructed
        input_basis = 'TERMINAL_JSON_RECONSTRUCTED_AND_EXACT_RECEIPT_HASH_VERIFIED'
    contract = dict(scope['GATEWAY_CONTEXT_RECEIPT'],bytes=len(raw),sha256=sha(raw),operation=report.get('operation'))
    scope['GATEWAY_CONTEXT_RECEIPT']=contract
    scope['read']=lambda fixed: raw if fixed.as_posix()==contract['path'] else (_ for _ in ()).throw(ValueError('FIXED_RECEIPT_ONLY'))
    binding=scope['gateway_context_binding']()
    binding['local_input_basis']=input_basis
    need(report.get('context_injection_qualification_basis')=='ISOLATED_SOURCE_FUNCTIONS_AND_TRANSPORT_PREFIX_WITH_SYNTHETIC_TASK_ID'
         and report.get('loaded_application_modules_verified') is False
         and report.get('business_acceptance_pending') is True
         and isinstance(report.get('verified_fleet_sha256'),str) and re.fullmatch('[0-9a-f]{64}',report['verified_fleet_sha256']),
         'GATEWAY_RUNTIME_ACCEPTANCE_EVIDENCE_INCOMPLETE')
    return contract,binding


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


def build(receipt_path=None):
    contract,binding = gateway_receipt(receipt_path)
    files = source_files()
    code = package(files)
    digest = sha(code)
    fixtures = {name: (REPO / 'portable-skills/tests' / name).read_bytes() for name in FIXTURE_NAMES}
    for name in PRIOR_SOURCE_NAMES:
        fixtures['published-2.1.2/scripts/' + name] = (REPO / 'portable-skills/candidates/energy-market-daily-2.1.2/scripts' / name).read_bytes()
    # The reviewed source inventory freezes the fixtures too. Changing a test
    # count or its code after review cannot silently weaken on-cluster gates.
    review = json.loads(SOURCE_REVIEW.read_text(encoding='utf-8-sig'))
    need(review.get('fixtures') == {name: {'bytes':len(raw),'sha256':sha(raw)} for name,raw in fixtures.items()},
         'REVIEWED_FIXTURES_CHANGED')
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
    verifier = replace_once(verifier,b'OFFLINE_SUITES = {}',b'OFFLINE_SUITES = '+repr(OFFLINE_SUITES).encode('ascii'))
    for name in ('publisher','verifier'):
        raw = publisher if name == 'publisher' else verifier
        raw = replace_once(raw,b'OFFLINE_TEST_COUNT = 0',('OFFLINE_TEST_COUNT = %d' % OFFLINE_TEST_COUNT).encode('ascii'))
        if name == 'publisher': publisher = raw
        else: verifier = raw
    publisher = publisher.replace(b'UNSEALED_CANDIDATE_VERSION', VERSION.encode('ascii')).replace(b'UNSEALED_PACKAGE_SHA', digest.encode('ascii'))
    verifier = verifier.replace(b'UNSEALED_CANDIDATE_VERSION', VERSION.encode('ascii')).replace(b'UNSEALED_PACKAGE_SHA', digest.encode('ascii'))
    fixture_bytes = canonical({name: base64.b64encode(raw).decode('ascii') for name, raw in fixtures.items()})
    raw_assets = {'publisher': publisher, 'verifier': verifier, 'package': code, 'test_files': fixture_bytes}
    assets = {name: {'base64': base64.b64encode(raw).decode('ascii'), 'sha256': sha(raw)} for name, raw in raw_assets.items()}
    host = (HERE / 'operator.py').read_bytes()
    host = replace_assignment(host,'GATEWAY_CONTEXT_RECEIPT',contract)
    host = replace_once(host, b'CANDIDATE_FILES = 0', ('CANDIDATE_FILES = %d' % INSTALLED_COUNT).encode('ascii'))
    host = replace_once(host, b'EXPECTED_SKILL_COUNT = 0', ('EXPECTED_SKILL_COUNT = %d' % INSTALLED_COUNT).encode('ascii'))
    host = replace_once(host,b'OFFLINE_TEST_COUNT = 0',('OFFLINE_TEST_COUNT = %d' % OFFLINE_TEST_COUNT).encode('ascii'))
    host = host.replace(b'UNSEALED_CANDIDATE_VERSION', VERSION.encode('ascii')).replace(b'UNSEALED_PACKAGE_SHA', digest.encode('ascii'))
    host = replace_once(host, b'ASSETS = {}', b'ASSETS = ' + repr(assets).encode('ascii'))
    for name, raw in (('publisher', publisher), ('verifier', verifier), ('operator', host)):
        need(b'UNSEALED_' not in raw, 'ASSET_NOT_FULLY_SEALED:' + name)
        compile(raw, '<sealed-' + name + '>', 'exec')
    ast.parse(host.decode('utf-8'), feature_version=(3, 7))
    rows = [{'path': name, 'bytes': len(raw), 'sha256': sha(raw)} for name, raw in sorted(files.items())]
    output = {'energy-market-daily-2.1.5-public-code.zip': code,
              'install-energy-html-reader-skill-20261114-r1.py': host,
              'publisher.sealed.py': publisher, 'verify_child.sealed.py': verifier,
              'CODE-MANIFEST.json':files['MANIFEST.json']}
    metadata = {'schema': 'energy-programmatic-install-package.v1', 'version': VERSION,
                'candidate_manifest_sha256': sha(files['MANIFEST.json']), 'code_files': CODE_COUNT,
                'installed_files_including_binding': INSTALLED_COUNT, 'code_tree_sha256': sha(canonical(rows)),
                'accepted_old_tree_sha256': '2a09015d31cee41cd2a329fff96062848255d3d49fb7cec8c52d47c939345259',
                'accepted_gateway_runtime_receipt':binding,
                'gateway_receipt_contract':contract,
                'compose_probe_sha256': sha(probe),
                'offline_suites':OFFLINE_SUITES,'offline_test_count':OFFLINE_TEST_COUNT,
                'assets': {name: {'bytes': len(raw), 'sha256': sha(raw)} for name, raw in raw_assets.items()},
                'files': {name: {'bytes': len(raw), 'sha256': sha(raw)} for name, raw in output.items()},
                'source_inputs': {name: {'bytes': (HERE / name).stat().st_size, 'sha256': sha((HERE / name).read_bytes())}
                                  for name in ('operator.py', 'publisher.py', 'verify_child.py', 'compose_probe.py', 'seal.py', 'REVIEWED-SOURCE.json', 'CORE-SOURCE-PRESERVATION.json')},
                'production_executed': False, 'credentials_included': False, 'runtime_binaries_included': False,
                'enterprise_binding_in_public_code': False, 'runtime_replacement_requested': False,
                'gateway_modified': False, 'provisioner_modified': False, 'messages_sent': 0, 'provider_requests': 0,
                'deployment_guard': 'ACCEPTED_GATEWAY_CONTEXT_RECEIPT_AND_UNCHANGED_CURRENT_PROTECTED_SPECS',
                'real_arm64_uid1000_render_and_synthetic_final_text_qualification_required_before_publication': True}
    output['SOURCE.json'] = (json.dumps(metadata, ensure_ascii=True, sort_keys=True, indent=2) + '\n').encode('ascii')
    sums = ''.join(sha(raw) + '  ' + name + '\n' for name, raw in sorted(output.items()))
    output['SHA256SUMS.txt'] = sums.encode('ascii')
    return output, metadata


def write_output(output, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, raw in output.items():
        destination = directory / name
        if destination.exists():
            need(destination.is_file() and not destination.is_symlink() and destination.read_bytes() == raw, 'EXISTING_RELEASE_DIFFERS:' + name)
        else:
            with destination.open('xb') as stream:
                stream.write(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gateway-receipt',required=True,help='Downloaded exact RUNTIME-ACCEPTANCE.json from the completed Gateway verification')
    args = parser.parse_args()
    output, metadata = build(args.gateway_receipt)
    write_output(output,RELEASE)
    print(json.dumps({'release': str(RELEASE), 'version': VERSION, 'files': metadata['files']}, sort_keys=True))


if __name__ == '__main__':
    main()

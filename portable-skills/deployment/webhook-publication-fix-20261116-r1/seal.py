"""Seal only the 2.1.8 artifact-publication permission correction."""
import ast
import base64
import hashlib
import io
import json
from pathlib import Path
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SKILL = ROOT / 'portable-skills/candidates/energy-market-daily-2.1.8'
RELEASE = ROOT / 'release/energy-publication-fix-20261116-r1'
NAME = 'install-energy-publication-fix-20261116-r1.py'
VERSION = '2.1.8-portable'
OLD_TREE = 'e9f273fb122b42a692517e39323ef0bbe6c80318cacdf694bb39ebc094ce99a0'
TEST_COUNT = 62
GATEWAY419 = {'uid': 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1', 'generation': 419,
              'spec_sha256': '08a8afffb65eaca6259fe8b01111a5c68a2523fba639f17a4b25611e9ccb98c8'}


def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def assign(source, name, expression):
    nodes = [node for node in ast.parse(source).body if isinstance(node, ast.Assign)
             and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)]
    assert len(nodes) == 1, name
    node = nodes[0];lines = source.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = [name + '=' + expression + '\n']
    return ''.join(lines)


def build():
    old = ROOT / 'release/energy-webhook-skill-20261116-r3'
    previous = old / 'install-energy-webhook-skill-20261116-r3.py'
    raw = previous.read_bytes();assert sha(raw) == '3ab0b0ef8d6adf318a61e3eba5fe36fa4522cfe1eb84a555425fc252eed452b8'
    scope = {'__name__': 'retained_installer_definitions'};exec(compile(raw, str(previous), 'exec'), scope)
    assets = scope['load_assets']()
    payloads = {path.relative_to(SKILL).as_posix(): path.read_bytes() for path in sorted(SKILL.rglob('*'))
                if path.is_file() and path.name not in ('MANIFEST.json', 'CANDIDATE.md')}
    assert len(payloads) == 108 and not any('__pycache__' in name or name.endswith('.pyc') for name in payloads)
    preserved = {}
    for name in ('common.py', 'market.py', 'research.py', 'report_renderer.py', 'browser_runtime.py'):
        value = payloads['scripts/' + name]
        assert value == (SKILL.with_name('energy-market-daily-2.1.7') / 'scripts' / name).read_bytes()
        preserved[name] = sha(value)
    manifest = {'schema': 'energy-daily-portable-files.v1', 'version': VERSION,
                'files': {name: {'bytes': len(value), 'sha256': sha(value)} for name, value in payloads.items()}}
    payloads['MANIFEST.json'] = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, value in sorted(payloads.items()):
            info = zipfile.ZipInfo('energy-market-daily/' + name, (2026, 11, 16, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED;info.external_attr = 0o100644 << 16
            archive.writestr(info, value)
    package = stream.getvalue()
    fixtures = json.loads(assets['test_files'])
    for name in ('test_energy_work_preview_20261115.py', 'test_energy_work_preview_news_20261115.py', 'test_energy_webhook_send_20261116.py'):
        source = base64.b64decode(fixtures[name]).decode().replace('energy-market-daily-2.1.7', 'energy-market-daily-2.1.8')
        if name == 'test_energy_webhook_send_20261116.py':
            source = source.rsplit("\nif __name__ == '__main__':", 1)[0]
            extension = (ROOT / 'portable-skills/tests/test_energy_publication_owner_20261116.py').read_text(encoding='utf-8')
            node = next(node for node in ast.parse(extension).body if isinstance(node, ast.ClassDef) and node.name == 'PublicationOwnerTests')
            source += '\n\n' + ast.get_source_segment(extension, node) + '\n'
        fixtures[name] = base64.b64encode(source.encode()).decode()
    verifier = assets['verifier'].decode().replace('2.1.7-portable', VERSION).replace('energy-market-daily-2.1.7', 'energy-market-daily-2.1.8')
    namespace = {'__name__': 'candidate_verifier_definitions'};exec(compile(verifier, '<verifier>', 'exec'), namespace)
    suites = dict(namespace['OFFLINE_SUITES']);suites['test_energy_webhook_send_20261116.py'] = ['WebhookTests', 'PublicationOwnerTests']
    verifier = assign(verifier, 'OFFLINE_SUITES', repr(suites))
    verifier = assign(verifier, 'FIXTURES', repr({name: sha(base64.b64decode(value)) for name, value in fixtures.items()}))
    marker = '            daily.validate_result(report, project)\n'
    assert verifier.count(marker) == 1
    verifier = verifier.replace(marker, marker + "            need(all((Path(item['path']).stat().st_mode & 0o777)==0o644 for item in report['files']), 'PUBLIC_ARTIFACT_MODE_REQUIRED')\n"
        + "            need(all(((project / name).stat().st_mode & 0o777)==0o600 for name in ('PROJECT.json','RESULT.json')), 'PRIVATE_PROJECT_MODES_CHANGED')\n"
        + "            out['publication_permissions']={'four_artifact_modes_0644':True,'private_project_modes_0600':True,'gateway_file_authorization_modified':False}\n")
    host = raw.decode().replace('2.1.7-portable', VERSION)
    publisher = assets['publisher'].decode().replace('2.1.7-portable', VERSION)
    for before, after in (
        ('portal-energy-webhook-skill-install-20261116-r3', 'portal-energy-publication-fix-20261116-r1'),
        ('.energy-webhook-skill-install-20261116-r3-', '.energy-publication-fix-20261116-r1-'),
        ('--install-webhook-skill-once', '--install-publication-fix-once'),
    ):
        host = host.replace(before, after);publisher = publisher.replace(before, after)
    publisher = publisher.replace('OLD_COUNT, CODE_COUNT, NEW_COUNT = 108, 109, 110', 'OLD_COUNT, CODE_COUNT, NEW_COUNT = 110, 109, 110')
    assert 'OLD_COUNT, CODE_COUNT, NEW_COUNT = 110, 109, 110' in publisher
    # r3's post-publication visibility child retained an obsolete 108-file count.
    host = host.replace('EXPECTED_SKILL_COUNT = 108', 'EXPECTED_SKILL_COUNT = 110')
    for label, source in (('host', host), ('publisher', publisher), ('verifier', verifier)):
        source = assign(source, 'PACKAGE_SHA', repr(sha(package)))
        source = assign(source, 'CANDIDATE_VERSION', repr(VERSION))
        source = assign(source, 'OFFLINE_TEST_COUNT', repr(TEST_COUNT))
        if label != 'publisher':source = assign(source, 'CANDIDATE_FILES', '110')
        if label != 'verifier':source = assign(source, 'OLD_TREE', repr(OLD_TREE))
        if label == 'host':host = source
        elif label == 'publisher':publisher = source
        else:verifier = source
    prior_report = (old / 'OPERATOR-INSTALL-REPORT.json').read_bytes()
    assert len(prior_report) == 9782 and sha(prior_report) == 'a6d9ead7b1a0fe4d3573c655eb0cac582290a59c0f978a1f691a633e79055815'
    report = json.loads(prior_report)
    for name, expression in {
        'OLD_INSTALL': "Path('/var/lib/portal-energy-webhook-skill-install-20261116-r3/REPORT.json')",
        'OLD_INSTALL_SHA': repr(sha(prior_report)), 'OLD_INSTALL_BYTES': '9782',
        'OLD_INSTALL_OPERATION': repr(report['operation']), 'OLD_INSTALL_STATUS': repr(report['status']),
        'OLD_INSTALL_FILES': '110',
    }.items():host = assign(host, name, expression)
    # 419 changes the scheduler config only. Its recorded qualification retained
    # the task-context sources; it is still rolling, which is not acceptance of
    # all 70 Pods. This Skill publisher never patches or restarts Gateway.
    marker = "    if current is not None:need(current==identity,'GATEWAY_CONTEXT_LIVE_IDENTITY_CHANGED')\n"
    assert host.count(marker) == 1
    host = host.replace(marker, "    scheduler_raw=read(Path('/var/lib/portal-scheduler-concurrency-20261116-r2/REPORT.json'))\n"
        + "    need(len(scheduler_raw)==3523 and sha(scheduler_raw)=='959343f4fe60773657d31f2831d2114ade04a493b35b13fdcaaa81c32a7a4a54','SCHEDULER_419_RECEIPT_CHANGED')\n"
        + "    scheduler=json.loads(scheduler_raw)\n"
        + "    need(scheduler.get('operation')=='f0f944d091bc434caff845c24d19a836' and scheduler.get('status')=='GATEWAY_419_CONCURRENCY_32_ROLLOUT_REQUESTED'\n"
        + "         and scheduler.get('before')==identity and scheduler.get('candidate_spec_sha256')==" + repr(GATEWAY419['spec_sha256'])
        + " and scheduler.get('qualification',{}).get('semantic_change_only_scheduler_concurrency_and_multi_instance') is True,'SCHEDULER_419_SOURCE_COMPATIBILITY_REQUIRED')\n"
        + "    if current is not None:need(current==" + repr(GATEWAY419) + ",'GATEWAY_CONTEXT_LIVE_IDENTITY_CHANGED')\n")
    marker = "        self.out['shared_runtime_qualification']=accepted\n"
    assert host.count(marker) == 1
    host = host.replace(marker, marker + "        need(accepted.get('publication_permissions',{}).get('four_artifact_modes_0644') is True\n"
        + "             and accepted.get('publication_permissions',{}).get('private_project_modes_0600') is True,'PUBLICATION_PERMISSION_QUALIFICATION_REQUIRED')\n")
    next_assets = dict(assets, package=package, publisher=publisher.encode(), verifier=verifier.encode(), test_files=canonical(fixtures))
    host = assign(host, 'ASSETS', repr({name: {'base64': base64.b64encode(value).decode(), 'sha256': sha(value)} for name, value in next_assets.items()}))
    for source in (host, publisher, verifier):
        compile(source, '<publication-fix>', 'exec')
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'Path' and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                assert '\\' not in node.args[0].value, 'POSIX_FILESYSTEM_LITERAL_REQUIRED'
    ast.parse(host, feature_version=(3, 7))
    output = {NAME: host.encode(), 'energy-market-daily-2.1.8-public-code.zip': package,
              'publisher.sealed.py': next_assets['publisher'], 'verify_child.sealed.py': next_assets['verifier'],
              'CODE-MANIFEST.json': payloads['MANIFEST.json']}
    meta = {'schema': 'energy-publication-permission-fix-package.v1', 'version': VERSION, 'installed_files': 110,
        'offline_test_count': TEST_COUNT, 'accepted_old_tree': OLD_TREE, 'skill_core_preserved': preserved,
        'production_executed': False, 'messages_sent': 0, 'gateway_modified': False, 'package_sha256': sha(package),
        'files': {name: {'bytes': len(value), 'sha256': sha(value)} for name, value in output.items()}}
    output['SOURCE.json'] = (json.dumps(meta, sort_keys=True, indent=2) + '\n').encode()
    return output, meta


def main():
    output, meta = build();RELEASE.mkdir(parents=True, exist_ok=True)
    for name, raw in output.items():
        path = RELEASE / name
        if path.exists():assert path.read_bytes() == raw, 'EXISTING_RELEASE_DIFFERS'
        else:path.write_bytes(raw)
    print(json.dumps({'filename': NAME, **meta['files'][NAME], 'package_sha256': meta['package_sha256']}))


if __name__ == '__main__':main()

"""Seal a Skill-only webhook sender using the accepted ordinary-mount publisher."""
import ast
import base64
import hashlib
import io
import json
from pathlib import Path
import zipfile

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SKILL = REPO / 'portable-skills/candidates/energy-market-daily-2.1.7'
RELEASE = REPO / 'release/energy-webhook-skill-20261116-r2'
VERSION = '2.1.7-portable'
OLD_TREE = '432383e2ee0d52d49328b6a0ccde56b0d04c10b81e03e4b854a507d9078e44ba'
TEST_COUNT = 57
NAME = 'install-energy-webhook-skill-20261116-r2.py'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def assign(source, name, value):
    nodes = [n for n in ast.parse(source).body if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    assert len(nodes) == 1, name
    node = nodes[0]; lines = source.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = [name + ' = ' + repr(value) + '\n']
    return ''.join(lines)


def remove_function(source, name):
    nodes = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(nodes) == 1
    node = nodes[0]; lines = source.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = []
    return ''.join(lines)


def build():
    prior = REPO / 'release/energy-work-preview-skill-20261115-r3'
    prior_meta = json.loads((prior / 'SOURCE.json').read_bytes())
    previous_name = 'install-energy-work-preview-skill-20261115-r3.py'
    raw = (prior / previous_name).read_bytes()
    assert sha(raw) == prior_meta['files'][previous_name]['sha256']
    scope = {'__name__': 'frozen_webhook_installer_parent'}
    exec(compile(raw, '<accepted-216-installer>', 'exec'), scope)
    assets = scope['load_assets']()
    files = {}
    for path in sorted(SKILL.rglob('*')):
        assert not path.is_symlink()
        if path.is_file() and path.name not in ('CANDIDATE.md', 'MANIFEST.json'):
            assert '__pycache__' not in path.parts and path.suffix != '.pyc'
            files[path.relative_to(SKILL).as_posix()] = path.read_bytes()
    assert len(files) == 108 and 'assets/browser-runtime.json' not in files
    preserved = {}
    for name in ('common.py', 'market.py', 'research.py', 'report_renderer.py', 'browser_runtime.py'):
        value = files['scripts/' + name]
        assert value == (SKILL.with_name('energy-market-daily-2.1.6') / 'scripts' / name).read_bytes()
        preserved[name] = sha(value)
    manifest = {'schema': 'energy-daily-portable-files.v1', 'version': VERSION,
                'files': {name: {'bytes': len(value), 'sha256': sha(value)} for name, value in files.items()}}
    files['MANIFEST.json'] = json.dumps(manifest, indent=2, sort_keys=True).encode() + b'\n'
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
        for name, value in sorted(files.items()):
            entry = zipfile.ZipInfo('energy-market-daily/' + name, (2026, 11, 16, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            zipped.writestr(entry, value)
    package = stream.getvalue(); package_sha = sha(package)
    fixtures = json.loads(assets['test_files'])
    # All test imports are rebound to this candidate by the existing qualifier.
    for name in ('test_energy_work_preview_20261115.py', 'test_energy_work_preview_news_20261115.py'):
        text = base64.b64decode(fixtures[name], validate=True).decode('utf-8')
        fixtures[name] = base64.b64encode(text.replace('energy-market-daily-2.1.6', 'energy-market-daily-2.1.7').encode()).decode()
    fixtures['test_energy_webhook_send_20261116.py'] = base64.b64encode(
        (REPO / 'portable-skills/tests/test_energy_webhook_send_20261116.py').read_bytes()).decode()
    verifier = assets['verifier'].decode().replace('2.1.6-portable', VERSION).replace('energy-market-daily-2.1.6', 'energy-market-daily-2.1.7')
    env = {'__name__': 'qualified_verifier_definitions'}
    exec(compile(verifier, '<verifier-definitions>', 'exec'), env)
    suites = dict(env['OFFLINE_SUITES'])
    suites['test_energy_webhook_send_20261116.py'] = ['WebhookTests']
    verifier = assign(verifier, 'OFFLINE_SUITES', suites)
    verifier = assign(verifier, 'FIXTURES', {name: sha(base64.b64decode(value, validate=True)) for name, value in fixtures.items()})
    publisher = assets['publisher'].decode().replace('2.1.6-portable', VERSION)
    host = raw.decode().replace('2.1.6-portable', VERSION)
    for before, after in [
        ('portal-energy-work-preview-skill-install-20261115-r3', 'portal-energy-webhook-skill-install-20261116-r2'),
        ('.energy-work-preview-skill-install-20261115-r3-', '.energy-webhook-skill-install-20261116-r2-'),
        ('--install-work-preview-skill-once', '--install-webhook-skill-once'),
    ]:
        host = host.replace(before, after); publisher = publisher.replace(before, after)
    publisher = publisher.replace('OLD_COUNT, CODE_COUNT, NEW_COUNT = 108, 107, 108',
                                  'OLD_COUNT, CODE_COUNT, NEW_COUNT = 108, 109, 110')
    for name in ('host', 'publisher', 'verifier'):
        value = locals()[name]
        value = assign(value, 'PACKAGE_SHA', package_sha)
        value = assign(value, 'CANDIDATE_VERSION', VERSION)
        value = assign(value, 'OFFLINE_TEST_COUNT', TEST_COUNT)
        if name != 'publisher':
            value = assign(value, 'CANDIDATE_FILES', 110)
        if name != 'verifier':
            value = assign(value, 'OLD_TREE', OLD_TREE)
        if name == 'host':host = value
        elif name == 'publisher':publisher = value
        else:verifier = value
    old_report = (prior / 'OPERATOR-INSTALL-REPORT.json').read_bytes()
    assert len(old_report) == 10368 and sha(old_report) == 'cf114e7b616df747e706f9efedb3757692abba0ea7f1ef14524fd30ebb362a5b'
    old = json.loads(old_report)
    constants = {
        'OLD_INSTALL': Path('/var/lib/portal-energy-work-preview-skill-install-20261115-r3/REPORT.json'),
        'OLD_INSTALL_SHA': sha(old_report), 'OLD_INSTALL_BYTES': len(old_report),
        'OLD_INSTALL_OPERATION': old['operation'], 'OLD_INSTALL_STATUS': old['status'], 'OLD_INSTALL_FILES': 108,
    }
    for name, value in constants.items():
        if name == 'OLD_INSTALL':
            host = assign(host, name, str(value)).replace('OLD_INSTALL = ' + repr(str(value)), 'OLD_INSTALL = Path(' + repr(str(value)) + ')')
        else:
            host = assign(host, name, value)
    host = remove_function(host, 'prior_failure_binding')
    marker = "        self.out['accepted_prior_failure']=prior_failure_binding()\n"
    assert host.count(marker) == 1
    host = host.replace(marker, '')
    next_assets = {'publisher': publisher.encode(), 'verifier': verifier.encode(),
                   'package': package, 'test_files': canonical(fixtures)}
    host = assign(host, 'ASSETS', {name: {'base64': base64.b64encode(value).decode(), 'sha256': sha(value)}
                                 for name, value in next_assets.items()})
    for name, value in [('host', host.encode()), ('publisher', next_assets['publisher']), ('verifier', next_assets['verifier'])]:
        compile(value, '<sealed-webhook-' + name + '>', 'exec')
    ast.parse(host, feature_version=(3, 7))
    output = {NAME: host.encode(), 'energy-market-daily-2.1.7-public-code.zip': package,
              'publisher.sealed.py': next_assets['publisher'], 'verify_child.sealed.py': next_assets['verifier'],
              'CODE-MANIFEST.json': files['MANIFEST.json']}
    meta = {'schema': 'energy-webhook-skill-package.v1', 'version': VERSION,
            'gateway_modified': False, 'skill_core_preserved': preserved, 'accepted_old_tree': OLD_TREE,
            'offline_test_count': TEST_COUNT, 'installed_files': 110, 'package_sha256': package_sha,
            'production_executed': False, 'webhook_messages_sent': 0,
            'files': {name: {'bytes': len(value), 'sha256': sha(value)} for name, value in output.items()}}
    output['SOURCE.json'] = json.dumps(meta, indent=2, sort_keys=True).encode() + b'\n'
    return output, meta


def main():
    output, meta = build()
    RELEASE.mkdir(parents=True, exist_ok=True)
    for name, value in output.items():
        path = RELEASE / name
        if path.exists():assert path.read_bytes() == value, 'EXISTING_RELEASE_DIFFERS'
        else:path.write_bytes(value)
    print(json.dumps({'filename': str(RELEASE / NAME), **meta['files'][NAME], 'package_sha256': meta['package_sha256']}))


if __name__ == '__main__':
    main()

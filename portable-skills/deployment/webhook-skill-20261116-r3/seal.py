"""Fix only installer POSIX-path serialization; keep the 2.1.7 Skill sealed."""
import ast
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SKILL = REPO / 'portable-skills/candidates/energy-market-daily-2.1.7'
RELEASE = REPO / 'release/energy-webhook-skill-20261116-r3'
NAME = 'install-energy-webhook-skill-20261116-r3.py'
OLD_TREE = '432383e2ee0d52d49328b6a0ccde56b0d04c10b81e03e4b854a507d9078e44ba'
EXPECTED_PATH = '/var/lib/portal-energy-work-preview-skill-install-20261115-r3/REPORT.json'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def assign_expression(source, name, expression):
    nodes = [n for n in ast.parse(source).body if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    assert len(nodes) == 1, name
    node = nodes[0]; lines = source.splitlines(keepends=True)
    lines[node.lineno - 1:node.end_lineno] = [name + ' = ' + expression + '\n']
    return ''.join(lines)


def build():
    prior = REPO / 'release/energy-webhook-skill-20261116-r2'
    meta = json.loads((prior / 'SOURCE.json').read_bytes())
    prior_name = 'install-energy-webhook-skill-20261116-r2.py'
    raw = (prior / prior_name).read_bytes()
    assert sha(raw) == meta['files'][prior_name]['sha256']
    scope = {'__name__': 'sealed_r2_installer_definitions'}
    exec(compile(raw, '<r2-installer>', 'exec'), scope)
    assets = scope['load_assets']()
    host = raw.decode().replace('portal-energy-webhook-skill-install-20261116-r2',
                                'portal-energy-webhook-skill-install-20261116-r3')
    host = host.replace('.energy-webhook-skill-install-20261116-r2-',
                        '.energy-webhook-skill-install-20261116-r3-')
    host = assign_expression(host, 'OLD_INSTALL', 'Path(' + repr(EXPECTED_PATH) + ')')
    marker = "def qualification_binding():\n    raw = read(OLD_INSTALL)"
    assert host.count(marker) == 1
    host = host.replace(marker, "def qualification_binding():\n    try:\n        raw = read(OLD_INSTALL)\n    except FileNotFoundError:\n        raise Refused('ACCEPTED_SKILL_INSTALL_RECEIPT_NOT_FOUND') from None")
    marker = "        report,runtime,runtime_hash,binding=qualification_binding()"
    assert host.count(marker) == 1
    host = host.replace(marker, "        self.out['last_step']='READ_ACCEPTED_SKILL_INSTALL_RECEIPT'\n" + marker)
    publisher = assets['publisher'].decode().replace('.energy-webhook-skill-install-20261116-r2-',
                                                    '.energy-webhook-skill-install-20261116-r3-')
    updated_assets = dict(assets, publisher=publisher.encode())
    import base64
    host = assign_expression(host, 'ASSETS', repr({name: {'base64': base64.b64encode(value).decode(), 'sha256': sha(value)}
                                                  for name, value in updated_assets.items()}))
    for source in (host, publisher, assets['verifier'].decode()):
        compile(source, '<posix-installer>', 'exec')
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'Path' and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    assert '\\' not in arg.value, 'BACKSLASH_FILESYSTEM_PATH_LITERAL'
    ast.parse(host, feature_version=(3, 7))
    output = {NAME: host.encode(), 'energy-market-daily-2.1.7-public-code.zip': assets['package'],
              'publisher.sealed.py': publisher.encode(), 'verify_child.sealed.py': assets['verifier'],
              'CODE-MANIFEST.json': (prior / 'CODE-MANIFEST.json').read_bytes()}
    result = {key: value for key, value in meta.items() if key != 'files'}
    result.update(schema='energy-webhook-skill-installer-posix-fix.v1', prior_installer_sha256=sha(raw),
                  production_executed=False, skill_payload_unchanged_from_r2=True,
                  original_failed_operation='cdf8c9bb0f0e48f98afc3db90b1da23c',
                  original_failed_attempt_not_replayed=True, original_once_claimed=False,
                  corrected_server_receipt_path=EXPECTED_PATH,
                  files={name: {'bytes': len(value), 'sha256': sha(value)} for name, value in output.items()})
    output['SOURCE.json'] = (json.dumps(result, sort_keys=True, indent=2) + '\n').encode()
    return output, result


def main():
    output, meta = build()
    RELEASE.mkdir(parents=True, exist_ok=True)
    for name, value in output.items():
        target = RELEASE / name
        if target.exists():assert target.read_bytes() == value, 'EXISTING_RELEASE_DIFFERS'
        else:target.write_bytes(value)
    print(json.dumps({'filename': str(RELEASE / NAME), **meta['files'][NAME], 'package_sha256': meta['package_sha256']}))


if __name__ == '__main__':
    main()

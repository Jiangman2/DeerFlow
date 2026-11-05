"""Package exact-host frontend preloads with read-only checks before the once claim."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import types


PINS = {
    '0015': ('ecs-zdz-pro-0015', 'de6ee5e8-dbfb-4272-9ebf-ae82182db085', '37523051cf47795c4ef1454bfa3a0aaae857a41ab1d044a345f574f6401e35de'),
    'datax02': ('datax-02', '3148d4c1-6a97-42ed-a78c-950dcea5cbec', 'c2354849999ce397dd3225def02d337d17f6243676dfc4ea6791644683cc98b5'),
}
TRANSPORT = {'bytes': 127319040, 'sha256': 'aeb791af15e7456ad41c1d64590e5f9796071475c5066da6bb46922418eae7b2'}
MANIFEST = 'sha256:15d1327740e688faf0617d1d8fb9f662618a963624ba7280a6d0efb54ed70475'
CONFIG = 'sha256:377bac0b69157c9cd0b1514ca986343381a6541ef30135127df4876f350d5ffc'
ARCHIVE = 'frontend.data-qa-progress.full.oci.tar'

EARLY_GATE = '''def early_preload_gate(I, R, F, tools, original, started):
    """Reuse the native cache-aware budget without creating a directory or log."""
    parent_path = CONTROL.rsplit('/', 1)[0]
    commands = []
    expected_reads = {
        'actual_imagefs': (True, ['imagefsinfo', '--output', 'json']),
        'budget_content_presence': (False, ['content', 'ls', '--quiet'] + ['digest==' + dg for dg in sorted(EARLY_BUDGET['blob_sizes'])]),
        'budget_committed_chains': (False, ['snapshots', '--snapshotter', 'overlayfs', 'ls']),
    }

    def call(phase, args, mutates=False, timeout=15, limit=MIB, is_cri=False, pass_fds=()):
        require(not mutates and not pass_fds and phase in expected_reads
                and (is_cri, args) == expected_reads[phase] and len(commands) < 3,
                'preclaim_read_scope')
        time_gate(started)
        argv = ([tools[1], '--runtime-endpoint', 'unix://' + I.SOCKET, '--image-endpoint', 'unix://' + I.SOCKET]
                if is_cri else [tools[0], '--address', I.SOCKET, '--namespace', 'k8s.io']) + args
        out, unused, receipt = original.run_bounded(argv, timeout=min(timeout, TOTAL_SECONDS - (time.monotonic() - started)), limit=limit)
        require(receipt.get('returncode') == 0 and receipt.get('error_code') is None, 'preclaim_metadata_query_failed')
        commands.append({'phase': phase, 'containerd_mutation_requested': False})
        return out

    # Before mkdir, the parent supplies exactly the future control directory's
    # device/capacity. The unchanged worker_budget retains both nodefs and
    # imagefs reserves and accounts for every missing ordered unpack chain.
    resource = types.SimpleNamespace(observed_directory=lambda path: R.observed_directory(parent_path if path == CONTROL else path))
    probe = types.SimpleNamespace(I=I, R=resource, F=F, p=EARLY_BUDGET, call=call)
    capacity = worker_budget(probe)
    source = os.open(INPUT, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(source)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == TRANSPORT_PIN['bytes'], 'input_type_or_size')
        digest, count = hashlib.sha256(), 0
        while True:
            time_gate(started)
            block = os.read(source, MIB)
            if not block:
                break
            count += len(block)
            require(count <= TRANSPORT_PIN['bytes'], 'input_bound')
            digest.update(block)
        require(stamp(before) == stamp(os.fstat(source)) and count == TRANSPORT_PIN['bytes']
                and digest.hexdigest() == TRANSPORT_PIN['sha256'], 'input_changed_or_digest')
    finally:
        os.close(source)
    return {'pass': True, 'capacity': capacity, 'commands': commands, 'input_hash_verified': True,
            'filesystem_capacity_reserved': False, 'files_written': False, 'containerd_mutation_requested': False}
'''


def require(ok, code):
    if not ok:
        raise ValueError(code)


def sha_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def replace_node(source, name, statement, function=False):
    found = []
    for node in ast.parse(source).body:
        if function and isinstance(node, ast.FunctionDef) and node.name == name:
            found.append(node)
        elif not function and isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            found.append(node)
    require(len(found) == 1, 'SOURCE_SHAPE_' + name)
    node = found[0]
    lines = source.splitlines(keepends=True)
    return ''.join(lines[:node.lineno - 1]) + statement + '\n' + ''.join(lines[node.end_lineno:])


def replace_once(source, old, new):
    require(source.count(old) == 1, 'MAIN_SHAPE_CHANGED')
    return source.replace(old, new, 1)


def load_source(source, host):
    module = types.ModuleType('frontend_preload_packaging')
    exec(compile(source, '<pinned-preload>', 'exec'), module.__dict__)
    module.HOST = host
    return module


def prepare_source(source, role, prepared):
    host, uid, unused = PINS[role]
    base = '/mnt/data' if role == '0015' else '/var/lib'
    source = replace_node(source, 'NODES', 'NODES = ' + repr({host: uid}))
    source = replace_node(source, 'CONTROL', 'CONTROL = ' + repr(base + '/portal-frontend-data-qa-progress-preload-20261022-r2'))
    source = replace_once(source, "['--preload-frontend-ui-once']", "['--preload-frontend-data-qa-progress-once']")
    main = next(ast.get_source_segment(source, n) for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    main = replace_once(main, "'image_execution': False, 'image_deletion': False}", "'image_execution': False, 'image_deletion': False, 'mutation_once_claimed': False}")
    parent_line = "        parent = trusted_directory('/mnt/data')\n" if role == '0015' else '        parent = trusted_directory(WORKSPACE_PARENT)\n'
    main = replace_once(main, parent_line, parent_line + "        if os.path.lexists(CONTROL):\n            report['mutation_once_claimed'] = True\n            raise Refused('once_exists_review_required')\n        preclaim = early_preload_gate(I, R, F, tools, original, started)\n        report['preclaim'] = preclaim\n")
    if role == '0015':
        main = replace_once(main, "        os.mkdir(CONTROL.rsplit('/', 1)[1], 0o700, dir_fd=parent); os.fsync(parent)\n",
                            "        os.mkdir(CONTROL.rsplit('/', 1)[1], 0o700, dir_fd=parent)\n        report['mutation_once_claimed'] = True\n        os.fsync(parent)\n")
    main = replace_once(main, '        report = controller.report\n', "        report = controller.report\n        report['preclaim'] = preclaim\n        report['mutation_once_claimed'] = True\n")
    main = replace_once(main, '    except BaseException as error:\n', "    except BaseException as error:\n        if isinstance(error, FileExistsError):\n            report['mutation_once_claimed'] = True\n")
    main = replace_once(main, "    summary.update(exitcode=0 if good else 1,", "    summary.update(mutation_once_claimed=report.get('mutation_once_claimed', False),\n                   preclaim_passed=report.get('preclaim', {}).get('pass', False),\n                   preflight_retry_available=not report.get('mutation_once_claimed', False),\n                   expected_node_uid=NODES.get(HOST), exitcode=0 if good else 1,")
    source = replace_node(source, 'main', main, function=True)
    budget_data = {key: prepared[key] for key in ('manifest', 'chains', 'blob_sizes', 'layer_stats')}
    marker = 'def main():\n'
    source = replace_once(source, marker, 'EARLY_BUDGET = ' + repr(budget_data) + '\n\n' + EARLY_GATE + '\n\n' + marker)
    ast.parse(source, feature_version=(3, 7))
    return source


def build(prior_directory, output):
    archive = prior_directory / ARCHIVE
    require(archive.is_file() and not archive.is_symlink() and archive.stat().st_size == TRANSPORT['bytes']
            and sha_file(archive) == TRANSPORT['sha256'], 'PRIOR_ARCHIVE_CHANGED')
    sources = {}
    for role, (host, uid, pin) in PINS.items():
        path = prior_directory / ('preload-portal-frontend-data-qa-progress-' + role + '-20261021-r1.py')
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == pin, 'PRIOR_SCRIPT_CHANGED')
        sources[role] = raw.decode('utf-8')
    module = load_source(sources['0015'], PINS['0015'][0])
    importer, unused, unused2 = module.load_inputs()
    with archive.open('rb') as stream:
        prepared = module.inspect_archive(stream.fileno(), importer, time.monotonic())
    require(module.MANIFEST == MANIFEST and module.CONFIG == CONFIG and len(prepared['chains']) == 11, 'ARCHIVE_IDENTITY')
    generated = {role: prepare_source(source, role, prepared) for role, source in sources.items()}
    output.mkdir(exist_ok=False)
    copied = output / ARCHIVE
    with archive.open('rb') as src, copied.open('xb') as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
    require(copied.stat().st_size == TRANSPORT['bytes'] and sha_file(copied) == TRANSPORT['sha256'], 'COPIED_ARCHIVE_CHANGED')
    scripts = {}
    for role, source in generated.items():
        name = 'preload-portal-frontend-data-qa-progress-' + role + '-20261022-r2.py'
        raw = source.encode('utf-8')
        (output / name).write_bytes(raw)
        scripts[role] = {'file': name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                         'hostname': PINS[role][0], 'expected_node_uid': PINS[role][1], 'prior_script_sha256': PINS[role][2]}
    record = {'schema': 'portal-data-qa-progress-frontend-package.v2', 'manifest': MANIFEST, 'config': CONFIG,
              'transport': TRANSPORT, 'scripts': scripts, 'archive_blob_and_diffid_validation': 'PASS',
              'exact_host_binding': True, 'capacity_checked_before_once_claim': True,
              'frontend_deployed': False, 'ready_for_rollout': False, 'fresh_node_uid_check_required_at_activation': True}
    (output / 'PACKAGE.json').write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prior-directory', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.prior_directory, args.output), sort_keys=True))

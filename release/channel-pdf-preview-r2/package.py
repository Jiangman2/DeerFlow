"""Rebase the frozen R1 payload on the supplied production-source audit, not HEAD."""
import argparse
import ast
import difflib
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAME = 'deerflow-channel-pdf-preview-20260923-r2'
OLD_NAME = 'deerflow-channel-pdf-preview-20260923-r1'
R1_SHA = '8b0025010f5bc1a5b946fd43e5be6077ae9ef354ec2205baf25a8ba630c6e16b'
IMAGES = {
    'gateway': 'deerflow.local/deer-flow-gateway:20260923-enterprise-arm64-rc15-ppt-master-v660-r15',
    'frontend': 'deerflow.local/deer-flow-frontend:20260921-enterprise-arm64-rc15-conversation-runtime4',
}
MANAGER = 'app/channels/manager.py'
LOCALES = {'src/core/i18n/locales/en-US.ts', 'src/core/i18n/locales/zh-CN.ts'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def replace_exact(source, old, new):
    if source.count(old) != 1:
        raise ValueError('Expected one audited replacement: ' + old[:100])
    return source.replace(old, new, 1)


def rebase(component, entry, source):
    """Only three known context mismatches may use these explicit narrow edits."""
    name = entry['path']
    result = source
    if name == MANAGER:
        if 'AsyncKeyedLockTable' in source:
            raise ValueError('manager baseline changed since production audit')
        result = replace_exact(result, 'from deerflow.config.paths import make_safe_user_id\n',
                               'from deerflow.config.paths import make_safe_user_id\nfrom deerflow.constants import ENTERPRISE_PRODUCT_NAME\n')
        # The identity messages already match R1 exactly; do not copy local manager logic.
        result = replace_exact(result, entry['edits'][1]['before'], entry['edits'][1]['after'])
    elif name in LOCALES:
        if 'appName: "中国海油通用智能体"' not in source:
            raise ValueError('expected existing production product name: ' + name)
        if name.endswith('en-US.ts'):
            old = 'feishu: "Feishu and Lark messages through your 本平台 app."'
            new = 'feishu: "Messages through your 海能 Work app."'
        else:
            old = 'feishu: "通过本平台应用接收飞书和 Lark 消息。"'
            new = 'feishu: "通过海能 Work 应用接收消息。"'
        result = replace_exact(result, old, new)
    else:
        for edit in entry['edits']:
            if result.count(edit['before']) == 1:
                result = result.replace(edit['before'], edit['after'], 1)
            elif result.count(edit['before']) == 0 and result.count(edit['after']) == 1:
                continue
            else:
                raise ValueError('Unexpected additional mismatch: ' + name)
    if component == 'gateway':
        ast.parse(result)
    # Some intended branding (notably constants.py) is already in production.
    # Keep such entries as hash-checked no-ops, rather than replacing the module.
    edits = []
    a, b = source.splitlines(keepends=True), result.splitlines(keepends=True)
    for group in difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(5):
        old = ''.join(a[group[0][1]:group[-1][2]])
        new = ''.join(b[group[0][3]:group[-1][4]])
        if not old or source.count(old) != 1:
            raise ValueError('ambiguous rebase: ' + name)
        edits.append({'before': old, 'after': new})
    return {'path': name, 'source_sha256': sha(source.encode('utf-8')),
            'target_sha256': sha(result.encode('utf-8')), 'edits': edits}, result


def load_inputs(archive, report):
    if sha(archive.read_bytes()) != R1_SHA:
        raise ValueError('original R1 checksum mismatch')
    audit = json.loads(report.read_bytes())
    if audit.get('audit') != 'channel-pdf-source-baseline-v1' or audit.get('errors'):
        raise ValueError('Incomplete or unexpected source audit')
    payload = {}
    with tarfile.open(archive) as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            name = member.name.removeprefix(OLD_NAME + '/')
            if '/' in name or '\\' in name:
                raise ValueError('unexpected R1 member')
            payload[name] = tar.extractfile(member).read()
    for row in payload['PACKAGE.SHA256'].decode().splitlines():
        expected, name = row.split('  ', 1)
        if sha(payload[name]) != expected:
            raise ValueError('R1 member checksum mismatch: ' + name)
    originals, targets, deltas = {}, {}, {}
    lock = {'schema': 1, 'report_sha256': sha(report.read_bytes()), 'components': {}}
    for component in IMAGES:
        data = audit['components'][component]
        if data['image'] != IMAGES[component]:
            raise ValueError('Unexpected production image')
        entries = json.loads(payload[component + '-delta.json'])
        files = {f['path']: f for f in data['files']}
        if len(files) != len(data['files']) or set(files) != {e['path'] for e in entries}:
            raise ValueError('audit source scope mismatch')
        originals[component], targets[component], deltas[component] = {}, {}, []
        for entry in entries:
            item = files[entry['path']]
            source = item['source']
            if '\r' in source or sha(source.encode('utf-8')) != item['source_sha256']:
                raise ValueError('Audit source checksum/newline mismatch')
            delta, result = rebase(component, entry, source)
            originals[component][entry['path']] = source
            targets[component][entry['path']] = result
            deltas[component].append(delta)
        status = data['container_status']
        if len(status) != 1 or status[0]['name'] != component:
            raise ValueError('audit container identity mismatch')
        lock['components'][component] = {'image': data['image'], 'runtime_image_id': status[0]['imageID'],
            'files': [{'path': e['path'], 'source_sha256': e['source_sha256'], 'target_sha256': e['target_sha256']}
                      for e in deltas[component]]}
    return payload, originals, targets, deltas, lock


def validate_engines(root, originals, targets):
    for component in IMAGES:
        with tempfile.TemporaryDirectory(prefix='channel-pdf-r2-replay-') as directory:
            base = Path(directory)
            for name, source in originals[component].items():
                target = base / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.encode('utf-8'))
            prefix = [sys.executable, str(root / 'apply-delta.py')] if component == 'gateway' else ['node', str(root / 'apply-delta.mjs')]
            command = prefix + [str(base), str(root / (component + '-delta.json')), str(base / 'receipt.json')]
            subprocess.run(command + ['--check-only'], check=True)
            if (base / 'receipt.json').exists() or any((base / name).read_bytes() != source.encode('utf-8') for name, source in originals[component].items()):
                raise ValueError('check-only must not mutate')
            subprocess.run(command, check=True)
            subprocess.run(command, check=True)
            for name, source in targets[component].items():
                if (base / name).read_bytes() != source.encode('utf-8'):
                    raise ValueError('replay mismatch: ' + name)
    print('production_source_replay=PASS files=19 check_only_readonly=yes idempotent=yes')


def assemble(root, archive, report):
    payload, originals, targets, deltas, lock = load_inputs(archive, report)
    for name in ('cnooc-haineng-mark.png', 'compressed-fixtures.json', 'smoke-gateway.py', 'isolated-buildkit.sh'):
        (root / name).write_bytes(payload[name])
    for name in ('apply-delta.py', 'apply-delta.mjs', 'Dockerfile.gateway', 'Dockerfile.frontend',
                 'verify-package.py', 'verify-oci-baseline.py', 'README.txt', 'VERIFICATION.md'):
        (root / name).write_bytes((HERE / name).read_text(encoding='utf-8').replace('\r\n', '\n').encode('utf-8'))
    env = payload['IMAGE_LOCKS.env'].decode().replace('channel-pdf-preview-r1', 'channel-pdf-preview-r2')
    (root / 'IMAGE_LOCKS.env').write_bytes(env.encode())
    builder = HERE.parent.joinpath('channel-pdf-preview-r1/build-candidate-datax-01.sh').read_text(encoding='utf-8')
    # The R1 builder here includes the tested exact containerd-name selector repair.
    if sha(builder.encode('utf-8')) != '5172bd79c853d06134b89a937bc3ec9b371b94eec7bb590d1a032fce95f35db6':
        raise ValueError('unexpected builder change outside audited OCI-FIX1')
    if 'io.containerd.image.name' not in builder:
        raise ValueError('fixed OCI selector missing')
    builder = replace_exact(builder, 'isolated_buildkit_start\ntrap isolated_buildkit_stop EXIT\n',
                            'trap isolated_buildkit_stop EXIT\nisolated_buildkit_start\n')
    anchor = 'fingerprint="$(\n'
    verify = 'for component in gateway frontend; do\n  python3 "${ROOT_DIR}/verify-oci-baseline.py" "${ARTIFACT_DIR}/base-layout-${component}" "${ROOT_DIR}/BASELINE_LOCK.json" "${component}"\ndone\n\n'
    builder = replace_exact(builder, anchor, verify + anchor)
    anchor = 'isolated_buildkit_start\nfor component in gateway frontend; do\n'
    check = '''isolated_buildkit_start
# Validate BOTH image source trees before native smoke or the frontend build.
for component in gateway frontend; do
  layout="${ARTIFACT_DIR}/base-layout-${component}"
  digest="$(cat "${layout}/manifest.digest")"
  isolated_buildctl build --progress=plain --frontend dockerfile.v0 \\
    --local "context=${ROOT_DIR}" --local "dockerfile=${ROOT_DIR}" \\
    --oci-layout "${component}_base=${layout}" \\
    --opt "filename=Dockerfile.${component}" --opt platform=linux/arm64 \\
    --opt target=source-check \\
    --opt "context:channel-pdf-${component}-base=oci-layout://${component}_base@${digest}"
done
echo 'candidate_image_source_preflight=PASS components=gateway,frontend files=19'
for component in gateway frontend; do
'''
    builder = replace_exact(builder, anchor, check)
    # Apply the same colocation guard immediately before each source-check stage.
    guard_start = builder.index('  kubectl --request-timeout=30s -n deer-flow get pods -l app=gateway -o json |')
    guard_end = builder.index('\n  if test "${component}" = gateway;', guard_start)
    guard = builder[guard_start:guard_end] + '\n'
    marker = '# Validate BOTH image source trees before native smoke or the frontend build.\nfor component in gateway frontend; do\n'
    builder = replace_exact(builder, marker, marker + guard)
    (root / 'build-candidate-datax-01.sh').write_bytes(builder.encode())
    for component, entries in deltas.items():
        (root / (component + '-delta.json')).write_bytes(json.dumps(entries, ensure_ascii=False, indent=2).encode('utf-8'))
    (root / 'BASELINE_LOCK.json').write_bytes(json.dumps(lock, indent=2).encode())
    validate_engines(root, originals, targets)
    manifest = ''.join(sha(p.read_bytes()) + '  ' + p.name + '\n' for p in sorted(root.iterdir()) if p.is_file())
    (root / 'PACKAGE.SHA256').write_bytes(manifest.encode('ascii'))
    subprocess.run([sys.executable, str(root / 'verify-package.py'), str(root)], check=True)
    return originals, targets


def package(archive, report, destination):
    output = destination / (NAME + '.tar.gz')
    checksum = output.with_name(output.name + '.sha256')
    if output.exists() or checksum.exists():
        raise ValueError('Refusing to overwrite a release')
    with tempfile.TemporaryDirectory(prefix='channel-pdf-r2-package-') as directory:
        root = Path(directory) / NAME
        root.mkdir()
        assemble(root, archive, report)
        def normalized(info):
            info.uid = info.gid = 0
            info.uname = info.gname = 'root'
            info.mode = 0o755 if info.isdir() or info.name.endswith('.sh') else 0o644
            return info
        with output.open('xb') as stream, tarfile.open(fileobj=stream, mode='w:gz', format=tarfile.PAX_FORMAT) as tar:
            tar.add(root, arcname=NAME, filter=normalized)
    with checksum.open('x', encoding='ascii', newline='\n') as stream:
        stream.write(sha(output.read_bytes()) + '  ' + output.name + '\n')
    print('candidate_package=' + str(output))
    print('arm64_image_build=NOT_RUN production_rollout=NOT_READY')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--r1', type=Path, required=True)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    package(args.r1, args.audit, args.output)

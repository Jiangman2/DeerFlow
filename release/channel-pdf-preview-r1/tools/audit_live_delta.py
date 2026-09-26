"""Collect only the 19 patch-target sources; never mutate a workload or run a build."""
import argparse
import datetime
import hashlib
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

PACKAGE = Path('/opt/deerflow-hotfixes/deerflow-channel-pdf-preview-20260923-r1')
IMAGES = {
    'gateway': 'deerflow.local/deer-flow-gateway:20260923-enterprise-arm64-rc15-ppt-master-v660-r15',
    'frontend': 'deerflow.local/deer-flow-frontend:20260921-enterprise-arm64-rc15-conversation-runtime4',
}
PATHS = {
    'gateway': (
        'app/gateway/routers/artifacts.py', 'app/gateway/routers/channel_connections.py',
        'packages/harness/deerflow/constants.py',
        *('app/channels/' + name + '.py' for name in
          ('feishu', 'telegram', 'slack', 'discord', 'dingtalk', 'wechat', 'wecom', 'manager')),
    ),
    'frontend': (
        'src/components/workspace/artifacts/artifact-file-detail.tsx',
        'src/components/workspace/settings/channels-settings-page.tsx',
        'src/components/workspace/channels/channel-provider-icon.tsx',
        'src/components/workspace/channels/channel-runtime-config-dialog.tsx',
        'src/components/workspace/channels/workspace-channels-list.tsx',
        'src/core/channels/provider-state.ts',
        'src/core/i18n/locales/en-US.ts', 'src/core/i18n/locales/zh-CN.ts',
    ),
}

# These readers import only standard libraries. No application import, file write,
# environment dump, database operation, package install, or network request.
PY_READER = r'''
import json, sys
from pathlib import Path
request = json.load(sys.stdin)
root = Path(request['root']).resolve()
result = {}
for name in request['paths']:
    try:
        target = (root / name).resolve()
        if root not in target.parents or not target.is_file():
            raise ValueError('missing_or_unsafe_source')
        with target.open('rb') as stream:
            data = stream.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise ValueError('source_size_limit')
        result[name] = {'source': data.decode('utf-8')}
    except (OSError, ValueError) as exc:
        result[name] = {'error': type(exc).__name__}
print(json.dumps(result, ensure_ascii=True))
'''

JS_READER = r'''
const fs = require('node:fs'), path = require('node:path');
const request = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = fs.realpathSync(request.root), result = {};
for (const name of request.paths) {
  let fd;
  try {
    const target = fs.realpathSync(path.resolve(root, name));
    if (!target.startsWith(root + path.sep) || !fs.statSync(target).isFile())
      throw Error('missing_or_unsafe_source');
    fd = fs.openSync(target, 'r');
    const buffer = Buffer.alloc(2 * 1024 * 1024 + 1);
    let size = 0, n;
    while (size < buffer.length && (n = fs.readSync(fd, buffer, size, buffer.length-size, null))) size += n;
    if (size > 2 * 1024 * 1024) throw Error('source_size_limit');
    result[name] = {source: new TextDecoder('utf-8', {fatal:true}).decode(buffer.subarray(0,size))};
  } catch (exc) {
    result[name] = {error: exc.code || exc.name};
  } finally {
    if (fd !== undefined) fs.closeSync(fd);
  }
}
process.stdout.write(JSON.stringify(result));
'''


def digest(data):
    return hashlib.sha256(data).hexdigest()


def manifests(root):
    checksums = {}
    for line in (root / 'PACKAGE.SHA256').read_text(encoding='ascii').splitlines():
        expected, name = line.split('  ', 1)
        if name in checksums:
            raise ValueError('duplicate_package_entry')
        checksums[name] = expected
    result = {}
    for component, paths in PATHS.items():
        name = component + '-delta.json'
        raw = (root / name).read_bytes()
        if checksums.get(name) != digest(raw):
            raise ValueError('delta_checksum_mismatch: ' + name)
        entries = json.loads(raw)
        if len(entries) != len(paths) or {e['path'] for e in entries} != set(paths):
            raise ValueError('unexpected_delta_scope: ' + component)
        for entry in entries:
            if not entry['edits'] or any(not e.get('before') or not e.get('after') for e in entry['edits']):
                raise ValueError('invalid_delta: ' + entry['path'])
        result[component] = entries
    return result


def kube(*args, input_text=None):
    env = dict(os.environ, KUBECONFIG='/etc/kubernetes/admin.conf')
    command = ['kubectl', '--request-timeout=25s', '-n', 'deer-flow', *args]
    result = subprocess.run(command, input=input_text, text=True, encoding='utf-8',
                            errors='strict', capture_output=True, timeout=45, env=env)
    if result.returncode:
        raise RuntimeError('kubectl_failed: ' + result.stderr[-1500:])
    return json.loads(result.stdout)


def owned_by(obj, kind, uid):
    return any(ref.get('controller') is True and ref.get('kind') == kind and ref.get('uid') == uid
               for ref in obj.get('metadata', {}).get('ownerReferences', []))


def select_pod(component, deployment, replicasets, pods):
    spec, status = deployment['spec'], deployment.get('status', {})
    containers = spec['template']['spec']['containers']
    if not any(c['name'] == component and c['image'] == IMAGES[component] for c in containers):
        raise ValueError('deployment_image_changed: ' + component)
    count = spec.get('replicas', 1)
    if (count < 1 or status.get('observedGeneration', 0) < deployment['metadata']['generation']
            or any(status.get(key, 0) != count for key in ('replicas', 'readyReplicas', 'updatedReplicas'))):
        raise ValueError('deployment_not_stable: ' + component)
    owners = {rs['metadata']['uid'] for rs in replicasets['items']
              if owned_by(rs, 'Deployment', deployment['metadata']['uid'])}
    candidates = []
    for pod in pods['items']:
        if pod['metadata'].get('deletionTimestamp') or pod.get('status', {}).get('phase') != 'Running':
            continue
        if not any(owned_by(pod, 'ReplicaSet', uid) for uid in owners):
            continue
        if not any(c['name'] == component and c['image'] == IMAGES[component]
                   for c in pod['spec']['containers']):
            continue
        if not any(c.get('type') == 'Ready' and c.get('status') == 'True'
                   for c in pod['status'].get('conditions', [])):
            continue
        if not any(c.get('name') == component and c.get('ready') is True
                   for c in pod['status'].get('containerStatuses', [])):
            continue
        candidates.append(pod)
    if not candidates:
        raise ValueError('no_ready_owned_pod: ' + component)
    return sorted(candidates, key=lambda p: p['metadata']['name'])[0]


def compare(entries, sources, component):
    if set(sources) != {e['path'] for e in entries}:
        raise ValueError('source_response_scope_mismatch')
    results = []
    for entry in entries:
        item = {'path': entry['path'], **sources[entry['path']]}
        if 'source' not in item:
            item['status'] = 'READ_FAILED'
        else:
            source = item['source']
            item['source_sha256'] = digest(source.encode('utf-8'))
            text = source.replace('\r\n', '\n')
            if component == 'gateway':
                text = text.replace('\r', '\n')  # Python read_text universal newline handling
            item['edits'] = []
            for i, edit in enumerate(entry['edits']):
                old_count, new_count = text.count(edit['before']), text.count(edit['after'])
                state = 'MISMATCH'
                if old_count == 1:
                    text = text.replace(edit['before'], edit['after'], 1)
                    state = 'MATCH'
                elif old_count == 0 and new_count == 1:
                    state = 'ALREADY_APPLIED'
                item['edits'].append({'index': i, 'before_count': old_count,
                                      'after_count': new_count, 'status': state})
            item['status'] = ('MISMATCH' if any(e['status'] == 'MISMATCH' for e in item['edits'])
                              else 'MATCH')
            # This is a text-context audit, not Python/TypeScript build acceptance.
            # The server host's Python may differ from the image's interpreter.
        results.append(item)
    return results


def audit_component(component, entries):
    deployment = kube('get', 'deployment', component, '-o', 'json')
    pod = select_pod(component, deployment, kube('get', 'replicasets', '-o', 'json'),
                     kube('get', 'pods', '-o', 'json'))
    name = pod['metadata']['name']
    print('source_audit_start component={} pod={}'.format(component, name), flush=True)
    root = '/app/backend' if component == 'gateway' else '/app/frontend'
    reader = ['python3', '-B', '-c', PY_READER] if component == 'gateway' else ['node', '-e', JS_READER]
    sources = kube('exec', '-i', name, '-c', component, '--', *reader,
                   input_text=json.dumps({'root': root, 'paths': PATHS[component]}))
    latest = kube('get', 'deployment', component, '-o', 'json')
    latest_pod = kube('get', 'pod', name, '-o', 'json')
    if (latest['metadata']['uid'] != deployment['metadata']['uid']
            or latest['metadata']['generation'] != deployment['metadata']['generation']
            or latest_pod['metadata']['uid'] != pod['metadata']['uid']
            or latest_pod['metadata'].get('deletionTimestamp')):
        raise ValueError('baseline_changed_during_audit: ' + component)
    prior_status = next(c for c in pod['status']['containerStatuses'] if c['name'] == component)
    current_status = next((c for c in latest_pod.get('status', {}).get('containerStatuses', [])
                           if c['name'] == component), None)
    if (current_status is None or not current_status.get('ready')
            or any(current_status.get(k) != prior_status.get(k) for k in ('imageID', 'restartCount'))):
        raise ValueError('container_changed_during_audit: ' + component)
    results = compare(entries, sources, component)
    for item in results:
        print('source_delta={} component={} file={}'.format(item['status'], component, item['path']), flush=True)
    return {'image': IMAGES[component], 'pod': name, 'pod_uid': pod['metadata']['uid'],
            'container_status': [{k: c.get(k) for k in ('name', 'image', 'imageID', 'restartCount')}
                                 for c in pod['status']['containerStatuses'] if c.get('name') == component],
            'delta_sha256': digest(json.dumps(entries, sort_keys=True).encode()), 'files': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if socket.gethostname().split('.')[0] != 'datax-01':
        raise ValueError('run_on_datax_01_only')
    output = args.output.absolute()
    if output.parent != Path('/home/cnooc') or output.exists() or output.suffix != '.json':
        raise ValueError('use_new_json_report_directly_under_home_cnooc')
    entries = manifests(PACKAGE)
    report = {'audit': 'channel-pdf-source-baseline-v1',
              'checks': 'exact_text_context_only_not_build_or_runtime_acceptance',
              'time_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'production_workloads_mutated': False, 'components': {}, 'errors': {}}
    for component in PATHS:
        try:
            report['components'][component] = audit_component(component, entries[component])
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            report['errors'][component] = str(exc)[:2000]
            print('source_audit_error component={} detail={}'.format(component, str(exc)[:500]), flush=True)
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(report, stream, ensure_ascii=True, indent=2)
    owner = output.parent.stat()
    if os.geteuid() == 0:
        os.chown(output, owner.st_uid, owner.st_gid)
    mismatch = sum(item['status'] != 'MATCH' for component in report['components'].values() for item in component['files'])
    print('source_audit_report=' + str(output), flush=True)
    print('source_audit_collection={} mismatched_files={}'.format('INCOMPLETE' if report['errors'] else 'COMPLETE', mismatch))
    print('production_workloads_mutated=no build=NOT_RUN rollout=NOT_RUN')
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())

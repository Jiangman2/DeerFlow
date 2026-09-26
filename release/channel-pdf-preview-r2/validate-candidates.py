#!/usr/bin/env python3
"""R2 synthetic validation only. No production workload, configuration or DB writes."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import uuid

NS = 'deer-flow'
LABEL = 'validation.deerflow.local/channel-pdf-r2'
BASE = {
    'gateway': 'deerflow.local/deer-flow-gateway:20260923-enterprise-arm64-rc15-ppt-master-v660-r15',
    'frontend': 'deerflow.local/deer-flow-frontend:20260921-enterprise-arm64-rc15-conversation-runtime4',
}
CANDIDATE = {c: 'deerflow.local/deer-flow-{}:20260923-enterprise-arm64-rc15-channel-pdf-preview-r2'.format(c) for c in BASE}
IDENTITIES = {
    'gateway': {'c0c7a0a34b6b2e667c605de6700800bdce192f822f2d95a1be8771b35dbe4932',
                'a18f04d64152ea0b1272863e2dd57c2706573a4642bc06943da36a9518fb0856'},
    'frontend': {'8f85516b9b7b8cf716c0f97de348abdda675b5d4c47479ea6043df14ab2516eb',
                 'b0e7e951586937b28e0f3d5dc0b2df8e6748405bef623c8dfc455e7613688c36'},
}
TARGETS = [('gateway', 'ecs-zdz-pro-0022')] + [('frontend', n) for n in ('datax-01', 'datax-02', 'datax-03')]
MARKERS = {'gateway': 'channel_pdf_gateway_native_smoke=PASS', 'frontend': 'channel_pdf_frontend_loopback_smoke=PASS'}

GATEWAY_PROBE = r'''
import hashlib, json, os, runpy, sys
from pathlib import Path
os.environ.clear()
os.environ.update(PATH='/app/backend/.venv/bin:/usr/local/bin:/usr/bin:/bin', HOME='/tmp', LANG='C.UTF-8', PYTHONDONTWRITEBYTECODE='1')
root = Path('/app/backend')
os.chdir(root)
sys.path[:0] = [str(root), str(root / 'packages/harness')]
entries = json.loads(Path('/tmp/channel-pdf-r2/gateway-delta.json').read_bytes())
assert len(entries) == 11
for e in entries:
    p = (root / e['path']).resolve()
    assert root in p.parents
    assert hashlib.sha256(p.read_bytes()).hexdigest() == e['target_sha256'], e['path']
print('candidate_source_hashes=PASS component=gateway files=11', flush=True)
runpy.run_path('/tmp/channel-pdf-r2/smoke-gateway.py', run_name='__main__')
'''

FRONTEND_PROBE = r'''
const fs = require('node:fs'), path = require('node:path');
const crypto = require('node:crypto'), {spawn} = require('node:child_process');
const assert = require('node:assert/strict');
const root = '/app/frontend';
const hash = b => crypto.createHash('sha256').update(b).digest('hex');
const delay = ms => new Promise(r => setTimeout(r, ms));
function firstJS(dir) {
  for (const name of fs.readdirSync(dir).sort()) {
    const p = path.join(dir, name), st = fs.lstatSync(p);
    if (st.isFile() && p.endsWith('.js') && st.size > 0 && st.size < 8*1024*1024) return p;
    if (st.isDirectory()) { const found = firstJS(p); if (found) return found; }
  }
}
(async () => {
  const entries = JSON.parse(fs.readFileSync('/tmp/channel-pdf-r2/frontend-delta.json', 'utf8'));
  assert.equal(entries.length, 8);
  for (const e of entries) {
    const p = path.resolve(root, e.path);
    assert.ok(p.startsWith(root + '/'));
    assert.equal(hash(fs.readFileSync(p)), e.target_sha256, e.path);
  }
  const icon = fs.readFileSync(root + '/public/images/cnooc-haineng-mark.png');
  assert.equal(hash(icon), 'f6df5c2a4a1c4bfbabc2e896df16d231f5d3379b4a40d1b0b4e5ffae3531bfc9');
  assert.ok(fs.readFileSync(root + '/.next/BUILD_ID', 'utf8').trim());
  const asset = firstJS(root + '/.next/static');
  assert.ok(asset, 'missing built static JS');
  console.log('candidate_source_hashes=PASS component=frontend files=8 icon=PASS');
  const child = spawn(process.execPath, ['node_modules/next/dist/bin/next', 'start', '--hostname', '127.0.0.1', '--port', '3000'], {
    cwd: root, stdio: ['ignore', 'pipe', 'pipe'],
    env: {PATH:'/usr/local/bin:/usr/bin:/bin', HOME:'/tmp', NODE_ENV:'production',
      SKIP_ENV_VALIDATION:'1', NEXT_TELEMETRY_DISABLED:'1', NEXT_CONFIG_BUILD_OUTPUT:'standalone',
      DEER_FLOW_GATEWAY_INTERNAL_URL:'http://127.0.0.1:9'}
  });
  let tail = '', spawnError;
  child.on('error', e => { spawnError = e; });
  for (const stream of [child.stdout, child.stderr]) stream.on('data', b => { tail = (tail+b.toString()).slice(-16000); });
  try {
    let ready = false;
    for (let i=0; i<60; i++) {
      if (spawnError) throw spawnError;
      if (child.exitCode !== null || child.signalCode) throw Error('next exited: '+child.exitCode);
      try {
        const r = await fetch('http://127.0.0.1:3000/images/cnooc-haineng-mark.png', {redirect:'manual', signal:AbortSignal.timeout(2500)});
        if (r.status === 200) {
          assert.match(r.headers.get('content-type') || '', /^image\/png/i);
          assert.equal(hash(Buffer.from(await r.arrayBuffer())), hash(icon));
          ready = true; break;
        }
        await r.body?.cancel();
      } catch (e) { if (e.code === 'ERR_ASSERTION') throw e; }
      await delay(1000);
    }
    assert.ok(ready, 'Next loopback HTTP did not become ready');
    const url = 'http://127.0.0.1:3000/_next/static/' + path.relative(root+'/.next/static', asset).split(path.sep).map(encodeURIComponent).join('/');
    const r = await fetch(url, {redirect:'manual', signal:AbortSignal.timeout(10000)});
    assert.equal(r.status, 200);
    assert.match(r.headers.get('content-type') || '', /javascript/i);
    assert.equal(hash(Buffer.from(await r.arrayBuffer())), hash(fs.readFileSync(asset)));
    console.log('channel_pdf_frontend_loopback_smoke=PASS next_start=yes icon_http=yes static_js_http=yes');
  } catch (e) { console.error(tail); throw e; }
  finally {
    child.kill('SIGTERM');
    for(let i=0; i<25 && child.exitCode===null && !child.signalCode; i++) await delay(200);
    if(child.exitCode===null && !child.signalCode) child.kill('SIGKILL');
  }
})().catch(e => {console.error(e.stack || e); process.exitCode=1;});
'''


def emit(message):
    print(message, flush=True)


def kubectl(*args, data=None, timeout=45):
    result = subprocess.run(['kubectl', '--request-timeout=30s', '-n', NS] + list(args),
        input=None if data is None else json.dumps(data), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise RuntimeError('kubectl {}: {}'.format(' '.join(args[:3]), result.stderr[-4000:]))
    return result.stdout


def get(kind, name=None):
    return json.loads(kubectl('get', kind, *([name] if name else []), '-o', 'json'))


def manifest(component, node, token):
    return {'apiVersion': 'v1', 'kind': 'Pod',
        'metadata': {'name': 'cpdf-r2-{}-{}-{}'.format(component, node, token), 'namespace': NS,
                     'labels': {LABEL: token}},
        'spec': {'nodeSelector': {'kubernetes.io/hostname': node},
            'tolerations': ([{'key':'dedicated', 'operator':'Equal', 'value':'gateway', 'effect':'NoSchedule'}] if component == 'gateway' else []),
            'automountServiceAccountToken': False, 'enableServiceLinks': False,
            'restartPolicy': 'Never', 'activeDeadlineSeconds': 900, 'terminationGracePeriodSeconds': 10,
            'containers': [{'name': 'probe', 'image': CANDIDATE[component], 'imagePullPolicy': 'Never',
                'command': (['/app/backend/.venv/bin/python', '-B', '-c', GATEWAY_PROBE] if component == 'gateway' else ['node', '-e', FRONTEND_PROBE]),
                'resources': {'requests': {'cpu':'100m', 'memory':'128Mi'},
                              'limits': {'cpu':'500m', 'memory':'1Gi', 'ephemeral-storage':'256Mi'}},
                'securityContext': {'allowPrivilegeEscalation':False, 'capabilities':{'drop':['ALL']}}}]}}


def baseline():
    snapshot = {}
    for component, image in BASE.items():
        obj = get('deployment', component)
        containers = obj['spec']['template']['spec']['containers']
        current = [c['image'] for c in containers if c['name'] == component]
        status = obj.get('status', {})
        if current != [image] or obj['spec'].get('replicas') != 1:
            raise RuntimeError('production baseline/topology changed: ' + component)
        if status.get('observedGeneration', 0) < obj['metadata']['generation'] or any(status.get(k, 0) != 1 for k in ('readyReplicas', 'updatedReplicas', 'availableReplicas')):
            raise RuntimeError('production deployment not converged: ' + component)
        snapshot[component] = {'uid':obj['metadata']['uid'], 'spec_sha256':hashlib.sha256(json.dumps(obj['spec'], sort_keys=True).encode()).hexdigest()}
    return snapshot


def check_node(node):
    obj = get('node', node)
    conditions = {c['type']:c['status'] for c in obj['status']['conditions']}
    if obj['spec'].get('unschedulable') or conditions.get('Ready') != 'True' or any(conditions.get(k) != 'False' for k in ('DiskPressure','MemoryPressure','PIDPressure')):
        raise RuntimeError('node not ready/pressure/cordoned: ' + node)
    if obj['metadata']['labels'].get('kubernetes.io/arch') != 'arm64':
        raise RuntimeError('node is not arm64: ' + node)


def check_service_selectors(labels):
    for svc in get('service')['items']:
        selector = svc.get('spec', {}).get('selector', {})
        if selector and all(labels.get(k) == v for k, v in selector.items()):
            raise RuntimeError('probe would match Service: ' + svc['metadata']['name'])


def verify_success(obj, component, logs):
    statuses = obj.get('status', {}).get('containerStatuses', [])
    if obj.get('status', {}).get('phase') != 'Succeeded' or len(statuses) != 1:
        raise RuntimeError('probe did not succeed')
    item = statuses[0]
    if item.get('state', {}).get('terminated', {}).get('exitCode') != 0 or MARKERS[component] not in logs:
        raise RuntimeError('missing successful native probe receipt')
    match = re.search(r'sha256:([0-9a-f]{64})$', item.get('imageID', ''))
    if not match or match.group(1) not in IDENTITIES[component]:
        raise RuntimeError('unexpected candidate runtime image identity: ' + item.get('imageID', ''))


def cleanup(kind, name, token, uid):
    obj = get(kind, name)
    if obj['metadata'].get('labels', {}).get(LABEL) != token or obj['metadata']['uid'] != uid:
        raise RuntimeError('refusing cleanup: object ownership changed')
    kubectl('delete', kind, name, '--wait=true', '--timeout=45s', timeout=55)


def run_probe(component, node, token, evidence):
    check_node(node)
    pod = manifest(component, node, token)
    check_service_selectors(pod['metadata']['labels'])
    name = pod['metadata']['name']
    emit('candidate_probe_start component={} node={} pod={}'.format(component, node, name))
    created = json.loads(kubectl('create', '-f', '-', '-o', 'json', data=pod))
    uid = created['metadata']['uid']
    try:
        deadline, last_update = time.monotonic() + 960, 0
        while True:
            obj = get('pod', name)
            status = obj.get('status', {})
            if status.get('phase') in ('Succeeded', 'Failed'):
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('candidate startup/runtime timeout: ' + name)
            if time.monotonic() - last_update > 20:
                emit('candidate_probe_wait node={} phase={}'.format(node, status.get('phase', 'Pending')))
                last_update = time.monotonic()
            time.sleep(3)
        logs = kubectl('logs', name, '-c', 'probe', '--tail=200', '--limit-bytes=24000')
        (evidence / (name + '.log')).write_text(logs, encoding='utf-8')
        emit(logs.rstrip())
        verify_success(obj, component, logs)
        emit('channel_pdf_r2_candidate_node=PASS component={} node={}'.format(component, node))
    finally:
        try:
            obj = get('pod', name)
            (evidence / (name + '.status.json')).write_text(json.dumps(obj.get('status', {}), ensure_ascii=False, indent=2), encoding='utf-8')
            events = json.loads(kubectl('get', 'events', '--field-selector=involvedObject.uid=' + uid, '-o', 'json'))
            (evidence / (name + '.events.json')).write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')
        finally:
            cleanup('pod', name, token, uid)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Create sequential temporary test Pods and a scoped NetworkPolicy; never rollout.')
    args = parser.parse_args()
    if not args.run:
        parser.print_help()
        return
    if socket.gethostname().split('.')[0] != 'datax-01' or os.geteuid() != 0:
        raise RuntimeError('run as root on datax-01 only')
    os.environ['KUBECONFIG'] = '/etc/kubernetes/admin.conf'
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    token = uuid.uuid4().hex[:8]
    evidence = Path('/opt/deerflow-hotfix-evidence/channel-pdf-r2-candidate-' + stamp + '-' + token)
    evidence.mkdir(parents=True, mode=0o700)
    emit('evidence_dir=' + str(evidence))
    before = baseline()
    (evidence / 'baseline.json').write_text(json.dumps(before, indent=2), encoding='utf-8')
    for _, node in TARGETS:
        check_node(node)
    name = 'cpdf-r2-isolation-' + token
    policy = {'apiVersion':'networking.k8s.io/v1', 'kind':'NetworkPolicy',
        'metadata':{'name':name, 'namespace':NS, 'labels':{LABEL:token}},
        'spec':{'podSelector':{'matchLabels':{LABEL:token}}, 'policyTypes':['Ingress','Egress'], 'ingress':[], 'egress':[]}}
    obj = json.loads(kubectl('create', '-f', '-', '-o', 'json', data=policy))
    try:
        for component, node in TARGETS:
            run_probe(component, node, token, evidence)
            if baseline() != before:
                raise RuntimeError('production deployment changed during validation')
    finally:
        cleanup('networkpolicy', name, token, obj['metadata']['uid'])
    emit('channel_pdf_r2_candidate_validation=PASS gateway=1 frontend=3')
    emit('production_deployment_specs=UNCHANGED rollout=NOT_PERFORMED production_database_mutation=NONE')
    emit('full_gateway_lifecycle=NOT_TESTED authenticated_browser_acceptance=NOT_RUN')
    emit('evidence_dir=' + str(evidence))


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        emit('channel_pdf_r2_candidate_validation=FAIL detail=' + str(error))
        sys.exit(1)

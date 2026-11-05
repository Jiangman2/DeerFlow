"""One receipt-bound frontend image CAS; package only after actual field receipts."""
import copy
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import sys
import time
import types
import uuid

FRONT_COMMON = ''
PRELOADS = {}
PRIOR_RECEIPT = {'path': '/var/lib/portal-frontend-data-qa-token-activation-a1ac5b2855ba4d71965dba198878b697/REPORT.json', 'bytes': 96421, 'sha256': 'dfd628dbd45b03859196593343399137ac3395f117df98921623b8ac0a3e1899'}
SOURCE_PINS = {}
PROVENANCE = {}
FAILED_R2 = {'path': '/var/lib/portal-frontend-nonconfidential-notice-activation-28a6a57592b045b4b82b6ad994d28e15/REPORT.json', 'bytes': 5200, 'sha256': '00feb27a01a5d69462ba7ad9a77205f3cb3c4a658bccebd70ae864ef8e250e3c'}

FRONT = {'uid': 'bb0b3f6e-31f7-4073-8b76-7650ea5b96ee', 'generation': 226,
         'spec_sha256': 'ecf0297bd8609f104a6b549d484d84b7d658c71b4a0b100a5c66149e821fb3a3'}
MANIFEST = ''
CONFIG = ''
OLD = 'deerflow.local/deer-flow-frontend@sha256:b6c17e31ebcadb3c514fd2c3f87752e0eb84ee3e28ccc921366bac6a0bf91cd5'
NEW = 'deerflow.local/deer-flow-frontend@' + MANIFEST
TAG = ''
NODES = {'datax-02': '3148d4c1-6a97-42ed-a78c-950dcea5cbec',
         'ecs-zdz-pro-0015': 'de6ee5e8-dbfb-4272-9ebf-ae82182db085'}
ONCE = 'portal-frontend-nonconfidential-notice-activation-20261023-r3.once'
ACCEPTED = 'FRONTEND_NOTICE_RUNTIME_ACCEPTED_BROWSER_PENDING'
SECONDS = 1800
ROLES = ['frontend', 'gateway', 'ppt-master-runner', 'provisioner', 'nginx']
PRELOAD_PATHS = {n: ('/mnt/data' if n == 'ecs-zdz-pro-0015' else '/var/lib') +
    '/portal-frontend-nonconfidential-notice-preload-20261023-r2/REPORT.json' for n in NODES}


class Refused(Exception):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')


def pin(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def binding(d):
    return {'uid': d['metadata']['uid'], 'generation': d['metadata']['generation'],
            'spec_sha256': pin(canonical(d['spec']))['sha256']}




def validate_preloads(rows):
    require(type(rows) is dict and set(rows) == set(NODES), 'two_preload_receipts_required')
    for host, r in rows.items():
        require(r.get('schema') == 'portal-frontend-ui-cache-preload.v1' and r.get('hostname') == host
                and r.get('expected_node_uid') == NODES[host] and r.get('report_path') == PRELOAD_PATHS[host]
                and r.get('status') == 'COMPLETE_FRONTEND_UI_CACHE_PRELOAD_REVIEW_REQUIRED'
                and r.get('exitcode') == 0, 'preload_identity_or_status')
        require(re.fullmatch('[a-f0-9]{32}', r.get('operation', '')) and type(r.get('report_bytes')) is int
                and 0 < r['report_bytes'] <= 1024**2 and re.fullmatch('[a-f0-9]{64}', r.get('report_sha256', '')),
                'preload_report_binding_missing')
        require(r.get('manifest_digest') == MANIFEST and r.get('config_digest') == CONFIG
                and all(r.get(k) == 'PASS' for k in ('phase_a', 'phase_b', 'content_and_unpack', 'cri_visibility')),
                'preload_image_or_phases')
        require(all(r.get(k) is True for k in ('old_references_preserved', 'preclaim_passed'))
                and all(r.get(k) is False for k in ('image_import_may_be_partial', 'production_workload_mutation',
                    'automatic_retry', 'kubernetes_node_uid_freshly_checked'))
                and set(r.get('canonical_names_created', [])) == {NEW, TAG}, 'preload_preservation_or_completion')
        require(all(r.get(k, True) is True for k in ('image_import_requested', 'mutation_once_claimed'))
                and all(r.get(k, False) is False for k in ('ready_for_rollout', 'preflight_retry_available')),
                'preload_contradictory_optional_fields')


def load_modules():
    require(FRONT_COMMON and SOURCE_PINS and PROVENANCE, 'package_incomplete')
    front = types.ModuleType('_proven_frontend_activation')
    exec(compile(FRONT_COMMON, '<proven-frontend-helper>', 'exec'), front.__dict__)
    front.require = require
    front.UID, front.OLD, front.OLD_SPEC = FRONT['uid'], OLD, FRONT['spec_sha256']
    front.MANIFEST, front.CONFIG, front.NEW, front.TAG, front.NODES = MANIFEST, CONFIG, NEW, TAG, NODES
    front.SPEC = None
    front.node_gate = node_gate
    require(pin(front.HELPER) == front.HELPER_PIN, 'embedded_bounded_helper_changed')
    helper = types.ModuleType('_bounded_helper')
    exec(compile(front.HELPER, '<bounded-helper>', 'exec'), helper.__dict__)
    return front, helper


def node_gate(rows):
    require({n['metadata']['name']: n['metadata']['uid'] for n in rows} == NODES, 'node_uid_changed')
    observed = []
    for n in rows:
        c = {c['type']: c['status'] for c in n.get('status', {}).get('conditions', [])}
        require(c.get('Ready') == 'True' and all(c.get(k) == 'False' for k in
                ('MemoryPressure', 'DiskPressure', 'PIDPressure')), 'frontend_node_unhealthy')
        require(n['status']['nodeInfo']['architecture'] == 'arm64', 'frontend_node_platform_changed')
        require(not n.get('spec', {}).get('unschedulable', False) and not any(t.get('effect') == 'NoExecute'
                for t in n.get('spec', {}).get('taints', [])), 'frontend_node_unavailable')
        images = n['status'].get('images', [])
        names = {name for image in images for name in image.get('names', [])}
        # NodeStatus.Images is capped and is not the CRI's complete image inventory.
        # Fresh imagePullPolicy=Never Jobs below supply the positive execution evidence.
        observed.append({'name': n['metadata']['name'], 'uid': n['metadata']['uid'],
                         'reported_images': len(images), 'candidate_in_node_status': NEW in names or TAG in names})
    return sorted(observed, key=lambda row: row['name'])




def read_prior(helper):
    path = PRIOR_RECEIPT['path']
    require(os.path.realpath(path) == path, 'prior_report_symlink')
    for directory in ('/var', '/var/lib', os.path.dirname(path)):
        st = os.lstat(directory)
        require(stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o022, 'prior_directory_permissions')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and not before.st_mode & 0o077
                and before.st_nlink == 1 and before.st_size == PRIOR_RECEIPT['bytes'], 'prior_report_metadata')
        raw = stream.read(PRIOR_RECEIPT['bytes'] + 1)
        after = os.fstat(stream.fileno())
        require((before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'prior_report_changed')
    require(pin(raw) == {k: PRIOR_RECEIPT[k] for k in ('bytes', 'sha256')}, 'prior_report_hash')
    report = helper.parse(raw)
    require(report.get('stage_passed') is True and report.get('status') == 'FRONTEND_DATA_QA_TOKEN_RUNTIME_ACCEPTED_BROWSER_PENDING'
            and report.get('after', {}).get('frontend') == FRONT, 'prior_frontend_receipt_invalid')
    return report


def read_failed_r2(helper):
    path = FAILED_R2['path']
    require(os.path.realpath(path) == path, 'failed_report_symlink')
    for directory in ('/var', '/var/lib', os.path.dirname(path)):
        st = os.lstat(directory)
        require(stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o022, 'failed_directory_permissions')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        st = os.fstat(stream.fileno())
        require(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o077
                and st.st_nlink == 1 and st.st_size == FAILED_R2['bytes'], 'failed_report_metadata')
        raw = stream.read(FAILED_R2['bytes'] + 1)
    require(pin(raw) == {k: FAILED_R2[k] for k in ('bytes', 'sha256')}, 'failed_report_changed')
    r = helper.parse(raw)
    validate_failed_r2(r)
    require(not os.path.lexists('/var/lib/portal-frontend-nonconfidential-notice-activation-20261023-r2.once'),
            'prior_activation_marker_present')
    return dict(FAILED_R2, error_code=r['error_code'], patch_request_count=0)


def validate_failed_r2(r):
    require(r.get('schema') == 'portal-frontend-nonconfidential-notice-activation.v1'
            and r.get('error_code') == 'new_image_not_observed_in_kubelet_cache'
            and r.get('status') == 'INCOMPLETE' and r.get('stage_passed') is False
            and r.get('patch_request_count') == 0
            and all(r.get(k) is False for k in ('once_claimed', 'patch_requested', 'patch_may_have_applied',
                                               'other_workload_mutation', 'schema_migration_requested'))
            and r.get('cache_manifest') == MANIFEST and r.get('cache_config') == CONFIG,
            'failed_report_not_noop')


def make_execution(front):
    class Execution(front.Execution):
        def call(self, phase, args, pass_fds=(), must_succeed=True):
            require(args and args[0] in ('get', 'exec', 'patch', 'create', 'logs'), 'command_forbidden')
            if args[0] == 'create':
                require(getattr(self, 'approved_check', None) is not None and len(pass_fds) == 1
                        and args == ['create', '-f', '/proc/self/fd/%d' % pass_fds[0], '-o', 'json'],
                        'check_creation_scope_forbidden')
                approved = self.approved_check
                require(not approved.get('dispatched') and self.report.get('once_claimed') is True
                        and pin(os.pread(pass_fds[0], 65537, 0)) == approved['pin'], 'check_creation_binding')
                approved['dispatched'] = True
            if args[0] == 'logs':
                require(getattr(self, 'approved_check_logs', None) == args, 'check_logs_scope_forbidden')
            if args[0] == 'patch':
                require(self.report.get('once_claimed') is True and self.report['patch_request_count'] == 1
                        and args[:4] == ['patch', 'deployment', 'frontend', '--type=json']
                        and len(args) == 7 and args[4].startswith('--patch-file=/proc/self/fd/')
                        and args[5:] == ['-o', 'json'] and len(pass_fds) == 1,
                        'patch_scope_forbidden')
                require(not self.report.get('patch_dispatched'), 'second_patch_forbidden')
                self.report['patch_dispatched'] = True
            left = SECONDS - (time.monotonic() - self.started)
            require(left > 0 and len(self.report['commands']) < 700, 'activation_deadline')
            timeout = 180 if args[0] == 'exec' else 15
            base = [self.kubectl, '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=%ds' % timeout, '-n', 'deer-flow']
            out, err, result = self.H.run_bounded(base + args, timeout=min(timeout + 25, left),
                limit=4 * 1024**2, pass_fds=pass_fds)
            n = len(self.report['commands']) + 1
            result.update(ordinal=n, phase=phase)
            self.report['commands'].append(result)
            self.captured += len(out) + len(err)
            require(self.captured <= 64 * 1024**2, 'private_capture_budget')
            self.H.write_new(self.directory, '%04d.stdout.private.log' % n, out)
            self.H.write_new(self.directory, '%04d.stderr.private.log' % n, err)
            if time.monotonic() - self.progress >= 20:
                print(json.dumps({'phase': phase, 'commands': n}), flush=True)
                self.progress = time.monotonic()
            if must_succeed:
                require(result.get('returncode') == 0 and result.get('error_code') is None, 'bounded_command_failed')
            return out, result

        def patch_once(self, patch):
            require(self.report['patch_request_count'] == 0 and self.report.get('once_claimed') is True, 'second_patch_forbidden')
            # Durable dispatch intent survives a lost CLI response or process death.
            self.write('DISPATCH.json', {'operation': self.report['operation'], 'patch_request_count': 1,
                'patch_may_have_applied': True, 'automatic_retry': False, 'patch_sha256': pin(canonical(patch))['sha256']})
            return super().patch_once(patch)

        def create_check_once(self, spec):
            name = spec['metadata']['name']
            raw = canonical(spec) + b'\n'
            require(len(raw) <= 65536, 'check_manifest_limit')
            self.write(name + '.json', spec)
            fd = os.open(name + '.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.directory)
            try:
                # The inherited writer serializes canonical JSON plus one newline.
                require(pin(os.pread(fd, 65537, 0)) == pin(raw), 'check_manifest_write_changed')
                self.approved_check = {'pin': pin(raw), 'dispatched': False}
                self.write(name + '.DISPATCH.json', {'name': name, 'creation_may_have_applied': True,
                           'manifest': pin(raw), 'automatic_retry': False})
                self.report['temporary_jobs_created'].append({'name': name, 'creation_requested': True})
                output, result = self.call('check-create', ['create', '-f', '/proc/self/fd/%d' % fd, '-o', 'json'],
                                           pass_fds=(fd,), must_succeed=False)
                require(result.get('returncode') == 0 and result.get('error_code') is None,
                        'check_create_outcome_requires_review')
                return self.H.parse(output)
            finally:
                self.approved_check = None
                os.close(fd)

    return Execution


def cache_probe_code():
    return """const fs=require('fs'),crypto=require('crypto');
const pins=JSON.parse(process.argv[1]);
try {for(const [p,v] of Object.entries(pins)) {
 if(fs.realpathSync(p)!==p||v.bytes>1048576)throw Error('source');
 const fd=fs.openSync(p,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);
 try {const s=fs.fstatSync(fd);if(!s.isFile()||s.size!==v.bytes)throw Error('source');
 const b=fs.readFileSync(fd),t=fs.fstatSync(fd);
 if(s.dev!==t.dev||s.ino!==t.ino||s.size!==t.size||s.mtimeMs!==t.mtimeMs||s.ctimeMs!==t.ctimeMs||crypto.createHash('sha256').update(b).digest('hex')!==v.sha256)throw Error('source');
 }finally{fs.closeSync(fd)}
} console.log(JSON.stringify({status:'LOCAL_PRELOADED_IMAGE_EXECUTED',sources_verified:true,files_verified:Object.keys(pins).length,provider_requests:0}));
}catch(e){console.log(JSON.stringify({status:'INCOMPLETE',error_code:'candidate_source_changed'}));process.exit(1)}"""


def check_spec(host, operation, ordinal):
    require(host in NODES and re.fullmatch('[a-f0-9]{32}', operation), 'check_identity')
    name = 'portal-notice-cache-' + operation[:16] + '-' + str(ordinal)
    return {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {'name': name, 'namespace': 'deer-flow'},
            'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': 180, 'ttlSecondsAfterFinished': 3600,
                     'template': {'metadata': {'labels': {'portal-notice-cache-check': operation}}, 'spec': {
                         'nodeName': host, 'restartPolicy': 'Never', 'automountServiceAccountToken': False,
                         'enableServiceLinks': False, 'terminationGracePeriodSeconds': 5,
                         'containers': [{'name': 'check', 'image': NEW, 'imagePullPolicy': 'Never',
                             'command': ['node'], 'args': ['-e', cache_probe_code(), json.dumps(SOURCE_PINS)],
                             'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'},
                                           'limits': {'cpu': '500m', 'memory': '128Mi'}},
                             'securityContext': {'runAsUser': 1000, 'runAsGroup': 1000, 'runAsNonRoot': True,
                                 'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False,
                                 'capabilities': {'drop': ['ALL']}, 'seccompProfile': {'type': 'RuntimeDefault'}}}]}}}}


def check_pod(pod, job, spec):
    require(pod.get('metadata', {}).get('namespace') == 'deer-flow' and any(o.get('uid') == job['metadata']['uid']
            and o.get('controller') is True and o.get('kind') == 'Job'
            for o in pod['metadata'].get('ownerReferences', [])), 'check_pod_owner')
    s = pod['spec']
    expected = spec['spec']['template']['spec']
    require(s.get('nodeName') == expected['nodeName'] and s.get('automountServiceAccountToken') is False
            and not s.get('hostNetwork', False) and not s.get('hostPID', False) and not s.get('hostIPC', False)
            and not s.get('volumes') and not s.get('initContainers') and not s.get('ephemeralContainers')
            and len(s.get('containers', [])) == 1, 'check_pod_scope')
    c = s['containers'][0]
    require(all(c.get(k) == expected['containers'][0][k] for k in ('name', 'image', 'imagePullPolicy', 'command', 'args'))
            and not c.get('envFrom') and not c.get('env') and not c.get('volumeMounts')
            and not c.get('ports') and c.get('securityContext') == expected['containers'][0]['securityContext'],
            'check_container_changed')
    statuses = pod.get('status', {}).get('containerStatuses', [])
    return {'pod': pod['metadata']['name'], 'uid': pod['metadata']['uid'], 'node': s['nodeName'],
            'phase': pod.get('status', {}).get('phase'), 'containers': [
                {'imageID': c.get('imageID'), 'restart_count': c.get('restartCount'),
                 'waiting_reason': c.get('state', {}).get('waiting', {}).get('reason'),
                 'exit_code': c.get('state', {}).get('terminated', {}).get('exitCode')}
                for c in statuses]}


def verify_local_cache(e, sleep=time.sleep):
    checks = e.report.setdefault('cache_checks', [])
    for ordinal, host in enumerate(sorted(NODES)):
        spec = check_spec(host, e.report['operation'], ordinal)
        name = spec['metadata']['name']
        print(json.dumps({'phase': 'VERIFY_LOCAL_PRELOADED_IMAGE', 'node': host, 'job': name}), flush=True)
        job = e.create_check_once(spec)
        require(job.get('kind') == 'Job' and job.get('metadata', {}).get('name') == name
                and job['metadata'].get('namespace') == 'deer-flow' and job['metadata'].get('uid'), 'created_check_identity')
        row = {'node': host, 'node_uid': NODES[host], 'job': name, 'job_uid': job['metadata']['uid'], 'status': 'INCOMPLETE'}
        checks.append(row)
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            current = e.get('check-observe-job', ['job', name])
            require(current['metadata']['uid'] == job['metadata']['uid'], 'check_job_replaced')
            pods = e.get('check-observe-pod', ['pods', '-l', 'batch.kubernetes.io/controller-uid=' + job['metadata']['uid']])['items']
            require(len(pods) <= 1, 'check_pod_count')
            observed = [check_pod(p, job, spec) for p in pods]
            row['pods'] = observed
            if observed:
                require(not any(c['waiting_reason'] in ('ErrImageNeverPull', 'ErrImagePull', 'ImagePullBackOff',
                            'InvalidImageName', 'CreateContainerConfigError', 'CreateContainerError')
                            for c in observed[0]['containers']), 'local_image_start_failed')
            conditions = current.get('status', {}).get('conditions', [])
            require(not any(c.get('type') == 'Failed' and c.get('status') == 'True' for c in conditions)
                    and not any(p['phase'] == 'Failed' for p in observed), 'local_image_check_failed')
            if any(c.get('type') == 'Complete' and c.get('status') == 'True' for c in conditions):
                require(len(observed) == 1 and observed[0]['phase'] == 'Succeeded'
                        and len(observed[0]['containers']) == 1, 'check_completion_missing')
                c = observed[0]['containers'][0]
                digest = (c.get('imageID') or '').rsplit('@', 1)[-1]
                if digest.startswith('containerd://'):
                    digest = digest[len('containerd://'):]
                require(c['exit_code'] == 0 and c['restart_count'] == 0 and digest in (MANIFEST, CONFIG), 'check_image_identity')
                args = ['logs', observed[0]['pod'], '-c', 'check', '--limit-bytes=16384']
                e.approved_check_logs = args
                try:
                    value = e.H.parse(e.call('check-fixed-result', args)[0])
                finally:
                    e.approved_check_logs = None
                require(value == {'status': 'LOCAL_PRELOADED_IMAGE_EXECUTED', 'sources_verified': True,
                                  'files_verified': len(SOURCE_PINS), 'provider_requests': 0}, 'check_source_result')
                node_gate(e.get('check-recheck-nodes', ['nodes'] + sorted(NODES))['items'])
                row.update(status='PASS', observation=value)
                break
            sleep(3)
        else:
            raise Refused('local_image_check_deadline')
    require(len(checks) == 2 and {c['node'] for c in checks} == set(NODES)
            and all(c['status'] == 'PASS' for c in checks), 'two_executed_image_checks_required')


def frontend_snapshot(e, front, deployment):
    inv = e.get('frontend-inventory', ['replicasets,pods', '-l', 'app=frontend'])['items']
    service = e.get('frontend-service', ['service', 'frontend'])
    slices = e.get('frontend-endpoints', ['endpointslices', '-l', 'kubernetes.io/service-name=frontend'])['items']
    return front.ready(deployment, inv, service, slices), service






def services(e):
    result = {}
    for role in ROLES:
        d = e.get('service-preservation', ['service', role])
        result[role] = {'uid': d['metadata']['uid'], 'spec_sha256': pin(canonical(d['spec']))['sha256']}
    return result


def runtime_code():
    # Read immutable candidate files and local HTTP only. No external/provider traffic.
    return """const fs=require('fs'),crypto=require('crypto'),http=require('http');
const pins=JSON.parse(process.argv[1]), hash=b=>crypto.createHash('sha256').update(b).digest('hex');
try { for(const [p,v] of Object.entries(pins)) {
 if(fs.realpathSync(p)!==p||v.bytes>1048576)throw Error('source');
 const fd=fs.openSync(p,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);
 try { const s=fs.fstatSync(fd); if(!s.isFile()||s.size!==v.bytes)throw Error('source');
 const b=fs.readFileSync(fd),t=fs.fstatSync(fd);
 if(s.dev!==t.dev||s.ino!==t.ino||s.size!==t.size||s.mtimeMs!==t.mtimeMs||s.ctimeMs!==t.ctimeMs||hash(b)!==v.sha256)throw Error('source');
 } finally {fs.closeSync(fd)}
} } catch(e) { console.log(JSON.stringify({status:'INCOMPLETE',error_code:'candidate_source_changed'}));process.exit(1); }
function get(path,expected){return new Promise((resolve,reject)=>{
 const q=http.get({host:'127.0.0.1',port:3000,path,timeout:8000,agent:false},r=>{let n=0;const h=crypto.createHash('sha256');
 r.on('data',b=>{n+=b.length;h.update(b);if(n>(expected?expected.bytes:1048576))q.destroy(Error('limit'))});
 r.on('error',reject);r.on('end',()=>{const ok=expected?r.statusCode===200&&n===expected.bytes&&h.digest('hex')===expected.sha256:r.statusCode>=200&&r.statusCode<400&&n>0;
 if(!ok)reject(Error('http'));else resolve({http_status:r.statusCode,bytes:n})})});
 q.on('timeout',()=>q.destroy(Error('timeout')));q.on('error',reject)})}
const assets=Object.entries(pins).filter(([p])=>p.includes('/.next/static/chunks/'));
Promise.all([get('/',null),...assets.map(([p,v])=>get(p.replace('/app/frontend/.next/static/','/_next/static/'),v))]).then(rows=>{
 console.log(JSON.stringify({status:'PASS',http_status:rows[0].http_status,bytes:rows[0].bytes,sources_verified:true,static_assets_verified:assets.length,provider_requests:0}));process.exit(0)
}).catch(()=>{console.log(JSON.stringify({status:'INCOMPLETE',error_code:'local_http_failed'}));process.exit(1)});
setTimeout(()=>process.exit(1),12000).unref();"""


def deployment_ready(d):
    s = d.get('status', {})
    wanted = d['spec'].get('replicas', 1)
    return (s.get('observedGeneration') == d['metadata']['generation']
            and all(s.get(k) == wanted for k in ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'))
            and not s.get('unavailableReplicas', 0) and not s.get('terminatingReplicas', 0))


def pod_identities(pods):
    return sorted([{'uid': p['metadata']['uid'], 'pod': p['metadata']['name'],
                    'node': p['spec']['nodeName'],
                    'containers': sorted((c.get('name'), c.get('containerID'), c.get('restartCount'), c.get('imageID'))
                                         for c in p['status'].get('containerStatuses', []))}
                   for p in pods], key=lambda p: p['uid'])


def execute(e, front, sleep=time.sleep):
    r = e.report
    validate_preloads(PRELOADS)
    before = {d['metadata']['name']: d for d in e.get('before-deployments', ['deployments'] + ROLES)['items']}
    require(set(before) == set(ROLES) and binding(before['frontend']) == FRONT
            and deployment_ready(before['frontend']), 'frontend_baseline_changed')
    original = before['frontend']
    front.prepare_patch(original, original)
    r['fresh_nodes_before'] = front.node_gate(e.get('frontend-nodes', ['nodes'] + sorted(NODES))['items'])
    service_before = e.get('before-service', ['service', 'frontend'])
    service_bindings = services(e)
    r['before'] = {k: binding(v) for k, v in before.items()}
    e.write('BEFORE-DEPLOYMENTS.private.json', list(before.values()))
    e.H.write_new(e.parent, ONCE, canonical({'operation': r['operation'], 'directory': r['directory'],
        'automatic_retry': False, 'prior_frontend_receipt': PRIOR_RECEIPT, 'failed_r2': FAILED_R2,
        'preloads_sha256': pin(canonical(PRELOADS))['sha256']}) + b'\n')
    r['once_claimed'] = True
    verify_local_cache(e, sleep)
    fresh = {d['metadata']['name']: d for d in e.get('fresh-deployments', ['deployments'] + ROLES)['items']}
    require({k: binding(v) for k, v in fresh.items()} == r['before'] and services(e) == service_bindings,
            'state_changed_before_patch')
    patch = front.prepare_patch(fresh['frontend'], original)
    require(len(r.get('cache_checks', [])) == 2 and {c['node'] for c in r['cache_checks']} == set(NODES)
            and all(c['status'] == 'PASS' for c in r['cache_checks']), 'executed_cache_checks_missing')
    patched = e.patch_once(patch)
    require(patched['metadata']['generation'] == FRONT['generation'] + 1, 'frontend_generation_changed')
    r['patch_acknowledged_by_readback'] = True
    while True:
        require(time.monotonic() - e.started < SECONDS, 'frontend_rollout_deadline')
        current = e.get('observe-frontend', ['deployment', 'frontend'])
        require(binding(current) == binding(patched), 'frontend_changed_during_rollout')
        pods, service = frontend_snapshot(e, front, current)
        require(service == service_before, 'frontend_service_changed')
        if pods is not None:
            break
        sleep(5)
    assets = sum('/.next/static/chunks/' in p for p in SOURCE_PINS)
    require(assets > 0, 'static_asset_pins_missing')
    runtime = []
    for pod in pods:
        value = json.loads(e.call('frontend-source-http', ['exec', pod['metadata']['name'], '-c', 'frontend', '--',
            'node', '-e', runtime_code(), json.dumps(SOURCE_PINS)])[0])
        require(value.get('status') == 'PASS' and value.get('sources_verified') is True and value.get('provider_requests') == 0
                and value.get('static_assets_verified') == assets
                and type(value.get('http_status')) is int and 200 <= value['http_status'] < 400
                and type(value.get('bytes')) is int and 0 < value['bytes'] <= 1048576, 'frontend_runtime_failed')
        runtime.append({'pod': pod['metadata']['name'], 'uid': pod['metadata']['uid'],
                        'node': pod['spec']['nodeName'], 'observation': value})
    r['runtime'] = runtime
    final = {d['metadata']['name']: d for d in e.get('after-deployments', ['deployments'] + ROLES)['items']}
    require(set(final) == set(before) and all(binding(final[k]) == r['before'][k] for k in ROLES if k != 'frontend'),
            'unrelated_spec_changed')
    require(binding(final['frontend']) == binding(patched) and services(e) == service_bindings,
            'service_or_frontend_changed')
    stable, service = frontend_snapshot(e, front, final['frontend'])
    require(service == service_before and stable is not None and pod_identities(stable) == pod_identities(pods),
            'frontend_containers_or_endpoints_changed')
    r['fresh_nodes_after'] = front.node_gate(e.get('final-frontend-nodes', ['nodes'] + sorted(NODES))['items'])
    e.write('AFTER-DEPLOYMENTS.private.json', list(final.values()))
    r.update(status=ACCEPTED, stage_passed=True, after={k: binding(v) for k, v in final.items()},
             unrelated_specs_preserved=True, services_preserved=True, accepted_pods=runtime,
             browser_acceptance_pending=True)


def run():
    r = {'schema': 'portal-frontend-nonconfidential-notice-activation.v2', 'status': 'INCOMPLETE', 'stage_passed': False,
         'operation': uuid.uuid4().hex, 'once_claimed': False, 'patch_requested': False, 'patch_request_count': 0,
         'patch_may_have_applied': False, 'automatic_retry': False, 'automatic_rollback': False,
         'schema_migration_requested': False, 'other_workload_mutation': False, 'provider_requests': 0,
         'temporary_jobs_created': [], 'cache_checks': [], 'image_pull_policy': 'Never',
         'check_job_ttl_seconds': 3600,
         'browser_acceptance_pending': True, 'commands': [], 'prior_frontend_receipt': PRIOR_RECEIPT,
         'preload_receipts': PRELOADS, 'cache_manifest': MANIFEST, 'cache_config': CONFIG, 'source_pins': SOURCE_PINS}
    helper = parent = directory = None
    started = time.monotonic()
    try:
        require(os.name == 'posix' and os.geteuid() == 0 and sys.flags.isolated and sys.flags.dont_write_bytecode
                and socket.gethostname() == 'datax-01', 'isolated_root_datax01_required')
        require(sys.argv[1:] == ['--resume-frontend-nonconfidential-notice-once'], 'explicit_activation_required')
        validate_preloads(PRELOADS)
        front, helper = load_modules()
        parent = helper.private_parent()
        require(not os.path.lexists('/var/lib/' + ONCE), 'once_marker_exists_review_required')
        disk = os.fstatvfs(parent)
        require(disk.f_bavail * disk.f_frsize >= 256 * 1024**2, 'private_log_capacity')
        read_prior(helper)
        r['prior_noop_failure'] = read_failed_r2(helper)
        folder = 'portal-frontend-nonconfidential-notice-activation-' + r['operation']
        os.mkdir(folder, 0o700, dir_fd=parent)
        os.fsync(parent)
        directory = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        r['directory'] = '/var/lib/' + folder
        kubectl = shutil.which('kubectl', path='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin')
        require(kubectl in ('/usr/bin/kubectl', '/usr/local/bin/kubectl'), 'trusted_kubectl_path')
        for path in (kubectl, os.path.dirname(kubectl)):
            st = os.lstat(path)
            require(st.st_uid == 0 and not st.st_mode & 0o022 and not stat.S_ISLNK(st.st_mode), 'trusted_kubectl_permissions')
        e = make_execution(front)(helper, directory, kubectl, r)
        e.parent = parent
        execute(e, front)
    except BaseException as error:
        code = str(error)
        r.update(status='INCOMPLETE', stage_passed=False, error_class=type(error).__name__,
                 error_code=code if isinstance(error, Refused) and re.fullmatch('[a-z0-9_]{1,120}', code) else 'details_withheld')
    finally:
        r['elapsed_seconds'] = round(time.monotonic() - started, 3)
        if directory is not None:
            try:
                raw = canonical(r) + b'\n'
                helper.write_new(directory, 'REPORT.json', raw)
                r.update(report_path=r['directory'] + '/REPORT.json', report_bytes=len(raw), report_sha256=pin(raw)['sha256'])
            except BaseException:
                r.update(status='INCOMPLETE', stage_passed=False, error_code='report_persistence_failed')
        for fd in (directory, parent):
            if fd is not None:
                os.close(fd)
    output = {k: v for k, v in r.items() if k not in ('commands', 'backend_before', 'backend_after', 'preload_receipts', 'source_pins')}
    output['exitcode'] = 0 if r['stage_passed'] else 1
    print(json.dumps(output, sort_keys=True), flush=True)
    return output['exitcode']


if __name__ == '__main__':
    sys.exit(run())

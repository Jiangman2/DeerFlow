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
BACKEND_COMMON = ''
PRELOADS = {}
BACKEND = {}
SOURCE_PINS = {}
PROVENANCE = {}

FRONT = {'uid': 'bb0b3f6e-31f7-4073-8b76-7650ea5b96ee', 'generation': 225,
         'spec_sha256': '1d3af5d2091bbb29fb4a45a3743acc03fbff9bf190c23c67b5bc43bb4fc37123'}
MANIFEST = 'sha256:15d1327740e688faf0617d1d8fb9f662618a963624ba7280a6d0efb54ed70475'
CONFIG = 'sha256:377bac0b69157c9cd0b1514ca986343381a6541ef30135127df4876f350d5ffc'
OLD = 'deerflow.local/deer-flow-frontend@sha256:ee145e6357e4f6b63003d786b7c6b0f28cf6bbe663e1b5589f92f7c829bb89e5'
NEW = 'deerflow.local/deer-flow-frontend@' + MANIFEST
TAG = 'deerflow.local/deer-flow-frontend:20261021-data-qa-progress-r1'
NODES = {'datax-02': '3148d4c1-6a97-42ed-a78c-950dcea5cbec',
         'ecs-zdz-pro-0015': 'de6ee5e8-dbfb-4272-9ebf-ae82182db085'}
ONCE = 'portal-frontend-data-qa-progress-activation-20261022-r1.once'
ACCEPTED = 'FRONTEND_DATA_QA_PROGRESS_RUNTIME_ACCEPTED_BROWSER_PENDING'
BACKEND_ACCEPTED = 'GATEWAY_OA_DATA_QA_RUNTIME_ACCEPTED_FRONTEND_AND_OA_PENDING'
TARGET = '0021_oa_result_sets'
SECONDS = 1800
ROLES = ['frontend', 'gateway', 'ppt-master-runner', 'provisioner', 'nginx']
PRELOAD_PATHS = {n: ('/mnt/data' if n == 'ecs-zdz-pro-0015' else '/var/lib') +
    '/portal-frontend-data-qa-progress-preload-20261022-r2/REPORT.json' for n in NODES}


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


def validate_backend_pin(value):
    require(type(value) is dict and value.get('path') == '/var/lib/portal-gateway-oa-data-qa-activation-20261022-r1/REPORT.json'
            and type(value.get('bytes')) is int and 0 < value['bytes'] <= 1024**2
            and re.fullmatch('[a-f0-9]{64}', value.get('sha256', '')), 'backend_report_pin_required')


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
    require(FRONT_COMMON and BACKEND_COMMON and SOURCE_PINS and PROVENANCE, 'package_incomplete')
    front = types.ModuleType('_proven_frontend_activation')
    exec(compile(FRONT_COMMON, '<proven-frontend-helper>', 'exec'), front.__dict__)
    front.require = require
    front.UID, front.OLD, front.OLD_SPEC = FRONT['uid'], OLD, FRONT['spec_sha256']
    front.MANIFEST, front.CONFIG, front.NEW, front.TAG, front.NODES = MANIFEST, CONFIG, NEW, TAG, NODES
    front.SPEC = None
    require(pin(front.HELPER) == front.HELPER_PIN, 'embedded_bounded_helper_changed')
    helper = types.ModuleType('_bounded_helper')
    exec(compile(front.HELPER, '<bounded-helper>', 'exec'), helper.__dict__)
    backend = types.ModuleType('_accepted_backend_checks')
    exec(compile(BACKEND_COMMON, '<accepted-backend-checks>', 'exec'), backend.__dict__)
    return front, helper, backend


def backend_gate(report, backend):
    require(report.get('status') == BACKEND_ACCEPTED and report.get('runtime_accepted') is True
            and report.get('feature_image_deployed') is True and report.get('v2_enabled') is False
            and all(report.get(k) is True for k in ('frontend_preserved', 'runner_preserved',
                'configuration_metadata_preserved', 'gateway_other_spec_preserved', 'patch_acknowledged')),
            'backend_acceptance_required')
    require(report.get('patch_request_count') == 1 and report.get('schema_migration_requested') is False
            and report.get('schema_revision') == TARGET and report.get('provider_requests') == 0
            and report.get('oa_callback_requests') == 0 and report.get('automatic_retry') is False,
            'backend_acceptance_scope')
    before, after = report.get('before', {}), report.get('after', {})
    old_bindings = {role: {k: p[k] for k in ('uid', 'generation', 'spec_sha256')}
                    for role, p in backend.EXPECTED.items()}
    require(before.get('deployments') == old_bindings and before.get('frontend') == FRONT
            and after.get('frontend') == FRONT and after.get('deployments', {}).get('ppt-master-runner') == old_bindings['ppt-master-runner']
            and old_bindings['ppt-master-runner']['generation'] == 55, 'backend_baseline_changed')
    gateway = after.get('deployments', {}).get('gateway', {})
    require(gateway == report.get('gateway_after') and gateway.get('uid') == old_bindings['gateway']['uid']
            and gateway.get('generation') == old_bindings['gateway']['generation'] + 1 == 384
            and re.fullmatch('[a-f0-9]{64}', gateway.get('spec_sha256', '')), 'gateway_acceptance_identity')
    require(all(before.get(k) == after.get(k) for k in ('configuration_metadata', 'services', 'node_coverage')),
            'backend_preservation_changed')
    runtime = report.get('runtime', [])
    require(len(runtime) == 70 and len({x.get('uid') for x in runtime}) == 70
            and len({x.get('pod') for x in runtime}) == 70, 'all_gateway_runtime_required')
    for row in runtime:
        obs = row.get('observation', {})
        require(obs.get('status') == 'PASS' and obs.get('sources_verified') is True
                and obs.get('v2_enabled') is False and obs.get('local_health_http_status') == 200
                and obs.get('scheduler_budget') == 300 and obs.get('oa_budget') == 1000
                and obs.get('provider_requests') == obs.get('oa_callback_requests') == 0,
                'gateway_runtime_not_accepted')
    for key in ('database_after', 'runner_compatibility_after'):
        obs = report.get(key, {})
        require(obs.get('status') == 'PASS' and obs.get('database_readonly') is True
                and obs.get('database_fingerprint') == backend.DATABASE_FINGERPRINT
                and (obs.get('revisions') == [TARGET] if key == 'database_after' else obs.get('schema_revision') == TARGET),
                'backend_schema_not_accepted')
    return after


def read_backend(helper):
    validate_backend_pin(BACKEND)
    path = BACKEND['path']
    require(os.path.realpath(path) == path, 'backend_report_symlink')
    for directory in ('/var', '/var/lib', os.path.dirname(path)):
        st = os.lstat(directory)
        require(stat.S_ISDIR(st.st_mode) and st.st_uid == 0 and not st.st_mode & 0o022, 'backend_directory_permissions')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and not before.st_mode & 0o077
                and before.st_nlink == 1 and before.st_size == BACKEND['bytes'], 'backend_report_metadata')
        raw = stream.read(BACKEND['bytes'] + 1)
        after = os.fstat(stream.fileno())
        require((before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'backend_report_changed')
    require(pin(raw) == {k: BACKEND[k] for k in ('bytes', 'sha256')}, 'backend_report_hash')
    return helper.parse(raw)


def make_execution(front):
    class Execution(front.Execution):
        def call(self, phase, args, pass_fds=(), must_succeed=True):
            require(args and args[0] in ('get', 'exec', 'patch'), 'command_forbidden')
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

    return Execution


def frontend_snapshot(e, front, deployment):
    inv = e.get('frontend-inventory', ['replicasets,pods', '-l', 'app=frontend'])['items']
    service = e.get('frontend-service', ['service', 'frontend'])
    slices = e.get('frontend-endpoints', ['endpointslices', '-l', 'kubernetes.io/service-name=frontend'])['items']
    return front.ready(deployment, inv, service, slices), service


def backend_live(e, backend, accepted):
    def command(args, value=None, raw=False):
        require(value is None and args[0] in ('get', 'exec'), 'backend_mutation_forbidden')
        result = e.call('backend-preservation-' + args[0], args)[0]
        return result if raw else json.loads(result)
    backend.command = command
    deployments = {role: e.get('backend-deployment', ['deployment', role]) for role in backend.EXPECTED}
    require({k: binding(v) for k, v in deployments.items()} == accepted['deployments']
            and all(backend.ready(d) for d in deployments.values()), 'accepted_backend_changed')
    pods = {role: backend.checked_pods(d, role, feature=role == 'gateway') for role, d in deployments.items()}
    for role in pods:
        backend.endpoints(role, pods[role])
    database = backend.feature_check(pods['gateway'][0], 'database')
    runner = backend.schema_check(pods['ppt-master-runner'][0], 'ppt-master-runner')
    return {'deployments': {k: binding(v) for k, v in deployments.items()},
            'pods': {k: backend.identities(v) for k, v in pods.items()},
            'database': database, 'runner': runner}


def metadata(e, backend, deployments):
    return backend.configuration_metadata(backend.configuration_refs(deployments))


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


def execute(e, front, backend, accepted, sleep=time.sleep):
    r = e.report
    validate_preloads(PRELOADS)
    before = {x['metadata']['name']: x for x in e.get('before-deployments', ['deployments'] + ROLES)['items']}
    require(set(before) == set(ROLES) and binding(before['frontend']) == FRONT and backend.ready(before['frontend']), 'frontend_baseline_changed')
    original = before['frontend']
    patch = front.prepare_patch(original, original)
    r['fresh_nodes_before'] = front.node_gate(e.get('frontend-nodes', ['nodes'] + sorted(NODES))['items'])
    r['backend_before'] = backend_live(e, backend, accepted)
    config_before = metadata(e, backend, before)
    require(all(config_before.get(k) == v for k, v in accepted['configuration_metadata'].items()), 'accepted_configuration_changed')
    service_before = e.get('before-service', ['service', 'frontend'])
    service_bindings = services(e)
    require(all(service_bindings.get(k) == v for k, v in accepted['services'].items()), 'accepted_services_changed')
    r['before'] = {k: binding(v) for k, v in before.items()}
    e.write('BEFORE-DEPLOYMENTS.private.json', list(before.values()))
    fresh = {x['metadata']['name']: x for x in e.get('fresh-deployments', ['deployments'] + ROLES)['items']}
    require({k: binding(v) for k, v in fresh.items()} == r['before']
            and metadata(e, backend, fresh) == config_before and services(e) == service_bindings, 'state_changed_before_patch')
    patch = front.prepare_patch(fresh['frontend'], original)
    e.H.write_new(e.parent, ONCE, canonical({'operation': r['operation'], 'directory': r['directory'],
        'automatic_retry': False, 'backend_receipt': BACKEND, 'preloads_sha256': pin(canonical(PRELOADS))['sha256']}) + b'\n')
    r['once_claimed'] = True
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
    runtime = []
    for pod in pods:
        value = json.loads(e.call('frontend-source-http', ['exec', pod['metadata']['name'], '-c', 'frontend', '--',
            'node', '-e', runtime_code(), json.dumps(SOURCE_PINS)])[0])
        require(value.get('status') == 'PASS' and value.get('sources_verified') is True and value.get('provider_requests') == 0
                and value.get('static_assets_verified') == 2
                and type(value.get('http_status')) is int and 200 <= value['http_status'] < 400
                and type(value.get('bytes')) is int and 0 < value['bytes'] <= 1048576, 'frontend_runtime_failed')
        runtime.append({'pod': pod['metadata']['name'], 'uid': pod['metadata']['uid'], 'node': pod['spec']['nodeName'], 'observation': value})
    r['runtime'] = runtime
    after_backend = backend_live(e, backend, accepted)
    require(after_backend['pods'] == r['backend_before']['pods'], 'backend_containers_changed')
    final = {x['metadata']['name']: x for x in e.get('after-deployments', ['deployments'] + ROLES)['items']}
    require(set(final) == set(before) and all(binding(final[k]) == r['before'][k] for k in ROLES if k != 'frontend'),
            'unrelated_spec_changed')
    require(binding(final['frontend']) == binding(patched) and metadata(e, backend, final) == config_before and services(e) == service_bindings,
            'configuration_or_frontend_changed')
    stable, service = frontend_snapshot(e, front, final['frontend'])
    require(service == service_before and stable is not None
            and backend.identities(stable) == backend.identities(pods), 'frontend_containers_or_endpoints_changed')
    r['fresh_nodes_after'] = front.node_gate(e.get('final-frontend-nodes', ['nodes'] + sorted(NODES))['items'])
    e.write('AFTER-DEPLOYMENTS.private.json', list(final.values()))
    r.update(status=ACCEPTED, stage_passed=True, after={k: binding(v) for k, v in final.items()},
             backend_after=after_backend, configuration_metadata_preserved=True, unrelated_specs_preserved=True,
             schema_revision=TARGET, runner_preserved=True, accepted_pods=runtime, browser_acceptance_pending=True)


def run():
    r = {'schema': 'portal-frontend-data-qa-progress-activation.v1', 'status': 'INCOMPLETE', 'stage_passed': False,
         'operation': uuid.uuid4().hex, 'once_claimed': False, 'patch_requested': False, 'patch_request_count': 0,
         'patch_may_have_applied': False, 'automatic_retry': False, 'automatic_rollback': False,
         'schema_migration_requested': False, 'other_workload_mutation': False, 'provider_requests': 0,
         'browser_acceptance_pending': True, 'commands': [], 'backend_receipt': BACKEND,
         'preload_receipts': PRELOADS, 'cache_manifest': MANIFEST, 'cache_config': CONFIG, 'source_pins': SOURCE_PINS}
    helper = parent = directory = None
    started = time.monotonic()
    try:
        require(os.name == 'posix' and os.geteuid() == 0 and sys.flags.isolated and sys.flags.dont_write_bytecode
                and socket.gethostname() == 'datax-01', 'isolated_root_datax01_required')
        require(sys.argv[1:] == ['--activate-frontend-data-qa-progress-once'], 'explicit_activation_required')
        validate_preloads(PRELOADS)
        validate_backend_pin(BACKEND)
        front, helper, backend = load_modules()
        parent = helper.private_parent()
        require(not os.path.lexists('/var/lib/' + ONCE), 'once_marker_exists_review_required')
        disk = os.fstatvfs(parent)
        require(disk.f_bavail * disk.f_frsize >= 256 * 1024**2, 'private_log_capacity')
        accepted = backend_gate(read_backend(helper), backend)
        folder = 'portal-frontend-data-qa-progress-activation-' + r['operation']
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
        execute(e, front, backend, accepted)
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

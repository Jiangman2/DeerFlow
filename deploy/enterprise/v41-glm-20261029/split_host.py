"""Two independently invoked phases: stage unused configuration, then roll later."""
import argparse
import base64
import json
import os
from pathlib import Path
import socket
import stat
import types

MODE = None
CORE = None
LIBRARY = None
CHILD = None
BUNDLE_ID = None
PREPARE_DIR = '/var/lib/portal-v41-glm-preparation-20261029-r1'
ROLLOUT_DIR = '/var/lib/portal-v41-glm-rollout-20261029-r1'
LEGACY_DIR = '/var/lib/portal-v41-glm-activation-20261029-r1'
PREPARED_STATUS = 'V41_CONFIG_PREPARED_NOT_ACTIVATED'


def private_read(path, cap=2097152):
    # The stage is local root-owned state, not an instruction from an attachment.
    parent = path.parent.lstat()
    if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o077:
        raise RuntimeError('UNTRUSTED_PREPARATION_DIRECTORY')
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o077 or s.st_size > cap:
            raise RuntimeError('UNTRUSTED_PREPARATION_FILE')
        with os.fdopen(fd, 'rb', closefd=False) as handle: raw = handle.read(cap + 1)
        end = os.fstat(fd)
        if len(raw) != s.st_size or (s.st_ino, s.st_mtime_ns, s.st_size) != (end.st_ino, end.st_mtime_ns, end.st_size):
            raise RuntimeError('PREPARATION_FILE_CHANGED')
        return raw
    finally: os.close(fd)


def snapshot(reader, core):
    before = {name: reader.get('deployment', name) for name in core.BASELINES}
    core.require({n: core.binding(o) for n, o in before.items()} == core.BASELINES, 'DEPLOYMENT_BASELINE_CHANGED')
    gateway = before['gateway']
    core.require(core.stable(gateway, 389), 'GATEWAY_NOT_STABLE')
    _, old_name = core.config_volume(gateway)
    old_cm = reader.resource('configmap', old_name)
    core.require(core.digest(old_cm.get('data', {}).get('config.yaml', '').encode()) == core.BASE_CONFIG_SHA,
                 'SOURCE_CONFIGMAP_CHANGED')
    services = {name: reader.resource('service', name) for name in ('gateway', 'nginx', 'frontend')}
    return before, gateway, old_name, old_cm, services


def prepare_candidate(reader, core, lib, gateway):
    sample = lib.select_pod(reader, gateway)
    prepared = core.probe(reader, lib, sample, 'prepare', core.BASE_CONFIG_SHA)
    raw = base64.b64decode(prepared.pop('private_candidate_b64'), validate=True)
    core.require(core.digest(raw) == prepared['config_sha256'], 'CANDIDATE_DIGEST_CHANGED')
    core.require(lib.identity(reader.get('pod', sample['metadata']['name'])) == lib.identity(sample), 'SAMPLE_CHANGED')
    return prepared, raw


def unchanged(reader, core, before, old_name, old_cm, services):
    core.require(all(core.binding(reader.get('deployment', n)) == core.binding(o) for n, o in before.items()),
                 'DEPLOYMENT_CHANGED_DURING_PREPARATION')
    core.require(core.cm_binding(reader.resource('configmap', old_name)) == core.cm_binding(old_cm), 'SOURCE_CONFIGMAP_DRIFT')
    core.require(all(core.binding(reader.resource('service', n)) == core.binding(o) for n, o in services.items()), 'SERVICE_DRIFT')


def claim(core, result):
    os.mkdir(core.CONTROL, 0o700)
    result['once_claimed'] = True
    core.save(result)


def prepare(reader, core, lib, result):
    result['provider_receipt'] = core.read_receipt(lib)
    before, gateway, old_name, old_cm, services = snapshot(reader, core)
    print(json.dumps({'phase': 'VALIDATE_V41_CONFIG_WITHOUT_ROLLOUT'}), flush=True)
    prepared, raw = prepare_candidate(reader, core, lib, gateway)
    cm = core.candidate_cm(old_cm, raw)
    candidate_file = core.packed(cm)
    snapshot_file = core.packed({'deployments': before, 'configmap': old_cm, 'services': services})
    result.update(candidate_configmap=cm['metadata']['name'], candidate_config_sha256=core.digest(raw),
                  source_configmap=old_name, source_configmap_binding=core.cm_binding(old_cm),
                  deployment_bindings={n: core.binding(o) for n, o in before.items()},
                  service_bindings={n: core.binding(o) for n, o in services.items()},
                  private_file_hashes={'candidate.private.json': core.digest(candidate_file),
                                       'before.private.json': core.digest(snapshot_file)}, prepared=prepared)
    claim(core, result)
    core.private_write('before.private.json', snapshot_file)
    core.private_write('candidate.private.json', candidate_file)
    unchanged(reader, core, before, old_name, old_cm, services)
    result['configmap_create_requested'] = True; core.save(result)
    reader.read(lib.BASE + ['create', '-f', str(Path(core.CONTROL) / 'candidate.private.json'), '-o', 'name'],
                timeout=30, cap=65536, step='create-unused-immutable-v41-configmap-once')
    created = reader.resource('configmap', cm['metadata']['name'])
    core.require(created.get('immutable') is True and created.get('data') == cm['data'] and
                 created.get('binaryData') == cm.get('binaryData'), 'CANDIDATE_READBACK_REJECTED')
    result['candidate_configmap_binding'] = core.cm_binding(created)
    unchanged(reader, core, before, old_name, old_cm, services)
    result.update(status=PREPARED_STATUS, preparation_accepted=True, v41_enabled=False,
                  gateway_rollout_required=True, gateway_rollout_started=False, workload_modified=False,
                  existing_configmap_preserved=True, protected_deployments_preserved=True,
                  activation_not_authorized_by_preparation=True)
    core.save(result)


def load_preparation(core):
    raw = private_read(Path(PREPARE_DIR) / 'REPORT.json')
    report = json.loads(raw)
    core.require(report.get('schema') == 'portal-v41-glm-preparation.v1' and report.get('status') == PREPARED_STATUS and
                 report.get('preparation_accepted') is True and report.get('bundle_id') == BUNDLE_ID and
                 report.get('v41_enabled') is False and report.get('patch_request_count') == 0,
                 'PREPARATION_NOT_ACCEPTED_OR_TOOL_CHANGED')
    core.require(report.get('deployment_bindings') == core.BASELINES, 'PREPARED_BASELINE_CHANGED')
    files = {}
    for name in ('candidate.private.json', 'before.private.json'):
        content = private_read(Path(PREPARE_DIR) / name)
        core.require(core.digest(content) == report.get('private_file_hashes', {}).get(name), 'PREPARATION_DIGEST_CHANGED')
        files[name] = json.loads(content)
    return report, files, {'path': PREPARE_DIR + '/REPORT.json', 'bytes': len(raw), 'sha256': core.digest(raw)}


def rollout(reader, core, lib, result):
    stage, files, receipt = load_preparation(core)
    result['preparation_receipt'] = receipt
    result['provider_receipt'] = core.read_receipt(lib)
    before, gateway, old_name, old_cm, services = snapshot(reader, core)
    core.require(old_name == stage['source_configmap'] and core.cm_binding(old_cm) == stage['source_configmap_binding'],
                 'PREPARED_SOURCE_CONFIGMAP_CHANGED')
    core.require({n: core.binding(o) for n, o in services.items()} == stage['service_bindings'], 'PREPARED_SERVICE_CHANGED')
    prepared, raw = prepare_candidate(reader, core, lib, gateway)
    cm = core.candidate_cm(old_cm, raw)
    core.require(cm == files['candidate.private.json'] and core.digest(raw) == stage['candidate_config_sha256'] and
                 cm['metadata']['name'] == stage['candidate_configmap'], 'PREPARED_CANDIDATE_CHANGED')
    name = cm['metadata']['name']; created = reader.resource('configmap', name)
    core.require(core.cm_binding(created) == stage['candidate_configmap_binding'] and created.get('immutable') is True and
                 created.get('data') == cm['data'] and created.get('binaryData') == cm.get('binaryData'), 'PREPARED_CONFIGMAP_CHANGED')
    # Recompute the CAS using the current resourceVersion, never the preparation's stale RV.
    expected, patch = core.plan(gateway, name)
    result.update(before=core.binding(gateway), candidate_configmap=name, candidate_config_sha256=core.digest(raw),
                  source_configmap=old_name, candidate_configmap_uid=created['metadata']['uid'], prepared=prepared)
    claim(core, result)
    core.private_write('before.private.json', core.packed({'deployments': before, 'configmap': old_cm, 'services': services}))
    core.private_write('patch.private.json', core.packed(patch))
    core.private_write('planned-spec.private.json', core.packed(expected))
    dry = json.loads(reader.read(lib.BASE + ['patch', 'deployment', 'gateway', '--type=json',
        '--patch-file=' + str(Path(core.CONTROL) / 'patch.private.json'), '--dry-run=server', '-o', 'json'],
        timeout=30, step='gateway-server-dryrun'))
    core.require(dry['spec'] == expected, 'ADMISSION_MUTATES_PLAN')
    result['server_dryrun_accepted'] = True
    unchanged(reader, core, before, old_name, old_cm, services)
    core.require(core.cm_binding(reader.resource('configmap', name)) == core.cm_binding(created), 'CANDIDATE_CONFIGMAP_DRIFT')
    print(json.dumps({'phase': 'ROLL_GATEWAY_TO_PREPARED_V41_CONFIG', 'desired_replicas': 70}), flush=True)
    result['gateway_rollout_started'] = True; result['workload_modified'] = None
    after = core.patch_once(reader, lib, patch, expected, result)
    result['workload_modified'] = True
    core.observe_rollout(reader, lib, result, gateway, old_name, old_cm, services, name, raw, created, expected, after)


def allowed_command(core, lib, args):
    """Deny a workload mutation from the preparation executable, even by mistake."""
    core.require(args[:len(lib.BASE)] == lib.BASE, 'KUBECTL_SCOPE_CHANGED')
    tail = args[len(lib.BASE):]
    if tail and tail[0] == 'get':
        core.require(len(tail) > 1 and tail[1] in ('deployment', 'pod', 'pods', 'replicaset', 'service', 'configmap', 'endpointslices'),
                     'READ_RESOURCE_SCOPE')
        return
    if tail and tail[0] == 'exec':
        core.require(len(tail) == 12 and tail[:2] == ['exec', '-i'] and lib.valid_name(tail[2]) and
                     tail[3:] == ['-c', 'gateway', '--', '/app/backend/.venv/bin/python', '-I', '-B', '-u', '-c', CHILD],
                     'CONFIG_PROBE_EXEC_SCOPE')
        return
    if MODE == 'prepare':
        permitted = ['create', '-f', str(Path(core.CONTROL) / 'candidate.private.json'), '-o', 'name']
        core.require(tail == permitted, 'PREPARATION_MAY_NOT_MUTATE_WORKLOAD')
        cm = json.loads((Path(core.CONTROL) / 'candidate.private.json').read_bytes())
        core.require(cm.get('apiVersion') == 'v1' and cm.get('kind') == 'ConfigMap' and
                     cm.get('immutable') is True and cm['metadata'].get('namespace') == 'deer-flow' and
                     cm['metadata'].get('name', '').startswith('deer-flow-config-v41-glm-20261029-'),
                     'PREPARATION_CREATE_SCOPE')
    else:
        core.require(tail[:3] == ['patch', 'deployment', 'gateway'],
                     'ROLLOUT_COMMAND_SCOPE')


def initialize():
    core = types.ModuleType('sealed_config_core'); exec(compile(CORE, '<core>', 'exec'), core.__dict__)
    lib = types.ModuleType('sealed_reader'); exec(compile(LIBRARY, '<reader>', 'exec'), lib.__dict__)
    core.CHILD = CHILD
    core.CONTROL = PREPARE_DIR if MODE == 'prepare' else ROLLOUT_DIR
    class API(lib.Reader):
        def read(self, args, *a, **kw):
            allowed_command(core, lib, args)
            return super().read(args, *a, **kw)
        def resource(self, kind, name):
            core.require(kind in ('service', 'configmap') and lib.valid_name(name), 'NAMED_RESOURCE_REQUIRED')
            return json.loads(self.read(lib.BASE + ['get', kind, name, '--show-managed-fields=false', '-o', 'json'],
                                       step='get-' + kind + ':' + name))
    return core, lib, API()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    flag = '--prepare-v41-config-only-once' if MODE == 'prepare' else '--rollout-prepared-v41-once'
    parser.add_argument(flag, required=True, action='store_true'); parser.parse_args()
    result = dict(schema='portal-v41-glm-' + ('preparation' if MODE == 'prepare' else 'rollout') + '.v1',
        status='INCOMPLETE', phase=MODE, bundle_id=BUNDLE_ID, once_claimed=False,
        automatic_retry=False, automatic_rollback=False, provider_requests=0, secret_api_reads=0,
        credentials_exported=False, database_access_by_tool=False, default_model_changed=False,
        image_changed=False, other_workload_mutation=False, workload_modified=False,
        patch_requested=False, patch_may_have_applied=False, patch_request_count=0,
        configmap_create_requested=False, gateway_rollout_started=False, v41_enabled=False)
    core = None
    try:
        if socket.gethostname().split('.')[0] != 'datax-01' or os.geteuid() != 0: raise RuntimeError('WRONG_HOST_OR_UID')
        if not all(isinstance(v, str) for v in (CORE, LIBRARY, CHILD, BUNDLE_ID)): raise RuntimeError('PACKAGED_TOOL_REQUIRED')
        if os.path.lexists(LEGACY_DIR): raise RuntimeError('LEGACY_ACTIVATION_EXISTS_REVIEW')
        control = PREPARE_DIR if MODE == 'prepare' else ROLLOUT_DIR
        if os.path.lexists(control): raise RuntimeError('ONCE_ALREADY_CLAIMED')
        if MODE == 'prepare' and os.path.lexists(ROLLOUT_DIR): raise RuntimeError('ROLLOUT_JOURNAL_ALREADY_EXISTS')
        os.umask(0o077)
        core, lib, reader = initialize(); result['commands'] = reader.observations
        if MODE == 'prepare': prepare(reader, core, lib, result)
        else: rollout(reader, core, lib, result)
    except Exception as exc:
        codes = {'WRONG_HOST_OR_UID', 'PACKAGED_TOOL_REQUIRED', 'LEGACY_ACTIVATION_EXISTS_REVIEW',
                 'ONCE_ALREADY_CLAIMED', 'ROLLOUT_JOURNAL_ALREADY_EXISTS', 'UNTRUSTED_PREPARATION_DIRECTORY',
                 'UNTRUSTED_PREPARATION_FILE', 'PREPARATION_FILE_CHANGED'}
        result.update(error_class=type(exc).__name__, error_code=str(exc) if type(exc).__name__ == 'Refused' or
                      str(exc) in codes else 'SPLIT_PHASE_FAILED', raw_errors_exported=False)
    if result['patch_may_have_applied'] and not result.get('runtime_accepted'):
        result['v41_enablement_requires_readback'] = True
    if core:
        core.save(result)
        if result['once_claimed']:
            raw = (Path(core.CONTROL) / 'REPORT.json').read_bytes()
            result.update(report_path=core.CONTROL + '/REPORT.json', report_bytes=len(raw), report_sha256=core.digest(raw))
    public = {k: v for k, v in result.items() if k not in ('commands', 'accepted_pods')}
    public['commands_executed'] = len(result.get('commands', []))
    if result.get('commands') and result['status'] == 'INCOMPLETE': public['last_command'] = result['commands'][-1]
    print(json.dumps(public, sort_keys=True))
    return 0 if result.get('preparation_accepted') or result.get('runtime_accepted') else 1


if __name__ == '__main__': raise SystemExit(main())

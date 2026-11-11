#!/usr/bin/env python3
"""One OA timeout environment change on Gateway 403; Python 3.7 host compatible.

No image/source changes, business SQL, callback requests, or generation replay.
An environment change starts the deployment's existing rolling-update strategy.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import threading
import time

IMAGE = 'deerflow.local/deer-flow-gateway@sha256:ebcde83d011eb0bb5f2babae7ccb3a5e782839801da5b156f2c0a5c971f029f7'
UID = 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1'
KEY = 'OA_POLICY_FLOW_RUN_TIMEOUT_SECONDS'
ROOT = '/var/lib/portal-oa-run-timeout-60m-20261103-r1'
BASE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=15s', '-n', 'deer-flow']
PROBE = r'''
import json, runpy
try:
    ns = runpy.run_path('/app/backend/app/gateway/oa_policy_flow_config.py')
    c = ns['load_oa_policy_flow_config']()
    print(json.dumps({'status':'PASS','run_timeout_seconds':c.run_timeout_seconds,
        'callback_timeout_seconds':c.callback_timeout_seconds,
        'callback_enabled':c.callback_enabled,'oa_enabled':c.enabled,
        'application_started':False,'database_access':False,'callback_requests':0}))
except Exception as exc:
    print(json.dumps({'status':'INCOMPLETE','error_class':type(exc).__name__}))
'''
POD_PATH = ('{range .items[*]}{.metadata.name}{"\\t"}{.metadata.uid}{"\\t"}'
            '{.metadata.ownerReferences[?(@.controller==true)].name}{"\\t"}'
            '{.metadata.ownerReferences[?(@.controller==true)].uid}{"\\t"}'
            '{.status.phase}{"\\t"}{.metadata.deletionTimestamp}{"\\t"}'
            '{.status.containerStatuses[?(@.name=="gateway")].ready}{"\\t"}'
            '{.status.containerStatuses[?(@.name=="gateway")].restartCount}{"\\t"}'
            '{.status.containerStatuses[?(@.name=="gateway")].containerID}{"\\t"}'
            '{.spec.containers[?(@.name=="gateway")].image}{"\\n"}{end}')


class Refused(Exception):
    pass


def require(condition, code):
    if not condition:
        raise Refused(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8')


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def query(args, payload=None, timeout=25, limit=1024 * 1024, parsed=True):
    # Never put a deployment spec or credentials into a process argument/log.
    require(payload is None or len(payload) <= 1024 * 1024, 'PATCH_SIZE_BOUND')
    process = subprocess.Popen(BASE + args, stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output = [bytearray(), bytearray()]
    exceeded = threading.Event()

    def drain(pipe, index):
        while True:
            block = pipe.read(16384)
            if not block:
                return
            if len(output[index]) + len(block) > limit:
                exceeded.set()
                process.kill()
                return
            output[index].extend(block)

    def feed():
        try:
            process.stdin.write(payload)
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass

    threads = [threading.Thread(target=drain, args=(pipe, i), daemon=True)
               for i, pipe in enumerate((process.stdout, process.stderr))]
    if payload is not None:
        threads.append(threading.Thread(target=feed, daemon=True))
    for thread in threads:
        thread.start()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)
        raise Refused('COMMAND_TIMEOUT_OUTCOME_REVIEW_REQUIRED')
    finally:
        for thread in threads:
            thread.join(timeout=3)
        process.stdout.close()
        process.stderr.close()
    require(not exceeded.is_set() and not any(t.is_alive() for t in threads), 'COMMAND_OUTPUT_BOUND')
    require(process.returncode == 0, 'KUBERNETES_COMMAND_FAILED')
    return json.loads(output[0]) if parsed else output[0].decode('utf-8')


def stable(dep):
    spec, status, meta = dep['spec'], dep.get('status', {}), dep['metadata']
    return (status.get('observedGeneration') == meta['generation']
            and all(status.get(k, 0) == spec['replicas'] for k in
                    ('replicas', 'updatedReplicas', 'readyReplicas', 'availableReplicas'))
            and not status.get('unavailableReplicas') and not status.get('terminatingReplicas'))


def make_plan(dep):
    meta, spec = dep['metadata'], dep['spec']
    require(dep.get('kind') == 'Deployment' and meta.get('name') == 'gateway'
            and meta.get('namespace') == 'deer-flow' and meta.get('uid') == UID
            and meta.get('generation') == 403, 'GATEWAY_403_BASELINE_REQUIRED')
    require(spec.get('replicas') == 70 and not spec.get('paused') and stable(dep), 'BASELINE_NOT_STABLE')
    require(spec.get('strategy', {}).get('type') == 'RollingUpdate', 'ROLLING_UPDATE_REQUIRED')
    containers = spec['template']['spec']['containers']
    indexes = [i for i, c in enumerate(containers) if c.get('name') == 'gateway']
    require(len(indexes) == 1, 'EXACT_GATEWAY_CONTAINER_REQUIRED')
    index = indexes[0]
    container = containers[index]
    require(container.get('image') == IMAGE, 'BASE_IMAGE_CHANGED')
    env = container.get('env', [])
    require(isinstance(env, list) and all(isinstance(v, dict) and isinstance(v.get('name'), str) for v in env), 'ENV_INVALID')
    require(len({v['name'] for v in env}) == len(env), 'DUPLICATE_ENV')
    prior = [v for v in env if v['name'] == KEY]
    require(not prior or prior == [{'name': KEY, 'value': '1800'}], 'TIMEOUT_OVERRIDE_CHANGED')
    after = copy.deepcopy(spec)
    changed = after['template']['spec']['containers'][index]
    if prior:
        changed['env'] = [dict(name=KEY, value='3600') if v['name'] == KEY else v for v in changed['env']]
    else:
        changed['env'] = list(env) + [dict(name=KEY, value='3600')]
    require(isinstance(meta.get('resourceVersion'), str) and meta['resourceVersion'], 'RESOURCE_VERSION_REQUIRED')
    patch = [dict(op='test', path='/metadata/uid', value=UID),
             dict(op='test', path='/metadata/generation', value=meta['generation']),
             dict(op='test', path='/spec', value=copy.deepcopy(spec)),
             dict(op='add', path='/spec/template/spec/containers/{}/env'.format(index), value=changed['env'])]
    receipt = {'schema':'portal-oa-timeout-plan.v1', 'uid':UID, 'image':IMAGE,
               'before_generation':403, 'after_generation':404, 'replicas':70,
               'before_spec_sha256':digest(spec), 'after_spec_sha256':digest(after),
               'changed_variable':KEY, 'before_explicit_value':'1800' if prior else None,
               'expected_effective_before':1800, 'expected_effective_after':3600,
               'images_changed':False, 'replicas_changed':False, 'strategy_changed':False,
               'callback_setting_changed':False, 'application_sources_changed':False}
    return receipt, patch


def pods(dep):
    raw = query(['get', 'pods', '-l', 'app=gateway', '-o', 'jsonpath=' + POD_PATH], limit=65536, parsed=False)
    rows = sorted(line.split('\t') for line in raw.splitlines() if line)
    require(len(rows) == 70 and all(len(row) == 10 for row in rows), 'POD_SET_NOT_STABLE')
    for row in rows:
        require(re.fullmatch(r'gateway-[a-z0-9-]{1,80}', row[0]) is not None
                and row[4:7] == ['Running', '', 'true'] and row[9] == IMAGE
                and row[1] and row[8], 'POD_NOT_READY')
    require(len({row[1] for row in rows}) == 70 and len({(r[2], r[3]) for r in rows}) == 1, 'POD_OWNER_SET_CHANGED')
    rs_name, rs_uid = rows[0][2:4]
    require(re.fullmatch(r'gateway-[a-z0-9]{1,40}', rs_name) is not None, 'RS_NAME_INVALID')
    rs = query(['get', 'replicaset', rs_name, '-o', 'json'])
    require(rs['metadata']['uid'] == rs_uid and any(v.get('controller') is True and v.get('uid') == UID
            and v.get('kind') == 'Deployment' for v in rs['metadata'].get('ownerReferences', [])), 'RS_OWNER_CHANGED')
    require(rs['spec']['template']['spec'] == dep['spec']['template']['spec'], 'RS_TEMPLATE_CHANGED')
    return rows


def probe(name):
    value = query(['exec', name, '-c', 'gateway', '--', '/app/backend/.venv/bin/python', '-I', '-B', '-c', PROBE],
                  timeout=30, limit=8192)
    require(value.get('status') == 'PASS' and value.get('oa_enabled') is True
            and value.get('callback_enabled') is False, 'OA_CONFIG_PROBE_FAILED')
    return value


def open_control(create):
    parent = os.open('/var/lib', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    name = os.path.basename(ROOT)
    try:
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=parent)
            except FileExistsError:
                raise Refused('ONCE_EXISTS_REVIEW_REQUIRED')
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        info = os.fstat(fd)
        if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
            os.close(fd)
            raise Refused('UNSAFE_REPORT_DIRECTORY')
        return fd
    finally:
        os.close(parent)


def save_new(directory, name, value):
    raw = canonical(value) + b'\n'
    require(len(raw) <= 65536, 'REPORT_BOUND')
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    os.fsync(directory)


def read_plan(directory):
    fd = os.open('PLAN.json', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and info.st_size <= 65536 and stat.S_IMODE(info.st_mode) == 0o600, 'UNSAFE_PLAN_FILE')
        plan = json.loads(handle.read(65537))
    require(plan.get('schema') == 'portal-oa-timeout-plan.v1' and plan.get('uid') == UID
            and plan.get('image') == IMAGE and plan.get('changed_variable') == KEY
            and plan.get('before_generation') == 403 and plan.get('after_generation') == 404
            and plan.get('expected_effective_after') == 3600
            and re.fullmatch(r'[a-f0-9]{64}', plan.get('after_spec_sha256', '')), 'PLAN_CHANGED')
    return plan


def check_expected(dep, plan):
    require(dep['metadata'].get('uid') == UID and dep['metadata']['generation'] == plan['after_generation']
            and digest(dep['spec']) == plan['after_spec_sha256'], 'DEPLOYMENT_CHANGED_AFTER_PATCH')


def verify(plan, out, wait_seconds):
    deadline = time.monotonic() + wait_seconds
    last_progress = 0
    while True:
        dep = query(['get', 'deployment', 'gateway', '-o', 'json'])
        check_expected(dep, plan)
        if stable(dep):
            break
        now = time.monotonic()
        require(now < deadline, 'ROLLOUT_STILL_RUNNING_VERIFY_READONLY_LATER')
        if now - last_progress >= 60:
            print(json.dumps({'phase':'WAIT_EXISTING_GATEWAY_ROLLOUT',
                              'updated':dep.get('status', {}).get('updatedReplicas', 0),
                              'ready':dep.get('status', {}).get('readyReplicas', 0), 'expected':70}), flush=True)
            last_progress = now
        time.sleep(10)
    before = pods(dep)
    for i, row in enumerate(before, 1):
        observed = probe(row[0])
        require(observed['run_timeout_seconds'] == 3600
                and observed['callback_timeout_seconds'] == plan['callback_timeout_seconds_before'], 'EFFECTIVE_TIMEOUT_MISMATCH')
        out['runtime_pods_verified'] = i
        if i == 1 or i % 10 == 0:
            print(json.dumps({'phase':'VERIFY_EFFECTIVE_OA_TIMEOUT', 'verified':i, 'expected':70, 'seconds':3600}), flush=True)
    final = query(['get', 'deployment', 'gateway', '-o', 'json'])
    check_expected(final, plan)
    require(stable(final) and before == pods(final), 'POD_IDENTITY_CHANGED_DURING_VERIFICATION')
    out.update(status='OA_RUN_TIMEOUT_60M_ACCEPTED', stable_readback=True,
               effective_run_timeout_seconds=3600, callback_enabled=False, gateway_generation=404)


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    flags = parser.add_mutually_exclusive_group(required=True)
    flags.add_argument('--set-oa-run-timeout-60m-once', action='store_true')
    flags.add_argument('--verify-readonly', action='store_true')
    args = parser.parse_args(argv)
    out = {'schema':'portal-oa-run-timeout.v1', 'tool_revision':'r1', 'status':'INCOMPLETE',
           'automatic_retry':False, 'automatic_rollback':False, 'patch_request_count':0,
           'patch_may_have_applied':False, 'once_claimed':False, 'runtime_pods_verified':0,
           'database_access':False, 'database_modified':False, 'provider_requests':0,
           'callback_requests':0, 'generation_restarted':False, 'sources_modified':False,
           'images_changed':False, 'frontend_modified':False, 'runner_modified':False,
           'raw_errors_exported':False, 'read_only':args.verify_readonly}
    control = None
    claimed = False
    try:
        require(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() == 0
                and os.uname().nodename.split('.')[0] == 'datax-01', 'DATAX01_ROOT_REQUIRED')
        require(sys.flags.isolated and sys.dont_write_bytecode, 'ISOLATED_NO_BYTECODE_REQUIRED')
        if args.verify_readonly:
            control = open_control(False)
            plan = read_plan(control)
            verify(plan, out, 0)
        else:
            require(not os.path.lexists(ROOT), 'ONCE_EXISTS_REVIEW_REQUIRED')
            out['last_step'] = 'read_stable_gateway_403'
            dep = query(['get', 'deployment', 'gateway', '-o', 'json'])
            plan, patch = make_plan(dep)
            old_pods = pods(dep)
            observed = probe(old_pods[0][0])
            require(observed['run_timeout_seconds'] == 1800, 'EFFECTIVE_BASE_TIMEOUT_CHANGED')
            plan['callback_timeout_seconds_before'] = observed['callback_timeout_seconds']
            out['plan'] = plan
            control = open_control(True)
            claimed = True
            out['once_claimed'] = True
            save_new(control, 'PLAN.json', plan)
            save_new(control, 'STARTED.json', {'schema':'portal-oa-timeout-start.v1', 'at_epoch':time.time(),
                                              'plan_sha256':digest(plan), 'patch_attempts_allowed':1})
            out['last_step'] = 'one_environment_patch'
            out['patch_request_count'] = 1
            out['patch_may_have_applied'] = True
            print(json.dumps({'phase':'SET_OA_RUN_TIMEOUT_1800_TO_3600', 'existing_strategy_preserved':True}), flush=True)
            changed = query(['patch', 'deployment', 'gateway', '--type=json', '--patch-file=/dev/stdin', '-o', 'json'],
                            payload=canonical(patch))
            check_expected(changed, plan)
            out['patch_applied'] = True
            out['last_step'] = 'verify_rollout_and_effective_configuration'
            verify(plan, out, 3600)
    except Exception as exc:
        out.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else 'LOCAL_FAILURE_REVIEW_REQUIRED')
    finally:
        if claimed:
            out['report_path'] = ROOT + '/REPORT.json'
            try:
                save_new(control, 'REPORT.json', out)
            except Exception as exc:
                out['report_write_error_class'] = type(exc).__name__
        if control is not None:
            os.close(control)
    print(json.dumps(out, sort_keys=True))
    return out


if __name__ == '__main__':
    raise SystemExit(0 if run()['status'] == 'OA_RUN_TIMEOUT_60M_ACCEPTED' else 1)

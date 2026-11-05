"""Verify the disabled V4.1 model with the existing GLM credential, at most 7 calls."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import time

BASE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=15s', '-n', 'deer-flow']
CONTROL = '/var/lib/portal-v41-glm-check-20261029-r1'
GATEWAY = {'uid': 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1', 'generation': 389,
           'spec_sha256': '32ff8cd158844287f0f34d2d9d48d2dda816922dc2dc0082bfd5f97ca77ee6f3'}
IMAGE = 'deerflow.local/deer-flow-gateway@sha256:a879d9a0688099417a10d301fb41fa78664cb32d62b17a15ec1f435b943a19d4'
PROTECTED = ('gateway', 'frontend', 'ppt-master-runner', 'nginx-security-heai-20261012-r1')
CHECKS = ['native_document_request', 'adapter_nonstream_text', 'adapter_stream_text',
          'stream_tool_call', 'stream_tool_result', 'stream_thinking', 'stream_image_input']
CHILD = None  # Replaced by package.py; the source template is not executable.
CHILD_SHA = None


class Refused(RuntimeError): pass


def require(ok, code):
    if not ok: raise Refused(code)


def binding(obj):
    raw = json.dumps(obj['spec'], sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
    return {'uid': obj['metadata']['uid'], 'generation': obj['metadata'].get('generation'),
            'spec_sha256': hashlib.sha256(raw).hexdigest()}


class Reader:
    def __init__(self):
        self.started = time.monotonic(); self.commands = 0

    def read(self, args, data=None, timeout=25, cap=8388608, progress=False):
        require(self.commands < 30, 'COMMAND_BUDGET')
        remaining = 440 - (time.monotonic() - self.started)
        require(remaining > 1, 'HOST_DEADLINE')
        self.commands += 1
        p = subprocess.Popen(args, stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        buffers = [bytearray(), bytearray()]; overflow = threading.Event()
        def drain(stream, index):
            while True:
                chunk = stream.read(65536)
                if not chunk: break
                if len(buffers[index]) + len(chunk) > cap:
                    overflow.set(); p.kill(); break
                buffers[index].extend(chunk)
        threads = [threading.Thread(target=drain, args=(p.stdout, 0)), threading.Thread(target=drain, args=(p.stderr, 1))]
        for thread in threads: thread.start()
        if data is not None:
            def feed():
                try: p.stdin.write(data); p.stdin.close()
                except (BrokenPipeError, OSError): pass
            writer = threading.Thread(target=feed); writer.start(); threads.append(writer)
        end = time.monotonic() + min(timeout, remaining)
        try:
            while p.poll() is None:
                wait = end - time.monotonic()
                if wait <= 0: raise subprocess.TimeoutExpired(args, timeout)
                try: p.wait(timeout=min(wait, 30))
                except subprocess.TimeoutExpired:
                    if progress:
                        print(json.dumps({'phase': 'V41_GLM_CAPABILITY_CHECK_RUNNING',
                                          'elapsed_seconds': round(time.monotonic() - self.started)}), flush=True)
        except subprocess.TimeoutExpired:
            p.kill(); p.wait(timeout=5); raise Refused('COMMAND_TIMEOUT')
        finally:
            for thread in threads: thread.join(timeout=5)
            p.stdout.close(); p.stderr.close()
        require(not overflow.is_set() and not any(t.is_alive() for t in threads), 'OUTPUT_BOUND')
        require(p.returncode == 0, 'READ_COMMAND_FAILED')
        return bytes(buffers[0])

    def get(self, kind, name=None):
        require(kind in ('deployment', 'replicaset', 'pods', 'pod'), 'RESOURCE_NOT_ALLOWED')
        return json.loads(self.read(BASE + ['get', kind] + ([name] if name else []) + ['-o', 'json']))


def owned_ready_pods(deployment, sets, pods):
    uid = deployment['metadata']['uid']
    owners = {r['metadata']['uid'] for r in sets if any(o.get('controller') and
        o.get('kind') == 'Deployment' and o.get('uid') == uid for o in r['metadata'].get('ownerReferences', []))}
    result = []
    for pod in pods:
        if pod['metadata'].get('deletionTimestamp') or pod.get('status', {}).get('phase') != 'Running': continue
        if not any(o.get('controller') and o.get('kind') == 'ReplicaSet' and o.get('uid') in owners
                   for o in pod['metadata'].get('ownerReferences', [])): continue
        containers = [c for c in pod['spec']['containers'] if c['name'] == 'gateway']
        statuses = [c for c in pod['status'].get('containerStatuses', []) if c['name'] == 'gateway']
        if len(containers) == 1 and containers[0].get('image') == IMAGE and len(statuses) == 1 and statuses[0].get('ready'):
            result.append(pod)
    return sorted(result, key=lambda p: p['metadata']['name'])


def identity(pod):
    return {'pod': pod['metadata']['name'], 'uid': pod['metadata']['uid'], 'node': pod['spec'].get('nodeName'),
            'deleting': bool(pod['metadata'].get('deletionTimestamp')), 'phase': pod.get('status', {}).get('phase'),
            'containers': [{k: c.get(k) for k in ('name', 'containerID', 'imageID', 'ready', 'restartCount')}
                           for c in pod.get('status', {}).get('containerStatuses', [])]}


def accepted(observation):
    return (observation.get('status') == 'V41_GLM_CAPABILITIES_ACCEPTED_NOT_ENABLED' and
            observation.get('checks') == CHECKS and observation.get('provider_requests') == 7 and
            [r.get('name') for r in observation.get('cases', [])] == CHECKS and
            all(r.get('status') == 'PASS' and r.get('provider_requests') == 1 and r.get('http_status') == 200
                and r.get('full_endpoint_verified') and r.get('glm_credential_verified') for r in observation['cases']) and
            observation.get('credential_binding', {}).get('credential_source') == 'GLM_5_3_FLASH_API_KEY' and
            observation.get('v41_enabled') is False and observation.get('configuration_modified') is False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify-v41-with-glm-key-once', required=True, action='store_true'); parser.parse_args()
    result = dict(schema='portal-v41-glm-host.v1', status='INCOMPLETE', once_claimed=False,
        provider_requests_may_have_occurred=False, maximum_provider_requests=7, automatic_retry=False,
        credentials_exported=False, model_text_exported=False, raw_errors_exported=False,
        database_access=False, workload_modified=False, configuration_modified=False,
        secret_api_reads=0, v41_enabled=False, default_model_changed=False, ready_for_rollout=False)
    def save():
        if result['once_claimed']:
            temporary = Path(CONTROL) / 'REPORT.tmp'
            with temporary.open('w', encoding='utf-8') as handle:
                json.dump(result, handle, sort_keys=True); handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
            os.replace(str(temporary), str(Path(CONTROL) / 'REPORT.json'))
    reader = Reader()
    try:
        require(socket.gethostname().split('.')[0] == 'datax-01' and os.geteuid() == 0, 'WRONG_HOST_OR_UID')
        require(isinstance(CHILD, str) and hashlib.sha256(CHILD.encode()).hexdigest() == CHILD_SHA, 'PACKAGED_TOOL_REQUIRED')
        require(not os.path.lexists(CONTROL), 'ONCE_ALREADY_CLAIMED')
        before = {name: reader.get('deployment', name) for name in PROTECTED}
        require(binding(before['gateway']) == GATEWAY, 'GATEWAY_BASELINE_CHANGED')
        require(before['gateway'].get('status', {}).get('observedGeneration') == 389, 'GATEWAY_NOT_OBSERVED')
        ready = owned_ready_pods(before['gateway'], reader.get('replicaset')['items'], reader.get('pods')['items'])
        require(ready, 'NO_READY_OWNED_GATEWAY')
        pod = ready[0]; result['sample'] = identity(pod)
        require(identity(reader.get('pod', pod['metadata']['name'])) == identity(pod), 'SAMPLE_CHANGED')
        os.mkdir(CONTROL, 0o700); result['once_claimed'] = True
        result['provider_requests_may_have_occurred'] = True; save()
        print(json.dumps({'phase': 'VERIFY_V41_USING_CONFIGURED_GLM_KEY', 'maximum_provider_requests': 7}), flush=True)
        raw = reader.read(BASE + ['exec', '-i', pod['metadata']['name'], '-c', 'gateway', '--',
            '/app/backend/.venv/bin/python', '-I', '-B', '-u', '-c', CHILD], b'', timeout=365, cap=65536, progress=True)
        observation = json.loads(raw); result['observation'] = observation; save()
        require(identity(reader.get('pod', pod['metadata']['name'])) == identity(pod), 'SAMPLE_CHANGED')
        after = {name: reader.get('deployment', name) for name in PROTECTED}
        require({n: binding(o) for n, o in before.items()} == {n: binding(o) for n, o in after.items()}, 'DEPLOYMENT_CHANGED_DURING_CHECK')
        result['protected_deployments_preserved'] = True
        require(accepted(observation), 'PROVIDER_ACCEPTANCE_INCOMPLETE')
        result.update(status='V41_GLM_CAPABILITIES_ACCEPTED_NOT_ENABLED', business_acceptance_pending=True)
    except Exception as exc:
        result.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else 'MODEL_CHECK_FAILED')
    save()
    if result['once_claimed']:
        raw = (Path(CONTROL) / 'REPORT.json').read_bytes()
        result.update(report_path=CONTROL + '/REPORT.json', report_bytes=len(raw), report_sha256=hashlib.sha256(raw).hexdigest())
    print(json.dumps(result, sort_keys=True))
    return 0 if result['status'] == 'V41_GLM_CAPABILITIES_ACCEPTED_NOT_ENABLED' else 1


if __name__ == '__main__': raise SystemExit(main())

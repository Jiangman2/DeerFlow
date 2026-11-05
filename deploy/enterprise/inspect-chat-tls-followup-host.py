#!/usr/bin/env python3
"""Python 3.7+: read-only follow-up using the existing, pinned egress proxy.

Keeps certificate/hostname verification. Uses the proxy credential only inside
the selected Pod to its configured 10.68.211.1:8080 proxy. No model/API key is
sent, no search query or response body is collected. No workload changes.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import zlib

SHARED_HOST = '__SEALED_SHARED_HOST__'
CHILD = '__SEALED_FOLLOWUP_CHILD__'
PRIOR_PATH = '/var/lib/portal-chat-tls-diagnosis-20261025-4ehr_8jo/REPORT.json'
PRIOR_SHA = '067ab54c04f511ae3b839ff1a4aa9cbba6439550036aa2fcc249530ea06084fc'
GATEWAY_UID = 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1'
GATEWAY_SPEC = '82e248a2fb9f55f9e5426ee8787f42f98f2c3190a2a4c0b66c04aa934b79d64c'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect-proxy-tls-readonly', required=True, action='store_true')
    parser.parse_args()
    ns = {'__name__': 'readonly_kubernetes_helpers'}
    exec(compile(zlib.decompress(base64.b64decode(SHARED_HOST)).decode(), '<sealed-host>', 'exec'), ns)
    require, query = ns['require'], ns['query']
    report = {'schema': 'portal-chat-tls-followup.v2', 'status': 'INCOMPLETE', 'exitcode': 1,
        'production_workload_modified': False, 'configuration_modified': False,
        'secret_api_reads': 0, 'database_modified': False, 'credentials_exported': False,
        'proxy_authentication': 'REUSE_EXISTING_ONLY_AT_PINNED_PROXY', 'automatic_retry': False,
        'model_requests': 0, 'browser_launched': False, 'maximum_head_requests': 10,
        'certificate_validation_disabled': False, 'raw_conversation_exported': False}
    directory = None
    started = time.monotonic()
    def save():
        if directory is not None:
            p = directory / 'REPORT.tmp'
            fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as stream:
                json.dump(report, stream, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(str(p), str(directory / 'REPORT.json'))
    try:
        require(os.name == 'posix' and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'ROOT_ON_DATAX01_REQUIRED')
        require(sys.flags.isolated and sys.flags.dont_write_bytecode, 'PYTHON_I_B_REQUIRED')
        prior = Path(PRIOR_PATH)
        require(prior.is_file() and not prior.is_symlink() and prior.stat().st_size <= 1048576, 'PRIOR_REPORT_REQUIRED')
        raw = prior.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == PRIOR_SHA, 'PRIOR_REPORT_CHANGED')
        report['prior_receipt'] = {'path': PRIOR_PATH, 'bytes': len(raw), 'sha256': PRIOR_SHA}
        deployments = query(['get', 'deployments', '-o', 'json'])['items']
        gateway = next(d for d in deployments if d['metadata']['name'] == 'gateway')
        before = ns['identity'](gateway)
        require(before['uid'] == GATEWAY_UID and before['spec_sha256'] == GATEWAY_SPEC and before['generation'] == 387, 'GATEWAY_BASELINE_CHANGED')
        rs = query(['get', 'replicasets', '-o', 'json'])['items']
        rs_ids = {x['metadata']['uid'] for x in rs if ns['owned'](x, GATEWAY_UID)}
        pods = query(['get', 'pods', '-o', 'json'])['items']
        samples = {}
        for pod in sorted(pods, key=lambda p: p['metadata']['name']):
            if not any(ns['owned'](pod, uid) for uid in rs_ids):
                continue
            info = ns['pod_identity'](pod, 'gateway')
            if info['ready'] and info['running']:
                samples.setdefault(info['node'], pod)
        expected = {'ecs-zdz-pro-%04d' % n for n in range(18, 23)}
        require(set(samples) == expected, 'FIVE_GATEWAY_NODES_REQUIRED')
        source = zlib.decompress(base64.b64decode(CHILD)).decode()
        directory = Path(tempfile.mkdtemp(prefix='portal-chat-tls-followup-20261025-', dir='/var/lib'))
        report['before'] = before
        print(json.dumps({'phase': 'READONLY_CERTIFICATE_AND_EVENT_DETAILS'}), flush=True)
        first = samples[sorted(samples)[0]]
        report['details'] = ns['runtime_exec'](source, first, 'gateway', 'details')
        save()
        report['probes'] = []
        for index, node in enumerate(sorted(samples)):
            print(json.dumps({'phase': 'EXISTING_PROXY_VERIFIED_TLS_HEAD', 'node': node, 'maximum_requests': 6 if index == 0 else 1}), flush=True)
            result = ns['runtime_exec'](source, samples[node], 'gateway', 'matrix' if index == 0 else 'proxy')
            report['probes'].append(result)
            save()
        after = query(['get', 'deployments', '-o', 'json'])['items']
        report['after'] = ns['identity'](next(d for d in after if d['metadata']['name'] == 'gateway'))
        endpods = {p['metadata']['name']: p for p in query(['get', 'pods', '-o', 'json'])['items']}
        report['changed_samples'] = [p['metadata']['name'] for p in samples.values() if p['metadata']['name'] not in endpods or ns['pod_identity'](p, 'gateway') != ns['pod_identity'](endpods[p['metadata']['name']], 'gateway')]
        observations = [item for p in report['probes'] for item in p['observation'].get('requests', [])]
        report['actual_head_requests'] = sum(p['requests_attempted'] for p in observations)
        report['outcomes'] = {status: sum(p.get('status') == status for p in observations) for status in ('HTTP_RESPONSE_TLS_ACCEPTED', 'FAILED')}
        report['snapshot_stable'] = report['before'] == report['after'] and not report['changed_samples']
        complete = report['snapshot_stable'] and len(observations) == 10 and report['details']['observation'].get('status') == 'DETAILS_OBSERVED'
        report.update(status='TLS_FOLLOWUP_EVIDENCE_COLLECTED_NOT_REPAIRED' if complete else 'PARTIAL_OBSERVATION', exitcode=0 if complete else 1)
    except (Exception, KeyboardInterrupt) as exc:
        report['error_class'] = type(exc).__name__
        report['error_code'] = str(exc) if isinstance(exc, ns['Refused']) else 'FOLLOWUP_INCOMPLETE'
    report['elapsed_seconds'] = round(time.monotonic() - started, 3)
    save()
    output = dict(report)
    if directory is not None:
        raw = (directory / 'REPORT.json').read_bytes()
        output.update(report_path=str(directory / 'REPORT.json'), report_bytes=len(raw), report_sha256=hashlib.sha256(raw).hexdigest())
    print(json.dumps(output, sort_keys=True), flush=True)
    return report['exitcode']


if __name__ == '__main__':
    raise SystemExit(main())

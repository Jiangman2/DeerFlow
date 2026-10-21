"""Read-only live expansion probe. Busy production work is allowed."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import stat
import sys
import time

STAGE_CODE = '__EMBED_FROZEN_STAGE_PROBE__'
STAGE_SHA = 'a5e23fa8b381c6443c86ceabba0e19db14d5a22072a04962d86da1f651e6f503'
PUBLIC = 'deer-flow-mineru-adapter.deer-flow.svc.cluster.local'


class ProbeFailure(RuntimeError):
    pass


def require(ok, label):
    if not ok:
        raise ProbeFailure(label)


def validate_health(row, role, expected):
    require(type(expected) is int and 6 <= expected <= 16, 'unexpected_slot_count')
    require(row.get('status') == 'ok' and row.get('role') == role, 'health_role')
    if role == 'worker':
        p = row.get('parser', {})
        require(all(type(p.get(k)) is int and p[k] >= 0 for k in ('active', 'waiting'))
                and p['active'] <= 1, 'worker_not_serialized_or_counter_invalid')
        return {k: p[k] for k in ('active', 'waiting')}
    require(role == 'dispatcher', 'unexpected_role')
    d = row.get('dispatcher', {})
    keys = ('discovered', 'live', 'minimum', 'busy', 'waiting', 'quarantined')
    require(all(type(d.get(k)) is int and d[k] >= 0 for k in keys), 'dispatcher_counter_invalid')
    require(d['minimum'] == 6, 'dispatcher_minimum_changed')
    require(d['discovered'] == d['live'] == expected and d['busy'] <= expected
            and d['quarantined'] == 0 and d.get('ready') is True
            and d.get('capacity_target_met') is True, 'dispatcher_capacity_not_converged')
    return {k: d[k] for k in keys}


def health(host, allowed):
    require(host in allowed, 'health_host_not_allowed')
    c = http.client.HTTPConnection(host, 50052, timeout=8)
    try:
        c.request('GET', '/health', headers={'Connection': 'close', 'Cache-Control': 'no-cache'})
        response = c.getresponse()
        raw = response.read(32769)
        require(response.status == 200 and len(raw) <= 32768, 'health_http_unavailable')
        return json.loads(raw)
    finally:
        c.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=('worker', 'dispatcher'), required=True)
    parser.add_argument('--expected-workers', type=int, required=True)
    parser.add_argument('--worker-ips', default='')
    args = parser.parse_args()
    require(6 <= args.expected_workers <= 16, 'expected_capacity_out_of_scope')
    require(hashlib.sha256(STAGE_CODE.encode()).hexdigest() == STAGE_SHA, 'stage_helper_hash_changed')
    ns = dict(__name__='pinned_config_helpers')
    exec(compile(STAGE_CODE, '<pinned-config-check>', 'exec'), ns)
    require(os.environ.get('MINERU_ADAPTER_MODE') == args.role, 'runtime_role_changed')
    path = Path('/app/deerflow_mineru_adapter.py')
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        st = os.fstat(stream.fileno())
        require(stat.S_ISREG(st.st_mode) and st.st_uid == 0 and st.st_nlink == 1
                and stat.S_IMODE(st.st_mode) == 0o644 and 0 < st.st_size < 262144, 'source_metadata_changed')
        require(hashlib.sha256(stream.read(262144)).hexdigest() == ns['SOURCE_SHA'], 'source_hash_changed')
    allowed = ('127.0.0.1', ns['PREVIEW'], PUBLIC)
    result = dict(expansion_runtime='PASS', role=args.role, source_sha256=ns['SOURCE_SHA'],
        expected_workers=args.expected_workers, provider_requests=0, documents_submitted=0,
        files_modified=False, credentials_printed=False, tls_connections=0)
    if args.role == 'worker':
        ns['worker_config']()  # Existing exact endpoint, token-presence and CA-hash checks.
        result['ca_sha256'] = ns['CA_SHA']
        result['counters'] = validate_health(health('127.0.0.1', allowed), args.role, args.expected_workers)
    else:
        require(os.environ.get('MINERU_DISPATCH_BACKEND_HOST') == ns['WORKERS']
                and os.environ.get('MINERU_DISPATCH_MIN_BACKENDS') == '6', 'dispatcher_configuration_changed')
        wanted = args.worker_ips.split(',')
        require(len(wanted) == len(set(wanted)) == args.expected_workers, 'worker_ip_set_changed')
        deadline = time.monotonic() + 60
        while True:
            try:
                actual = {row[4][0] for row in socket.getaddrinfo(ns['WORKERS'], None, socket.AF_INET, socket.SOCK_STREAM)}
                require(actual == set(wanted), 'worker_dns_not_converged')
                counters = {key: validate_health(health(host, allowed), 'dispatcher', args.expected_workers)
                            for key, host in [('local', '127.0.0.1'), ('private', ns['PREVIEW']), ('public', PUBLIC)]}
                result.update(counters=counters, worker_dns='EXACT_OWNED_PODS', public_health='PASS')
                break
            except (OSError, ProbeFailure) as exc:
                retry = isinstance(exc, OSError) or str(exc) in (
                    'worker_dns_not_converged', 'dispatcher_capacity_not_converged', 'health_http_unavailable')
                if not retry or time.monotonic() >= deadline:
                    raise
                time.sleep(3)  # Read-only DNS/health convergence, never parse resubmission.
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps(dict(expansion_runtime='FAIL', error_type=type(exc).__name__,
            failed_check=str(exc) if isinstance(exc, ProbeFailure) or type(exc).__name__ == 'CheckFailure'
                else 'HEALTH_OR_RUNTIME_READ_UNAVAILABLE',
            raw_error_printed=False, provider_requests=0, documents_submitted=0, credentials_printed=False)), flush=True)
        sys.exit(1)

"""Read-only candidate process/health and TLS probe; NEVER submit a document."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import ssl
import stat
import sys

SOURCE_SHA = 'a509a7e145ba3d5d72ba86036437a9e5da2e46678054558522a6d61b66a22e74'
CA_SHA = '84c58afec9e48e1bd7b41d832ff6986b74a6565b4473f7cc77b868900b4d76af'
BASE = 'https://aiapi.cnooc.com.cn/MinerU2.5-2605-1.2B/FC-02'
TASKS = BASE + '/dynamic/member1/mineru-for-zy-agent/tasks'
WORKERS = 'deer-flow-mineru-dedicated-workers-r1.deer-flow.svc.cluster.local'
PREVIEW = 'deer-flow-mineru-dedicated-preview-r1.deer-flow.svc.cluster.local'


class CheckFailure(RuntimeError):
    pass


def require(ok, label):
    if not ok:
        raise CheckFailure(label)


def health(host):
    require(host in ('127.0.0.1', PREVIEW), 'unexpected_health_host')
    c = http.client.HTTPConnection(host, 50052, timeout=8)
    try:
        c.request('GET', '/health', headers={'Connection': 'close'})
        response = c.getresponse()
        raw = response.read(32769)
        require(response.status == 200 and len(raw) <= 32768, 'health_unavailable')
        return json.loads(raw), response.version
    finally:
        c.close()


def validate_health(row, role, expected):
    require(row.get('status') == 'ok' and row.get('role') == role, 'health_role')
    if role == 'worker':
        p = row.get('parser', {})
        require(p.get('active') == 0 and p.get('waiting') == 0, 'new_worker_unexpected_activity')
        return {k: p.get(k) for k in ('active', 'waiting')}
    d = row.get('dispatcher', {})
    require(d.get('discovered') == expected and d.get('live') == expected
            and d.get('minimum') == expected and d.get('capacity_target_met') is True
            and d.get('ready') is True, 'new_dispatcher_capacity')
    require(all(d.get(k) == 0 for k in ('busy', 'waiting', 'quarantined')), 'new_dispatcher_unexpected_activity')
    return {k: d.get(k) for k in ('discovered', 'live', 'minimum', 'busy', 'waiting', 'quarantined', 'capacity_target_met')}


def worker_config():
    require(os.environ.get('MINERU_API_URL', '').rstrip('/') == BASE
            and os.environ.get('MINERU_TASK_QUERY_URL', '').rstrip('/') == TASKS
            and os.environ.get('MINERU_REQUEST_MODE') == 'async'
            and os.environ.get('MINERU_TRUST_ENV', '').lower() == 'false', 'new_worker_config')
    token = os.environ.get('MINERU_API_TOKEN', '')
    require(8 <= len(token) <= 4096 and token == token.strip(), 'credential_not_present_or_invalid_shape')
    del token
    filename = (os.environ.get('MINERU_CA_BUNDLE', '').strip()
        or os.environ.get('REQUESTS_CA_BUNDLE', '').strip()
        or os.environ.get('SSL_CERT_FILE', '').strip())
    require(filename, 'ca_path_missing')
    path = Path(filename)
    s = path.stat()
    require(stat.S_ISREG(s.st_mode) and 0 < s.st_size <= 262144, 'ca_file_invalid')
    require(hashlib.sha256(path.read_bytes()).hexdigest() == CA_SHA, 'ca_hash_changed')
    return filename


def tls_probe(cafile):
    # Raw direct TLS handshake: no HTTP, API token, proxy, parsing or retry.
    ctx = ssl.create_default_context(cafile=cafile)
    require(ctx.check_hostname and ctx.verify_mode == ssl.CERT_REQUIRED, 'tls_verification_disabled')
    host = 'aiapi.cnooc.com.cn'
    with socket.create_connection((host, 443), timeout=8) as tcp:
        with ctx.wrap_socket(tcp, server_hostname=host) as tls:
            require(bool(tls.getpeercert()), 'tls_peer_missing')
            return {'tls': 'PASS', 'tls_version': tls.version(), 'tls_connections': 1,
                    'proxy_inherited': False, 'certificate_and_hostname_verification': True,
                    'credentials_sent': False, 'provider_http_requests': 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', choices=('worker', 'dispatcher'), required=True)
    parser.add_argument('--expected-workers', type=int, required=True)
    parser.add_argument('--worker-ips', default='')
    args = parser.parse_args()
    require(args.expected_workers == 6, 'initial_worker_count_changed')
    require(os.environ.get('MINERU_ADAPTER_MODE') == args.role, 'runtime_role_changed')
    source = Path('/app/deerflow_mineru_adapter.py')
    s = source.lstat()
    require(stat.S_ISREG(s.st_mode) and source.resolve() == source and s.st_uid == 0
            and s.st_nlink == 1 and stat.S_IMODE(s.st_mode) == 0o644
            and 0 < s.st_size < 262144, 'source_metadata_changed')
    require(hashlib.sha256(source.read_bytes()).hexdigest() == SOURCE_SHA, 'source_hash_changed')
    row, version = health('127.0.0.1')
    counters = validate_health(row, args.role, args.expected_workers)
    result = dict(stage_runtime='PASS', role=args.role, source_sha256=SOURCE_SHA,
                  health_counters=counters, http_version=version, files_modified=False,
                  provider_http_requests=0, documents_submitted=0, credentials_printed=False)
    if args.role == 'worker':
        result['ca_sha256'] = CA_SHA
        result.update(tls_probe(worker_config()))
    else:
        require(os.environ.get('MINERU_DISPATCH_BACKEND_HOST') == WORKERS
                and os.environ.get('MINERU_DISPATCH_MIN_BACKENDS') == '6', 'dispatcher_configuration')
        expected = args.worker_ips.split(',')
        require(len(expected) == 6 and len(set(expected)) == 6, 'worker_address_set')
        addresses = {r[4][0] for r in socket.getaddrinfo(WORKERS, None, socket.AF_INET, socket.SOCK_STREAM)}
        require(addresses == set(expected), 'worker_dns_does_not_match_owned_pods')
        private, _ = health(PREVIEW)
        validate_health(private, 'dispatcher', 6)
        result.update(private_service_health='PASS', owned_worker_dns='PASS', tls_connections=0)
    print(json.dumps(result, sort_keys=True), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps(dict(stage_runtime='FAIL', error_type=type(exc).__name__,
            failed_check=str(exc) if isinstance(exc, CheckFailure) else 'TRANSPORT_OR_RUNTIME_ERROR',
            raw_error_printed=False, provider_http_requests=0, documents_submitted=0,
            credentials_printed=False)), flush=True)
        sys.exit(1)

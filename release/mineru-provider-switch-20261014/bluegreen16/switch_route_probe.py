"""Bounded fresh public-Service GET /health; no parse, keys, proxy or retry."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import stat
import sys

PUBLIC = 'deer-flow-mineru-adapter.deer-flow.svc.cluster.local'
SOURCES = {
    'candidate': ('a509a7e145ba3d5d72ba86036437a9e5da2e46678054558522a6d61b66a22e74', 0o644),
    'original': ('1c54fbea077137f39a4ce66b1cc309d7e643541808f4b9670fe0fb8037eb6b39', 0o666),
}


class ProbeFailure(RuntimeError):
    pass


def require(ok, label):
    if not ok:
        raise ProbeFailure(label)


def validate_health(row, pool):
    require(row.get('status') == 'ok' and row.get('role') == 'dispatcher', 'public_health_role')
    d = row.get('dispatcher', {})
    keys = ('live', 'discovered', 'minimum', 'busy', 'waiting', 'quarantined')
    require(all(type(d.get(k)) is int and d[k] >= 0 for k in keys), 'public_health_counters')
    require(d.get('ready') is True, 'public_dispatcher_not_ready')
    if pool == 'candidate':
        require(d['live'] == d['discovered'] == d['minimum'] == 6
                and d.get('capacity_target_met') is True and d['busy'] <= 6
                and d['quarantined'] == 0, 'candidate_public_capacity')
    else:
        # Original pool already had three missing images. Do not pretend that
        # rollback repairs them or demand an unapproved baseline change.
        require(d['live'] >= 6 and d['busy'] <= 10, 'original_public_capacity')
    return {k: d[k] for k in keys}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--caller', choices=tuple(SOURCES), required=True)
    parser.add_argument('--pool', choices=tuple(SOURCES), required=True)
    args = parser.parse_args()
    source = Path('/app/deerflow_mineru_adapter.py')
    expected_sha, expected_mode = SOURCES[args.caller]
    fd = os.open(str(source), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        s = os.fstat(stream.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                and stat.S_IMODE(s.st_mode) == expected_mode and 0 < s.st_size < 262144,
                'caller_source_metadata_changed')
        raw = stream.read(262144)
        require(hashlib.sha256(raw).hexdigest() == expected_sha, 'caller_source_hash_changed')
    c = http.client.HTTPConnection(PUBLIC, 50052, timeout=8)
    try:
        c.request('GET', '/health', headers={'Connection': 'close', 'Cache-Control': 'no-cache'})
        response = c.getresponse()
        raw = response.read(32769)
        require(response.status == 200 and len(raw) <= 32768, 'public_health_http_failed')
        counters = validate_health(json.loads(raw), args.pool)
    finally:
        c.close()
    print(json.dumps(dict(public_service_health='PASS', expected_pool=args.pool,
        caller_source_sha256=expected_sha, counters=counters, fresh_connection=True,
        redirects_followed=False, proxy_inherited=False, provider_requests=0,
        documents_submitted=0, files_modified=False, credentials_sent=False)), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps(dict(public_service_health='FAIL', error_type=type(exc).__name__,
            failed_check=str(exc) if isinstance(exc, ProbeFailure) else 'HEALTH_TRANSPORT_OR_RUNTIME',
            raw_error_printed=False, provider_requests=0, documents_submitted=0)), flush=True)
        sys.exit(1)

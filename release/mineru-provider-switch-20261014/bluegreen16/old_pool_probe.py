"""Bounded, content-free localhost health and TCP snapshots. Never drains."""
import argparse
from collections import Counter
import hashlib
import http.client
import json
import os
from pathlib import Path
import stat
import sys

SOURCE_SHA = '1c54fbea077137f39a4ce66b1cc309d7e643541808f4b9670fe0fb8037eb6b39'
PORT = 50052
STATES = {'01': 'ESTABLISHED', '02': 'SYN_SENT', '03': 'SYN_RECV',
          '04': 'FIN_WAIT1', '05': 'FIN_WAIT2', '06': 'TIME_WAIT', '07': 'CLOSE',
          '08': 'CLOSE_WAIT', '09': 'LAST_ACK', '0A': 'LISTEN', '0B': 'CLOSING',
          '0C': 'NEW_SYN_RECV'}


class ProbeFailure(RuntimeError):
    pass


def require(ok, label):
    if not ok:
        raise ProbeFailure(label)


def parse_tcp(text):
    """Count all namespace sockets; no peer address or request data leaves Pod."""
    rows = []
    lines = text.splitlines()
    require(lines and 'local_address' in lines[0], 'tcp_header_invalid')
    require(len(lines) <= 8193, 'tcp_rows_limit')
    for line in lines[1:]:
        if not line.strip():
            continue
        fields = line.split()
        require(len(fields) >= 10, 'tcp_row_invalid')
        local, remote = (int(fields[i].rsplit(':', 1)[1], 16) for i in (1, 2))
        if PORT not in (local, remote):
            continue
        state = STATES.get(fields[3])
        require(state is not None, 'tcp_state_unknown')
        tx, rx = (int(n, 16) for n in fields[4].split(':'))
        require(tx >= 0 and rx >= 0, 'tcp_queue_invalid')
        rows.append(dict(direction='inbound' if local == PORT else 'outbound',
                         state=state, tx=tx, rx=rx))
    return rows


def summarize_tcp(rows):
    inbound = [r for r in rows if r['direction'] == 'inbound']
    active = [r for r in inbound if r['state'] not in ('LISTEN', 'TIME_WAIT', 'CLOSE')]
    listeners = [r for r in inbound if r['state'] == 'LISTEN']
    return dict(inbound_states=dict(Counter(r['state'] for r in inbound)),
        outbound_states=dict(Counter(r['state'] for r in rows if r['direction'] == 'outbound')),
        inbound_nonterminal=len(active), listener_count=len(listeners),
        listener_receive_queue=sum(r['rx'] for r in listeners),
        inbound_pending_send_bytes=sum(r['tx'] for r in active),
        inbound_pending_receive_bytes=sum(r['rx'] for r in active),
        health_connections_may_be_included=True, addresses_printed=False)


def tcp_snapshot():
    rows = []
    for name in ('tcp', 'tcp6'):
        with open('/proc/net/'+name, 'rb') as f:
            raw = f.read(2*1024**2+1)
        require(len(raw) <= 2*1024**2, 'tcp_table_too_large')
        rows.extend(parse_tcp(raw.decode('ascii')))
    return summarize_tcp(rows)


def source_identity():
    p = Path('/app/deerflow_mineru_adapter.py')
    for parent in (Path('/'), p.parent):
        s = parent.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                'unsafe_source_ancestor')
    fd = os.open(str(p), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        before = os.fstat(f.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and stat.S_IMODE(before.st_mode) == 0o666 and before.st_size == 119416,
                'legacy_source_metadata_changed')
        raw = f.read(262144)
        after = os.fstat(f.fileno())
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns,
                          s.st_mode, s.st_uid, s.st_nlink)
    require(identity(before) == identity(after) == identity(p.lstat())
            and hashlib.sha256(raw).hexdigest() == SOURCE_SHA, 'legacy_source_hash_changed')


def safe_health(body, role):
    require(body.get('role') == role and body.get('status') == 'ok', 'health_role_or_status')
    value = body.get('parser' if role == 'worker' else 'dispatcher', {})
    required = ('active', 'waiting') if role == 'worker' else ('busy', 'waiting', 'live', 'discovered', 'quarantined')
    require(all(type(value.get(k)) is int and value[k] >= 0 for k in required), 'health_counter_invalid')
    return {k: value[k] for k in required}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--role', required=True, choices=('worker', 'dispatcher'))
    args = parser.parse_args()
    source_identity()
    before = tcp_snapshot()
    conn = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10)
    try:
        conn.request('GET', '/health', headers={'Connection': 'close'})
        response = conn.getresponse()
        body = response.read(32769)
        require(response.status == 200 and len(body) <= 32768, 'health_http_failed')
        counters = safe_health(json.loads(body), args.role)
        version = response.version
    finally:
        conn.close()
    after = tcp_snapshot()
    require(before['listener_count'] >= 1 and after['listener_count'] >= 1, 'listener_not_observed')
    print(json.dumps(dict(old_pool_probe='PASS', role=args.role, source_sha256=SOURCE_SHA,
        counters=counters, http_version=version, tcp_before=before, tcp_after=after,
        no_admission_barrier_installed=True, files_modified=False, provider_requests=0,
        documents_submitted=0, raw_content_printed=False)), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # The frozen exec wrapper recognizes this compatibility error marker.
        print(json.dumps(dict(old_pool_probe='INCOMPLETE', stage_runtime='FAIL', error_type=type(exc).__name__,
            failed_check=str(exc) if isinstance(exc, ProbeFailure) else 'PROBE_RUNTIME_ERROR',
            provider_requests=0, documents_submitted=0, raw_error_printed=False)), flush=True)
        sys.exit(1)

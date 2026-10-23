"""Bounded metadata-only diagnostics for the operator-owned OA HTTPS callback."""
import contextlib
import errno
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import signal
import socket
import ssl
import time
from urllib.parse import urlsplit


@contextlib.contextmanager
def deadline(seconds):
    def expired(signum, frame):
        raise TimeoutError('DIAGNOSTIC_DEADLINE')
    previous = signal.signal(signal.SIGALRM, expired)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if previous_timer[0]:
            signal.setitimer(signal.ITIMER_REAL, *previous_timer)


def failure(exc):
    rows, seen, current = [], set(), exc
    categories = set()
    while current is not None and id(current) not in seen and len(rows) < 8:
        seen.add(id(current))
        name = type(current).__name__
        row = {'error_class': name if name.isidentifier() else 'OTHER'}
        # Inspect text only for fixed categories; never emit the raw error or URL.
        lower = str(current).lower()
        if isinstance(current, ssl.SSLCertVerificationError) or 'certificate_verify_failed' in lower:
            categories.add('TLS_CERTIFICATE_VERIFICATION')
        elif isinstance(current, ssl.SSLError):
            categories.add('TLS_PROTOCOL')
        if isinstance(current, socket.gaierror):
            categories.add('DNS_RESOLUTION')
        if isinstance(current, TimeoutError) or name.endswith('Timeout'):
            categories.add('TIMEOUT')
        number = getattr(current, 'errno', None)
        if type(number) is int:
            row['errno'] = number
            if number == errno.ECONNREFUSED:
                categories.add('TCP_REFUSED')
            if number in (errno.ENETUNREACH, errno.EHOSTUNREACH):
                categories.add('NETWORK_UNREACHABLE')
        verify = getattr(current, 'verify_code', None)
        if type(verify) is int:
            row['certificate_verify_code'] = verify
            row['certificate_verify_category'] = {
                9: 'NOT_YET_VALID', 10: 'EXPIRED', 18: 'SELF_SIGNED_LEAF',
                19: 'SELF_SIGNED_CHAIN', 20: 'ISSUER_NOT_FOUND',
                21: 'CHAIN_UNVERIFIABLE', 62: 'HOSTNAME_MISMATCH',
            }.get(verify, 'OTHER')
        rows.append(row)
        current = current.__cause__ or current.__context__
    priority = ('TLS_CERTIFICATE_VERIFICATION', 'DNS_RESOLUTION', 'TIMEOUT',
                'TCP_REFUSED', 'NETWORK_UNREACHABLE', 'TLS_PROTOCOL')
    return {'status': 'INCOMPLETE', 'category': next((v for v in priority if v in categories), 'OTHER'),
            'exception_chain': rows, 'raw_error_exported': False}


def resolve(host, port):
    with deadline(5):
        raw = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    rows = []
    for family, kind, proto, _, address in raw:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        ip = str(ipaddress.ip_address(address[0]))
        if ip not in [r['ip'] for r in rows]:
            rows.append({'ip': ip, 'family': family, 'sockaddr': address})
    if not rows or len(rows) > 8:
        raise ValueError('DNS_ADDRESS_COUNT_OUTSIDE_LIMIT')
    return rows


def tcp_check(row):
    started = time.monotonic()
    try:
        with socket.socket(row['family'], socket.SOCK_STREAM) as stream:
            stream.settimeout(5)
            stream.connect(row['sockaddr'])
        result = {'status': 'PASS'}
    except Exception as exc:
        result = failure(exc)
    result.update(address=row['ip'], elapsed_seconds=round(time.monotonic() - started, 3), attempts=1)
    return result


def ca_inventory(config):
    candidates = [('OA_POLICY_FLOW_CALLBACK_CA_BUNDLE', config.callback_ca_bundle)]
    candidates += [(key, os.environ.get(key)) for key in
                   ('SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE', 'DEERFLOW_BROWSER_CA_BUNDLE')]
    rows, usable = [], []
    for name, path in candidates:
        row = {'variable': name, 'configured': bool(path), 'path_exported': False}
        if path:
            try:
                p = Path(path)
                if not p.is_file() or not 0 < p.stat().st_size <= 4 * 1024 * 1024:
                    raise ValueError('CA_FILE_INVALID')
                with p.open('rb') as stream:
                    raw = stream.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise ValueError('CA_FILE_LIMIT')
                # Use the production TLS loader. A PEM bundle may contain UTF-8
                # comments outside its ASCII certificate blocks; decoding the
                # entire file as ASCII falsely rejects those valid bundles.
                ctx = ssl.create_default_context(cafile=str(p))
                with p.open('rb') as stream:
                    after = stream.read(4 * 1024 * 1024 + 1)
                if raw != after:
                    raise ValueError('CA_FILE_CHANGED_DURING_LOAD')
                stats = ctx.cert_store_stats()
                if stats['x509'] < 1:
                    raise ValueError('NO_CERTIFICATES')
                row.update(status='USABLE', bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                           ca_count=stats['x509_ca'], certificate_count=stats['x509'],
                           loading_method='OPENSSL_CAFILE')
                # Frozen context: do not re-read a changed file at connect time.
                usable.append((name, ctx, row['sha256']))
            except Exception as exc:
                row.update(status='LOAD_FAILED', error_class=type(exc).__name__,
                           loading_method='OPENSSL_CAFILE')
        rows.append(row)
    return rows, usable


def head(url, verify, ca_label):
    import httpx
    started = time.monotonic()
    attempts = 0
    try:
        with deadline(12):
            with httpx.Client(verify=verify, trust_env=False, follow_redirects=False,
                              timeout=8.0) as client:
                attempts = 1
                with client.stream('HEAD', url) as response:
                    result = {'status': 'HTTP_RESPONSE_OBSERVED', 'http_status': response.status_code,
                              'tls_handshake': 'PASS'}
    except Exception as exc:
        result = failure(exc)
    result.update(ca_source=ca_label, head_requests=attempts,
                  elapsed_seconds=round(time.monotonic() - started, 3),
                  tls_verification='REQUIRED', hostname_verification='REQUIRED',
                  proxy_used=False, redirects_followed=False, credentials_sent=False,
                  response_headers_exported=False, response_body_exported=False)
    return result


def diagnose(config):
    parsed = urlsplit(config.callback_url)
    if (parsed.scheme != 'https' or parsed.hostname != 'grc-web.apps.shdev.ocpx.cnooc'
            or parsed.port not in (None, 443) or parsed.username or parsed.password
            or parsed.fragment or parsed.query):
        raise ValueError('CALLBACK_TARGET_CHANGED')
    result = {'target_host': parsed.hostname, 'target_port': 443,
              'path_and_query_exported': False, 'head_requests': 0,
              'scope': 'TRANSPORT_DIAGNOSIS_NOT_OA_BUSINESS_ACCEPTANCE',
              'proxy_policy': 'DIRECT_TRUST_ENV_FALSE', 'configuration_modified': False}
    inventory, usable = ca_inventory(config)
    result['ca_inventory'] = inventory
    try:
        rows = resolve(parsed.hostname, 443)
        result['dns'] = {'status': 'PASS', 'addresses': [r['ip'] for r in rows]}
        result['tcp'] = tcp_check(rows[0])
    except Exception as exc:
        result['dns'] = failure(exc)
        result['tcp'] = {'status': 'NOT_ATTEMPTED'}
    label = 'OA_POLICY_FLOW_CALLBACK_CA_BUNDLE' if config.callback_ca_bundle else 'HTTPX_DEFAULT_TRUST_ENV_FALSE'
    current = head(config.callback_url, config.callback_ca_bundle or True, label)
    result['current_transport'] = current
    result['head_requests'] += current['head_requests']
    alternate = {'status': 'NOT_ATTEMPTED'}
    if current.get('category') == 'TLS_CERTIFICATE_VERIFICATION':
        configured_hash = next((r.get('sha256') for r in inventory if r['variable'] == label), None)
        candidates = [(name, ctx) for name, ctx, digest in usable
                      if name != 'OA_POLICY_FLOW_CALLBACK_CA_BUNDLE' and digest != configured_hash]
        if candidates:
            name, context = candidates[0]
            alternate = head(config.callback_url, context, name)
            alternate['diagnostic_only_not_applied'] = True
            result['head_requests'] += alternate['head_requests']
    result['existing_ca_comparison'] = alternate
    result['status'] = 'CALLBACK_TRANSPORT_DIAGNOSED_NOT_ENABLED'
    return result

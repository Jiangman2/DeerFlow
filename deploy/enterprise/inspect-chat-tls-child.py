"""Bounded public-certificate inventory inside an existing Python container."""
import ast
import base64
import contextlib
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import signal
import socket
import ssl
import stat
import time
from urllib.parse import urlsplit

CA_VARS = ('SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE',
           'DEERFLOW_BROWSER_CA_BUNDLE', 'OA_POLICY_FLOW_CALLBACK_CA_BUNDLE',
           'GLM_5_3_FLASH_CA_BUNDLE', 'NODE_EXTRA_CA_CERTS')
PROXY_VARS = ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy')
TARGETS = ('api.tavily.com', 'www.bing.com', 'www.baidu.com', 'aiapi.cnooc.com.cn', 'model.ai.cnooc')
SOURCE_ROOT = Path('/app/backend/packages/harness/deerflow')
TAVILY_SOURCE_SHA = '990967ef654db706183253f6505f526268c5681bbafbded16e37a0140e976576'
INCIDENT_THREAD = '678bb3ea-3106-4c81-b9bc-5b43edfd656d'
SOURCE_NAMES = ('community/tavily/tools.py', 'community/browser_automation/session.py',
                'community/browser_automation/tools.py', 'models/robust_openai.py',
                'models/enterprise_glm.py', 'models/factory.py')
PEM = re.compile(rb'-----BEGIN CERTIFICATE-----\s+.+?\s+-----END CERTIFICATE-----', re.S)
MAX_FILE = 8 * 1024 * 1024


def sha(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def error_info(error):
    found, seen, pending = [], set(), [error]
    labels = set()
    tokens = {'CERTIFICATE_UNTRUSTED': ('certificate_verify_failed', 'certificate verify failed', 'err_cert_authority_invalid'),
              'CERTIFICATE_EXPIRED': ('certificate has expired', 'certificate_has_expired', 'err_cert_date_invalid'),
              'HOSTNAME_MISMATCH': ('hostname mismatch', 'err_cert_common_name_invalid'),
              'WEAK_KEY_OR_SIGNATURE': ('key too weak', 'key_too_weak', 'digest too weak'),
              'TLS_PROTOCOL': ('wrong_version_number', 'ssl handshake', 'sslv3_alert', 'unexpected_eof', 'err_ssl_protocol_error', 'tlsv1_alert'),
              'PROXY': ('proxyerror', 'proxy error', 'tunnel connection'),
              'DNS': ('name resolution', 'name or service not known', 'getaddrinfo failed'),
              'TIMEOUT': ('timed out', 'timeout', 'deadline'),
              'CONNECTION': ('connection refused', 'connection reset', 'network is unreachable')}
    while pending and len(seen) < 12:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        text = str(current).lower()  # Classify locally; never return raw errors/URLs.
        labels.update(k for k, words in tokens.items() if any(w in text for w in words))
        row = {'class': type(current).__name__}
        if isinstance(current, ssl.SSLCertVerificationError):
            row['verify_code'] = current.verify_code
            labels.add('CERTIFICATE_UNTRUSTED')
        found.append(row)
        for value in (current.__cause__, current.__context__, getattr(current, 'reason', None)):
            if isinstance(value, BaseException):
                pending.append(value)
        pending.extend(x for x in current.args if isinstance(x, BaseException))
    return {'status': 'FAILED', 'categories': sorted(labels) or ['OTHER'], 'exception_chain': found,
            'raw_error_exported': False}


def safe_path(value):
    if not isinstance(value, str) or not value.startswith('/') or len(value) > 512:
        return None
    if any(c in value for c in ('\n', '\r', '\x00', '?', '#', '://')):
        return None
    if any(part in ('..', 'token', '.ssh', '.env') for part in value.split('/')) or value.lower().endswith(('.key', '.p12', '.pfx')):
        return None
    return value


def bounded_public_file(value):
    path = safe_path(value)
    if path is None:
        raise ValueError('UNSAFE_CERTIFICATE_PATH')
    p = Path(path)
    info = p.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_FILE:
        raise ValueError('CERTIFICATE_FILE_OUTSIDE_LIMIT')
    with p.open('rb') as handle:
        data = handle.read(MAX_FILE + 1)
    if len(data) > MAX_FILE:
        raise ValueError('CERTIFICATE_FILE_OUTSIDE_LIMIT')
    if b'PRIVATE KEY-----' in data:
        raise ValueError('PRIVATE_KEY_MARKER_IN_CA_FILE')
    return p, data


def cert_metadata(der, now):
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.x509.oid import NameOID
    c = x509.load_der_x509_certificate(der)
    def cn(name):
        values = name.get_attributes_for_oid(NameOID.COMMON_NAME)
        # Public certificate identities only; no full DN or email attributes.
        return values[0].value[:160] if values else None
    try:
        is_ca = c.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    except x509.ExtensionNotFound:
        is_ca = None
    before = getattr(c, 'not_valid_before_utc', None)
    after = getattr(c, 'not_valid_after_utc', None)
    if before is None:
        before = c.not_valid_before.replace(tzinfo=datetime.timezone.utc)
        after = c.not_valid_after.replace(tzinfo=datetime.timezone.utc)
    key = c.public_key()
    public = key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return {'sha256': sha(der), 'spki_sha256': sha(public), 'common_name': cn(c.subject),
            'issuer_common_name': cn(c.issuer), 'is_ca': is_ca, 'self_issued': c.subject == c.issuer,
            'classification': 'ROOT_CANDIDATE' if is_ca and c.subject == c.issuer else 'INTERMEDIATE_CA' if is_ca else 'LEAF_OR_UNSPECIFIED',
            'not_before_utc': before.isoformat(), 'not_after_utc': after.isoformat(),
            'time_status': 'EXPIRED' if now > after else 'NOT_YET_VALID' if now < before else 'VALID_NOW',
            'key_type': type(key).__name__, 'key_bits': getattr(key, 'key_size', None),
            'signature_hash': c.signature_hash_algorithm.name if c.signature_hash_algorithm else None}


def inspect_bundle(value, details=True):
    row = {'path': safe_path(value), 'path_valid': safe_path(value) is not None}
    try:
        p, raw = bounded_public_file(value)
        row.update(bytes=len(raw), sha256=sha(raw), resolved_path=str(p.resolve()),
                   file_mtime_utc=datetime.datetime.fromtimestamp(p.stat().st_mtime, datetime.timezone.utc).isoformat())
        blocks = PEM.findall(raw)
        if len(blocks) > 512:
            raise ValueError('CERTIFICATE_COUNT_LIMIT')
        ders = [ssl.PEM_cert_to_DER_cert(b.decode('ascii')) for b in blocks]
        row['pem_certificate_count'] = len(ders)
        if not ders:
            try:
                from cryptography import x509
                from cryptography.hazmat.primitives import serialization
                certificate = x509.load_der_x509_certificate(raw)
                ders = [certificate.public_bytes(serialization.Encoding.DER)]
                row['encoding'] = 'DER_NOT_PEM'
            except Exception:
                row['encoding'] = 'UNRECOGNIZED_OR_PARSER_UNAVAILABLE'
        else:
            row['encoding'] = 'PEM'
        row['certificate_set_sha256'] = sha(canonical(sorted(sha(b) for b in ders)))
        try:
            ctx = ssl.create_default_context(cafile=str(p))
            row.update(openssl_load='PASS', store=ctx.cert_store_stats())
        except Exception as exc:
            row['openssl_load'] = error_info(exc)
        now = datetime.datetime.now(datetime.timezone.utc)
        try:
            metadata = [cert_metadata(b, now) for b in ders]
            row['counts'] = {key: sum(c['classification'] == key for c in metadata)
                             for key in ('ROOT_CANDIDATE', 'INTERMEDIATE_CA', 'LEAF_OR_UNSPECIFIED')}
            row['time_counts'] = {key: sum(c['time_status'] == key for c in metadata)
                                 for key in ('VALID_NOW', 'EXPIRED', 'NOT_YET_VALID')}
            row['certificates'] = metadata[:32] if details and len(metadata) <= 32 else [c for c in metadata if c['classification'] == 'LEAF_OR_UNSPECIFIED' or c['time_status'] != 'VALID_NOW'][:32]
            row['certificate_details_truncated'] = len(row['certificates']) < len(metadata)
            row['all_spki_set_sha256'] = sha(canonical(sorted(c['spki_sha256'] for c in metadata)))
        except ImportError:
            row['classification_status'] = 'CRYPTOGRAPHY_NOT_INSTALLED'
        row['status'] = 'OBSERVED'
    except Exception as exc:
        row.update(status='UNAVAILABLE', error_class=type(exc).__name__)
        if isinstance(exc, ValueError) and str(exc) in ('UNSAFE_CERTIFICATE_PATH', 'PRIVATE_KEY_MARKER_IN_CA_FILE', 'CERTIFICATE_FILE_OUTSIDE_LIMIT', 'CERTIFICATE_COUNT_LIMIT'):
            row['error_code'] = str(exc)
    return row


def proxy_metadata(value):
    if not value:
        return {'configured': False}
    try:
        u = urlsplit(value if '://' in value else 'http://' + value)
        if u.scheme not in ('http', 'https', 'socks5', 'socks5h') or not u.hostname:
            raise ValueError()
        return {'configured': True, 'scheme': u.scheme, 'hostname': u.hostname,
                'port': u.port, 'authentication_present': bool(u.username or u.password),
                'path_or_query_present': bool(u.path.strip('/') or u.query or u.fragment)}
    except Exception:
        return {'configured': True, 'status': 'INVALID_REDACTED'}


def browser_processes(expected_values):
    result = {'scope': 'CURRENT_CONTAINER_PROC_NOT_SAVED_THREADS', 'processes_checked': 0,
              'browser_processes': 0, 'broad_ignore_certificate_errors': 0, 'spki_allowlist_matches': 0,
              'spki_allowlist_mismatches': 0, 'browser_processes_without_spki': 0, 'truncated': False}
    expected = sorted(set(expected_values))
    try:
        entries = [p for p in Path('/proc').iterdir() if p.name.isdigit()]
        result['truncated'] = len(entries) > 512
        for p in entries[:512]:
            try:
                with (p / 'cmdline').open('rb') as handle:
                    data = handle.read(65537)
                if not data or len(data) > 65536:
                    continue
                args = data.decode('utf-8', 'replace').split('\0')
                result['processes_checked'] += 1
                if not any(v in Path(args[0]).name.lower() for v in ('chromium', 'chrome', 'headless_shell')):
                    continue
                if any(a.startswith('--type=') for a in args):
                    continue
                result['browser_processes'] += 1
                result['broad_ignore_certificate_errors'] += '--ignore-certificate-errors' in args
                actual = next((a.split('=', 1)[1].split(',') for a in args if a.startswith('--ignore-certificate-errors-spki-list=')), None)
                if actual is None:
                    result['browser_processes_without_spki'] += 1
                elif sorted(set(actual)) == expected:
                    result['spki_allowlist_matches'] += 1
                else:
                    result['spki_allowlist_mismatches'] += 1
            except (OSError, ValueError):
                continue
    except OSError:
        result['status'] = 'PROC_UNAVAILABLE'
    return result


def inventory():
    result = {'schema': 'portal-chat-tls-runtime.v1', 'status': 'OBSERVED', 'openssl_version': ssl.OPENSSL_VERSION,
              'sampled_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'credentials_exported': False, 'certificate_bodies_exported': False, 'files_modified': False,
              'provider_requests': 0, 'environment': {}, 'bundles': {}, 'sources': {}}
    for name in CA_VARS:
        value = os.getenv(name, '').strip()
        result['environment'][name] = {'configured': bool(value), 'path': safe_path(value)}
        if value and safe_path(value) and value not in result['bundles']:
            result['bundles'][value] = inspect_bundle(value)
    defaults = ssl.get_default_verify_paths()
    result['openssl_default_paths'] = {k: v if k.endswith('_env') else safe_path(v) for k, v in defaults._asdict().items()}
    openssl_conf = os.getenv('OPENSSL_CONF', '')
    result['openssl_configuration'] = {'configured': bool(openssl_conf), 'path': safe_path(openssl_conf)}
    if safe_path(openssl_conf):
        try:
            _, raw_conf = bounded_public_file(openssl_conf)
            result['openssl_configuration'].update(sha256=sha(raw_conf), bytes=len(raw_conf),
                security_level_directives=sorted(set(v.decode() for v in re.findall(rb'@SECLEVEL=([0-5])', raw_conf))),
                raw_configuration_exported=False)
        except Exception as exc:
            result['openssl_configuration']['error_class'] = type(exc).__name__
    try:
        result['default_context_security_level'] = getattr(ssl.create_default_context(), 'security_level', None)
    except Exception as exc:
        result['default_context_error'] = error_info(exc)
    directory = os.getenv('SSL_CERT_DIR', '')
    result['environment']['SSL_CERT_DIR'] = {'configured': bool(directory), 'path': safe_path(directory),
        'exists_as_directory': bool(safe_path(directory) and Path(directory).is_dir()),
        'directory_contents_not_enumerated': True}
    for value in (defaults.cafile, '/etc/ssl/certs/ca-certificates.crt'):
        if value and value not in result['bundles']:
            result['bundles'][value] = inspect_bundle(value, details=False)
    try:
        import certifi
        result['certifi_path'] = certifi.where()
        if certifi.where() not in result['bundles']:
            result['bundles'][certifi.where()] = inspect_bundle(certifi.where(), details=False)
    except ImportError:
        result['certifi_status'] = 'NOT_INSTALLED'
    result['proxies'] = {name: proxy_metadata(os.getenv(name)) for name in PROXY_VARS}
    result['no_proxy'] = {name: {'configured': bool(os.getenv(name)), 'entry_count': len([x for x in os.getenv(name, '').split(',') if x.strip()]),
                                'sha256': sha(os.getenv(name, '').encode())} for name in ('NO_PROXY', 'no_proxy')}
    result['upper_lower_proxy_equivalent'] = {name: os.getenv(name, '') == os.getenv(name.lower(), '') for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY')}
    result['flags'] = {name: os.getenv(name, '')[:16] if os.getenv(name, '').lower() in ('', '1', '0', 'true', 'false', 'on', 'off', 'yes', 'no') else 'SET_OTHER' for name in ('TAVILY_TLS_COMPAT', 'WEKNORA_BAILIAN_TLS_COMPAT')}
    try:
        import requests
        result['requests_routes'] = {host: proxy_metadata(requests.utils.select_proxy('https://' + host + '/', requests.utils.get_environ_proxies('https://' + host + '/'))) for host in TARGETS}
        result['requests_default_ca_path'] = requests.certs.where()
    except ImportError:
        result['requests_status'] = 'NOT_INSTALLED'
    for name in SOURCE_NAMES:
        p = SOURCE_ROOT / name
        if p.is_file() and p.stat().st_size <= 2 * 1024 * 1024:
            raw = p.read_bytes()
            result['sources'][name] = {'sha256': sha(raw), 'bytes': len(raw)}
    browser = SOURCE_ROOT / 'community/browser_automation/session.py'
    if browser.is_file():
        source = browser.read_text()
        result['browser_source_features'] = {'ca_env_hook': 'DEERFLOW_BROWSER_CA_BUNDLE' in source,
            'spki_launch_hook': '--ignore-certificate-errors-spki-list=' in source,
            'explicit_proxy_launch_present': bool(re.search(r'launch_options\[.[Pp]roxy.|launch\([^\n]*proxy\s*=', source))}
    spki_values = []
    value = os.getenv('DEERFLOW_BROWSER_CA_BUNDLE', '').strip()
    if value:
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import serialization
            _, raw = bounded_public_file(value)
            blocks = PEM.findall(raw)
            if not 0 < len(blocks) <= 256:
                raise ValueError('BROWSER_CA_CERTIFICATE_LIMIT')
            for b in blocks:
                c = x509.load_pem_x509_certificate(b)
                public = c.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
                spki_values.append(base64.b64encode(hashlib.sha256(public).digest()).decode())
            result['browser_ca'] = {'parse_status': 'PASS', 'spki_count': len(set(spki_values)),
                'mode': 'PUBLIC_KEY_ALLOWLIST_NOT_SYSTEM_CA_IMPORT',
                'diagnostic_replication_not_import_of_application': True}
        except Exception as exc:
            result['browser_ca'] = {'parse_status': 'FAILED', 'error_class': type(exc).__name__}
    else:
        result['browser_ca'] = {'parse_status': 'NOT_CONFIGURED'}
    result['live_browser_flags'] = browser_processes(spki_values)
    return result


def make_tavily_session():
    """Load only the observed TLS adapter definitions, never app config/API keys."""
    import requests
    path = SOURCE_ROOT / 'community/tavily/tools.py'
    source = path.read_text()
    if sha(path.read_bytes()) != TAVILY_SOURCE_SHA:
        raise ValueError('TAVILY_TLS_SOURCE_NOT_QUALIFIED_NO_REQUEST_SENT')
    names = ('_TavilyTLSAdapter', '_truthy_env', '_build_tavily_session')
    tree = ast.parse(source)
    selected = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    if len(selected) != len(names):
        raise ValueError('TAVILY_ADAPTER_SHAPE_CHANGED')
    module = ast.Module(body=selected, type_ignores=[]) if hasattr(ast, 'TypeIgnore') else ast.Module(body=selected)
    namespace = {'os': os, 'ssl': ssl, 'requests': requests, 'HTTPAdapter': requests.adapters.HTTPAdapter}
    exec(compile(module, '<observed-tavily-tls-definitions>', 'exec'), namespace)
    return namespace['_build_tavily_session']() or requests.Session()


def probes():
    import requests
    rows = []
    for host in TARGETS[:3]:
        url = 'https://' + host + '/'
        row = {'target_hostname': host, 'method': 'HEAD', 'maximum_requests': 1, 'requests_attempted': 0,
               'redirects_followed': False, 'response_body_exported': False,
               'application_credentials_sent': False, 'scope': 'TLS_TRANSPORT_NOT_SEARCH_OR_BROWSER_ACCEPTANCE'}
        session = None
        started = time.monotonic()
        try:
            selected = requests.utils.select_proxy(url, requests.utils.get_environ_proxies(url))
            proxy = proxy_metadata(selected)
            row['selected_proxy'] = proxy
            if proxy.get('authentication_present') or proxy.get('path_or_query_present'):
                row['status'] = 'SKIPPED_AUTHENTICATED_OR_COMPLEX_PROXY'
                continue
            session = make_tavily_session() if host == 'api.tavily.com' else requests.Session()
            session.auth = lambda request: request  # Suppress .netrc lookup.
            row['client'] = 'OBSERVED_TAVILY_TLS_HELPER' if host == 'api.tavily.com' else 'REQUESTS_DEFAULT_ENVIRONMENT'
            row['certificate_verification'] = True
            request = session.prepare_request(requests.Request('HEAD', url))
            settings = session.merge_environment_settings(url, {}, True, True, None)
            if settings.get('verify') is False:
                raise ValueError('UNVERIFIED_TLS_REFUSED')
            row['requests_attempted'] = 1
            with session.send(request, timeout=(4, 6), allow_redirects=False, **settings) as response:
                row.update(status='HTTP_RESPONSE_OBSERVED_TLS_ACCEPTED', http_status=response.status_code)
                connection = getattr(response.raw, '_connection', None)
                sock = getattr(connection, 'sock', None)
                try:
                    if sock is not None and hasattr(sock, 'getpeercert'):
                        row['peer_certificate'] = cert_metadata(sock.getpeercert(binary_form=True), datetime.datetime.now(datetime.timezone.utc))
                        row['tls_version'] = sock.version()
                    else:
                        row['peer_certificate_status'] = 'SOCKET_NOT_EXPOSED'
                except Exception as exc:
                    row['peer_certificate_status'] = 'METADATA_UNAVAILABLE'
                    row['peer_metadata_error_class'] = type(exc).__name__
        except Exception as exc:
            row.update(error_info(exc))
        finally:
            if session is not None:
                session.close()
            row['elapsed_seconds'] = round(time.monotonic() - started, 3)
            rows.append(row)
    return {'schema': 'portal-chat-tls-probes.v1', 'status': 'PROBES_OBSERVED', 'probes': rows,
            'maximum_head_requests': 3, 'provider_inference_requests': 0, 'browser_launched': False,
            'credentials_exported': False, 'configuration_modified': False}


def incident_event(row):
    """Return fixed-vocabulary classifications, never stored conversation text."""
    text = row.get('content') or ''
    name, tool_status = None, None
    if row['event_type'] == 'llm.tool.result':
        try:
            value = json.loads(text)
        except (ValueError, TypeError):
            return None
        if not isinstance(value, dict) or value.get('type') != 'tool':
            return None
        allowed = ('web_search', 'web_fetch', 'browser_navigate', 'browser_click',
                   'browser_snapshot', 'browser_evaluate', 'browser_look', 'bash')
        name = value.get('name')
        if name not in allowed:
            return None
        tool_status = value.get('status') if value.get('status') in ('success', 'error') else None
        content = value.get('content', '')
        text = content if isinstance(content, str) else json.dumps(content)
    elif row['event_type'] not in ('run.error', 'llm.error'):
        return None
    # Words in model reasoning, prompts and successful web pages are not proof
    # of a transport failure. Require concrete exception / browser error markers.
    markers = ('SSLError', 'SSLCertVerificationError', 'CERTIFICATE_VERIFY_FAILED',
               'ERR_CERT_AUTHORITY_INVALID', 'ERR_CERT_DATE_INVALID',
               'ERR_CERT_COMMON_NAME_INVALID', 'ERR_SSL_PROTOCOL_ERROR',
               'SSL_ERROR', 'WRONG_VERSION_NUMBER', 'UNEXPECTED_EOF_WHILE_READING',
               'ProxyError', 'CERTIFICATE_HAS_EXPIRED', 'TLSV1_ALERT',
               'certificate verify failed', 'unable to get local issuer certificate')
    matched = [m for m in markers if m.lower() in text.lower()]
    if not matched:
        return None
    return {'seq': row['seq'], 'created_at_utc': str(row['created_at']),
        'run_id_sha256': sha(str(row['run_id']).encode()), 'event_type': row['event_type'],
        'tool_name': name, 'tool_status': tool_status, 'error_markers': matched,
        'classification': error_info(RuntimeError(text))['categories'],
        'known_target_mentions': [h for h in TARGETS if h in text.lower()],
        'evidence': 'STORED_TOOL_RESULT_ERROR_MARKERS' if name else 'STORED_RUNTIME_ERROR',
        'historical_transport_origin_not_independently_proven': True,
        'raw_content_exported': False}


async def incident_async():
    import asyncpg
    from deerflow.config import get_app_config
    cfg = get_app_config().database
    if cfg.backend != 'postgres':
        raise ValueError('POSTGRES_REQUIRED')
    conn = await asyncpg.connect(cfg.postgres_url.replace('postgresql+asyncpg://', 'postgresql://', 1), timeout=8, command_timeout=10)
    result = {'thread_id': INCIDENT_THREAD, 'database_readonly': True, 'database_modified': False,
        'scope': 'ONE_OPERATOR_SUPPLIED_THREAD_TOOL_AND_RUNTIME_ERRORS_ONLY',
        'raw_messages_exported': False, 'credentials_exported': False, 'matches': [],
        'maximum_events': 1000, 'maximum_content_bytes': 16777216, 'truncated': False}
    try:
        async with conn.transaction(readonly=True, isolation='repeatable_read'):
            result['transaction_read_only'] = await conn.fetchval('SHOW transaction_read_only')
            if result['transaction_read_only'] != 'on':
                raise ValueError('READONLY_TRANSACTION_REQUIRED')
            runs = await conn.fetch('SELECT status,created_at,updated_at FROM runs WHERE thread_id=$1 ORDER BY created_at DESC LIMIT 20', INCIDENT_THREAD)
            result['recent_runs'] = [{k: str(v) if v is not None else None for k, v in dict(r).items()} for r in runs]
            kinds = ['llm.tool.result', 'run.error', 'llm.error']
            counts = await conn.fetch('SELECT event_type,count(*) AS count FROM run_events WHERE thread_id=$1 AND event_type=ANY($2::text[]) GROUP BY event_type', INCIDENT_THREAD, kinds)
            result['event_counts'] = {r['event_type']: r['count'] for r in counts}
            result['truncated'] = sum(result['event_counts'].values()) > 1000
            sql = '''SELECT seq,run_id,event_type,created_at,octet_length(content) AS original_bytes,
                     CASE WHEN octet_length(content)<=262144 THEN content ELSE NULL END AS content
                     FROM run_events WHERE thread_id=$1 AND event_type=ANY($2::text[])
                     ORDER BY seq DESC LIMIT 1000'''
            scanned = total = oversized = 0
            async for row in conn.cursor(sql, INCIDENT_THREAD, kinds, prefetch=20):
                scanned += 1
                if row['original_bytes'] and row['original_bytes'] > 262144:
                    oversized += 1
                    continue
                total += row['original_bytes'] or 0
                if total > result['maximum_content_bytes']:
                    result['truncated'] = True
                    break
                match = incident_event(dict(row))
                if match and len(result['matches']) < 60:
                    result['matches'].append(match)
                elif match:
                    result['truncated'] = True
            result.update(events_scanned=scanned, content_bytes_scanned=total, oversized_events_skipped=oversized)
            result['truncated'] = result['truncated'] or oversized > 0
    finally:
        await conn.close()
    result['status'] = 'SCOPED_INCIDENT_EVIDENCE_OBSERVED'
    result['absence_of_matches_does_not_disprove_failure'] = True
    return result


def incident():
    import asyncio
    # App config may log initialization metadata; discard it, do not export it.
    class Discard:
        def write(self, value):
            return len(value)
        def flush(self):
            pass
    with contextlib.redirect_stdout(Discard()), contextlib.redirect_stderr(Discard()):
        return asyncio.run(incident_async())


def main(mode):
    def expired(*unused):
        raise TimeoutError('CHILD_DEADLINE')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(45 if mode == 'inventory' else 50)
    try:
        if mode == 'inventory':
            return inventory()
        if mode == 'probe':
            return probes()
        if mode == 'incident':
            return incident()
        raise ValueError('UNKNOWN_MODE')
    except Exception as exc:
        return {'schema': 'portal-chat-tls-child.v1', 'status': 'INCOMPLETE', 'error': error_info(exc)}
    finally:
        signal.alarm(0)

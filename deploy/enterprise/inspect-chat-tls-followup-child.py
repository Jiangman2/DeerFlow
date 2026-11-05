"""Append to the sealed r1 child: focused authenticated-proxy evidence only."""
import collections
import importlib.metadata
import shlex

EXPECTED_CA_SHA = '84c58afec9e48e1bd7b41d832ff6986b74a6565b4473f7cc77b868900b4d76af'
CA_PATH = '/etc/deerflow-ca/ca-bundle.crt'


def qualified_proxy(host):
    import requests
    url = 'https://' + host + '/'
    value = requests.utils.select_proxy(url, requests.utils.get_environ_proxies(url))
    if not value:
        raise ValueError('EXPECTED_PROXY_MISSING')
    parsed = urlsplit(value)
    if (parsed.scheme, parsed.hostname, parsed.port) != ('http', '10.68.211.1', 8080):
        raise ValueError('PROXY_ENDPOINT_CHANGED')
    if parsed.path not in ('', '/') or parsed.query or parsed.fragment or not parsed.username or not parsed.password:
        raise ValueError('PROXY_SHAPE_CHANGED')
    if any(c in value for c in ('\r', '\n', '\x00')):
        raise ValueError('PROXY_CONTROL_CHARACTER')
    return value


def proxy_probes(matrix=False):
    import requests
    _, raw = bounded_public_file(CA_PATH)
    if sha(raw) != EXPECTED_CA_SHA:
        raise ValueError('BASELINE_CA_CHANGED')
    rows = []
    plans = [(h, profile) for h in TARGETS[:3] for profile in ('production_tavily_context', 'requests_default_context')] if matrix else [('api.tavily.com', 'production_tavily_context')]
    for host, profile in plans:
        row = {'target_hostname': host, 'profile': profile, 'method': 'HEAD', 'requests_attempted': 0,
            'certificate_verification': True, 'hostname_verification': True,
            'model_or_search_api_key_sent': False, 'proxy_credentials_exported': False,
            'redirects_followed': False, 'response_body_exported': False}
        started = time.monotonic()
        session = None
        try:
            proxy = qualified_proxy(host)
            session = make_tavily_session() if profile == 'production_tavily_context' else requests.Session()
            session.trust_env = False  # Only the just-validated proxy is eligible.
            session.auth = lambda request: request
            session.proxies = {}
            adapter = session.adapters['https://']
            if adapter.max_retries.total != 0:
                raise ValueError('AUTOMATIC_RETRY_REFUSED')
            ctx = getattr(adapter, '_tavily_ssl_context', None)
            if profile == 'production_tavily_context' and (ctx is None or not ctx.check_hostname or ctx.verify_mode != ssl.CERT_REQUIRED):
                raise ValueError('QUALIFIED_TAVILY_CONTEXT_REQUIRED')
            row['context_security_level'] = getattr(ctx, 'security_level', None) if ctx else 'REQUESTS_DEFAULT'
            request = session.prepare_request(requests.Request('HEAD', 'https://' + host + '/'))
            if request.body or any(k.lower() in ('authorization', 'proxy-authorization', 'cookie') for k in request.headers):
                raise ValueError('ORIGIN_AUTHORIZATION_REFUSED')
            row['requests_attempted'] = 1
            row['proxy_authentication'] = 'EXISTING_CREDENTIAL_USED_ONLY_AT_PINNED_CONFIGURED_PROXY'
            with session.send(request, proxies={'https': proxy}, verify=CA_PATH, stream=True,
                              allow_redirects=False, timeout=(3, 5)) as response:
                row.update(status='HTTP_RESPONSE_TLS_ACCEPTED', http_status=response.status_code)
                sock = getattr(getattr(response.raw, '_connection', None), 'sock', None)
                try:
                    if sock is not None:
                        row['peer_certificate'] = cert_metadata(sock.getpeercert(binary_form=True), datetime.datetime.now(datetime.timezone.utc))
                        row['tls_version'] = sock.version()
                    else:
                        row['peer_certificate_status'] = 'SOCKET_UNAVAILABLE'
                except Exception as exc:
                    row['peer_metadata_error_class'] = type(exc).__name__
        except Exception as exc:
            row.update(error_info(exc))
        finally:
            if session is not None:
                session.close()
            row['elapsed_seconds'] = round(time.monotonic() - started, 3)
            rows.append(row)
    return {'status': 'PROXY_TRANSPORT_OBSERVED', 'requests': rows,
        'automatic_retry': False, 'maximum_head_requests': len(plans), 'provider_inference_requests': 0,
        'no_validation_disabled': True, 'production_configuration_modified': False}


def browser_main_args(args):
    """Exclude crash handlers and child process titles before counting browsers."""
    if len(args) == 1:
        try:
            args = shlex.split(args[0])
        except ValueError:
            return None
    if not args:
        return None
    base = Path(args[0]).name
    if base not in ('chrome', 'chromium', 'chromium-browser', 'headless_shell', 'chrome-headless-shell'):
        return None
    if any(a.startswith('--type=') for a in args) or '--type' in args:
        return None
    return args


def focused_browser_flags():
    result = {'scope': 'BROWSER_MAIN_PROCESSES_ONLY_NOT_HELPERS', 'main_processes': 0,
        'spki_present': 0, 'spki_matches_current_bundle': 0, 'spki_missing': 0,
        'broad_ignore': 0, 'explicit_proxy_flag': 0, 'processes_truncated': False}
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    _, bundle = bounded_public_file(CA_PATH)
    expected = sorted(set(base64.b64encode(hashlib.sha256(x509.load_pem_x509_certificate(b).public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)).digest()).decode() for b in PEM.findall(bundle)))
    pids = [p for p in Path('/proc').iterdir() if p.name.isdigit()]
    result['processes_truncated'] = len(pids) > 512
    for p in pids[:512]:
        try:
            with (p / 'cmdline').open('rb') as f:
                data = f.read(65537)
            if len(data) > 65536:
                continue
            args = browser_main_args([s for s in data.decode('utf-8', 'replace').split('\0') if s])
            if args is None:
                continue
            result['main_processes'] += 1
            actual = next((a.split('=', 1)[1].split(',') for a in args if a.startswith('--ignore-certificate-errors-spki-list=')), None)
            result['spki_present'] += actual is not None
            result['spki_missing'] += actual is None
            result['spki_matches_current_bundle'] += actual is not None and sorted(set(actual)) == expected
            result['broad_ignore'] += '--ignore-certificate-errors' in args
            result['explicit_proxy_flag'] += any(a.startswith('--proxy-server=') or a.startswith('--proxy-pac-url=') for a in args)
        except (OSError, ValueError):
            continue
    return result


def certificate_differences():
    import certifi
    known = set()
    for path in (certifi.where(), '/etc/ssl/certs/ca-certificates.crt'):
        _, raw = bounded_public_file(path)
        known.update(sha(ssl.PEM_cert_to_DER_cert(b.decode())) for b in PEM.findall(raw))
    _, raw = bounded_public_file(CA_PATH)
    if sha(raw) != EXPECTED_CA_SHA:
        raise ValueError('BASELINE_CA_CHANGED')
    extras, intermediates = [], []
    for block in PEM.findall(raw):
        der = ssl.PEM_cert_to_DER_cert(block.decode())
        meta = cert_metadata(der, datetime.datetime.now(datetime.timezone.utc))
        if sha(der) not in known:
            extras.append(meta)
        if meta['is_ca'] and not meta['self_issued']:
            intermediates.append(meta)
    return {'custom_bundle_sha256': sha(raw), 'compared_with': 'IMAGE_SYSTEM_AND_CERTIFI_UNION',
        'extra_certificates': extras[:80], 'extra_count': len(extras),
        'intermediate_certificates': intermediates[:16], 'certificate_bodies_exported': False}


def event_summary(row):
    """Handle JSON wrappers and unlabelled error results; no prose is returned."""
    raw = row.get('content') or ''
    value, shape = raw, []
    for _ in range(4):
        if isinstance(value, str):
            try:
                value = json.loads(value)
                shape.append('json')
                continue
            except ValueError:
                break
        if isinstance(value, dict):
            child = next((key for key in ('data', 'kwargs', 'message') if isinstance(value.get(key), dict) and ('content' in value[key] or 'name' in value[key])), None)
            if child:
                value = value[child]
                shape.append(child)
                continue
        break
    allowed = ('web_search', 'web_fetch', 'browser_navigate', 'browser_click', 'browser_snapshot',
               'browser_evaluate', 'browser_look', 'bash', 'task', 'read_file', 'write_file', 'ls')
    name = value.get('name') if isinstance(value, dict) else None
    name = name if name in allowed else 'OTHER_OR_MISSING'
    status = value.get('status') if isinstance(value, dict) else None
    status = status if status in ('success', 'error') else None
    # Event kind is already restricted to persisted tool results/runtime errors.
    # A mention alone is reported as a mention, not proof of a failed handshake.
    text = json.dumps(value, ensure_ascii=False).lower()
    labels = ('ssl', 'tls', 'certificate', '证书', 'proxy', '代理', 'handshake', '握手',
              'unable to get local issuer', 'self-signed', 'key too weak', 'digest too weak',
              'timed out', 'err_cert', 'err_ssl', 'unexpected eof', 'connection reset')
    markers = [label for label in labels if label in text]
    kinds = ('SSLError', 'SSLCertVerificationError', 'ProxyError', 'ConnectError', 'TimeoutError',
             'CERTIFICATE_VERIFY_FAILED', 'ERR_CERT_AUTHORITY_INVALID', 'ERR_CERT_DATE_INVALID',
             'ERR_SSL_PROTOCOL_ERROR', 'ERR_CERT_COMMON_NAME_INVALID', 'WRONG_VERSION_NUMBER',
             'UNEXPECTED_EOF_WHILE_READING', 'ERR_TUNNEL_CONNECTION_FAILED', 'ERR_PROXY_CONNECTION_FAILED',
             'ERR_CONNECTION_CLOSED', 'ERR_CONNECTION_RESET', 'EE_KEY_TOO_SMALL', 'CA_MD_TOO_WEAK')
    codes = [code for code in kinds if code.lower() in text]
    return {'seq': row['seq'], 'created_at_utc': str(row['created_at']), 'event_type': row['event_type'],
        'wrapper_shape': shape, 'top_level_type': type(value).__name__, 'tool_name': name,
        'message_type': value.get('type') if isinstance(value, dict) and value.get('type') in ('tool', 'ai', 'human') else 'OTHER_OR_MISSING',
        'tool_status': status, 'error_codes': codes, 'keyword_mentions': markers,
        'known_target_mentions': [h for h in TARGETS if h in text],
        'classification': error_info(RuntimeError(raw))['categories'] if codes else [],
        'evidence_is_keyword_classification_not_raw_exception': True,
        'raw_text_exported': False}


original_incident_async = incident_async


async def incident_async():
    global incident_event
    original_classifier = incident_event
    incident_event = event_summary
    try:
        result = await original_incident_async()
        result['event_summaries'] = result.pop('matches')
        result['schema'] = 'portal-scoped-tls-event-shapes.v2'
        return result
    finally:
        incident_event = original_classifier


def main(mode):
    def expired(*args):
        raise TimeoutError('CHILD_DEADLINE')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(55)
    try:
        if mode in ('proxy', 'matrix'):
            return proxy_probes(mode == 'matrix')
        if mode == 'details':
            result = {'status': 'DETAILS_OBSERVED', 'certificates': certificate_differences(),
                'browser_processes': focused_browser_flags(), 'versions': {}}
            for name in ('requests', 'urllib3', 'tavily-python', 'playwright', 'cryptography'):
                try:
                    result['versions'][name] = importlib.metadata.version(name)
                except importlib.metadata.PackageNotFoundError:
                    result['versions'][name] = 'NOT_FOUND'
            result['incident'] = incident()
            return result
        raise ValueError('UNKNOWN_MODE')
    except Exception as exc:
        return {'status': 'INCOMPLETE', 'error': error_info(exc)}
    finally:
        signal.alarm(0)

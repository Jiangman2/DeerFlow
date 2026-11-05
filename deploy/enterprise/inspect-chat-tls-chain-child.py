"""Append to r1+r2 helpers. Verified TLS handshakes only, no origin HTTP."""
import ipaddress
from urllib.parse import unquote


class ChainDeadline(BaseException):
    pass


def connect_proxy_tunnel(host):
    if host not in TARGETS[:3]:
        raise ValueError('TARGET_NOT_ALLOWED')
    proxy = urlsplit(qualified_proxy(host))
    username, password = unquote(proxy.username), unquote(proxy.password)
    if ':' in username or any(c in username + password for c in ('\r', '\n', '\x00')):
        raise ValueError('PROXY_CREDENTIAL_SHAPE')
    authorization = base64.b64encode((username + ':' + password).encode('latin-1'))
    connection = socket.create_connection((proxy.hostname, proxy.port), timeout=5)
    try:
        connection.settimeout(5)
        connection.sendall(('CONNECT ' + host + ':443 HTTP/1.1\r\nHost: ' + host + ':443\r\n').encode('ascii')
                           + b'Proxy-Authorization: Basic ' + authorization + b'\r\n\r\n')
        header = bytearray()
        while not header.endswith(b'\r\n\r\n') and len(header) < 16384:
            part = connection.recv(1)
            if not part:
                break
            header.extend(part)
        if not header.endswith(b'\r\n\r\n'):
            raise ValueError('PROXY_HEADER_INCOMPLETE_OR_OVERSIZED')
        first = bytes(header).split(b'\r\n', 1)[0]
        if not re.fullmatch(rb'HTTP/1\.[01] 200(?: [^\r\n]*)?', first):
            raise ValueError('PROXY_CONNECT_NOT_200')
        return connection
    except BaseException:
        connection.close()
        raise


def connect_direct_public(host):
    if host not in ('www.bing.com', 'www.baidu.com'):
        raise ValueError('DIRECT_TARGET_NOT_ALLOWED')
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or len(addresses) > 32:
        raise ValueError('DNS_ADDRESS_LIMIT')
    # Validate every answer, then pin one address. No private-address fallback.
    for item in addresses:
        address = ipaddress.ip_address(item[4][0])
        if not address.is_global or address.is_multicast or address.is_unspecified:
            raise ValueError('DIRECT_NONPUBLIC_ADDRESS_REFUSED')
    family, kind, protocol, _, address = addresses[0]
    connection = socket.socket(family, kind, protocol)
    try:
        connection.settimeout(5)
        connection.connect(address)
        return connection
    except BaseException:
        connection.close()
        raise


def observed_chain(secured, trusted_ders):
    # CPython 3.12 exposes this through _sslobj. Unlike getpeercert(), it also
    # returns the presented chain after verification failed. Never retry with
    # CERT_NONE. A future unsupported runtime reports evidence unavailable.
    getter = getattr(secured, 'get_unverified_chain', None)
    if getter is None:
        getter = getattr(getattr(secured, '_sslobj', None), 'get_unverified_chain', None)
    if getter is None:
        return {'status': 'CHAIN_API_UNAVAILABLE', 'certificates': []}
    chain = getter() or []
    if len(chain) > 16:
        raise ValueError('PEER_CHAIN_LIMIT')
    rows = []
    for item in chain:
        raw = item if isinstance(item, bytes) else item.public_bytes()
        if isinstance(raw, str):
            raw = ssl.PEM_cert_to_DER_cert(raw)
        elif raw.startswith(b'-----BEGIN'):
            raw = ssl.PEM_cert_to_DER_cert(raw.decode('ascii'))
        if len(raw) > 65536:
            raise ValueError('PEER_CERTIFICATE_LIMIT')
        metadata = cert_metadata(raw, datetime.datetime.now(datetime.timezone.utc))
        metadata['present_in_mounted_ca_bundle'] = sha(raw) in trusted_ders
        rows.append(metadata)
    return {'status': 'PRESENTED_CHAIN_CAPTURED' if rows else 'CHAIN_EMPTY',
            'certificates': rows, 'certificate_bodies_exported': False,
            'presented_chain_is_not_proof_of_trust': True}


def chain_handshake(host, route, trusted_ders):
    row = {'target_hostname': host, 'route': route, 'tls_attempted': False,
           'connection_attempted': False, 'certificate_verification': True,
           'hostname_verification': True, 'origin_http_requests': 0,
           'client_certificate_used': False, 'model_or_search_api_key_sent': False,
           'credentials_exported': False, 'sampled_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    started = time.monotonic()
    session = connection = secured = None
    try:
        session = make_tavily_session()
        context = getattr(session.adapters['https://'], '_tavily_ssl_context', None)
        if context is None or not context.check_hostname or context.verify_mode != ssl.CERT_REQUIRED:
            raise ValueError('VERIFIED_TAVILY_CONTEXT_REQUIRED')
        row['context_security_level'] = context.security_level
        row['connection_attempted'] = True
        if route == 'configured_authenticated_proxy':
            connection = connect_proxy_tunnel(host)
            row['proxy_connect_accepted'] = True
        elif route == 'direct_public_dns':
            connection = connect_direct_public(host)
        else:
            raise ValueError('ROUTE_NOT_ALLOWED')
        secured = context.wrap_socket(connection, server_hostname=host, do_handshake_on_connect=False)
        connection = None
        row['tls_attempted'] = True
        secured.do_handshake()
        row.update(status='TLS_VERIFIED', tls_version=secured.version())
    except Exception as exc:
        row.update(error_info(exc))
    finally:
        if secured is not None:
            try:
                row['peer_chain'] = observed_chain(secured, trusted_ders)
            except Exception as exc:
                row['peer_chain'] = {'status': 'CHAIN_UNAVAILABLE', 'error_class': type(exc).__name__}
            secured.close()
        if connection is not None:
            connection.close()
        if session is not None:
            session.close()
        row['elapsed_seconds'] = round(time.monotonic() - started, 3)
    return row


def main(mode):
    def deadline(*args):
        raise ChainDeadline('CHAIN_SAMPLE_DEADLINE')
    signal.signal(signal.SIGALRM, deadline)
    signal.alarm(55)
    result = {'status': 'INCOMPLETE', 'samples': [], 'maximum_connections': 4,
              'automatic_retry': False, 'bounded_diagnostic_sampling': True,
              'certificate_validation_disabled': False, 'origin_http_requests': 0,
              'production_configuration_modified': False, 'provider_requests': 0,
              'browser_launched': False, 'browser_equivalent_test': False,
              'credentials_exported': False, 'database_access': False}
    try:
        _, raw = bounded_public_file(CA_PATH)
        if sha(raw) != EXPECTED_CA_SHA:
            raise ValueError('BASELINE_CA_CHANGED')
        trusted = {sha(ssl.PEM_cert_to_DER_cert(block.decode('ascii'))) for block in PEM.findall(raw)}
        proxy = ('api.tavily.com', 'configured_authenticated_proxy')
        if mode == 'control':
            plans = [proxy, proxy, ('www.bing.com', 'direct_public_dns'), ('www.baidu.com', 'direct_public_dns')]
        elif mode == 'failed_node':
            plans = [proxy] * 4
        else:
            raise ValueError('MODE_NOT_ALLOWED')
        for host, route in plans:
            result['samples'].append(chain_handshake(host, route, trusted))
        result['status'] = 'TLS_CHAIN_SAMPLES_COLLECTED_NOT_REPAIRED'
    except (Exception, ChainDeadline) as exc:
        result['error'] = error_info(exc)
    finally:
        signal.alarm(0)
    return result

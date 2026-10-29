"""Pod-side probe, streamed by check-tavily-pod-tls-via-ssh.sh from a dev checkout.

The SSH server needs no repository: kubectl passes this source to Pod Python stdin.
No application API credentials are sent. TLS findings are printed in the report;
a completed probe is not itself a successful TLS verification.
"""
import ast
import hashlib
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import tempfile
from urllib.parse import unquote, urlsplit

import requests
from requests.adapters import HTTPAdapter


def main():
    print('pod:', os.getenv('HOSTNAME'), flush=True)
    source = Path('/app/backend/packages/harness/deerflow/community/tavily/tools.py')
    print('tavily_source_sha256:', hashlib.sha256(source.read_bytes()).hexdigest())
    for name in ('TAVILY_TLS_COMPAT', 'SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE',
                 'CURL_CA_BUNDLE', 'OPENSSL_CONF'):
        value = os.getenv(name, '')
        print(name, repr(value))
        if name != 'TAVILY_TLS_COMPAT' and value:
            path = Path(value)
            print('  exists:', path.is_file())
            if path.is_file():
                print('  sha256:', hashlib.sha256(path.read_bytes()).hexdigest())
    for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
                 'http_proxy', 'https_proxy', 'all_proxy'):
        value = os.getenv(name, '')
        if value:
            try:
                proxy = urlsplit(value)
                print(name, {'scheme': proxy.scheme, 'host': proxy.hostname,
                             'port': proxy.port, 'auth_present': bool(proxy.username or proxy.password)})
            except ValueError:
                print(name, 'SET_BUT_INVALID')
        else:
            print(name, 'EMPTY')

    # Load only the current source's TLS helper, never app config or Tavily keys.
    names = {'_TavilyTLSAdapter', '_truthy_env', '_build_tavily_session'}
    tree = ast.parse(source.read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    if {n.name for n in nodes} != names:
        raise RuntimeError('TLS helper shape changed; stop without probing')
    namespace = dict(os=os, ssl=ssl, requests=requests, HTTPAdapter=HTTPAdapter)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(source), 'exec'), namespace)
    session = namespace['_build_tavily_session']() or requests.Session()
    settings = session.merge_environment_settings('https://api.tavily.com/search', {}, None, None, None)
    proxy_value = requests.utils.select_proxy('https://api.tavily.com/search', settings['proxies'])
    print('effective_route:', 'proxy' if proxy_value else 'direct')
    verify = settings['verify']
    if verify is False:
        raise RuntimeError('Verification disabled unexpectedly; refusing probe')
    ca = verify if isinstance(verify, str) else requests.certs.where()
    print('requests_effective_ca:', ca)
    print('requests_effective_ca_sha256:', hashlib.sha256(Path(ca).read_bytes()).hexdigest() if Path(ca).is_file() else 'DIRECTORY')
    context = getattr(session.adapters['https://'], '_tavily_ssl_context', None)
    if context is None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_verify_locations(capath=ca if Path(ca).is_dir() else None,
                                  cafile=ca if Path(ca).is_file() else None)
    print('hostname_verification:', context.check_hostname, 'verify_mode:', context.verify_mode,
          'security_level:', context.security_level)
    executable = shutil.which('openssl')
    if not executable:
        print('openssl missing; trying one verified HEAD, no search/API key')
        session.trust_env = True
        try:
            response = session.head('https://api.tavily.com/search', timeout=(5, 10), allow_redirects=False)
            print('TLS_PASS; HTTP_status:', response.status_code)
            response.close()
        except requests.RequestException as exc:
            print('probe_error_class:', type(exc).__name__)
            # Raw exceptions may contain proxy credentials; never print them.
        print('Certificate chain unavailable without openssl; no verification bypass attempted.')
        return
    with tempfile.TemporaryDirectory(prefix='tavily-tls-readonly-') as directory:
        bundle = Path(directory) / 'effective-ca.pem'
        bundle.write_text(''.join(ssl.DER_cert_to_PEM_cert(c) for c in context.get_ca_certs(binary_form=True)))
        command = [executable, 's_client', '-connect', 'api.tavily.com:443',
                   '-servername', 'api.tavily.com', '-showcerts', '-verify_return_error',
                   '-verify_hostname', 'api.tavily.com', '-CAfile', str(bundle),
                   '-cipher', 'DEFAULT:@SECLEVEL=' + str(context.security_level)]
        environment = os.environ.copy()
        if proxy_value:
            proxy = urlsplit(proxy_value)
            if proxy.scheme != 'http' or not proxy.hostname:
                print('Unsupported proxy protocol for chain inspection; stop, no direct fallback.')
                return
            command += ['-proxy', '%s:%s' % (proxy.hostname, proxy.port or 80)]
            if proxy.username is not None:
                environment['TAVILY_DIAGNOSTIC_PROXY_PASS'] = unquote(proxy.password or '')
                command += ['-proxy_user', unquote(proxy.username),
                            '-proxy_pass', 'env:TAVILY_DIAGNOSTIC_PROXY_PASS']
        try:
            result = subprocess.run(command, input='', text=True, capture_output=True,
                                    timeout=20, env=environment)
        except subprocess.TimeoutExpired:
            print('TLS_PROBE_TIMEOUT_20_SECONDS')
            return
        output = result.stdout + '\n' + result.stderr
        print('openssl_exit:', result.returncode)
        for line in output.splitlines():
            if re.match(r'^\s*(Verify return code:|Verification error:|verify error:num=)', line):
                print(line.strip())
        certificates = re.findall(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', output, re.S)
        print('peer_certificates_received:', len(certificates))
        for index, certificate in enumerate(certificates, 1):
            details = subprocess.run([executable, 'x509', '-noout', '-subject', '-issuer',
                                      '-dates', '-fingerprint', '-sha256'], input=certificate,
                                     text=True, capture_output=True, timeout=5)
            print('certificate', index)
            print(details.stdout.strip())
        print('One handshake sample only; failure chain may be partial. No HTTP search was sent.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('diagnostic_stopped:', type(exc).__name__, '(raw exception withheld)')
        raise SystemExit(1)

"""Synthetic loopback-only Nginx qualification helper, never a production probe.

Run a mock upstream and Nginx in one network-none container namespace, then
--probe. These HTTP responses model authentication; they cannot prove native
SkillHub token validation/RBAC or current production session integration.
"""
import argparse
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time

RECEIPTS = []


class Upstream(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/__qa__/receipts':
            body = json.dumps(RECEIPTS).encode()
            RECEIPTS.clear()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        row = {'path': self.path, 'host': self.headers.get('Host'),
               'forwarded': self.headers.get('Forwarded'), 'forwarded_host': self.headers.get('X-Forwarded-Host'),
               'forwarded_port': self.headers.get('X-Forwarded-Port'),
               'internal_token': self.headers.get('X-DeerFlow-Internal-Token'),
               'owner_id': self.headers.get('X-DeerFlow-Owner-User-Id'),
               'identity': self.headers.get('X-SkillHub-Identity'), 'signature': self.headers.get('X-SkillHub-Signature')}
        RECEIPTS.append(row)
        cookie = self.headers.get('Cookie', '')
        if self.path in ('/api/v1/auth/me', '/internal/skillhub/session/assertion'):
            code = 200 if 'gateway-session=valid' in cookie else 401
        elif self.path.startswith('/threads/'):
            code = 200  # Synthetic legacy-history path, no actual stored data.
        else:
            code = 200 if 'SESSION=valid' in cookie or self.headers.get('Authorization') == 'Bearer native-read' else 401
        body = json.dumps(row).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Powered-By', 'synthetic-framework')
        self.send_header('X-AspNet-Version', 'synthetic-version')
        self.send_header('X-AspNetMvc-Version', 'synthetic-version')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    do_HEAD = do_GET

    def do_POST(self):
        self.send_response(400)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def log_message(self, *args):
        pass


def request(path, headers=None, method='GET', port=2026):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        connection.request(method, path, body='{' if method == 'POST' else None,
                           headers=dict({'Host': 'portal.example.test'}, **(headers or {})))
        response = connection.getresponse()
        return response.status, dict((k.lower(), v) for k, v in response.getheaders()), response.read(16385)
    finally:
        connection.close()


def probe():
    deadline = time.monotonic() + 15
    while True:
        try:
            request('/health')
            break
        except (ConnectionError, OSError):
            if time.monotonic() > deadline:
                raise
            time.sleep(.1)
    checks = []
    def check(name, path, status, headers=None, method='GET'):
        code, observed_headers, body = request(path, headers, method)
        assert code == status, (name, code, status)
        assert len(body) <= 16384, 'synthetic output bound'
        if code == 200 and path.startswith('/platform/skillhub/'):
            assert not any(k in observed_headers for k in ('x-powered-by', 'x-aspnet-version', 'x-aspnetmvc-version')), name
        checks.append(name)
        return observed_headers, body
    h, _ = check('anonymous_page_redirect', '/platform/skillhub/', 302)
    assert h.get('location') in ('/login', 'http://portal.example.test:2026/login', 'http://portal.example.test/login')
    for path in ('/platform/skillhub/api/web/labels', '/platform/skillhub/api/web/skills?page=0&size=1',
                 '/platform/skillhub/api/web/notifications/sse'):
        check('anonymous401:' + path, path, 401)
        check('fabricated_bearer401:' + path, path, 401, {'Authorization': 'Bearer fabricated'})
        check('synthetic_valid_bearer200:' + path, path, 200, {'Authorization': 'Bearer native-read'})
        check('synthetic_valid_sessions200:' + path, path, 200, {'Cookie': 'gateway-session=valid; SESSION=valid'})
    check('page_requires_gateway_even_with_native_session', '/platform/skillhub/', 302, {'Cookie': 'SESSION=valid'})
    check('bearer_does_not_bypass_page_session', '/platform/skillhub/', 302, {'Authorization': 'Bearer native-read'})
    check('valid_page_sessions_pass', '/platform/skillhub/', 200, {'Cookie': 'gateway-session=valid; SESSION=valid'})
    check('gateway_session_does_not_bypass_native_auth', '/platform/skillhub/api/web/labels', 401, {'Cookie': 'gateway-session=valid'})
    check('bootstrap_auth_preserved', '/platform/skillhub/api/v1/auth/session/bootstrap', 401)
    check('bearer_does_not_bypass_bootstrap_session', '/platform/skillhub/api/v1/auth/session/bootstrap', 401, {'Authorization': 'Bearer native-read'})
    check('anonymous_head_api401', '/platform/skillhub/api/web/labels', 401, method='HEAD')
    check('internal_auth_endpoint_not_public', '/_internal/skillhub/api-auth', 404)
    _, body = check('ppt_new_execution_retired', '/api/ppt-master-runtime/runs/stream', 409, method='POST')
    assert json.loads(body)['code'] == 'PPT_AGENT_RETIRED'
    check('legacy_ppt_get_preserved', '/api/ppt-master-runtime/threads/history', 200)
    request('/__qa__/receipts', port=8080)
    forged = {'Cookie': 'gateway-session=valid; SESSION=valid', 'Forwarded': 'host=attacker.invalid',
              'X-Forwarded-Host': 'attacker.invalid', 'X-Forwarded-Port': '9999',
              'X-DeerFlow-Internal-Token': 'untrusted', 'X-DeerFlow-Owner-User-Id': 'untrusted',
              'X-SkillHub-Identity': 'untrusted', 'X-SkillHub-Signature': 'untrusted'}
    check('forged_headers_filtered', '/platform/skillhub/api/web/labels', 200, forged)
    rows = json.loads(request('/__qa__/receipts', port=8080)[2])
    assert len(rows) == 2, rows
    for row in rows:
        assert row['forwarded'] is None and row['forwarded_port'] is None, row
        assert row['forwarded_host'] == 'portal.example.test', row
        if row['path'] == '/api/v1/auth/me':
            assert row['internal_token'] is None and row['owner_id'] is None, row
        else:
            assert row['identity'] is None and row['signature'] is None, row
    print(json.dumps({'status': 'SYNTHETIC_NGINX_HTTP_ACCEPTED', 'checks_passed': len(checks), 'checks': checks,
                      'scope': 'real_fixed_nginx_synthetic_fixture_and_auth_upstream',
                      'production_requests': 0, 'real_credentials_used': False, 'native_rbac_proven': False}, sort_keys=True), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--probe', action='store_true')
    args = parser.parse_args()
    if args.serve:
        ThreadingHTTPServer(('127.0.0.1', 8080), Upstream).serve_forever()
    elif args.probe:
        probe()
    else:
        parser.error('--serve or --probe required')


if __name__ == '__main__':
    main()

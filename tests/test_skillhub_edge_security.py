"""Exercise SkillHub edge auth and header defenses with real Nginx.

The upstream models only authentication responses, not native RBAC/token validation.

Set NGINX_TEST_BINARY (and NGINX_TEST_CERT/KEY for the TLS listener).
Only service addresses, listener ports, log/PID/certificate paths are adapted.
"""

import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = [
    "k8s/local-pvc/nginx.conf",
    "docker/nginx/nginx.conf",
    "docker/nginx/nginx.local.conf",
]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Upstream(BaseHTTPRequestHandler):
    def respond(self):
        receipt = {
            "path": self.path,
            "host": self.headers.get("Host"),
            "forwarded": self.headers.get("Forwarded"),
            "forwarded_host": self.headers.get("X-Forwarded-Host"),
            "forwarded_port": self.headers.get("X-Forwarded-Port"),
            "internal_token": self.headers.get("X-DeerFlow-Internal-Token"),
            "owner_id": self.headers.get("X-DeerFlow-Owner-User-Id"),
        }
        self.server.receipts.append(receipt)
        cookie = self.headers.get("Cookie", "")
        token = self.headers.get("Authorization", "")
        if self.path == "/api/v1/auth/me":
            status = 200 if "gateway-session=valid" in cookie else 401
        else:
            status = (
                200
                if token == "Bearer native-read" or "SESSION=valid" in cookie
                else 401
            )
        body = json.dumps(receipt).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Powered-By", "test-framework/1.2.3")
        self.send_header("X-AspNet-Version", "4.0.30319")
        self.send_header("X-AspNetMvc-Version", "5.2")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = respond

    def log_message(self, *args):
        pass


class NginxInstance:
    def __init__(self, relative, upstream):
        self.relative = relative
        self.directory = tempfile.TemporaryDirectory(prefix="deerflow-nginx-test-")
        root = Path(self.directory.name)
        (root / "logs").mkdir()
        (root / "temp").mkdir()
        self.port, self.tls_port = free_port(), free_port()
        config = (ROOT / relative).read_text(encoding="utf-8")
        config = config.replace("pid /tmp/nginx.pid;", "pid logs/nginx.pid;")
        config = config.replace("/dev/stdout", "logs/access.log").replace(
            "/dev/stderr", "logs/error.log"
        )
        config = re.sub(r"listen \[::\]:2026[^;]*;", "", config)
        config = config.replace("listen 2026", "listen 127.0.0.1:" + str(self.port))
        config = config.replace("listen 2443", "listen 127.0.0.1:" + str(self.tls_port))
        config = config.replace("127.0.0.1:2026", "127.0.0.1:" + str(self.port))
        for address in (
            "gateway:8001",
            "frontend:3000",
            "ppt-master-runner:8003",
            "provisioner:8002",
            "skillhub-web:80",
            "weknora-frontend:80",
            "weknora-app:8080",
            "127.0.0.1:8001",
            "127.0.0.1:3000",
            "127.0.0.1:8088",
        ):
            config = config.replace(address, "127.0.0.1:" + str(upstream))
        config = re.sub(
            r"http://10\.57\.128\.79:[0-9]+",
            "http://127.0.0.1:" + str(upstream),
            config,
        )
        config = config.replace(
            "/etc/nginx/tls/tls.crt",
            os.environ.get("NGINX_TEST_CERT", "missing.crt").replace("\\", "/"),
        )
        config = config.replace(
            "/etc/nginx/tls/tls.key",
            os.environ.get("NGINX_TEST_KEY", "missing.key").replace("\\", "/"),
        )
        (root / "test.conf").write_text(config, encoding="utf-8")
        self.argv = [
            os.environ["NGINX_TEST_BINARY"],
            "-p",
            root.as_posix() + "/",
            "-c",
            "test.conf",
        ]
        checked = subprocess.run(self.argv + ["-t"], capture_output=True, timeout=15)
        if checked.returncode:
            self.directory.cleanup()
            raise RuntimeError(checked.stderr.decode(errors="replace"))
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = subprocess.Popen(
            self.argv + ["-g", "daemon off;"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            self.close()
            raise RuntimeError("Nginx did not start: " + relative)

    def request(self, method, path="/api/threads/test", headers=None, tls=False):
        if tls:
            connection = http.client.HTTPSConnection(
                "127.0.0.1",
                self.tls_port,
                context=ssl._create_unverified_context(),
                timeout=5,
            )
        else:
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return (
                response.status,
                dict((k.lower(), v) for k, v in response.getheaders()),
                response.read(),
            )
        finally:
            connection.close()

    def close(self):
        try:
            subprocess.run(self.argv + ["-s", "quit"], capture_output=True, timeout=10)
            self.process.wait(timeout=10)
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=5)
            self.directory.cleanup()


@unittest.skipUnless(
    os.environ.get("NGINX_TEST_BINARY"), "Set NGINX_TEST_BINARY for live Nginx tests"
)
class SkillHubEdgeSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        cls.upstream.receipts = []
        cls.thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.thread.start()
        cls.instances = []
        try:
            for config in CONFIGS:
                cls.instances.append(NginxInstance(config, cls.upstream.server_port))
        except Exception:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        for instance in cls.instances:
            instance.close()
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.thread.join(timeout=3)

    def assert_safe_headers(self, receipt):
        self.assertEqual(receipt["host"], "cnoocclaw.cnooc.com.cn")
        self.assertEqual(receipt["forwarded_host"], "cnoocclaw.cnooc.com.cn")
        self.assertIsNone(receipt["forwarded"])
        self.assertIsNone(receipt["forwarded_port"])

    def test_authenticated_api_clears_injected_authority_and_version_headers(self):
        for instance in self.instances:
            with self.subTest(config=instance.relative):
                status, headers, body = instance.request(
                    "GET",
                    "/platform/skillhub/api/web/skills",
                    {
                        "Host": "cnoocclaw.cnooc.com.cn",
                        "Authorization": "Bearer native-read",
                        "Forwarded": "host=attacker.example;proto=http",
                        "X-Forwarded-Host": "attacker.example",
                        "X-Forwarded-Port": "9999",
                    },
                )
                self.assertEqual(status, 200)
                self.assert_safe_headers(json.loads(body))
                for name in ("x-powered-by", "x-aspnet-version", "x-aspnetmvc-version"):
                    self.assertNotIn(name, headers)
                self.assertEqual(headers["x-frame-options"], "SAMEORIGIN")
                self.assertEqual(
                    headers["content-security-policy"], "frame-ancestors 'self'"
                )

    def test_browser_and_api_auth_subrequests_clear_injected_headers(self):
        for instance in self.instances:
            for path in ("/platform/skillhub/", "/platform/skillhub/api/web/labels"):
                with self.subTest(config=instance.relative, path=path):
                    before = len(self.upstream.receipts)
                    status, _, _ = instance.request(
                        "GET",
                        path,
                        {
                            "Host": "cnoocclaw.cnooc.com.cn",
                            "Cookie": "gateway-session=valid; SESSION=valid",
                            "Forwarded": "host=attacker.example",
                            "X-Forwarded-Host": "attacker.example",
                            "X-Forwarded-Port": "9999",
                            "X-DeerFlow-Internal-Token": "fabricated",
                            "X-DeerFlow-Owner-User-Id": "fabricated",
                        },
                    )
                    self.assertEqual(status, 200)
                    subrequests = [
                        r
                        for r in self.upstream.receipts[before:]
                        if r["path"] == "/api/v1/auth/me"
                    ]
                    self.assertEqual(len(subrequests), 1)
                    self.assert_safe_headers(subrequests[0])
                    self.assertIsNone(subrequests[0]["internal_token"])
                    self.assertIsNone(subrequests[0]["owner_id"])

    def test_anonymous_api_and_sse_cannot_reach_native_business_routes(self):
        for instance in self.instances:
            for path in (
                "/platform/skillhub/api/web/skills",
                "/platform/skillhub/api/web/notifications/sse",
            ):
                with self.subTest(config=instance.relative, path=path):
                    before = len(self.upstream.receipts)
                    status, _, _ = instance.request(
                        "GET", path, {"Host": "cnoocclaw.cnooc.com.cn"}
                    )
                    self.assertEqual(status, 401)
                    self.assertTrue(
                        all(
                            r["path"] == "/api/v1/auth/me"
                            for r in self.upstream.receipts[before:]
                        )
                    )

    def test_native_session_alone_does_not_bypass_deerflow_auth(self):
        for instance in self.instances:
            with self.subTest(config=instance.relative):
                status, _, _ = instance.request(
                    "GET",
                    "/platform/skillhub/api/web/skills",
                    {"Host": "cnoocclaw.cnooc.com.cn", "Cookie": "SESSION=valid"},
                )
                self.assertEqual(status, 401)

    def test_bearer_shape_does_not_override_native_rejection(self):
        for instance in self.instances:
            with self.subTest(config=instance.relative):
                status, _, _ = instance.request(
                    "GET",
                    "/platform/skillhub/api/web/skills",
                    {
                        "Host": "cnoocclaw.cnooc.com.cn",
                        "Authorization": "Bearer fabricated",
                    },
                )
                self.assertEqual(status, 401)

    def test_anonymous_page_redirects_to_same_origin_login(self):
        for instance in self.instances:
            with self.subTest(config=instance.relative):
                status, headers, _ = instance.request(
                    "GET", "/platform/skillhub/", {"Host": "cnoocclaw.cnooc.com.cn"}
                )
                self.assertEqual(status, 302)
                self.assertIn(
                    headers["location"],
                    ("/login", f"http://cnoocclaw.cnooc.com.cn:{instance.port}/login"),
                )


if __name__ == "__main__":
    unittest.main()

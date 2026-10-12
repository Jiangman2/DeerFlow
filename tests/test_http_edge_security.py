"""Exercise real Nginx configurations against a bounded local upstream.

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
CONFIGS = ["k8s/local-pvc/nginx.conf", "docker/nginx/nginx.conf", "docker/nginx/nginx.local.conf"]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Upstream(BaseHTTPRequestHandler):
    def respond(self):
        self.server.receipts.append(self.command)
        status = 401 if self.command == "DELETE" and not self.headers.get("Authorization") else 200
        if self.path == "/api/v1/auth/me" and not self.headers.get("Authorization"):
            status = 401
        body = json.dumps({"method": self.command, "host": self.headers.get("Host"),
                           "forwarded": self.headers.get("Forwarded"),
                           "forwarded_host": self.headers.get("X-Forwarded-Host"),
                           "forwarded_port": self.headers.get("X-Forwarded-Port")}).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Powered-By", "test-framework/1.2.3")
        self.send_header("X-AspNet-Version", "4.0.30319")
        self.send_header("X-AspNetMvc-Version", "5.2")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = respond

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
        config = config.replace("/dev/stdout", "logs/access.log").replace("/dev/stderr", "logs/error.log")
        config = re.sub(r"listen \[::\]:2026[^;]*;", "", config)
        config = config.replace("listen 2026", "listen 127.0.0.1:" + str(self.port))
        config = config.replace("listen 2443", "listen 127.0.0.1:" + str(self.tls_port))
        config = config.replace("127.0.0.1:2026", "127.0.0.1:" + str(self.port))
        for address in ("gateway:8001", "frontend:3000", "ppt-master-runner:8003", "provisioner:8002",
                        "skillhub-web:80", "weknora-frontend:80", "weknora-app:8080",
                        "127.0.0.1:8001", "127.0.0.1:3000", "127.0.0.1:8088"):
            config = config.replace(address, "127.0.0.1:" + str(upstream))
        config = re.sub(r"http://10\.57\.128\.79:[0-9]+", "http://127.0.0.1:" + str(upstream), config)
        config = config.replace("/etc/nginx/tls/tls.crt", os.environ.get("NGINX_TEST_CERT", "missing.crt").replace("\\", "/"))
        config = config.replace("/etc/nginx/tls/tls.key", os.environ.get("NGINX_TEST_KEY", "missing.key").replace("\\", "/"))
        (root / "test.conf").write_text(config, encoding="utf-8")
        self.argv = [os.environ["NGINX_TEST_BINARY"], "-p", root.as_posix() + "/", "-c", "test.conf"]
        checked = subprocess.run(self.argv + ["-t"], capture_output=True, timeout=15)
        if checked.returncode:
            self.directory.cleanup()
            raise RuntimeError(checked.stderr.decode(errors="replace"))
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = subprocess.Popen(self.argv + ["-g", "daemon off;"], stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, creationflags=flags)
        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=.1):
                    break
            except OSError:
                time.sleep(.05)
        else:
            self.close()
            raise RuntimeError("Nginx did not start: " + relative)

    def request(self, method, path="/api/threads/test", headers=None, tls=False):
        if tls:
            connection = http.client.HTTPSConnection("127.0.0.1", self.tls_port,
                                                     context=ssl._create_unverified_context(), timeout=5)
        else:
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict((k.lower(), v) for k, v in response.getheaders()), response.read()
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


class ProbeConfigurationTests(unittest.TestCase):
    def test_kubernetes_health_probes_use_allowed_host(self):
        deployment = (ROOT / "k8s/local-pvc/deer-flow-pvc-stack.yaml").read_text(encoding="utf-8")
        nginx = deployment.split("  name: nginx\n", 1)[1].split("\n---", 1)[0]
        for probe in ("readinessProbe", "livenessProbe", "startupProbe"):
            block = nginx.split(probe + ":", 1)[1].split("Seconds:", 1)[0]
            self.assertIn("name: Host\n                  value: localhost", block)


@unittest.skipUnless(os.environ.get("NGINX_TEST_BINARY"), "Set NGINX_TEST_BINARY for live Nginx tests")
class EdgeSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        cls.upstream.receipts = []
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()
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

    def test_unsafe_methods_never_reach_upstream(self):
        for instance in self.instances:
            for method in ("TRACE", "CONNECT", "PROPFIND", "TRACK"):
                with self.subTest(config=instance.relative, method=method):
                    before = len(self.upstream.receipts)
                    status, _, body = instance.request(method)
                    self.assertIn(status, (400, 405) if method == "CONNECT" else (405,))
                    self.assertNotIn(b"test-framework", body)
                    self.assertEqual(before, len(self.upstream.receipts))

    def test_business_methods_and_delete_auth_are_preserved(self):
        for instance in self.instances:
            for method in ("GET", "HEAD", "POST", "PUT", "PATCH", "OPTIONS"):
                with self.subTest(config=instance.relative, method=method):
                    self.assertEqual(instance.request(method)[0], 200)
            self.assertEqual(instance.request("DELETE")[0], 401)
            self.assertEqual(instance.request("DELETE", headers={"Authorization": "Bearer test"})[0], 200)

    def test_tls_edge_also_rejects_unsafe_methods(self):
        instance = self.instances[0]
        for method in ("TRACE", "PROPFIND"):
            self.assertEqual(instance.request(method, tls=True)[0], 405)

    def test_foreign_hosts_never_reach_upstream_or_redirect(self):
        for instance in self.instances:
            for host in ("attacker.example", "cnoocclaw.cnooc.com.cn.attacker.example", "cnoocclaw.cnooc.com.cn:9999"):
                for path in ("/api/threads/test", "/platform/skillhub"):
                    with self.subTest(config=instance.relative, host=host, path=path):
                        before = len(self.upstream.receipts)
                        status, headers, _ = instance.request("GET", path, {"Host": host})
                        self.assertEqual(status, 400)
                        self.assertNotIn("location", headers)
                        self.assertEqual(before, len(self.upstream.receipts))

    def test_forwarded_host_injection_is_removed(self):
        for instance in self.instances:
            status, _, body = instance.request("GET", headers={
                "Host": "cnoocclaw.cnooc.com.cn",
                "Forwarded": 'host="attacker.example";proto=https',
                "X-Forwarded-Host": "attacker.example",
                "X-Forwarded-Port": "9999"})
            self.assertEqual(status, 200)
            receipt = json.loads(body)
            self.assertEqual(receipt["host"], "cnoocclaw.cnooc.com.cn")
            self.assertEqual(receipt["forwarded_host"], "cnoocclaw.cnooc.com.cn")
            self.assertIsNone(receipt["forwarded"])
            self.assertIsNone(receipt["forwarded_port"])

    def test_absolute_uri_cannot_override_valid_host(self):
        for instance in self.instances:
            self.assertEqual(instance.request("GET", "http://attacker.example/platform/skillhub",
                                              {"Host": "cnoocclaw.cnooc.com.cn"})[0], 400)

    def test_corporate_and_local_hosts_keep_relative_redirects(self):
        for instance in self.instances:
            for host in ("cnoocclaw.cnooc.com.cn", "CNOOCCLAW.CNOOC.COM.CN:443", "localhost:2026", "127.0.0.1:2026", "[::1]:2026"):
                status, headers, _ = instance.request("GET", "/platform/skillhub", {"Host": host})
                self.assertEqual(status, 308)
                self.assertEqual(headers["location"], "/platform/skillhub/")

    def test_tls_rejects_foreign_host_and_cleans_forwarded_headers(self):
        instance = self.instances[0]
        self.assertEqual(instance.request("GET", headers={"Host": "attacker.example"}, tls=True)[0], 400)
        status, _, body = instance.request("GET", headers={"Host": "cnoocclaw.cnooc.com.cn",
                                                          "Forwarded": "host=attacker.example"}, tls=True)
        self.assertEqual(status, 200)
        self.assertIsNone(json.loads(body)["forwarded"])

    def test_framework_headers_hidden_on_frontend_and_api_routes(self):
        for instance in self.instances:
            for path in ("/", "/api/models", "/api/runtime-config", "/api/threads/test", "/api/langgraph/threads"):
                with self.subTest(config=instance.relative, path=path):
                    status, headers, _ = instance.request("GET", path)
                    self.assertEqual(status, 200)
                    for header in ("x-powered-by", "x-aspnet-version", "x-aspnetmvc-version"):
                        self.assertNotIn(header, headers)
                    self.assertNotRegex(headers.get("server", ""), r"[0-9]+\.[0-9]+")

    def test_versions_hidden_on_native_and_upstream_error_responses(self):
        for instance in self.instances:
            for method, headers in (("GET", {"Host": "attacker.example"}), ("PROPFIND", {}), ("DELETE", {})):
                status, response_headers, body = instance.request(method, headers=headers)
                self.assertIn(status, (400, 401, 405))
                self.assertNotRegex(response_headers.get("server", ""), r"[0-9]+\.[0-9]+")
                self.assertNotRegex(body.decode(errors="replace"), r"nginx/[0-9]")
                self.assertNotIn("x-powered-by", response_headers)

    def test_tls_also_hides_framework_headers(self):
        status, headers, _ = self.instances[0].request("GET", tls=True)
        self.assertEqual(status, 200)
        self.assertNotIn("x-powered-by", headers)
        self.assertNotIn("x-aspnet-version", headers)

    def test_merged_external_agents_preserve_auth_and_filter_identification(self):
        instance = self.instances[0]
        for path in ("/zywdjxzj/", "/dzsjtpzj/", "/zjbgjszj/", "/zdsczj/",
                     "/qqyjbgbxzj/", "/zwjkylxcczs/", "/yjzs/"):
            with self.subTest(path=path):
                status, headers, _ = instance.request("GET", path, {"Host": "cnoocclaw.cnooc.com.cn"})
                self.assertEqual(status, 302)
                self.assertEqual(headers["location"], "https://cnoocclaw.cnooc.com.cn/")
                status, headers, body = instance.request("GET", path, {
                    "Host": "cnoocclaw.cnooc.com.cn", "Authorization": "Bearer test",
                    "Forwarded": "host=attacker.example", "X-Forwarded-Host": "attacker.example",
                    "X-Forwarded-Port": "9999"})
                self.assertEqual(status, 200)
                self.assertNotIn("x-powered-by", headers)
                self.assertNotIn("x-aspnet-version", headers)
                receipt = json.loads(body)
                self.assertIsNone(receipt["forwarded"])
                self.assertIsNone(receipt["forwarded_port"])
                self.assertNotEqual(receipt["forwarded_host"], "attacker.example")
                if path == "/zwjkylxcczs/":
                    self.assertEqual(receipt["host"], "10.57.128.79:5010")
                elif path == "/yjzs/":
                    self.assertEqual(receipt["host"], "10.57.128.79:10086")
                self.assertEqual(instance.request("GET", path, {"Host": "attacker.example"})[0], 400)


if __name__ == "__main__":
    unittest.main()

"""Offline unit and isolated local Nginx UI-only lifecycle acceptance."""
import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

SOURCE = Path(__file__).with_name("knowledge-entry-ui-20261014-r1.py")
spec = importlib.util.spec_from_file_location("entry_ui", SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

HTML = '''<!doctype html><html><head><meta charset="utf-8"><title>知识库入口临时禁用验证</title>
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body><h1>DeerFlow 导航验证</h1><nav data-sidebar="sidebar">
<p><a href="/workspace/chats">对话</a></p>
<p><a id="knowledge" data-sidebar="menu-button" href="/workspace/knowledge">知识库</a>
<a id="open" data-sidebar="menu-action" href="/platform/knowledge-bases" target="_blank" aria-label="打开知识库平台">↗</a></p>
<p><a href="/workspace/agents">数字员工</a></p></nav>
<main><button id="picker">对话中的知识库选择</button><span id="count">0</span>
<a id="outside" href="/workspace/knowledge">正文中的原地址（保持可访问）</a></main>
<script src="/app.js"></script></body></html>'''
APP = '''window.clicked=0;document.getElementById('picker').onclick=()=>{document.getElementById('count').textContent=String(++window.clicked);};
document.getElementById('knowledge').onclick=()=>{window.navAttempts=(window.navAttempts||0)+1;};'''
BASE = b'''events { worker_connections 64; }
pid /tmp/nginx.pid;
error_log /dev/stderr notice;
http {
    access_log off;
    server {
        listen 2026 default_server;
        server_name _;
        if ($host = invalid.example) { return 421; }
        location = /health { return 200 'ok'; }
        location ^~ /platform/skillhub/ { return 200 'skillhub'; }
        location ^~ /platform/ { return 200 'knowledge'; }
        location ^~ /api/ { return 200 'api'; }
        location / { proxy_pass http://frontend:3000; }
    }
    server {
        listen 3000;
        root /tmp/ui-fixture;
        location = /app.js { default_type application/javascript; }
        location = /rsc { default_type text/x-component; return 200 '</head>not-html'; }
        location / { default_type text/html; try_files /index.html =404; }
    }
}
'''


class PatchTests(unittest.TestCase):
    def test_python37(self):
        ast.parse(SOURCE.read_text(encoding="utf-8"), feature_version=(3, 7))

    def test_insert_only_no_knowledge_block(self):
        result = m.patch(BASE)
        for addition in m.ui_blocks():
            result = result.replace(addition.encode(), b"", 1)
        self.assertEqual(result, BASE)
        self.assertNotIn(b"return 503", m.patch(BASE))

    def test_crlf(self):
        base = BASE.replace(b"\n", b"\r\n")
        value = m.patch(base)
        for addition in m.ui_blocks():
            value = value.replace(addition.encode(), b"", 1)
        self.assertEqual(value, base)

    def test_repeat_rejected(self):
        with self.assertRaisesRegex(m.CheckFailure, "already_marked"):
            m.patch(m.patch(BASE))

    def test_ambiguous_anchor(self):
        for value in (BASE.replace(b"2026", b"2027"), BASE + BASE):
            with self.assertRaises(m.CheckFailure):
                m.patch(value)

    def test_existing_filters(self):
        for directive in (b"sub_filter_once off;", b'proxy_set_header Accept-Encoding "gzip";', b"sub_filter_types *;"):
            for anchor in (b"http {", b"server {", b"location / {"):
                with self.subTest(directive=directive, anchor=anchor), self.assertRaisesRegex(m.CheckFailure, "needs_review"):
                    m.patch(BASE.replace(anchor, anchor + b"\n" + directive, 1))

    def test_wrong_frontend_fails_closed(self):
        with self.assertRaisesRegex(m.CheckFailure, "upstream_changed"):
            m.patch(BASE.replace(b"http://frontend:3000", b"http://other:3000"))

    def test_template_upstream_supported(self):
        self.assertIn(m.INJECTION.encode(), m.patch(BASE.replace(b"http://frontend:3000", b"http://$frontend_upstream")))

    def test_quotes_braces_comments(self):
        base = BASE.replace(b"server_name _;", b'''# { ignored }
server_name _;
set $example "${host}/{quoted}";''')
        self.assertIn(m.INJECTION.encode(), m.patch(base))

    def test_malformed_fails(self):
        for value in (BASE + b"\x00", BASE[:-4], BASE + b"'missing", b"x" * 2097152):
            with self.assertRaises(m.CheckFailure):
                m.patch(value)

    def test_cas(self):
        cm = {"metadata": {"uid": "u", "resourceVersion": "v"}, "data": {"nginx.conf": "old"}}
        calls = []
        with patch.object(m, "command", side_effect=lambda args, data: calls.append(json.loads(data))), patch.object(m, "get", return_value={"data": {"nginx.conf": "new"}}):
            m.patch_cm(cm, b"new")
        self.assertEqual([v["op"] for v in calls[0]], ["test", "test", "test", "replace"])
        self.assertEqual(calls[0][-1]["path"], "/data/nginx.conf")

    def test_body_marker_not_header_marker(self):
        with patch.object(m, "execute", return_value=("HTTP/1.1 200 OK\r\n\r\nx-knowledge-entry-ui: " + m.MARKER).encode()):
            self.assertFalse(m.http_probe(m.PATHS[0])["ui_asset_marker"])


def integration(keep=False):
    container = "kb-entry-ui-test-" + uuid.uuid4().hex[:10]
    def docker(*args, data=None):
        return m.command(["docker"] + list(args), data, timeout=30)
    def execute(script, args=(), data=None):
        return docker("exec", "-i", container, "sh", "-eu", "-c", script, "test", *args, data=data)
    options = ("--publish", "127.0.0.1:3374:2026") if keep else ("--network", "none")
    docker("run", "--rm", "-d", "--name", container, *options, "--add-host", "frontend:127.0.0.1", "--memory", "128m", "--cpus", "0.5", "--entrypoint", "sh", "nginx:alpine", "-c", "sleep 1800")
    succeeded = False
    try:
        with patch.object(m, "execute", side_effect=execute):
            execute('mkdir /tmp/ui-fixture; cat > /tmp/ui-fixture/index.html', data=HTML.encode())
            execute('cat > /tmp/ui-fixture/app.js', data=APP.encode())
            execute('umask 077; cat > /tmp/nginx.conf', data=BASE)
            execute('nginx -t -c /tmp/nginx.conf; nginx -c /tmp/nginx.conf')
            pid_before = execute('cat /tmp/nginx.pid')
            def probe(path, host=m.HOST):
                return execute('curl -sS -i --http1.1 --noproxy "*" --max-time 10 --max-redirs 0 --header "Host: $2" "http://127.0.0.1:2026$1"', (path, host))
            assert m.http_probe("/health")["status"] == 200
            directory = m.stage_runtime(m.patch(BASE))
            m.apply_runtime(directory, BASE)
            enabled = m.verify(True)
            assert m.live_read() == m.patch(BASE)
            assert execute('cat /tmp/nginx.pid') == pid_before
            for path, body in ((m.PATHS[0], m.JS), (m.PATHS[1], m.CSS)):
                assert body.encode() == probe(path).split(b"\r\n\r\n", 1)[1], path
            for path in ("/api/runtime-config", "/api/weknora/knowledge-bases", "/api/v1/integrations/oa/policy-flow/jobs", "/platform/skillhub/", "/platform/knowledge-bases", "/workspace/knowledge", "/workspace/knowledge/foo"):
                assert b"200" in probe(path).split(b"\r\n", 1)[0], path
            assert m.INJECTION.encode() in probe("/workspace/chats")
            assert m.INJECTION.encode() not in probe("/rsc")
            assert b"421" in probe("/workspace/knowledge", "invalid.example").split(b"\r\n", 1)[0]
            try:
                m.stage_runtime(b"invalid nginx config;")
                raise AssertionError("syntax gate failed")
            except m.CheckFailure:
                pass
            assert m.live_read() == m.patch(BASE)
            stage = m.stage_runtime(BASE)
            try:
                m.apply_runtime(stage, BASE)
                raise AssertionError("hash gate failed")
            except m.CheckFailure:
                pass
            m.apply_runtime(stage, m.patch(BASE))
            restored = m.verify(False)
            assert m.live_read() == BASE
            assert m.INJECTION.encode() not in probe("/workspace/chats")
            assert execute('cat /tmp/nginx.pid') == pid_before
            if keep:
                stage = m.stage_runtime(m.patch(BASE))
                m.apply_runtime(stage, BASE)
                m.verify(True)
            succeeded = True
            print(json.dumps({"local_nginx_ui_lifecycle": "PASS", "enable": enabled, "restore": restored, "knowledge_pages_and_apis": "UNCHANGED", "host_guard": "PRESERVED", "html_only_injection": "PASS", "master_pid": "UNCHANGED", "syntax_and_drift_rejection": "PASS", "production_mutation": False, "browser_fixture_container": container if keep else None}))
    finally:
        if not keep or not succeeded:
            docker("stop", "-t", "1", container)


if __name__ == "__main__":
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PatchTests))
    if not result.wasSuccessful():
        sys.exit(1)
    if "--nginx" in sys.argv:
        integration("--keep" in sys.argv)

"""Real Chromium qualification, not enterprise login acceptance.

Run in a network-disabled Linux test container with the browser extra installed.
All HTTP traffic terminates at a new loopback fixture. Only test DNS answers and
Chromium host mapping are synthetic; the candidate URL policy, BrowserSession,
HTTP redirects, JavaScript, cookies, DOM and request counters are real.
"""

import asyncio
import base64
import hashlib
import ipaddress
import os
import threading
from collections import Counter
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from deerflow.community.browser_automation import session as session_mod
from deerflow.community.browser_automation.session import BrowserSession
from deerflow.community.browser_automation.url_policy import validate_browser_url_policy


@pytest.fixture
def website():
    hits = Counter()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            host = self.headers.get("Host", "").split(":")[0]
            path = urlsplit(self.path).path
            hits[(host, path)] += 1
            code, headers, body = 200, {"Content-Type": "text/html; charset=utf-8"}, ""
            port = self.server.server_address[1]
            denied = f"http://blocked.example.test:{port}"
            if path.startswith("/redirect/"):
                code = int(path.rsplit("/", 1)[1])
                headers["Location"] = denied + "/must-not-receive"
            elif path == "/login":
                body = """<!doctype html><title>Isolated browser login</title>
                <link rel='stylesheet' href='/style.css'><script src='/app.js' defer></script>
                <h1>Test-only sign in</h1><form method='post' action='/session'>
                <input name='username' aria-label='Username'>
                <button type='submit'>Sign in</button></form><div id='ready'></div>"""
            elif path == "/style.css":
                headers["Content-Type"] = "text/css"
                body = "body { color: rgb(12, 34, 56); }"
            elif path == "/app.js":
                headers["Content-Type"] = "application/javascript"
                body = "document.querySelector('#ready').textContent='Resources ready';"
            elif path == "/welcome":
                if self.headers.get("Cookie") == "fixture_session=yes":
                    body = "<title>Signed in</title><h1>Fixture login complete</h1><button onclick=\"this.textContent='Interaction complete'\">Test action</button>"
                else:
                    code, body = 401, "No fixture session"
            elif path == "/attack-resource":
                body = f"<title>Resource guard</title><img src='{denied}/must-not-receive'><iframe src='{denied}/must-not-receive'></iframe>"
            elif path == "/attack-js":
                body = f"<script>location.href='{denied}/must-not-receive'</script>"
            elif path == "/attack-popup":
                body = f"<button onclick=\"window.open('{denied}/must-not-receive')\">Open blocked fixture</button>"
            elif path == "/socket":
                if self.headers.get("Upgrade", "").lower() == "websocket":
                    # Bounded RFC6455 echo fixture; real Chromium supplies the
                    # handshake and masked short frame, no network dependencies.
                    self.connection.settimeout(4)
                    key = self.headers["Sec-WebSocket-Key"]
                    accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
                    self.protocol_version = "HTTP/1.1"
                    self.send_response(101)
                    self.send_header("Upgrade", "websocket")
                    self.send_header("Connection", "Upgrade")
                    self.send_header("Sec-WebSocket-Accept", accept)
                    self.end_headers()
                    head = self.rfile.read(2)
                    if len(head) == 2 and head[0] == 0x81 and head[1] & 0x80 and head[1] & 0x7F < 126:
                        mask = self.rfile.read(4)
                        payload = self.rfile.read(head[1] & 0x7F)
                        decoded = bytes(value ^ mask[i % 4] for i, value in enumerate(payload))
                        self.wfile.write(bytes([0x81, len(decoded)]) + decoded)
                        self.wfile.flush()
                    return
                code, body = 400, "Expected bounded WebSocket fixture handshake"
            else:
                body = "<title>Fixture</title><h1>Fixture ready</h1>"
            raw = body.encode()
            self.send_response(code)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_POST(self):
            host = self.headers.get("Host", "").split(":")[0]
            hits[(host, "/session")] += 1
            data = self.rfile.read(min(int(self.headers.get("Content-Length", "0")), 1024))
            assert parse_qs(data.decode()).get("username") == ["fixture-user"]
            self.send_response(303)
            self.send_header("Location", "/welcome")
            self.send_header("Set-Cookie", "fixture_session=yes; HttpOnly; SameSite=Lax; Path=/")
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1], hits
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@asynccontextmanager
async def guarded_browser(monkeypatch, port, *, suffix=False):
    pytest.importorskip("playwright.async_api")
    monkeypatch.setattr(
        session_mod,
        "_browser_ca_launch_args",
        lambda: [
            "--no-proxy-server",
            "--host-resolver-rules=MAP *.example.test 127.0.0.1",
        ],
    )
    policy = [{"origin": f"http://allowed.example.test:{port}", "cidrs": ["10.56.10.20/32"]}]
    observed = []

    def guard(url):
        outcome = validate_browser_url_policy(
            url,
            None if suffix else policy,
            private_origin_suffixes=[{"origin_suffix": policy[0]["origin"], "cidrs": policy[0]["cidrs"]}] if suffix else None,
            resolver=lambda _: [ipaddress.ip_address("10.56.10.20")],
        )
        observed.append((url, outcome))
        return outcome

    session = BrowserSession(None, headless=True, timeout_ms=4000, viewport={"width": 1000, "height": 700}, url_guard=guard)
    try:
        await session._ensure_page()
        yield session, observed
    finally:
        await session._close()


@pytest.mark.asyncio
async def test_real_suffix_subdomain_resources_and_redirect_boundary(website, monkeypatch):
    port, hits = website
    async with guarded_browser(monkeypatch, port, suffix=True) as (session, observed):
        from playwright.async_api import Error

        host = "portal.allowed.example.test"
        snapshot = await session._navigate(f"http://{host}:{port}/login")
        assert snapshot.title == "Isolated browser login"
        assert await session._page.locator("#ready").inner_text() == "Resources ready"
        assert hits[(host, "/style.css")] == 1
        assert hits[(host, "/app.js")] == 1
        try:
            await session._navigate(f"http://{host}:{port}/redirect/302")
        except Error:
            pass
        assert any("blocked.example.test" in url and outcome for url, outcome in observed)
        assert hits[("blocked.example.test", "/must-not-receive")] == 0


@pytest.mark.asyncio
async def test_real_resources_form_cookie_redirect_and_interaction(website, monkeypatch):
    port, hits = website
    async with guarded_browser(monkeypatch, port) as (session, _):
        errors = []
        session._page.on("pageerror", lambda error: errors.append(str(error)))
        snapshot = await session._navigate(f"http://allowed.example.test:{port}/login")
        assert snapshot.title == "Isolated browser login"
        assert await session._page.locator("#ready").inner_text() == "Resources ready"
        assert await session._page.evaluate("getComputedStyle(document.body).color") == "rgb(12, 34, 56)"
        field = next(item for item in snapshot.elements if item.name == "Username")
        await session._type(field.ref, "fixture-user", submit=False)
        async with session._page.expect_navigation(wait_until="domcontentloaded"):
            await session._page.get_by_role("button", name="Sign in").click()
        assert session._page.url.endswith("/welcome")
        assert await session._page.title() == "Signed in"
        await session._page.get_by_role("button", name="Test action").click()
        assert await session._page.get_by_role("button").inner_text() == "Interaction complete"
        assert errors == []
        assert hits[("allowed.example.test", "/session")] == 1
        assert hits[("allowed.example.test", "/welcome")] == 1
        evidence = os.environ.get("BROWSER_FIXTURE_EVIDENCE")
        if evidence:
            await session._page.screenshot(path=str(Path(evidence) / "synthetic-login-interaction.png"))


@pytest.mark.asyncio
async def test_real_direct_navigation_never_reaches_unapproved_origin(website, monkeypatch):
    port, hits = website
    async with guarded_browser(monkeypatch, port) as (session, observed):
        from playwright.async_api import Error

        with pytest.raises(Error):
            await session._navigate(f"http://blocked.example.test:{port}/must-not-receive")
        assert any("blocked.example.test" in url and outcome for url, outcome in observed)
        assert hits[("blocked.example.test", "/must-not-receive")] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
async def test_real_http_redirect_never_reaches_unapproved_origin(website, monkeypatch, code):
    port, hits = website
    async with guarded_browser(monkeypatch, port) as (session, _):
        from playwright.async_api import Error

        try:
            await session._navigate(f"http://allowed.example.test:{port}/redirect/{code}")
        except Error:
            pass
        assert hits[("blocked.example.test", "/must-not-receive")] == 0, "HTTP redirect reached denied fixture before policy rejection"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/attack-resource", "/attack-js", "/attack-popup"])
async def test_real_resource_js_popup_never_reaches_unapproved_origin(website, monkeypatch, path):
    port, hits = website
    async with guarded_browser(monkeypatch, port) as (session, observed):
        await session._navigate(f"http://allowed.example.test:{port}{path}")
        if path == "/attack-popup":
            await session._page.get_by_role("button", name="Open blocked fixture").click()
        for _ in range(50):
            if any("blocked.example.test" in url and outcome for url, outcome in observed):
                break
            await asyncio.sleep(0.02)
        assert any("blocked.example.test" in url and outcome for url, outcome in observed)
        assert hits[("blocked.example.test", "/must-not-receive")] == 0


@pytest.mark.asyncio
async def test_real_websocket_allowed_origin_echoes(website, monkeypatch):
    port, hits = website
    async with guarded_browser(monkeypatch, port) as (session, observed):
        await session._navigate(f"http://allowed.example.test:{port}/")
        outcome = await session._page.evaluate(
            """url => new Promise(resolve => {
            const ws = new WebSocket(url);
            const timer = setTimeout(() => { ws.close(); resolve('timeout'); }, 3000);
            ws.onerror = () => { clearTimeout(timer); resolve('error'); };
            ws.onopen = () => ws.send('merge-echo');
            ws.onmessage = event => { clearTimeout(timer); ws.close(); resolve(event.data); };
        })""",
            f"ws://allowed.example.test:{port}/socket",
        )
        assert outcome == "merge-echo"
        assert hits[("allowed.example.test", "/socket")] == 1
        assert any(url.endswith("/socket") and result is None for url, result in observed)


@pytest.mark.asyncio
async def test_real_websocket_never_reaches_unapproved_origin(website, monkeypatch):
    port, hits = website
    async with guarded_browser(monkeypatch, port) as (session, _):
        await session._navigate(f"http://allowed.example.test:{port}/")
        await session._page.evaluate(
            """url => new Promise(resolve => {
            const ws = new WebSocket(url);
            ws.onerror = () => resolve('error');
            ws.onopen = () => { ws.close(); resolve('opened'); };
            setTimeout(() => { ws.close(); resolve('timeout'); }, 1500);
        })""",
            f"ws://blocked.example.test:{port}/socket",
        )
        assert hits[("blocked.example.test", "/socket")] == 0, "Denied WebSocket handshake reached fixture"

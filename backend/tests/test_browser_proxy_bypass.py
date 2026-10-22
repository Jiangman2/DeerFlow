"""Routing checks do not grant private-address authorization or login reuse."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import proxy_bypass_environment

import pytest
import yaml

from deerflow.community.browser_automation.proxy_bypass import browser_proxy_bypass_args


def test_unset_has_no_routing_effect(monkeypatch):
    monkeypatch.delenv("DEERFLOW_BROWSER_DIRECT_SUFFIXES", raising=False)
    assert browser_proxy_bypass_args() == []


def test_explicit_suffixes_have_dot_boundary_and_no_proxy_override(monkeypatch):
    monkeypatch.setenv("DEERFLOW_BROWSER_DIRECT_SUFFIXES", "cnooc.com.cn,cnooc,CNOOC.COM.CN")
    assert browser_proxy_bypass_args() == ["--proxy-bypass-list=cnooc.com.cn;*.cnooc.com.cn;cnooc;*.cnooc"]


@pytest.mark.parametrize(
    "value",
    [
        "*",
        ".cnooc.com.cn",
        "cnooc.com.cn;*",
        "cnooc.com.cn,--no-proxy-server",
        "https://cnooc.com.cn",
        "cnooc.com.cn:443",
        "com",
        "127.0.0.1",
        "10.0.0.0/8",
        "secret@host.test",
        "cnooc..com.cn",
        "cnooc.com.cn,",
        "cnooc," * 32 + "cnooc",
        "a" * 4097,
    ],
)
def test_invalid_operator_values_are_refused_without_echo(monkeypatch, value):
    monkeypatch.setenv("DEERFLOW_BROWSER_DIRECT_SUFFIXES", value)
    with pytest.raises(ValueError, match="^Invalid browser direct suffix configuration$"):
        browser_proxy_bypass_args()


def test_enterprise_manifest_updates_both_command_environment_sources():
    root = Path(__file__).resolve().parents[2]
    documents = list(yaml.safe_load_all((root / "k8s/enterprise/runtime-patches.yaml").read_text()))
    for role in ("gateway", "provisioner"):
        document = next(d for d in documents if d.get("kind") == "Deployment" and d["metadata"]["name"] == role)
        container = next(c for c in document["spec"]["template"]["spec"]["containers"] if c["name"] == role)
        env = {item["name"]: item.get("value") for item in container["env"]}
        assert env["NO_PROXY"] == env["no_proxy"]
        for host in ("proofread.cnooc.com.cn", "au.cnooc.com.cn", "hnwork.w.cnooc.com.cn", "datalake.cnooc"):
            assert proxy_bypass_environment(host, {"no": env["NO_PROXY"]})
        for host in ("evilcnooc.com.cn", "cnooc.com.cn.evil.test", "dashscope.aliyuncs.com", "tavily.com"):
            assert not proxy_bypass_environment(host, {"no": env["NO_PROXY"]})
        assert "gateway" in env["NO_PROXY"].split(",")
        assert ".svc" in env["NO_PROXY"].split(",")
        if role == "gateway":
            assert env["DEERFLOW_BROWSER_DIRECT_SUFFIXES"] == "cnooc.com.cn,cnooc"
        else:
            assert {"NO_PROXY", "no_proxy"}.issubset(env["SANDBOX_PASSTHROUGH_ENV_KEYS"].split(","))


@pytest.mark.asyncio
async def test_real_chromium_bypasses_only_company_domains(monkeypatch):
    pytest.importorskip("playwright.async_api")
    from deerflow.community.browser_automation import session as module

    direct_hits, proxy_hits = [], []

    class Website(BaseHTTPRequestHandler):
        def log_message(self, *unused):
            pass

        def do_GET(self):
            direct_hits.append(self.headers.get("Host"))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<title>Direct fixture</title><p>ready</p>")

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *unused):
            pass

        def do_GET(self):
            proxy_hits.append(self.path)
            self.send_response(502)
            self.end_headers()

    website = ThreadingHTTPServer(("127.0.0.1", 0), Website)
    proxy = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    workers = []
    for server in (website, proxy):
        server.daemon_threads = True
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        workers.append(worker)
    port = website.server_address[1]
    monkeypatch.setenv("DEERFLOW_BROWSER_DIRECT_SUFFIXES", "cnooc.com.cn,cnooc")
    monkeypatch.setattr(
        module,
        "_browser_ca_launch_args",
        lambda: [
            "--proxy-server=http://127.0.0.1:" + str(proxy.server_address[1]),
            "--host-resolver-rules=MAP *.cnooc.com.cn 127.0.0.1, MAP *.cnooc 127.0.0.1",
        ],
    )
    browser = module.BrowserSession(None, headless=True, timeout_ms=5000, viewport={"width": 800, "height": 600})
    try:
        await browser._ensure_page()
        for host in ("proofread.cnooc.com.cn", "au.cnooc.com.cn", "datalake.cnooc"):
            response = await browser._page.goto(f"http://{host}:{port}/", wait_until="domcontentloaded")
            assert response.status == 200
            assert await browser._page.title() == "Direct fixture"
        assert not any("proofread.cnooc.com.cn" in x or "au.cnooc.com.cn" in x or "datalake.cnooc" in x for x in proxy_hits)
        for host in ("public.example.test", "evilcnooc.com.cn", "cnooc.com.cn.evil.test"):
            response = await browser._page.goto(f"http://{host}:{port}/", wait_until="domcontentloaded")
            assert response.status == 502
            assert any(host in x for x in proxy_hits)
            assert not any(host + ":" in x for x in direct_hits)
    finally:
        await browser._close()
        for server, worker in zip((website, proxy), workers):
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

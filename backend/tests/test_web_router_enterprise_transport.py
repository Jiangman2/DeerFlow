"""Actual router with mocked HTTP: proxy/CA policy and failed-primary fallback."""
import asyncio
import json
import os
import ssl
from unittest.mock import patch
from types import SimpleNamespace

import httpx
import pytest

from deerflow.community.web_router import providers, tools
from deerflow.config.app_config import AppConfig, push_current_app_config, pop_current_app_config


@pytest.fixture(autouse=True)
def clean_network_env(monkeypatch):
    for name in list(os.environ):
        if name.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR", "CURL_CA_BUNDLE", "DEER_FLOW_WEB_ROUTER_TRUST_ENV"}:
            monkeypatch.delenv(name)


def test_proxy_requires_operator_opt_in(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    assert providers._transport_options() == {"verify": True, "trust_env": False, "follow_redirects": False}
    monkeypatch.setenv("DEER_FLOW_WEB_ROUTER_TRUST_ENV", "true")
    assert providers._transport_options()["trust_env"] is True


@pytest.mark.parametrize("flag", ["1", "yes", "", "arbitrary"])
def test_invalid_proxy_flag_rejected(monkeypatch, flag):
    monkeypatch.setenv("DEER_FLOW_WEB_ROUTER_TRUST_ENV", flag)
    with pytest.raises(providers.ProviderFailure) as failure:
        providers._transport_options()
    assert failure.value.code == "CONFIG_ERROR" and failure.value.stop


def test_bad_ca_never_silently_falls_back(monkeypatch):
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/missing-enterprise.pem")
    monkeypatch.setenv("SSL_CERT_FILE", "/other.pem")
    with patch.object(providers.ssl, "create_default_context", side_effect=OSError("private-canary")) as factory:
        with pytest.raises(providers.ProviderFailure) as failure:
            providers._transport_options()
    factory.assert_called_once_with(cafile="/missing-enterprise.pem")
    assert str(failure.value) == "CONFIG_ERROR" and failure.value.stop


@pytest.mark.parametrize("operation,primary_failure", [("search", "timeout"), ("fetch", "timeout"), ("fetch", "404")])
def test_verified_proxy_and_primary_failure_fallback(monkeypatch, operation, primary_failure):
    context = ssl.create_default_context()
    original_client = httpx.AsyncClient
    seen = []
    monkeypatch.setenv("DEER_FLOW_WEB_ROUTER_TRUST_ENV", "true")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/enterprise.pem")
    monkeypatch.setenv("SSL_CERT_FILE", "/lower-priority.pem")

    def handler(request):
        seen.append(request)
        if request.url.host == "api.deepseek.com":
            if primary_failure == "timeout":
                raise httpx.ReadTimeout("private-canary", request=request)
            return httpx.Response(404, text="private-canary")
        if operation == "search":
            return httpx.Response(200, json={"results": [{"title": "Python", "url": "https://example.com", "content": "public sample"}]})
        return httpx.Response(200, json={"results": [{"title": "Sample", "raw_content": "public sample " * 2000}]})

    def client(**kwargs):
        assert kwargs["trust_env"] is True and kwargs["follow_redirects"] is False
        assert kwargs["verify"] is context
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        return original_client(**kwargs, transport=httpx.MockTransport(handler))

    config = AppConfig.model_validate({
        "sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider", "allow_host_bash": False},
        "models": [], "tool_groups": [{"name": "web"}],
        "tools": [{"name": name, "group": "web", "use": f"deerflow.community.web_router.tools:{name}_tool",
                   "provider_order": ["deepseek", "tavily"], "pdf_provider_order": ["deepseek", "tavily"],
                   "max_results": 5, "max_attempts": 3, "timeout": 15, "total_timeout": 40, "max_characters": 20000,
                   "providers": {p: {"api_key": "synthetic-" + p} for p in ("deepseek", "tavily")}}
                  for name in ("web_search", "web_fetch")]})
    push_current_app_config(config)
    try:
        with patch.object(providers.ssl, "create_default_context", return_value=context) as factory, patch.object(providers.httpx, "AsyncClient", side_effect=client):
            content, artifact = asyncio.run(tools._aroute(operation, "python" if operation == "search" else "https://example.com/report.pdf"))
        assert all(call.kwargs == {"cafile": "/enterprise.pem"} for call in factory.call_args_list)
    finally:
        pop_current_app_config()
    assert [r.url.host for r in seen] == ["api.deepseek.com", "api.tavily.com"]
    routing = artifact["web_routing"]
    assert routing["status"] == "success" and routing["provider"] == "tavily"
    assert routing["attempts"][0]["error_code"] == ("TIMEOUT" if primary_failure == "timeout" else "CONTENT_UNAVAILABLE")
    assert "private-canary" not in json.dumps(artifact)
    assert "synthetic-" not in content
    if operation == "fetch":
        assert len(content) < 20500 and routing["route"] == "pdf"


def test_redirect_does_not_send_key_to_second_host():
    seen = []
    original = httpx.AsyncClient
    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"Location": "https://other.invalid"})
    with patch.object(providers.httpx, "AsyncClient", side_effect=lambda **kw: original(**kw, transport=httpx.MockTransport(handler))):
        with pytest.raises(providers.ProviderFailure) as failure:
            asyncio.run(providers._request("tavily", "search", "synthetic-key", {}, 15))
    assert len(seen) == 1 and failure.value.stop and failure.value.status == 302


def test_equal_total_budget_roundoff_never_dispatches_backup(monkeypatch):
    # (base + 0.02) - base is slightly above 0.02 at this clock magnitude.
    base = 88888.0
    assert (base + 0.02) - base > 0.02
    monkeypatch.setattr(tools, 'time', SimpleNamespace(monotonic=lambda: base))
    monkeypatch.setattr(tools, '_settings', lambda *args: tools.Settings(
        order=['deepseek', 'tavily'], keys={'deepseek': 'synthetic', 'tavily': 'synthetic'}, timeout=0.02, total_timeout=0.02))
    seen = []
    async def timed_out(provider, *args):
        seen.append(provider)
        raise TimeoutError()
    monkeypatch.setattr(providers, 'search', timed_out)
    content, _ = asyncio.run(tools._aroute('search', 'q'))
    assert seen == ['deepseek'] and 'BUDGET_EXHAUSTED' in content

"""Tests for the Feishu channel credentials_provider (skill secret injection).

All channel-service access is mocked — no real Feishu app, network, or config
file is involved. Verifies that app credentials land in
``run_context["secrets"]`` under the exact names the feishu-document-summary
skill declares in ``required-secrets``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import app.channels.feishu_run_policy as feishu_run_policy
from app.channels.run_policy import CHANNEL_RUN_POLICY
from app.channels.service import get_channel_service  # noqa: F401  (import target for monkeypatch)


def _msg(channel_name: str = "feishu"):
    return SimpleNamespace(channel_name=channel_name)


def _run(coro):
    return asyncio.run(coro)


class _FakeService:
    def __init__(self, config=None, channel_config=None):
        self._config = config
        self._channel = SimpleNamespace(config=channel_config) if channel_config is not None else None

    def get_channel_config(self, name):
        return self._config

    def get_channel(self, name):
        return self._channel


def test_policy_registers_credentials_provider():
    policy = CHANNEL_RUN_POLICY.get("feishu")
    assert policy is not None
    assert policy.serialize_thread_runs is True
    assert policy.credentials_provider is feishu_run_policy.inject_feishu_credentials


def test_ignores_non_feishu_channel(monkeypatch):
    called = []

    def _boom():
        called.append(True)
        raise AssertionError("should not be called for non-feishu channels")

    monkeypatch.setattr("app.channels.service.get_channel_service", _boom)
    run_context = {}
    _run(feishu_run_policy.inject_feishu_credentials(_msg("telegram"), run_context))
    assert run_context == {}
    assert called == []


def test_no_channel_service_degrades_silently(monkeypatch):
    monkeypatch.setattr("app.channels.service.get_channel_service", lambda: None)
    run_context = {}
    _run(feishu_run_policy.inject_feishu_credentials(_msg(), run_context))
    assert run_context == {}


def test_injects_three_secret_keys_from_live_config(monkeypatch):
    service = _FakeService(
        config={
            "app_id": "cli_aa3b9ac9dfb8dd25",
            "app_secret": "s3cret",
            "domain": "https://open.feishu.cn",
        }
    )
    monkeypatch.setattr("app.channels.service.get_channel_service", lambda: service)
    run_context = {}
    _run(feishu_run_policy.inject_feishu_credentials(_msg(), run_context))
    assert run_context["secrets"] == {
        "FEISHU_APP_ID": "cli_aa3b9ac9dfb8dd25",
        "FEISHU_APP_SECRET": "s3cret",
        "FEISHU_DOMAIN": "https://open.feishu.cn",
    }


def test_domain_defaults_when_absent(monkeypatch):
    service = _FakeService(config={"app_id": "cli_x", "app_secret": "s"})
    monkeypatch.setattr("app.channels.service.get_channel_service", lambda: service)
    run_context = {}
    _run(feishu_run_policy.inject_feishu_credentials(_msg(), run_context))
    assert run_context["secrets"]["FEISHU_DOMAIN"] == "https://open.feishu.cn"


def test_falls_back_to_channel_instance_config(monkeypatch):
    service = _FakeService(config=None, channel_config={"app_id": "cli_fb", "app_secret": "s", "domain": "https://open.w.cnooc.com.cn"})
    monkeypatch.setattr("app.channels.service.get_channel_service", lambda: service)
    run_context = {}
    _run(feishu_run_policy.inject_feishu_credentials(_msg(), run_context))
    assert run_context["secrets"]["FEISHU_APP_ID"] == "cli_fb"
    assert run_context["secrets"]["FEISHU_DOMAIN"] == "https://open.w.cnooc.com.cn"


def test_missing_app_credentials_injects_nothing(monkeypatch):
    service = _FakeService(config={"app_id": "", "app_secret": ""})
    monkeypatch.setattr("app.channels.service.get_channel_service", lambda: service)
    run_context = {}
    _run(feishu_run_policy.inject_feishu_credentials(_msg(), run_context))
    assert "secrets" not in run_context


def test_existing_caller_secrets_are_not_overwritten(monkeypatch):
    service = _FakeService(config={"app_id": "cli_new", "app_secret": "new", "domain": "https://open.feishu.cn"})
    monkeypatch.setattr("app.channels.service.get_channel_service", lambda: service)
    run_context = {"secrets": {"FEISHU_APP_ID": "cli_keep", "OTHER": "x"}}
    _run(feishu_run_policy.inject_feishu_credentials(_msg(), run_context))
    assert run_context["secrets"]["FEISHU_APP_ID"] == "cli_keep"
    assert run_context["secrets"]["FEISHU_APP_SECRET"] == "new"
    assert run_context["secrets"]["OTHER"] == "x"

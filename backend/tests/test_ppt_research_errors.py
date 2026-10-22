"""Typed research failures contain no provider body or user input."""

import asyncio
import importlib.util
import json
import ssl
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "app/gateway/ppt_runtime_broker.py"


@pytest.fixture
def broker():
    spec = importlib.util.spec_from_file_location("research_errors_broker_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_nested_certificate_failure_retains_category_without_raw_message(broker):
    inner = ssl.SSLCertVerificationError("private-url-and-secret")
    outer = RuntimeError("opaque")
    outer.__cause__ = inner
    assert broker.research_error_category(outer) == "tls_verification"


def test_sdk_limit_without_status_is_not_falsely_called_quota(broker):
    error = type("UsageLimitExceededError", (Exception,), {"__module__": "tavily.errors"})
    assert broker.research_error_category(error("429 or credits")) == "provider_limit_unknown"


def test_unknown_message_keywords_do_not_establish_quota(broker):
    assert broker.research_error_category(RuntimeError("quota exhausted")) == "unknown_research_failure"
    assert broker.research_error_category(TimeoutError("private")) == "network_timeout"


@pytest.mark.parametrize("failure,category", [(TimeoutError("secret"), "network_timeout"), ("Error: raw provider page and key", "extract_failed")])
def test_broker_persists_safe_outcome_and_returns_typed_error(broker, monkeypatch, failure, category):
    saved = []

    class Store:
        async def list_events(self, *args, **kwargs):
            return []

        async def put(self, **kwargs):
            saved.append(kwargs)

    async def scope(request):
        return {"run": "run", "thread": "thread", "owner": "owner"}

    async def body(request):
        return {"name": "web_fetch", "arguments": {"url": "https://private-topic.example"}}

    def invoke(args):
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setitem(sys.modules, "app.gateway.deps", SimpleNamespace(get_run_event_store=lambda request: Store()))
    monkeypatch.setattr(broker, "require_scope", scope)
    monkeypatch.setattr(broker, "bounded_body", body)
    monkeypatch.setattr(broker, "_receipt_owner_context", lambda value: nullcontext())
    monkeypatch.setattr(broker, "public_url", lambda value: value)
    monkeypatch.setattr(broker, "configured_tool", lambda name: SimpleNamespace(invoke=invoke))
    with pytest.raises(broker.HTTPException) as info:
        asyncio.run(broker.research(None))
    assert info.value.detail == {"research_error": category}
    assert saved[-1]["event_type"] == "ppt.research_result"
    assert saved[-1]["content"]["error_category"] == category
    assert "secret" not in json.dumps(saved)
    assert "private-topic" not in json.dumps(saved)
    assert broker._slots._value == 4

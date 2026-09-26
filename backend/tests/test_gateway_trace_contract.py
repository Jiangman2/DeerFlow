"""Enterprise adaptation of v2.1 #5119: HTTP and admission trace authority."""

import asyncio
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException
from starlette.testclient import TestClient

from app.gateway.trace_middleware import TraceMiddleware
from deerflow.trace_context import DEERFLOW_TRACE_METADATA_KEY as TRACE_KEY
from deerflow.trace_context import TRACE_ID_HEADER, get_current_trace_id, request_trace_context


@pytest.mark.parametrize("status", [200, 401, 403, 404, 422, 500])
def test_trace_always_present_including_errors(status):
    app = FastAPI()
    # Compatibility with deployed callers must not retain a disabling switch.
    app.add_middleware(TraceMiddleware, enabled=False)

    @app.get("/test")
    async def route():
        if status == 500:
            raise RuntimeError("test error")
        if status != 200:
            raise HTTPException(status, "test rejection")
        return {"trace_id": get_current_trace_id()}

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test", headers={TRACE_ID_HEADER: "http-authority"})
    assert response.status_code == status
    assert response.headers[TRACE_ID_HEADER] == "http-authority"
    if status == 500:
        assert response.content == b"Internal Server Error"
        assert response.headers["content-length"] == "21"
    assert get_current_trace_id() is None


def test_real_gateway_traces_health_and_cors_without_enhanced_logging(monkeypatch):
    import app.gateway.app as gateway

    monkeypatch.setattr(gateway, "get_configured_cors_origins", lambda: {"https://client.example"})
    app = gateway.create_app()
    # No lifespan: this is real middleware wiring, not production startup/DB.
    client = TestClient(app)
    response = client.get("/health", headers={"Origin": "https://client.example", TRACE_ID_HEADER: "real-stack"})
    assert response.status_code == 200
    assert response.headers[TRACE_ID_HEADER] == "real-stack"
    exposed = response.headers["access-control-expose-headers"].lower()
    assert TRACE_ID_HEADER.lower() in exposed


@pytest.mark.asyncio
async def test_stream_failure_has_only_one_response_start_and_restores_context():
    sent = []

    async def broken_stream(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        raise RuntimeError("stream interrupted")

    async def send(message):
        sent.append(message)

    with request_trace_context("outer"):
        with pytest.raises(RuntimeError, match="stream interrupted"):
            await TraceMiddleware(broken_stream, enabled=False)({"type": "http", "headers": []}, None, send)
        assert get_current_trace_id() == "outer"
    assert len([message for message in sent if message["type"] == "http.response.start"]) == 1
    assert dict(sent[0]["headers"])[b"x-trace-id"]
    assert sent[-1]["body"] == b"first"


@pytest.fixture
def trace_config():
    from deerflow.config.app_config import AppConfig, reset_app_config, set_app_config

    set_app_config(AppConfig.model_validate({"sandbox": {"use": "deerflow.sandbox.local:LocalSandboxProvider"}}))
    yield
    reset_app_config()


@pytest.mark.asyncio
@pytest.mark.parametrize("caller_metadata", [None, {TRACE_KEY: "forged", "caller_key": "keep"}])
@pytest.mark.parametrize("inbound", [None, "server-issued"])
async def test_admission_persists_server_trace_and_scrubs_request_echo(trace_config, caller_metadata, inbound):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.store.memory import InMemoryStore

    from app.gateway.routers.thread_runs import RunCreateRequest
    from app.gateway.services import start_run
    from deerflow.persistence.thread_meta.memory import MemoryThreadMetaStore
    from deerflow.runtime.events.store.memory import MemoryRunEventStore
    from deerflow.runtime.runs.manager import RunManager
    from deerflow.runtime.runs.store.memory import MemoryRunStore

    thread_store = MemoryThreadMetaStore(InMemoryStore())
    request = SimpleNamespace(
        headers={},
        state=SimpleNamespace(auth_source=None),
        app=SimpleNamespace(
            state=SimpleNamespace(
                stream_bridge=SimpleNamespace(),
                run_manager=RunManager(store=MemoryRunStore()),
                checkpointer=InMemorySaver(),
                store=InMemoryStore(),
                run_event_store=MemoryRunEventStore(),
                run_events_config=None,
                thread_store=thread_store,
            )
        ),
    )
    body = RunCreateRequest(
        assistant_id="lead_agent",
        input={"messages": []},
        metadata=caller_metadata,
        config={"metadata": {TRACE_KEY: "forged-config", "other": "keep"}, "context": {TRACE_KEY: "forged-context"}},
    )
    before = deepcopy(body.model_dump())
    captured = {}

    async def fake_worker(*args, **kwargs):
        captured.update(kwargs["config"])
        captured["ambient_trace"] = get_current_trace_id()

    with (
        patch("app.gateway.services.resolve_agent_factory", return_value=object()),
        patch("app.gateway.services.run_agent", side_effect=fake_worker),
        request_trace_context(inbound) if inbound else nullcontext(),
    ):
        record = await start_run(body, "thread-trace-authority", request)
        await record.task
    issued = record.metadata[TRACE_KEY]
    assert issued
    if inbound:
        assert issued == inbound
    assert captured["metadata"][TRACE_KEY] == issued
    assert captured["ambient_trace"] == issued
    assert captured["metadata"]["other"] == "keep"
    assert TRACE_KEY not in record.kwargs["config"]["metadata"]
    assert TRACE_KEY not in record.kwargs["config"]["context"]
    assert TRACE_KEY not in (await thread_store.get(record.thread_id))["metadata"]
    assert body.model_dump() == before
    assert get_current_trace_id() is None
    if inbound is None:
        with (
            patch("app.gateway.services.resolve_agent_factory", return_value=object()),
            patch("app.gateway.services.run_agent", side_effect=fake_worker),
        ):
            next_record = await start_run(body, "next-thread", request)
            await next_record.task
        assert next_record.metadata[TRACE_KEY] != issued
        assert get_current_trace_id() is None


def test_metadata_merge_and_historical_echo_do_not_mutate_input(trace_config):
    from app.gateway.services import build_run_config
    from deerflow.runtime.secret_context import redact_config_secrets

    request = {"metadata": {TRACE_KEY: "old-forgery", "keep": 1}, "context": {TRACE_KEY: "old-forgery", "secrets": {"TOKEN": "private"}, "keep": 2}}
    before = deepcopy(request)
    live = build_run_config("thread-trace", request, {TRACE_KEY: "canonical"})
    assert live["metadata"][TRACE_KEY] == "canonical"
    assert request == before
    assert redact_config_secrets(request) == {"metadata": {"keep": 1}, "context": {"keep": 2}}
    assert request == before


def test_worker_runtime_context_rejects_trace_forgery():
    from deerflow.runtime.runs.worker import _build_runtime_context, _install_runtime_context

    caller = {TRACE_KEY: "forged", "agent_name": "企业员工"}
    runtime = _build_runtime_context("thread", "run", caller)
    assert TRACE_KEY not in runtime
    runtime[TRACE_KEY] = "server-issued"
    config = {"context": caller.copy()}
    _install_runtime_context(config, runtime)
    assert config["context"][TRACE_KEY] == "server-issued"
    assert config["context"]["agent_name"] == "企业员工"


def test_historical_run_response_scrubs_echo_without_rewriting_record():
    from app.gateway.routers.thread_runs import _record_to_response
    from deerflow.runtime.runs.manager import RunRecord
    from deerflow.runtime.runs.schemas import DisconnectMode, RunStatus

    record = RunRecord(
        run_id="old-run",
        thread_id="old-thread",
        assistant_id="lead_agent",
        status=RunStatus.success,
        on_disconnect=DisconnectMode.cancel,
        metadata={TRACE_KEY: "original-admission"},
        kwargs={
            "input": {"messages": []},
            "config": {
                "context": {TRACE_KEY: "forged-echo", "secrets": {"TOKEN": "private"}, "keep": 1},
                "metadata": {TRACE_KEY: "forged-echo", "keep": 2},
            },
        },
    )
    before = deepcopy(record.kwargs)
    response = _record_to_response(record)
    assert response.kwargs["config"] == {"context": {"keep": 1}, "metadata": {"keep": 2}}
    assert response.metadata == {TRACE_KEY: "original-admission"}
    assert record.kwargs == before


@pytest.mark.asyncio
async def test_concurrent_http_requests_do_not_share_trace_context():
    arrived = 0
    both_arrived = asyncio.Event()
    observed = {}

    async def route(scope, receive, send):
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            both_arrived.set()
        await asyncio.wait_for(both_arrived.wait(), timeout=5)
        observed[scope["path"]] = get_current_trace_id()
        await send({"type": "http.response.start", "status": 200, "headers": []})

    async def send(message):
        pass

    middleware = TraceMiddleware(route)
    await asyncio.gather(*(middleware({"type": "http", "path": name, "headers": [(b"x-trace-id", name.encode())]}, None, send) for name in ("trace-a", "trace-b")))
    assert observed == {"trace-a": "trace-a", "trace-b": "trace-b"}
    assert get_current_trace_id() is None

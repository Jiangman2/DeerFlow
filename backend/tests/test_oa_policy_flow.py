from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.auth_middleware import (
    _has_valid_oa_policy_flow_credential,
    _is_public,
)
from app.gateway.csrf_middleware import should_check_csrf
from app.gateway.oa_policy_flow_config import OAPolicyFlowConfig, load_oa_policy_flow_config
from app.gateway.oa_policy_flow_service import (
    OAPolicyFlowError,
    OAPolicyFlowService,
    allowed_oa_document_extensions,
    parse_strict_json_text,
)
from app.gateway.routers.oa_policy_flow import _parse_key_fields
from app.gateway.routers.oa_policy_flow import router as oa_router
from deerflow.persistence.base import Base
from deerflow.persistence.oa_policy_flow_jobs import OAPolicyFlowJobRepository, OAPolicyFlowJobRow


def _config(**overrides) -> OAPolicyFlowConfig:
    values = {
        "enabled": True,
        "ingest_key": "i" * 32,
        "owner_user_id": "owner-1",
        "assistant_id": "制度文生图智能体",
        "callback_enabled": True,
        "callback_url": "https://oa.example.test/callback",
        "callback_bearer_token": "callback-token",
        "callback_signing_secret": "signing-secret",
        "callback_ca_bundle": None,
        "poll_interval_seconds": 2,
        "run_poll_seconds": 5,
        "lease_seconds": 90,
        "max_concurrent_jobs": 2,
        "max_process_attempts": 5,
        "max_callback_attempts": 8,
        "callback_timeout_seconds": 30,
        "run_timeout_seconds": 30 * 60,
        "max_document_bytes": 10 * 1024 * 1024,
        "max_result_bytes": 10 * 1024 * 1024,
    }
    values.update(overrides)
    return OAPolicyFlowConfig(**values)


def _job_values(*, job_id: str = "job-1", key_hash: str = "a" * 64) -> dict:
    now = datetime.now(UTC)
    return {
        "id": job_id,
        "idempotency_key_hash": key_hash,
        "request_id": "OA-001",
        "request_fingerprint": "b" * 64,
        "owner_user_id": "owner-1",
        "assistant_id": "制度文生图智能体",
        "thread_id": f"oa-{job_id}",
        "status": "queued",
        "callback_status": "not_ready",
        "document_filename": "制度.docx",
        "key_fields": {"documentId": "ZD-001"},
        "process_attempts": 0,
        "callback_attempts": 0,
        "next_attempt_at": now,
        "created_at": now,
        "updated_at": now,
    }


def test_disabled_config_does_not_require_credentials(monkeypatch):
    for name in (
        "OA_POLICY_FLOW_ENABLED",
        "OA_POLICY_FLOW_INGEST_KEY",
        "OA_POLICY_FLOW_OWNER_USER_ID",
        "OA_POLICY_FLOW_AGENT_NAME",
        "OA_POLICY_FLOW_CALLBACK_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    assert load_oa_policy_flow_config().enabled is False


def test_enabled_config_loads_verified_target(monkeypatch):
    monkeypatch.setenv("OA_POLICY_FLOW_ENABLED", "true")
    monkeypatch.setenv("OA_POLICY_FLOW_INGEST_KEY", "x" * 32)
    monkeypatch.setenv("OA_POLICY_FLOW_OWNER_USER_ID", "d1792c77-c297-439d-843f-81ea66095d1a")
    monkeypatch.setenv("OA_POLICY_FLOW_AGENT_NAME", "制度文生图智能体")
    monkeypatch.setenv("OA_POLICY_FLOW_CALLBACK_URL", "https://oa.example.test/callback")
    monkeypatch.setenv("OA_POLICY_FLOW_CALLBACK_BEARER_TOKEN", "token")

    config = load_oa_policy_flow_config()

    assert config.owner_user_id == "d1792c77-c297-439d-843f-81ea66095d1a"
    assert config.assistant_id == "制度文生图智能体"
    assert config.run_timeout_seconds == 30 * 60


def test_polling_only_config_does_not_require_callback_settings(monkeypatch):
    monkeypatch.setenv("OA_POLICY_FLOW_ENABLED", "true")
    monkeypatch.setenv("OA_POLICY_FLOW_INGEST_KEY", "x" * 32)
    monkeypatch.setenv("OA_POLICY_FLOW_OWNER_USER_ID", "owner")
    monkeypatch.setenv("OA_POLICY_FLOW_AGENT_NAME", "agent")
    monkeypatch.setenv("OA_POLICY_FLOW_CALLBACK_ENABLED", "false")
    monkeypatch.delenv("OA_POLICY_FLOW_CALLBACK_URL", raising=False)
    monkeypatch.delenv("OA_POLICY_FLOW_CALLBACK_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("OA_POLICY_FLOW_CALLBACK_SIGNING_SECRET", raising=False)

    config = load_oa_policy_flow_config()

    assert config.enabled is True
    assert config.callback_enabled is False
    assert config.callback_url == ""


def test_enabled_config_rejects_http_callback_by_default(monkeypatch):
    monkeypatch.setenv("OA_POLICY_FLOW_ENABLED", "true")
    monkeypatch.setenv("OA_POLICY_FLOW_INGEST_KEY", "x" * 32)
    monkeypatch.setenv("OA_POLICY_FLOW_OWNER_USER_ID", "owner")
    monkeypatch.setenv("OA_POLICY_FLOW_AGENT_NAME", "agent")
    monkeypatch.setenv("OA_POLICY_FLOW_CALLBACK_URL", "http://oa.test/callback")
    monkeypatch.setenv("OA_POLICY_FLOW_CALLBACK_BEARER_TOKEN", "token")
    monkeypatch.delenv("OA_POLICY_FLOW_CALLBACK_ALLOW_HTTP", raising=False)

    with pytest.raises(ValueError, match="fixed HTTPS URL"):
        load_oa_policy_flow_config()


def test_enabled_config_rejects_short_callback_signing_secret(monkeypatch):
    monkeypatch.setenv("OA_POLICY_FLOW_ENABLED", "true")
    monkeypatch.setenv("OA_POLICY_FLOW_INGEST_KEY", "x" * 32)
    monkeypatch.setenv("OA_POLICY_FLOW_OWNER_USER_ID", "owner")
    monkeypatch.setenv("OA_POLICY_FLOW_AGENT_NAME", "agent")
    monkeypatch.setenv("OA_POLICY_FLOW_CALLBACK_URL", "https://oa.test/callback")
    monkeypatch.delenv("OA_POLICY_FLOW_CALLBACK_BEARER_TOKEN", raising=False)
    monkeypatch.setenv("OA_POLICY_FLOW_CALLBACK_SIGNING_SECRET", "too-short")

    with pytest.raises(ValueError, match="SIGNING_SECRET"):
        load_oa_policy_flow_config()


def test_key_fields_requires_json_object_and_canonicalizes():
    value, canonical = _parse_key_fields('{"b":2,"a":"中文"}')

    assert value == {"a": "中文", "b": 2}
    assert canonical == '{"a":"中文","b":2}'
    with pytest.raises(Exception, match="JSON object"):
        _parse_key_fields("[]")
    with pytest.raises(Exception, match="strict JSON"):
        _parse_key_fields('{"a":NaN}')
    with pytest.raises(Exception, match="strict JSON"):
        _parse_key_fields('{"a":1,"a":2}')


def test_oa_document_contract_is_limited_to_office_policy_formats():
    assert allowed_oa_document_extensions() == {".doc", ".docx", ".pdf"}
    assert _config().max_document_bytes == 10 * 1024 * 1024


def test_oa_paths_are_narrowly_auth_and_csrf_exempt():
    base = "/api/v1/integrations/oa/policy-flow/jobs"
    assert _is_public(base)
    assert _is_public(f"{base}/job-1")
    assert not _is_public(f"{base}-lookalike")
    request = SimpleNamespace(method="POST", url=SimpleNamespace(path=base))
    assert should_check_csrf(request) is False


def test_oa_credential_can_be_checked_before_multipart_parsing(monkeypatch):
    key = "k" * 32
    monkeypatch.setenv("OA_POLICY_FLOW_INGEST_KEY", key)
    valid = SimpleNamespace(headers={"authorization": f"Bearer {key}"})
    invalid = SimpleNamespace(headers={"authorization": "Bearer wrong"})

    assert _has_valid_oa_policy_flow_credential(valid) is True
    assert _has_valid_oa_policy_flow_credential(invalid) is False


def test_submit_endpoint_persists_a_machine_authenticated_job(tmp_path, monkeypatch):
    from deerflow.config import paths as paths_module

    class Repository:
        async def create_or_get(self, values):
            return values, True

    class Service:
        is_running = True

        def __init__(self):
            self.notified = False

        def notify(self):
            self.notified = True

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr(paths_module, "_paths", None)
    monkeypatch.setattr(
        "app.gateway.routers.oa_policy_flow.get_app_config",
        lambda: SimpleNamespace(
            uploads=SimpleNamespace(
                max_files=10,
                max_file_size=1024 * 1024,
                max_total_size=1024 * 1024,
            )
        ),
    )
    service = Service()
    app = FastAPI()
    app.state.oa_policy_flow_service = service
    app.state.oa_policy_flow_job_repo = Repository()
    app.state.oa_policy_flow_config = _config()
    app.include_router(oa_router)

    with TestClient(app) as client:
        unauthorized = client.post(
            "/api/v1/integrations/oa/policy-flow/jobs",
            headers={"Idempotency-Key": "policy:ZD-001"},
            data={"request_id": "ZD-001", "key_fields": "{}"},
            files={"document": ("制度.docx", b"policy", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        response = client.post(
            "/api/v1/integrations/oa/policy-flow/jobs",
            headers={
                "Authorization": f"Bearer {_config().ingest_key}",
                "Idempotency-Key": "policy:ZD-001",
            },
            data={"request_id": "ZD-001", "key_fields": '{"documentId":"ZD-001"}'},
            files={"document": ("制度.docx", b"policy", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        empty = client.post(
            "/api/v1/integrations/oa/policy-flow/jobs",
            headers={
                "Authorization": f"Bearer {_config().ingest_key}",
                "Idempotency-Key": "policy:ZD-EMPTY",
            },
            data={"request_id": "ZD-EMPTY", "key_fields": "{}"},
            files={"document": ("空制度.docx", b"", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert empty.status_code == 422
    assert service.notified is True


def test_prompt_pins_jointjs_file_contract(tmp_path, monkeypatch):
    from deerflow.config import paths as paths_module

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr(paths_module, "_paths", None)
    uploads = paths_module.get_paths().sandbox_uploads_dir("oa-job-1", user_id="owner-1")
    uploads.mkdir(parents=True)
    (uploads / "制度.md").write_text("parsed", encoding="utf-8")
    service = OAPolicyFlowService(app=None, repository=None, config=_config())

    prompt = service._build_prompt(
        {
            "thread_id": "oa-job-1",
            "owner_user_id": "owner-1",
            "document_filename": "制度.docx",
            "key_fields": {"documentId": "ZD-001"},
        }
    )

    assert prompt.startswith("请解析这份制度，按固化格式输出JointJS JSON流程数据。")
    assert "/mnt/user-data/uploads/制度.md" in prompt
    assert "仅生成一个" in prompt
    assert "present_files" in prompt


@pytest.mark.asyncio
async def test_result_acceptance_requires_one_valid_presented_json(tmp_path, monkeypatch):
    from deerflow.config import paths as paths_module

    class Checkpointer:
        async def aget_tuple(self, _config):
            return SimpleNamespace(checkpoint={"channel_values": {"artifacts": ["/mnt/user-data/outputs/流程.json"]}})

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr(paths_module, "_paths", None)
    output = paths_module.get_paths().sandbox_outputs_dir("oa-job-1", user_id="owner-1") / "流程.json"
    output.parent.mkdir(parents=True)
    output.write_text('{"cells":[]}', encoding="utf-8")
    app = SimpleNamespace(state=SimpleNamespace(checkpointer=Checkpointer()))
    service = OAPolicyFlowService(app=app, repository=None, config=_config())
    job = {"thread_id": "oa-job-1", "owner_user_id": "owner-1"}

    accepted_path, virtual_path, digest = await service._accept_result(job)

    assert accepted_path == output
    assert virtual_path == "/mnt/user-data/outputs/流程.json"
    assert len(digest) == 64

    output.write_text("not-json", encoding="utf-8")
    with pytest.raises(OAPolicyFlowError, match="valid UTF-8 JSON"):
        await service._accept_result(job)

    output.write_text('{"cells":[],"scale":NaN}', encoding="utf-8")
    with pytest.raises(OAPolicyFlowError, match="valid UTF-8 JSON"):
        await service._accept_result(job)


@pytest.mark.asyncio
async def test_active_agent_run_is_interrupted_after_configured_deadline():
    patches: list[dict] = []
    cancellations: list[tuple[str, str]] = []

    class Repository:
        async def patch(self, _job_id, **values):
            patches.append(values)
            return True

    class RunStore:
        async def get(self, run_id, *, user_id):
            assert run_id == "run-1"
            assert user_id == "owner-1"
            return {
                "run_id": run_id,
                "status": "running",
                "created_at": (datetime.now(UTC) - timedelta(minutes=31)).isoformat(),
            }

    class RunManager:
        async def cancel(self, run_id, *, action):
            cancellations.append((run_id, action))
            return "cancelled"

    app = SimpleNamespace(state=SimpleNamespace(run_store=RunStore(), run_manager=RunManager()))
    service = OAPolicyFlowService(app=app, repository=Repository(), config=_config())
    service._worker_id = "worker-test"
    job = {
        **_job_values(),
        "status": "running",
        "run_id": "run-1",
    }

    await service._poll_run(job)

    assert cancellations == [("run-1", "interrupt")]
    assert patches[-1]["status"] == "failed"
    assert patches[-1]["callback_status"] == "pending"
    assert patches[-1]["last_error_code"] == "agent_run_timeout"


@pytest.mark.asyncio
async def test_polling_only_terminal_failure_never_schedules_callback():
    patches: list[dict] = []

    class Repository:
        async def patch(self, _job_id, **values):
            patches.append(values)
            return True

    service = OAPolicyFlowService(
        app=SimpleNamespace(),
        repository=Repository(),
        config=_config(callback_enabled=False, callback_url="", callback_bearer_token="", callback_signing_secret=""),
    )
    service._worker_id = "worker-test"

    await service._set_terminal_failure(_job_values(), "agent_run_failed", "failed")

    assert patches[-1]["status"] == "failed"
    assert patches[-1]["callback_status"] == "disabled"
    assert patches[-1]["next_attempt_at"] is None


@pytest.mark.asyncio
async def test_polling_only_success_exposes_result_without_scheduling_callback(tmp_path):
    patches: list[dict] = []

    class Repository:
        async def patch(self, _job_id, **values):
            patches.append(values)
            return True

    class RunStore:
        async def get(self, _run_id, *, user_id):
            assert user_id == "owner-1"
            return {"status": "success"}

    service = OAPolicyFlowService(
        app=SimpleNamespace(state=SimpleNamespace(run_store=RunStore())),
        repository=Repository(),
        config=_config(callback_enabled=False, callback_url="", callback_bearer_token="", callback_signing_secret=""),
    )
    service._worker_id = "worker-test"
    output = tmp_path / "result.json"
    output.write_text('{"cells":[]}', encoding="utf-8")

    async def accept_result(_job):
        return output, "/mnt/user-data/outputs/result.json", "d" * 64

    service._accept_result = accept_result
    await service._poll_run({**_job_values(), "status": "running", "run_id": "run-1"})

    assert patches[-1]["status"] == "succeeded"
    assert patches[-1]["callback_status"] == "disabled"
    assert patches[-1]["output_virtual_path"] == "/mnt/user-data/outputs/result.json"
    assert patches[-1]["output_sha256"] == "d" * 64
    assert patches[-1]["next_attempt_at"] is None


@pytest.mark.asyncio
async def test_disabling_callback_drains_legacy_pending_callback_without_http():
    patches: list[dict] = []

    class Repository:
        async def patch(self, _job_id, **values):
            patches.append(values)
            return True

    service = OAPolicyFlowService(
        app=SimpleNamespace(),
        repository=Repository(),
        config=_config(callback_enabled=False, callback_url="", callback_bearer_token="", callback_signing_secret=""),
    )
    service._worker_id = "worker-test"
    job = {
        **_job_values(),
        "status": "succeeded",
        "callback_status": "pending",
    }

    await service._process_claimed_body(job)

    assert patches[-1]["callback_status"] == "disabled"
    assert patches[-1]["next_attempt_at"] is None


@pytest.mark.asyncio
async def test_repository_idempotency_and_expiring_lease(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'oa.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync_connection: Base.metadata.create_all(
                sync_connection,
                tables=[OAPolicyFlowJobRow.__table__],
            )
        )
    repository = OAPolicyFlowJobRepository(async_sessionmaker(engine, expire_on_commit=False))
    try:
        created, was_created = await repository.create_or_get(_job_values())
        duplicate, duplicate_created = await repository.create_or_get(_job_values(job_id="job-2"))

        assert was_created is True
        assert duplicate_created is False
        assert duplicate["id"] == created["id"]

        now = datetime.now(UTC)
        first = await repository.claim_due(
            worker_id="worker-a",
            lease_seconds=30,
            limit=1,
            now=now,
        )
        concurrent = await repository.claim_due(
            worker_id="worker-b",
            lease_seconds=30,
            limit=1,
            now=now,
        )
        assert await repository.renew_lease(
            "job-1",
            lease_owner="worker-a",
            lease_seconds=30,
            now=now + timedelta(seconds=20),
        )
        premature_takeover = await repository.claim_due(
            worker_id="worker-b",
            lease_seconds=30,
            limit=1,
            now=now + timedelta(seconds=31),
        )
        takeover = await repository.claim_due(
            worker_id="worker-b",
            lease_seconds=30,
            limit=1,
            now=now + timedelta(seconds=51),
        )

        assert [job["id"] for job in first] == ["job-1"]
        assert concurrent == []
        assert premature_takeover == []
        assert [job["id"] for job in takeover] == ["job-1"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_successful_failure_callback_is_signed_and_marked_delivered(monkeypatch):
    patches: list[dict] = []
    sent: dict = {}

    class Repository:
        async def patch(self, _job_id, **values):
            patches.append(values)
            return True

    class Response:
        status_code = 204

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, method, url, **kwargs):
            sent["method"] = method
            sent["url"] = url
            sent.update(kwargs)
            return Response()

    monkeypatch.setattr("app.gateway.oa_policy_flow_service.httpx.AsyncClient", Client)
    service = OAPolicyFlowService(app=SimpleNamespace(), repository=Repository(), config=_config())
    service._worker_id = "worker-test"
    now = datetime.now(UTC).isoformat()
    job = {
        **_job_values(),
        "status": "failed",
        "callback_status": "pending",
        "completed_at": now,
        "last_error_code": "agent_run_failed",
        "last_error_detail": "failed",
        "output_filename": None,
        "output_sha256": None,
    }

    await service._deliver_callback(job)

    assert sent["url"] == "https://oa.example.test/callback"
    assert sent["method"] == "POST"
    assert sent["headers"]["Authorization"] == "Bearer callback-token"
    assert sent["headers"]["X-OA-Callback-Signature"].startswith("sha256=")
    manifest = json.loads(sent["files"]["manifest"][1])
    assert manifest["key_fields"] == {"documentId": "ZD-001"}
    assert patches[-1]["callback_status"] == "delivered"


@pytest.mark.asyncio
async def test_success_callback_carries_result_file_and_verifiable_sha_signature(tmp_path, monkeypatch):
    from deerflow.config import paths as paths_module

    patches: list[dict] = []
    sent: dict = {}

    class Repository:
        async def patch(self, _job_id, **values):
            patches.append(values)
            return True

    class Response:
        status_code = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, method, url, **kwargs):
            sent["method"] = method
            sent["url"] = url
            sent.update(kwargs)
            return Response()

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr(paths_module, "_paths", None)
    payload = b'{"cells":[{"id":"start","type":"standard.Rectangle"}]}'
    digest = hashlib.sha256(payload).hexdigest()
    output = paths_module.get_paths().sandbox_outputs_dir("oa-job-1", user_id="owner-1") / "policy-flow.json"
    output.parent.mkdir(parents=True)
    output.write_bytes(payload)
    monkeypatch.setattr("app.gateway.oa_policy_flow_service.httpx.AsyncClient", Client)
    service = OAPolicyFlowService(app=SimpleNamespace(), repository=Repository(), config=_config())
    service._worker_id = "worker-test"
    job = {
        **_job_values(),
        "status": "succeeded",
        "callback_status": "pending",
        "completed_at": datetime.now(UTC).isoformat(),
        "output_filename": output.name,
        "output_sha256": digest,
        "output_virtual_path": "/mnt/user-data/outputs/policy-flow.json",
    }

    await service._deliver_callback(job)

    manifest_json = sent["files"]["manifest"][1]
    callback_file = sent["files"]["result_file"]
    timestamp = sent["headers"]["X-OA-Callback-Timestamp"]
    expected_signature = hmac.new(
        _config().callback_signing_secret.encode(),
        f"{timestamp}.{manifest_json}.{digest}".encode(),
        hashlib.sha256,
    ).hexdigest()
    assert sent["method"] == "POST"
    assert sent["headers"]["Idempotency-Key"] == "oa-policy-flow-callback:job-1"
    assert sent["headers"]["X-OA-Callback-Signature"] == f"sha256={expected_signature}"
    assert callback_file == (output.name, payload, "application/json")
    assert hashlib.sha256(callback_file[1]).hexdigest() == json.loads(manifest_json)["result_sha256"]
    assert patches[-1]["callback_status"] == "delivered"
    assert patches[-1]["last_callback_status_code"] == 200


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_status", "should_retry"),
    [(503, "pending", True), (422, "exhausted", False)],
)
async def test_callback_retries_only_retryable_statuses(status_code, expected_status, should_retry, monkeypatch):
    patches: list[dict] = []

    class Repository:
        async def patch(self, _job_id, **values):
            patches.append(values)
            return True

    class Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, *_args, **_kwargs):
            response = Response()
            response.status_code = status_code
            return response

    monkeypatch.setattr("app.gateway.oa_policy_flow_service.httpx.AsyncClient", Client)
    service = OAPolicyFlowService(app=SimpleNamespace(), repository=Repository(), config=_config())
    service._worker_id = "worker-test"
    job = {
        **_job_values(),
        "status": "failed",
        "callback_status": "pending",
        "completed_at": datetime.now(UTC).isoformat(),
        "last_error_code": "agent_run_failed",
        "last_error_detail": "failed",
        "output_filename": None,
        "output_sha256": None,
    }

    await service._deliver_callback(job)

    assert patches[-1]["callback_status"] == expected_status
    assert patches[-1]["callback_attempts"] == 1
    assert patches[-1]["last_callback_status_code"] == status_code
    assert (patches[-1]["next_attempt_at"] is not None) is should_retry


def test_real_oa_docx_and_json_samples_satisfy_minimum_transport_contract():
    document_value = os.getenv("OA_POLICY_FLOW_REAL_DOCUMENT_FIXTURE")
    json_value = os.getenv("OA_POLICY_FLOW_REAL_JSON_FIXTURE")
    if not document_value or not json_value:
        pytest.skip("Set OA_POLICY_FLOW_REAL_DOCUMENT_FIXTURE and OA_POLICY_FLOW_REAL_JSON_FIXTURE")

    document = Path(document_value)
    result = Path(json_value)
    document_payload = document.read_bytes()
    result_payload = result.read_bytes()
    parsed = parse_strict_json_text(result_payload.decode("utf-8"))

    assert document.suffix.lower() in allowed_oa_document_extensions()
    assert 0 < len(document_payload) <= _config().max_document_bytes
    assert result.suffix.lower() == ".json"
    assert 0 < len(result_payload) <= _config().max_result_bytes
    assert isinstance(parsed, dict)
    assert isinstance(parsed.get("cells"), list)
    assert parsed["cells"]
    assert len(hashlib.sha256(document_payload).hexdigest()) == 64
    assert len(hashlib.sha256(result_payload).hexdigest()) == 64


@pytest.mark.asyncio
async def test_callback_preparation_failure_never_requeues_agent_run():
    patches: list[dict] = []

    class Repository:
        async def patch(self, _job_id, **values):
            patches.append(values)
            return True

    service = OAPolicyFlowService(app=SimpleNamespace(), repository=Repository(), config=_config())
    service._worker_id = "worker-test"
    job = {
        **_job_values(),
        "status": "succeeded",
        "callback_status": "pending",
        "callback_attempts": 0,
        "completed_at": datetime.now(UTC).isoformat(),
        "output_filename": "missing.json",
        "output_sha256": "c" * 64,
        "output_virtual_path": "/mnt/user-data/outputs/missing.json",
    }

    await service._process_claimed_body(job)

    assert patches[-1]["callback_status"] == "pending"
    assert "status" not in patches[-1]
    assert patches[-1]["callback_attempts"] == 1

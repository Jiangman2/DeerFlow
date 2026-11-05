from __future__ import annotations

import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_oa_policy_flow import _config, _job_values

from app.gateway.oa_policy_flow_results import collect_result_set, read_result_snapshot
from app.gateway.oa_policy_flow_service import OAPolicyFlowError, OAPolicyFlowService
from app.gateway.routers.oa_policy_flow import _job_response, router


@pytest.fixture
def outputs(tmp_path, monkeypatch):
    from deerflow.config import paths as paths_module

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr(paths_module, "_paths", None)
    path = paths_module.get_paths().sandbox_outputs_dir("oa-job-1", user_id="owner-1")
    path.mkdir(parents=True)
    return path


def make_job(outputs, count=3):
    artifacts = []
    for i in range(count):
        name = f"流程{i}.json"
        (outputs / name).write_text(json.dumps({"cells": [], "index": i}), encoding="utf-8")
        artifacts.append("/mnt/user-data/outputs/" + name)
    return {**_job_values(), "result_protocol": "v2", "run_id": "run-1"}, artifacts


def test_batch_snapshots_survive_agent_edits_and_support_idempotent_collection(outputs):
    job, artifacts = make_job(outputs)
    rows = collect_result_set(job, artifacts, _config())
    assert rows == collect_result_set(job, list(reversed(artifacts)), _config())
    assert len(rows) == 3
    for i, row in enumerate(rows):
        (outputs / row["filename"]).write_text("changed", encoding="utf-8")
        payload = read_result_snapshot(job, row, max_result_bytes=1000)
        assert json.loads(payload)["index"] == i
        assert row["sha256"] == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize("problem", ["invalid_json", "duplicate", "count", "total", "single", "outside", "duplicate_name"])
def test_batch_rejects_entire_invalid_set_before_snapshot(outputs, problem):
    job, artifacts = make_job(outputs)
    config = _config()
    if problem == "invalid_json":
        (outputs / "流程2.json").write_text('{"bad":NaN}')
    elif problem == "duplicate":
        artifacts.append(artifacts[0])
    elif problem == "count":
        config = _config(max_result_files=2)
    elif problem == "total":
        config = _config(max_total_result_bytes=30)
    elif problem == "single":
        config = _config(max_result_bytes=2)
    elif problem == "outside":
        artifacts[-1] = "/mnt/user-data/uploads/secret.json"
    elif problem == "duplicate_name":
        (outputs / "other").mkdir()
        (outputs / "other" / "流程0.json").write_text("{}")
        artifacts.append("/mnt/user-data/outputs/other/流程0.json")
    with pytest.raises(OAPolicyFlowError):
        collect_result_set(job, artifacts, config)
    from deerflow.config.paths import get_paths

    assert not (get_paths().thread_dir(job["thread_id"], user_id=job["owner_user_id"]) / "oa-delivery").exists()


def test_v1_response_has_no_v2_wire_fields():
    data = _job_response(_job_values()).model_dump(exclude_unset=True)
    assert "results" not in data and "result_protocol" not in data
    assert "result_filename" in data


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_run", [False, True])
async def test_acceptance_binds_original_run_checkpoint(outputs, wrong_run):
    job, artifacts = make_job(outputs, 2)

    class Runs:
        async def list_by_thread(self, *args, **kwargs):
            return [{"run_id": "run-1", "status": "success"}]

    class Checkpointer:
        async def aget_tuple(self, config):
            return SimpleNamespace(metadata={"deerflow_timing_run_id": "later" if wrong_run else "run-1"}, checkpoint={"channel_values": {"artifacts": artifacts}})

    service = OAPolicyFlowService(app=SimpleNamespace(state=SimpleNamespace(run_store=Runs(), checkpointer=Checkpointer())), repository=None, config=_config())
    if wrong_run:
        with pytest.raises(OAPolicyFlowError, match="attributed"):
            await service._accept_result_set(job)
    else:
        assert len(await service._accept_result_set(job)) == 2


@pytest.mark.asyncio
async def test_new_run_during_snapshot_collection_is_rejected(outputs):
    from unittest.mock import AsyncMock

    job, artifacts = make_job(outputs, 2)
    runs = SimpleNamespace(
        list_by_thread=AsyncMock(
            side_effect=[
                [{"run_id": "run-1", "status": "success"}],
                [{"run_id": "later", "status": "running"}, {"run_id": "run-1", "status": "success"}],
            ]
        )
    )
    saved = SimpleNamespace(metadata={"deerflow_timing_run_id": "run-1"}, checkpoint={"channel_values": {"artifacts": artifacts}})
    app = SimpleNamespace(state=SimpleNamespace(run_store=runs, checkpointer=SimpleNamespace(aget_tuple=AsyncMock(return_value=saved))))
    service = OAPolicyFlowService(app=app, repository=None, config=_config())
    with pytest.raises(OAPolicyFlowError, match="changed during"):
        await service._accept_result_set(job)


@pytest.mark.asyncio
async def test_error_run_with_artifacts_is_never_accepted(outputs):
    job, _ = make_job(outputs)
    patches = []

    class Runs:
        async def get(self, *args, **kwargs):
            return {"status": "error", "stop_reason": "recursion_capped"}

    class Repo:
        async def patch(self, job_id, **values):
            patches.append(values)
            return True

    service = OAPolicyFlowService(app=SimpleNamespace(state=SimpleNamespace(run_store=Runs())), repository=Repo(), config=_config())
    await service._poll_run(job)
    assert patches[-1]["status"] == "failed"
    assert patches[-1]["result_files"] is None


@pytest.mark.asyncio
async def test_v2_callback_retry_preserves_all_parts_manifest_signature_and_success(outputs, monkeypatch):
    job, artifacts = make_job(outputs)
    job.update(status="succeeded", result_files=collect_result_set(job, artifacts, _config()))
    sent, patches = [], []

    class Repo:
        async def patch(self, job_id, **values):
            patches.append(values)
            return True

    class Response:
        status_code = 503

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    class Client(Response):
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False

        def stream(self, method, url, **kwargs):
            sent.append(kwargs)
            return Response()

    monkeypatch.setattr("app.gateway.oa_policy_flow_service.httpx.AsyncClient", Client)
    service = OAPolicyFlowService(app=SimpleNamespace(), repository=Repo(), config=_config())
    await service._deliver_callback(job)
    assert patches[-1]["callback_status"] == "pending"
    assert "status" not in patches[-1]
    job["callback_attempts"] = 1
    Response.status_code = 204
    await service._deliver_callback(job)
    assert patches[-1]["callback_status"] == "delivered"
    assert sent[0]["files"] == sent[1]["files"]
    request = sent[0]
    manifest = request["files"]["manifest"][1]
    data = json.loads(manifest)
    assert len(data["results"]) == 3 and data["result_protocol"] == "v2"
    for row in data["results"]:
        payload = request["files"][row["part_name"]][1]
        assert row["sha256"] == hashlib.sha256(payload).hexdigest()
    signature_input = f"v2.{request['headers']['X-OA-Callback-Timestamp']}.{manifest}".encode()
    assert request["headers"]["X-OA-Callback-Signature"] == "sha256=" + hmac.new(b"signing-secret", signature_input, hashlib.sha256).hexdigest()


def test_v2_download_authentication_selection_and_integrity(outputs):
    job, artifacts = make_job(outputs)
    job.update(status="succeeded", result_files=collect_result_set(job, artifacts, _config()))

    class Repo:
        async def get(self, job_id):
            return job if job_id == job["id"] else None

    app = FastAPI()
    app.state.oa_policy_flow_config = _config()
    app.state.oa_policy_flow_service = SimpleNamespace(is_running=True)
    app.state.oa_policy_flow_job_repo = Repo()
    app.include_router(router)
    client = TestClient(app)
    base = "/api/v1/integrations/oa/policy-flow/jobs/job-1"
    headers = {"Authorization": "Bearer " + "i" * 32}
    assert client.get(base + "/results/result_001").status_code == 401
    assert client.get(base + "/result", headers=headers).status_code == 409
    assert client.get(base + "/results/result_004", headers=headers).status_code == 404
    response = client.get(base + "/results/result_003", headers=headers)
    assert response.status_code == 200 and response.json()["index"] == 2
    assert response.headers["content-disposition"].startswith("attachment;")
    from deerflow.config.paths import get_paths

    root = get_paths().thread_dir(job["thread_id"], user_id=job["owner_user_id"]) / "oa-delivery"
    (root / (job["result_files"][2]["sha256"] + ".json")).write_bytes(b"{}")
    assert client.get(base + "/results/result_003", headers=headers).status_code == 410


def test_submission_version_gate_and_cross_version_idempotency(outputs, monkeypatch):
    stored = {}

    class Repo:
        async def create_or_get(self, values):
            key = values["idempotency_key_hash"]
            if key in stored:
                return stored[key], False
            stored[key] = values
            return values, True

    monkeypatch.setattr("app.gateway.routers.oa_policy_flow.get_app_config", lambda: SimpleNamespace(uploads=SimpleNamespace(max_files=10, max_file_size=1048576, max_total_size=1048576)))
    app = FastAPI()
    app.state.oa_policy_flow_config = _config()
    app.state.oa_policy_flow_service = SimpleNamespace(is_running=True, notify=lambda: None)
    app.state.oa_policy_flow_job_repo = Repo()
    app.include_router(router)
    client = TestClient(app)

    def submit(version=None):
        data = {"request_id": "test", "key_fields": "{}"}
        if version:
            data["result_protocol"] = version
        return client.post("/api/v1/integrations/oa/policy-flow/jobs", headers={"Authorization": "Bearer " + "i" * 32, "Idempotency-Key": "same-request"}, data=data, files={"document": ("policy.docx", b"policy")})

    assert submit("v3").status_code == 422
    assert submit("v2").status_code == 503
    app.state.oa_policy_flow_config = _config(multi_result_enabled=True)
    first = submit("v2")
    assert first.status_code == 202 and first.json()["result_protocol"] == "v2"
    assert submit("v2").json()["job_id"] == first.json()["job_id"]
    assert submit().status_code == 409


def test_additive_migration_preserves_legacy_row_and_is_idempotent():
    import importlib.util
    from pathlib import Path

    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).parents[1] / "packages/harness/deerflow/persistence/migrations/versions/0021_oa_result_sets.py"
    spec = importlib.util.spec_from_file_location("oa_result_sets_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE oa_policy_flow_jobs (id TEXT PRIMARY KEY, status TEXT)")
        connection.exec_driver_sql("INSERT INTO oa_policy_flow_jobs VALUES ('old','succeeded')")
        module.op = Operations(MigrationContext.configure(connection))
        module.upgrade()
        module.upgrade()
        row = connection.exec_driver_sql("SELECT * FROM oa_policy_flow_jobs").mappings().one()
        assert dict(row) == {"id": "old", "status": "succeeded", "result_protocol": "v1", "result_files": None}
        with pytest.raises(RuntimeError, match="retained"):
            module.downgrade()
    engine.dispose()


@pytest.mark.asyncio
async def test_oa_budget_is_independent_of_scheduler_and_still_globally_capped(monkeypatch):
    from unittest.mock import AsyncMock

    from app.gateway import services

    config = SimpleNamespace(scheduler=SimpleNamespace(recursion_limit=300), max_recursion_limit=2000)
    monkeypatch.setattr(services, "get_app_config", lambda: config)
    start = AsyncMock(return_value=SimpleNamespace(run_id="run-1", thread_id="thread-1"))
    monkeypatch.setattr(services, "start_run", start)
    args = dict(thread_id="thread-1", assistant_id="agent", prompt="test", app=SimpleNamespace())
    await services.launch_scheduled_thread_run(**args)
    assert start.await_args.args[0].config["recursion_limit"] == 300
    await services.launch_scheduled_thread_run(**args, recursion_limit=_config().recursion_limit)
    assert start.await_args.args[0].config["recursion_limit"] == 1000
    config.max_recursion_limit = 500
    await services.launch_scheduled_thread_run(**args, recursion_limit=1000)
    assert start.await_args.args[0].config["recursion_limit"] == 500


@pytest.mark.asyncio
async def test_real_graph_can_complete_after_300_steps_without_removing_hard_cap():
    from typing import TypedDict

    from langgraph.errors import GraphRecursionError
    from langgraph.graph import END, START, StateGraph

    class State(TypedDict):
        steps: int
        target: int

    def advance(state):
        return {"steps": state["steps"] + 1}

    graph = StateGraph(State)
    graph.add_node("stage", advance)
    graph.add_edge(START, "stage")
    graph.add_conditional_edges("stage", lambda s: END if s["steps"] >= s["target"] else "stage")
    compiled = graph.compile()
    with pytest.raises(GraphRecursionError):
        await compiled.ainvoke({"steps": 0, "target": 320}, {"recursion_limit": 300})
    result = await compiled.ainvoke({"steps": 0, "target": 320}, {"recursion_limit": _config().recursion_limit})
    assert result["steps"] == 320
    with pytest.raises(GraphRecursionError):
        await compiled.ainvoke({"steps": 0, "target": 1100}, {"recursion_limit": _config().recursion_limit})

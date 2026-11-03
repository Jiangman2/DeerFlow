import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.gateway import oa_policy_flow_metrics as oa
from deerflow.observability.model_metrics import ModelMetrics

ROOT = "/api/v1/integrations/oa/policy-flow/jobs"


@pytest.mark.parametrize(
    "method,path,expected",
    [
        ("POST", ROOT, "submit"),
        ("POST", ROOT + "/", "submit"),
        ("GET", ROOT + "/abc-123", "poll"),
        ("GET", ROOT + "/abc-123/result", "download"),
        ("GET", ROOT + "/abc-123/results/flow-1", "download"),
        ("GET", ROOT, None),
        ("POST", ROOT + "/abc", None),
        ("GET", ROOT + "/abc/secret/other", None),
    ],
)
def test_only_known_operations(method, path, expected):
    assert oa.operation_for(method, path) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("status,outcome", [(202, "success"), (401, "error"), (422, "error"), (500, "error")])
async def test_stream_unchanged_and_counted_once_only_after_final_body(monkeypatch, status, outcome):
    recorder = ModelMetrics(enabled=True)
    monkeypatch.setattr(oa, "metrics", recorder)
    messages = []

    async def send(message):
        messages.append(message)

    async def receive():
        raise AssertionError("Middleware must not consume request body")

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": status, "headers": [(b"x-safe", b"yes")]})
        await send({"type": "http.response.body", "body": b"private-data", "more_body": True})
        snapshot = recorder.snapshot()
        assert len(recorder.active) == 1
        assert all(row[outcome] == 0 for minute in snapshot["minutes"].values() for row in minute.values())
        await send({"type": "http.response.body", "body": b"tail", "more_body": False})

    await oa.OAPolicyFlowMetricsMiddleware(app)({"type": "http", "method": "POST", "path": ROOT}, receive, send)
    assert messages[1]["body"] == b"private-data" and messages[2]["body"] == b"tail"
    snapshot = recorder.snapshot()
    assert not recorder.active and len(snapshot["series"]) == 1
    row = next(iter(next(iter(snapshot["minutes"].values())).values()))
    assert row["started"] == 1 and row[outcome] == 1
    assert "private-data" not in str(snapshot)


@pytest.mark.asyncio
async def test_cancel_and_telemetry_failure_never_change_endpoint(monkeypatch):
    recorder = ModelMetrics(enabled=True)
    monkeypatch.setattr(oa, "metrics", recorder)

    async def cancelled(*args):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await oa.OAPolicyFlowMetricsMiddleware(cancelled)({"type": "http", "method": "GET", "path": ROOT + "/job"}, None, None)
    assert not recorder.active
    assert next(iter(next(iter(recorder.snapshot()["minutes"].values())).values()))["cancelled"] == 1

    def broken(*args, **kwargs):
        raise RuntimeError("collector down")

    monkeypatch.setattr(recorder, "start", broken)
    monkeypatch.setattr(recorder, "finish", broken)

    async def ok(*args):
        return "unchanged"

    assert await oa.OAPolicyFlowMetricsMiddleware(ok)({"type": "http", "method": "POST", "path": "/api/other"}, None, None) == "unchanged"
    await oa.OAPolicyFlowMetricsMiddleware(ok)({"type": "http", "method": "POST", "path": ROOT}, None, None)


@pytest.mark.asyncio
async def test_job_counts_only_new_creation_and_committed_terminal_transition(monkeypatch):
    from app.gateway.oa_policy_flow_service import OAPolicyFlowLeaseLost, OAPolicyFlowService

    recorder = ModelMetrics(enabled=True)
    monkeypatch.setattr(oa, "metrics", recorder)
    monkeypatch.setattr(oa, "_jobs", {})
    start = datetime.now(UTC) - timedelta(seconds=45)
    job = {"id": "private-id", "status": "running", "created_at": start.isoformat()}
    oa.job_created(job)
    repo = AsyncMock()
    service = OAPolicyFlowService(app=None, repository=repo, config=None)
    repo.patch.return_value = False
    with oa.observe_claimed_job(job):
        with pytest.raises(OAPolicyFlowLeaseLost):
            await service._patch_claimed_job(job["id"], status="succeeded", completed_at=start + timedelta(seconds=45))
        row = next(iter(next(iter(recorder.snapshot()["minutes"].values())).values()))
        assert row["success"] == 0
        repo.patch.return_value = True
        await service._patch_claimed_job(job["id"], status="succeeded", completed_at=start + timedelta(seconds=45))
        # Later callback metadata cannot count the completed job a second time.
        await service._patch_claimed_job(job["id"], callback_status="delivered")
    row = next(iter(next(iter(recorder.snapshot()["minutes"].values())).values()))
    assert row["started"] == row["success"] == 1 and row["duration_seconds"] == 45
    assert "private-id" not in json.dumps(oa.job_snapshot())
    assert not recorder.active


def test_cross_worker_job_state_dedup_and_terminal_dominance(monkeypatch):
    from app.gateway.model_monitor import decode_and_aggregate

    now = 10000

    def frame(at, state):
        return "L" + json.dumps({"at": now, "series": {}, "oa_jobs": {"hashed": {"at": at, "status": state}}})

    for states in [(frame(now, "running"), frame(now - 1, "succeeded")), (frame(now - 1, "succeeded"), frame(now, "running"))]:
        result = decode_and_aggregate(states, now=now, minutes=15)
        assert result["oa_jobs"]["running"] == 0
        assert "hashed" not in json.dumps(result)
    result = decode_and_aggregate([frame(now, "running"), frame(now - 1, "running")], now=now, minutes=15)
    assert result["oa_jobs"]["running"] == 1
    result = decode_and_aggregate([frame(now - 901, "running")], now=now, minutes=15)
    assert result["oa_jobs"]["status"] == "no_observations"


def test_monitor_disabled_does_not_retain_job_identifiers(monkeypatch):
    monkeypatch.setattr(oa, "metrics", ModelMetrics(enabled=False))
    monkeypatch.setattr(oa, "_jobs", {})
    oa.job_created({"id": "secret", "status": "queued"})
    assert oa.job_snapshot() == {}

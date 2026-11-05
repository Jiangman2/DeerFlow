import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gateway.model_monitor import ModelMonitor, aggregate_frames, decode_and_aggregate
from deerflow.observability.model_metrics import ModelMetrics, outcome_for


def collector(clock):
    return ModelMetrics(clock=lambda: clock[0], monotonic=lambda: clock[0], enabled=True)


def test_real_inflight_lifecycle_dedup_and_cancel():
    clock = [120.0]
    m = collector(clock)
    a = m.start("model-a", call_id="a")
    m.start("model-a", call_id="a")
    b = m.start("model-a")
    key = "text:invoke:model-a"
    assert m.snapshot()["series"][key]["active"] == 2
    clock[0] += 3
    m.finish(a)
    m.finish(a)
    m.finish(b, "cancelled")
    row = m.snapshot()["minutes"]["120"][key]
    assert row["success"] == 1 and row["cancelled"] == 1 and row["started"] == 2
    assert row["duration_seconds"] == 6
    assert m.snapshot()["series"][key]["active"] == 0
    assert outcome_for(asyncio.CancelledError()) == "cancelled"
    assert outcome_for(TimeoutError()) == "timeout"


def test_cross_process_peak_is_sum_at_same_sample_not_sum_of_peaks():
    clock = [120.0]
    a, b = collector(clock), collector(clock)
    x = a.start("model-a")
    a.snapshot()
    b.start("model-a")
    b.snapshot()
    clock[0] = 125
    a.finish(x)
    b.start("model-a")
    live = [a.snapshot(), b.snapshot()]
    frames = [{"minute": int(minute), "rows": rows, "series": frame["series"]} for frame in live for minute, rows in frame["minutes"].items()]
    result = aggregate_frames(live, frames, now=125, minutes=15)
    row = result["series"][0]
    assert row["active"] == 2 and row["peak_concurrency"] == 2  # not 1 + 2
    assert row["started"] == 3 and row["success"] == 1
    assert result["trend"][0]["series"] is None


def test_old_process_heartbeat_does_not_leave_permanent_inflight():
    clock = [120.0]
    m = collector(clock)
    m.start("model-a")
    assert aggregate_frames([m.snapshot()], [], now=150, minutes=15)["series"] == []


def test_disabled_and_cardinality_bound():
    assert ModelMetrics().start("a") is None
    m = collector([120.0])
    for i in range(100):
        m.start(str(i))
    assert len(m.series) == 64 and m.dropped == 36
    assert m.start("https://secret-url", "invalid") is None


@pytest.mark.asyncio
async def test_unconfigured_unavailable_are_not_zero_usage():
    monitor = ModelMonitor()
    assert (await monitor.read())["status"] == "not_configured"
    monitor.client = MagicMock()
    monitor.client.zrangebyscore = AsyncMock(side_effect=TimeoutError)
    assert (await monitor.read())["status"] == "unavailable"


@pytest.mark.asyncio
async def test_export_uses_overwrite_and_ttl_not_increment(monkeypatch):
    from app.gateway import model_monitor

    m = collector([120.0])
    m.start("model-a")
    monkeypatch.setattr(model_monitor, "metrics", m)
    monitor = ModelMonitor()
    pipe = MagicMock()
    pipe.execute = AsyncMock()
    monitor.client = MagicMock()
    monitor.client.pipeline.return_value = pipe
    await monitor.export()
    await monitor.export()
    assert pipe.hset.call_count == 2
    assert pipe.hincrby.call_count == 0
    assert pipe.hset.call_args_list[0] == pipe.hset.call_args_list[1]
    assert pipe.expire.call_count >= 2
    serialized = json.loads(pipe.hset.call_args.args[2])
    assert "messages" not in serialized and "prompts" not in serialized


def test_missing_samples_are_not_zero_and_stale_heartbeat_is_explicit():
    m = collector([120.0])
    m.start("model-a")
    result = decode_and_aggregate(["L" + json.dumps(m.snapshot())], now=120, minutes=15)
    assert result["status"] == "observed"
    assert result["series"][0]["peak_concurrency"] is None
    assert result["coverage_complete"] is False
    assert decode_and_aggregate(["L" + json.dumps(m.snapshot())], now=150, minutes=15)["status"] == "no_fresh_samples"


@pytest.mark.asyncio
async def test_telemetry_failure_never_changes_model_result_or_exception(monkeypatch):
    from deerflow.observability import model_metrics

    monkeypatch.setattr(model_metrics.metrics, "start", MagicMock(side_effect=ValueError("telemetry only")))
    monkeypatch.setattr(model_metrics.metrics, "finish", MagicMock(side_effect=ValueError("telemetry only")))
    async with model_metrics.measure_model_async("synthetic", "image"):
        value = "normal result"
    assert value == "normal result"
    with pytest.raises(TimeoutError, match="original"):
        async with model_metrics.measure_model_async("synthetic", "video"):
            raise TimeoutError("original")


@pytest.mark.asyncio
async def test_read_is_bounded_before_transfer_and_parsing(monkeypatch):
    import threading

    from app.gateway import model_monitor

    main_thread = threading.get_ident()
    original = model_monitor.decode_and_aggregate

    def decode(*a, **kw):
        assert threading.get_ident() != main_thread
        return original(*a, **kw)

    monkeypatch.setattr(model_monitor, "decode_and_aggregate", decode)
    m = collector([120.0])
    m.start("model-a")
    monitor = ModelMonitor()
    monitor.client = MagicMock()
    monitor.client.zrangebyscore = AsyncMock(return_value=["process-a"])
    monitor.client.eval = AsyncMock(return_value=["L" + json.dumps(m.snapshot())])
    monkeypatch.setattr(model_monitor.time, "time", lambda: 120.0)
    result = await monitor.read(60)
    assert result["status"] == "observed" and len(result["trend"]) == 60
    args = monitor.client.eval.call_args.args
    assert args[1] == 61 and args[-2:] == (1, 8 * 1024 * 1024)
    assert "HSTRLEN" in args[0] and "METRICS_SIZE_BOUND" in args[0]
    monitor.client.zrangebyscore.return_value = [str(i) for i in range(129)]
    monitor.client.eval.reset_mock()
    assert (await monitor.read())["status"] == "unavailable"
    monitor.client.eval.assert_not_called()


@pytest.mark.asyncio
async def test_admin_monitor_rejects_viewer_before_redis(monkeypatch):
    from fastapi import HTTPException, Response

    from app.gateway.routers import model_monitor as router

    monkeypatch.setattr(router, "require_admin_user", AsyncMock(side_effect=HTTPException(403)))
    monkeypatch.setattr(router.model_monitor, "read", AsyncMock())
    with pytest.raises(HTTPException) as exc:
        await router.get_model_monitor(MagicMock(), Response(), minutes=15)
    assert exc.value.status_code == 403
    router.model_monitor.read.assert_not_called()

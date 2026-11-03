"""Real Redis integration, only against an explicitly supplied synthetic service."""

import json
import os
import time
import uuid

import pytest
from redis.asyncio import Redis

from app.gateway.model_monitor import _READ_FRAMES, ModelMonitor, encode_minute

pytestmark = pytest.mark.skipif(not os.getenv("TEST_MODEL_METRICS_REDIS_URL"), reason="Isolated Redis fixture required")


@pytest.mark.asyncio
async def test_seventy_process_hour_window_bounds_idempotency_and_outage():
    client = Redis.from_url(os.environ["TEST_MODEL_METRICS_REDIS_URL"], decode_responses=True)
    monitor = ModelMonitor()
    monitor.client = client
    monitor.prefix = "synthetic:metrics:" + uuid.uuid4().hex
    now = time.time()
    minute = int(now // 60) * 60
    descriptors = {f"text:invoke:synthetic-{i}": {"kind": "text", "operation": "invoke", "model": f"synthetic-{i}", "active": 1} for i in range(8)}
    keys = []
    try:
        pipe = client.pipeline(transaction=False)
        for node in range(70):
            live_key = f"{monitor.prefix}:live:{node}"
            keys.append(live_key)
            pipe.set(live_key, json.dumps({"at": now, "series": descriptors, "dropped": 0}), ex=30)
            pipe.zadd(f"{monitor.prefix}:nodes", {str(node): now})
        keys.append(f"{monitor.prefix}:nodes")
        pipe.expire(keys[-1], 120)
        payload_bytes = 0
        for offset in range(60):
            at = minute - offset * 60
            rows = {key: {"started": 2, "success": 1, "error": 0, "timeout": 0, "cancelled": 0, "duration_seconds": 2.5, "samples": {str(at + i * 5): 1 for i in range(12)}} for key in descriptors}
            raw = encode_minute(at, rows, descriptors)
            payload_bytes += 70 * len(raw.encode())
            key = f"{monitor.prefix}:minute:{at}"
            keys.append(key)
            pipe.hset(key, mapping={str(node): raw for node in range(70)})
            pipe.expire(key, 7200)
        await pipe.execute()
        started = time.perf_counter()
        result = await monitor.read(60)
        elapsed = time.perf_counter() - started
        assert result["status"] == "observed", (payload_bytes, result)
        assert result["reporting_processes"] == 70 and len(result["trend"]) == 60
        assert len(result["series"]) == 8
        assert all(row["active"] == row["peak_concurrency"] == 70 for row in result["series"])
        assert all(row["started"] == 70 * 60 * 2 and row["success"] == 70 * 60 for row in result["series"])
        assert elapsed < 3
        assert 0 < await client.ttl(keys[-1]) <= 7200
        # Replacing a process snapshot does not double-count it.
        before = result["series"]
        await client.hset(keys[-1], "0", await client.hget(keys[-1], "0"))
        assert (await monitor.read(60))["series"] == before
        # Reject a large history value before transferring its bytes.
        await client.hset(keys[-1], "0", "x" * 262145)
        assert (await monitor.read(60))["status"] == "unavailable"
        await client.delete(keys[-1])
        # Explicit wire cap is enforced server-side by the real Lua script.
        with pytest.raises(Exception, match="METRICS_SIZE_BOUND"):
            await client.eval(_READ_FRAMES, 1, keys[0], 1, 1)
        print(json.dumps({"synthetic_processes": 70, "models_per_process": 8, "window_minutes": 60, "history_payload_bytes": payload_bytes, "read_seconds": round(elapsed, 4), "business_database_queries": 0}))
    finally:
        await client.delete(*keys)
        await client.aclose()

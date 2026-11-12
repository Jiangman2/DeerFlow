"""Optional Redis monitoring pipeline, isolated from business SQL and model I/O."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from contextlib import suppress

from deerflow.observability.model_metrics import metrics

_FIELDS = ("started", "success", "error", "timeout", "cancelled", "duration_seconds")


def encode_minute(minute, rows, series):
    """Compact bounded counters, retaining twelve aligned 5-second samples."""
    return json.dumps(
        {
            "version": 2,
            "minute": minute,
            "series": {key: {k: series[key][k] for k in ("model", "kind", "operation")} for key in rows},
            "rows": {key: [*(row[f] for f in _FIELDS), [row["samples"].get(str(minute + slot * 5)) for slot in range(12)]] for key, row in rows.items()},
        },
        separators=(",", ":"),
    )


def decode_minute(frame):
    if frame.get("version") != 2:
        raise ValueError("Unknown metrics frame version")
    minute = frame["minute"]
    frame["rows"] = {key: {**dict(zip(_FIELDS, row[:6], strict=True)), "samples": {str(minute + i * 5): value for i, value in enumerate(row[6]) if value is not None}} for key, row in frame["rows"].items()}
    return frame


# Bound values inside Redis before returning them over the network. A local
# JSON size check alone is too late when a long window spans many replicas.
_READ_FRAMES = """
local live_count = tonumber(ARGV[1])
local remaining = tonumber(ARGV[2])
local result = {}
for i, key in ipairs(KEYS) do
  if i <= live_count then
    local size = redis.call('STRLEN', key)
    if size > 524288 or size > remaining then return redis.error_reply('METRICS_SIZE_BOUND') end
    if size > 0 then
      remaining = remaining - size
      table.insert(result, 'L' .. redis.call('GET', key))
    end
  else
    if redis.call('HLEN', key) > 128 then return redis.error_reply('METRICS_PROCESS_BOUND') end
    for _, field in ipairs(redis.call('HKEYS', key)) do
      local size = redis.call('HSTRLEN', key, field)
      if size > 262144 or size > remaining then return redis.error_reply('METRICS_SIZE_BOUND') end
      remaining = remaining - size
      table.insert(result, 'H' .. redis.call('HGET', key, field))
    end
  end
end
return result
"""


def decode_and_aggregate(values, *, now, minutes):
    live, history = [], []
    for raw in values:
        value = json.loads(raw[1:])
        (live if raw[0] == "L" else history).append(value if raw[0] == "L" else decode_minute(value))
    fresh = [f for f in live if 0 <= now - f.get("at", 0) <= 20]
    result = aggregate_frames(fresh, history, now=now, minutes=minutes)
    jobs = {}
    for frame in fresh:
        for key, value in frame.get("oa_jobs", {}).items():
            if not 0 <= now - value["at"] <= 900:
                continue
            previous = jobs.get(key)
            # A terminal receipt always dominates an older running observation
            # from a different worker, even with minor node clock skew.
            terminal = value["status"] in {"succeeded", "failed"}
            if previous is None or terminal or (previous["status"] not in {"succeeded", "failed"} and value["at"] > previous["at"]):
                jobs[key] = value
    return {
        **result,
        "status": "observed" if fresh else "no_fresh_samples",
        "reporting_processes": len(fresh),
        "coverage_complete": False,
        "dropped_observations": sum(f.get("dropped", 0) for f in fresh),
        "oldest_sample_at": min((f["at"] for f in fresh), default=None),
        "oa_jobs": {
            "status": "observed" if jobs else "no_observations",
            "running": sum(v["status"] in {"launching", "running"} for v in jobs.values()),
            "queued": sum(v["status"] == "queued" for v in jobs.values()),
            "freshness_seconds": 900,
        },
    }


def aggregate_frames(live: list[dict], frames: list[dict], *, now: float, minutes: int) -> dict:
    series: dict[str, dict] = {}
    buckets: dict[int, dict] = {}
    samples: dict[tuple[int, str], float] = {}
    start = int(now // 60) * 60 - (minutes - 1) * 60
    for frame in live:
        if now - frame.get("at", 0) > 20:
            continue
        for key, value in frame.get("series", {}).items():
            row = series.setdefault(key, {**value, "active": 0, **dict.fromkeys(_FIELDS, 0)})
            row["active"] += value["active"]
    for frame in frames:
        minute = frame["minute"]
        if minute < start or minute > now:
            continue
        for key, value in frame.get("rows", {}).items():
            descriptor = frame["series"].get(key)
            if descriptor is None:
                continue
            row = series.setdefault(key, {**descriptor, "active": 0, **dict.fromkeys(_FIELDS, 0)})
            bucket = buckets.setdefault(minute, {}).setdefault(key, dict.fromkeys(_FIELDS, 0))
            for field in _FIELDS:
                row[field] += value[field]
                bucket[field] += value[field]
            for stamp, count in value["samples"].items():
                sample_key = (int(stamp), key)
                samples[sample_key] = samples.get(sample_key, 0) + count
    peaks: dict[str, float] = {}
    minute_peaks: dict[int, dict[str, float]] = {}
    for (stamp, key), value in samples.items():
        peaks[key] = max(peaks.get(key, 0), value)
        values = minute_peaks.setdefault(stamp // 60 * 60, {})
        values[key] = max(values.get(key, 0), value)
    for key, row in series.items():
        row["peak_concurrency"] = peaks.get(key)
        completed = sum(row[field] for field in ("success", "error", "timeout", "cancelled"))
        row["average_seconds"] = row["duration_seconds"] / completed if completed else None
        row["key"] = key
    trend = []
    for minute in range(start, int(now // 60) * 60 + 1, 60):
        # Missing buckets stay missing; do not fabricate a zero before installation.
        rows = buckets.get(minute)
        trend.append({"at": minute, "series": rows, "concurrency": minute_peaks.get(minute)})
    return {"series": sorted(series.values(), key=lambda x: (x["kind"], x["model"], x["operation"])), "trend": trend}


class ModelMonitor:
    def __init__(self):
        self.client = None
        self.task = None
        self.process = uuid.uuid4().hex
        self.prefix = ""
        self.last_export = None

    async def start(self):
        url = os.getenv("DEER_FLOW_MODEL_METRICS_REDIS_URL") or os.getenv("DEER_FLOW_DASHBOARD_REDIS_URL") or os.getenv("DEER_FLOW_STREAM_BRIDGE_REDIS_URL")
        namespace = os.getenv("DEER_FLOW_MODEL_METRICS_NAMESPACE", "")
        if os.getenv("DEER_FLOW_MODEL_METRICS_ENABLED", "").lower() != "true" or not url or not namespace or len(namespace) > 64:
            return
        try:
            from redis.asyncio import Redis

            self.client = Redis.from_url(url, decode_responses=True, max_connections=4, socket_connect_timeout=0.3, socket_timeout=0.5)
        except (ImportError, ValueError):
            return
        import hashlib

        self.prefix = "deerflow:model-metrics:v1:" + hashlib.sha256(namespace.encode()).hexdigest()[:16]
        metrics.enabled = True
        self.task = asyncio.create_task(self._publish_loop())

    async def export(self):
        from app.gateway.oa_policy_flow_metrics import job_snapshot

        frame = metrics.snapshot()
        pipe = self.client.pipeline(transaction=False)
        pipe.set(f"{self.prefix}:live:{self.process}", json.dumps({**{key: frame[key] for key in ("at", "series", "dropped")}, "oa_jobs": job_snapshot()}, separators=(",", ":")), ex=30)
        pipe.zadd(f"{self.prefix}:nodes", {self.process: frame["at"]})
        pipe.zremrangebyscore(f"{self.prefix}:nodes", "-inf", frame["at"] - 30)
        pipe.expire(f"{self.prefix}:nodes", 120)
        for minute, rows in frame["minutes"].items():
            key = f"{self.prefix}:minute:{minute}"
            # HSET replaces this process snapshot; retry cannot double the counts.
            pipe.hset(key, self.process, encode_minute(int(minute), rows, frame["series"]))
            pipe.expire(key, 7200)
        await pipe.execute()
        self.last_export = frame["at"]

    async def _publish_loop(self):
        while True:
            try:
                await asyncio.wait_for(self.export(), 2)
            except Exception:
                pass  # Telemetry failures never interrupt or retry a model request.
            await asyncio.sleep(5 - time.time() % 5)

    async def close(self):
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            with suppress(Exception):
                await asyncio.wait_for(self.export(), 2)
        metrics.enabled = False
        if self.client:
            with suppress(Exception):
                await self.client.aclose()

    async def read(self, minutes=15):
        if minutes not in (15, 60):
            raise ValueError("Unsupported monitoring window")
        base = {
            "generated_at": time.time(),
            "window_minutes": minutes,
            "sample_seconds": 5,
            "scope": "instrumented_gateway_invocations",
            "database_queries": 0,
            "coverage": ["factory_text_models", "platform_image_requests", "platform_video_submit_and_poll", "ppt_gateway_upstream_attempts", "oa_policy_flow_http_requests"],
            "excluded": ["external_services_without_instrumentation", "provider_internal_retries", "gpu_utilization", "unobserved_remote_video_jobs"],
        }
        if self.client is None:
            return {**base, "status": "not_configured", "series": [], "trend": [], "reporting_processes": 0}
        now = time.time()
        try:
            async with asyncio.timeout(3):
                nodes = await self.client.zrangebyscore(f"{self.prefix}:nodes", now - 20, "+inf", start=0, num=129)
                if len(nodes) > 128:
                    raise ValueError("Monitoring process bound exceeded")
                keys = [f"{self.prefix}:live:{node}" for node in nodes]
                start = int(now // 60) * 60 - (minutes - 1) * 60
                for minute in range(start, int(now // 60) * 60 + 1, 60):
                    keys.append(f"{self.prefix}:minute:{minute}")
                values = await self.client.eval(_READ_FRAMES, len(keys), *keys, len(nodes), 8 * 1024 * 1024)
                result = await asyncio.to_thread(decode_and_aggregate, values, now=now, minutes=minutes)
            return {**base, **result}
        except Exception:
            return {**base, "status": "unavailable", "series": [], "trend": [], "reporting_processes": 0}


model_monitor = ModelMonitor()

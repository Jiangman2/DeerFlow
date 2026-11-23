"""Read-only SSE transport: disconnects never cancel or relaunch a durable job."""

import asyncio
import hashlib
import json
import logging
import time
from time import monotonic

from deerflow.config.paths import get_paths
from deerflow.utils.file_io import run_file_io

from .rules import RULE_VERSION

logger = logging.getLogger(__name__)
MAX_REPORT_BYTES = 2 * 1024 * 1024


def event(name, payload):
    # JSON escapes newlines in report text; blank lines delimit complete SSE
    # events, never individual network chunks. No private model/tool trace.
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def read_report(payload):
    path = get_paths().thread_dir(payload["thread_id"], user_id=payload["owner_user_id"]) / "procurement-delivery" / "report.md"
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("Invalid report snapshot path")
    with path.open("rb") as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if not raw or len(raw) > MAX_REPORT_BYTES or hashlib.sha256(raw).hexdigest() != payload.get("report_sha256"):
        raise ValueError("Report snapshot integrity failure")
    return raw.decode("utf-8-sig")


async def stream_result(service, initial, *, poll_seconds=2, heartbeat_seconds=15):
    identity = {"request_id": initial["request_id"], "job_id": initial["id"]}
    yield event("accepted", {**identity, "status": initial["state"]})
    # Use monotonic time for this connection, not to extend the stored task
    # deadline. The grace period lets another worker persist a timeout result.
    remaining = max(0, initial["payload"]["deadline"] - time.time())
    stop_at = monotonic() + min(remaining + 120, service.config.job_timeout + 120)
    next_heartbeat = monotonic() + heartbeat_seconds
    try:
        while True:
            async with asyncio.timeout(10):
                job = await service.repo.get(initial["id"])
            if not job or job["request_id"] != initial["request_id"]:
                raise ValueError("Task identity changed")
            p = job["payload"]
            if (p.get("owner_user_id"), p.get("assistant_id"), p.get("delivery_mode"), p.get("rule_version")) != (service.config.owner_user_id, service.config.assistant_id, "stream", RULE_VERSION):
                raise ValueError("Task binding changed")
            if job["state"] == "result_ready":
                status = p.get("result_status")
                if status not in {"succeeded", "failed"}:
                    raise ValueError("Invalid result status")
                result = {**identity, "status": status, "message": p.get("message", "")}
                if status == "succeeded":
                    markdown = await run_file_io(read_report, p)
                    encoded = markdown.encode("utf-8")
                    result.update(report_format="markdown", report_markdown=markdown, report_bytes=len(encoded), report_sha256=hashlib.sha256(encoded).hexdigest())
                yield event("result", result)
                return
            if job["state"] not in {"queued", "launching", "running"}:
                yield event("error", {**identity, "code": "TASK_UNAVAILABLE", "retryable": False, "message": "任务状态异常，请联系管理员。"})
                return
            if monotonic() >= stop_at:
                yield event("error", {**identity, "code": "WAIT_TIMEOUT", "retryable": True, "message": "本次连接等待结束；可使用相同编号和材料重连，不重复创建任务。"})
                return
            if monotonic() >= next_heartbeat:
                yield event("heartbeat", {**identity, "status": job["state"]})
                next_heartbeat = monotonic() + heartbeat_seconds
            await asyncio.sleep(poll_seconds)
    except asyncio.CancelledError:
        # Starlette closes the generator on disconnect. Worker ownership and
        # the SQL queue remain independent of the transport connection.
        raise
    except Exception as exc:
        logger.error("Procurement stream %s failed (%s)", initial["id"], type(exc).__name__)
        yield event("error", {**identity, "code": "RESULT_UNAVAILABLE", "retryable": True, "message": "结果暂不可读，可用相同编号和材料重连；持续失败请联系管理员。"})

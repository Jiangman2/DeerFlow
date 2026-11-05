"""OA endpoint telemetry: never buffers bodies, alters auth, or submits jobs."""
import hashlib
import re
import threading
import time
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from datetime import UTC, datetime

from deerflow.observability.model_metrics import metrics, outcome_for

_ROOT = '/api/v1/integrations/oa/policy-flow/jobs'
_JOB = re.compile(r'/[A-Za-z0-9-]{1,64}')
_RESULT = re.compile(r'/[A-Za-z0-9-]{1,64}/(?:result|results/[A-Za-z0-9-]{1,64})')

# Workers already read these states while holding their SQL lease. Reuse those
# observations; dashboard requests never query the OA table or poll its API.
_current_job = ContextVar("oa_metrics_job", default=None)
_job_lock = threading.Lock()
_jobs = {}
_JOB_TTL = 900  # Covers the configured 300-second maximum worker poll interval.


def _observe_job(job):
    if not metrics.enabled:
        return
    status = job.get("status")
    if status not in {"queued", "launching", "running", "succeeded", "failed"}:
        return
    key = hashlib.sha256(str(job["id"]).encode()).hexdigest()[:24]
    now = time.time()
    with _job_lock:
        for old in list(_jobs):
            if now - _jobs[old]["at"] > _JOB_TTL:
                del _jobs[old]
        if key not in _jobs and len(_jobs) >= 1024:
            return
        _jobs[key] = {"at": now, "status": status}


def job_snapshot():
    now = time.time()
    with _job_lock:
        return {key: dict(value) for key, value in _jobs.items() if 0 <= now - value["at"] <= _JOB_TTL}


def job_created(job):
    with suppress(Exception):
        _observe_job(job)
        metrics.record_job_event(created=True)


@contextmanager
def observe_claimed_job(job):
    token = _current_job.set(dict(job))
    with suppress(Exception):
        _observe_job(job)
    try:
        yield
    finally:
        _current_job.reset(token)


def job_patch_committed(job_id, values):
    """Only called after the existing lease-checked database update succeeds."""
    with suppress(Exception):
        job = _current_job.get()
        if job is None or job["id"] != job_id:
            return
        before = job.get("status")
        job.update(values)
        _observe_job(job)
        if before not in {"succeeded", "failed"} and job["status"] in {"succeeded", "failed"}:
            start, end = job.get("created_at"), job.get("completed_at")
            if isinstance(start, str):
                start = datetime.fromisoformat(start.replace("Z", "+00:00"))
            if isinstance(end, str):
                end = datetime.fromisoformat(end.replace("Z", "+00:00"))
            duration = 0
            if isinstance(start, datetime) and isinstance(end, datetime):
                start = start.replace(tzinfo=UTC) if start.tzinfo is None else start
                end = end.replace(tzinfo=UTC) if end.tzinfo is None else end
                duration = (end - start).total_seconds()
            metrics.record_job_event(outcome="success" if job["status"] == "succeeded" else "error", duration=duration)


def operation_for(method, path):
    if path.rstrip('/') == _ROOT and method == 'POST':
        return 'submit'
    suffix = path[len(_ROOT):]
    if method == 'GET' and _JOB.fullmatch(suffix):
        return 'poll'
    if method == 'GET' and _RESULT.fullmatch(suffix):
        return 'download'
    return None


class OAPolicyFlowMetricsMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get('path', '')
        if scope['type'] != 'http' or not metrics.enabled or not path.startswith(_ROOT):
            return await self.app(scope, receive, send)
        operation = operation_for(scope.get('method'), path)
        if operation is None:
            return await self.app(scope, receive, send)
        token = None
        with suppress(Exception):
            token = metrics.start('oa-policy-flow', 'integration', operation)
        status, complete, outcome = 500, False, 'error'

        async def measured_send(message):
            nonlocal status, complete
            await send(message)
            if message['type'] == 'http.response.start':
                status = message['status']
            elif message['type'] == 'http.response.body' and not message.get('more_body', False):
                complete = True

        try:
            await self.app(scope, receive, measured_send)
            outcome = ('success' if status < 400 else 'error') if complete else 'cancelled'
        except BaseException as exc:
            outcome = outcome_for(exc)
            raise
        finally:
            with suppress(Exception):
                metrics.finish(token, outcome)

"""Machine-authenticated Group OA policy-to-JointJS integration API."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.gateway.file_security import UploadSecurityError
from app.gateway.oa_policy_flow_service import (
    allowed_oa_document_extensions,
    parse_strict_json_text,
    resolve_oa_result_path,
)
from app.gateway.routers.uploads import (
    _get_upload_limits,
    _make_uploaded_paths_sandbox_readable,
    _write_upload_file_with_limits,
)
from deerflow.config.app_config import get_app_config
from deerflow.config.paths import get_paths
from deerflow.uploads.manager import ensure_uploads_dir, normalize_filename
from deerflow.utils.file_io import run_file_io

router = APIRouter(
    prefix="/api/v1/integrations/oa/policy-flow/jobs",
    tags=["oa-policy-flow"],
)

_KEY_FIELDS_MAX_BYTES = 64 * 1024
_IDEMPOTENCY_KEY_RE = re.compile(r"^[\x21-\x7e]{8,200}$")


class OAPolicyFlowJobResponse(BaseModel):
    job_id: str
    request_id: str
    status: str
    callback_status: str
    created_at: str
    updated_at: str
    completed_at: str | None = None
    delivered_at: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    callback_error: str | None = None
    result_filename: str | None = None
    result_sha256: str | None = None


def _job_response(job: dict[str, Any]) -> OAPolicyFlowJobResponse:
    return OAPolicyFlowJobResponse(
        job_id=job["id"],
        request_id=job["request_id"],
        status=job["status"],
        callback_status=job["callback_status"],
        created_at=str(job["created_at"]),
        updated_at=str(job["updated_at"]),
        completed_at=str(job["completed_at"]) if job.get("completed_at") else None,
        delivered_at=str(job["delivered_at"]) if job.get("delivered_at") else None,
        error_code=job.get("last_error_code"),
        error_detail=job.get("last_error_detail"),
        callback_error=job.get("last_callback_error"),
        result_filename=job.get("output_filename"),
        result_sha256=job.get("output_sha256"),
    )


def _get_runtime(request: Request) -> tuple[Any, Any, Any]:
    service = getattr(request.app.state, "oa_policy_flow_service", None)
    repository = getattr(request.app.state, "oa_policy_flow_job_repo", None)
    config = getattr(request.app.state, "oa_policy_flow_config", None)
    if service is None or repository is None or config is None or not config.enabled or not service.is_running:
        raise HTTPException(status_code=503, detail="OA policy-flow integration is unavailable")
    return service, repository, config


def _authenticate(request: Request, config: Any) -> None:
    authorization = request.headers.get("authorization", "")
    supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    if not supplied:
        supplied = request.headers.get("x-oa-integration-key", "").strip()
    if not supplied or not hmac.compare_digest(supplied, config.ingest_key):
        raise HTTPException(
            status_code=401,
            detail="Invalid OA integration credential",
            headers={"WWW-Authenticate": "Bearer"},
        )


def _parse_key_fields(raw: str) -> tuple[dict[str, Any], str]:
    encoded = raw.encode("utf-8")
    if len(encoded) > _KEY_FIELDS_MAX_BYTES:
        raise HTTPException(status_code=413, detail="key_fields exceeds 64 KiB")
    try:
        value = parse_strict_json_text(raw)
    except (ValueError, RecursionError) as exc:
        raise HTTPException(status_code=422, detail="key_fields must be strict JSON without duplicate keys") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail="key_fields must be a JSON object")
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return value, canonical


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cleanup_submission(thread_id: str, owner_user_id: str) -> None:
    get_paths().delete_thread_dir(thread_id, user_id=owner_user_id)


@router.post("", response_model=OAPolicyFlowJobResponse, status_code=status.HTTP_202_ACCEPTED)
async def submit_policy_flow_job(
    request: Request,
    document: Annotated[UploadFile, File(...)],
    request_id: Annotated[str, Form(min_length=1, max_length=128)],
    key_fields: Annotated[str, Form(min_length=2)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> OAPolicyFlowJobResponse:
    service, repository, config = _get_runtime(request)
    _authenticate(request, config)
    request_id = request_id.strip()
    if not request_id or any(ord(char) < 32 for char in request_id):
        raise HTTPException(status_code=422, detail="request_id contains invalid characters")
    if idempotency_key is None or not _IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key):
        raise HTTPException(status_code=422, detail="Idempotency-Key must contain 8-200 visible ASCII characters")
    parsed_fields, canonical_fields = _parse_key_fields(key_fields)

    if not document.filename:
        raise HTTPException(status_code=422, detail="document filename is required")
    try:
        filename = normalize_filename(document.filename)
    except (ValueError, UploadSecurityError) as exc:
        raise HTTPException(status_code=422, detail="document filename is invalid") from exc
    if Path(filename).suffix.lower() not in allowed_oa_document_extensions():
        allowed = ", ".join(sorted(allowed_oa_document_extensions()))
        raise HTTPException(status_code=415, detail=f"Unsupported policy document type; allowed: {allowed}")

    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
    job_id = str(uuid.uuid4())
    thread_id = f"oa-{job_id}"
    written_path: Path | None = None
    try:
        uploads_dir = await run_file_io(
            ensure_uploads_dir,
            thread_id,
            user_id=config.owner_user_id,
        )
        limits = _get_upload_limits(get_app_config())
        raw_path, file_size, _total_size = await _write_upload_file_with_limits(
            document,
            uploads_dir=uploads_dir,
            display_filename=filename,
            max_single_file_size=min(limits.max_file_size, config.max_document_bytes),
            max_total_size=min(limits.max_file_size, config.max_document_bytes),
            total_size=0,
        )
        written_path = Path(raw_path)
        if file_size == 0:
            raise HTTPException(status_code=422, detail="document must not be empty")
        await run_file_io(_make_uploaded_paths_sandbox_readable, [written_path])
        document_sha = await run_file_io(_hash_file, written_path)
        fingerprint = hashlib.sha256(
            f"{request_id}\n{canonical_fields}\n{document_sha}".encode()
        ).hexdigest()
        now = datetime.now(UTC)
        job, created = await repository.create_or_get(
            {
                "id": job_id,
                "idempotency_key_hash": key_hash,
                "request_id": request_id,
                "request_fingerprint": fingerprint,
                "owner_user_id": config.owner_user_id,
                "assistant_id": config.assistant_id,
                "thread_id": thread_id,
                "status": "queued",
                "callback_status": "not_ready",
                "document_filename": filename,
                "key_fields": parsed_fields,
                "process_attempts": 0,
                "callback_attempts": 0,
                "next_attempt_at": now,
                "created_at": now,
                "updated_at": now,
            }
        )
        if not created:
            await run_file_io(_cleanup_submission, thread_id, config.owner_user_id)
            if job["request_fingerprint"] != fingerprint:
                raise HTTPException(status_code=409, detail="Idempotency-Key was already used with a different request")
            return _job_response(job)
    except HTTPException:
        if written_path is not None and get_paths().thread_dir(thread_id, user_id=config.owner_user_id).exists():
            await run_file_io(_cleanup_submission, thread_id, config.owner_user_id)
        raise
    except Exception as exc:
        if get_paths().thread_dir(thread_id, user_id=config.owner_user_id).exists():
            await run_file_io(_cleanup_submission, thread_id, config.owner_user_id)
        raise HTTPException(status_code=500, detail="Failed to persist OA policy-flow job") from exc

    service.notify()
    return _job_response(job)


@router.get("/{job_id}", response_model=OAPolicyFlowJobResponse)
async def get_policy_flow_job(job_id: str, request: Request) -> OAPolicyFlowJobResponse:
    _service, repository, config = _get_runtime(request)
    _authenticate(request, config)
    job = await repository.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="OA policy-flow job not found")
    return _job_response(job)


@router.get("/{job_id}/result")
async def download_policy_flow_result(job_id: str, request: Request) -> FileResponse:
    _service, repository, config = _get_runtime(request)
    _authenticate(request, config)
    job = await repository.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="OA policy-flow job not found")
    if job["status"] != "succeeded":
        raise HTTPException(status_code=409, detail=f"Result is not available; current status is {job['status']}")
    try:
        path = resolve_oa_result_path(job, max_result_bytes=config.max_result_bytes)
    except Exception as exc:
        raise HTTPException(status_code=410, detail="Accepted result is no longer available") from exc
    return FileResponse(
        path,
        media_type="application/json",
        filename=job.get("output_filename") or path.name,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "ETag": f'"{job["output_sha256"]}"',
        },
    )

"""One public submit operation. Authentication precedes multipart parsing."""

import asyncio
import hashlib
import hmac
import json
import re
import shutil
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from deerflow.uploads.manager import ensure_uploads_dir, normalize_filename
from deerflow.utils.file_io import run_file_io

from .config import ENDPOINT
from .files import digest
from .rules import RULE_VERSION

router = APIRouter(tags=["procurement-review"])
ALLOWED = {".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg"}


@router.post(
    ENDPOINT,
    status_code=202,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["request_id", "files"],
                        "additionalProperties": False,
                        "properties": {"request_id": {"type": "string", "maxLength": 128}, "files": {"type": "array", "items": {"type": "string", "format": "binary"}, "maxItems": 12}},
                    }
                }
            },
        }
    },
)
async def submit(request: Request):
    service = getattr(request.app.state, "procurement_review_service", None)
    if service is None:
        raise HTTPException(404, "Integration is disabled")
    config = service.config
    expected = "Bearer " + config.submit_key
    if not hmac.compare_digest(request.headers.get("authorization", "").encode(), expected.encode()):
        raise HTTPException(401, "Invalid integration credential")
    if not service.is_running:
        raise HTTPException(503, "Review worker unavailable")
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data;"):
        raise HTTPException(415, "Use multipart/form-data")

    # Bound the stream itself, including files spooled by Starlette before the
    # endpoint can inspect them. Content-Length alone is not authoritative.
    received = 0

    async def receive():
        nonlocal received
        message = await request.receive()
        received += len(message.get("body", b""))
        if received > config.max_total_bytes + 1024 * 1024:
            raise MultiPartException("Multipart request too large")
        return message

    limited = Request(request.scope, receive=receive)
    job_id = str(uuid.uuid4())
    thread_id = "procurement-" + job_id
    uploads = None
    retain = False
    try:
        async with asyncio.timeout(120):
            async with limited.form(max_files=config.max_files, max_fields=1, max_part_size=256) as form:
                if set(form.keys()) != {"request_id", "files"} or len(form.getlist("request_id")) != 1:
                    raise HTTPException(422, "Only request_id and files are accepted")
                request_id = form["request_id"]
                if not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", request_id):
                    raise HTTPException(422, "request_id must be 1-128 ASCII letters, digits, _, ., : or -")
                parts = form.getlist("files")
                if not parts or any(not isinstance(f, UploadFile) for f in parts):
                    raise HTTPException(422, "files must contain attachments")
                from app.gateway.routers.uploads import _write_upload_file_with_limits

                uploads = await run_file_io(ensure_uploads_dir, thread_id, user_id=config.owner_user_id)
                files, seen, total = [], set(), 0
                for i, part in enumerate(parts):
                    try:
                        name = normalize_filename(part.filename or "")
                    except ValueError as exc:
                        raise HTTPException(422, "Invalid filename") from exc
                    suffix = Path(name).suffix.lower()
                    if suffix not in ALLOWED or any(ord(c) < 32 for c in name):
                        raise HTTPException(415, "Only PDF, DOC, DOCX, PNG and JPEG materials are supported")
                    if name.casefold() in seen:
                        raise HTTPException(422, "Duplicate filenames are not accepted")
                    seen.add(name.casefold())
                    # Sanitization can rewrite a PDF. Bind idempotency to the
                    # submitted bytes, independently of sanitized file hashes.
                    raw_hash = hashlib.sha256()
                    raw_size = 0
                    while chunk := await part.read(1024 * 1024):
                        raw_size += len(chunk)
                        if raw_size > config.max_file_bytes:
                            raise HTTPException(413, "File too large")
                        raw_hash.update(chunk)
                    await part.seek(0)
                    stored = f"material-{i:02d}{suffix}"
                    path, size, total, _security, _rollback = await _write_upload_file_with_limits(
                        part,
                        uploads_dir=uploads,
                        display_filename=stored,
                        max_single_file_size=config.max_file_bytes,
                        max_total_size=config.max_total_bytes,
                        total_size=total,
                    )
                    if not size:
                        raise HTTPException(422, "Empty attachment")
                    files.append(
                        {"id": f"material-{i:02d}", "name": name, "stored": stored, "kind": "submitted", "path": "/mnt/user-data/uploads/" + stored, "sha256": await run_file_io(digest, path), "submitted_sha256": raw_hash.hexdigest()}
                    )
                identity = sorted((f["name"], f["submitted_sha256"]) for f in files)
                fingerprint = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
                payload = {
                    "thread_id": thread_id,
                    "owner_user_id": config.owner_user_id,
                    "assistant_id": config.assistant_id,
                    "files": files,
                    "reference_sha": service.references["manifest_sha256"],
                    "reference_version": service.references["version"],
                    "callback_url": config.callback_url,
                    "deadline": time.time() + config.job_timeout,
                    "rule_version": RULE_VERSION,
                }
                # Once persistence begins an ambiguous commit must not delete
                # files that may already belong to an accepted durable task.
                retain = True
                job, created = await service.repo.create(job_id=job_id, request_id=request_id, fingerprint=fingerprint, payload=payload)
                if not created:
                    retain = False
                    if job["fingerprint"] != fingerprint:
                        raise HTTPException(409, "request_id already used with different materials; use a new request_id")
                status = job["payload"].get("result_status") or {"queued": "queued", "launching": "queued", "running": "running"}.get(job["state"], "failed")
                return {"request_id": job["request_id"], "job_id": job["id"], "status": status}
    except TimeoutError as exc:
        raise HTTPException(408, "Submission deadline exceeded") from exc
    finally:
        if uploads is not None and not retain:
            # This UUID-owned directory was created only by this submission.
            await run_file_io(shutil.rmtree, uploads)

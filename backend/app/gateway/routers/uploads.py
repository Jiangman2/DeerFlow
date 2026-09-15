"""Upload router for handling file uploads."""

import logging
import os
import shlex
import shutil
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, BinaryIO, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.gateway.authz import require_permission
from app.gateway.deps import get_config
from app.gateway.file_security import (
    UploadSecurityError,
    UploadSecurityResult,
    validate_staged_upload,
    validate_upload_extension,
)
from app.gateway.upload_document_parser import (
    UploadDocumentParseError,
    conversation_document_parser_endpoint,
    parse_conversation_document,
)
from deerflow.config.app_config import AppConfig
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.sandbox.sandbox_provider import SandboxProvider, get_sandbox_provider
from deerflow.uploads.manager import (
    UPLOAD_STAGING_PREFIX,
    UPLOAD_STAGING_SUFFIX,
    PathTraversalError,
    UnsafeUploadPathError,
    claim_unique_filename,
    delete_file_safe,
    enrich_file_listing,
    ensure_uploads_dir,
    get_uploads_dir,
    list_files_in_dir,
    normalize_filename,
    upload_artifact_url,
    upload_virtual_path,
    validate_upload_destination,
)
from deerflow.utils.file_conversion import CONVERTIBLE_EXTENSIONS, convert_file_to_markdown
from deerflow.utils.file_io import run_file_io

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads/{thread_id}/uploads", tags=["uploads"])

UPLOAD_CHUNK_SIZE = 8192
DEFAULT_MAX_FILES = 10
DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024
DEFAULT_MAX_TOTAL_SIZE = 100 * 1024 * 1024
ENTERPRISE_PARSER_EXTENSIONS = {".doc", ".docx", ".pdf", ".ppt", ".pptx"}
PRESENTATION_TEMPLATE_EXTENSIONS = {".ppt", ".pptx"}
UploadPurpose = Literal["attachment", "presentation_template"]


@dataclass(slots=True)
class _UploadTempFile:
    file_path: Path
    temp_path: Path
    handle: BinaryIO


@dataclass(slots=True)
class _UploadRollbackSnapshot:
    destination: Path
    backup_path: Path


class UploadedFileInfo(BaseModel):
    """Uploaded file metadata exposed by upload and list APIs."""

    filename: str
    size: int
    path: str
    virtual_path: str
    artifact_url: str
    extension: str | None = None
    modified: float | None = None
    original_filename: str | None = None
    markdown_file: str | None = None
    markdown_path: str | None = None
    markdown_virtual_path: str | None = None
    markdown_artifact_url: str | None = None
    processing_warning: str | None = None
    security_sanitized: bool = False
    security_findings: list[str] = Field(default_factory=list)


class UploadResponse(BaseModel):
    """Response model for file upload."""

    success: bool
    files: list[UploadedFileInfo]
    message: str
    skipped_files: list[str] = Field(default_factory=list)


class UploadListResponse(BaseModel):
    """Response model for uploaded file listing."""

    files: list[UploadedFileInfo]
    count: int


class UploadLimits(BaseModel):
    """Application-level upload limits exposed to clients."""

    max_files: int
    max_file_size: int
    max_total_size: int


def _make_file_sandbox_writable(file_path: os.PathLike[str] | str) -> None:
    """Ensure uploaded files remain writable when mounted into non-local sandboxes.

    In AIO sandbox mode, the gateway writes the authoritative host-side file
    first, then the sandbox runtime may rewrite the same mounted path. Granting
    world-writable access here prevents permission mismatches between the
    gateway user and the sandbox runtime user.
    """
    file_stat = os.lstat(file_path)
    if stat.S_ISLNK(file_stat.st_mode):
        logger.warning("Skipping sandbox chmod for symlinked upload path: %s", file_path)
        return

    writable_mode = stat.S_IMODE(file_stat.st_mode) | stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH | stat.S_IRGRP | stat.S_IROTH
    chmod_kwargs = {"follow_symlinks": False} if os.chmod in os.supports_follow_symlinks else {}
    os.chmod(file_path, writable_mode, **chmod_kwargs)


def _make_file_sandbox_readable(file_path: os.PathLike[str] | str) -> None:
    """Ensure uploaded files are readable by the sandbox process.

    For Docker sandboxes (AIO), the gateway writes files as root with 0o600
    permissions, then bind-mounts the host directory into the container. The
    sandbox process inside the container runs as a non-root user and cannot
    read those files without group/other read bits. This function adds
    ``S_IRGRP | S_IROTH`` so the sandbox can read the uploaded content.
    """
    file_stat = os.lstat(file_path)
    if stat.S_ISLNK(file_stat.st_mode):
        logger.warning("Skipping sandbox chmod for symlinked upload path: %s", file_path)
        return

    readable_mode = stat.S_IMODE(file_stat.st_mode) | stat.S_IRGRP | stat.S_IROTH
    chmod_kwargs = {"follow_symlinks": False} if os.chmod in os.supports_follow_symlinks else {}
    os.chmod(file_path, readable_mode, **chmod_kwargs)


def _uses_thread_data_mounts(sandbox_provider: SandboxProvider) -> bool:
    return bool(getattr(sandbox_provider, "uses_thread_data_mounts", False))


def _get_uploads_config_value(app_config: AppConfig, key: str, default: object) -> object:
    """Read a value from the uploads config, supporting dict and attribute access."""
    uploads_cfg = getattr(app_config, "uploads", None)
    if isinstance(uploads_cfg, dict):
        return uploads_cfg.get(key, default)
    return getattr(uploads_cfg, key, default)


def _get_upload_limit(app_config: AppConfig, key: str, default: int, *, legacy_key: str | None = None) -> int:
    try:
        value = _get_uploads_config_value(app_config, key, None)
        if value is None and legacy_key is not None:
            value = _get_uploads_config_value(app_config, legacy_key, None)
        if value is None:
            value = default
        limit = int(value)
        if limit <= 0:
            raise ValueError
        return limit
    except Exception:
        logger.warning("Invalid uploads.%s value; falling back to %d", key, default)
        return default


def _get_upload_limits(app_config: AppConfig) -> UploadLimits:
    return UploadLimits(
        max_files=_get_upload_limit(app_config, "max_files", DEFAULT_MAX_FILES, legacy_key="max_file_count"),
        max_file_size=_get_upload_limit(app_config, "max_file_size", DEFAULT_MAX_FILE_SIZE, legacy_key="max_single_file_size"),
        max_total_size=_get_upload_limit(app_config, "max_total_size", DEFAULT_MAX_TOTAL_SIZE),
    )


def _cleanup_uploaded_paths(paths: list[os.PathLike[str] | str]) -> None:
    for path in reversed(paths):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except Exception:
            logger.warning("Failed to clean up upload path after rejected request: %s", path, exc_info=True)


def _snapshot_existing_upload(destination: Path) -> _UploadRollbackSnapshot | None:
    """Preserve an existing regular upload until the request is accepted."""
    try:
        file_stat = os.lstat(destination)
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink > 1:
        raise UnsafeUploadPathError(
            f"Upload destination is not a safe regular file: {destination.name}",
        )

    backup_fd, backup_path_str = tempfile.mkstemp(
        prefix=UPLOAD_STAGING_PREFIX,
        suffix=UPLOAD_STAGING_SUFFIX,
        dir=destination.parent,
    )
    os.close(backup_fd)
    backup_path = Path(backup_path_str)
    try:
        shutil.copy2(destination, backup_path)
    except Exception:
        backup_path.unlink(missing_ok=True)
        raise
    return _UploadRollbackSnapshot(destination=destination, backup_path=backup_path)


def _discard_upload_snapshots(snapshots: list[_UploadRollbackSnapshot]) -> None:
    for snapshot in snapshots:
        try:
            snapshot.backup_path.unlink(missing_ok=True)
        except Exception:
            logger.warning(
                "Failed to remove accepted upload rollback snapshot: %s",
                snapshot.backup_path,
                exc_info=True,
            )


def _rollback_upload_transaction(
    written_paths: list[os.PathLike[str] | str],
    snapshots: list[_UploadRollbackSnapshot],
) -> None:
    """Remove new request files and restore every replaced pre-existing file."""
    _cleanup_uploaded_paths(written_paths)
    for snapshot in reversed(snapshots):
        try:
            os.replace(snapshot.backup_path, snapshot.destination)
        except Exception:
            logger.error(
                "Failed to restore upload replaced by rejected request: %s",
                snapshot.destination,
                exc_info=True,
            )
    _discard_upload_snapshots(snapshots)


def _prepare_upload_destination(uploads_dir: os.PathLike[str] | str, display_filename: str) -> _UploadTempFile:
    uploads_dir_path = Path(uploads_dir)
    file_path = validate_upload_destination(uploads_dir_path, display_filename)
    temp_fd, temp_path_str = tempfile.mkstemp(prefix=UPLOAD_STAGING_PREFIX, suffix=UPLOAD_STAGING_SUFFIX, dir=uploads_dir_path)
    temp_path = Path(temp_path_str)
    try:
        handle = os.fdopen(temp_fd, "wb")
    except Exception:
        try:
            os.close(temp_fd)
        except OSError:
            pass
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise
    return _UploadTempFile(file_path=file_path, temp_path=temp_path, handle=handle)


def _write_upload_chunk(upload_temp: _UploadTempFile, chunk: bytes) -> None:
    upload_temp.handle.write(chunk)


def _abort_upload_temp(upload_temp: _UploadTempFile) -> None:
    try:
        upload_temp.handle.close()
    finally:
        try:
            os.unlink(upload_temp.temp_path)
        except FileNotFoundError:
            pass


def _commit_upload_temp(upload_temp: _UploadTempFile) -> None:
    upload_temp.handle.close()
    try:
        os.replace(upload_temp.temp_path, upload_temp.file_path)
    except Exception:
        try:
            os.unlink(upload_temp.temp_path)
        except FileNotFoundError:
            pass
        raise


def _close_upload_temp(upload_temp: _UploadTempFile) -> None:
    upload_temp.handle.close()


def _make_uploaded_paths_sandbox_readable(paths: list[os.PathLike[str] | str]) -> None:
    for file_path in paths:
        _make_file_sandbox_readable(file_path)


def _write_parsed_markdown(
    source_path: Path,
    markdown: str,
    *,
    output_path: Path | None = None,
) -> Path:
    """Atomically write a non-empty parser companion next to its source."""
    target_path = output_path or source_path.with_suffix(".md")
    normalized_markdown = markdown.strip() if isinstance(markdown, str) else ""
    if not normalized_markdown:
        # Remove only this conversion's reserved destination. A same-stem
        # Markdown upload may legitimately own the default companion name.
        target_path.unlink(missing_ok=True)
        raise UploadDocumentParseError("Document parser returned no readable text")
    temp_fd, temp_path_str = tempfile.mkstemp(
        prefix=UPLOAD_STAGING_PREFIX,
        suffix=UPLOAD_STAGING_SUFFIX,
        dir=target_path.parent,
    )
    temp_path = Path(temp_path_str)
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(f"# {source_path.name} 解析结果\n\n> 本文件由企业文档解析服务生成，原始文档保持不变。\n\n")
            handle.write(normalized_markdown)
            handle.write("\n")
        os.replace(temp_path, target_path)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise
    return target_path


def _markdown_companion_has_content(path: Path) -> bool:
    """Reject stale, missing, or whitespace-only local conversion output."""
    try:
        valid = path.is_file() and bool(path.read_text(encoding="utf-8").strip())
    except (OSError, UnicodeError):
        valid = False
    if not valid:
        path.unlink(missing_ok=True)
    return valid


def _sync_upload_to_sandbox(sandbox, file_path: os.PathLike[str] | str, virtual_path: str) -> None:
    _make_file_sandbox_writable(file_path)
    sandbox.update_file(virtual_path, Path(file_path).read_bytes())


async def _convert_legacy_doc_via_sandbox(
    *,
    sandbox_provider: SandboxProvider,
    sandbox,
    thread_id: str,
    user_id: str,
    source_path: Path,
    output_path: Path | None = None,
) -> Path | None:
    """Recover legacy ``.doc`` text through the task sandbox's LibreOffice.

    MarkItDown handles DOCX but intentionally has no converter for the old OLE
    binary Word format.  The AIO sandbox already carries LibreOffice for office
    artifact rendering, so use a request-unique workspace directory to convert
    DOC to DOCX and then feed that temporary DOCX back through DeerFlow's normal
    ``convert_file_to_markdown()`` implementation.

    This remains a last-resort path after both the enterprise Adapter and direct
    local conversion have failed.  It does not broaden the Gateway image or
    retain the temporary office document in the user's workspace.
    """
    if source_path.suffix.lower() != ".doc":
        return None

    active_sandbox = sandbox
    workdir = f"/mnt/user-data/workspace/.deerflow-doc-fallback-{uuid.uuid4().hex}"
    source_virtual_path = f"{workdir}/source.doc"
    converted_virtual_path = f"{workdir}/source.docx"
    temp_docx_path: Path | None = None
    temp_md_path: Path | None = None

    try:
        if active_sandbox is None:
            sandbox_id = await sandbox_provider.acquire_async(thread_id, user_id=user_id)
            active_sandbox = sandbox_provider.get(sandbox_id)
        if active_sandbox is None:
            logger.warning("Legacy DOC fallback could not acquire a sandbox for %s", source_path.name)
            return None

        await run_file_io(
            active_sandbox.execute_command,
            f"set -eu; mkdir -p {shlex.quote(workdir)}",
            timeout=30,
        )
        source_bytes = await run_file_io(source_path.read_bytes)
        await run_file_io(active_sandbox.update_file, source_virtual_path, source_bytes)
        command = (
            "set -eu; "
            "office_bin=; "
            "for candidate in libreoffice soffice; do "
            'if command -v "$candidate" >/dev/null 2>&1; then office_bin=$candidate; break; fi; '
            "done; "
            'test -n "$office_bin"; '
            f'"$office_bin" --headless --convert-to docx --outdir {shlex.quote(workdir)} '
            f"{shlex.quote(source_virtual_path)} >/dev/null; "
            f"test -s {shlex.quote(converted_virtual_path)}"
        )
        await run_file_io(active_sandbox.execute_command, command, timeout=120)
        docx_bytes = await run_file_io(active_sandbox.download_file, converted_virtual_path)
        if not docx_bytes:
            return None

        temp_fd, temp_docx_name = tempfile.mkstemp(
            prefix=UPLOAD_STAGING_PREFIX,
            suffix=".docx",
            dir=source_path.parent,
        )
        os.close(temp_fd)
        temp_docx_path = Path(temp_docx_name)
        await run_file_io(temp_docx_path.write_bytes, docx_bytes)

        temp_md_path = await convert_file_to_markdown(temp_docx_path)
        if temp_md_path is None or not temp_md_path.is_file():
            return None

        markdown = await run_file_io(temp_md_path.read_text, encoding="utf-8")
        target_path = await run_file_io(
            _write_parsed_markdown,
            source_path,
            markdown,
            output_path=output_path,
        )
        logger.info(
            "Converted legacy DOC %s to markdown through sandbox LibreOffice",
            source_path.name,
        )
        return target_path
    except Exception as exc:
        logger.warning(
            "Legacy DOC sandbox fallback failed for %s: %s",
            source_path.name,
            type(exc).__name__,
        )
        return None
    finally:
        for temp_path in (temp_md_path, temp_docx_path):
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    logger.debug("Could not remove temporary legacy DOC file %s", temp_path)
        if active_sandbox is not None:
            try:
                await run_file_io(
                    active_sandbox.execute_command,
                    f"rm -rf -- {shlex.quote(workdir)}",
                    timeout=30,
                )
            except Exception:
                logger.debug("Could not remove sandbox legacy DOC workspace %s", workdir)


def _list_uploaded_files_for_thread(thread_id: str, user_id: str) -> dict:
    uploads_dir = get_uploads_dir(thread_id, user_id=user_id)
    result = list_files_in_dir(uploads_dir)
    enrich_file_listing(result, thread_id)

    sandbox_uploads = get_paths().sandbox_uploads_dir(thread_id, user_id=user_id)
    for f in result["files"]:
        f["path"] = str(sandbox_uploads / f["filename"])
    return result


def _delete_uploaded_file_for_thread(thread_id: str, filename: str, user_id: str) -> dict:
    uploads_dir = get_uploads_dir(thread_id, user_id=user_id)
    return delete_file_safe(uploads_dir, filename, convertible_extensions=CONVERTIBLE_EXTENSIONS)


async def _write_upload_file_with_limits(
    file: UploadFile,
    *,
    uploads_dir: os.PathLike[str] | str,
    display_filename: str,
    max_single_file_size: int,
    max_total_size: int,
    total_size: int,
) -> tuple[
    os.PathLike[str] | str,
    int,
    int,
    UploadSecurityResult,
    _UploadRollbackSnapshot | None,
]:
    file_size = 0
    upload_temp: _UploadTempFile | None = None
    rollback_snapshot: _UploadRollbackSnapshot | None = None
    try:
        upload_temp = await run_file_io(_prepare_upload_destination, uploads_dir, display_filename)
        while chunk := await file.read(UPLOAD_CHUNK_SIZE):
            file_size += len(chunk)
            total_size += len(chunk)
            if file_size > max_single_file_size:
                raise HTTPException(status_code=413, detail=f"File too large: {display_filename}")
            if total_size > max_total_size:
                raise HTTPException(status_code=413, detail="Total upload size too large")
            await run_file_io(_write_upload_chunk, upload_temp, chunk)

        # Validate staged bytes before they can replace an existing upload.
        # Closing first guarantees the scanner sees every buffered byte.
        await run_file_io(_close_upload_temp, upload_temp)
        try:
            security_result = await run_file_io(
                validate_staged_upload,
                upload_temp.temp_path,
                display_filename,
            )
        except UploadSecurityError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        validated_size = await run_file_io(lambda: upload_temp.temp_path.stat().st_size)
        validated_total_size = total_size - file_size + validated_size
        if validated_size > max_single_file_size:
            raise HTTPException(
                status_code=413,
                detail=f"Safely sanitized file is too large: {display_filename}",
            )
        if validated_total_size > max_total_size:
            raise HTTPException(status_code=413, detail="Total upload size too large after PDF sanitization")
        rollback_snapshot = await run_file_io(
            _snapshot_existing_upload,
            upload_temp.file_path,
        )
        try:
            await run_file_io(_commit_upload_temp, upload_temp)
        except Exception:
            if rollback_snapshot is not None:
                await run_file_io(
                    _rollback_upload_transaction,
                    [],
                    [rollback_snapshot],
                )
                rollback_snapshot = None
            raise
        file_path = upload_temp.file_path
        file_size = validated_size
        total_size = validated_total_size
        upload_temp = None
    except Exception:
        if upload_temp is not None:
            await run_file_io(_abort_upload_temp, upload_temp)
        raise
    return file_path, file_size, total_size, security_result, rollback_snapshot


def _auto_convert_documents_enabled(app_config: AppConfig) -> bool:
    """Return whether automatic host-side document conversion is enabled.

    The secure default is disabled unless an operator explicitly opts in via
    uploads.auto_convert_documents in config.yaml. Enterprise deployments may
    set DEERFLOW_UPLOAD_DOCUMENT_FALLBACK_ENABLED explicitly so the release
    manifest remains the source of truth for parser-failure fallback behavior.
    """
    environment_override = os.getenv("DEERFLOW_UPLOAD_DOCUMENT_FALLBACK_ENABLED")
    if environment_override is not None:
        return environment_override.strip().lower() in {"1", "true", "yes", "on"}
    try:
        raw = _get_uploads_config_value(app_config, "auto_convert_documents", False)
        if isinstance(raw, str):
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        return bool(raw)
    except Exception:
        return False


def _require_parsed_text_enabled() -> bool:
    """Return whether enterprise uploads must yield readable Markdown.

    This is deliberately opt-in so the default Gateway behavior remains
    compatible with upstream DeerFlow: conversion failure retains the original
    upload and reports a warning instead of rejecting the request.
    """
    return os.getenv(
        "DEERFLOW_UPLOAD_DOCUMENT_REQUIRE_PARSED_TEXT",
        "false",
    ).strip().lower() in {"1", "true", "yes", "on"}


def _local_fallback_after_enterprise_enabled() -> bool:
    """Allow the legacy Gateway fallback after the Adapter exhausts its chain.

    Enterprise can disable this duplicate, unbounded parse path because the
    Adapter already owns AnyDoc, DocReader, LibreOffice, and MinerU fallback.
    The code default stays enabled for upstream-compatible deployments.
    """

    return os.getenv(
        "DEERFLOW_UPLOAD_DOCUMENT_LOCAL_FALLBACK_AFTER_ENTERPRISE_ENABLED",
        "true",
    ).strip().lower() in {"1", "true", "yes", "on"}


@router.post("", response_model=UploadResponse)
@require_permission("threads", "write", owner_check=True, require_existing=False)
async def upload_files(
    thread_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    upload_purpose: Annotated[UploadPurpose, Form()] = "attachment",
    config: AppConfig = Depends(get_config),
) -> UploadResponse:
    """Upload multiple files to a thread's uploads directory."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    limits = _get_upload_limits(config)
    if len(files) > limits.max_files:
        raise HTTPException(status_code=413, detail=f"Too many files: maximum is {limits.max_files}")

    try:
        effective_user_id = get_effective_user_id()
        uploads_dir = await run_file_io(ensure_uploads_dir, thread_id, user_id=effective_user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    sandbox_uploads = uploads_dir
    uploaded_files = []
    written_paths = []
    rollback_snapshots: list[_UploadRollbackSnapshot] = []
    sandbox_sync_targets = []
    skipped_files = []
    total_size = 0
    processing_warning_count = 0
    sanitized_file_count = 0
    # Track filenames within this request so duplicate form parts do not
    # silently truncate each other. Existing uploads keep the historical
    # overwrite behavior for a single replacement upload.
    seen_filenames: set[str] = set()

    sandbox_provider = get_sandbox_provider()
    sync_to_sandbox = not _uses_thread_data_mounts(sandbox_provider)
    sandbox = None
    if sync_to_sandbox:
        sandbox_id = await sandbox_provider.acquire_async(thread_id, user_id=effective_user_id)
        sandbox = sandbox_provider.get(sandbox_id)
        if sandbox is None:
            raise HTTPException(status_code=500, detail="Failed to acquire sandbox")
    auto_convert_documents = _auto_convert_documents_enabled(config)
    document_parser_endpoint = conversation_document_parser_endpoint()
    require_parsed_text = _require_parsed_text_enabled()

    for file in files:
        if not file.filename:
            continue

        try:
            original_filename = normalize_filename(file.filename)
            safe_filename = claim_unique_filename(original_filename, seen_filenames)
            validate_upload_extension(safe_filename)
        except UploadSecurityError as exc:
            await run_file_io(
                _rollback_upload_transaction,
                written_paths,
                rollback_snapshots,
            )
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        except ValueError:
            logger.warning(f"Skipping file with unsafe filename: {file.filename!r}")
            continue

        try:
            file_path, file_size, total_size, security_result, rollback_snapshot = await _write_upload_file_with_limits(
                file,
                uploads_dir=uploads_dir,
                display_filename=safe_filename,
                max_single_file_size=limits.max_file_size,
                max_total_size=limits.max_total_size,
                total_size=total_size,
            )
            written_paths.append(file_path)
            if rollback_snapshot is not None:
                rollback_snapshots.append(rollback_snapshot)

            virtual_path = upload_virtual_path(safe_filename)

            if sync_to_sandbox:
                sandbox_sync_targets.append((file_path, virtual_path))

            file_info = {
                "filename": safe_filename,
                "size": file_size,
                "path": str(sandbox_uploads / safe_filename),
                "virtual_path": virtual_path,
                "artifact_url": upload_artifact_url(thread_id, safe_filename),
                "security_sanitized": security_result.sanitized,
                "security_findings": list(security_result.findings),
            }
            if security_result.sanitized:
                sanitized_file_count += 1
            if safe_filename != original_filename:
                file_info["original_filename"] = original_filename

            logger.info(f"Saved file: {safe_filename} ({file_size} bytes) to {file_info['path']}")

            file_ext = file_path.suffix.lower()
            is_presentation_template = upload_purpose == "presentation_template" and file_ext in PRESENTATION_TEMPLATE_EXTENSIONS
            parsed_document = False
            parse_attempted = False
            parser_warning: str | None = None
            enterprise_parse_enabled = file_ext in ENTERPRISE_PARSER_EXTENSIONS and bool(document_parser_endpoint)
            local_parse_enabled = auto_convert_documents and file_ext in CONVERTIBLE_EXTENSIONS
            markdown_output: Path | None = None
            if not is_presentation_template and (enterprise_parse_enabled or local_parse_enabled):
                # Backport upstream upload collision handling: reserve the
                # derived companion name before writing so a same-request
                # source such as report.md cannot be silently overwritten by
                # report.docx parsing (or vice versa).
                provisional_md_name = Path(safe_filename).with_suffix(".md").name
                unique_md_name = claim_unique_filename(
                    provisional_md_name,
                    seen_filenames,
                )
                try:
                    markdown_output = validate_upload_destination(
                        Path(file_path).parent,
                        unique_md_name,
                    )
                except UnsafeUploadPathError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                markdown_snapshot = await run_file_io(
                    _snapshot_existing_upload,
                    markdown_output,
                )
                if markdown_snapshot is not None:
                    rollback_snapshots.append(markdown_snapshot)
                # Replacing an upload with the same filename must not retain a
                # companion generated from the previous file version.
                await run_file_io(markdown_output.unlink, missing_ok=True)
            if is_presentation_template:
                logger.info(
                    "Keeping presentation template %s as an original upload; document parsing is intentionally skipped",
                    safe_filename,
                )
            elif file_ext in ENTERPRISE_PARSER_EXTENSIONS and document_parser_endpoint:
                parse_attempted = True
                try:
                    document_content = await run_file_io(Path(file_path).read_bytes)
                    parsed_markdown = await parse_conversation_document(
                        endpoint=document_parser_endpoint,
                        filename=safe_filename,
                        content=document_content,
                    )
                    md_path = await run_file_io(
                        _write_parsed_markdown,
                        Path(file_path),
                        parsed_markdown,
                        output_path=markdown_output,
                    )
                    written_paths.append(md_path)
                    md_virtual_path = upload_virtual_path(md_path.name)
                    if sync_to_sandbox:
                        sandbox_sync_targets.append((md_path, md_virtual_path))
                    file_info["markdown_file"] = md_path.name
                    file_info["markdown_path"] = str(sandbox_uploads / md_path.name)
                    file_info["markdown_virtual_path"] = md_virtual_path
                    file_info["markdown_artifact_url"] = upload_artifact_url(
                        thread_id,
                        md_path.name,
                    )
                    parsed_document = True
                except UploadDocumentParseError as exc:
                    parser_warning = f"企业文档解析服务失败：{exc}。"
                    logger.warning(
                        "Conversation document parser failed for %s: %s",
                        safe_filename,
                        exc,
                    )

            local_fallback_allowed = not enterprise_parse_enabled or _local_fallback_after_enterprise_enabled()
            if auto_convert_documents and local_fallback_allowed and not parsed_document and not is_presentation_template and file_ext in CONVERTIBLE_EXTENSIONS:
                parse_attempted = True
                md_path = await convert_file_to_markdown(
                    file_path,
                    output_path=markdown_output,
                )
                if md_path is None and file_ext == ".doc":
                    md_path = await _convert_legacy_doc_via_sandbox(
                        sandbox_provider=sandbox_provider,
                        sandbox=sandbox,
                        thread_id=thread_id,
                        user_id=effective_user_id,
                        source_path=file_path,
                        output_path=markdown_output,
                    )
                if md_path and not await run_file_io(
                    _markdown_companion_has_content,
                    Path(md_path),
                ):
                    logger.error(
                        "Local document conversion produced an empty companion for %s",
                        safe_filename,
                    )
                    md_path = None
                if md_path:
                    written_paths.append(md_path)
                    md_virtual_path = upload_virtual_path(md_path.name)

                    if sync_to_sandbox:
                        sandbox_sync_targets.append((md_path, md_virtual_path))

                    file_info["markdown_file"] = md_path.name
                    file_info["markdown_path"] = str(sandbox_uploads / md_path.name)
                    file_info["markdown_virtual_path"] = md_virtual_path
                    file_info["markdown_artifact_url"] = upload_artifact_url(thread_id, md_path.name)
                    parsed_document = True

            if require_parsed_text and parse_attempted and not parsed_document:
                reason = parser_warning or "文档转换没有生成可读取的文本。"
                raise HTTPException(
                    status_code=503,
                    detail=(f"文档“{safe_filename}”解析失败，任务未启动。{reason} 请稍后重试；系统不会提交空白解析结果。"),
                )

            if parser_warning is not None and not parsed_document:
                processing_warning_count += 1
                file_info["processing_warning"] = parser_warning

            uploaded_files.append(file_info)

        except HTTPException as e:
            await run_file_io(
                _rollback_upload_transaction,
                written_paths,
                rollback_snapshots,
            )
            raise e
        except UnsafeUploadPathError as e:
            logger.warning("Skipping upload with unsafe destination %s: %s", file.filename, e)
            skipped_files.append(safe_filename)
            continue
        except Exception as e:
            logger.error(f"Failed to upload {file.filename}: {e}")
            await run_file_io(
                _rollback_upload_transaction,
                written_paths,
                rollback_snapshots,
            )
            raise HTTPException(status_code=500, detail=f"Failed to upload {file.filename}: {str(e)}")

    # Uploaded files are created with 0o600 permissions (owner read/write only).
    # In Docker sandbox deployments the gateway writes as root but the sandbox
    # process runs as a non-root user (typically UID 1000).  Without group/other
    # read bits the sandbox cannot access the files — whether the uploads
    # directory is bind-mounted into the container or synced via
    # sandbox.update_file.  Always add group/other read bits so every sandbox
    # configuration can read the uploaded content.
    try:
        await run_file_io(_make_uploaded_paths_sandbox_readable, written_paths)

        if sync_to_sandbox:
            for file_path, virtual_path in sandbox_sync_targets:
                await run_file_io(_sync_upload_to_sandbox, sandbox, file_path, virtual_path)
    except Exception:
        await run_file_io(
            _rollback_upload_transaction,
            written_paths,
            rollback_snapshots,
        )
        raise

    await run_file_io(_discard_upload_snapshots, rollback_snapshots)

    message = f"Successfully uploaded {len(uploaded_files)} file(s)"
    if skipped_files:
        message += f"; skipped {len(skipped_files)} unsafe file(s)"
    if processing_warning_count:
        message += f"; {processing_warning_count} document(s) were kept without enterprise parsed text"
    if sanitized_file_count:
        message += f"; safely sanitized {sanitized_file_count} PDF file(s)"

    return UploadResponse(
        success=not skipped_files,
        files=uploaded_files,
        message=message,
        skipped_files=skipped_files,
    )


@router.get("/limits", response_model=UploadLimits)
@require_permission("threads", "read", owner_check=True)
async def get_upload_limits(
    thread_id: str,
    request: Request,
    config: AppConfig = Depends(get_config),
) -> UploadLimits:
    """Return upload limits used by the gateway for this thread."""
    return _get_upload_limits(config)


@router.get("/list", response_model=UploadListResponse)
@require_permission("threads", "read", owner_check=True)
async def list_uploaded_files(thread_id: str, request: Request) -> UploadListResponse:
    """List all files in a thread's uploads directory."""
    try:
        result = await run_file_io(_list_uploaded_files_for_thread, thread_id, get_effective_user_id())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return UploadListResponse(**result)


@router.delete("/{filename}")
@require_permission("threads", "delete", owner_check=True, require_existing=True)
async def delete_uploaded_file(thread_id: str, filename: str, request: Request) -> dict:
    """Delete a file from a thread's uploads directory."""
    try:
        return await run_file_io(_delete_uploaded_file_for_thread, thread_id, filename, get_effective_user_id())
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    except PathTraversalError:
        raise HTTPException(status_code=400, detail="Invalid path")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Failed to delete {filename}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete {filename}: {str(e)}")

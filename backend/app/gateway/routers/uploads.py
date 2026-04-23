"""Upload router for handling file uploads."""

import logging
import os
import sqlite3
from pathlib import Path

from fastapi import APIRouter, File, Header, HTTPException, UploadFile
from pydantic import BaseModel

from app.gateway.proxy_auth import verify_proxy_headers
from deerflow.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from deerflow.sandbox.sandbox_provider import get_sandbox_provider
from deerflow.utils.file_conversion import CONVERTIBLE_EXTENSIONS, convert_file_to_markdown

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/threads/{thread_id}/uploads", tags=["uploads"])


class UploadResponse(BaseModel):
    """Response model for file upload."""

    success: bool
    files: list[dict[str, str]]
    message: str


def _db_path() -> Path:
    base = os.getenv("DEER_FLOW_HOME") or os.path.join(Path.home(), ".deer-flow")
    return Path(base).expanduser() / "deerflow.db"


def _ensure_db() -> None:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p.as_posix())
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS threads (thread_id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, title TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
        conn.commit()
    finally:
        conn.close()


def _is_owned(thread_id: str, owner_user_id: str) -> bool:
    _ensure_db()
    conn = sqlite3.connect(_db_path().as_posix())
    try:
        cur = conn.cursor()
        cur.execute("SELECT owner_user_id FROM threads WHERE thread_id = ?", (thread_id,))
        row = cur.fetchone()
        return row is not None and row[0] == owner_user_id
    finally:
        conn.close()


def _require_owner(thread_id: str, x_df_user: str, x_df_ts: str, x_df_sig: str) -> None:
    verify_proxy_headers(x_df_user, x_df_ts, x_df_sig)
    if not _is_owned(thread_id, x_df_user):
        raise HTTPException(status_code=403, detail="forbidden")


def get_uploads_dir(thread_id: str, user_id: str) -> Path:
    """Get the uploads directory for a thread.

    Args:
        thread_id: The thread ID.

    Returns:
        Path to the uploads directory.
    """
    paths = get_paths()
    paths.ensure_thread_dirs(thread_id, user_id=user_id)
    base_dir = paths.sandbox_uploads_dir(thread_id, user_id=user_id)
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir


def _should_sync_uploads_to_sandbox(sandbox_provider: object, sandbox_id: str) -> bool:
    if sandbox_id == "local":
        return False

    backend = getattr(sandbox_provider, "_backend", None)
    return backend is None or backend.__class__.__name__ != "LocalContainerBackend"


def _is_local_container_backend(sandbox_provider: object) -> bool:
    backend = getattr(sandbox_provider, "_backend", None)
    return backend is not None and backend.__class__.__name__ == "LocalContainerBackend"


@router.post("", response_model=UploadResponse)
async def upload_files(
    thread_id: str,
    files: list[UploadFile] = File(...),
    x_df_user: str = Header(default=""),
    x_df_ts: str = Header(default=""),
    x_df_sig: str = Header(default=""),
) -> UploadResponse:
    """Upload multiple files to a thread's uploads directory.

    For PDF, PPT, Excel, and Word files, they will be converted to markdown using markitdown.
    All files (original and converted) are saved to /mnt/user-data/uploads.

    Args:
        thread_id: The thread ID to upload files to.
        files: List of files to upload.

    Returns:
        Upload response with success status and file information.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    _require_owner(thread_id, x_df_user, x_df_ts, x_df_sig)

    uploads_dir = get_uploads_dir(thread_id, x_df_user)
    paths = get_paths()
    uploaded_files = []

    sandbox_provider = get_sandbox_provider()
    sandbox_id = sandbox_provider.acquire(thread_id)
    sandbox = sandbox_provider.get(sandbox_id)
    sync_to_sandbox = _should_sync_uploads_to_sandbox(sandbox_provider, sandbox_id)

    for file in files:
        if not file.filename:
            continue

        try:
            # Normalize filename to prevent path traversal
            safe_filename = Path(file.filename).name
            if not safe_filename or safe_filename in {".", ".."} or "/" in safe_filename or "\\" in safe_filename:
                logger.warning(f"Skipping file with unsafe filename: {file.filename!r}")
                continue

            content = await file.read()
            file_path = uploads_dir / safe_filename
            file_path.write_bytes(content)

            # Build relative path from backend root
            relative_path = str(paths.sandbox_uploads_dir(thread_id, user_id=x_df_user) / safe_filename)
            virtual_path = f"{VIRTUAL_PATH_PREFIX}/uploads/{safe_filename}"

            if sync_to_sandbox:
                if sandbox is None:
                    raise HTTPException(status_code=500, detail="Sandbox not available")
                try:
                    sandbox.update_file(virtual_path, content)
                except Exception as e:
                    if _is_local_container_backend(sandbox_provider) and "Permission denied" in str(e):
                        pass
                    else:
                        raise

            file_info = {
                "filename": safe_filename,
                "size": str(len(content)),
                "path": relative_path,  # Actual filesystem path (relative to backend/)
                "virtual_path": virtual_path,  # Path for Agent in sandbox
                "artifact_url": f"/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/{safe_filename}",  # HTTP URL
            }

            logger.info(f"Saved file: {safe_filename} ({len(content)} bytes) to {relative_path}")

            # Check if file should be converted to markdown
            file_ext = file_path.suffix.lower()
            if file_ext in CONVERTIBLE_EXTENSIONS:
                md_path = await convert_file_to_markdown(file_path)
                if md_path:
                    md_relative_path = str(paths.sandbox_uploads_dir(thread_id, user_id=x_df_user) / md_path.name)
                    md_virtual_path = f"{VIRTUAL_PATH_PREFIX}/uploads/{md_path.name}"

                    if sync_to_sandbox:
                        if sandbox is None:
                            raise HTTPException(status_code=500, detail="Sandbox not available")
                        try:
                            sandbox.update_file(md_virtual_path, md_path.read_bytes())
                        except Exception as e:
                            if _is_local_container_backend(sandbox_provider) and "Permission denied" in str(e):
                                pass
                            else:
                                raise

                    file_info["markdown_file"] = md_path.name
                    file_info["markdown_path"] = md_relative_path
                    file_info["markdown_virtual_path"] = md_virtual_path
                    file_info["markdown_artifact_url"] = f"/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/{md_path.name}"

            uploaded_files.append(file_info)

        except Exception as e:
            logger.error(f"Failed to upload {file.filename}: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to upload {file.filename}: {str(e)}")

    return UploadResponse(
        success=True,
        files=uploaded_files,
        message=f"Successfully uploaded {len(uploaded_files)} file(s)",
    )


@router.get("/list", response_model=dict)
async def list_uploaded_files(
    thread_id: str,
    x_df_user: str = Header(default=""),
    x_df_ts: str = Header(default=""),
    x_df_sig: str = Header(default=""),
) -> dict:
    """List all files in a thread's uploads directory.

    Args:
        thread_id: The thread ID to list files for.

    Returns:
        Dictionary containing list of files with their metadata.
    """
    _require_owner(thread_id, x_df_user, x_df_ts, x_df_sig)
    uploads_dir = get_uploads_dir(thread_id, x_df_user)

    if not uploads_dir.exists():
        return {"files": [], "count": 0}

    files = []
    for file_path in sorted(uploads_dir.iterdir()):
        if file_path.is_file():
            stat = file_path.stat()
            relative_path = str(get_paths().sandbox_uploads_dir(thread_id, user_id=x_df_user) / file_path.name)
            files.append(
                {
                    "filename": file_path.name,
                    "size": stat.st_size,
                    "path": relative_path,  # Actual filesystem path
                    "virtual_path": f"{VIRTUAL_PATH_PREFIX}/uploads/{file_path.name}",  # Path for Agent in sandbox
                    "artifact_url": f"/api/threads/{thread_id}/artifacts/mnt/user-data/uploads/{file_path.name}",  # HTTP URL
                    "extension": file_path.suffix,
                    "modified": stat.st_mtime,
                }
            )

    return {"files": files, "count": len(files)}


@router.delete("/{filename}")
async def delete_uploaded_file(
    thread_id: str,
    filename: str,
    x_df_user: str = Header(default=""),
    x_df_ts: str = Header(default=""),
    x_df_sig: str = Header(default=""),
) -> dict:
    """Delete a file from a thread's uploads directory.

    Args:
        thread_id: The thread ID.
        filename: The filename to delete.

    Returns:
        Success message.
    """
    _require_owner(thread_id, x_df_user, x_df_ts, x_df_sig)
    uploads_dir = get_uploads_dir(thread_id, x_df_user)
    file_path = uploads_dir / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")

    # Security check: ensure the path is within the uploads directory
    try:
        file_path.resolve().relative_to(uploads_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        if file_path.suffix.lower() in CONVERTIBLE_EXTENSIONS:
            companion_markdown = file_path.with_suffix(".md")
            companion_markdown.unlink(missing_ok=True)
        file_path.unlink(missing_ok=True)
        logger.info(f"Deleted file: {filename}")
        return {"success": True, "message": f"Deleted {filename}"}
    except Exception as e:
        logger.error(f"Failed to delete {filename}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete {filename}: {str(e)}")

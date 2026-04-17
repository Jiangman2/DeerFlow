import logging
import os
import shutil
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from app.gateway.proxy_auth import verify_proxy_headers
from deerflow.config.paths import Paths, get_paths

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/threads", tags=["threads"])


class ThreadCreateRequest(BaseModel):
    thread_id: str


class ThreadCreateResponse(BaseModel):
    success: bool
    thread_id: str


class ThreadDeleteResponse(BaseModel):
    success: bool
    message: str


class ThreadUpdateRequest(BaseModel):
    title: str | None = None


def _db_path() -> Path:
    base = os.getenv("DEER_FLOW_HOME") or os.path.join(Path.home(), ".deer-flow")
    return Path(base).expanduser() / "deerflow.db"


def _ensure_db() -> None:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p.as_posix())
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS threads (thread_id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, title TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(threads)").fetchall()}
        if "title" not in cols:
            conn.execute("ALTER TABLE threads ADD COLUMN title TEXT")
        if "updated_at" not in cols:
            conn.execute("ALTER TABLE threads ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP")
        conn.commit()
    finally:
        conn.close()


def _sqlite_utc_to_iso_z(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().replace(" ", "T")
    if normalized.endswith("Z") or "+" in normalized or normalized.endswith("z"):
        return normalized
    if "T" in normalized:
        return f"{normalized}Z"
    return value


@router.post("", response_model=ThreadCreateResponse)
async def create_thread(
    req: ThreadCreateRequest,
    x_df_user: str = Header(default=""),
    x_df_ts: str = Header(default=""),
    x_df_sig: str = Header(default=""),
) -> ThreadCreateResponse:
    verify_proxy_headers(x_df_user, x_df_ts, x_df_sig)
    _ensure_db()
    conn = sqlite3.connect(_db_path().as_posix())
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO threads(thread_id, owner_user_id, updated_at) VALUES(?, ?, CURRENT_TIMESTAMP)",
            (req.thread_id, x_df_user),
        )
        conn.commit()
    finally:
        conn.close()
    get_paths().ensure_thread_dirs(req.thread_id, user_id=x_df_user)
    return ThreadCreateResponse(success=True, thread_id=req.thread_id)


@router.get("")
async def list_threads(
    x_df_user: str = Header(default=""),
    x_df_ts: str = Header(default=""),
    x_df_sig: str = Header(default=""),
    limit: int = Query(default=50, ge=0),
    offset: int = Query(default=0, ge=0),
    sortBy: str = Query(default="updated_at"),
    sortOrder: str = Query(default="desc"),
) -> list[dict]:
    verify_proxy_headers(x_df_user, x_df_ts, x_df_sig)
    _ensure_db()
    sort_col = "updated_at" if sortBy == "updated_at" else "created_at"
    sort_dir = "ASC" if sortOrder == "asc" else "DESC"
    conn = sqlite3.connect(_db_path().as_posix())
    try:
        cur = conn.cursor()
        cur.execute(
            f"SELECT thread_id, title, updated_at FROM threads WHERE owner_user_id = ? ORDER BY {sort_col} {sort_dir} LIMIT ? OFFSET ?",
            (x_df_user, limit, offset),
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {
            "thread_id": thread_id,
            "updated_at": _sqlite_utc_to_iso_z(updated_at),
            "values": {"title": title} if title else {},
        }
        for (thread_id, title, updated_at) in rows
    ]


@router.patch("/{thread_id}")
async def update_thread(
    thread_id: str,
    req: ThreadUpdateRequest,
    x_df_user: str = Header(default=""),
    x_df_ts: str = Header(default=""),
    x_df_sig: str = Header(default=""),
) -> dict:
    verify_proxy_headers(x_df_user, x_df_ts, x_df_sig)
    if req.title is None:
        raise HTTPException(status_code=422, detail="missing title")
    _ensure_db()
    conn = sqlite3.connect(_db_path().as_posix())
    try:
        cur = conn.cursor()
        cur.execute("SELECT owner_user_id FROM threads WHERE thread_id = ?", (thread_id,))
        row = cur.fetchone()
        if row is None or row[0] != x_df_user:
            raise HTTPException(status_code=403, detail="forbidden")
        cur.execute(
            "UPDATE threads SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE thread_id = ?",
            (req.title, thread_id),
        )
        conn.commit()
    finally:
        conn.close()
    return {"success": True}


def _delete_thread_data(thread_id: str, owner_user_id: str, paths: Paths | None = None) -> ThreadDeleteResponse:
    _ensure_db()
    conn = sqlite3.connect(_db_path().as_posix())
    try:
        cur = conn.cursor()
        cur.execute("SELECT owner_user_id FROM threads WHERE thread_id = ?", (thread_id,))
        row = cur.fetchone()
        if row is None:
            return ThreadDeleteResponse(success=True, message=f"Thread {thread_id} not found (already deleted).")
        if row[0] != owner_user_id:
            raise HTTPException(status_code=403, detail="forbidden")
        cur.execute("DELETE FROM threads WHERE thread_id = ?", (thread_id,))
        conn.commit()
    finally:
        conn.close()
    path_manager = paths or get_paths()
    try:
        thread_dir = path_manager.thread_dir(thread_id, user_id=owner_user_id)
        shutil.rmtree(thread_dir, ignore_errors=True)
        legacy_dir = path_manager.legacy_thread_dir(thread_id)
        shutil.rmtree(legacy_dir, ignore_errors=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to delete thread data for %s", thread_id)
        raise HTTPException(status_code=500, detail="Failed to delete local thread data.") from exc
    return ThreadDeleteResponse(success=True, message=f"Deleted local thread data for {thread_id}")


@router.delete("/{thread_id}", response_model=ThreadDeleteResponse)
async def delete_thread_data(
    thread_id: str,
    x_df_user: str = Header(default=""),
    x_df_ts: str = Header(default=""),
    x_df_sig: str = Header(default=""),
) -> ThreadDeleteResponse:
    verify_proxy_headers(x_df_user, x_df_ts, x_df_sig)
    return _delete_thread_data(thread_id, x_df_user)

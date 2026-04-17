import json
import os
import sqlite3
from pathlib import Path

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from app.gateway.proxy_auth import verify_proxy_headers

router = APIRouter(prefix="/api/langgraph", tags=["langgraph"])


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


def _ensure_thread_owner(thread_id: str, owner_user_id: str) -> None:
    _ensure_db()
    conn = sqlite3.connect(_db_path().as_posix())
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO threads(thread_id, owner_user_id, updated_at) VALUES(?, ?, CURRENT_TIMESTAMP)",
            (thread_id, owner_user_id),
        )
        conn.commit()
    finally:
        conn.close()


def _extract_thread_id(path: str) -> str | None:
    cleaned = path.lstrip("/")
    parts = [p for p in cleaned.split("/") if p]
    if len(parts) >= 2 and parts[0] == "threads":
        return parts[1]
    return None


def _filter_headers(headers: httpx.Headers) -> dict[str, str]:
    excluded = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "content-length",
        "content-encoding",
    }
    return {k: v for k, v in headers.items() if k.lower() not in excluded}


async def _aclose_httpx(response: httpx.Response, client: httpx.AsyncClient) -> None:
    try:
        await response.aclose()
    finally:
        await client.aclose()


async def _stream_bytes(response: httpx.Response):
    try:
        async for chunk in response.aiter_bytes():
            yield chunk
    except Exception:
        return


def _is_thread_not_found_response(status_code: int, body: bytes) -> bool:
    if status_code != 404:
        return False
    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    detail = payload.get("detail")
    if not isinstance(detail, str):
        return False
    return "Thread with ID" in detail and "not found" in detail


async def _ensure_upstream_thread(upstream: str, timeout: httpx.Timeout, thread_id: str) -> None:
    url = httpx.URL(upstream).join("threads")
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        await client.post(url, json={"thread_id": thread_id})


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy_langgraph(
    request: Request,
    path: str,
    x_df_user: str = Header(default=""),
    x_df_ts: str = Header(default=""),
    x_df_sig: str = Header(default=""),
) -> Response:
    verify_proxy_headers(x_df_user, x_df_ts, x_df_sig)

    thread_id = _extract_thread_id(path)
    if thread_id is not None:
        _ensure_db()
        conn = sqlite3.connect(_db_path().as_posix())
        try:
            cur = conn.cursor()
            cur.execute("SELECT owner_user_id FROM threads WHERE thread_id = ?", (thread_id,))
            row = cur.fetchone()
        finally:
            conn.close()
        if row is None:
            _ensure_thread_owner(thread_id, x_df_user)
        elif row[0] != x_df_user:
            raise HTTPException(status_code=403, detail="forbidden")

    upstream = os.getenv("LANGGRAPH_BASE_URL") or "http://127.0.0.1:2025"
    url = httpx.URL(upstream).join(path)
    if request.url.query:
        url = url.copy_with(query=request.url.query.encode("utf-8"))

    headers = {k: v for k, v in request.headers.items() if k.lower() not in {"host", "content-length"}}
    body = await request.body()

    if request.method.upper() == "POST":
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("application/json") and body:
            try:
                payload = json.loads(body)
                if isinstance(payload, dict):
                    cfg = payload.get("config")
                    if not isinstance(cfg, dict):
                        cfg = {}
                        payload["config"] = cfg
                    context = cfg.get("context")
                    if not isinstance(context, dict):
                        context = {}
                        cfg["context"] = context

                    configurable = cfg.get("configurable")
                    if isinstance(configurable, dict):
                        for k, v in configurable.items():
                            context.setdefault(k, v)
                    cfg.pop("configurable", None)

                    context.setdefault("user_id", x_df_user)
                    context.setdefault("x_df_user", x_df_user)
                    if thread_id is not None:
                        context.setdefault("thread_id", thread_id)
                    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            except Exception:
                pass

    timeout = httpx.Timeout(connect=10.0, read=None, write=None, pool=10.0)
    if request.method.upper() == "POST" and path.rstrip("/") == "threads":
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            r = await client.request(request.method, url, headers=headers, content=body)
            content_type = r.headers.get("content-type", "")
            if r.status_code < 300 and "application/json" in content_type:
                try:
                    payload = r.json()
                    created_id = payload.get("thread_id")
                    if isinstance(created_id, str) and created_id:
                        _ensure_thread_owner(created_id, x_df_user)
                except Exception:
                    pass
            return Response(content=r.content, status_code=r.status_code, headers=_filter_headers(r.headers))

    client = httpx.AsyncClient(timeout=timeout, trust_env=False)
    req = client.build_request(request.method, url, headers=headers, content=body)
    r = await client.send(req, stream=True)
    if thread_id is not None and r.status_code == 404:
        status_code = r.status_code
        resp_headers = _filter_headers(r.headers)
        data = await r.aread()
        await _aclose_httpx(r, client)
        if _is_thread_not_found_response(status_code, data):
            await _ensure_upstream_thread(upstream, timeout, thread_id)
            client2 = httpx.AsyncClient(timeout=timeout, trust_env=False)
            req2 = client2.build_request(request.method, url, headers=headers, content=body)
            r2 = await client2.send(req2, stream=True)
            resp_headers2 = _filter_headers(r2.headers)
            return StreamingResponse(
                _stream_bytes(r2),
                status_code=r2.status_code,
                headers=resp_headers2,
                background=BackgroundTask(_aclose_httpx, r2, client2),
            )
        return Response(content=data, status_code=status_code, headers=resp_headers)
    resp_headers = _filter_headers(r.headers)
    return StreamingResponse(
        _stream_bytes(r),
        status_code=r.status_code,
        headers=resp_headers,
        background=BackgroundTask(_aclose_httpx, r, client),
    )

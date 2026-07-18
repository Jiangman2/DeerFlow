"""Gateway proxy endpoints for the external WeKnora knowledge service."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.gateway.deps import get_current_user_from_request
from app.gateway.weknora_identity import (
    ensure_weknora_session_for_deerflow_user,
    weknora_endpoint,
    weknora_timeout,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/weknora", tags=["weknora"])

_DEFAULT_EMBEDDING_MODEL_ID = "deerflow-dashscope-embedding"
_DEFAULT_SUMMARY_MODEL_ID = "deerflow-qwen-plus"
_COUNT_ENRICH_CONCURRENCY = 8
_COUNT_PAGE_SIZE = 100
_ALLOWED_SSO_ORIGINS = {
    "http://127.0.0.1:32090",
    "http://localhost:32090",
    "http://127.0.0.1:8090",
    "http://localhost:8090",
}


class WeKnoraKnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    type: str = "public"


class WeKnoraManualKnowledgeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    status: str = "publish"
    channel: str = "deerflow"


def _headers(access_token: str, *, json_content: bool = True) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    if json_content:
        headers["Content-Type"] = "application/json"
    return headers


async def _request(method: str, path: str, access_token: str, **kwargs: Any) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=weknora_timeout()) as client:
            response = await client.request(method, weknora_endpoint(path), headers=_headers(access_token, json_content="files" not in kwargs), **kwargs)
    except HTTPException:
        raise
    except httpx.RequestError as exc:
        logger.warning("WeKnora request failed: %s %s", method, path, exc_info=True)
        raise HTTPException(status_code=502, detail=f"WeKnora is unavailable: {exc}") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="WeKnora returned a non-JSON response.") from exc

    if response.status_code >= 400:
        detail = data.get("message") if isinstance(data, dict) else None
        raise HTTPException(status_code=response.status_code, detail=detail or data)
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Unexpected WeKnora response shape.")
    return data


async def _current_weknora_token(user: Any) -> str:
    session = await ensure_weknora_session_for_deerflow_user(user)
    return session.token


def _normalise_tenant_id(value: Any) -> str:
    return str(value) if value is not None else ""


def _unwrap_list_response(data: dict[str, Any]) -> dict[str, Any]:
    items = data.get("data")
    if not isinstance(items, list):
        items = []
    return {
        "items": items,
        "total": data.get("total", len(items)),
        "page": data.get("page"),
        "page_size": data.get("page_size"),
        "success": data.get("success", True),
        "message": data.get("message", ""),
    }


def _int_value(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _is_processing_knowledge(item: dict[str, Any]) -> bool:
    status = str(item.get("parse_status") or item.get("summary_status") or "").lower()
    return status in {"pending", "processing", "parsing", "unprocessed"}


async def _knowledge_base_counts(kb_id: str, access_token: str) -> dict[str, Any]:
    response = await _knowledge_base_knowledge_page(kb_id, access_token, page=1)
    items = [item for item in response["items"] if isinstance(item, dict)]
    knowledge_count = _int_value(response.get("total"), len(items))
    if knowledge_count > len(items):
        remaining_pages = range(2, (knowledge_count + _COUNT_PAGE_SIZE - 1) // _COUNT_PAGE_SIZE + 1)
        extra_pages = await asyncio.gather(
            *[
                _knowledge_base_knowledge_page(kb_id, access_token, page=page)
                for page in remaining_pages
            ],
            return_exceptions=True,
        )
        for page_data in extra_pages:
            if isinstance(page_data, Exception):
                logger.debug("Failed to fetch all WeKnora knowledge rows for %s: %s", kb_id, page_data)
                continue
            items.extend(item for item in page_data["items"] if isinstance(item, dict))
    processing_count = sum(1 for item in items if _is_processing_knowledge(item))

    async def _chunk_count_for_knowledge(knowledge_id: str) -> int:
        chunk_data = await _request(
            "GET",
            f"/chunks/{knowledge_id}",
            access_token,
            params={"page": 1, "page_size": 1},
        )
        return _int_value(chunk_data.get("total"), 0)

    chunk_counts = await asyncio.gather(
        *[
            _chunk_count_for_knowledge(str(item.get("id")))
            for item in items
            if item.get("id")
        ],
        return_exceptions=True,
    )
    chunk_count = 0
    for count in chunk_counts:
        if isinstance(count, Exception):
            logger.debug("Failed to count WeKnora chunks for knowledge base %s: %s", kb_id, count)
            continue
        chunk_count += count

    return {
        "knowledge_count": knowledge_count,
        "chunk_count": chunk_count,
        "processing_count": processing_count,
        "is_processing": processing_count > 0,
    }


async def _knowledge_base_knowledge_page(kb_id: str, access_token: str, *, page: int) -> dict[str, Any]:
    data = await _request(
        "GET",
        f"/knowledge-bases/{kb_id}/knowledge",
        access_token,
        params={"page": page, "page_size": _COUNT_PAGE_SIZE},
    )
    return _unwrap_list_response(data)


async def _enrich_knowledge_base_counts(items: list[dict[str, Any]], access_token: str) -> None:
    semaphore = asyncio.Semaphore(_COUNT_ENRICH_CONCURRENCY)

    async def _enrich(item: dict[str, Any]) -> None:
        kb_id = str(item.get("id") or "")
        if not kb_id:
            return
        if all(key in item for key in ("knowledge_count", "chunk_count", "processing_count", "is_processing")):
            return
        try:
            async with semaphore:
                item.update(await _knowledge_base_counts(kb_id, access_token))
        except HTTPException:
            logger.debug("Failed to enrich WeKnora knowledge-base counts for %s", kb_id, exc_info=True)

    await asyncio.gather(*[_enrich(item) for item in items])


@router.get("/knowledge-bases")
async def list_knowledge_bases(user: Any = Depends(get_current_user_from_request)) -> dict[str, Any]:
    session = await ensure_weknora_session_for_deerflow_user(user)
    own_data = await _request("GET", "/knowledge-bases", session.token)
    own_response = _unwrap_list_response(own_data)
    active_tenant_id = _normalise_tenant_id(session.tenant.get("id"))

    items_by_id: dict[str, dict[str, Any]] = {}
    for item in own_response["items"]:
        if not isinstance(item, dict):
            continue
        kb = dict(item)
        kb_id = str(kb.get("id") or "")
        if not kb_id:
            continue
        kb["access_scope"] = "owned"
        kb["is_owner"] = True
        kb["owner_tenant_id"] = _normalise_tenant_id(kb.get("tenant_id") or active_tenant_id)
        items_by_id[kb_id] = kb

    try:
        shared_data = await _request("GET", "/shared-knowledge-bases", session.token)
    except HTTPException as exc:
        logger.warning("Failed to list shared WeKnora knowledge bases: %s", exc.detail)
    else:
        shared_items = _unwrap_list_response(shared_data)["items"]
        for row in shared_items:
            if not isinstance(row, dict):
                continue
            kb_raw = row.get("knowledge_base")
            if not isinstance(kb_raw, dict):
                continue
            kb = dict(kb_raw)
            kb_id = str(kb.get("id") or "")
            if not kb_id or kb_id in items_by_id:
                continue
            kb.update(
                {
                    "access_scope": "joined",
                    "is_owner": False,
                    "share_id": row.get("share_id"),
                    "organization_id": row.get("organization_id"),
                    "org_name": row.get("org_name"),
                    "permission": row.get("permission"),
                    "source_tenant_id": _normalise_tenant_id(row.get("source_tenant_id")),
                    "shared_at": row.get("shared_at"),
                }
            )
            items_by_id[kb_id] = kb

    items = list(items_by_id.values())
    await _enrich_knowledge_base_counts(items, session.token)
    return {
        **own_response,
        "items": items,
        "total": len(items),
        "owned_total": sum(1 for item in items if item.get("access_scope") == "owned"),
        "joined_total": sum(1 for item in items if item.get("access_scope") == "joined"),
    }


@router.post("/knowledge-bases")
async def create_knowledge_base(request: WeKnoraKnowledgeBaseCreate, user: Any = Depends(get_current_user_from_request)) -> dict[str, Any]:
    payload = {
        "name": request.name.strip(),
        "description": request.description.strip(),
        "type": request.type,
        "embedding_model_id": os.getenv("WEKNORA_DEFAULT_EMBEDDING_MODEL_ID", _DEFAULT_EMBEDDING_MODEL_ID),
        "summary_model_id": os.getenv("WEKNORA_DEFAULT_SUMMARY_MODEL_ID", _DEFAULT_SUMMARY_MODEL_ID),
        "chunking_config": {
            "chunk_size": 512,
            "chunk_overlap": 50,
            "separators": ["\n\n", "\n", ". "],
        },
        "image_processing_config": {},
        "storage_provider_config": {"provider": "local"},
        "storage_config": {"provider": "local"},
        "extract_config": {"enabled": False},
    }
    return await _request("POST", "/knowledge-bases", await _current_weknora_token(user), json=payload)


@router.get("/knowledge-bases/{knowledge_base_id}/knowledge")
async def list_knowledge(
    knowledge_base_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    keyword: str | None = None,
    parse_status: str | None = None,
    user: Any = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if keyword:
        params["keyword"] = keyword
    if parse_status:
        params["parse_status"] = parse_status
    data = await _request("GET", f"/knowledge-bases/{knowledge_base_id}/knowledge", await _current_weknora_token(user), params=params)
    return _unwrap_list_response(data)


@router.get("/knowledge/{knowledge_id}")
async def get_knowledge(knowledge_id: str, user: Any = Depends(get_current_user_from_request)) -> dict[str, Any]:
    return await _request("GET", f"/knowledge/{knowledge_id}", await _current_weknora_token(user))


@router.post("/knowledge-bases/{knowledge_base_id}/knowledge/manual")
async def create_manual_knowledge(
    knowledge_base_id: str,
    request: WeKnoraManualKnowledgeCreate,
    user: Any = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    payload = {
        "title": request.title.strip(),
        "content": request.content,
        "status": request.status,
        "channel": request.channel,
    }
    return await _request("POST", f"/knowledge-bases/{knowledge_base_id}/knowledge/manual", await _current_weknora_token(user), json=payload)


@router.post("/knowledge-bases/{knowledge_base_id}/knowledge/file")
async def upload_knowledge_file(
    knowledge_base_id: str,
    file: UploadFile = File(...),
    enable_multimodel: bool = False,
    user: Any = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    form_data = {
        "channel": "deerflow",
        "enable_multimodel": "true" if enable_multimodel else "false",
        "metadata": json.dumps({"source": "deerflow"}, ensure_ascii=False),
    }
    files = {
        "file": (
            file.filename or "upload",
            content,
            file.content_type or "application/octet-stream",
        )
    }
    return await _request("POST", f"/knowledge-bases/{knowledge_base_id}/knowledge/file", await _current_weknora_token(user), data=form_data, files=files)


def _sso_cors_headers(request: Request) -> dict[str, str]:
    origin = request.headers.get("origin")
    if origin in _ALLOWED_SSO_ORIGINS:
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Credentials": "true",
            "Vary": "Origin",
        }
    return {}


@router.options("/sso-session")
async def sso_session_options(request: Request) -> Response:
    headers = _sso_cors_headers(request)
    headers.update(
        {
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }
    )
    return Response(status_code=204, headers=headers)


@router.get("/sso-session")
async def sso_session(request: Request, user: Any = Depends(get_current_user_from_request)) -> JSONResponse:
    session = await ensure_weknora_session_for_deerflow_user(user)
    return JSONResponse(session.as_login_response(), headers=_sso_cors_headers(request))

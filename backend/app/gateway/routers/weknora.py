"""Gateway proxy endpoints for the external WeKnora knowledge service."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import math
import mimetypes
import os
import re
import ssl
from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.gateway.deps import get_current_user_from_request
from app.gateway.weknora_identity import (
    build_weknora_ui_policy,
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
_PRESENTATION_FILE_TYPES = ("ppt", "pptx")
_VISUAL_DOCUMENT_FILE_TYPES = ("pdf", "doc", "docx", "ppt", "pptx")
_SUPPORTED_PRESENTATION_PARSERS = {"markitdown", "mineru"}
_DEFAULT_BAILIAN_ASR_MODEL = "qwen3-asr-flash"
_DEFAULT_BAILIAN_ASR_UPSTREAM_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
_DEFAULT_BAILIAN_ASR_MAX_BYTES = 10 * 1024 * 1024
_DEFAULT_BAILIAN_EMBEDDING_MODEL = "text-embedding-v4"
_DEFAULT_BAILIAN_EMBEDDING_UPSTREAM_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
_DEFAULT_BAILIAN_EMBEDDING_MAX_BYTES = 8 * 1024 * 1024
_DEFAULT_CNOOC_EMBEDDING_MODEL = "Qwen3-Embedding-8B"
_DEFAULT_CNOOC_EMBEDDING_UPSTREAM_URL = "http://model.ai.cnooc/member1/qwen3-embedding-8b/v1/embeddings"
_DEFAULT_CNOOC_RERANK_MODEL = "Qwen3-Reranker-8B"
_DEFAULT_CNOOC_RERANK_UPSTREAM_URL = "http://model.ai.cnooc/member1/qwen3-reranker-8b/v1/rerank"
_DEFAULT_CNOOC_RETRIEVAL_MAX_BYTES = 8 * 1024 * 1024
_ALLOWED_SSO_ORIGINS = {
    "http://127.0.0.1:32090",
    "http://localhost:32090",
    "http://127.0.0.1:8090",
    "http://localhost:8090",
}
_WEKNORA_RESOURCE_URI_RE = re.compile(r"^resource://[A-Za-z0-9_-]{1,256}$")


def _truthy_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _presentation_parser_rules() -> list[dict[str, Any]]:
    """Return parser rules for presentations and visual document preservation."""
    engine = os.getenv("WEKNORA_PRESENTATION_PARSER_ENGINE", "markitdown").strip().lower()
    if engine not in _SUPPORTED_PRESENTATION_PARSERS:
        logger.warning(
            "Unsupported WEKNORA_PRESENTATION_PARSER_ENGINE=%r; falling back to markitdown",
            engine,
        )
        engine = "markitdown"
    document_visual_fallback = _truthy_env(
        "WEKNORA_MINERU_DOCUMENT_VISUAL_FALLBACK",
        _truthy_env("WEKNORA_MINERU_SCANNED_PDF_FALLBACK"),
    )
    file_types = list(_VISUAL_DOCUMENT_FILE_TYPES) if engine == "mineru" and document_visual_fallback else list(_PRESENTATION_FILE_TYPES)
    return [{"file_types": file_types, "engine": engine}]


def _upload_process_config(filename: str | None) -> dict[str, Any] | None:
    extension = os.path.splitext(filename or "")[1].lower().lstrip(".")
    rules = _presentation_parser_rules()
    if extension in rules[0]["file_types"]:
        config: dict[str, Any] = {"parser_engine_rules": rules}
        mineru_endpoint = os.getenv("WEKNORA_MINERU_ENDPOINT", "").strip()
        if rules[0]["engine"] == "mineru" and mineru_endpoint:
            config["parser_engine_overrides"] = {"mineru_endpoint": mineru_endpoint}
        return config
    return None


def _float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning("Ignoring invalid float value for %s", name)
        return default


def _int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning("Ignoring invalid integer value for %s", name)
        return default


def _bailian_tls_verify() -> bool | ssl.SSLContext:
    """Return scoped TLS verification for the authenticated Bailian adapters.

    Some audited enterprise interception certificates use an RSA key that
    OpenSSL 3 rejects at its default security level. Compatibility mode lowers
    only the cipher security level for these two Gateway-owned upstream calls;
    the normal CA chain and hostname checks remain enabled.
    """

    if not _truthy_env("WEKNORA_BAILIAN_TLS_COMPAT"):
        return True
    context = ssl.create_default_context()
    context.set_ciphers("DEFAULT:@SECLEVEL=1")
    return context


def _bailian_adapter_api_key() -> str:
    for env_name in ("ASR_API_KEY", "LLM_API_KEY", "DASHSCOPE_API_KEY"):
        value = os.getenv(env_name)
        if value and value.strip():
            return value.strip()
    raise HTTPException(status_code=503, detail="Bailian ASR API key is not configured.")


def _require_bailian_adapter_key(request: Request) -> str:
    expected = _bailian_adapter_api_key()
    authorization = request.headers.get("authorization", "")
    scheme, _, provided = authorization.partition(" ")
    if scheme.lower() != "bearer" or not provided or not hmac.compare_digest(provided.strip(), expected):
        raise HTTPException(status_code=401, detail="Invalid Bailian ASR adapter credentials.")
    return expected


def _embedding_adapter_api_key() -> str:
    for env_name in ("EMBEDDING_API_KEY", "DASHSCOPE_API_KEY", "LLM_API_KEY"):
        value = os.getenv(env_name)
        if value and value.strip():
            return value.strip()
    raise HTTPException(status_code=503, detail="Bailian embedding API key is not configured.")


def _require_embedding_adapter_key(request: Request) -> str:
    expected = _embedding_adapter_api_key()
    authorization = request.headers.get("authorization", "")
    scheme, _, provided = authorization.partition(" ")
    if scheme.lower() != "bearer" or not provided or not hmac.compare_digest(provided.strip(), expected):
        raise HTTPException(status_code=401, detail="Invalid Bailian embedding adapter credentials.")
    return expected


def _internal_model_relay_key() -> str:
    value = os.getenv("WEKNORA_INTERNAL_MODEL_RELAY_KEY", "").strip()
    if value:
        return value
    raise HTTPException(status_code=503, detail="Internal model relay credentials are not configured.")


def _require_internal_model_relay_key(request: Request) -> None:
    expected = _internal_model_relay_key()
    authorization = request.headers.get("authorization", "")
    scheme, _, provided = authorization.partition(" ")
    if scheme.lower() != "bearer" or not provided or not hmac.compare_digest(provided.strip(), expected):
        raise HTTPException(status_code=401, detail="Invalid internal model relay credentials.")


def _internal_retrieval_upstream_key() -> str:
    value = os.getenv("CNOOC_RETRIEVAL_API_KEY", "").strip()
    if value:
        return value
    raise HTTPException(status_code=503, detail="Internal retrieval model credentials are not configured.")


async def _read_json_request(request: Request, *, label: str, max_bytes: int) -> dict[str, Any]:
    content_length = request.headers.get("content-length", "").strip()
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(status_code=413, detail=f"{label} request exceeds the {max_bytes}-byte limit.")
        except ValueError:
            pass
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail=f"{label} request body is empty.")
    if len(body) > max_bytes:
        raise HTTPException(status_code=413, detail=f"{label} request exceeds the {max_bytes}-byte limit.")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"{label} request body must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail=f"{label} request body must be a JSON object.")
    return payload


async def _post_internal_retrieval_model(
    *,
    upstream_url: str,
    upstream_key: str,
    payload: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    timeout = max(1.0, _float_env("WEKNORA_INTERNAL_MODEL_TIMEOUT_SECONDS", 180.0))
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(
                upstream_url,
                headers={
                    "Authorization": f"Bearer {upstream_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
                json=payload,
            )
    except httpx.RequestError as exc:
        logger.warning("%s upstream request failed.", label, exc_info=True)
        raise HTTPException(status_code=502, detail=f"{label} is unavailable.") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"{label} returned a non-JSON response.") from exc
    if response.status_code >= 400:
        logger.warning("%s returned HTTP %s.", label, response.status_code)
        raise HTTPException(status_code=502, detail=f"{label} returned HTTP {response.status_code}.")
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail=f"{label} returned an invalid response.")
    if data.get("success") is False:
        code = str(data.get("code") or "unknown")
        logger.warning("%s returned business failure code=%s.", label, code)
        raise HTTPException(status_code=502, detail=f"{label} rejected the request (code {code}).")
    return data


def _audio_media_type(file: UploadFile) -> str:
    content_type = (file.content_type or "").strip().lower()
    if content_type and content_type != "application/octet-stream":
        return content_type
    guessed, _ = mimetypes.guess_type(file.filename or "")
    return guessed or "application/octet-stream"


def _transcript_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "".join(str(item.get("text") or "") for item in content if isinstance(item, dict)).strip()
    return ""


class WeKnoraKnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    type: str = "document"


class WeKnoraManualKnowledgeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1)
    status: str = "publish"
    channel: str = "deerflow"


def _headers(
    access_token: str,
    *,
    json_content: bool = True,
    tenant_id: str | int | None = None,
) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    if json_content:
        headers["Content-Type"] = "application/json"
    if tenant_id is not None and str(tenant_id).strip():
        headers["X-Tenant-ID"] = str(tenant_id)
    return headers


async def _request(
    method: str,
    path: str,
    access_token: str,
    *,
    tenant_id: str | int | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=weknora_timeout()) as client:
            response = await client.request(
                method,
                weknora_endpoint(path),
                headers=_headers(
                    access_token,
                    json_content="files" not in kwargs,
                    tenant_id=tenant_id,
                ),
                **kwargs,
            )
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


async def _request_knowledge_image(
    knowledge_base_id: str,
    file_path: str,
    access_token: str,
    *,
    tenant_id: str | int | None,
) -> Response:
    try:
        async with httpx.AsyncClient(timeout=weknora_timeout()) as client:
            response = await client.get(
                weknora_endpoint(f"/knowledge-bases/{knowledge_base_id}/files"),
                headers=_headers(
                    access_token,
                    json_content=False,
                    tenant_id=tenant_id,
                ),
                params={"file_path": file_path},
            )
    except httpx.RequestError as exc:
        logger.warning(
            "WeKnora image request failed for knowledge base %s",
            knowledge_base_id,
            exc_info=True,
        )
        raise HTTPException(
            status_code=502,
            detail="WeKnora image is unavailable.",
        ) from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail="WeKnora image request failed.",
        )
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if not content_type.startswith("image/"):
        raise HTTPException(
            status_code=415,
            detail="The requested WeKnora resource is not an image.",
        )
    return Response(
        content=response.content,
        media_type=content_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _current_weknora_token(user: Any) -> str:
    session = await ensure_weknora_session_for_deerflow_user(user)
    return session.token


def _normalise_tenant_id(value: Any) -> str:
    return str(value) if value is not None else ""


def _weknora_root_endpoint(path: str) -> str:
    api_v1_root = weknora_endpoint("").rstrip("/")
    suffix = "/api/v1"
    if not api_v1_root.endswith(suffix):
        raise HTTPException(
            status_code=500,
            detail="Unexpected WeKnora API endpoint configuration.",
        )
    return f"{api_v1_root[: -len(suffix)]}/{path.lstrip('/')}"


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


async def _knowledge_base_counts(
    kb_id: str,
    access_token: str,
    tenant_id: str | int | None = None,
) -> dict[str, Any]:
    response = await _knowledge_base_knowledge_page(
        kb_id,
        access_token,
        page=1,
        tenant_id=tenant_id,
    )
    items = [item for item in response["items"] if isinstance(item, dict)]
    knowledge_count = _int_value(response.get("total"), len(items))
    if knowledge_count > len(items):
        remaining_pages = range(2, (knowledge_count + _COUNT_PAGE_SIZE - 1) // _COUNT_PAGE_SIZE + 1)
        extra_pages = await asyncio.gather(
            *[
                _knowledge_base_knowledge_page(
                    kb_id,
                    access_token,
                    page=page,
                    tenant_id=tenant_id,
                )
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
            tenant_id=tenant_id,
            params={"page": 1, "page_size": 1},
        )
        return _int_value(chunk_data.get("total"), 0)

    chunk_counts = await asyncio.gather(
        *[_chunk_count_for_knowledge(str(item.get("id"))) for item in items if item.get("id")],
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


async def _knowledge_base_knowledge_page(
    kb_id: str,
    access_token: str,
    *,
    page: int,
    tenant_id: str | int | None = None,
) -> dict[str, Any]:
    data = await _request(
        "GET",
        f"/knowledge-bases/{kb_id}/knowledge",
        access_token,
        tenant_id=tenant_id,
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
                tenant_id = item.get("access_tenant_id") or item.get("owner_tenant_id") or item.get("source_tenant_id") or item.get("tenant_id")
                item.update(await _knowledge_base_counts(kb_id, access_token, tenant_id=tenant_id))
        except HTTPException:
            logger.debug("Failed to enrich WeKnora knowledge-base counts for %s", kb_id, exc_info=True)

    await asyncio.gather(*[_enrich(item) for item in items])


def _session_identity_fallback(session: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    session_user = session.user if isinstance(getattr(session, "user", None), dict) else {}
    memberships = [item for item in (getattr(session, "memberships", None) or []) if isinstance(item, dict)]
    if not memberships and isinstance(getattr(session, "tenant", None), dict):
        memberships = [
            {
                "tenant_id": session.tenant.get("id"),
                "tenant_name": session.tenant.get("name"),
                "role": "owner",
            }
        ]
    return session_user, memberships


async def _live_weknora_identity(session: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    fallback = _session_identity_fallback(session)
    try:
        response = await _request("GET", "/auth/me", session.token)
    except HTTPException:
        logger.warning("Failed to refresh WeKnora memberships; using the cached login session.", exc_info=True)
        return fallback

    data = response.get("data")
    if not isinstance(data, dict):
        return fallback
    live_user = data.get("user")
    live_memberships = data.get("memberships")
    if not isinstance(live_user, dict):
        live_user = fallback[0]
    if not isinstance(live_memberships, list):
        live_memberships = fallback[1]
    memberships = [item for item in live_memberships if isinstance(item, dict) and item.get("tenant_id") is not None]
    return live_user, memberships or fallback[1]


async def _resolve_knowledge_base_access_context(
    user: Any,
    knowledge_base_id: str,
    *,
    requested_tenant_id: str | int | None,
) -> tuple[str, str]:
    """Resolve the consumer tenant for a knowledge-base-scoped request.

    New frontends send ``tenant_id`` explicitly. This fallback protects stale
    browser bundles and direct API clients from accidentally using the tenant
    embedded in the current WeKnora JWT, which may differ from the tenant that
    owns or consumes the selected knowledge base.
    """

    explicit_tenant_id = _normalise_tenant_id(requested_tenant_id).strip()
    if explicit_tenant_id:
        return await _current_weknora_token(user), explicit_tenant_id

    session = await ensure_weknora_session_for_deerflow_user(user)
    _, memberships = await _live_weknora_identity(session)
    target_id = str(knowledge_base_id)

    async def _membership_access_tenant(membership: dict[str, Any]) -> tuple[str | None, bool]:
        access_tenant_id = _normalise_tenant_id(membership.get("tenant_id")).strip()
        if not access_tenant_id:
            return None, False

        knowledge_data, shared_data = await asyncio.gather(
            _request("GET", "/knowledge-bases", session.token, tenant_id=access_tenant_id),
            _request("GET", "/shared-knowledge-bases", session.token, tenant_id=access_tenant_id),
            return_exceptions=True,
        )
        lookup_succeeded = not isinstance(knowledge_data, Exception) or not isinstance(shared_data, Exception)

        if not isinstance(knowledge_data, Exception):
            for item in _unwrap_list_response(knowledge_data)["items"]:
                if isinstance(item, dict) and str(item.get("id") or "") == target_id:
                    return access_tenant_id, True

        if not isinstance(shared_data, Exception):
            for row in _unwrap_list_response(shared_data)["items"]:
                if not isinstance(row, dict):
                    continue
                knowledge_base = row.get("knowledge_base")
                if isinstance(knowledge_base, dict) and str(knowledge_base.get("id") or "") == target_id:
                    return access_tenant_id, True

        return None, lookup_succeeded

    results = await asyncio.gather(*[_membership_access_tenant(membership) for membership in memberships])
    matching_tenant_ids = [tenant_id for tenant_id, _ in results if tenant_id]
    if matching_tenant_ids:
        active_tenant_id = ""
        if isinstance(getattr(session, "tenant", None), dict):
            active_tenant_id = _normalise_tenant_id(session.tenant.get("id")).strip()
        resolved_tenant_id = active_tenant_id if active_tenant_id in matching_tenant_ids else matching_tenant_ids[0]
        logger.info(
            "Resolved missing WeKnora tenant context for knowledge base %s to tenant %s.",
            knowledge_base_id,
            resolved_tenant_id,
        )
        return session.token, resolved_tenant_id

    if not any(lookup_succeeded for _, lookup_succeeded in results):
        raise HTTPException(status_code=502, detail="Unable to resolve the WeKnora tenant for this knowledge base.")
    raise HTTPException(status_code=403, detail="This knowledge base is not accessible in the current user's tenants.")


def _classify_tenant_knowledge_base(
    item: dict[str, Any],
    *,
    current_user_id: str,
    membership: dict[str, Any],
) -> dict[str, Any]:
    kb = dict(item)
    tenant_id = _normalise_tenant_id(kb.get("tenant_id") or membership.get("tenant_id"))
    creator_id = str(kb.get("creator_id") or "")
    is_owner = bool(current_user_id and creator_id and creator_id == current_user_id)
    is_joined = not is_owner
    is_team_or_public = bool(_int_value(kb.get("share_count"), 0) > 0 or kb.get("access_scope") in {"team", "public"})
    kb.update(
        {
            "access_scope": "owned" if is_owner else "joined",
            "is_owner": is_owner,
            "is_joined": is_joined,
            "is_team_or_public": is_team_or_public,
            "owner_tenant_id": tenant_id,
            "access_tenant_id": _normalise_tenant_id(membership.get("tenant_id") or tenant_id),
            "tenant_name": membership.get("tenant_name") or "",
            "membership_role": membership.get("role") or "",
        }
    )
    return kb


@router.get("/knowledge-bases")
async def list_knowledge_bases(user: Any = Depends(get_current_user_from_request)) -> dict[str, Any]:
    session = await ensure_weknora_session_for_deerflow_user(user)
    live_user, memberships = await _live_weknora_identity(session)
    session_user = getattr(session, "user", None)
    if not isinstance(session_user, dict):
        session_user = {}
    current_user_id = str(live_user.get("id") or session_user.get("id") or "")

    async def _list_tenant_resources(membership: dict[str, Any]) -> tuple[dict[str, Any], Any, Any]:
        tenant_id = membership.get("tenant_id")
        knowledge_bases, shared = await asyncio.gather(
            _request("GET", "/knowledge-bases", session.token, tenant_id=tenant_id),
            _request("GET", "/shared-knowledge-bases", session.token, tenant_id=tenant_id),
            return_exceptions=True,
        )
        return membership, knowledge_bases, shared

    tenant_results = await asyncio.gather(*[_list_tenant_resources(item) for item in memberships])
    items_by_id: dict[str, dict[str, Any]] = {}

    for membership, knowledge_data, shared_data in tenant_results:
        if isinstance(knowledge_data, Exception):
            logger.warning(
                "Failed to list WeKnora knowledge bases for tenant %s: %s",
                membership.get("tenant_id"),
                knowledge_data,
            )
        else:
            for item in _unwrap_list_response(knowledge_data)["items"]:
                if not isinstance(item, dict):
                    continue
                kb = _classify_tenant_knowledge_base(
                    item,
                    current_user_id=current_user_id,
                    membership=membership,
                )
                kb_id = str(kb.get("id") or "")
                if kb_id:
                    items_by_id[kb_id] = kb

        if isinstance(shared_data, Exception):
            logger.warning(
                "Failed to list shared WeKnora knowledge bases for tenant %s: %s",
                membership.get("tenant_id"),
                shared_data,
            )
            continue
        for row in _unwrap_list_response(shared_data)["items"]:
            if not isinstance(row, dict):
                continue
            kb_raw = row.get("knowledge_base")
            if not isinstance(kb_raw, dict):
                continue
            kb_id = str(kb_raw.get("id") or "")
            if not kb_id:
                continue
            existing = items_by_id.get(kb_id)
            if existing is None:
                source_membership = {
                    **membership,
                    "tenant_id": row.get("source_tenant_id") or kb_raw.get("tenant_id"),
                }
                existing = _classify_tenant_knowledge_base(
                    kb_raw,
                    current_user_id=current_user_id,
                    membership=source_membership,
                )
                existing["access_tenant_id"] = _normalise_tenant_id(membership.get("tenant_id"))
                items_by_id[kb_id] = existing
            existing.update(
                {
                    "is_team_or_public": True,
                    "share_id": row.get("share_id"),
                    "organization_id": row.get("organization_id"),
                    "org_name": row.get("org_name"),
                    "permission": row.get("permission"),
                    "source_tenant_id": _normalise_tenant_id(row.get("source_tenant_id")),
                    "shared_at": row.get("shared_at"),
                }
            )

    items = list(items_by_id.values())
    try:
        count_timeout = _float_env("WEKNORA_KB_COUNT_TIMEOUT_SECONDS", 5.0)
        if count_timeout > 0:
            await asyncio.wait_for(_enrich_knowledge_base_counts(items, session.token), timeout=count_timeout)
        else:
            await _enrich_knowledge_base_counts(items, session.token)
    except TimeoutError:
        logger.warning("Skipping WeKnora knowledge-base count enrichment after %.1fs.", count_timeout)
    except Exception:
        logger.warning("Skipping WeKnora knowledge-base count enrichment due to an unexpected error.", exc_info=True)
    return {
        "items": items,
        "total": len(items),
        "owned_total": sum(1 for item in items if item.get("is_owner") is True),
        "joined_total": sum(1 for item in items if item.get("is_joined") is True),
        "team_total": sum(1 for item in items if item.get("is_team_or_public") is True),
        "success": True,
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
            "parser_engine_rules": _presentation_parser_rules(),
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
    tenant_id: str | None = None,
    user: Any = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    params: dict[str, Any] = {"page": page, "page_size": page_size}
    if keyword:
        params["keyword"] = keyword
    if parse_status:
        params["parse_status"] = parse_status
    access_token, access_tenant_id = await _resolve_knowledge_base_access_context(
        user,
        knowledge_base_id,
        requested_tenant_id=tenant_id,
    )
    data = await _request(
        "GET",
        f"/knowledge-bases/{knowledge_base_id}/knowledge",
        access_token,
        tenant_id=access_tenant_id,
        params=params,
    )
    return _unwrap_list_response(data)


@router.get("/knowledge/{knowledge_id}")
async def get_knowledge(
    knowledge_id: str,
    tenant_id: str | None = None,
    user: Any = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    return await _request(
        "GET",
        f"/knowledge/{knowledge_id}",
        await _current_weknora_token(user),
        tenant_id=tenant_id,
    )


@router.get("/knowledge-bases/{knowledge_base_id}/files")
async def get_knowledge_file(
    knowledge_base_id: str,
    file_path: str,
    tenant_id: str | None = None,
    user: Any = Depends(get_current_user_from_request),
) -> Response:
    """Serve a WeKnora image through the authenticated DeerFlow origin."""

    if not _WEKNORA_RESOURCE_URI_RE.fullmatch(file_path):
        raise HTTPException(
            status_code=400,
            detail="Invalid WeKnora resource path.",
        )
    access_token, access_tenant_id = await _resolve_knowledge_base_access_context(
        user,
        knowledge_base_id,
        requested_tenant_id=tenant_id,
    )
    return await _request_knowledge_image(
        knowledge_base_id,
        file_path,
        access_token,
        tenant_id=access_tenant_id,
    )


@router.get("/files")
async def get_active_tenant_file(
    file_path: str,
    user: Any = Depends(get_current_user_from_request),
) -> Response:
    """Fallback for a raw resource handle that reaches DeerFlow Markdown.

    Normal WeKnora tool output uses the knowledge-base-scoped endpoint above.
    This route exists only for defensive rendering of unnormalized historical
    messages and still uses the authenticated user's active WeKnora workspace.
    """

    if not _WEKNORA_RESOURCE_URI_RE.fullmatch(file_path):
        raise HTTPException(
            status_code=400,
            detail="Invalid WeKnora resource path.",
        )
    session = await ensure_weknora_session_for_deerflow_user(user)
    tenant_id = session.tenant.get("id") if isinstance(session.tenant, dict) else None
    try:
        async with httpx.AsyncClient(timeout=weknora_timeout()) as client:
            response = await client.get(
                _weknora_root_endpoint("/files"),
                headers=_headers(
                    session.token,
                    json_content=False,
                    tenant_id=tenant_id,
                ),
                params={"file_path": file_path},
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail="WeKnora image is unavailable.",
        ) from exc
    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail="WeKnora image request failed.",
        )
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if not content_type.startswith("image/"):
        raise HTTPException(
            status_code=415,
            detail="The requested WeKnora resource is not an image.",
        )
    return Response(
        content=response.content,
        media_type=content_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/knowledge-bases/{knowledge_base_id}/knowledge/manual")
async def create_manual_knowledge(
    knowledge_base_id: str,
    request: WeKnoraManualKnowledgeCreate,
    tenant_id: str | None = None,
    user: Any = Depends(get_current_user_from_request),
) -> dict[str, Any]:
    payload = {
        "title": request.title.strip(),
        "content": request.content,
        "status": request.status,
        "channel": request.channel,
    }
    return await _request(
        "POST",
        f"/knowledge-bases/{knowledge_base_id}/knowledge/manual",
        await _current_weknora_token(user),
        tenant_id=tenant_id,
        json=payload,
    )


@router.post("/knowledge-bases/{knowledge_base_id}/knowledge/file")
async def upload_knowledge_file(
    knowledge_base_id: str,
    file: UploadFile = File(...),
    enable_multimodel: bool = False,
    tenant_id: str | None = None,
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
    process_config = _upload_process_config(file.filename)
    if process_config is not None:
        form_data["process_config"] = json.dumps(process_config, ensure_ascii=False)
    files = {
        "file": (
            file.filename or "upload",
            content,
            file.content_type or "application/octet-stream",
        )
    }
    return await _request(
        "POST",
        f"/knowledge-bases/{knowledge_base_id}/knowledge/file",
        await _current_weknora_token(user),
        tenant_id=tenant_id,
        data=form_data,
        files=files,
    )


@router.post("/bailian/v1/embeddings")
async def bailian_embeddings(request: Request) -> JSONResponse:
    """Relay WeKnora embedding requests through Gateway's managed TLS path.

    The enterprise WeKnora Pod must not connect to DashScope directly because
    its outbound proxy can alternate certificate profiles. Gateway already
    owns the scoped proxy/CA/OpenSSL compatibility configuration, so this
    endpoint authenticates the in-cluster request and forwards the unchanged
    OpenAI-compatible JSON body without persisting document text.
    """

    api_key = _require_embedding_adapter_key(request)
    max_bytes = max(
        1,
        _int_env(
            "WEKNORA_BAILIAN_EMBEDDING_MAX_BYTES",
            _DEFAULT_BAILIAN_EMBEDDING_MAX_BYTES,
        ),
    )
    content_length = request.headers.get("content-length", "").strip()
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"Embedding request exceeds the {max_bytes}-byte limit.",
                )
        except ValueError:
            pass
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Embedding request body is empty.")
    if len(body) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Embedding request exceeds the {max_bytes}-byte limit.",
        )
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Embedding request body must be valid JSON.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Embedding request body must be a JSON object.")

    configured_model = os.getenv(
        "EMBEDDING_MODEL_NAME",
        _DEFAULT_BAILIAN_EMBEDDING_MODEL,
    ).strip()
    requested_model = str(payload.get("model") or "").strip()
    if requested_model != configured_model:
        raise HTTPException(status_code=400, detail="Unsupported Bailian embedding model.")
    if "input" not in payload:
        raise HTTPException(status_code=400, detail="Embedding request input is required.")

    upstream_url = os.getenv(
        "WEKNORA_BAILIAN_EMBEDDING_UPSTREAM_URL",
        _DEFAULT_BAILIAN_EMBEDDING_UPSTREAM_URL,
    ).strip()
    timeout = max(
        1.0,
        _float_env("WEKNORA_BAILIAN_EMBEDDING_TIMEOUT_SECONDS", 120.0),
    )
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            verify=_bailian_tls_verify(),
        ) as client:
            response = await client.post(
                upstream_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
            )
    except httpx.RequestError as exc:
        logger.warning("Bailian embedding request failed.", exc_info=True)
        raise HTTPException(status_code=502, detail="Bailian embedding is unavailable.") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Bailian embedding returned a non-JSON response.") from exc
    if response.status_code >= 400:
        logger.warning("Bailian embedding returned HTTP %s.", response.status_code)
        raise HTTPException(
            status_code=502,
            detail=f"Bailian embedding returned HTTP {response.status_code}.",
        )
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="Bailian embedding returned an invalid response.")
    return JSONResponse(content=data)


@router.post("/internal-models/v1/embeddings")
async def cnooc_embeddings(request: Request) -> JSONResponse:
    """Relay WeKnora embedding batches to the approved internal model service."""

    _require_internal_model_relay_key(request)
    upstream_key = _internal_retrieval_upstream_key()
    max_bytes = max(
        1,
        _int_env("WEKNORA_INTERNAL_MODEL_MAX_BYTES", _DEFAULT_CNOOC_RETRIEVAL_MAX_BYTES),
    )
    payload = await _read_json_request(request, label="Internal embedding", max_bytes=max_bytes)
    configured_model = os.getenv(
        "CNOOC_EMBEDDING_MODEL_NAME",
        _DEFAULT_CNOOC_EMBEDDING_MODEL,
    ).strip()
    if str(payload.get("model") or "").strip() != configured_model:
        raise HTTPException(status_code=400, detail="Unsupported internal embedding model.")
    inputs = payload.get("input")
    if isinstance(inputs, str):
        if not inputs.strip():
            raise HTTPException(status_code=400, detail="Internal embedding input must not be empty.")
        expected_rows = 1
    elif isinstance(inputs, list):
        if not inputs or not all(isinstance(item, str) and item.strip() for item in inputs):
            raise HTTPException(status_code=400, detail="Internal embedding input must contain non-empty strings.")
        expected_rows = len(inputs)
    else:
        raise HTTPException(status_code=400, detail="Internal embedding input must be a string or string array.")

    expected_dimension = max(1, _int_env("CNOOC_EMBEDDING_DIMENSION", 1024))
    requested_dimension = payload.get("dimensions")
    if requested_dimension is not None and (not isinstance(requested_dimension, int) or requested_dimension != expected_dimension):
        raise HTTPException(
            status_code=400,
            detail=f"Internal embedding dimensions must be {expected_dimension}.",
        )

    upstream_payload = {
        "input": inputs,
        "encoding_format": str(payload.get("encoding_format") or "float"),
        "dimensions": expected_dimension,
    }
    data = await _post_internal_retrieval_model(
        upstream_url=os.getenv(
            "CNOOC_EMBEDDING_UPSTREAM_URL",
            _DEFAULT_CNOOC_EMBEDDING_UPSTREAM_URL,
        ).strip(),
        upstream_key=upstream_key,
        payload=upstream_payload,
        label="Internal embedding",
    )
    rows = data.get("data")
    if not isinstance(rows, list) or len(rows) != expected_rows:
        raise HTTPException(status_code=502, detail="Internal embedding returned an unexpected row count.")
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("embedding"), list):
            raise HTTPException(status_code=502, detail="Internal embedding returned an invalid vector.")
        vector = row["embedding"]
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in vector):
            raise HTTPException(status_code=502, detail="Internal embedding returned a non-numeric vector.")
        if len(vector) < expected_dimension:
            raise HTTPException(status_code=502, detail="Internal embedding returned an unexpected vector dimension.")
        if len(vector) > expected_dimension:
            if not _truthy_env("CNOOC_EMBEDDING_MRL_TRUNCATION_ENABLED", True):
                raise HTTPException(status_code=502, detail="Internal embedding returned an unexpected vector dimension.")
            truncated = vector[:expected_dimension]
            norm = math.sqrt(sum(float(value) * float(value) for value in truncated))
            if not math.isfinite(norm) or norm <= 0:
                raise HTTPException(status_code=502, detail="Internal embedding returned a zero or invalid vector.")
            row["embedding"] = [float(value) / norm for value in truncated]
    return JSONResponse(content=data)


@router.post("/internal-models/v1/rerank")
async def cnooc_rerank(request: Request) -> JSONResponse:
    """Relay WeKnora rerank requests to the approved internal model service."""

    _require_internal_model_relay_key(request)
    upstream_key = _internal_retrieval_upstream_key()
    max_bytes = max(
        1,
        _int_env("WEKNORA_INTERNAL_MODEL_MAX_BYTES", _DEFAULT_CNOOC_RETRIEVAL_MAX_BYTES),
    )
    payload = await _read_json_request(request, label="Internal rerank", max_bytes=max_bytes)
    configured_model = os.getenv(
        "CNOOC_RERANK_MODEL_NAME",
        _DEFAULT_CNOOC_RERANK_MODEL,
    ).strip()
    if str(payload.get("model") or "").strip() != configured_model:
        raise HTTPException(status_code=400, detail="Unsupported internal rerank model.")
    query = payload.get("query")
    documents = payload.get("documents")
    if not isinstance(query, str) or not query.strip():
        raise HTTPException(status_code=400, detail="Internal rerank query must not be empty.")
    if not isinstance(documents, list) or not documents or not all(isinstance(item, str) and item.strip() for item in documents):
        raise HTTPException(status_code=400, detail="Internal rerank documents must contain non-empty strings.")

    data = await _post_internal_retrieval_model(
        upstream_url=os.getenv(
            "CNOOC_RERANK_UPSTREAM_URL",
            _DEFAULT_CNOOC_RERANK_UPSTREAM_URL,
        ).strip(),
        upstream_key=upstream_key,
        payload={"query": query, "documents": documents},
        label="Internal rerank",
    )
    results = data.get("results")
    if not isinstance(results, list):
        raise HTTPException(status_code=502, detail="Internal rerank returned an invalid result list.")
    for result in results:
        if not isinstance(result, dict) or not isinstance(result.get("index"), int) or not isinstance(result.get("relevance_score"), (int, float)):
            raise HTTPException(status_code=502, detail="Internal rerank returned an invalid result.")
    return JSONResponse(content=data)


@router.post("/bailian/v1/audio/transcriptions")
async def bailian_audio_transcription(
    request: Request,
    file: UploadFile = File(...),
    model: str = Form(default=_DEFAULT_BAILIAN_ASR_MODEL),
    language: str | None = Form(default=None),
    response_format: str | None = Form(default=None),
) -> dict[str, Any]:
    """Translate WeKnora's OpenAI transcription request to Bailian Qwen-ASR.

    WeKnora 0.7.0 sends multipart ``/audio/transcriptions`` requests, while
    Bailian's Qwen3-ASR-Flash uses an OpenAI-compatible chat-completions body
    with an ``input_audio`` part. Keeping this adapter in Gateway preserves
    WeKnora's upstream model implementation and its normal model-selection UI.
    """

    del response_format  # We always return the verbose-compatible envelope.
    api_key = _require_bailian_adapter_key(request)
    max_bytes = max(1, _int_env("WEKNORA_BAILIAN_ASR_MAX_BYTES", _DEFAULT_BAILIAN_ASR_MAX_BYTES))
    content = await file.read(max_bytes + 1)
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded audio file is empty.")
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"Audio file exceeds the {max_bytes}-byte limit.")

    configured_model = os.getenv("ASR_MODEL_NAME", _DEFAULT_BAILIAN_ASR_MODEL).strip()
    requested_model = model.strip() or configured_model
    if requested_model != configured_model:
        raise HTTPException(status_code=400, detail="Unsupported Bailian ASR model.")

    media_type = _audio_media_type(file)
    data_uri = f"data:{media_type};base64,{base64.b64encode(content).decode('ascii')}"
    asr_options: dict[str, Any] = {"enable_itn": True}
    language_value = language.strip() if isinstance(language, str) else ""
    if language_value:
        asr_options["language"] = language_value
    payload = {
        "model": requested_model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {"data": data_uri},
                    }
                ],
            }
        ],
        "stream": False,
        "asr_options": asr_options,
    }
    upstream_url = os.getenv(
        "WEKNORA_BAILIAN_ASR_UPSTREAM_URL",
        _DEFAULT_BAILIAN_ASR_UPSTREAM_URL,
    ).strip()
    timeout = max(1.0, _float_env("WEKNORA_BAILIAN_ASR_TIMEOUT_SECONDS", 300.0))
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            verify=_bailian_tls_verify(),
        ) as client:
            response = await client.post(
                upstream_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
            )
    except httpx.RequestError as exc:
        logger.warning("Bailian ASR request failed.", exc_info=True)
        raise HTTPException(status_code=502, detail="Bailian speech recognition is unavailable.") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Bailian ASR returned a non-JSON response.") from exc
    if response.status_code >= 400:
        logger.warning("Bailian ASR returned HTTP %s.", response.status_code)
        raise HTTPException(status_code=502, detail=f"Bailian ASR returned HTTP {response.status_code}.")

    choices = data.get("choices") if isinstance(data, dict) else None
    message = choices[0].get("message") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None
    transcript = _transcript_text(message.get("content") if isinstance(message, dict) else None)
    if not transcript:
        raise HTTPException(status_code=502, detail="Bailian ASR returned an empty transcript.")

    detected_language = language_value
    annotations = message.get("annotations") if isinstance(message, dict) else None
    if not detected_language and isinstance(annotations, list):
        for annotation in annotations:
            if isinstance(annotation, dict) and annotation.get("language"):
                detected_language = str(annotation["language"])
                break
    usage = data.get("usage") if isinstance(data, dict) else None
    duration = usage.get("seconds", 0) if isinstance(usage, dict) else 0
    return {
        "text": transcript,
        "language": detected_language,
        "duration": duration,
        "segments": [],
    }


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
    return JSONResponse(
        session.as_login_response(ui_policy=build_weknora_ui_policy(user)),
        headers=_sso_cors_headers(request),
    )

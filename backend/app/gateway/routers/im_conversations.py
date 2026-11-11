"""Read-only IM conversation routes backing the Web sidebar tree.

All six endpoints are GETs. Feishu keeps owning the "current conversation"
pointer; this module only reads it, so opening history in the Web UI can never
move what the next Feishu message continues.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.gateway.authz import require_permission
from app.gateway.deps import get_authorized_read_scope_user_id, get_current_user
from app.gateway.im_conversations import (
    MAX_GROUP_THREADS,
    ImConversationService,
    im_session_tree_enabled,
)

router = APIRouter(prefix="/api/im", tags=["im-conversations"])


class ImCapabilitiesResponse(BaseModel):
    im_session_tree_enabled: bool = Field(..., description="Whether the Web IM session tree should render")
    provider: str = "feishu"
    max_group_threads: int = MAX_GROUP_THREADS
    search_enabled: bool = False


class ImThreadItem(BaseModel):
    thread_id: str
    title: str | None = None
    channel_type: str
    chat_id: str
    chat_name: str | None = None
    last_active_at: str | None = None
    is_current: bool = False
    status: str


class ImPrivateSection(BaseModel):
    threads: list[ImThreadItem] = Field(default_factory=list)
    total: int = 0


class ImGroupNode(BaseModel):
    chat_id: str
    name: str | None = None
    name_source: str = "UNKNOWN"
    chat_status: str = "UNKNOWN"
    thread_count: int = 0
    last_active_at: str | None = None
    current_thread_id: str | None = None
    threads: list[ImThreadItem] = Field(default_factory=list)


class SessionTreeResponse(BaseModel):
    private: ImPrivateSection
    groups: list[ImGroupNode] = Field(default_factory=list)
    groups_total: int = 0


class ThreadPageResponse(BaseModel):
    threads: list[ImThreadItem] = Field(default_factory=list)
    next_cursor: str | None = None


class SearchResponse(BaseModel):
    results: list[ImThreadItem] = Field(default_factory=list)
    limit: int = 20
    offset: int = 0


class ThreadOriginResponse(BaseModel):
    thread_id: str
    owner_user_id: str
    channel_type: str
    chat_id: str
    chat_name: str | None = None
    chat_status: str | None = None
    status: str
    is_current: bool = False
    last_active_at: str | None = None
    thread_exists: bool = True


def _service() -> ImConversationService:
    from deerflow.persistence import get_session_factory
    from deerflow.persistence.im_routing import (
        ImGroupCatalogRepository,
        ImRoutingRepository,
        ImThreadRegistryRepository,
    )

    session_factory = get_session_factory()
    if session_factory is None:
        raise HTTPException(status_code=503, detail="IM conversation store not available")
    return ImConversationService(
        registry_repo=ImThreadRegistryRepository(session_factory),
        catalog_repo=ImGroupCatalogRepository(session_factory),
        routing_repo=ImRoutingRepository(session_factory),
        enabled=im_session_tree_enabled(),
    )


async def _scope_user_id(request: Request) -> str:
    """Resolve the read scope; IM data is never fetched for an anonymous call."""
    scope = await get_authorized_read_scope_user_id(request)
    if scope is None:
        scope = await get_current_user(request)
    if not scope:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return str(scope)


@router.get("/capabilities", response_model=ImCapabilitiesResponse)
async def get_capabilities(request: Request) -> ImCapabilitiesResponse:
    await _scope_user_id(request)
    enabled = im_session_tree_enabled()
    return ImCapabilitiesResponse(im_session_tree_enabled=enabled, search_enabled=enabled)


@router.get("/session-tree", response_model=SessionTreeResponse)
async def get_session_tree(
    request: Request,
    groups_limit: int = Query(50, ge=1, le=200),
    threads_per_group: int = Query(8, ge=1, le=50),
    group_offset: int = Query(0, ge=0),
) -> SessionTreeResponse:
    owner_user_id = await _scope_user_id(request)
    payload = await _service().session_tree(
        owner_user_id=owner_user_id,
        groups_limit=groups_limit,
        threads_per_group=threads_per_group,
        group_offset=group_offset,
    )
    return SessionTreeResponse(**payload)


@router.get("/groups/{chat_id}/threads", response_model=ThreadPageResponse)
async def get_group_threads(
    request: Request,
    chat_id: str,
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = None,
) -> ThreadPageResponse:
    owner_user_id = await _scope_user_id(request)
    payload = await _service().group_threads(
        owner_user_id=owner_user_id,
        chat_id=chat_id,
        limit=limit,
        cursor=cursor,
    )
    return ThreadPageResponse(**payload)


@router.get("/threads", response_model=ThreadPageResponse)
async def get_private_threads(
    request: Request,
    scope: Literal["private"] = "private",
    limit: int = Query(30, ge=1, le=100),
    cursor: str | None = None,
) -> ThreadPageResponse:
    owner_user_id = await _scope_user_id(request)
    payload = await _service().private_threads(owner_user_id=owner_user_id, limit=limit, cursor=cursor)
    return ThreadPageResponse(**payload)


# NOTE: /threads/search MUST stay declared before /threads/{thread_id}/origin.
# FastAPI matches routes in declaration order, so the dynamic segment would
# otherwise swallow "search".
@router.get("/threads/search", response_model=SearchResponse)
async def search_threads(
    request: Request,
    q: str = Query(..., min_length=1, max_length=100),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
) -> SearchResponse:
    owner_user_id = await _scope_user_id(request)
    payload = await _service().search_threads(
        owner_user_id=owner_user_id,
        query=q,
        limit=limit,
        offset=offset,
    )
    return SearchResponse(**payload)


@router.get("/threads/{thread_id}/origin", response_model=ThreadOriginResponse)
@require_permission("threads", "read", owner_check=True)
async def get_thread_origin(request: Request, thread_id: str) -> ThreadOriginResponse:
    payload = await _service().thread_origin(thread_id=thread_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="Thread has no IM origin")
    return ThreadOriginResponse(**payload)

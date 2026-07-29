"""Tests for DeerFlow's WeKnora proxy response enrichment."""

import asyncio
import io
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.gateway.routers import weknora


class _BinaryResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        content: bytes = b"image-bytes",
        content_type: str = "image/png",
    ):
        self.status_code = status_code
        self.content = content
        self.headers = {"content-type": content_type}


class _BinaryClient:
    def __init__(self, response: _BinaryResponse, captured: dict[str, object], **kwargs):
        self.response = response
        self.captured = captured
        self.captured["client_kwargs"] = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, **kwargs):
        self.captured.update({"url": url, **kwargs})
        return self.response


@pytest.mark.asyncio
async def test_proxy_knowledge_image_uses_user_session_and_resolved_tenant(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_context(user, knowledge_base_id, requested_tenant_id):
        assert knowledge_base_id == "kb-1"
        assert requested_tenant_id is None
        return "user-token", "tenant-1"

    monkeypatch.setattr(weknora, "_resolve_knowledge_base_access_context", fake_context)
    monkeypatch.setattr(
        weknora,
        "weknora_endpoint",
        lambda path: f"http://weknora.test/api/v1{path}",
    )
    monkeypatch.setattr(weknora, "weknora_timeout", lambda: 10.0)
    monkeypatch.setattr(
        weknora.httpx,
        "AsyncClient",
        lambda **kwargs: _BinaryClient(_BinaryResponse(), captured, **kwargs),
    )

    response = await weknora.get_knowledge_file(
        "kb-1",
        file_path="resource://image_handle-1",
        tenant_id=None,
        user=SimpleNamespace(),
    )

    assert response.status_code == 200
    assert response.body == b"image-bytes"
    assert response.media_type == "image/png"
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert captured["url"] == "http://weknora.test/api/v1/knowledge-bases/kb-1/files"
    assert captured["params"] == {"file_path": "resource://image_handle-1"}
    assert captured["headers"]["Authorization"] == "Bearer user-token"
    assert captured["headers"]["X-Tenant-ID"] == "tenant-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "file_path",
    [
        "https://example.com/image.png",
        "resource://",
        "resource://../../secret",
        "resource://image/child",
    ],
)
async def test_proxy_knowledge_image_rejects_untrusted_resource_paths(file_path):
    with pytest.raises(HTTPException) as exc_info:
        await weknora.get_knowledge_file(
            "kb-1",
            file_path=file_path,
            tenant_id=None,
            user=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_proxy_knowledge_image_rejects_non_image_response(monkeypatch):
    async def fake_context(user, knowledge_base_id, requested_tenant_id):
        return "user-token", "tenant-1"

    monkeypatch.setattr(weknora, "_resolve_knowledge_base_access_context", fake_context)
    monkeypatch.setattr(weknora, "weknora_timeout", lambda: 10.0)
    monkeypatch.setattr(
        weknora,
        "weknora_endpoint",
        lambda path: f"http://weknora.test/api/v1{path}",
    )
    monkeypatch.setattr(
        weknora.httpx,
        "AsyncClient",
        lambda **kwargs: _BinaryClient(
            _BinaryResponse(content=b"document", content_type="application/pdf"),
            {},
            **kwargs,
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await weknora.get_knowledge_file(
            "kb-1",
            file_path="resource://image_handle-1",
            tenant_id=None,
            user=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 415


@pytest.mark.asyncio
async def test_proxy_raw_resource_uses_weknora_root_files_endpoint(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_session(user):
        return SimpleNamespace(token="user-token", tenant={"id": "tenant-1"})

    monkeypatch.setattr(
        weknora,
        "ensure_weknora_session_for_deerflow_user",
        fake_session,
    )
    monkeypatch.setattr(
        weknora,
        "weknora_endpoint",
        lambda path: f"http://weknora.test/api/v1{path}",
    )
    monkeypatch.setattr(weknora, "weknora_timeout", lambda: 10.0)
    monkeypatch.setattr(
        weknora.httpx,
        "AsyncClient",
        lambda **kwargs: _BinaryClient(_BinaryResponse(), captured, **kwargs),
    )

    response = await weknora.get_active_tenant_file(
        file_path="resource://image_handle-1",
        user=SimpleNamespace(),
    )

    assert response.status_code == 200
    assert captured["url"] == "http://weknora.test/files"
    assert captured["headers"]["Authorization"] == "Bearer user-token"
    assert captured["headers"]["X-Tenant-ID"] == "tenant-1"


@pytest.mark.asyncio
async def test_upload_pptx_selects_configured_mineru_parser(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_token(user):
        return "token"

    async def fake_request(method, path, access_token, **kwargs):
        captured.update({"method": method, "path": path, **kwargs})
        return {"success": True}

    monkeypatch.setattr(weknora, "_current_weknora_token", fake_token)
    monkeypatch.setattr(weknora, "_request", fake_request)
    monkeypatch.setenv("WEKNORA_PRESENTATION_PARSER_ENGINE", "mineru")
    monkeypatch.setenv("WEKNORA_MINERU_ENDPOINT", "http://deer-flow-mineru-adapter:50052")
    monkeypatch.setenv("WEKNORA_MINERU_SCANNED_PDF_FALLBACK", "true")

    response = await weknora.upload_knowledge_file(
        "kb-1",
        UploadFile(filename="Quarterly Review.PPTX", file=io.BytesIO(b"pptx")),
        tenant_id="tenant-1",
        user=SimpleNamespace(),
    )

    assert response == {"success": True}
    assert captured["tenant_id"] == "tenant-1"
    assert json.loads(captured["data"]["process_config"]) == {
        "parser_engine_rules": [
            {
                "file_types": ["pdf", "doc", "docx", "ppt", "pptx"],
                "engine": "mineru",
            }
        ],
        "parser_engine_overrides": {"mineru_endpoint": "http://deer-flow-mineru-adapter:50052"},
    }


@pytest.mark.asyncio
async def test_upload_pdf_uses_hybrid_mineru_adapter_when_fallback_enabled(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_token(user):
        return "token"

    async def fake_request(method, path, access_token, **kwargs):
        captured.update(kwargs)
        return {"success": True}

    monkeypatch.setattr(weknora, "_current_weknora_token", fake_token)
    monkeypatch.setattr(weknora, "_request", fake_request)
    monkeypatch.setenv("WEKNORA_PRESENTATION_PARSER_ENGINE", "mineru")
    monkeypatch.setenv("WEKNORA_MINERU_ENDPOINT", "http://deer-flow-mineru-adapter:50052")
    monkeypatch.setenv("WEKNORA_MINERU_SCANNED_PDF_FALLBACK", "true")

    await weknora.upload_knowledge_file(
        "kb-1",
        UploadFile(filename="report.pdf", file=io.BytesIO(b"%PDF-test")),
        user=SimpleNamespace(),
    )

    assert json.loads(captured["data"]["process_config"]) == {
        "parser_engine_rules": [
            {
                "file_types": ["pdf", "doc", "docx", "ppt", "pptx"],
                "engine": "mineru",
            }
        ],
        "parser_engine_overrides": {"mineru_endpoint": "http://deer-flow-mineru-adapter:50052"},
    }


@pytest.mark.asyncio
async def test_upload_word_uses_visual_document_adapter(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_token(user):
        return "token"

    async def fake_request(method, path, access_token, **kwargs):
        captured.update(kwargs)
        return {"success": True}

    monkeypatch.setattr(weknora, "_current_weknora_token", fake_token)
    monkeypatch.setattr(weknora, "_request", fake_request)
    monkeypatch.setenv("WEKNORA_PRESENTATION_PARSER_ENGINE", "mineru")
    monkeypatch.setenv("WEKNORA_MINERU_ENDPOINT", "http://deer-flow-mineru-adapter:50052")
    monkeypatch.setenv("WEKNORA_MINERU_DOCUMENT_VISUAL_FALLBACK", "true")
    await weknora.upload_knowledge_file(
        "kb-1",
        UploadFile(filename="notes.docx", file=io.BytesIO(b"docx")),
        user=SimpleNamespace(),
    )

    assert json.loads(captured["data"]["process_config"]) == {
        "parser_engine_rules": [
            {
                "file_types": ["pdf", "doc", "docx", "ppt", "pptx"],
                "engine": "mineru",
            }
        ],
        "parser_engine_overrides": {"mineru_endpoint": "http://deer-flow-mineru-adapter:50052"},
    }


@pytest.mark.asyncio
async def test_upload_non_visual_document_preserves_kb_parser_defaults(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_token(user):
        return "token"

    async def fake_request(method, path, access_token, **kwargs):
        captured.update(kwargs)
        return {"success": True}

    monkeypatch.setattr(weknora, "_current_weknora_token", fake_token)
    monkeypatch.setattr(weknora, "_request", fake_request)
    await weknora.upload_knowledge_file(
        "kb-1",
        UploadFile(filename="notes.txt", file=io.BytesIO(b"text")),
        user=SimpleNamespace(),
    )

    assert "process_config" not in captured["data"]


@pytest.mark.asyncio
async def test_created_kb_persists_presentation_parser_rule(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_token(user):
        return "token"

    async def fake_request(method, path, access_token, **kwargs):
        captured.update(kwargs)
        return {"success": True}

    monkeypatch.setattr(weknora, "_current_weknora_token", fake_token)
    monkeypatch.setattr(weknora, "_request", fake_request)
    monkeypatch.setenv("WEKNORA_PRESENTATION_PARSER_ENGINE", "mineru")
    monkeypatch.setenv("WEKNORA_MINERU_SCANNED_PDF_FALLBACK", "true")

    await weknora.create_knowledge_base(
        weknora.WeKnoraKnowledgeBaseCreate(name="KB", description="test"),
        user=SimpleNamespace(),
    )

    assert captured["json"]["chunking_config"]["parser_engine_rules"] == [
        {
            "file_types": ["pdf", "doc", "docx", "ppt", "pptx"],
            "engine": "mineru",
        }
    ]


def test_presentation_parser_falls_back_to_markitdown_for_local_development(monkeypatch):
    monkeypatch.delenv("WEKNORA_PRESENTATION_PARSER_ENGINE", raising=False)

    assert weknora._presentation_parser_rules() == [{"file_types": ["ppt", "pptx"], "engine": "markitdown"}]


@pytest.mark.asyncio
async def test_enrich_knowledge_base_counts_from_weknora_lists(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def fake_request(method, path, access_token, **kwargs):
        calls.append((method, path))
        if path == "/knowledge-bases/kb-1/knowledge":
            return {
                "success": True,
                "total": 2,
                "data": [
                    {"id": "doc-1", "parse_status": "completed"},
                    {"id": "doc-2", "parse_status": "processing"},
                ],
            }
        if path == "/chunks/doc-1":
            return {"success": True, "total": 3, "data": []}
        if path == "/chunks/doc-2":
            return {"success": True, "total": 1, "data": []}
        raise AssertionError(path)

    monkeypatch.setattr(weknora, "_request", fake_request)
    items = [{"id": "kb-1", "name": "Team KB", "knowledge_count": 0, "chunk_count": 0}]

    await weknora._enrich_knowledge_base_counts(items, "token")

    assert items == [
        {
            "id": "kb-1",
            "name": "Team KB",
            "knowledge_count": 2,
            "chunk_count": 4,
            "processing_count": 1,
            "is_processing": True,
        }
    ]
    assert "/knowledge-bases/kb-1/knowledge" in [path for _, path in calls]


@pytest.mark.asyncio
async def test_enrich_knowledge_base_counts_keeps_list_on_count_failure(monkeypatch):
    async def fake_request(method, path, access_token, **kwargs):
        raise HTTPException(status_code=502, detail="boom")

    monkeypatch.setattr(weknora, "_request", fake_request)
    items = [{"id": "kb-1", "name": "Team KB", "knowledge_count": 0, "chunk_count": 0}]

    await weknora._enrich_knowledge_base_counts(items, "token")

    assert items == [{"id": "kb-1", "name": "Team KB", "knowledge_count": 0, "chunk_count": 0}]


@pytest.mark.asyncio
async def test_enrich_knowledge_base_counts_trusts_complete_upstream_counts(monkeypatch):
    async def fail_request(*args, **kwargs):
        raise AssertionError("complete list rows must not trigger per-document requests")

    monkeypatch.setattr(weknora, "_request", fail_request)
    items = [
        {
            "id": "kb-1",
            "name": "Team KB",
            "knowledge_count": 12,
            "chunk_count": 34,
            "processing_count": 0,
            "is_processing": False,
        }
    ]

    await weknora._enrich_knowledge_base_counts(items, "token")

    assert items[0]["knowledge_count"] == 12
    assert items[0]["chunk_count"] == 34


@pytest.mark.asyncio
async def test_list_knowledge_bases_returns_when_count_enrichment_times_out(monkeypatch):
    async def fake_session(user):
        return SimpleNamespace(token="token", tenant={"id": "tenant-1"})

    async def fake_request(method, path, access_token, **kwargs):
        if path == "/auth/me":
            return {
                "success": True,
                "data": {
                    "user": {"id": "user-1"},
                    "memberships": [{"tenant_id": "tenant-1", "role": "owner"}],
                },
            }
        if path == "/knowledge-bases":
            return {
                "success": True,
                "total": 1,
                "data": [{"id": "kb-1", "name": "Team KB", "creator_id": "user-1"}],
            }
        if path == "/shared-knowledge-bases":
            return {"success": True, "total": 0, "data": []}
        raise AssertionError(path)

    async def slow_enrichment(items, access_token):
        await asyncio.sleep(1)

    monkeypatch.setattr(weknora, "ensure_weknora_session_for_deerflow_user", fake_session)
    monkeypatch.setattr(weknora, "_request", fake_request)
    monkeypatch.setattr(weknora, "_enrich_knowledge_base_counts", slow_enrichment)
    monkeypatch.setenv("WEKNORA_KB_COUNT_TIMEOUT_SECONDS", "0.01")

    response = await weknora.list_knowledge_bases(user=SimpleNamespace())

    assert response["total"] == 1
    assert response["items"][0]["id"] == "kb-1"


def test_float_env_uses_default_for_invalid_values(monkeypatch):
    monkeypatch.setenv("WEKNORA_KB_COUNT_TIMEOUT_SECONDS", "not-a-number")

    assert weknora._float_env("WEKNORA_KB_COUNT_TIMEOUT_SECONDS", 5.0) == 5.0


@pytest.mark.asyncio
async def test_list_knowledge_bases_aggregates_live_memberships_and_classifies_access(monkeypatch):
    calls: list[tuple[str, str, str | None]] = []

    async def fake_session(user):
        return SimpleNamespace(
            token="token",
            user={"id": "user-a"},
            tenant={"id": 100},
            # Deliberately stale: the live /auth/me response below contains a
            # second membership that must still be included.
            memberships=[{"tenant_id": 100, "role": "owner"}],
        )

    async def fake_request(method, path, access_token, *, tenant_id=None, **kwargs):
        calls.append((method, path, str(tenant_id) if tenant_id is not None else None))
        if path == "/auth/me":
            return {
                "success": True,
                "data": {
                    "user": {"id": "user-a"},
                    "memberships": [
                        {"tenant_id": 100, "tenant_name": "A", "role": "owner"},
                        {"tenant_id": 200, "tenant_name": "B", "role": "viewer"},
                    ],
                },
            }
        if path == "/knowledge-bases" and str(tenant_id) == "100":
            return {
                "success": True,
                "data": [
                    {
                        "id": "kb-owned",
                        "tenant_id": 100,
                        "creator_id": "user-a",
                        "share_count": 1,
                    }
                ],
            }
        if path == "/knowledge-bases" and str(tenant_id) == "200":
            return {
                "success": True,
                "data": [
                    {
                        "id": "kb-joined",
                        "tenant_id": 200,
                        "creator_id": "user-b",
                        "share_count": 0,
                    }
                ],
            }
        if path == "/shared-knowledge-bases" and str(tenant_id) == "100":
            return {"success": True, "data": [], "total": 0}
        if path == "/shared-knowledge-bases" and str(tenant_id) == "200":
            return {
                "success": True,
                "data": [
                    {
                        "knowledge_base": {
                            "id": "kb-team",
                            "tenant_id": 300,
                            "creator_id": "user-c",
                        },
                        "share_id": "share-1",
                        "organization_id": "org-1",
                        "org_name": "Team",
                        "source_tenant_id": 300,
                        "permission": "viewer",
                    }
                ],
                "total": 1,
            }
        raise AssertionError((method, path, tenant_id))

    async def no_count_enrichment(items, access_token):
        return None

    monkeypatch.setattr(weknora, "ensure_weknora_session_for_deerflow_user", fake_session)
    monkeypatch.setattr(weknora, "_request", fake_request)
    monkeypatch.setattr(weknora, "_enrich_knowledge_base_counts", no_count_enrichment)

    response = await weknora.list_knowledge_bases(user=SimpleNamespace())

    assert {item["id"] for item in response["items"]} == {"kb-owned", "kb-joined", "kb-team"}
    by_id = {item["id"]: item for item in response["items"]}
    assert by_id["kb-owned"]["is_owner"] is True
    assert by_id["kb-owned"]["is_joined"] is False
    assert by_id["kb-owned"]["is_team_or_public"] is True
    assert by_id["kb-joined"]["is_owner"] is False
    assert by_id["kb-joined"]["is_joined"] is True
    assert by_id["kb-joined"]["is_team_or_public"] is False
    assert by_id["kb-team"]["is_joined"] is True
    assert by_id["kb-team"]["is_team_or_public"] is True
    assert by_id["kb-team"]["owner_tenant_id"] == "300"
    assert by_id["kb-team"]["access_tenant_id"] == "200"
    assert response["total"] == 3
    assert response["owned_total"] == 1
    assert response["joined_total"] == 2
    assert response["team_total"] == 2
    assert ("GET", "/knowledge-bases", "100") in calls
    assert ("GET", "/knowledge-bases", "200") in calls


@pytest.mark.asyncio
async def test_resolve_knowledge_base_access_context_keeps_explicit_tenant(monkeypatch):
    async def fake_session(user):
        return SimpleNamespace(token="token", tenant={"id": 10000})

    async def fail_live_identity(session):
        raise AssertionError("explicit tenant must not trigger membership discovery")

    monkeypatch.setattr(weknora, "ensure_weknora_session_for_deerflow_user", fake_session)
    monkeypatch.setattr(weknora, "_live_weknora_identity", fail_live_identity)

    token, tenant_id = await weknora._resolve_knowledge_base_access_context(
        SimpleNamespace(),
        "kb-1",
        requested_tenant_id="20000",
    )

    assert token == "token"
    assert tenant_id == "20000"


@pytest.mark.asyncio
async def test_resolve_knowledge_base_access_context_finds_owned_tenant(monkeypatch):
    calls: list[tuple[str, str, str | None]] = []

    async def fake_session(user):
        return SimpleNamespace(token="token", tenant={"id": 10001})

    async def fake_live_identity(session):
        return (
            {"id": "user-1"},
            [
                {"tenant_id": 10001, "role": "owner"},
                {"tenant_id": 10000, "role": "owner"},
            ],
        )

    async def fake_request(method, path, access_token, *, tenant_id=None, **kwargs):
        calls.append((method, path, str(tenant_id) if tenant_id is not None else None))
        if path == "/knowledge-bases" and str(tenant_id) == "10000":
            return {"success": True, "data": [{"id": "kb-1"}]}
        return {"success": True, "data": [], "total": 0}

    monkeypatch.setattr(weknora, "ensure_weknora_session_for_deerflow_user", fake_session)
    monkeypatch.setattr(weknora, "_live_weknora_identity", fake_live_identity)
    monkeypatch.setattr(weknora, "_request", fake_request)

    token, tenant_id = await weknora._resolve_knowledge_base_access_context(
        SimpleNamespace(),
        "kb-1",
        requested_tenant_id=None,
    )

    assert token == "token"
    assert tenant_id == "10000"
    assert ("GET", "/knowledge-bases", "10000") in calls
    assert ("GET", "/shared-knowledge-bases", "10000") in calls


@pytest.mark.asyncio
async def test_resolve_knowledge_base_access_context_uses_consumer_tenant_for_shared_kb(monkeypatch):
    async def fake_session(user):
        return SimpleNamespace(token="token", tenant={"id": 10000})

    async def fake_live_identity(session):
        return (
            {"id": "user-1"},
            [
                {"tenant_id": 10000, "role": "owner"},
                {"tenant_id": 20000, "role": "viewer"},
            ],
        )

    async def fake_request(method, path, access_token, *, tenant_id=None, **kwargs):
        if path == "/shared-knowledge-bases" and str(tenant_id) == "20000":
            return {
                "success": True,
                "data": [
                    {
                        "knowledge_base": {"id": "kb-shared", "tenant_id": 30000},
                        "source_tenant_id": 30000,
                    }
                ],
            }
        return {"success": True, "data": [], "total": 0}

    monkeypatch.setattr(weknora, "ensure_weknora_session_for_deerflow_user", fake_session)
    monkeypatch.setattr(weknora, "_live_weknora_identity", fake_live_identity)
    monkeypatch.setattr(weknora, "_request", fake_request)

    token, tenant_id = await weknora._resolve_knowledge_base_access_context(
        SimpleNamespace(),
        "kb-shared",
        requested_tenant_id=None,
    )

    assert token == "token"
    assert tenant_id == "20000"


@pytest.mark.asyncio
async def test_list_knowledge_resolves_missing_tenant_before_upstream_request(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_context(user, knowledge_base_id, requested_tenant_id):
        assert knowledge_base_id == "kb-1"
        assert requested_tenant_id is None
        return "token", "tenant-1"

    async def fake_request(method, path, access_token, **kwargs):
        captured.update(
            {
                "method": method,
                "path": path,
                "access_token": access_token,
                **kwargs,
            }
        )
        return {"success": True, "data": [{"id": "doc-1"}], "total": 1}

    monkeypatch.setattr(weknora, "_resolve_knowledge_base_access_context", fake_context)
    monkeypatch.setattr(weknora, "_request", fake_request)

    response = await weknora.list_knowledge("kb-1", tenant_id=None, user=SimpleNamespace())

    assert response["items"] == [{"id": "doc-1"}]
    assert captured["tenant_id"] == "tenant-1"

"""Tests for DeerFlow's WeKnora proxy response enrichment."""

import asyncio
import io
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from app.gateway.routers import weknora


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

    response = await weknora.upload_knowledge_file(
        "kb-1",
        UploadFile(filename="Quarterly Review.PPTX", file=io.BytesIO(b"pptx")),
        tenant_id="tenant-1",
        user=SimpleNamespace(),
    )

    assert response == {"success": True}
    assert captured["tenant_id"] == "tenant-1"
    assert json.loads(captured["data"]["process_config"]) == {
        "parser_engine_rules": [{"file_types": ["ppt", "pptx"], "engine": "mineru"}],
        "parser_engine_overrides": {"mineru_endpoint": "http://deer-flow-mineru-adapter:50052"},
    }


@pytest.mark.asyncio
async def test_upload_non_presentation_preserves_kb_parser_defaults(monkeypatch):
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
        UploadFile(filename="notes.docx", file=io.BytesIO(b"docx")),
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

    await weknora.create_knowledge_base(
        weknora.WeKnoraKnowledgeBaseCreate(name="KB", description="test"),
        user=SimpleNamespace(),
    )

    assert captured["json"]["chunking_config"]["parser_engine_rules"] == [{"file_types": ["ppt", "pptx"], "engine": "mineru"}]


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

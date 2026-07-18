"""Tests for DeerFlow's WeKnora proxy response enrichment."""

import pytest
from fastapi import HTTPException

from app.gateway.routers import weknora


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
    assert ("/knowledge-bases/kb-1/knowledge" in [path for _, path in calls])


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

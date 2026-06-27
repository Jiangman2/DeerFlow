from __future__ import annotations

from collections.abc import Callable
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import UploadFile
from langchain_core.messages import HumanMessage

from app.gateway.routers import knowledge as knowledge_router
from deerflow.agents.middlewares.knowledge_middleware import KnowledgeMiddleware
from deerflow.config.knowledge_config import KnowledgeConfig
from deerflow.knowledge import (
    copy_knowledge_file,
    create_knowledge_folder,
    delete_knowledge_file,
    index_knowledge_file,
    list_knowledge_files,
    list_knowledge_folders,
    move_knowledge_file,
    reindex_knowledge_file,
    rename_knowledge_folder,
    search_knowledge,
)
from deerflow.knowledge import service as knowledge_service
from deerflow.persistence.engine import close_engine, init_engine
from deerflow.tools.builtins.search_knowledge_tool import search_knowledge_tool

pytestmark = pytest.mark.asyncio


def _call_unwrapped(decorated: Callable, /, *args, **kwargs):
    fn = decorated
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn(*args, **kwargs)


async def _init_test_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path / "home"))
    await close_engine()
    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'knowledge.db'}", sqlite_dir=str(tmp_path))


async def test_index_search_delete_and_user_isolation(tmp_path, monkeypatch):
    await _init_test_db(tmp_path, monkeypatch)
    try:
        source = tmp_path / "roadmap.md"
        source.write_text("# Alpha Plan\nThe launch code is private-rag-omega.\n", encoding="utf-8")

        file = await index_knowledge_file(
            user_id="user-a",
            source_path=source,
            original_filename="roadmap.md",
            content_type="text/markdown",
        )

        assert file.status == "indexed"
        assert file.chunk_count == 1
        assert file.folder_path == ""
        assert file.display_path == "roadmap.md"

        duplicate = await index_knowledge_file(
            user_id="user-a",
            source_path=source,
            original_filename="roadmap-copy.md",
            content_type="text/markdown",
        )
        assert duplicate.id == file.id
        assert len(await list_knowledge_files("user-a")) == 1

        results = await search_knowledge("user-a", "private rag omega", limit=3)
        assert len(results) == 1
        assert results[0].filename == "roadmap.md"
        assert "private-rag-omega" in results[0].snippet

        assert await search_knowledge("user-b", "private rag omega", limit=3) == []

        reindexed = await reindex_knowledge_file("user-a", file.id)
        assert reindexed is not None
        assert reindexed.status == "indexed"
        assert reindexed.chunk_count == 1

        assert await delete_knowledge_file("user-a", file.id) is True
        assert await list_knowledge_files("user-a") == []
        assert await search_knowledge("user-a", "private rag omega", limit=3) == []
    finally:
        await close_engine()


async def test_folder_paths_and_recursive_folder_delete(tmp_path, monkeypatch):
    await _init_test_db(tmp_path, monkeypatch)
    try:
        source = tmp_path / "brief.md"
        source.write_text("The folder marker is private-rag-folder.", encoding="utf-8")

        first = await index_knowledge_file(
            user_id="user-a",
            source_path=source,
            original_filename="Projects/Alpha/brief.md",
            content_type="text/markdown",
        )
        second = await index_knowledge_file(
            user_id="user-a",
            source_path=source,
            original_filename="Projects/Beta/brief.md",
            content_type="text/markdown",
        )

        assert first.id != second.id
        assert first.folder_path == "Projects/Alpha"
        assert first.display_path == "Projects/Alpha/brief.md"
        assert second.folder_path == "Projects/Beta"

        results = await search_knowledge("user-a", "private rag folder", limit=5)
        assert {result.filename for result in results} == {
            "Projects/Alpha/brief.md",
            "Projects/Beta/brief.md",
        }

        scoped_results = await search_knowledge(
            "user-a",
            "private rag folder",
            limit=5,
            scope_paths=["Projects/Alpha"],
        )
        assert [result.filename for result in scoped_results] == ["Projects/Alpha/brief.md"]

        file_scoped_results = await search_knowledge(
            "user-a",
            "private rag folder",
            limit=5,
            scope_paths=["Projects/Beta/brief.md"],
        )
        assert [result.filename for result in file_scoped_results] == ["Projects/Beta/brief.md"]

        deleted_count = await knowledge_service.delete_knowledge_folder("user-a", "Projects")
        assert deleted_count == 2
        assert await list_knowledge_files("user-a") == []
    finally:
        await close_engine()


async def test_knowledge_file_manager_operations(tmp_path, monkeypatch):
    await _init_test_db(tmp_path, monkeypatch)
    try:
        source = tmp_path / "brief.md"
        source.write_text("The file manager marker is private-rag-manager.", encoding="utf-8")

        folder = await create_knowledge_folder("user-a", "Inbox/Raw")
        assert folder.path == "Inbox/Raw"
        assert {item.path for item in await list_knowledge_folders("user-a")} >= {"Inbox", "Inbox/Raw"}

        file = await index_knowledge_file(
            user_id="user-a",
            source_path=source,
            original_filename="Inbox/Raw/brief.md",
            content_type="text/markdown",
        )

        moved = await move_knowledge_file(
            "user-a",
            file.id,
            target_folder_path="Projects/Alpha",
            target_filename="renamed.md",
        )
        assert moved is not None
        assert moved.display_path == "Projects/Alpha/renamed.md"

        results = await search_knowledge("user-a", "private rag manager", limit=5)
        assert [result.filename for result in results] == ["Projects/Alpha/renamed.md"]

        copied = await copy_knowledge_file(
            "user-a",
            file.id,
            target_folder_path="Projects/Beta",
            target_filename="copy.md",
        )
        assert copied is not None
        assert copied.id != file.id
        assert copied.display_path == "Projects/Beta/copy.md"

        renamed_folder = await rename_knowledge_folder("user-a", "Projects/Beta", "BetaRenamed")
        assert renamed_folder is not None
        assert renamed_folder.path == "Projects/BetaRenamed"
        files = await list_knowledge_files("user-a")
        assert {item.display_path for item in files} == {
            "Projects/Alpha/renamed.md",
            "Projects/BetaRenamed/copy.md",
        }
    finally:
        await close_engine()


async def test_semantic_search_finds_non_keyword_match(tmp_path, monkeypatch):
    await _init_test_db(tmp_path, monkeypatch)

    config = KnowledgeConfig(
        semantic_search_enabled=True,
        embedding_model="fake-embedding",
        embedding_dimensions=3,
        embedding_api_key="test-key",
        semantic_candidates=10,
        keyword_candidates=10,
    )

    async def fake_embed_texts(texts: list[str], config=None):  # noqa: ARG001
        vectors = []
        for text in texts:
            text_l = text.lower()
            if "needle-query" in text_l or "banana" in text_l:
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([1.0, 0.0, 0.0])
        return vectors

    monkeypatch.setattr(knowledge_service, "_get_knowledge_config", lambda: config)
    monkeypatch.setattr(knowledge_service, "_embed_texts", fake_embed_texts)

    try:
        apple = tmp_path / "apple.md"
        apple.write_text("This file talks about crisp apples.", encoding="utf-8")
        banana = tmp_path / "banana.md"
        banana.write_text("This file talks about yellow bananas.", encoding="utf-8")

        await index_knowledge_file(
            user_id="user-a",
            source_path=apple,
            original_filename="apple.md",
            content_type="text/markdown",
        )
        await index_knowledge_file(
            user_id="user-a",
            source_path=banana,
            original_filename="banana.md",
            content_type="text/markdown",
        )

        results = await search_knowledge("user-a", "needle-query", limit=1)

        assert len(results) == 1
        assert results[0].filename == "banana.md"
    finally:
        await close_engine()


async def test_unsupported_file_is_recorded_as_failed(tmp_path, monkeypatch):
    await _init_test_db(tmp_path, monkeypatch)
    try:
        source = tmp_path / "archive.bin"
        source.write_bytes(b"\x00\x01\x02")

        file = await index_knowledge_file(
            user_id="user-a",
            source_path=source,
            original_filename="archive.bin",
            content_type="application/octet-stream",
        )

        assert file.status == "failed"
        assert file.chunk_count == 0
        assert "Unsupported knowledge file type" in (file.error or "")
    finally:
        await close_engine()


async def test_knowledge_router_upload_search_reindex_delete(tmp_path, monkeypatch):
    await _init_test_db(tmp_path, monkeypatch)
    try:
        upload = UploadFile(
            filename="Reports/2026/notes.txt",
            file=BytesIO(b"Q3 budget includes private-rag-router."),
        )
        uploaded = await _call_unwrapped(
            knowledge_router.upload_knowledge_files,
            request=MagicMock(),
            files=[upload],
        )

        assert uploaded.success is True
        assert uploaded.files[0].status == "indexed"
        assert uploaded.files[0].folder_path == "Reports/2026"
        assert uploaded.files[0].display_path == "Reports/2026/notes.txt"

        search = await _call_unwrapped(
            knowledge_router.search_knowledge_endpoint,
            knowledge_router.KnowledgeSearchRequest(query="private-rag-router", limit=5),
            request=MagicMock(),
        )
        assert search.count == 1
        assert search.results[0].filename == "Reports/2026/notes.txt"

        reindexed = await _call_unwrapped(
            knowledge_router.reindex_knowledge_file,
            uploaded.files[0].id,
            request=MagicMock(),
        )
        assert reindexed.status == "indexed"

        deleted = await _call_unwrapped(
            knowledge_router.delete_knowledge_folder_endpoint,
            "Reports",
            request=MagicMock(),
        )
        assert deleted.success is True
        assert deleted.deleted_count == 1
    finally:
        await close_engine()


async def test_knowledge_middleware_injects_private_context(tmp_path, monkeypatch):
    await _init_test_db(tmp_path, monkeypatch)
    try:
        source = tmp_path / "brief.md"
        source.write_text("The customer codename is private-rag-brief.", encoding="utf-8")
        await index_knowledge_file(
            user_id="user-a",
            source_path=source,
            original_filename="brief.md",
            content_type="text/markdown",
        )

        middleware = KnowledgeMiddleware(limit=3)
        state = {"messages": [HumanMessage(content="What is the customer codename?")]}
        runtime = SimpleNamespace(context={"user_id": "user-a"})

        update = await middleware.abefore_agent(state, runtime)

        assert update is not None
        message = update["messages"][-1]
        assert "<personal_knowledge>" in message.content
        assert "private-rag-brief" in message.content
        assert message.additional_kwargs["original_user_content"] == "What is the customer codename?"
    finally:
        await close_engine()


async def test_knowledge_middleware_respects_scope_paths(tmp_path, monkeypatch):
    await _init_test_db(tmp_path, monkeypatch)
    try:
        alpha = tmp_path / "alpha.md"
        alpha.write_text("The scoped codename is alpha-scope-only.", encoding="utf-8")
        beta = tmp_path / "beta.md"
        beta.write_text("The scoped codename is beta-scope-only.", encoding="utf-8")
        await index_knowledge_file(
            user_id="user-a",
            source_path=alpha,
            original_filename="Projects/Alpha/brief.md",
            content_type="text/markdown",
        )
        await index_knowledge_file(
            user_id="user-a",
            source_path=beta,
            original_filename="Projects/Beta/brief.md",
            content_type="text/markdown",
        )

        middleware = KnowledgeMiddleware(limit=3)
        state = {"messages": [HumanMessage(content="What is the scoped codename?")]}
        runtime = SimpleNamespace(context={"user_id": "user-a", "knowledge_scope_paths": ["Projects/Beta"]})

        update = await middleware.abefore_agent(state, runtime)

        assert update is not None
        message = update["messages"][-1]
        assert "beta-scope-only" in message.content
        assert "alpha-scope-only" not in message.content
    finally:
        await close_engine()


async def test_knowledge_middleware_loads_selected_documents_without_query_match(tmp_path, monkeypatch):
    await _init_test_db(tmp_path, monkeypatch)
    monkeypatch.setattr(
        knowledge_service,
        "_get_knowledge_config",
        lambda: KnowledgeConfig(semantic_search_enabled=False),
    )
    try:
        source = tmp_path / "manual.md"
        source.write_text("zxqv-alpha-passphrase.", encoding="utf-8")
        await index_knowledge_file(
            user_id="user-a",
            source_path=source,
            original_filename="Manuals/manual.md",
            content_type="text/markdown",
        )

        middleware = KnowledgeMiddleware(limit=3)
        state = {"messages": [HumanMessage(content="Explain onboarding plan")]}

        unscoped_update = await middleware.abefore_agent(state, SimpleNamespace(context={"user_id": "user-a"}))
        assert unscoped_update is None

        scoped_update = await middleware.abefore_agent(
            state,
            SimpleNamespace(context={"user_id": "user-a", "knowledge_scope_paths": ["Manuals/manual.md"]}),
        )

        assert scoped_update is not None
        message = scoped_update["messages"][-1]
        assert "Selected documents from the current user's private knowledge base" in message.content
        assert "zxqv-alpha-passphrase" in message.content
        assert scoped_update["knowledge_documents"][0]["filename"] == "Manuals/manual.md"
    finally:
        await close_engine()


async def test_knowledge_middleware_skips_when_private_knowledge_disabled(tmp_path, monkeypatch):
    await _init_test_db(tmp_path, monkeypatch)
    try:
        source = tmp_path / "brief.md"
        source.write_text("The disabled codename is private-rag-off.", encoding="utf-8")
        await index_knowledge_file(
            user_id="user-a",
            source_path=source,
            original_filename="brief.md",
            content_type="text/markdown",
        )

        middleware = KnowledgeMiddleware(limit=3)
        state = {"messages": [HumanMessage(content="What is the disabled codename?")]}
        runtime = SimpleNamespace(context={"user_id": "user-a", "use_private_knowledge": False})

        update = await middleware.abefore_agent(state, runtime)

        assert update is None
    finally:
        await close_engine()


async def test_search_knowledge_tool_uses_runtime_user_id(tmp_path, monkeypatch):
    await _init_test_db(tmp_path, monkeypatch)
    try:
        source = tmp_path / "tool.md"
        source.write_text("The tool-only marker is private-rag-tool.", encoding="utf-8")
        await index_knowledge_file(
            user_id="tool-user",
            source_path=source,
            original_filename="tool.md",
            content_type="text/markdown",
        )

        runtime = SimpleNamespace(context={"user_id": "tool-user"})
        result = await search_knowledge_tool.coroutine(runtime, "private-rag-tool", 5)

        assert "Private knowledge search results" in result
        assert "private-rag-tool" in result
    finally:
        await close_engine()

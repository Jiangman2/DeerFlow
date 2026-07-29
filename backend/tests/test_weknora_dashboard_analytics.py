from datetime import UTC, datetime

import pytest

from app.gateway.weknora_analytics import (
    _knowledge_base_conversation_sql,
    _knowledge_conversation_summary_sql,
    _load_weknora_dashboard_analytics,
)


def test_per_message_scope_takes_priority_with_session_fallback():
    query = _knowledge_base_conversation_sql(
        {
            "messages": {"execution_context", "mentioned_items"},
            "sessions": {"agent_config"},
        }
    )

    assert query is not None
    assert "m.execution_context" in query
    assert "m.mentioned_items" in query
    assert "s.agent_config" in query
    assert "AND NOT (" in query


def test_knowledge_conversation_summary_counts_sessions_and_questions_separately():
    query = _knowledge_conversation_summary_sql(
        {
            "messages": {"mentioned_items"},
            "sessions": {"agent_config"},
        }
    )

    assert query is not None
    assert "count(DISTINCT ds.session_id)" in query
    assert "count(DISTINCT ds.message_id)" in query
    assert "dashboard:knowledge-conversation-summary" in query


class _FakeConnection:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.query_args: list[tuple[object, ...]] = []

    async def fetchrow(self, query: str, *_args):
        self.queries.append(query)
        self.query_args.append(_args)
        if "dashboard:knowledge-conversation-summary" in query:
            return {
                "current_conversation_sessions": 3,
                "conversation_sessions": 4,
                "current_question_turns": 11,
                "question_turns": 13,
            }
        assert "dashboard:summary" in query
        return {
            "current_users": 6,
            "registered_users": 2,
            "current_knowledge_bases": 3,
            "created_knowledge_bases": 2,
            "current_documents": 12,
            "uploaded_documents": 7,
            "current_conversation_sessions": 4,
            "conversation_sessions": 5,
            "current_question_turns": 14,
            "question_turns": 18,
            "current_shared_spaces": 2,
            "created_shared_spaces": 1,
            "current_shared_space_members": 7,
            "joined_shared_space_members": 3,
            "current_shared_knowledge_bases": 4,
            "shared_knowledge_bases": 2,
            "current_shared_documents": 16,
            "uploaded_shared_documents": 6,
            "pending_shared_space_requests": 1,
            "shared_space_join_requests": 4,
        }

    async def fetch(self, query: str, *_args):
        self.queries.append(query)
        self.query_args.append(_args)
        if "dashboard:file-formats" in query:
            return [
                {
                    "format": "pdf",
                    "current_count": 9,
                    "uploaded_count": 4,
                },
                {
                    "format": "docx",
                    "current_count": 2,
                    "uploaded_count": 2,
                },
                {
                    "format": "png",
                    "current_count": 1,
                    "uploaded_count": 1,
                },
            ]
        if "dashboard:knowledge-bases" in query:
            return [
                {
                    "knowledge_base_id": "kb-1",
                    "name": "勘探制度",
                    "creator_email": "user@example.com",
                    "creator_name": "User",
                    "current_documents": 8,
                    "uploaded_documents": 10,
                    "format": "pdf",
                    "current_count": 5,
                    "uploaded_count": 6,
                },
                {
                    "knowledge_base_id": "kb-1",
                    "name": "勘探制度",
                    "creator_email": "user@example.com",
                    "creator_name": "User",
                    "current_documents": 8,
                    "uploaded_documents": 10,
                    "format": "docx",
                    "current_count": 3,
                    "uploaded_count": 4,
                },
            ]
        if "dashboard:users" in query:
            return [
                {
                    "weknora_user_id": "wk-user-1",
                    "email": "user@example.com",
                    "name": "User",
                    "current_knowledge_bases": 2,
                    "created_knowledge_bases": 1,
                    "current_documents": 8,
                    "uploaded_documents": 3,
                    "conversation_sessions": 4,
                    "question_turns": 12,
                }
            ]
        if "dashboard:schema-capabilities" in query:
            return [
                {"table_name": "sessions", "column_name": "agent_config"},
                {
                    "table_name": "messages",
                    "column_name": "execution_context",
                },
                {"table_name": "messages", "column_name": "mentioned_items"},
            ]
        if "dashboard:knowledge-base-conversations" in query:
            return [
                {
                    "knowledge_base_id": "kb-1",
                    "current_conversation_sessions": 3,
                    "conversation_sessions": 4,
                    "current_question_turns": 11,
                    "question_turns": 13,
                }
            ]
        if "dashboard:shared-spaces" in query:
            return [
                {
                    "shared_space_id": "org-1",
                    "name": "勘探协作空间",
                    "description": "跨部门共享",
                    "created_at": datetime(2026, 7, 2, tzinfo=UTC),
                    "owner_email": "owner@example.com",
                    "owner_name": "Owner",
                    "current_members": 5,
                    "joined_members": 2,
                    "admins": 1,
                    "editors": 3,
                    "viewers": 1,
                    "current_knowledge_bases": 3,
                    "shared_knowledge_bases": 2,
                    "current_documents": 12,
                    "uploaded_documents": 5,
                    "pending_requests": 1,
                    "join_requests": 3,
                }
            ]
        raise AssertionError(query)


@pytest.mark.asyncio
async def test_load_weknora_dashboard_analytics_includes_inventory_history_and_formats():
    connection = _FakeConnection()
    start = datetime(2026, 7, 1, tzinfo=UTC)
    end = datetime(2026, 7, 31, tzinfo=UTC)

    result = await _load_weknora_dashboard_analytics(
        connection,
        start=start,
        end=end,
        limit=10,
    )

    assert result["available"] is True
    assert result["current_users"] == 6
    assert result["registered_users"] == 2
    assert result["current_knowledge_bases"] == 3
    assert result["created_knowledge_bases"] == 2
    assert result["current_documents"] == 12
    assert result["uploaded_documents"] == 7
    assert result["current_conversation_sessions"] == 3
    assert result["conversation_sessions"] == 4
    assert result["current_question_turns"] == 11
    assert result["question_turns"] == 13
    assert result["current_shared_spaces"] == 2
    assert result["created_shared_spaces"] == 1
    assert result["current_shared_space_members"] == 7
    assert result["joined_shared_space_members"] == 3
    assert result["current_shared_knowledge_bases"] == 4
    assert result["shared_knowledge_bases"] == 2
    assert result["current_shared_documents"] == 16
    assert result["uploaded_shared_documents"] == 6
    assert result["pending_shared_space_requests"] == 1
    assert result["shared_space_join_requests"] == 4
    assert result["file_formats"] == [
        {"format": "pdf", "current_count": 9, "uploaded_count": 4},
        {"format": "docx", "current_count": 2, "uploaded_count": 2},
        {"format": "png", "current_count": 1, "uploaded_count": 1},
    ]
    assert result["knowledge_bases"] == [
        {
            "knowledge_base_id": "kb-1",
            "name": "勘探制度",
            "creator_email": "user@example.com",
            "creator_name": "User",
            "current_documents": 8,
            "uploaded_documents": 10,
            "current_conversation_sessions": 3,
            "conversation_sessions": 4,
            "current_question_turns": 11,
            "question_turns": 13,
            "file_formats": [
                {
                    "format": "pdf",
                    "current_count": 5,
                    "uploaded_count": 6,
                },
                {
                    "format": "docx",
                    "current_count": 3,
                    "uploaded_count": 4,
                },
            ],
        }
    ]
    assert result["users"][0]["conversation_sessions"] == 4
    assert result["users"][0]["question_turns"] == 12
    assert result["shared_spaces"] == [
        {
            "shared_space_id": "org-1",
            "name": "勘探协作空间",
            "description": "跨部门共享",
            "created_at": datetime(2026, 7, 2, tzinfo=UTC),
            "owner_email": "owner@example.com",
            "owner_name": "Owner",
            "current_members": 5,
            "joined_members": 2,
            "admins": 1,
            "editors": 3,
            "viewers": 1,
            "current_knowledge_bases": 3,
            "shared_knowledge_bases": 2,
            "current_documents": 12,
            "uploaded_documents": 5,
            "pending_requests": 1,
            "join_requests": 3,
        }
    ]
    assert any("dashboard:summary" in query for query in connection.queries)
    assert any("dashboard:file-formats" in query for query in connection.queries)
    assert any("dashboard:knowledge-bases" in query for query in connection.queries)
    assert any("dashboard:knowledge-base-conversations" in query for query in connection.queries)
    assert any("dashboard:knowledge-conversation-summary" in query for query in connection.queries)
    assert any("dashboard:users" in query for query in connection.queries)
    assert any("dashboard:shared-spaces" in query for query in connection.queries)
    users_query = next(query for query in connection.queries if "dashboard:users" in query)
    assert "LIMIT" not in users_query
    assert "u.deleted_at IS NULL" in users_query
    assert "COALESCE(qs.question_turns, 0)" in users_query

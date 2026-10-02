"""Read-only WeKnora usage analytics for DeerFlow's admin dashboard."""

from __future__ import annotations

import logging
from collections import OrderedDict
from datetime import datetime
from typing import Any

from app.gateway.weknora_identity import (
    _connect_weknora_database,
    _weknora_database_is_configured,
)

logger = logging.getLogger(__name__)

_FORMAT_SQL = """
CASE
    WHEN COALESCE(file_type, '') <> ''
        THEN lower(trim(both '.' from file_type))
    WHEN COALESCE(file_name, '') LIKE '%.%'
        THEN lower(regexp_replace(file_name, '^.*\\.', ''))
    ELSE 'other'
END
"""

_EMPTY_COUNTS = {
    "current_users": 0,
    "registered_users": 0,
    "current_knowledge_bases": 0,
    "created_knowledge_bases": 0,
    "current_documents": 0,
    "uploaded_documents": 0,
    "current_conversation_sessions": 0,
    "conversation_sessions": 0,
    "current_question_turns": 0,
    "question_turns": 0,
    "current_shared_spaces": 0,
    "created_shared_spaces": 0,
    "current_shared_space_members": 0,
    "joined_shared_space_members": 0,
    "current_shared_knowledge_bases": 0,
    "shared_knowledge_bases": 0,
    "current_shared_documents": 0,
    "uploaded_shared_documents": 0,
    "pending_shared_space_requests": 0,
    "shared_space_join_requests": 0,
}


def _integer(row: Any, key: str) -> int:
    value = row.get(key) if hasattr(row, "get") else row[key]
    return int(value or 0)


def _json_array(expression: str) -> str:
    return "CASE WHEN jsonb_typeof(" + expression + ") = 'array' THEN " + expression + " ELSE '[]'::jsonb END"


def _knowledge_message_scope_union(columns: dict[str, set[str]]) -> str | None:
    message_columns = columns.get("messages", set())
    session_columns = columns.get("sessions", set())
    scope_selects: list[str] = []
    per_message_scope_checks: list[str] = []

    if "execution_context" in message_columns:
        execution_context = "COALESCE(m.execution_context, '{}'::jsonb)"
        execution_scopes = _json_array(f"{execution_context}->'knowledge_base_ids'")
        scope_selects.append(
            f"""
            SELECT
                m.id::text AS message_id,
                m.session_id,
                scope.knowledge_base_id
            FROM messages m
            CROSS JOIN LATERAL jsonb_array_elements_text(
                {execution_scopes}
            ) AS scope(knowledge_base_id)
            WHERE m.role = 'user'
            """
        )
        per_message_scope_checks.append(
            f"""
            EXISTS (
                SELECT 1
                FROM jsonb_array_elements_text({execution_scopes}) AS explicit_scope(value)
                WHERE COALESCE(explicit_scope.value, '') <> ''
            )
            """
        )

    if "mentioned_items" in message_columns:
        mentioned_items = "COALESCE(m.mentioned_items, '[]'::jsonb)"
        mentioned_array = _json_array(mentioned_items)
        mentioned_knowledge_base_id = """
            COALESCE(
                NULLIF(item.value->>'kb_id', ''),
                CASE
                    WHEN item.value->>'type' = 'kb'
                        THEN NULLIF(item.value->>'id', '')
                END
            )
        """
        scope_selects.append(
            f"""
            SELECT
                m.id::text AS message_id,
                m.session_id,
                {mentioned_knowledge_base_id} AS knowledge_base_id
            FROM messages m
            CROSS JOIN LATERAL jsonb_array_elements(
                {mentioned_array}
            ) AS item(value)
            WHERE m.role = 'user'
            """
        )
        per_message_scope_checks.append(
            f"""
            EXISTS (
                SELECT 1
                FROM jsonb_array_elements({mentioned_array}) AS item(value)
                WHERE {mentioned_knowledge_base_id} IS NOT NULL
            )
            """
        )

    # WeKnora 0.7.0 exposes per-message scope columns but does not always
    # populate them. Fall back to the session's current scope only for messages
    # that have no explicit per-message scope, so explicit history still wins
    # when a user switches knowledge bases during a conversation.
    if "agent_config" in session_columns:
        agent_config = "COALESCE(s.agent_config, '{}'::jsonb)"
        explicit_scope_guard = " AND NOT (" + " OR ".join(per_message_scope_checks) + ")" if per_message_scope_checks else ""
        scope_selects.append(
            f"""
            SELECT
                m.id::text AS message_id,
                m.session_id,
                scope.knowledge_base_id
            FROM messages m
            JOIN sessions s ON s.id = m.session_id
            CROSS JOIN LATERAL jsonb_array_elements_text(
                {_json_array(f"{agent_config}->'knowledge_base_ids'")}
            ) AS scope(knowledge_base_id)
            WHERE m.role = 'user'
              {explicit_scope_guard}
            """
        )

    if not scope_selects:
        return None

    return "\nUNION\n".join(scope_selects)


def _knowledge_base_conversation_sql(columns: dict[str, set[str]]) -> str | None:
    """Build a version-tolerant query for per-KB native WeKnora conversations."""

    scope_union = _knowledge_message_scope_union(columns)
    if not scope_union:
        return None
    return f"""
        /* dashboard:knowledge-base-conversations */
        WITH message_scopes AS (
            {scope_union}
        ),
        deduplicated_scopes AS (
            SELECT DISTINCT message_id, session_id, knowledge_base_id
            FROM message_scopes
            WHERE COALESCE(knowledge_base_id, '') <> ''
        )
        SELECT
            ds.knowledge_base_id,
            count(DISTINCT ds.session_id) FILTER (
                WHERE s.deleted_at IS NULL AND m.deleted_at IS NULL
            ) AS current_conversation_sessions,
            count(DISTINCT ds.session_id) FILTER (
                WHERE s.created_at >= $1 AND s.created_at <= $2
            ) AS conversation_sessions,
            count(*) FILTER (
                WHERE s.deleted_at IS NULL AND m.deleted_at IS NULL
            ) AS current_question_turns,
            count(*) FILTER (
                WHERE m.created_at >= $1 AND m.created_at <= $2
            ) AS question_turns
        FROM deduplicated_scopes ds
        JOIN messages m ON m.id::text = ds.message_id
        JOIN sessions s ON s.id = ds.session_id
        GROUP BY ds.knowledge_base_id
    """


def _knowledge_conversation_summary_sql(
    columns: dict[str, set[str]],
) -> str | None:
    """Build aggregate session/question totals limited to knowledge-base use."""

    scope_union = _knowledge_message_scope_union(columns)
    if not scope_union:
        return None
    return f"""
        /* dashboard:knowledge-conversation-summary */
        WITH message_scopes AS (
            {scope_union}
        ),
        deduplicated_scopes AS (
            SELECT DISTINCT message_id, session_id
            FROM message_scopes
            WHERE COALESCE(knowledge_base_id, '') <> ''
        )
        SELECT
            count(DISTINCT ds.session_id) FILTER (
                WHERE s.deleted_at IS NULL AND m.deleted_at IS NULL
            ) AS current_conversation_sessions,
            count(DISTINCT ds.session_id) FILTER (
                WHERE s.created_at >= $1 AND s.created_at <= $2
            ) AS conversation_sessions,
            count(DISTINCT ds.message_id) FILTER (
                WHERE s.deleted_at IS NULL AND m.deleted_at IS NULL
            ) AS current_question_turns,
            count(DISTINCT ds.message_id) FILTER (
                WHERE m.created_at >= $1 AND m.created_at <= $2
            ) AS question_turns
        FROM deduplicated_scopes ds
        JOIN messages m ON m.id::text = ds.message_id
        JOIN sessions s ON s.id = ds.session_id
    """


async def _load_weknora_dashboard_analytics(
    connection: Any,
    *,
    start: datetime,
    end: datetime,
    limit: int,
) -> dict[str, Any]:
    """Load inventory and append-only-by-soft-delete metrics from WeKnora."""

    summary = await connection.fetchrow(
        """
        /* dashboard:summary */
        SELECT
            (
                SELECT count(*)
                FROM users
                WHERE deleted_at IS NULL
            ) AS current_users,
            (
                SELECT count(*)
                FROM users
                WHERE created_at >= $1 AND created_at <= $2
            ) AS registered_users,
            (
                SELECT count(*)
                FROM knowledge_bases
                WHERE deleted_at IS NULL
            ) AS current_knowledge_bases,
            (
                SELECT count(*)
                FROM knowledge_bases
                WHERE created_at >= $1 AND created_at <= $2
            ) AS created_knowledge_bases,
            (
                SELECT count(*)
                FROM knowledges k
                JOIN knowledge_bases kb ON kb.id = k.knowledge_base_id
                WHERE k.deleted_at IS NULL AND kb.deleted_at IS NULL
            ) AS current_documents,
            (
                SELECT count(*)
                FROM knowledges
                WHERE created_at >= $1 AND created_at <= $2
            ) AS uploaded_documents,
            (
                SELECT count(*)
                FROM sessions
                WHERE deleted_at IS NULL
            ) AS current_conversation_sessions,
            (
                SELECT count(*)
                FROM sessions
                WHERE created_at >= $1 AND created_at <= $2
            ) AS conversation_sessions,
            (
                SELECT count(*)
                FROM messages m
                JOIN sessions s ON s.id = m.session_id
                WHERE m.role = 'user'
                  AND m.deleted_at IS NULL
                  AND s.deleted_at IS NULL
            ) AS current_question_turns,
            (
                SELECT count(*)
                FROM messages
                WHERE role = 'user'
                  AND created_at >= $1 AND created_at <= $2
            ) AS question_turns,
            (
                SELECT count(*)
                FROM organizations
                WHERE deleted_at IS NULL
            ) AS current_shared_spaces,
            (
                SELECT count(*)
                FROM organizations
                WHERE created_at >= $1 AND created_at <= $2
            ) AS created_shared_spaces,
            (
                SELECT count(*)
                FROM organization_tenant_members otm
                JOIN organizations o ON o.id = otm.organization_id
                WHERE o.deleted_at IS NULL
            ) AS current_shared_space_members,
            (
                SELECT count(*)
                FROM organization_tenant_members
                WHERE joined_at >= $1 AND joined_at <= $2
            ) AS joined_shared_space_members,
            (
                SELECT count(DISTINCT ks.knowledge_base_id)
                FROM kb_shares ks
                JOIN organizations o ON o.id = ks.organization_id
                JOIN knowledge_bases kb ON kb.id = ks.knowledge_base_id
                WHERE ks.deleted_at IS NULL
                  AND o.deleted_at IS NULL
                  AND kb.deleted_at IS NULL
            ) AS current_shared_knowledge_bases,
            (
                SELECT count(DISTINCT knowledge_base_id)
                FROM kb_shares
                WHERE created_at >= $1 AND created_at <= $2
            ) AS shared_knowledge_bases,
            (
                SELECT count(DISTINCT k.id)
                FROM kb_shares ks
                JOIN organizations o ON o.id = ks.organization_id
                JOIN knowledge_bases kb ON kb.id = ks.knowledge_base_id
                JOIN knowledges k ON k.knowledge_base_id = kb.id
                WHERE ks.deleted_at IS NULL
                  AND o.deleted_at IS NULL
                  AND kb.deleted_at IS NULL
                  AND k.deleted_at IS NULL
            ) AS current_shared_documents,
            (
                SELECT count(DISTINCT k.id)
                FROM kb_shares ks
                JOIN knowledges k ON k.knowledge_base_id = ks.knowledge_base_id
                WHERE k.created_at >= $1 AND k.created_at <= $2
            ) AS uploaded_shared_documents,
            (
                SELECT count(*)
                FROM organization_join_requests ojr
                JOIN organizations o ON o.id = ojr.organization_id
                WHERE lower(ojr.status) = 'pending'
                  AND o.deleted_at IS NULL
            ) AS pending_shared_space_requests,
            (
                SELECT count(*)
                FROM organization_join_requests
                WHERE created_at >= $1 AND created_at <= $2
            ) AS shared_space_join_requests
        """,
        start,
        end,
    )

    format_rows = await connection.fetch(
        f"""
        /* dashboard:file-formats */
        SELECT
            {_FORMAT_SQL} AS format,
            count(*) FILTER (
                WHERE k.deleted_at IS NULL AND kb.deleted_at IS NULL
            ) AS current_count,
            count(*) FILTER (
                WHERE k.created_at >= $1 AND k.created_at <= $2
            ) AS uploaded_count
        FROM knowledges k
        JOIN knowledge_bases kb ON kb.id = k.knowledge_base_id
        GROUP BY 1
        HAVING
            count(*) FILTER (
                WHERE k.deleted_at IS NULL AND kb.deleted_at IS NULL
            ) > 0
            OR count(*) FILTER (
                WHERE k.created_at >= $1 AND k.created_at <= $2
            ) > 0
        ORDER BY uploaded_count DESC, current_count DESC, format ASC
        """,
        start,
        end,
    )

    knowledge_base_rows = await connection.fetch(
        f"""
        /* dashboard:knowledge-bases */
        WITH document_totals AS (
            SELECT
                knowledge_base_id,
                count(*) FILTER (WHERE deleted_at IS NULL) AS current_documents,
                count(*) FILTER (
                    WHERE created_at >= $1 AND created_at <= $2
                ) AS uploaded_documents
            FROM knowledges
            GROUP BY knowledge_base_id
        ),
        document_formats AS (
            SELECT
                knowledge_base_id,
                {_FORMAT_SQL} AS format,
                count(*) FILTER (
                    WHERE deleted_at IS NULL
                ) AS current_count,
                count(*) FILTER (
                    WHERE created_at >= $1 AND created_at <= $2
                ) AS uploaded_count
            FROM knowledges
            GROUP BY knowledge_base_id, 2
        ),
        ranked_bases AS (
            SELECT
                kb.id,
                kb.name,
                kb.creator_id,
                COALESCE(dt.current_documents, 0) AS current_documents,
                COALESCE(dt.uploaded_documents, 0) AS uploaded_documents
            FROM knowledge_bases kb
            LEFT JOIN document_totals dt ON dt.knowledge_base_id = kb.id
            WHERE kb.deleted_at IS NULL
            ORDER BY COALESCE(dt.uploaded_documents, 0) DESC, kb.created_at DESC
            LIMIT $3
        )
        SELECT
            rb.id::text AS knowledge_base_id,
            rb.name,
            lower(u.email) AS creator_email,
            COALESCE(NULLIF(u.username, ''), u.email, rb.creator_id::text) AS creator_name,
            rb.current_documents,
            rb.uploaded_documents,
            df.format,
            COALESCE(df.current_count, 0) AS current_count,
            COALESCE(df.uploaded_count, 0) AS uploaded_count
        FROM ranked_bases rb
        LEFT JOIN users u ON u.id = rb.creator_id
        LEFT JOIN document_formats df ON df.knowledge_base_id = rb.id
        ORDER BY
            rb.uploaded_documents DESC,
            rb.name ASC,
            df.uploaded_count DESC
        """,
        start,
        end,
        limit,
    )

    schema_rows = await connection.fetch(
        """
        /* dashboard:schema-capabilities */
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name IN ('sessions', 'messages')
        """
    )
    columns: dict[str, set[str]] = {}
    for row in schema_rows:
        columns.setdefault(str(row["table_name"]), set()).add(str(row["column_name"]))
    per_knowledge_base_conversations: dict[str, dict[str, int]] = {}
    conversation_sql = _knowledge_base_conversation_sql(columns)
    if conversation_sql:
        conversation_rows = await connection.fetch(conversation_sql, start, end)
        per_knowledge_base_conversations = {
            str(row["knowledge_base_id"]): {
                "current_conversation_sessions": _integer(
                    row,
                    "current_conversation_sessions",
                ),
                "conversation_sessions": _integer(row, "conversation_sessions"),
                "current_question_turns": _integer(
                    row,
                    "current_question_turns",
                ),
                "question_turns": _integer(row, "question_turns"),
            }
            for row in conversation_rows
        }

    knowledge_conversation_counts = {
        "current_conversation_sessions": _integer(
            summary,
            "current_conversation_sessions",
        ),
        "conversation_sessions": _integer(summary, "conversation_sessions"),
        "current_question_turns": _integer(summary, "current_question_turns"),
        "question_turns": _integer(summary, "question_turns"),
    }
    conversation_summary_sql = _knowledge_conversation_summary_sql(columns)
    if conversation_summary_sql:
        conversation_summary = await connection.fetchrow(
            conversation_summary_sql,
            start,
            end,
        )
        knowledge_conversation_counts = {key: _integer(conversation_summary, key) for key in knowledge_conversation_counts}

    scope_union = _knowledge_message_scope_union(columns)
    if not scope_union:
        scope_union = """
            SELECT
                NULL::text AS message_id,
                NULL::text AS session_id,
                NULL::text AS knowledge_base_id
            WHERE FALSE
        """
    user_rows = await connection.fetch(
        f"""
        /* dashboard:users */
        WITH message_scopes AS (
            {scope_union}
        ),
        deduplicated_scopes AS (
            SELECT DISTINCT message_id, session_id
            FROM message_scopes
            WHERE COALESCE(knowledge_base_id, '') <> ''
        ),
        knowledge_activity AS (
            SELECT DISTINCT
                m.id::text AS message_id,
                s.id::text AS session_id,
                s.user_id,
                m.created_at AS message_created_at,
                s.created_at AS session_created_at
            FROM deduplicated_scopes ds
            JOIN messages m ON m.id::text = ds.message_id
            JOIN sessions s ON s.id = ds.session_id
        ),
        knowledge_base_stats AS (
            SELECT
                creator_id,
                count(*) FILTER (WHERE deleted_at IS NULL) AS current_knowledge_bases,
                count(*) FILTER (
                    WHERE created_at >= $1 AND created_at <= $2
                ) AS created_knowledge_bases
            FROM knowledge_bases
            GROUP BY creator_id
        ),
        document_stats AS (
            SELECT
                kb.creator_id,
                count(*) FILTER (
                    WHERE k.deleted_at IS NULL AND kb.deleted_at IS NULL
                ) AS current_documents,
                count(*) FILTER (
                    WHERE k.created_at >= $1 AND k.created_at <= $2
                ) AS uploaded_documents
            FROM knowledges k
            JOIN knowledge_bases kb ON kb.id = k.knowledge_base_id
            GROUP BY kb.creator_id
        ),
        session_stats AS (
            SELECT
                user_id,
                count(DISTINCT session_id) FILTER (
                    WHERE session_created_at >= $1
                      AND session_created_at <= $2
                ) AS conversation_sessions
            FROM knowledge_activity
            GROUP BY user_id
        ),
        question_stats AS (
            SELECT
                user_id,
                count(DISTINCT message_id) FILTER (
                    WHERE message_created_at >= $1
                      AND message_created_at <= $2
                ) AS question_turns
            FROM knowledge_activity
            GROUP BY user_id
        )
        SELECT
            u.id::text AS weknora_user_id,
            lower(u.email) AS email,
            COALESCE(NULLIF(u.username, ''), u.email, u.id::text) AS name,
            COALESCE(kb.current_knowledge_bases, 0) AS current_knowledge_bases,
            COALESCE(kb.created_knowledge_bases, 0) AS created_knowledge_bases,
            COALESCE(ds.current_documents, 0) AS current_documents,
            COALESCE(ds.uploaded_documents, 0) AS uploaded_documents,
            COALESCE(ss.conversation_sessions, 0) AS conversation_sessions,
            COALESCE(qs.question_turns, 0) AS question_turns
        FROM users u
        LEFT JOIN knowledge_base_stats kb ON kb.creator_id = u.id
        LEFT JOIN document_stats ds ON ds.creator_id = u.id
        LEFT JOIN session_stats ss ON ss.user_id = u.id::text
        LEFT JOIN question_stats qs ON qs.user_id = u.id::text
        WHERE u.deleted_at IS NULL
          AND (
              COALESCE(kb.current_knowledge_bases, 0)
              + COALESCE(kb.created_knowledge_bases, 0)
              + COALESCE(ds.current_documents, 0)
              + COALESCE(ds.uploaded_documents, 0)
              + COALESCE(ss.conversation_sessions, 0)
              + COALESCE(qs.question_turns, 0)
          ) > 0
        ORDER BY
            COALESCE(qs.question_turns, 0) DESC,
            COALESCE(ds.uploaded_documents, 0) DESC,
            lower(u.email) ASC
        """,
        start,
        end,
    )

    shared_space_rows = await connection.fetch(
        """
        /* dashboard:shared-spaces */
        WITH member_stats AS (
            SELECT
                organization_id,
                count(*) AS current_members,
                count(*) FILTER (
                    WHERE joined_at >= $1 AND joined_at <= $2
                ) AS joined_members,
                count(*) FILTER (WHERE lower(role) = 'admin') AS admins,
                count(*) FILTER (WHERE lower(role) = 'editor') AS editors,
                count(*) FILTER (WHERE lower(role) = 'viewer') AS viewers
            FROM organization_tenant_members
            GROUP BY organization_id
        ),
        share_stats AS (
            SELECT
                ks.organization_id,
                count(DISTINCT ks.knowledge_base_id) FILTER (
                    WHERE ks.deleted_at IS NULL
                      AND kb.deleted_at IS NULL
                ) AS current_knowledge_bases,
                count(DISTINCT ks.knowledge_base_id) FILTER (
                    WHERE ks.created_at >= $1 AND ks.created_at <= $2
                ) AS shared_knowledge_bases,
                count(DISTINCT k.id) FILTER (
                    WHERE ks.deleted_at IS NULL
                      AND kb.deleted_at IS NULL
                      AND k.deleted_at IS NULL
                ) AS current_documents,
                count(DISTINCT k.id) FILTER (
                    WHERE k.created_at >= $1 AND k.created_at <= $2
                ) AS uploaded_documents
            FROM kb_shares ks
            LEFT JOIN knowledge_bases kb ON kb.id = ks.knowledge_base_id
            LEFT JOIN knowledges k ON k.knowledge_base_id = ks.knowledge_base_id
            GROUP BY ks.organization_id
        ),
        request_stats AS (
            SELECT
                organization_id,
                count(*) FILTER (
                    WHERE lower(status) = 'pending'
                ) AS pending_requests,
                count(*) FILTER (
                    WHERE created_at >= $1 AND created_at <= $2
                ) AS join_requests
            FROM organization_join_requests
            GROUP BY organization_id
        )
        SELECT
            o.id::text AS shared_space_id,
            o.name,
            o.description,
            o.created_at,
            lower(u.email) AS owner_email,
            COALESCE(NULLIF(u.username, ''), u.email, o.owner_id) AS owner_name,
            COALESCE(ms.current_members, 0) AS current_members,
            COALESCE(ms.joined_members, 0) AS joined_members,
            COALESCE(ms.admins, 0) AS admins,
            COALESCE(ms.editors, 0) AS editors,
            COALESCE(ms.viewers, 0) AS viewers,
            COALESCE(ss.current_knowledge_bases, 0) AS current_knowledge_bases,
            COALESCE(ss.shared_knowledge_bases, 0) AS shared_knowledge_bases,
            COALESCE(ss.current_documents, 0) AS current_documents,
            COALESCE(ss.uploaded_documents, 0) AS uploaded_documents,
            COALESCE(rs.pending_requests, 0) AS pending_requests,
            COALESCE(rs.join_requests, 0) AS join_requests
        FROM organizations o
        LEFT JOIN users u ON u.id = o.owner_id
        LEFT JOIN member_stats ms ON ms.organization_id = o.id
        LEFT JOIN share_stats ss ON ss.organization_id = o.id
        LEFT JOIN request_stats rs ON rs.organization_id = o.id
        WHERE o.deleted_at IS NULL
        ORDER BY o.created_at DESC, o.name ASC
        """,
        start,
        end,
    )

    knowledge_bases: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for row in knowledge_base_rows:
        item = dict(row)
        knowledge_base_id = str(item["knowledge_base_id"])
        base = knowledge_bases.setdefault(
            knowledge_base_id,
            {
                "knowledge_base_id": knowledge_base_id,
                "name": str(item.get("name") or knowledge_base_id),
                "creator_email": item.get("creator_email"),
                "creator_name": item.get("creator_name"),
                "current_documents": _integer(item, "current_documents"),
                "uploaded_documents": _integer(item, "uploaded_documents"),
                **per_knowledge_base_conversations.get(
                    knowledge_base_id,
                    {
                        "current_conversation_sessions": 0,
                        "conversation_sessions": 0,
                        "current_question_turns": 0,
                        "question_turns": 0,
                    },
                ),
                "file_formats": [],
            },
        )
        if item.get("format") and (_integer(item, "current_count") or _integer(item, "uploaded_count")):
            base["file_formats"].append(
                {
                    "format": str(item["format"]),
                    "current_count": _integer(item, "current_count"),
                    "uploaded_count": _integer(item, "uploaded_count"),
                }
            )

    return {
        "available": True,
        "error": None,
        "current_users": _integer(summary, "current_users"),
        "registered_users": _integer(summary, "registered_users"),
        "current_knowledge_bases": _integer(summary, "current_knowledge_bases"),
        "created_knowledge_bases": _integer(summary, "created_knowledge_bases"),
        "current_documents": _integer(summary, "current_documents"),
        "uploaded_documents": _integer(summary, "uploaded_documents"),
        **knowledge_conversation_counts,
        "current_shared_spaces": _integer(summary, "current_shared_spaces"),
        "created_shared_spaces": _integer(summary, "created_shared_spaces"),
        "current_shared_space_members": _integer(
            summary,
            "current_shared_space_members",
        ),
        "joined_shared_space_members": _integer(
            summary,
            "joined_shared_space_members",
        ),
        "current_shared_knowledge_bases": _integer(
            summary,
            "current_shared_knowledge_bases",
        ),
        "shared_knowledge_bases": _integer(summary, "shared_knowledge_bases"),
        "current_shared_documents": _integer(
            summary,
            "current_shared_documents",
        ),
        "uploaded_shared_documents": _integer(
            summary,
            "uploaded_shared_documents",
        ),
        "pending_shared_space_requests": _integer(
            summary,
            "pending_shared_space_requests",
        ),
        "shared_space_join_requests": _integer(
            summary,
            "shared_space_join_requests",
        ),
        "file_formats": [
            {
                "format": str(row["format"]),
                "current_count": _integer(row, "current_count"),
                "uploaded_count": _integer(row, "uploaded_count"),
            }
            for row in format_rows
        ],
        "knowledge_bases": list(knowledge_bases.values()),
        "users": [
            {
                "weknora_user_id": str(row["weknora_user_id"]),
                "email": row.get("email"),
                "name": row.get("name"),
                "current_knowledge_bases": _integer(
                    row,
                    "current_knowledge_bases",
                ),
                "created_knowledge_bases": _integer(
                    row,
                    "created_knowledge_bases",
                ),
                "current_documents": _integer(row, "current_documents"),
                "uploaded_documents": _integer(row, "uploaded_documents"),
                "conversation_sessions": _integer(
                    row,
                    "conversation_sessions",
                ),
                "question_turns": _integer(row, "question_turns"),
            }
            for row in user_rows
        ],
        "shared_spaces": [
            {
                "shared_space_id": str(row["shared_space_id"]),
                "name": str(row.get("name") or row["shared_space_id"]),
                "description": row.get("description"),
                "created_at": row["created_at"],
                "owner_email": row.get("owner_email"),
                "owner_name": row.get("owner_name"),
                "current_members": _integer(row, "current_members"),
                "joined_members": _integer(row, "joined_members"),
                "admins": _integer(row, "admins"),
                "editors": _integer(row, "editors"),
                "viewers": _integer(row, "viewers"),
                "current_knowledge_bases": _integer(
                    row,
                    "current_knowledge_bases",
                ),
                "shared_knowledge_bases": _integer(
                    row,
                    "shared_knowledge_bases",
                ),
                "current_documents": _integer(row, "current_documents"),
                "uploaded_documents": _integer(row, "uploaded_documents"),
                "pending_requests": _integer(row, "pending_requests"),
                "join_requests": _integer(row, "join_requests"),
            }
            for row in shared_space_rows
        ],
    }


def empty_weknora_dashboard_analytics(error: str | None = None) -> dict[str, Any]:
    return {
        "available": False,
        "error": error,
        **_EMPTY_COUNTS,
        "file_formats": [],
        "knowledge_bases": [],
        "users": [],
        "shared_spaces": [],
    }


async def get_weknora_dashboard_analytics(
    *,
    start: datetime,
    end: datetime,
    limit: int,
) -> dict[str, Any]:
    """Return WeKnora analytics without making the DeerFlow dashboard fail."""

    if not _weknora_database_is_configured():
        return empty_weknora_dashboard_analytics("WeKnora database is not configured")

    connection = None
    try:
        connection = await _connect_weknora_database()
        return await _load_weknora_dashboard_analytics(
            connection,
            start=start,
            end=end,
            limit=limit,
        )
    except Exception:
        logger.warning("Failed to load WeKnora dashboard analytics.", exc_info=True)
        return empty_weknora_dashboard_analytics("WeKnora analytics are temporarily unavailable")
    finally:
        if connection is not None:
            await connection.close()

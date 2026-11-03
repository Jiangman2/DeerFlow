from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.channels.audit import (
    ACTION_MESSAGE_RECEIVED,
    ACTION_MESSAGE_SKIPPED,
    ACTION_RUN_FAILED,
    build_channel_audit_event,
    text_preview,
)
from app.channels.audit_analysis import build_evidence, render_analysis_message
from app.channels.audit_report import (
    AUDIT_NOTIFY_CHAT_ENV,
    render_digest,
    resolve_notify_chat_id,
    send_digest,
)
from app.channels.message_bus import InboundMessage, InboundMessageType
from deerflow.persistence.base import Base
from deerflow.persistence.observability.sql import ObservabilityRepository


def _message(**kwargs: Any) -> InboundMessage:
    payload: dict[str, Any] = {
        "channel_name": "feishu",
        "chat_id": "oc_chat",
        "user_id": "ou_user",
        "text": "帮我总结一下这个文档",
        "owner_user_id": "u1",
    }
    payload.update(kwargs)
    return InboundMessage(**payload)


async def _repo():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    return ObservabilityRepository(sf), engine


def test_text_preview_collapses_whitespace_and_truncates() -> None:
    assert text_preview("a\n\n  b\tc") == "a b c"
    assert text_preview("x" * 10, limit=4) == "xxxx…"
    assert text_preview(None) == ""


def test_build_event_carries_owner_and_platform_identity() -> None:
    message = _message(
        metadata={"chat_type": "group"},
        topic_id="t-1",
        connection_id="conn-1",
        files=[{"name": "a.pdf"}],
    )

    event = build_channel_audit_event(action=ACTION_MESSAGE_RECEIVED, channel_name="feishu", msg=message)

    assert event["action"] == ACTION_MESSAGE_RECEIVED
    assert event["resource_type"] == "channel"
    assert event["method"] == "EVENT"
    assert event["path"] == "channel/feishu/chat"
    assert event["status_code"] == 200
    assert event["result"] == "success"
    # The admin filters group by DeerFlow user, not by the platform open_id.
    assert event["user_id"] == "u1"
    details = event["details_json"]
    assert details["platform_user_id"] == "ou_user"
    assert details["chat_id"] == "oc_chat"
    assert details["chat_type"] == "group"
    assert details["connection_id"] == "conn-1"
    assert details["topic_id"] == "t-1"
    assert details["file_count"] == 1
    assert details["text_preview"].startswith("帮我总结")


def test_build_event_marks_failures() -> None:
    event = build_channel_audit_event(
        action=ACTION_MESSAGE_SKIPPED,
        channel_name="feishu",
        result="failure",
        status_code=403,
        error="group message without @-mention",
        msg=_message(),
    )

    assert event["status_code"] == 403
    assert event["result"] == "failure"
    assert event["error"] == "group message without @-mention"
    assert event["details_json"]["error_preview"] == "group message without @-mention"


def test_build_event_defaults_status_from_result() -> None:
    event = build_channel_audit_event(
        action=ACTION_RUN_FAILED,
        channel_name="feishu",
        result="failure",
        msg=_message(msg_type=InboundMessageType.COMMAND),
    )

    assert event["status_code"] == 500
    assert event["path"] == "channel/feishu/command"
    assert event["details_json"]["message_type"] == "command"


@pytest.mark.asyncio
async def test_audit_summary_groups_by_action_user_and_channel() -> None:
    repo, engine = await _repo()
    try:
        await repo.record_audit(build_channel_audit_event(action=ACTION_MESSAGE_RECEIVED, channel_name="feishu", msg=_message()))
        await repo.record_audit(
            build_channel_audit_event(
                action=ACTION_RUN_FAILED,
                channel_name="feishu",
                result="failure",
                error="boom: sandbox crashed",
                msg=_message(owner_user_id="u2", user_id="ou_other"),
            )
        )
        await repo.record_audit({"action": "thread.delete", "resource_type": "thread", "method": "DELETE", "path": "/api/threads/t1", "status_code": 204, "result": "success", "duration_ms": 5})

        summary = await repo.audit_summary(hours=1, resource_type="channel")

        assert summary["totals"]["events"] == 2
        assert summary["totals"]["success"] == 1
        assert summary["totals"]["failure"] == 1
        assert [item["action"] for item in summary["by_action"]] == [ACTION_MESSAGE_RECEIVED, ACTION_RUN_FAILED]
        assert len(summary["by_user"]) == 2
        channels = {item["channel"]: item for item in summary["by_channel"]}
        assert channels["feishu"]["count"] == 2
        assert len(summary["errors"]) == 1
        assert summary["errors"][0]["user_id"] == "u2"
        assert summary["errors"][0]["error"] == "boom: sandbox crashed"
        assert summary["errors"][0]["chat_id"] == "oc_chat"
        assert summary["timeline"] and summary["timeline"][0]["bucket"].endswith(":00")
        assert summary["first_at"] and summary["last_at"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_audit_summary_filters_by_action_and_result() -> None:
    repo, engine = await _repo()
    try:
        await repo.record_audit(build_channel_audit_event(action=ACTION_MESSAGE_RECEIVED, channel_name="feishu", msg=_message()))
        await repo.record_audit(
            build_channel_audit_event(
                action=ACTION_RUN_FAILED,
                channel_name="feishu",
                result="failure",
                error="nope",
                msg=_message(),
            )
        )

        only_failures = await repo.audit_summary(hours=1, result="failure")
        by_action = await repo.audit_summary(hours=1, actions=[ACTION_MESSAGE_RECEIVED])

        assert only_failures["totals"]["events"] == 1
        assert only_failures["totals"]["failure"] == 1
        assert by_action["totals"]["events"] == 1
        assert by_action["by_action"][0]["action"] == ACTION_MESSAGE_RECEIVED
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_channel_rows_get_their_own_operation_type() -> None:
    repo, engine = await _repo()
    try:
        await repo.record_audit(build_channel_audit_event(action=ACTION_MESSAGE_RECEIVED, channel_name="feishu", msg=_message()))

        rows = await repo.list_audit_logs()

        assert len(rows) == 1
        assert rows[0]["operation_type"] == "channel"
        assert rows[0]["details"]["chat_id"] == "oc_chat"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_render_digest_is_chat_friendly_and_table_free() -> None:
    repo, engine = await _repo()
    try:
        await repo.record_audit(build_channel_audit_event(action=ACTION_MESSAGE_RECEIVED, channel_name="feishu", msg=_message()))
        await repo.record_audit(
            build_channel_audit_event(
                action=ACTION_RUN_FAILED,
                channel_name="feishu",
                result="failure",
                error="boom",
                msg=_message(),
            )
        )
        summary = await repo.audit_summary(hours=1)

        digest = render_digest(summary)

        assert "事件总数：2" in digest
        assert "按事件类型" in digest
        assert "失败样本" in digest
        # Feishu lark_md does not render markdown tables.
        assert "|" not in digest
    finally:
        await engine.dispose()


def test_render_digest_handles_empty_window() -> None:
    digest = render_digest({"window": {"hours": 1}, "totals": {"events": 0}})

    assert "没有任何机器人使用记录" in digest


def test_build_evidence_keeps_error_details() -> None:
    evidence = build_evidence(
        {
            "window": {"start_at": "2026-11-02T10:00:00+00:00", "end_at": "2026-11-02T11:00:00+00:00", "hours": 1},
            "totals": {"events": 3, "success": 2, "failure": 1},
            "by_action": [{"action": ACTION_RUN_FAILED, "count": 1, "failure": 1}],
            "by_user": [],
            "by_channel": [{"channel": "feishu", "count": 3, "failure": 1}],
            "by_result": [{"result": "failure", "count": 1}],
            "errors": [
                {
                    "created_at": "2026-11-02T10:30:00+00:00",
                    "action": ACTION_RUN_FAILED,
                    "channel": "feishu",
                    "user_id": "u2",
                    "error": "RuntimeError: sandbox crashed",
                    "text_preview": "报表发我",
                    "reason": None,
                }
            ],
        },
        question="为什么失败了？",
    )

    assert "统计窗口" in evidence
    assert ACTION_RUN_FAILED in evidence
    assert "RuntimeError: sandbox crashed" in evidence
    assert "为什么失败了？" in evidence


def test_render_analysis_message_keeps_window_header() -> None:
    message = render_analysis_message(
        {"window": {"start_at": "2026-11-02T10:00:00+00:00", "end_at": "2026-11-02T11:00:00+00:00", "hours": 1}},
        "结论：沙箱镜像缺失。",
    )

    assert "机器人日志分析" in message
    assert "结论：沙箱镜像缺失。" in message


@pytest.mark.asyncio
async def test_send_digest_without_channel_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.channels.audit_report.get_channel_service", lambda: None)

    result = await send_digest("hello")

    assert result["sent"] is False
    assert "not running" in result["reason"]


@pytest.mark.asyncio
async def test_send_digest_publishes_fresh_card(monkeypatch: pytest.MonkeyPatch) -> None:
    published: list[Any] = []

    class _Bus:
        async def publish_outbound(self, message):  # noqa: ANN001
            published.append(message)

    class _Channel:
        is_running = True

    class _Service:
        bus = _Bus()

        def get_channel(self, name: str):  # noqa: ANN201
            return _Channel() if name == "feishu" else None

    monkeypatch.setattr("app.channels.audit_report.get_channel_service", lambda: _Service())

    result = await send_digest("**摘要**", chat_id="oc_target")

    assert result["sent"] is True
    assert result["chat_id"] == "oc_target"
    assert len(published) == 1
    message = published[0]
    assert message.chat_id == "oc_target"
    # Empty thread_id/thread_ts select FeishuChannel's "fresh card" branch.
    assert message.thread_id == ""
    assert message.thread_ts is None


@pytest.mark.asyncio
async def test_send_digest_requires_a_target_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Service:
        bus = None

        def get_channel(self, name: str):  # noqa: ANN201
            return None

        def get_channel_config(self, name: str) -> dict[str, Any]:  # noqa: ANN401
            return {}

    monkeypatch.delenv(AUDIT_NOTIFY_CHAT_ENV, raising=False)
    monkeypatch.setattr("app.channels.audit_report.get_channel_service", lambda: _Service())

    result = await send_digest("hello")

    assert result["sent"] is False
    assert "no target chat" in result["reason"]


def test_resolve_notify_chat_id_prefers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(AUDIT_NOTIFY_CHAT_ENV, " oc_env ")

    assert resolve_notify_chat_id("feishu") == "oc_env"


def test_event_created_at_is_timezone_aware() -> None:
    event = build_channel_audit_event(action=ACTION_MESSAGE_RECEIVED, channel_name="feishu", msg=_message())

    assert isinstance(event["created_at"], datetime)
    assert event["created_at"].tzinfo is not None
    assert event["created_at"].astimezone(UTC).year >= 2024
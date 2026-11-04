"""Semantic task routing with real service boundaries and synthetic providers."""

import time
from unittest.mock import AsyncMock

import pytest
from test_work_documents import DOC_URL, begin, inbound, seed_task, service, work_decision

from app.integrations.work.auth import Grant
from app.integrations.work.calendar import CalendarIntent
from app.integrations.work.capabilities import Action
from app.integrations.work.client import WorkError
from app.integrations.work.routing import WorkTurnDecision
from app.integrations.work.tasks import TaskRef

DAY = {"start": "2026-10-22T00:00:00+08:00", "end": "2026-10-23T00:00:00+08:00"}
WEEK = {"start": "2026-10-19T00:00:00+08:00", "end": "2026-10-26T00:00:00+08:00"}
CREATE = {"title": "技术交流", "start": "2026-10-22T10:00:00+08:00", "end": "2026-10-22T10:30:00+08:00"}


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["请帮我写一份项目说明文档，不需要访问 Work", "请总结我上传的会议纪要", "解释一下日历的起源", "换个话题，解释一下光合作用", "请写一份会议通知", "你是谁"])
@pytest.mark.parametrize("pending", [False, True])
async def test_ordinary_chat_never_enters_work_or_drops_attachments(text, pending):
    instance, repo, api, send, _ = service(turn_planner=AsyncMock(return_value=WorkTurnDecision(route="general")))
    msg = inbound(text=text, files=[{"name": "会议纪要.docx"}])
    p = instance._identity(msg)
    old_ref = None
    if pending:
        instance._remember_question(p, "安排日程", "需要多长时间？")
        _, old_ref = await seed_task(instance, context=instance._context_data(p))
    try:
        assert await instance.handle(msg) is False
        assert msg.files == [{"name": "会议纪要.docx"}]
        assert not instance.jobs and p not in instance._calendar_dialogues
        if old_ref:
            with pytest.raises(WorkError, match="task_superseded"):
                await instance._assert_task(p, old_ref)
        instance._turn_planner.assert_not_awaited()
        repo.find_connection_by_external_identity.assert_not_awaited()
        api.assert_not_awaited()
        send.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["解释一下日历的起源", "请写一篇文档", "换个话题，解释一下光合作用", "你是谁"])
async def test_pending_question_does_not_capture_unrelated_text(text):
    instance, _, api, send, _ = service(turn_planner=AsyncMock(return_value=WorkTurnDecision(route="general")))
    p = instance._identity(inbound())
    instance._remember_question(p, "安排日程", "需要多长时间？")
    _, old_ref = await seed_task(instance, context=instance._context_data(p))
    try:
        assert await instance.handle(inbound(text=text)) is False
        assert p not in instance._calendar_dialogues
        with pytest.raises(WorkError, match="task_superseded"):
            await instance._assert_task(p, old_ref)
        instance._turn_planner.assert_awaited_once()
        assert instance._turn_planner.call_args.args[0] == text
        api.assert_not_awaited()
        send.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text,decision,context,expected_domain",
    [
        ("请总结 " + DOC_URL, work_decision(Action(operation="document_read", document_id="doxTest123")), None, "document"),
        ("/work 创建空白文档", work_decision(Action(operation="document_create", title="测试文档")), None, "document"),
        ("请创建 Work 文档", work_decision(Action(operation="document_create", title="测试文档")), None, "document"),
        ("请查一下我明天的日程", work_decision(CalendarIntent(operation="calendar_query", selector=DAY)), None, "calendar"),
        ("读取我这一周的日程", work_decision(CalendarIntent(operation="calendar_query", selector=WEEK)), None, "calendar"),
        ("最近七天我都排了什么", work_decision(CalendarIntent(operation="calendar_query", selector=WEEK)), None, "calendar"),
        ("在我的个人日历新增日程，10月22日10点至10点半，技术交流", work_decision(CalendarIntent(operation="calendar_create", changes=CREATE)), None, "calendar"),
        ("半个小时就够了", work_decision(CalendarIntent(operation="calendar_create", changes=CREATE), route="continue_work"), {"dialogue": [{"user": "新增技术交流", "question": "需要多久？"}]}, "calendar"),
        ("半个小时就够了", WorkTurnDecision(route="general"), None, None),
        ("请总结刚才的文档", work_decision(Action(operation="document_read", document_id="doxTest123"), route="continue_work"), {"document": "doxTest123"}, "document"),
        ("请帮我总结这个文件", WorkTurnDecision(route="general"), None, None),
        ("解释一下日历的起源", WorkTurnDecision(route="general"), None, None),
    ],
)
async def test_semantic_decisions_replace_operation_word_admission(text, decision, context, expected_domain):
    planner = AsyncMock(return_value=decision)
    instance, _, api, send, _ = service(turn_planner=planner)
    if context:
        await seed_task(instance, domain=expected_domain, context=context)
    try:
        handled = await instance.handle(inbound(text=text))
        planner.assert_awaited_once()
        assert planner.call_args.args[0] == text
        assert handled is (expected_domain is not None)
        api.assert_not_awaited()
        if expected_domain:
            job = next(iter(instance.jobs.values()))
            assert job.scope == ("docx:document" if expected_domain == "document" else "calendar:calendar:readonly calendar:calendar")
            assert TaskRef.parse(job.task_ref) is not None
            assert "授权并继续" in send.call_args.args[0].text
        else:
            assert not instance.jobs
            send.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_explicit_attachment_reports_limitation_without_empty_file_job():
    instance, _, api, send, _ = service()
    try:
        assert await instance.handle(inbound(text="/work 总结附件", files=["test.docx"]))
        assert not instance.jobs
        assert "附件" in send.call_args.args[0].text and "分开发送" in send.call_args.args[0].text
        instance._turn_planner.assert_not_awaited()
        api.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_non_whitelisted_ordinary_user_falls_through():
    instance, repo, api, send, _ = service(turn_planner=AsyncMock(return_value=WorkTurnDecision(route="general")))
    repo.find_connection_by_external_identity.return_value = None
    try:
        assert await instance.handle(inbound(text="请写一份项目说明文档")) is False
        instance._turn_planner.assert_awaited_once()
        repo.find_connection_by_external_identity.assert_not_awaited()
        api.assert_not_awaited()
        send.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_topic_change_clears_task_context_without_revoking_personal_consent():
    instance, _, api, _, _ = service(turn_planner=AsyncMock(return_value=WorkTurnDecision(route="general")))
    context = {"document": "doxTest123", "dialogue": [{"user": "安排日程", "question": "需要多久？"}]}
    p, old_ref = await seed_task(instance, context=context)
    grant = Grant("synthetic-token", time.monotonic() + 600, frozenset({"docx:document"}))
    instance.vault.put(p, grant)
    try:
        assert await instance.handle(inbound(text="解释光合作用")) is False
        assert p not in instance._contexts and p not in instance._calendar_dialogues
        with pytest.raises(WorkError, match="task_superseded"):
            await instance._assert_task(p, old_ref)
        reservation = await instance._turn_state.reserve_turn(instance.vault.principal_key(p), "inspect-after-exit")
        assert reservation.task_ref is None and reservation.context == {} and reservation.task == {}
        assert await instance.vault.require(p, "docx:document") is grant
        api.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_topic_change_rejects_late_oauth_before_token_exchange():
    planner = AsyncMock(side_effect=[work_decision(Action(operation="document_read", document_id="doxTest123")), WorkTurnDecision(route="general")])
    instance, _, api, _, _ = service(turn_planner=planner)
    try:
        ticket, state, nonce = await begin(instance)
        assert await instance.handle(inbound(text="换个话题，解释光合作用", thread_ts="topic-change")) is False
        assert instance.jobs[ticket].stage == "cancelled"
        with pytest.raises(WorkError):
            await instance.callback(state=state, code="synthetic-code", browser_session=nonce)
        api.assert_not_awaited()
    finally:
        await instance.close()

"""Task routing regressions with real services and synthetic Work dependencies."""

import time
from unittest.mock import AsyncMock

import pytest
from test_work_documents import DOC_URL, inbound, service

from app.integrations.work.persistent import PersistentWorkService
from app.integrations.work.routing import route_work


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["请帮我写一份项目说明文档，不需要访问 Work", "请总结我上传的会议纪要", "解释一下日历的起源", "换个话题，解释一下光合作用", "请写一份会议通知", "你是谁"])
@pytest.mark.parametrize("pending", [False, True])
async def test_ordinary_chat_never_enters_work_or_drops_attachments(text, pending):
    instance, repo, api, send, _ = service()
    msg = inbound(text=text, files=[{"name": "会议纪要.docx"}])
    p = instance._identity(msg)
    if pending:
        instance._remember_question(p, "安排日程", "需要多长时间？")
    try:
        assert await instance.handle(msg) is False
        assert msg.files == [{"name": "会议纪要.docx"}]
        assert not instance.jobs and p not in instance._calendar_dialogues
        repo.find_connection_by_external_identity.assert_not_awaited()
        api.assert_not_awaited()
        send.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["解释一下日历的起源", "请写一篇文档", "换个话题，解释一下光合作用", "你是谁"])
async def test_pending_question_does_not_capture_unrelated_text(text):
    instance, _, api, send, _ = service()
    p = instance._identity(inbound())
    instance._remember_question(p, "安排日程", "需要多长时间？")
    try:
        assert await instance.handle(inbound(text=text)) is False
        assert p not in instance._calendar_dialogues
        api.assert_not_awaited()
        send.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.parametrize(
    "text,options,expected",
    [
        ("请总结 " + DOC_URL, {"links": [("doxTest123", DOC_URL)]}, "document"),
        ("/work 创建空白文档", {"explicit": True}, "document"),
        ("请创建 Work 文档", {}, "document"),
        ("请查一下我明天的日程", {}, "calendar"),
        ("在我的个人日历新增日程", {}, "calendar"),
        ("半个小时就够了", {"pending_dialogue": True}, "calendar"),
        ("半个小时就够了", {}, None),
        ("刚才那条改到明天下午三点", {"calendar_context": True}, "calendar"),
        ("/work 总结附件", {"explicit": True, "files": ["test.docx"]}, "unsupported_attachment"),
        ("请总结刚才的文档", {"document_context": True}, "document"),
        ("请帮我总结这个文件", {}, None),
        ("解释一下日历的起源", {}, None),
    ],
)
def test_positive_and_negative_routing(text, options, expected):
    args = {"links": [], "explicit": False, "files": []}
    args.update(options)
    assert route_work(text, **args) == expected


@pytest.mark.asyncio
async def test_explicit_attachment_reports_limitation_without_empty_file_job():
    instance, _, api, send, _ = service()
    try:
        assert await instance.handle(inbound(text="/work 总结附件", files=["test.docx"]))
        assert not instance.jobs
        assert "暂不处理上传附件" in send.call_args.args[0].text
        api.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_non_whitelisted_ordinary_user_falls_through():
    instance, repo, _, send, _ = service()
    repo.find_connection_by_external_identity.return_value = None
    try:
        assert await instance.handle(inbound(text="请写一份项目说明文档")) is False
        send.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_topic_change_clears_shared_context_with_versioned_remove():
    instance, _, _, _, _ = service()
    instance.__class__ = PersistentWorkService
    instance.store = AsyncMock()
    record = object()
    instance.store.read.return_value = record
    p = instance._identity(inbound())
    instance._contexts[p] = (time.monotonic() + 600, "doxTest123")
    instance._remember_question(p, "安排日程", "需要多长时间？")
    try:
        await instance._forget_context(p)
        key = instance.vault.principal_key(p)
        instance.store.remove.assert_awaited_once_with("context", key, record)
        assert p not in instance._contexts and p not in instance._calendar_dialogues
    finally:
        # Only the context method is under test; retain the normal cleanup owner.
        from app.integrations.work.service import WorkDocumentService

        instance.__class__ = WorkDocumentService
        await instance.close()

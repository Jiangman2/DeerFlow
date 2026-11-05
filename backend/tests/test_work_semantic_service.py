"""Semantic orchestration with real fencing and synthetic provider boundaries."""

import asyncio
import logging
import re
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest

from app.channels.message_bus import InboundMessage
from app.integrations.work.auth import Grant
from app.integrations.work.calendar import CalendarIntent
from app.integrations.work.capabilities import Action
from app.integrations.work.client import UAT_APP_ID, WorkError
from app.integrations.work.routing import WorkTurnDecision
from app.integrations.work.service import WorkDocumentService

DOC = "https://haiyoutest.w.cnooc.com.cn/docx/doxSynthetic123"


def message(text, source="one", **overrides):
    data = dict(channel_name="feishu", chat_id="chat-A", user_id="ou-A", text=text, connection_id="connection-A", owner_user_id="owner-A", workspace_id="chat-A", thread_ts=source, metadata={"chat_type": "p2p", "message_id": source})
    data.update(overrides)
    return InboundMessage(**data)


def calendar_decision():
    return WorkTurnDecision(route="new_work", domain="calendar", intent=CalendarIntent(operation="calendar_query", selector={"start": "2026-11-02T00:00:00+08:00", "end": "2026-11-09T00:00:00+08:00"}))


def make_service(*decisions):
    repo = SimpleNamespace(find_connection_by_external_identity=AsyncMock(return_value={"id": "connection-A", "owner_user_id": "owner-A", "status": "connected"}))
    planner = AsyncMock(side_effect=list(decisions))
    service = WorkDocumentService(app_id=UAT_APP_ID, app_secret="synthetic-secret", allowed_owners={"owner-A"}, repo=repo, send=AsyncMock(), api=AsyncMock(), summarize=AsyncMock(return_value="synthetic summary"), turn_planner=planner)
    return service


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["读取我下周的日历", "查询我这一周的日程", "接下来七天我都排了什么", "下星期有哪些安排", "你能读我明天的日程吗？"])
async def test_semantic_result_enters_oauth_without_a_verb_gate(text):
    service = make_service(calendar_decision())
    try:
        assert await service.handle(message(text))
        service._turn_planner.assert_awaited_once()
        assert service._turn_planner.call_args.kwargs["task_context"] == {}
        assert next(iter(service.jobs.values())).scope == "calendar:calendar:readonly calendar:calendar"
        assert "授权并继续" in service.send.call_args.args[0].text
        service._api.assert_not_awaited()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_missing_date_is_asked_before_consent_and_short_answer_continues():
    question = WorkTurnDecision(route="clarify", domain="calendar", intent=CalendarIntent(operation="calendar_query"), question="您想查看哪段时间的日程？")
    answer = calendar_decision().model_copy(update={"route": "continue_work"})
    service = make_service(question, answer)
    try:
        assert await service.handle(message("帮我看看最近的行程"))
        assert not service.jobs and "哪段时间" in service.send.call_args.args[0].text
        assert "授权并继续" not in service.send.call_args.args[0].text
        assert await service.handle(message("下周", "two"))
        context = service._turn_planner.call_args.kwargs["task_context"]
        assert context["question"] == question.question
        assert context["pending_dialogue"][0]["user"] == "帮我看看最近的行程"
        assert len(service.jobs) == 1 and "授权并继续" in service.send.call_args.args[0].text
        service._api.assert_not_awaited()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_topic_exit_invalidates_old_authorization_before_provider_exchange():
    service = make_service(calendar_decision(), WorkTurnDecision(route="general"))
    try:
        await service.handle(message("读取下周日程"))
        ticket = next(iter(service.jobs))
        start = await service.authorize(ticket, "")
        state = parse_qs(urlsplit(start["authorize_url"]).query)["state"][0]
        assert await service.handle(message("换个话题，解释光合作用", "two")) is False
        with pytest.raises(WorkError):
            await service.callback(state=state, code="synthetic-code", browser_session=start["browser_session"])
        service._api.assert_not_awaited()
        assert not service._calendar_dialogues
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_topic_exit_revokes_preview_but_keeps_the_valid_personal_grant():
    decision = WorkTurnDecision(route="new_work", domain="document", intent=Action(operation="document_create", title="会议纪要"))
    service = make_service(decision, WorkTurnDecision(route="general"))
    p = service._identity(message("create"))
    grant = Grant("synthetic-user-token", time.monotonic() + 600, frozenset({"docx:document"}))
    service.vault.put(p, grant)
    try:
        await service.handle(message("创建空白云文档，标题会议纪要"))
        await service.drain()
        plan = next(iter(service.tools.plans.values()))
        assert plan.stage == "pending"
        assert await service.handle(message("解释光合作用", "two")) is False
        await service.handle(message("/work 确认 " + plan.id, "three"))
        assert plan.stage == "cancelled"
        service._api.assert_not_awaited()
        assert (await service.vault.require(p, "docx:document")).token == grant.token
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["web", "slack", "telegram", "github"])
async def test_other_channels_do_not_run_work_planning_or_touch_work_state(provider):
    service = make_service(calendar_decision())
    try:
        assert await service.handle(message("帮我看看最近的行程", channel_name=provider)) is False
        service._turn_planner.assert_not_awaited()
        service._api.assert_not_awaited()
        assert not service.jobs and not service._turn_state._rows
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_slow_old_planning_cannot_start_a_job_after_new_topic():
    service = make_service()
    entered, release = asyncio.Event(), asyncio.Event()

    async def planner(text, **kwargs):
        if text == "读取日程":
            entered.set()
            await release.wait()
            return calendar_decision()
        return WorkTurnDecision(route="general")

    service._turn_planner.side_effect = planner
    old = asyncio.create_task(service.handle(message("读取日程")))
    try:
        await entered.wait()
        assert await service.handle(message("解释光合作用", "two")) is False
        release.set()
        assert await old
        assert not service.jobs
        service._api.assert_not_awaited()
    finally:
        release.set()
        await old
        await service.close()


@pytest.mark.asyncio
async def test_planner_failure_is_explicit_and_never_falls_into_generic_refusal():
    service = make_service()
    service._turn_planner.side_effect = WorkError("turn_model_timeout")
    try:
        assert await service.handle(message("读取日程")) is True
        text = service.send.call_args.args[0].text
        assert "识别暂未完成" in text and "没有接入" not in text
        assert "本次输入尚未保存" in text and "重新发送完整请求" in text
        assert not service.jobs
        service._api.assert_not_awaited()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_queue_wait_does_not_consume_the_planner_execution_budget():
    service = make_service()
    service.settings = replace(service.settings, turn_timeout=0.04, turn_queue_timeout=0.2)
    service._turn_semaphore = asyncio.Semaphore(0)

    async def planner(text, **kwargs):
        assert kwargs["timeout"] == 0.04
        async with asyncio.timeout(kwargs["timeout"]):
            await asyncio.sleep(0.025)
        return calendar_decision()

    service._turn_planner.side_effect = planner
    pending = asyncio.create_task(service.handle(message("读取下周日程")))
    try:
        await asyncio.sleep(0.06)
        assert not pending.done()
        service._turn_semaphore.release()
        assert await asyncio.wait_for(pending, timeout=0.5)
        assert len(service.jobs) == 1
        service._api.assert_not_awaited()
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
async def test_bounded_queue_reports_busy_without_calling_the_planner_or_provider(caplog):
    service = make_service(calendar_decision())
    service.settings = replace(service.settings, turn_timeout=0.2, turn_queue_timeout=0.01)
    service._turn_semaphore = asyncio.Semaphore(0)
    try:
        with caplog.at_level(logging.INFO, logger="app.integrations.work.service"):
            assert await service.handle(message("private-user-text", "private-message-id"))
        text = service.send.call_args.args[0].text
        assert "繁忙" in text and "turn_queue_busy" in text
        assert "本次输入尚未保存" in text
        assert not service.jobs
        service._turn_planner.assert_not_awaited()
        service._api.assert_not_awaited()
        diagnostics = [r.getMessage() for r in caplog.records if "work_turn_diagnostic" in r.getMessage()]
        assert any("stage=queued" in row for row in diagnostics)
        assert any("stage=failure" in row and "error=turn_queue_busy" in row for row in diagnostics)
        refs = [re.search(r"message_ref=([0-9a-f]{16})", row).group(1) for row in diagnostics]
        assert len(set(refs)) == 1
        assert "private-user-text" not in caplog.text and "private-message-id" not in caplog.text
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_injected_hanging_planner_has_a_defensive_watchdog_and_releases_the_slot(monkeypatch, caplog):
    from app.integrations.work import service as module

    monkeypatch.setattr(module, "TURN_WATCHDOG_GRACE_SECONDS", 0.01)
    service = make_service()
    service.settings = replace(service.settings, turn_timeout=0.01, turn_queue_timeout=0.1)
    service._turn_semaphore = asyncio.Semaphore(1)

    async def hanging(*args, **kwargs):
        await asyncio.Future()

    service._turn_planner.side_effect = hanging
    try:
        with caplog.at_level(logging.INFO, logger="app.integrations.work.service"):
            assert await asyncio.wait_for(service.handle(message("private-input", "private-source")), timeout=0.5)
        text = service.send.call_args.args[0].text
        assert "turn_model_timeout" in text and "本次输入尚未保存" in text
        assert not service.jobs
        service._api.assert_not_awaited()
        assert any("stage=model_start" in r.getMessage() for r in caplog.records)
        assert any("error=turn_model_timeout" in r.getMessage() for r in caplog.records)
        assert "private-input" not in caplog.text and "private-source" not in caplog.text
        service._turn_planner.side_effect = None
        service._turn_planner.return_value = WorkTurnDecision(route="general")
        assert await asyncio.wait_for(service.handle(message("普通问题", "second")), timeout=0.5) is False
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_planner_cancellation_releases_admission_and_diagnostics_never_contain_the_message(caplog):
    service = make_service()
    service._turn_semaphore = asyncio.Semaphore(1)
    entered = asyncio.Event()

    async def planner(*args, **kwargs):
        entered.set()
        await asyncio.Future()

    service._turn_planner.side_effect = planner
    pending = None
    try:
        with caplog.at_level(logging.INFO, logger="app.integrations.work.service"):
            pending = asyncio.create_task(service.handle(message("private-cancel-text", "private-cancel-source")))
            await asyncio.wait_for(entered.wait(), timeout=0.5)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            service._turn_planner.side_effect = None
            service._turn_planner.return_value = WorkTurnDecision(route="general")
            assert await asyncio.wait_for(service.handle(message("private-next-text", "private-next-source")), timeout=0.5) is False
        diagnostics = [r.getMessage() for r in caplog.records if "work_turn_diagnostic" in r.getMessage()]
        assert any("stage=cancelled" in row for row in diagnostics)
        assert any("stage=done" in row for row in diagnostics)
        assert len({re.search(r"message_ref=([0-9a-f]{16})", row).group(1) for row in diagnostics}) == 2
        assert "private-cancel" not in caplog.text and "private-next" not in caplog.text
        service._api.assert_not_awaited()
    finally:
        if pending:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
async def test_unavailable_service_uses_the_same_bounded_queue_and_busy_reply(monkeypatch):
    from app.integrations.work import service as module

    settings = replace(module.WorkSettings.local_uat(), turn_timeout=0.2, turn_queue_timeout=0.01)
    monkeypatch.setattr(module.WorkSettings, "from_env", classmethod(lambda cls: settings))
    planner, send = AsyncMock(), AsyncMock()
    monkeypatch.setattr(module, "plan_turn", planner)
    assert await module.handle_unavailable_work(message("读取下周日程"), send=send, semaphore=asyncio.Semaphore(0))
    planner.assert_not_awaited()
    text = send.call_args.args[0].text
    assert "繁忙" in text and "本次输入尚未保存" in text


@pytest.mark.asyncio
async def test_opaque_summary_task_is_not_replanned_after_successful_authorization():
    decision = WorkTurnDecision(route="new_work", domain="document", intent=Action(operation="document_read", document_id="doxSynthetic123"))
    service = make_service(decision)
    service._planner = AsyncMock(side_effect=AssertionError("OAuth must reuse the validated intent"))
    service._api.side_effect = [
        {"access_token": "synthetic-token", "expires_in": 7200, "scope": "docx:document"},
        {"data": {"open_id": "ou-A"}},
        {"data": {"document": {"title": "测试文档"}}},
        {"data": {"content": "会议讨论了项目。"}},
    ]
    try:
        await service.handle(message("总结 " + DOC))
        ticket = next(iter(service.jobs))
        start = await service.authorize(ticket, "")
        state = parse_qs(urlsplit(start["authorize_url"]).query)["state"][0]
        await service.callback(state=state, code="synthetic-code", browser_session=start["browser_session"])
        await service.drain()
        service._turn_planner.assert_awaited_once()
        service._planner.assert_not_awaited()
        assert "synthetic summary" in service.send.call_args.args[0].text
        assert service._api.await_count == 4
        assert service.jobs[ticket].stage == "completed"
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_late_oauth_error_does_not_send_a_failure_after_topic_exit():
    service = make_service(calendar_decision(), WorkTurnDecision(route="general"))
    entered, release = asyncio.Event(), asyncio.Event()

    async def api(operation, **kwargs):
        assert operation == "token"
        entered.set()
        await release.wait()
        raise WorkError("token", 400, 99991401)

    service._api.side_effect = api
    callback = None
    try:
        await service.handle(message("读取日程"))
        ticket = next(iter(service.jobs))
        start = await service.authorize(ticket, "")
        state = parse_qs(urlsplit(start["authorize_url"]).query)["state"][0]
        callback = asyncio.create_task(service.callback(state=state, code="synthetic", browser_session=start["browser_session"]))
        await entered.wait()
        assert await service.handle(message("解释光合作用", "two")) is False
        replies = service.send.await_count
        release.set()
        with pytest.raises(WorkError, match="task_superseded"):
            await callback
        assert service.send.await_count == replies
        assert not service.grants
    finally:
        release.set()
        if callback:
            await asyncio.gather(callback, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
async def test_memory_continuation_uses_the_reserved_context_expiration(monkeypatch):
    from app.integrations.work import turn_state

    clock = [100.0]
    monkeypatch.setattr(turn_state, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    state = turn_state.MemoryTurns()
    first = await state.reserve_turn("A", "one")
    ref = await state.finish_turn("A", first.turn_id, mode="new")
    await state.save_task_context("A", ref, {"calendar": {"candidates": ["old"]}}, ttl=1)
    second = await state.reserve_turn("A", "two")
    await state.save_task_context("A", ref, {"calendar": {"candidates": ["late"]}}, ttl=600)
    clock[0] += 2
    current = await state.finish_turn("A", second.turn_id, mode="continue")
    assert await state.read_task_context("A", current) is None


@pytest.mark.asyncio
async def test_busy_execution_does_not_block_a_new_turn_or_topic_exit():
    service = make_service(WorkTurnDecision(route="general"))
    await service._semaphore.acquire()
    await service._semaphore.acquire()
    try:
        assert await asyncio.wait_for(service.handle(message("解释光合作用")), timeout=1) is False
        service._turn_planner.assert_awaited_once()
    finally:
        service._semaphore.release()
        service._semaphore.release()
        await service.close()

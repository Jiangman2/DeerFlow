"""Search buttons and quoted numbers resolve only their authorized candidate list."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from test_work_cards import SETTINGS, card_env, stores  # noqa: F401
from test_work_file_search_service import _DOC1, _DOC2, _REQUEST, _TITLE, _authorize, _enable, _grant, _oauth, _plan, _results
from test_work_production import principal_message, production_service

from app.integrations.work.cards import CARD_PREVIEW_KEY, CARD_REPLY_TARGET_KEY, CardClick, CardReplyTarget
from app.integrations.work.file_search import SEARCH_SCOPE
from app.integrations.work.scopes import DOCUMENT_SCOPE


@pytest.mark.asyncio
async def test_search_card_button_binds_actor_message_and_current_candidate_generation(card_env, stores, monkeypatch):  # noqa: F811
    _enable(monkeypatch, "PRODUCTION")
    first_store, second_store, _ = stores
    search = _results((_TITLE, "DOCX", _DOC1), (_TITLE, "DOCX", _DOC2))
    first = production_service(first_store, AsyncMock(return_value=search), turn_planner=_plan())
    reading = asyncio.Event()
    finish_read = asyncio.Event()

    async def read_provider(operation, **kwargs):
        if operation == "document_info":
            return {"data": {"document": {"document_id": _DOC2, "title": _TITLE}}}
        reading.set()
        await finish_read.wait()
        return {"data": {"content": "第二份文档"}}

    provider = AsyncMock(side_effect=read_provider)
    second = production_service(second_store, provider)
    try:
        first_message = principal_message(text=_REQUEST, source="om_search_old")
        actor = first._identity(first_message)
        await first._search_vault.install(actor, _grant("search-grant", SEARCH_SCOPE))
        await first._file_vaults["docx"].install(actor, _grant("docx-grant", DOCUMENT_SCOPE))
        assert await first.handle(first_message)
        await first.drain()
        old_preview = first.send.await_args.args[0].metadata[CARD_PREVIEW_KEY]
        assert await old_preview.bind("om_old_choices")

        current_message = principal_message(text=_REQUEST, source="om_search_new")
        assert await first.handle(current_message)
        await first.drain()
        preview = first.send.await_args.args[0].metadata[CARD_PREVIEW_KEY]
        assert preview.choice_count == 2
        assert await preview.bind("om_current_choices")

        def click(event_id, *, sender="ou-A", message_id="om_current_choices", reference=preview.reference, verb="select_2"):
            return CardClick(event_id, sender, "chat-A", message_id, reference, verb, SETTINGS.tenant_key)

        assert await second.enqueue_card_click(click("old", message_id="om_old_choices", reference=old_preview.reference), SETTINGS) == "invalid"
        assert await second.enqueue_card_click(click("wrong-card", message_id="om_old_choices"), SETTINGS) == "invalid"
        assert await second.enqueue_card_click(click("wrong-sender", sender="ou-B"), SETTINGS) == "invalid"
        assert await second.enqueue_card_click(click("wrong-action", verb="confirm"), SETTINGS) == "invalid"
        old_reply = principal_message(text="2", source="om_reply_to_old")
        old_reply.metadata["parent_id"] = "om_old_choices"
        assert await second.handle(old_reply)
        provider.assert_not_awaited()

        assert await second.enqueue_card_click(click("chosen"), SETTINGS) == "accepted"
        await asyncio.wait_for(reading.wait(), timeout=5)
        progress = next(call.args[0] for call in second.send.await_args_list if "正在准备读取" in call.args[0].text)
        assert progress.is_final
        assert progress.metadata[CARD_REPLY_TARGET_KEY] == CardReplyTarget("om_current_choices")
        assert CARD_PREVIEW_KEY not in progress.metadata
        assert await second.handle(principal_message(text="2", source="om_repeat_number"))
        assert "上次选择已受理" in second.send.await_args.args[0].text
        second._turn_planner.assert_not_awaited()
        finish_read.set()
        await second.drain()
        assert [call.kwargs["document_id"] for call in provider.await_args_list] == [_DOC2, _DOC2]
        assert second.send.await_args.args[0].metadata[CARD_REPLY_TARGET_KEY] == CardReplyTarget("om_current_choices")
        assert await second.enqueue_card_click(click("repeated"), SETTINGS) == "invalid"
        assert provider.await_count == 2
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_search_card_oauth_resumes_into_original_card_across_workers(card_env, stores, monkeypatch):  # noqa: F811
    _enable(monkeypatch, "PRODUCTION")
    first_store, second_store, _ = stores
    search = _results((_TITLE, "DOCX", _DOC1), (_TITLE, "DOCX", _DOC2))
    first = production_service(first_store, AsyncMock(return_value=search), turn_planner=_plan())
    provider = AsyncMock(side_effect=[*_oauth("docx-grant", DOCUMENT_SCOPE, "ou-A"), {"data": {"document": {"document_id": _DOC2, "title": _TITLE}}}, {"data": {"content": "授权后的正文"}}])
    second = production_service(second_store, provider)
    try:
        message = principal_message(text=_REQUEST, source="om_search_with_oauth")
        await first._search_vault.install(first._identity(message), _grant("search-grant", SEARCH_SCOPE))
        assert await first.handle(message)
        await first.drain()
        preview = first.send.await_args.args[0].metadata[CARD_PREVIEW_KEY]
        assert await preview.bind("om_oauth_choices")
        click = CardClick("click-oauth", "ou-A", "chat-A", "om_oauth_choices", preview.reference, "select_2", SETTINGS.tenant_key)
        assert await first.enqueue_card_click(click, SETTINGS) == "accepted"
        await first.drain()
        prompt = first.send.await_args.args[0]
        assert "授权并继续" in prompt.text
        assert prompt.metadata[CARD_REPLY_TARGET_KEY] == CardReplyTarget("om_oauth_choices")
        ticket = next(ticket for ticket, job in first.jobs.items() if job.scope == DOCUMENT_SCOPE)
        await second.callback(**await _authorize(second, ticket, DOCUMENT_SCOPE))
        await second.drain()
        receipt = second.send.await_args.args[0]
        assert receipt.is_final
        assert receipt.metadata[CARD_REPLY_TARGET_KEY] == CardReplyTarget("om_oauth_choices")
        assert provider.await_args.kwargs["document_id"] == _DOC2
    finally:
        await first.close()
        await second.close()

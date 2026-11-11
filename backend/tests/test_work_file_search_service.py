"""By-title Work search, consent isolation and stale-choice fencing."""

import time
from dataclasses import replace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
from test_work_documents import inbound, service
from test_work_file_content import _docx
from test_work_production import principal_message, production_service, stores  # noqa: F401

from app.integrations.work.auth import Grant
from app.integrations.work.client import WorkError
from app.integrations.work.file_search import LEGACY_SEARCH_SCOPE, SEARCH_JOB_PROTOCOL, SEARCH_SCOPE
from app.integrations.work.routing import FileLookupIntent, WorkTurnDecision
from app.integrations.work.scopes import CALENDAR_AUTH_SCOPE, DOCUMENT_SCOPE
from app.integrations.work.tasks import TaskRef

_TITLE = "项目复盘"
_REQUEST = f"请在海能 Work 里读取并总结《{_TITLE}》"
_DOC1 = "doxSearchSynthetic1"
_DOC2 = "doxSearchSynthetic2"


def _enable(monkeypatch, profile="UAT", *, scope=SEARCH_SCOPE):
    monkeypatch.setenv("DEER_FLOW_WORK_FILE_SEARCH_ENABLED", "true")
    monkeypatch.setenv(f"DEER_FLOW_WORK_{profile}_FILE_SEARCH_SCOPE", scope)


def _plan(*, title=_TITLE, read=True):
    return AsyncMock(return_value=WorkTurnDecision(route="new_work", domain="file_lookup", intent=FileLookupIntent(operation="file_lookup", title=title, read=read)))


def _results(*targets, more=False):
    return {
        "data": {
            "res_units": [{"title": title, "result_meta": {"doc_types": kind, "token": token}} for title, kind, token in targets],
            "has_more": more,
        }
    }


def _grant(token, scope):
    return Grant(token, time.monotonic() + 600, frozenset(scope.split()))


def _oauth(token, scope, sender="ou-sender"):
    return [{"access_token": token, "expires_in": 7200, "scope": scope}, {"data": {"open_id": sender}}]


async def _authorize(instance, ticket, expected_scope):
    started = await instance.authorize(ticket, "")
    params = parse_qs(urlsplit(started["authorize_url"]).query)
    assert params["scope"] == [expected_scope]
    return {"state": params["state"][0], "code": "synthetic-code", "browser_session": started["browser_session"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("include_similar", [False, True])
async def test_exact_search_uses_separate_content_consent_and_original_request(monkeypatch, include_similar):
    _enable(monkeypatch)
    turn_planner = _plan()
    instance, _, api, send, summarize = service(turn_planner=turn_planner)
    msg = inbound(text=_REQUEST)
    targets = [(_TITLE, "DOCX", _DOC1)]
    if include_similar:
        targets.append((_TITLE + "副本", "DOCX", _DOC2))
    api.side_effect = [
        *_oauth("search-token", SEARCH_SCOPE),
        _results(*targets),
        *_oauth("content-token", DOCUMENT_SCOPE),
        {"data": {"document": {"document_id": _DOC1, "title": _TITLE}}},
        {"data": {"content": "已验证的正文"}},
    ]
    try:
        assert await instance.handle(msg)
        search_ticket = next(iter(instance.jobs))
        assert instance.jobs[search_ticket].protocol == SEARCH_JOB_PROTOCOL
        assert "只申请按标题搜索" in send.await_args.args[0].text
        api.assert_not_awaited()
        await instance.callback(**await _authorize(instance, search_ticket, SEARCH_SCOPE))
        await instance.drain()
        content_ticket = next(ticket for ticket in instance.jobs if ticket != search_ticket)
        assert instance.jobs[content_ticket].task_ref == instance.jobs[search_ticket].task_ref
        assert [call.args[0] for call in api.await_args_list] == ["token", "identity", "file_search"]
        summarize.assert_not_awaited()
        assert "唯一同名" in send.await_args.args[0].text
        await instance.callback(**await _authorize(instance, content_ticket, DOCUMENT_SCOPE))
        await instance.drain()
        api_calls = api.await_args_list
        assert api_calls[2].kwargs == {"token": "search-token", "search_query": _TITLE, "page_token": None}
        assert [(call.args[0], call.kwargs) for call in api_calls[-2:]] == [(op, {"token": "content-token", "document_id": _DOC1}) for op in ("document_info", "document_content")]
        summarize.assert_awaited_once_with(_REQUEST, _TITLE, "已验证的正文")
        turn_planner.assert_awaited_once()
        assert send.await_args.args[0].thread_ts == msg.thread_ts
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_legacy_search_consent_uses_only_legacy_api_and_separate_drive_grant(monkeypatch):
    _enable(monkeypatch, scope=LEGACY_SEARCH_SCOPE)
    drive_scope = "drive:file:download"
    monkeypatch.setenv("DEER_FLOW_WORK_FILE_READ_ENABLED", "true")
    monkeypatch.setenv("DEER_FLOW_WORK_UAT_DRIVE_READ_SCOPE", drive_scope)
    title, file_token = "项目复盘.docx", "fileLegacySearch1"
    request = f"请在海能 Work 里读取并总结《{title}》"
    turn_planner = _plan(title=title)
    instance, _, api, send, summarize = service(turn_planner=turn_planner)
    msg = inbound(text=request)
    api.side_effect = [
        *_oauth("synthetic-legacy-search", LEGACY_SEARCH_SCOPE),
        {"data": {"docs_entities": [{"docs_type": "file", "docs_token": file_token, "title": title}], "has_more": False}},
        *_oauth("synthetic-drive-read", drive_scope),
        {"content": _docx("<w:p><w:r><w:t>旧版命中的文件正文</w:t></w:r></w:p>"), "filename": title, "content_type": "application/octet-stream"},
    ]
    try:
        p = instance._identity(msg)
        instance.vault.put(p, _grant("existing-calendar", CALENDAR_AUTH_SCOPE))
        assert await instance.handle(msg)
        search_ticket = next(iter(instance.jobs))
        assert "查看、评论和下载" in send.await_args.args[0].text
        await instance.callback(**await _authorize(instance, search_ticket, LEGACY_SEARCH_SCOPE))
        await instance.drain()
        assert [call.args[0] for call in api.await_args_list] == ["token", "identity", "file_search_legacy"]
        assert api.await_args.kwargs == {"token": "synthetic-legacy-search", "search_query": title, "search_offset": 0}
        summarize.assert_not_awaited()
        read_ticket = next(ticket for ticket in instance.jobs if ticket != search_ticket)
        assert instance.jobs[read_ticket].task_ref == instance.jobs[search_ticket].task_ref
        await instance.callback(**await _authorize(instance, read_ticket, drive_scope))
        await instance.drain()
        assert [call.args[0] for call in api.await_args_list] == ["token", "identity", "file_search_legacy", "token", "identity", "drive_file_download"]
        api.assert_awaited_with("drive_file_download", token="synthetic-drive-read", document_id=file_token)
        summarize.assert_awaited_once_with(request, title, "旧版命中的文件正文")
        assert (await instance.vault.require(p, CALENDAR_AUTH_SCOPE)).token == "existing-calendar"
        assert (await instance._search_vault.require(p, LEGACY_SEARCH_SCOPE)).token == "synthetic-legacy-search"
        turn_planner.assert_awaited_once()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_pending_v2_consent_cannot_exchange_token_after_legacy_scope_switch(stores, monkeypatch):  # noqa: F811
    _enable(monkeypatch, "PRODUCTION")
    first_store, second_store, _ = stores
    first = production_service(first_store, turn_planner=_plan())
    second = None
    msg = principal_message(text=_REQUEST)
    try:
        assert await first.handle(msg)
        ticket = next(iter(first.jobs))
        callback = await _authorize(first, ticket, SEARCH_SCOPE)
        await first.close()
        _enable(monkeypatch, "PRODUCTION", scope=LEGACY_SEARCH_SCOPE)
        second = production_service(second_store)
        with pytest.raises(WorkError, match="file_search_unavailable"):
            await second.callback(**callback)
        second._api.assert_not_awaited()
        with pytest.raises(WorkError, match="authorization_required"):
            await second._search_vault.require(second._identity(msg), LEGACY_SEARCH_SCOPE)
    finally:
        await first.close()
        if second is not None:
            await second.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["list_only", "partial", "fuzzy", "empty"])
async def test_nonunique_or_list_only_result_never_requests_content_without_choice(monkeypatch, mode):
    _enable(monkeypatch)
    instance, _, api, send, summarize = service(turn_planner=_plan(read=mode != "list_only"))
    msg = inbound(text=f"在海能 Work 中找《{_TITLE}》" if mode == "list_only" else _REQUEST)
    targets = [] if mode == "empty" else [(_TITLE + "副本" if mode == "fuzzy" else _TITLE, "DOCX", _DOC1)]
    api.side_effect = [_results(*targets, more=mode == "partial")]
    try:
        p = instance._identity(msg)
        instance._search_vault.put(p, _grant("search-token", SEARCH_SCOPE))
        instance.vault.put(p, _grant("content-token", DOCUMENT_SCOPE))
        assert await instance.handle(msg)
        await instance.drain()
        api.assert_awaited_once_with("file_search", token="search-token", search_query=_TITLE, page_token=None)
        summarize.assert_not_awaited()
        assert len(instance.jobs) == 1
        text = send.await_args.args[0].text
        if mode == "empty":
            assert "没有找到" in text
        else:
            ref = TaskRef.parse(next(iter(instance.jobs.values())).task_ref)
            context = (await instance._turn_state.read_task_context(instance.vault.principal_key(p), ref)).value
            assert "直接回复序号" in text and "尚未读取正文" in text
            assert "/work 选择" not in text and context["file_search_selection"]["code"] not in text
    finally:
        await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("requested", ["2023月度工作日历", "2023年 月度工作日历", "２０２３年月度工作日历"])
async def test_legacy_approximate_sheet_retains_candidate_without_reading_even_with_consent(monkeypatch, requested):
    _enable(monkeypatch, scope=LEGACY_SEARCH_SCOPE)
    sheet_scope = "sheets:spreadsheet:readonly"
    monkeypatch.setenv("DEER_FLOW_WORK_FILE_READ_ENABLED", "true")
    monkeypatch.setenv("DEER_FLOW_WORK_UAT_SHEETS_READ_SCOPE", sheet_scope)
    title, resource = "2023年月度工作日历", "shtSyntheticCalendar1"
    request = f"读取云空间里的《{requested}》"
    instance, _, api, send, summarize = service(turn_planner=_plan(title=requested))
    msg = inbound(text=request)
    api.side_effect = [{"data": {"docs_entities": [{"title": title, "docs_type": "sheet", "docs_token": resource}], "has_more": False}}]
    try:
        actor = instance._identity(msg)
        instance._search_vault.put(actor, _grant("search-only-token", LEGACY_SEARCH_SCOPE))
        instance._file_vaults["sheet"].put(actor, _grant("existing-sheet-token", sheet_scope))
        assert await instance.handle(msg)
        await instance.drain()
        api.assert_awaited_once_with("file_search_legacy", token="search-only-token", search_query=requested, search_offset=0)
        summarize.assert_not_awaited()
        assert len(instance.jobs) == 1
        text = send.await_args.args[0].text
        assert title in text and "直接回复序号" in text and "尚未读取正文" in text
        ref = TaskRef.parse(next(iter(instance.jobs.values())).task_ref)
        context = (await instance._turn_state.read_task_context(instance.vault.principal_key(actor), ref)).value
        assert context["file_search_selection"]["items"] == [{"kind": "sheet", "token": resource, "url": f"https://haiyoutest.w.cnooc.com.cn/sheets/{resource}"}]
    finally:
        await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("duplicate", [False, True])
async def test_legacy_sheet_auto_handoff_requires_one_exact_name(monkeypatch, duplicate):
    _enable(monkeypatch, scope=LEGACY_SEARCH_SCOPE)
    sheet_scope = "sheets:spreadsheet:readonly"
    monkeypatch.setenv("DEER_FLOW_WORK_FILE_READ_ENABLED", "true")
    monkeypatch.setenv("DEER_FLOW_WORK_UAT_SHEETS_READ_SCOPE", sheet_scope)
    title = "2023年月度工作日历"
    instance, _, api, send, summarize = service(turn_planner=_plan(title=title))
    entries = [{"title": title, "docs_type": "sheet", "docs_token": "shtExactCalendar1"}]
    if duplicate:
        entries.append({"title": title, "docs_type": "sheet", "docs_token": "shtExactCalendar2"})
    api.side_effect = [{"data": {"docs_entities": entries, "has_more": False}}]
    msg = inbound(text=f"读取云空间里的《{title}》")
    try:
        instance._search_vault.put(instance._identity(msg), _grant("search-token", LEGACY_SEARCH_SCOPE))
        assert await instance.handle(msg)
        await instance.drain()
        assert api.await_count == 1
        summarize.assert_not_awaited()
        if duplicate:
            assert len(instance.jobs) == 1
            assert "直接回复序号" in send.await_args.args[0].text
        else:
            read_jobs = [job for job in instance.jobs.values() if job.scope == sheet_scope]
            assert len(read_jobs) == 1
            assert read_jobs[0].intent["token"] == "shtExactCalendar1"
            assert "唯一同名" in send.await_args.args[0].text
    finally:
        await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("use_plain_number", [True, False])
async def test_same_name_choice_survives_cross_pod_and_cannot_be_reused_or_cross_owner(stores, monkeypatch, use_plain_number):  # noqa: F811
    _enable(monkeypatch, "PRODUCTION")
    first_store, second_store, _ = stores
    first_api = AsyncMock(side_effect=[_results((_TITLE, "DOCX", _DOC1), (_TITLE, "DOCX", _DOC2))])
    second_api = AsyncMock(side_effect=[{"data": {"document": {"document_id": _DOC2, "title": _TITLE}}}, {"data": {"content": "只读取第二个"}}])
    first = production_service(first_store, first_api, turn_planner=_plan())
    second = production_service(second_store, second_api, turn_planner=AsyncMock(return_value=WorkTurnDecision(route="general")))
    msg = principal_message(text=_REQUEST)
    try:
        p = first._identity(msg)
        await first.vault.install(p, _grant("existing-calendar-token", CALENDAR_AUTH_SCOPE))
        await first._file_vaults["docx"].install(p, _grant("lookup-content-token", DOCUMENT_SCOPE))
        await first._search_vault.install(p, _grant("search-token", SEARCH_SCOPE))
        assert await first.handle(msg)
        await first.drain()
        first_api.assert_awaited_once()
        source_ref = TaskRef.parse(next(iter(first.jobs.values())).task_ref)
        context = (await first._turn_state.read_task_context(first.vault.principal_key(p), source_ref)).value
        code = context["file_search_selection"]["code"]
        await first.close()
        assert await second.handle(principal_message("B", text=f"/work 选择 2 {code}", source="other-owner"))
        assert not await second.handle(principal_message("B", text="2", source="other-owner-number"))
        second._turn_planner.assert_awaited_once()
        second._turn_planner.reset_mock()
        assert await second.handle(principal_message(text=f"/work 选择 2 {'Z' * 11}", source="wrong-code"))
        assert await second.handle(principal_message(text="5", source="out-of-range"))
        stale_reply = principal_message(text="2", source="reply-to-old-list")
        assert await second.handle(replace(stale_reply, metadata={**stale_reply.metadata, "parent_id": "old-list"}))
        second_api.assert_not_awaited()
        second._turn_planner.assert_not_awaited()
        selection = principal_message(text="2" if use_plain_number else f"/work 选择 2 {code}", source="select-two")
        assert await second.handle(selection)
        await second.drain()
        assert await second.handle(selection)
        assert [(call.args[0], call.kwargs) for call in second_api.await_args_list] == [(op, {"token": "lookup-content-token", "document_id": _DOC2}) for op in ("document_info", "document_content")]
        child_ref = TaskRef.parse(next(iter(second.jobs.values())).task_ref)
        assert child_ref.task_id == source_ref.task_id and child_ref.generation != source_ref.generation
        second._summarize.assert_awaited_once_with(_REQUEST, _TITLE, "只读取第二个")
        assert (await second.vault.require(p, CALENDAR_AUTH_SCOPE)).token == "existing-calendar-token"
        assert (await second._search_vault.require(p, SEARCH_SCOPE)).token == "search-token"
        assert await second.handle(principal_message(text=f"/work 选择 1 {code}", source="replay"))
        assert await second.handle(principal_message(text="2", source="number-after-selection"))
        assert "上次选择已受理" in second.send.await_args.args[0].text
        second._turn_planner.assert_not_awaited()
        await second._turn_state.assert_task(second.vault.principal_key(p), child_ref)
        await second.drain()
        assert second_api.await_count == 2
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_cross_pod_search_callback_hands_off_with_one_active_job_per_user(stores, monkeypatch):  # noqa: F811
    _enable(monkeypatch, "PRODUCTION")
    first_store, second_store, _ = stores
    first = production_service(first_store, turn_planner=_plan())
    api = AsyncMock(
        side_effect=[
            *_oauth("search-token", SEARCH_SCOPE, "ou-A"),
            _results((_TITLE, "DOCX", _DOC1)),
            *_oauth("lookup-content-token", DOCUMENT_SCOPE, "ou-A"),
            {"data": {"document": {"document_id": _DOC1, "title": _TITLE}}},
            {"data": {"content": "跨实例读取"}},
        ]
    )
    second = production_service(second_store, api)
    second.settings = replace(second.settings, per_user_jobs=1)
    msg = principal_message(text=_REQUEST)
    try:
        p = first._identity(msg)
        await first.vault.install(p, _grant("old-calendar-token", CALENDAR_AUTH_SCOPE))
        assert await first.handle(msg)
        ticket = next(iter(first.jobs))
        await first.close()
        await second.callback(**await _authorize(second, ticket, SEARCH_SCOPE))
        await second.drain()
        assert [call.args[0] for call in api.await_args_list] == ["token", "identity", "file_search"]
        content_ticket = next(child for child in second.jobs if child != ticket)
        await second.callback(**await _authorize(second, content_ticket, DOCUMENT_SCOPE))
        await second.drain()
        assert [call.args[0] for call in api.await_args_list] == ["token", "identity", "file_search", "token", "identity", "document_info", "document_content"]
        assert api.await_args_list[2].kwargs["token"] == "search-token"
        assert api.await_args.kwargs["token"] == "lookup-content-token"
        assert (await second.vault.require(p, CALENDAR_AUTH_SCOPE)).token == "old-calendar-token"
        assert (await second._file_vaults["docx"].require(p, DOCUMENT_SCOPE)).token == "lookup-content-token"
        assert len(second.jobs) == 2 and all(job.stage == "completed" for job in second.jobs.values())
        second._turn_planner.assert_not_awaited()
        second._summarize.assert_awaited_once_with(_REQUEST, _TITLE, "跨实例读取")
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["expired", "new_task"])
async def test_stale_selection_neither_reads_nor_supersedes_current_task(monkeypatch, mode):
    _enable(monkeypatch)
    instance, _, api, send, _ = service(turn_planner=_plan())
    msg = inbound(text=_REQUEST)
    api.side_effect = [_results((_TITLE, "DOCX", _DOC1), (_TITLE, "DOCX", _DOC2))]
    try:
        p = instance._identity(msg)
        owner = instance.vault.principal_key(p)
        instance._search_vault.put(p, _grant("search-token", SEARCH_SCOPE))
        assert await instance.handle(msg)
        await instance.drain()
        active_ref = TaskRef.parse(next(iter(instance.jobs.values())).task_ref)
        context = (await instance._turn_state.read_task_context(owner, active_ref)).value
        code = context["file_search_selection"]["code"]
        if mode == "expired":
            context["file_search_selection"]["expires_at"] = time.time() - 1
            await instance._turn_state.save_task_context(owner, active_ref, context)
        else:
            turn = await instance._turn_state.reserve_turn(owner, "new-calendar-task")
            active_ref = await instance._turn_state.finish_turn(owner, turn.turn_id, mode="new", task={"domain": "calendar"})
        assert await instance.handle(inbound(text=f"/work 选择 2 {code}", thread_ts="stale-choice"))
        await instance.drain()
        assert api.await_count == 1 and "没有可选" in send.await_args.args[0].text
        await instance._turn_state.assert_task(owner, active_ref)
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_exact_search_with_disabled_content_reader_returns_link_without_extra_oauth(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.delenv("DEER_FLOW_WORK_FILE_READ_ENABLED", raising=False)
    instance, _, api, send, _ = service(turn_planner=_plan())
    msg = inbound(text=_REQUEST)
    api.side_effect = [_results((_TITLE, "BITABLE", "baseSynthetic"))]
    try:
        instance._search_vault.put(instance._identity(msg), _grant("search-token", SEARCH_SCOPE))
        assert await instance.handle(msg)
        await instance.drain()
        assert api.await_count == 1 and len(instance.jobs) == 1
        text = send.await_args.args[0].text
        assert "未开通" in text and "/base/baseSynthetic" in text
        assert "/work 选择" not in text and "授权并继续" not in text
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_superseded_search_discards_results_before_showing_or_reading(monkeypatch):
    _enable(monkeypatch)
    instance, _, api, send, _ = service(turn_planner=_plan())
    msg = inbound(text=_REQUEST)
    p = instance._identity(msg)
    owner = instance.vault.principal_key(p)

    async def finish_different_task(*_args, **_kwargs):
        turn = await instance._turn_state.reserve_turn(owner, "switch-during-search")
        await instance._turn_state.finish_turn(owner, turn.turn_id, mode="new", task={"domain": "calendar"})
        return _results((_TITLE, "DOCX", _DOC1))

    api.side_effect = finish_different_task
    try:
        instance._search_vault.put(p, _grant("search-token", SEARCH_SCOPE))
        assert await instance.handle(msg)
        await instance.drain()
        assert api.await_count == 1 and len(instance.jobs) == 1
        assert next(iter(instance.jobs.values())).stage == "cancelled"
        assert not any(_DOC1 in call.args[0].text or "授权并继续" in call.args[0].text for call in send.await_args_list)
    finally:
        await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["disabled", "invented_title", "wrong_account", "search_scope_missing"])
async def test_invalid_title_or_search_grant_never_reaches_provider_search(monkeypatch, mode):
    if mode != "disabled":
        _enable(monkeypatch)
    instance, _, api, _, _ = service(turn_planner=_plan(title="模型猜的标题" if mode == "invented_title" else _TITLE))
    try:
        assert await instance.handle(inbound(text=_REQUEST))
        if mode in {"disabled", "invented_title"}:
            assert not instance.jobs
            api.assert_not_awaited()
            return
        api.side_effect = _oauth("invalid-search-token", DOCUMENT_SCOPE if mode == "search_scope_missing" else SEARCH_SCOPE, sender="ou-other" if mode == "wrong_account" else "ou-sender")
        with pytest.raises(WorkError, match="authorization_user_mismatch" if mode == "wrong_account" else "scope_not_granted"):
            await instance.callback(**await _authorize(instance, next(iter(instance.jobs)), SEARCH_SCOPE))
        assert [call.args[0] for call in api.await_args_list] == ["token", "identity"]
    finally:
        await instance.close()

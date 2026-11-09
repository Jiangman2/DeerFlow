"""Work Sheets/Drive use fixed read APIs and separate, sender-bound grants."""

import time
from unittest.mock import AsyncMock, call
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from test_work_documents import inbound, service, work_decision
from test_work_file_content import _docx, _pdf
from test_work_production import principal_message, production_service, stores  # noqa: F401

from app.integrations.work.auth import Grant
from app.integrations.work.capabilities import Action
from app.integrations.work.client import WorkAPI, WorkError
from app.integrations.work.file_content import MAX_FILE_BYTES
from app.integrations.work.file_read import FILE_JOB_PROTOCOL, FILE_PLAN_TARGET, WorkFileRef, WorkFileSettings, file_links, read_file
from app.integrations.work.routing import WorkTurnDecision
from app.integrations.work.scopes import CALENDAR_AUTH_SCOPE, DOCUMENT_SCOPE

HOST = "haiyoutest.w.cnooc.com.cn"
SHEET = "https://" + HOST + "/sheets/shtSynthetic"
DRIVE = "https://" + HOST + "/file/fileSynthetic"
BASE = "https://" + HOST + "/base/baseSynthetic"
SHEET_SCOPE = "sheets:spreadsheet:readonly"
DRIVE_SCOPE = "drive:file:download"


def enable_files(monkeypatch, profile):
    monkeypatch.setenv("DEER_FLOW_WORK_FILE_READ_ENABLED", "true")
    monkeypatch.setenv(f"DEER_FLOW_WORK_{profile}_SHEETS_READ_SCOPE", SHEET_SCOPE)
    monkeypatch.setenv(f"DEER_FLOW_WORK_{profile}_DRIVE_READ_SCOPE", DRIVE_SCOPE)


def read_planner():
    return AsyncMock(return_value=work_decision(Action(operation="document_read", document_id=FILE_PLAN_TARGET)))


def sheet_tabs():
    return {"data": {"sheets": [{"sheet_id": "tab1", "title": "库存", "grid_properties": {"row_count": 320, "column_count": 4}}]}}


def sheet_values():
    return {"data": {"valueRange": {"values": [["设备", "数量"], ["A308", 4]]}}}


def test_links_require_profile_origin_and_single_validated_tab():
    assert file_links(f"总结 {SHEET}?sheet=tab1，文件 {DRIVE}", HOST) == [
        WorkFileRef("sheet", "shtSynthetic", "tab1"),
        WorkFileRef("drive", "fileSynthetic"),
    ]
    for url in (
        SHEET + "?sheet=",
        SHEET + "?sheet=tab1&sheet=tab2",
        SHEET + "?sheet=..",
        SHEET.replace("https:", "http:"),
        SHEET.replace(HOST, HOST + ".evil.test"),
        SHEET.replace(HOST, "evil.test@" + HOST),
        DRIVE + "/extra",
        DRIVE + "%2fprivate",
    ):
        assert not file_links(url, HOST)


def test_scopes_are_opt_in_profile_specific_and_read_only(monkeypatch):
    enable_files(monkeypatch, "UAT")
    assert WorkFileSettings.from_env("uat").scope_for("sheet") == SHEET_SCOPE
    monkeypatch.setenv("DEER_FLOW_WORK_PRODUCTION_SHEETS_READ_SCOPE", "sheets:spreadsheet")
    monkeypatch.setenv("DEER_FLOW_WORK_PRODUCTION_DRIVE_READ_SCOPE", DRIVE_SCOPE + " calendar:calendar")
    assert WorkFileSettings.from_env("production") == WorkFileSettings()
    monkeypatch.delenv("DEER_FLOW_WORK_FILE_READ_ENABLED")
    assert WorkFileSettings.from_env("uat") == WorkFileSettings()


def test_bitable_selection_round_trips_and_rejects_malformed_query():
    selected = WorkFileRef("bitable", "baseSynthetic", table_id="tblMain", view_id="vewOpen")
    assert file_links("总结 " + BASE + "?table=tblMain&view=vewOpen", HOST) == [selected]
    assert selected.url(HOST) == BASE + "?table=tblMain&view=vewOpen"
    assert WorkFileRef.from_intent(selected.intent()) == selected
    for query in ("?table=..", "?table=tblMain&table=tblOther", "?view=https://external.test", "?view="):
        assert not file_links(BASE + query, HOST)
    with pytest.raises(WorkError, match="invalid_file_reference"):
        WorkFileRef.from_intent({**selected.intent(), "view_id": "vew../wrong"})


@pytest.mark.asyncio
async def test_selected_docx_read_uses_fixed_identity_and_keeps_direct_link_path_unchanged():
    ref = WorkFileRef("docx", "docxSynthetic")
    assert WorkFileRef.from_intent(ref.intent()) == ref
    assert ref.url(HOST) == f"https://{HOST}/docx/docxSynthetic"
    assert not file_links(ref.url(HOST), HOST)
    assert WorkFileSettings().scope_for("docx") == DOCUMENT_SCOPE
    wrong = AsyncMock(return_value={"data": {"document": {"title": "Other", "document_id": "wrongDocument"}}})
    with pytest.raises(WorkError, match="document_info_invalid"):
        await read_file(ref, wrong)
    wrong.assert_awaited_once_with("document_info", document_id=ref.token)
    request = AsyncMock(side_effect=[{"data": {"document": {"title": "Synthetic", "document_id": ref.token}}}, {"data": {"content": "测试正文"}}])
    assert await read_file(ref, request) == ("Synthetic", "测试正文", False)
    assert request.await_args_list == [call("document_info", document_id=ref.token), call("document_content", document_id=ref.token)]


@pytest.mark.parametrize(
    ("value", "allowed"),
    [("bitable:app:readonly", True), ("base:table:read base:record:retrieve", True), ("base:table:read", False), ("bitable:app:readonly base:record:create", False)],
)
def test_bitable_needs_only_approved_read_scopes(monkeypatch, value, allowed):
    enable_files(monkeypatch, "UAT")
    monkeypatch.setenv("DEER_FLOW_WORK_UAT_BITABLE_READ_SCOPE", value)
    assert WorkFileSettings.from_env("uat").scope_for("bitable") == (value if allowed else "")
    assert not WorkFileSettings.from_env("production").scope_for("bitable")


@pytest.mark.asyncio
async def test_bitable_read_uses_exact_view_and_never_follows_complex_fields():
    request = AsyncMock(
        side_effect=[
            {"data": {"items": [{"table_id": "tblOther", "name": "其他"}], "has_more": True, "page_token": "next"}},
            {"data": {"items": [{"table_id": "tblMain", "name": "项目"}]}},
            {"data": {"items": [{"fields": {"状态": [{"text": "进行中", "link": "https://external.test"}], "金额": 42, "附件": [{"file_token": "private"}]}}], "has_more": True}},
        ]
    )
    ref = WorkFileRef("bitable", "baseSynthetic", table_id="tblMain", view_id="vewOpen")
    title, result, truncated = await read_file(ref, request)
    assert title == "Work 多维表格 · 项目"
    assert "进行中" in result and "42" in result and "[复杂字段]" in result
    assert "external.test" not in result and "private" not in result
    assert truncated
    assert request.await_args_list == [
        call("bitable_tables", document_id="baseSynthetic", page_token=None),
        call("bitable_tables", document_id="baseSynthetic", page_token="next"),
        call("bitable_records", document_id="baseSynthetic", block_id="tblMain", view_id="vewOpen"),
    ]


@pytest.mark.asyncio
async def test_bitable_never_reads_records_from_ambiguous_or_wrong_table():
    tables = {"data": {"items": [{"table_id": "tblFirst"}, {"table_id": "tblSecond"}]}}
    for table, view, stage in (("", "vewOnly", "bitable_target_required"), ("tblWrong", "", "bitable_table_not_found")):
        request = AsyncMock(return_value=tables)
        with pytest.raises(WorkError, match=stage):
            await read_file(WorkFileRef("bitable", "baseSynthetic", table_id=table, view_id=view), request)
        request.assert_awaited_once_with("bitable_tables", document_id="baseSynthetic", page_token=None)


@pytest.mark.asyncio
async def test_bitable_default_preview_bounds_visible_tables_and_total_records():
    tables = [
        {"table_id": "tblHidden", "name": "隐藏表", "hidden": True},
        {"table_id": "tblFirst", "name": "第一张"},
        {"table_id": "tblSecond", "name": "第二张"},
        {"table_id": "tblThird", "name": "第三张"},
        {"table_id": "tblFourth", "name": "第四张"},
    ]
    rows = {"data": {"items": [{"fields": {"记录": f"第{row}行"}} for row in range(1, 41)]}}
    request = AsyncMock(side_effect=[{"data": {"items": tables}}, rows, rows, rows])
    title, result, truncated = await read_file(WorkFileRef("bitable", "baseSynthetic"), request)
    assert title == "Work 多维表格"
    assert "第一张" in result and "第三张" in result and "第四张" not in result and "隐藏表" not in result
    assert "第33行" in result and "第34行" not in result and result.count(" 条 ") == 99
    assert truncated
    assert request.await_args_list[1:] == [call("bitable_records", document_id="baseSynthetic", block_id=table, view_id=None, page_size=33) for table in ("tblFirst", "tblSecond", "tblThird")]


@pytest.mark.asyncio
async def test_bitable_table_pagination_is_bounded_for_explicit_target():
    request = AsyncMock(side_effect=[{"data": {"items": [{"table_id": "tblOnlySeen"}], "has_more": True, "page_token": "next-" + str(page)}} for page in range(4)])
    with pytest.raises(WorkError, match="bitable_table_scan_limit"):
        await read_file(WorkFileRef("bitable", "baseSynthetic", table_id="tblBeyondLimit"), request)
    assert request.await_count == 3


@pytest.mark.asyncio
async def test_cross_gateway_file_consent_keeps_calendar_grant_and_isolates_sender(stores, monkeypatch):  # noqa: F811
    enable_files(monkeypatch, "PRODUCTION")
    one, two, _ = stores
    first, second = production_service(one, turn_planner=read_planner()), production_service(two)
    api = AsyncMock(side_effect=[{"access_token": "synthetic-sheet-A", "expires_in": 7200, "scope": SHEET_SCOPE}, {"data": {"open_id": "ou-A"}}, sheet_tabs(), sheet_values()])
    third = production_service(one, api)
    msg = principal_message(text="总结 https://documents.example.test/sheets/shtSynthetic?sheet=tab1 中 A150:D165 的数据")
    owner = first._identity(msg)
    other = first._identity(principal_message("B"))
    try:
        await first.vault.install(owner, Grant("synthetic-calendar-A", time.monotonic() + 600, frozenset(CALENDAR_AUTH_SCOPE.split())))
        assert await first.handle(msg)
        ticket = next(iter(first.jobs))
        assert first._job_records[ticket].value["protocol"] == FILE_JOB_PROTOCOL == 3
        await first.close()
        started = await second.authorize(ticket, "")
        query = parse_qs(urlsplit(started["authorize_url"]).query)
        assert query["scope"] == [SHEET_SCOPE]
        await second.close()
        await third.callback(state=query["state"][0], code="synthetic-code", browser_session=started["browser_session"])
        await third.drain()
        assert [call.args[0] for call in api.await_args_list] == ["token", "identity", "sheet_tabs", "sheet_values"]
        assert api.await_args.kwargs == {"token": "synthetic-sheet-A", "document_id": "shtSynthetic", "block_id": "tab1", "sheet_range": "A150:D165"}
        assert "A150:D165" in third._summarize.await_args.args[2]
        assert "A308\t4" in third._summarize.await_args.args[2]
        output = third.send.await_args.args[0]
        assert output.thread_ts == msg.thread_ts and "A150%3AD165" in output.text
        assert "synthetic-sheet-A" not in output.text + repr(third._summarize.await_args)
        assert (await third.vault.require(owner, CALENDAR_AUTH_SCOPE)).token == "synthetic-calendar-A"
        assert (await third._file_vaults["sheet"].require(owner, SHEET_SCOPE)).token == "synthetic-sheet-A"
        with pytest.raises(WorkError, match="authorization_required"):
            await third._file_vaults["sheet"].require(other, SHEET_SCOPE)
        await third.store.invalidate_owner(third.vault.principal_key(owner))
        for vault, scope in ((third.vault, CALENDAR_AUTH_SCOPE), (third._file_vaults["sheet"], SHEET_SCOPE)):
            with pytest.raises(WorkError, match="authorization_required"):
                await vault.require(owner, scope)
    finally:
        await first.close()
        await second.close()
        await third.close()


@pytest.mark.asyncio
async def test_mismatched_file_job_protocol_stops_callback_before_token_exchange(stores, monkeypatch):  # noqa: F811
    enable_files(monkeypatch, "PRODUCTION")
    one, two, _ = stores
    first, second = production_service(one, turn_planner=read_planner()), production_service(two)
    try:
        assert await first.handle(principal_message(text="总结 https://documents.example.test/file/fileSynthetic"))
        ticket = next(iter(first.jobs))
        started = await first.authorize(ticket, "")
        state = parse_qs(urlsplit(started["authorize_url"]).query)["state"][0]
        record = first._job_records[ticket]
        assert await one.change("job", ticket, record, value={**record.value, "protocol": 2})
        with pytest.raises(WorkError, match="job_protocol_upgrade_required"):
            await second.callback(state=state, code="synthetic-code", browser_session=started["browser_session"])
        second._api.assert_not_awaited()
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("decision", [WorkTurnDecision(route="general"), work_decision(Action(operation="document_rename", document_id=FILE_PLAN_TARGET, title="改名"))])
async def test_nonread_file_turn_never_admits_oauth_or_calls_provider(decision, monkeypatch):
    enable_files(monkeypatch, "UAT")
    instance, _, api, _, summarize = service(turn_planner=AsyncMock(return_value=decision))
    try:
        assert await instance.handle(inbound(text="看看 " + SHEET)) is (decision.route != "general")
        assert not instance.jobs
        api.assert_not_awaited()
        summarize.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("range_input", [" A1:P101", " A1:Q100", "?range=", "?range=A1:B3 读取 D5:F10"])
async def test_invalid_ranges_stop_before_authorization(range_input, monkeypatch):
    enable_files(monkeypatch, "UAT")
    instance, _, api, send, summarize = service(turn_planner=read_planner())
    try:
        assert await instance.handle(inbound(text="总结 " + SHEET + range_input))
        assert not instance.jobs
        assert "100 行、16 列" in send.await_args.args[0].text
        api.assert_not_awaited()
        summarize.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_wrong_account_never_reads_drive_and_download_denial_never_summarizes(monkeypatch):
    enable_files(monkeypatch, "UAT")
    instance, _, api, send, summarize = service(turn_planner=read_planner())
    try:
        msg = inbound(text="总结 " + DRIVE)
        assert await instance.handle(msg)
        started = await instance.authorize(next(iter(instance.jobs)), "")
        state = parse_qs(urlsplit(started["authorize_url"]).query)["state"][0]
        api.side_effect = [{"access_token": "synthetic-wrong-user", "expires_in": 7200, "scope": DRIVE_SCOPE}, {"data": {"open_id": "ou-other"}}]
        with pytest.raises(WorkError, match="authorization_user_mismatch"):
            await instance.callback(state=state, code="synthetic-code", browser_session=started["browser_session"])
        assert [call.args[0] for call in api.await_args_list] == ["token", "identity"]
        api.reset_mock()
        api.side_effect = WorkError("drive_file_download", 403)
        instance._file_vaults["drive"].put(instance._identity(msg), Grant("synthetic-right-user", time.monotonic() + 600, frozenset({DRIVE_SCOPE})))
        assert await instance.handle(inbound(text=msg.text, thread_ts="message-retry"))
        await instance.drain()
        api.assert_awaited_once_with("drive_file_download", token="synthetic-right-user", document_id="fileSynthetic")
        assert "未允许下载" in send.await_args.args[0].text
        summarize.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_drive_read_reuses_matching_grant_and_summarizes_real_pdf_text(monkeypatch):
    enable_files(monkeypatch, "UAT")
    instance, _, api, send, summarize = service(turn_planner=read_planner())
    msg = inbound(text="总结 " + DRIVE)
    try:
        instance._file_vaults["drive"].put(instance._identity(msg), Grant("synthetic-drive-user", time.monotonic() + 600, frozenset({DRIVE_SCOPE})))
        api.side_effect = None
        api.return_value = {"content": _pdf("Synthetic budget 42"), "filename": "fixture.pdf", "content_type": "application/pdf"}
        assert await instance.handle(msg)
        await instance.drain()
        api.assert_awaited_once_with("drive_file_download", token="synthetic-drive-user", document_id="fileSynthetic")
        assert summarize.await_args.args == (msg.text, "fixture.pdf", "第 1 页\nSynthetic budget 42")
        assert DRIVE in send.await_args.args[0].text
        assert "synthetic-drive-user" not in send.await_args.args[0].text
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_direct_bitable_link_uses_only_the_bitable_grant(monkeypatch):
    enable_files(monkeypatch, "UAT")
    scope = "bitable:app:readonly"
    monkeypatch.setenv("DEER_FLOW_WORK_UAT_BITABLE_READ_SCOPE", scope)
    instance, _, api, send, summarize = service(turn_planner=read_planner())
    url = BASE + "?table=tblMain&view=vewOpen"
    msg = inbound(text="总结 " + url)
    try:
        identity = instance._identity(msg)
        instance._file_vaults["drive"].put(identity, Grant("synthetic-drive-user", time.monotonic() + 600, frozenset({DRIVE_SCOPE})))
        instance._file_vaults["bitable"].put(identity, Grant("synthetic-bitable-user", time.monotonic() + 600, frozenset({scope})))
        api.side_effect = [{"data": {"items": [{"table_id": "tblMain", "name": "测试台账"}]}}, {"data": {"items": [{"fields": {"名称": "合成记录"}}]}}]
        assert await instance.handle(msg)
        await instance.drain()
        assert api.await_args_list == [
            call("bitable_tables", token="synthetic-bitable-user", document_id="baseSynthetic", page_token=None),
            call("bitable_records", token="synthetic-bitable-user", document_id="baseSynthetic", block_id="tblMain", view_id="vewOpen"),
        ]
        assert "合成记录" in summarize.await_args.args[2]
        assert url in send.await_args.args[0].text
        assert "synthetic-bitable-user" not in send.await_args.args[0].text + repr(summarize.await_args)
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_drive_read_summarizes_cloud_uploaded_docx_using_existing_download_grant(monkeypatch):
    enable_files(monkeypatch, "UAT")
    instance, _, api, _, summarize = service(turn_planner=read_planner())
    msg = inbound(text="总结 " + DRIVE)
    try:
        instance._file_vaults["drive"].put(instance._identity(msg), Grant("synthetic-drive-user", time.monotonic() + 600, frozenset({DRIVE_SCOPE})))
        api.side_effect = None
        api.return_value = {"content": _docx("<w:p><w:r><w:t>合成 Word 正文</w:t></w:r></w:p>"), "filename": "fixture.docx", "content_type": "application/octet-stream"}
        assert await instance.handle(msg)
        await instance.drain()
        api.assert_awaited_once_with("drive_file_download", token="synthetic-drive-user", document_id="fileSynthetic")
        assert summarize.await_args.args == (msg.text, "fixture.docx", "合成 Word 正文")
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_sheet_only_uses_selected_rectangle_even_when_response_has_extra_cells():
    request = AsyncMock(side_effect=[sheet_tabs(), {"data": {"valueRange": {"range": "tab1!B150:B150", "values": [["Selected", "Unrequested column"], ["Unrequested row"]]}}}])
    _, content, truncated = await read_file(WorkFileRef("sheet", "shtSynthetic", "tab1", "B150:B150"), request)
    assert content.endswith("第150行\tSelected")
    assert "Unrequested" not in content
    assert truncated


@pytest.mark.asyncio
async def test_file_transport_is_get_only_bounded_and_does_not_follow_redirects():
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "GET" and request.url.host == "open.w.cnooc.com.cn"
        assert request.headers["authorization"] == "Bearer synthetic"
        if request.url.path.endswith("/download"):
            if "/fileHuge/" in request.url.path:
                return httpx.Response(200, content=b"", headers={"content-length": str(MAX_FILE_BYTES + 1), "content-type": "application/pdf"})
            if "/fileRedirect/" in request.url.path:
                return httpx.Response(302, headers={"location": "https://external.example.test/download"})
            return httpx.Response(200, content=b"%PDF-synthetic", headers={"content-disposition": 'attachment; filename="sample.pdf"', "content-type": "application/pdf"})
        return httpx.Response(200, json=sheet_values())

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True) as client:
        api = WorkAPI(client)
        result = await api("sheet_values", token="synthetic", document_id="shtSynthetic", block_id="tab1", sheet_range="A1:D100")
        assert result == sheet_values()
        assert requests[-1].url.path == "/open-apis/sheets/v2/spreadsheets/shtSynthetic/values/tab1!A1:D100"
        assert requests[-1].url.params["valueRenderOption"] == "ToString"
        assert (await api("drive_file_download", token="synthetic", document_id="fileSynthetic"))["filename"] == "sample.pdf"
        with pytest.raises(WorkError, match="file_too_large"):
            await api("drive_file_download", token="synthetic", document_id="fileHuge")
        with pytest.raises(WorkError) as exc:
            await api("drive_file_download", token="synthetic", document_id="fileRedirect")
        assert exc.value.http_status == 302 and len(requests) == 4
        with pytest.raises(WorkError, match="invalid_sheet_range"):
            await api("sheet_values", token="synthetic", document_id="shtSynthetic", block_id="tab1", sheet_range="A1:Z101")
        assert len(requests) == 4

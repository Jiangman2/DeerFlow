import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.integrations.work.client import WORK_API, WorkAPI, WorkError
from app.integrations.work.file_search import LEGACY_SEARCH_SCOPE, SEARCH_SCOPE, WorkSearchSettings, is_exact_title, search_documents

HOST = "documents.example.test"
TITLE = "AA 标准测试.docx"


def _unit(title, kind, token, **metadata):
    return {"title_highlighted": title, "result_meta": {"doc_types": kind, "token": token, **metadata}}


def _page(*units, cursor=None):
    return {"code": 0, "data": {"res_units": list(units), "has_more": cursor is not None, "page_token": cursor}}


def test_search_permission_is_opt_in_and_profile_specific(monkeypatch):
    monkeypatch.delenv("DEER_FLOW_WORK_FILE_SEARCH_ENABLED", raising=False)
    monkeypatch.setenv("DEER_FLOW_WORK_UAT_FILE_SEARCH_SCOPE", SEARCH_SCOPE)
    assert not WorkSearchSettings.from_env("uat").scope
    monkeypatch.setenv("DEER_FLOW_WORK_FILE_SEARCH_ENABLED", "true")
    monkeypatch.setenv("DEER_FLOW_WORK_PRODUCTION_FILE_SEARCH_SCOPE", f"{SEARCH_SCOPE} drive:drive")
    assert WorkSearchSettings.from_env("uat").scope == SEARCH_SCOPE
    assert not WorkSearchSettings.from_env("production").scope
    monkeypatch.setenv("DEER_FLOW_WORK_UAT_FILE_SEARCH_SCOPE", LEGACY_SEARCH_SCOPE)
    monkeypatch.setenv("DEER_FLOW_WORK_PRODUCTION_FILE_SEARCH_SCOPE", "drive:drive")
    assert WorkSearchSettings.from_env("uat").scope == LEGACY_SEARCH_SCOPE
    assert not WorkSearchSettings.from_env("production").scope


def _legacy_page(*entries, more=False):
    return {"code": 0, "data": {"docs_entities": list(entries), "has_more": more}}


def _legacy_unit(title, kind, token):
    return {"title": title, "docs_type": kind, "docs_token": token}


@pytest.mark.asyncio
async def test_legacy_search_uses_user_authority_and_offset_and_filters_content_matches():
    title = "测" * 32 + ".docx"
    request_bodies = []

    def transport(request):
        assert request.method == "POST"
        assert str(request.url) == WORK_API + "/open-apis/suite/docs-api/search/object"
        assert request.headers["authorization"] == "Bearer legacy-user-grant"
        body = json.loads(request.content)
        request_bodies.append(body)
        if body["offset"] == 0:
            return httpx.Response(200, json=_legacy_page(_legacy_unit(title + "备份", "file", "FileToken2"), _legacy_unit("只匹配正文的文件", "file", "FileToken3"), more=True))
        return httpx.Response(
            200, json=_legacy_page(_legacy_unit(title, "file", "FileToken1"), _legacy_unit(title + "报表", "sheet", "SheetToken1"), _legacy_unit(title + "表", "bitable", "BaseToken1"), _legacy_unit(title + "文档", "docx", "DocToken1"))
        )

    guard = AsyncMock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        result = await search_documents(WorkAPI(client), "legacy-user-grant", title, host=HOST, guard=guard, scope=LEGACY_SEARCH_SCOPE)
    expected = {"search_key": title, "count": 20, "docs_types": ["doc", "sheet", "bitable", "file"]}
    assert request_bodies == [{**expected, "offset": 0}, {**expected, "offset": 2}]
    assert guard.await_count == 2
    assert result.complete
    assert [item.url for item in result.items] == [
        f"https://{HOST}/file/FileToken1",
        f"https://{HOST}/file/FileToken2",
        f"https://{HOST}/sheets/SheetToken1",
        f"https://{HOST}/base/BaseToken1",
        f"https://{HOST}/docx/DocToken1",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "pages",
    [
        [_legacy_page(_legacy_unit(TITLE, "file", "FileToken1"), _legacy_unit(TITLE, "doc", "GenericDoc1"))],
        [_legacy_page(_legacy_unit(TITLE, "file", "FileToken1"), more=True), _legacy_page(_legacy_unit(TITLE, "file", "FileToken1"))],
        [_legacy_page(_legacy_unit(TITLE, "file", "FileToken1"), more=True), _legacy_page(more=True)],
        [_legacy_page(_legacy_unit(TITLE, "file", "FileToken1"), more=True), _legacy_page(_legacy_unit(TITLE, "file", "FileToken2"), more=True), _legacy_page(_legacy_unit(TITLE, "file", "FileToken3"), more=True)],
    ],
)
async def test_legacy_search_never_treats_ambiguous_or_unfinished_results_as_complete(pages):
    api = AsyncMock(side_effect=pages)
    result = await search_documents(api, "search-token", TITLE, host=HOST, guard=AsyncMock(), scope=LEGACY_SEARCH_SCOPE)
    assert not result.complete
    assert result.items[0].token == "FileToken1"
    assert api.await_count == len(pages)


@pytest.mark.asyncio
async def test_user_search_sends_bounded_title_only_request_and_normalizes_typed_matches():
    requests = []

    def transport(request):
        requests.append(request)
        assert request.method == "POST"
        assert str(request.url) == WORK_API + "/open-apis/search/v2/doc_wiki/search"
        assert request.headers["authorization"] == "Bearer user-search-grant"
        expected = {"query": TITLE, "page_size": 20, "doc_filter": {"only_title": True, "doc_types": ["DOCX", "SHEET", "BITABLE", "FILE"]}}
        if len(requests) == 1:
            assert json.loads(request.content) == expected
            return httpx.Response(200, json=_page(_unit("AA 标准测试", "docx", "DocToken1"), cursor="page-2"))
        assert json.loads(request.content) == {**expected, "page_token": "page-2"}
        return httpx.Response(
            200,
            json=_page(
                _unit("<h>AA</h> <hb>标准测试</hb>.docx", "FILE", "FileToken1", url="https://untrusted.test/a", update_time=1_700_000_000),
                _unit("AA 标准测试", "DOCX", "DocToken1"),
                _unit("AA 标准测试", "SHEET", "SheetToken1"),
                _unit("AA 标准测试", "BITABLE", "BaseToken1"),
                _unit("AA 标准测试", "WIKI", "WikiToken1"),
                _unit(TITLE, "FILE", "ForeignFile1", is_cross_tenant=True),
            ),
        )

    guard = AsyncMock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        result = await search_documents(WorkAPI(client), "user-search-grant", TITLE, host=HOST, guard=guard)
    assert guard.await_count == 2
    assert result.complete
    assert [(item.kind, item.url) for item in result.items] == [
        ("drive", f"https://{HOST}/file/FileToken1"),
        ("docx", f"https://{HOST}/docx/DocToken1"),
        ("sheet", f"https://{HOST}/sheets/SheetToken1"),
        ("bitable", f"https://{HOST}/base/BaseToken1"),
    ]
    assert result.items[0].updated_at == "2023-11-15 06:13"
    assert is_exact_title(result.items[0].title, TITLE)
    assert not is_exact_title(result.items[1].title, TITLE)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("pages", "count"),
    [
        ([_page(_unit(TITLE, "FILE", "FileToken1"), cursor="next"), _page(cursor="next")], 2),
        ([_page(_unit(TITLE, "FILE", "FileToken1"), cursor="bad\nvalue")], 1),
        ([_page(_unit(TITLE, "FILE", "FileToken1"), cursor="a"), _page(cursor="b"), _page(cursor="c")], 3),
        ([_page(_unit(TITLE, "FILE", "FileToken1"), _unit(TITLE, "FILE", "../outside"))], 1),
    ],
)
async def test_incomplete_or_malformed_search_cannot_authorize_an_automatic_read(pages, count):
    api = AsyncMock(side_effect=pages)
    result = await search_documents(api, "search-token", TITLE, host=HOST, guard=AsyncMock())
    assert not result.complete
    assert [item.token for item in result.items] == ["FileToken1"]
    assert api.await_count == count


@pytest.mark.asyncio
async def test_long_title_uses_prefix_for_query_and_full_name_for_matching():
    title = "测" * 32 + ".docx"
    api = AsyncMock(return_value=_page(_unit(title[:30], "FILE", "FileToken1"), _unit(title, "FILE", "FileToken2")))
    result = await search_documents(api, "search-token", title, host=HOST, guard=AsyncMock())
    assert api.await_args.kwargs["search_query"] == title[:30]
    assert [item.token for item in result.items] == ["FileToken2", "FileToken1"]


@pytest.mark.asyncio
async def test_search_and_bitable_transport_require_user_grant_and_fixed_read_paths():
    paths = []

    def transport(request):
        assert request.headers["authorization"] == "Bearer user-read-grant"
        body = json.loads(request.content) if request.content else None
        paths.append((request.method, request.url.path, dict(request.url.params), body))
        return httpx.Response(200, json={"code": 0, "data": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        api = WorkAPI(client)
        with pytest.raises(WorkError, match="missing_user_grant"):
            await api("file_search", search_query=TITLE)
        with pytest.raises(WorkError, match="invalid_file_reference"):
            await api("bitable_records", token="user-read-grant", document_id="BaseToken1", block_id="../tables")
        await api("bitable_tables", token="user-read-grant", document_id="BaseToken1", page_token="next-page")
        await api("bitable_records", token="user-read-grant", document_id="BaseToken1", block_id="tblToken1", view_id="vewToken1", payload={"fields": "ignored"})
    assert paths == [
        ("GET", "/open-apis/bitable/v1/apps/BaseToken1/tables", {"page_size": "100", "page_token": "next-page"}, None),
        ("POST", "/open-apis/bitable/v1/apps/BaseToken1/tables/tblToken1/records/search", {"page_size": "100"}, {"automatic_fields": False, "view_id": "vewToken1"}),
    ]

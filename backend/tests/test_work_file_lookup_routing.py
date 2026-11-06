"""Current-turn grounding for read-only Work filename lookup."""

import json
from unittest.mock import AsyncMock

import pytest

from app.integrations.work import agent
from app.integrations.work.capabilities import Action
from app.integrations.work.client import WorkError
from app.integrations.work.routing import FileLookupIntent, WorkTurnDecision, validate_file_lookup


def lookup(title, *, read=True):
    return {"route": "new_work", "domain": "file_lookup", "intent": {"operation": "file_lookup", "title": title, "read": read}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_text", "title", "read"),
    [
        ("读取我云文档空间下的 AA标准测试.docx", "AA标准测试.docx", True),
        ("帮我打开Work里的项目进度表并总结", "项目进度表", True),
        ("读取我云盘里的季度报表.pdf", "季度报表.pdf", True),
        ("帮我查找 Work 里的「2026 项目进度.xlsx」", "2026 项目进度.xlsx", False),
        ("读取 AA标准测试.docx", "AA标准测试.docx", True),
        ("帮我总结一下《项目周报.xlsx》", "项目周报.xlsx", True),
        ("请读取季度报表.PDF.", "季度报表.PDF", True),
        ("Read the quarterly report.pdf.", "quarterly report.pdf", True),
        ("读取我云盘里上传的 AA标准测试.docx", "AA标准测试.docx", True),
    ],
)
async def test_current_work_filename_can_be_found_or_read(request_text, title, read, monkeypatch):
    model = AsyncMock(return_value=json.dumps(lookup(title, read=read)))
    monkeypatch.setattr(agent, "model_text", model)

    decision = await agent.plan_turn(request_text)

    assert decision.intent == FileLookupIntent(operation="file_lookup", title=title, read=read)
    assert decision.model_dump(mode="json")["intent"] == lookup(title, read=read)["intent"]
    fields = model.await_args.args[1]["turn_schema"]["$defs"]["FileLookupIntent"]["properties"]
    assert set(fields) == {"operation", "title", "read"}


@pytest.mark.asyncio
async def test_link_and_ordinary_routes_keep_existing_schema(monkeypatch):
    model = AsyncMock(side_effect=[json.dumps({"route": "general"}), json.dumps({"route": "new_work", "domain": "document", "intent": {"operation": "document_read", "document_id": "doxTest123"}})])
    monkeypatch.setattr(agent, "model_text", model)

    assert (await agent.plan_turn("举例说明文件命名规则")).route == "general"
    url = "https://documents.example.test/docx/doxTest123"
    doc = await agent.plan_turn("总结 Work 里的 " + url, links=[("doxTest123", url)])
    assert isinstance(doc.intent, Action) and doc.intent.document_id == "doxTest123"
    assert all("FileLookupIntent" not in call.args[1]["turn_schema"]["$defs"] for call in model.await_args_list)


@pytest.mark.asyncio
async def test_filename_example_and_bare_filename_do_not_choose_a_file_operation(monkeypatch):
    model = AsyncMock(side_effect=[json.dumps({"route": "general"}), json.dumps(lookup("项目周报.xlsx")), json.dumps({"route": "clarify", "domain": "file_lookup", "question": "要查找文件，还是读取内容？"})])
    monkeypatch.setattr(agent, "model_text", model)

    assert (await agent.plan_turn("用季度报表.pdf举例说明文件命名规则")).route == "general"
    assert (await agent.plan_turn("《项目周报.xlsx》")).route == "clarify"


@pytest.mark.asyncio
@pytest.mark.parametrize("title", ["历史报表.xlsx", "季度报表", "云盘"])
async def test_model_cannot_invent_reuse_or_shorten_a_title(title, monkeypatch):
    model = AsyncMock(return_value=json.dumps(lookup(title)))
    monkeypatch.setattr(agent, "model_text", model)

    with pytest.raises(WorkError, match="turn_model_invalid"):
        await agent.plan_turn("读取我云盘里的季度报表.pdf", task_context={"original_request": "历史报表.xlsx"})
    assert model.await_count == 2


@pytest.mark.parametrize("title", ["https://work.test/secret.pdf", "../财务.xlsx", "财务\\报表.pdf", "财务%2f报表.pdf", "ou_synthetic123", "报表\x1f.pdf"])
def test_lookup_target_cannot_carry_paths_urls_controls_or_provider_ids(title):
    with pytest.raises(ValueError, match="invalid_file_lookup_title"):
        WorkTurnDecision.model_validate(lookup(title))


def test_lookup_cannot_supply_credentials_or_continue_a_historical_task():
    raw = lookup("季度报表.pdf")
    with pytest.raises(ValueError):
        WorkTurnDecision.model_validate({**raw, "intent": {**raw["intent"], "token": "synthetic"}})
    with pytest.raises(ValueError):
        WorkTurnDecision.model_validate({**raw, "intent": {**raw["intent"], "read": "true"}})
    with pytest.raises(ValueError, match="file_lookup_requires_new_current_request"):
        WorkTurnDecision.model_validate({**raw, "route": "continue_work"})

    intent = FileLookupIntent.model_validate(raw["intent"])
    for request_text in (
        "我云盘里的季度报表.pdf，参考 https://example.test",
        "我云盘里的 AA季度报表.pdf",
        "读取本地的季度报表.pdf",
        "读取附件里的季度报表.pdf",
        "读取我刚上传的季度报表.pdf",
        "读取 C:\\test\\季度报表.pdf",
        "读取 目录/季度报表.pdf",
    ):
        with pytest.raises(ValueError, match="file_lookup_title_not_in_current_request"):
            validate_file_lookup(intent, request_text)
    with pytest.raises(ValueError, match="file_lookup_title_not_in_current_request"):
        validate_file_lookup(FileLookupIntent(operation="file_lookup", title="项目周报", read=True), "读取项目周报")


@pytest.mark.parametrize(
    ("request_text", "title", "accepted"),
    [
        ("请在海能 Work 的我的云文档里读取《项目复盘》", "海能 Work", False),
        ("请在海能 Work 中读取《项目复盘》", "复盘", False),
        ("请在海能 Work 中读取《 项目复盘 》", "项目复盘", True),
        ("读取我云盘里的「海能 Work 接入指南」", "海能 Work 接入指南", True),
        ("读取《项目复盘.pdf》", "复盘.pdf", False),
    ],
)
def test_title_grounding_preserves_quoted_name_and_excludes_every_location_marker(request_text, title, accepted):
    intent = FileLookupIntent(operation="file_lookup", title=title, read=True)
    if accepted:
        assert validate_file_lookup(intent, request_text) == intent
    else:
        with pytest.raises(ValueError, match="file_lookup_title_not_in_current_request"):
            validate_file_lookup(intent, request_text)

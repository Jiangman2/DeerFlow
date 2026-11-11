"""Cloud title slots stay grounded while semantic routing remains available."""

import json
import time
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest
from test_work_documents import inbound, service
from test_work_production import principal_message, production_service, stores  # noqa: F401

from app.integrations.work import agent
from app.integrations.work.client import WorkError
from app.integrations.work.routing import WorkTurnDecision, explicit_lookup_facts


def proposal(**slots):
    return {"route": "clarify", "domain": "file_lookup", "lookup": slots, "question": "请提供文档标题。"}


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["云盘里的 ", "云空间的", "云文档 "])
async def test_explicit_title_is_kept_even_if_model_asks_for_it_again(location, monkeypatch):
    model = AsyncMock(return_value=json.dumps(proposal(source="work", read=True)))
    monkeypatch.setattr(agent, "model_text", model)
    decision = await agent.plan_turn("读取" + location + "团队日报汇总")
    assert decision.route == "new_work"
    assert decision.intent.title == "团队日报汇总" and decision.intent.read is True
    assert "FileLookupDraft" in model.await_args.args[1]["turn_schema"]["$defs"]


@pytest.mark.asyncio
async def test_named_cloud_request_does_not_need_location_whitelist(monkeypatch):
    model = AsyncMock(return_value=json.dumps(proposal(source="work", title="团队日报汇总", read=True)))
    monkeypatch.setattr(agent, "model_text", model)
    decision = await agent.plan_turn("麻烦从我的工作资料中调出《团队日报汇总》看看")
    assert decision.route == "new_work" and decision.intent.title == "团队日报汇总"


@pytest.mark.asyncio
async def test_pending_title_short_answer_survives_service_turn_and_oauth_validation(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_WORK_FILE_SEARCH_ENABLED", "true")
    monkeypatch.setenv("DEER_FLOW_WORK_UAT_FILE_SEARCH_SCOPE", "search:docs:read")
    model = AsyncMock(side_effect=[json.dumps(proposal(source="work", read=True)), json.dumps(proposal(title="团队日报汇总"))])
    monkeypatch.setattr(agent, "model_text", model)
    instance, _, api, send, _ = service(turn_planner=agent.plan_turn)
    try:
        assert await instance.handle(inbound(text="帮我读取我的Work云空间文档"))
        assert not instance.jobs and "标题" in send.await_args.args[0].text
        assert await instance.handle(inbound(text="团队日报汇总", thread_ts="second-title-answer"))
        assert len(instance.jobs) == 1
        job = next(iter(instance.jobs.values()))
        assert job.intent["title"] == "团队日报汇总" and job.intent["read"] is True
        assert instance._grant_vault(job) is instance._search_vault
        api.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_unknown_source_preserves_title_and_mode_until_source_answer(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_WORK_FILE_SEARCH_ENABLED", "true")
    monkeypatch.setenv("DEER_FLOW_WORK_UAT_FILE_SEARCH_SCOPE", "search:docs:read")
    model = AsyncMock(side_effect=[json.dumps(proposal(title="团队日报汇总", read=True, source="unknown")), json.dumps(proposal(source="work"))])
    monkeypatch.setattr(agent, "model_text", model)
    instance, _, api, send, _ = service(turn_planner=agent.plan_turn)
    try:
        await instance.handle(inbound(text="读取《团队日报汇总》"))
        assert not instance.jobs and "Work" in send.await_args.args[0].text
        await instance.handle(inbound(text="在海能Work里", thread_ts="source-answer"))
        assert len(instance.jobs) == 1
        assert next(iter(instance.jobs.values())).intent["title"] == "团队日报汇总"
        api.assert_not_awaited()
    finally:
        await instance.close()


def pending_lookup(**slots):
    return {"domain": "file_lookup", "stage": "awaiting_input", "lookup": slots, "lookup_evidence": "我的Work里《团队日报汇总》", "question": "要查找文件还是读取内容？"}


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["总结", "/work 总结"])
async def test_mode_short_answer_reuses_only_pending_canonical_title(reply, monkeypatch):
    model = AsyncMock(return_value=json.dumps(proposal(read=True)))
    monkeypatch.setattr(agent, "model_text", model)
    decision = await agent.plan_turn(reply, task_context=pending_lookup(source="work", title="团队日报汇总"))
    assert decision.intent.title == "团队日报汇总" and decision.intent.read is True
    assert decision.lookup_continuation and "团队日报汇总" in decision.lookup_evidence


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_text", "context", "patch"),
    [
        (
            "团队日报汇总",
            {"domain": "file_lookup", "stage": "awaiting_input", "lookup": {"source": "work", "read": True}, "lookup_evidence": "读取我的Work云空间文档"},
            {"source": "unknown", "title": "团队日报汇总", "read": None, "ambiguity": "none"},
        ),
        ("总结", pending_lookup(source="work", title="团队日报汇总"), {"source": "unknown", "title": None, "read": True, "ambiguity": "none"}),
        ("海能Work里", pending_lookup(source="unknown", title="团队日报汇总", read=True), {"source": "work", "title": None, "read": None, "ambiguity": "none"}),
    ],
)
async def test_serialized_optional_defaults_do_not_erase_verified_pending_slots(request_text, context, patch, monkeypatch):
    monkeypatch.setattr(agent, "model_text", AsyncMock(return_value=json.dumps(proposal(**patch))))
    result = await agent.plan_turn(request_text, task_context=context)
    assert result.route == "new_work" and result.intent.title == "团队日报汇总" and result.intent.read is True
    assert result.lookup.source == "work" and result.lookup_continuation


@pytest.mark.asyncio
async def test_empty_default_patch_cannot_clear_pending_business_conflict(monkeypatch):
    monkeypatch.setattr(agent, "model_text", AsyncMock(return_value=json.dumps(proposal(source="unknown", title=None, read=None, ambiguity="none"))))
    context = pending_lookup(source="work", title="团队日报汇总", read=True, ambiguity="conflicting_request")
    result = await agent.plan_turn("还没决定", task_context=context)
    assert result.route == "clarify" and result.lookup.ambiguity == "conflicting_request" and result.intent is None


@pytest.mark.asyncio
async def test_explicit_source_uncertainty_clears_only_that_slot_and_cannot_later_inherit_work(monkeypatch):
    model = AsyncMock(side_effect=[json.dumps(proposal(source="unknown", title="团队日报汇总", read=True, uncertain_fields=["source"])), json.dumps(proposal(source="unknown", title=None, read=True, uncertain_fields=[]))])
    monkeypatch.setattr(agent, "model_text", model)
    first = await agent.plan_turn("我现在不确定它是否在Work，标题叫团队日报汇总", task_context=pending_lookup(source="work", title="团队日报汇总"))
    assert first.route == "clarify" and first.lookup.source == "unknown" and first.lookup.read is True
    assert first.lookup.title == "团队日报汇总" and first.intent is None
    context = {"domain": "file_lookup", "stage": "awaiting_input", "lookup": first.lookup.model_dump(mode="json"), "lookup_evidence": first.lookup_evidence}
    second = await agent.plan_turn("读取", task_context=context)
    assert second.route == "clarify" and second.lookup.source == "unknown" and second.intent is None


@pytest.mark.asyncio
@pytest.mark.parametrize("echo_read", [None, True])
async def test_adding_only_a_title_never_resolves_prior_read_write_conflict(echo_read, monkeypatch):
    monkeypatch.setattr(agent, "model_text", AsyncMock(return_value=json.dumps(proposal(source="unknown", title="团队日报汇总", read=echo_read, ambiguity="none"))))
    context = pending_lookup(source="work", read=True, ambiguity="conflicting_request")
    result = await agent.plan_turn("团队日报汇总", task_context=context)
    assert result.route == "clarify" and result.lookup.title == "团队日报汇总" and result.lookup.ambiguity == "conflicting_request"
    assert result.intent is None


@pytest.mark.asyncio
@pytest.mark.parametrize("choice,read", [("read_only", True), ("find_only", False)])
async def test_explicit_pure_read_or_find_choice_can_resolve_current_conflict(choice, read, monkeypatch):
    monkeypatch.setattr(agent, "model_text", AsyncMock(return_value=json.dumps(proposal(source="unknown", title=None, read=None, ambiguity="none", conflict_resolution=choice))))
    context = pending_lookup(source="work", title="团队日报汇总", read=True, ambiguity="conflicting_request")
    result = await agent.plan_turn("本次只读取，不修改" if read else "本次只查找链接，不修改", task_context=context)
    assert result.route == "new_work" and result.intent.read is read and result.lookup.conflict_resolution is None


@pytest.mark.asyncio
async def test_conflict_choice_is_unavailable_without_server_pending_conflict(monkeypatch):
    monkeypatch.setattr(agent, "model_text", AsyncMock(return_value=json.dumps(proposal(source="work", title="团队日报汇总", read=True, conflict_resolution="read_only"))))
    with pytest.raises(WorkError, match="turn_model_invalid"):
        await agent.plan_turn("读取云盘里的团队日报汇总")


@pytest.mark.asyncio
@pytest.mark.parametrize("reply", ["团队日报汇总", "标题为团队日报汇总", "还没决定"])
async def test_model_resolution_label_without_current_business_choice_cannot_clear_conflict(reply, monkeypatch):
    monkeypatch.setattr(agent, "model_text", AsyncMock(return_value=json.dumps(proposal(source="unknown", title=None, read=None, ambiguity="none", conflict_resolution="read_only"))))
    result = await agent.plan_turn(reply, task_context=pending_lookup(source="work", title="团队日报汇总", read=True, ambiguity="conflicting_request"))
    assert result.route == "clarify" and result.lookup.ambiguity == "conflicting_request" and result.intent is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["requested", "processing", "completed", "cancelled"])
async def test_non_pending_tasks_cannot_supply_old_titles(stage, monkeypatch):
    model = AsyncMock(return_value=json.dumps(proposal(source="work", read=True, title="团队日报汇总")))
    monkeypatch.setattr(agent, "model_text", model)
    with pytest.raises(WorkError, match="turn_model_invalid"):
        await agent.plan_turn("总结", task_context={**pending_lookup(source="work", title="团队日报汇总"), "stage": stage})
    assert model.await_count == 2


@pytest.mark.asyncio
async def test_new_request_does_not_inherit_pending_source_or_evidence(monkeypatch):
    raw = {"route": "new_work", "domain": "file_lookup", "lookup": {"title": "另一份报告", "read": True}}
    monkeypatch.setattr(agent, "model_text", AsyncMock(return_value=json.dumps(raw)))
    decision = await agent.plan_turn("读取《另一份报告》", task_context=pending_lookup(source="work", title="团队日报汇总"))
    assert decision.route == "clarify" and decision.lookup.source == "unknown"
    assert not decision.lookup_continuation and "团队日报汇总" not in decision.lookup_evidence


@pytest.mark.asyncio
@pytest.mark.parametrize("request_text", ["读取云盘里的A只是个例子", "读取云盘里的A但不要访问", "不要读取云文档，解释光合作用", "换个话题，解释光合作用"])
async def test_correct_general_decision_is_never_overridden_by_title_extraction(request_text, monkeypatch):
    monkeypatch.setattr(agent, "model_text", AsyncMock(return_value='{"route":"general"}'))
    assert (await agent.plan_turn(request_text, task_context=pending_lookup(source="work", read=True))).route == "general"


@pytest.mark.asyncio
@pytest.mark.parametrize("source,text", [("external", "读取百度网盘里的《团队日报汇总》"), ("local", "读取本地的团队日报汇总"), ("attachment", "读取刚上传的附件团队日报汇总")])
async def test_other_sources_leave_work_without_search_or_oauth(source, text, monkeypatch):
    monkeypatch.setattr(agent, "model_text", AsyncMock(return_value=json.dumps(proposal(source=source, read=True, title="团队日报汇总"))))
    instance, _, api, _, _ = service(turn_planner=agent.plan_turn)
    try:
        assert await instance.handle(inbound(text=text)) is False
        assert not instance.jobs
        api.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["local", "attachment", "external"])
async def test_narrow_cloud_title_facts_never_override_explicit_non_work_source(source, monkeypatch):
    monkeypatch.setattr(agent, "model_text", AsyncMock(return_value=json.dumps(proposal(source=source, read=True))))
    assert (await agent.plan_turn("读取云盘里的团队日报汇总")).route == "general"


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["web", "slack", "scheduled"])
async def test_other_message_sources_do_not_enter_work_planning(channel):
    planner = AsyncMock(side_effect=AssertionError("Non-Work input must not reach Work planner"))
    instance, _, api, _, _ = service(turn_planner=planner)
    try:
        assert await instance.handle(inbound(channel_name=channel, text="读取云空间的团队日报汇总")) is False
        planner.assert_not_awaited()
        api.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["读取云盘里的A和B", "读取云盘里的A以及B", "读取云盘里的A并总结"])
async def test_uncertain_or_multiple_targets_cannot_become_narrow_title_facts(text, monkeypatch):
    assert explicit_lookup_facts(text) is None
    raw = proposal(source="work", read=True, ambiguity="multiple_targets")
    monkeypatch.setattr(agent, "model_text", AsyncMock(return_value=json.dumps(raw)))
    decision = await agent.plan_turn(text)
    assert decision.route == "clarify" and decision.intent is None


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["修改云文档团队日报汇总", "先读取云盘里的团队日报汇总再删除", "别读，只修改团队日报汇总"])
async def test_write_requests_are_not_downgraded_to_search(text, monkeypatch):
    raw = {"route": "unsupported", "domain": "file_lookup", "question": "按名称暂不支持修改或删除，请提供具体链接并说明支持范围内的变更。"}
    monkeypatch.setattr(agent, "model_text", AsyncMock(return_value=json.dumps(raw)))
    decision = await agent.plan_turn(text)
    assert decision.route == "unsupported" and decision.intent is None


@pytest.mark.asyncio
async def test_pending_lookup_recovers_after_model_failure_without_saving_failed_answer(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_WORK_FILE_SEARCH_ENABLED", "true")
    monkeypatch.setenv("DEER_FLOW_WORK_UAT_FILE_SEARCH_SCOPE", "search:docs:read")
    model = AsyncMock(side_effect=[json.dumps(proposal(source="work", read=True)), WorkError("turn_model_timeout"), json.dumps(proposal(title="团队日报汇总"))])
    monkeypatch.setattr(agent, "model_text", model)
    instance, _, api, send, _ = service(turn_planner=agent.plan_turn)
    try:
        await instance.handle(inbound(text="帮我读取我的Work云空间文档"))
        await instance.handle(inbound(text="失败的输入", thread_ts="failed-answer"))
        assert "已保留" in send.await_args.args[0].text and not instance.jobs
        await instance.handle(inbound(text="团队日报汇总", thread_ts="retry-answer"))
        assert len(instance.jobs) == 1
        assert "失败的输入" not in next(iter(instance.jobs.values())).message.text
        api.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_expired_lookup_cannot_resume_title_or_source(monkeypatch):
    monkeypatch.setenv("DEER_FLOW_WORK_FILE_SEARCH_ENABLED", "true")
    monkeypatch.setenv("DEER_FLOW_WORK_UAT_FILE_SEARCH_SCOPE", "search:docs:read")
    model = AsyncMock(side_effect=[json.dumps(proposal(source="work", title="团队日报汇总")), json.dumps(proposal(read=True))])
    monkeypatch.setattr(agent, "model_text", model)
    instance, _, api, send, _ = service(turn_planner=agent.plan_turn)
    try:
        await instance.handle(inbound(text="我的Work里《团队日报汇总》"))
        owner = instance.vault.principal_key(instance._identity(inbound()))
        instance._turn_state._rows[owner]["task"]["lookup_expires_at"] = time.time() - 1
        await instance.handle(inbound(text="总结", thread_ts="expired-reply"))
        assert not instance.jobs and "Work" in send.await_args.args[0].text
        api.assert_not_awaited()
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_repeated_unclear_answers_have_bounded_pending_rounds(monkeypatch):
    monkeypatch.setattr(agent, "model_text", AsyncMock(return_value=json.dumps(proposal(source="work", read=True))))
    instance, _, api, send, _ = service(turn_planner=agent.plan_turn)
    try:
        await instance.handle(inbound(text="帮我读取我的Work云空间文档"))
        for index in range(4):
            await instance.handle(inbound(text="还没决定", thread_ts=f"unclear-{index}"))
        assert not instance.jobs and "重新发送" in send.await_args.args[0].text
        owner = instance.vault.principal_key(instance._identity(inbound()))
        assert instance._turn_state._rows[owner]["task_ref"] is None
        api.assert_not_awaited()
    finally:
        await instance.close()


def test_model_cannot_supply_lookup_evidence_or_conflicting_legacy_intent():
    with pytest.raises(ValueError):
        WorkTurnDecision.model_validate({**proposal(source="work", title="报告", read=True), "lookup_evidence": "模型拼的标题"})
    with pytest.raises(ValueError, match="lookup_and_intent_conflict"):
        WorkTurnDecision.model_validate({"route": "new_work", "domain": "file_lookup", "lookup": {"source": "work", "title": "A", "read": True}, "intent": {"operation": "file_lookup", "title": "B", "read": True}})


@pytest.mark.asyncio
async def test_real_provider_null_question_does_not_reject_complete_lookup_proposal(monkeypatch):
    raw = {
        "route": "new_work",
        "domain": "file_lookup",
        "intent": {"operation": "file_lookup", "title": "测试日报汇总", "read": True},
        "lookup": {"source": "work", "title": "测试日报汇总", "read": True, "ambiguity": "none", "uncertain_fields": [], "conflict_resolution": None},
        "question": None,
    }
    model = AsyncMock(return_value=json.dumps(raw))
    monkeypatch.setattr(agent, "model_text", model)
    result = await agent.plan_turn("读取云盘里的测试日报汇总")
    assert result.route == "new_work" and result.question == "" and result.intent.title == "测试日报汇总"
    model.assert_awaited_once()


@pytest.mark.asyncio
async def test_real_provider_matching_intent_and_draft_can_continue_pending_title(monkeypatch):
    raw = {"route": "continue_work", "domain": "file_lookup", "lookup": {"source": "work", "title": "测试日报汇总", "read": True}, "intent": {"operation": "file_lookup", "title": "测试日报汇总", "read": True}}
    monkeypatch.setattr(agent, "model_text", AsyncMock(return_value=json.dumps(raw)))
    context = {"domain": "file_lookup", "stage": "awaiting_input", "lookup": {"source": "work", "read": True}, "lookup_evidence": "帮我读取我的云文档"}
    result = await agent.plan_turn("测试日报汇总", task_context=context)
    assert result.intent.title == "测试日报汇总" and result.lookup_continuation


@pytest.mark.asyncio
async def test_real_provider_redundant_incomplete_intent_asks_only_for_missing_action(monkeypatch):
    raw = {"route": "new_work", "domain": "file_lookup", "lookup": {"source": "work", "title": "测试周报", "read": None}, "intent": {"operation": "file_lookup", "title": "测试周报", "read": None}}
    monkeypatch.setattr(agent, "model_text", AsyncMock(return_value=json.dumps(raw)))
    result = await agent.plan_turn("云空间里的《测试周报》")
    assert result.route == "clarify" and result.lookup.title == "测试周报" and result.lookup.read is None
    assert "查找" in result.question and "标题" not in result.question and result.intent is None


@pytest.mark.asyncio
@pytest.mark.parametrize("redundant_intent", [True, False])
async def test_real_provider_unknown_source_and_new_route_question_preserve_business_uncertainty(redundant_intent, monkeypatch):
    raw = {
        "route": "new_work",
        "domain": "file_lookup",
        "lookup": {"source": "unknown", "title": "另一份测试季度总结", "read": True, "uncertain_fields": ["source"]},
        "intent": {"operation": "file_lookup", "title": "另一份测试季度总结", "read": True} if redundant_intent else None,
        "question": "请确认文件来源。",
    }
    monkeypatch.setattr(agent, "model_text", AsyncMock(return_value=json.dumps(raw)))
    result = await agent.plan_turn("换个新任务，帮我读取《另一份测试季度总结》，这份文件放在哪里我还没确定", task_context=pending_lookup(source="work", title="团队日报汇总", read=True))
    assert result.route == "clarify" and result.lookup.source == "unknown" and result.lookup.title == "另一份测试季度总结"
    assert not result.lookup_continuation and result.intent is None


@pytest.mark.parametrize(
    "intent,lookup",
    [
        ({"operation": "file_lookup", "title": "A", "read": None, "token": "forbidden"}, {"source": "work", "title": "A", "read": None}),
        ({"operation": "file_lookup", "title": "A", "read": None, "document_id": "forbidden"}, {"source": "work", "title": "A", "read": None}),
        ({"operation": "file_lookup", "title": "A", "read": None}, {"source": "work", "title": "B", "read": None}),
        ({"operation": "file_lookup", "title": "A", "read": True}, {"source": "unknown", "title": "A", "read": None}),
        ({"operation": "file_lookup", "title": "A", "read": "true"}, {"source": "work", "title": "A", "read": True}),
        ({"operation": "file_lookup", "title": "A", "read": None}, {"source": "work", "title": "A", "read": None, "credential": "forbidden"}),
    ],
)
def test_provider_shape_normalization_never_discards_credentials_extra_keys_or_conflicting_slots(intent, lookup):
    with pytest.raises(ValueError):
        WorkTurnDecision.model_validate({"route": "new_work", "domain": "file_lookup", "intent": intent, "lookup": lookup})


@pytest.mark.parametrize("question", ["请提供access_token", "打开 https://example.test/authorize", "请告诉我文档ID"])
def test_provider_new_work_question_is_still_bounded_before_server_replaces_it(question):
    with pytest.raises(ValueError):
        WorkTurnDecision.model_validate({"route": "new_work", "domain": "file_lookup", "lookup": {"source": "unknown", "title": "A", "read": True}, "question": question})


@pytest.mark.parametrize("route", ["clarify", "unsupported"])
def test_null_question_still_rejects_routes_without_required_question_or_business_draft(route):
    with pytest.raises(ValueError):
        WorkTurnDecision.model_validate({"route": route, "domain": "file_lookup", "question": None})


@pytest.mark.asyncio
async def test_explicit_title_facts_do_not_erase_model_business_conflicts(monkeypatch):
    raw = proposal(source="work", read=True, ambiguity="conflicting_request")
    monkeypatch.setattr(agent, "model_text", AsyncMock(return_value=json.dumps(raw)))
    result = await agent.plan_turn("读取云盘里的A后删除")
    assert result.route == "clarify" and result.lookup.ambiguity == "conflicting_request" and result.intent is None


@pytest.mark.asyncio
async def test_two_quoted_targets_require_choice_even_when_model_mistakenly_picks_one(monkeypatch):
    model = AsyncMock(side_effect=[json.dumps(proposal(source="work", title="A", read=True)), json.dumps(proposal(title="B"))])
    monkeypatch.setattr(agent, "model_text", model)
    first = await agent.plan_turn("读取云盘里的《A》和《B》")
    assert first.route == "clarify" and first.lookup.title is None and first.lookup.ambiguity == "multiple_targets"
    context = {"domain": "file_lookup", "stage": "awaiting_input", "lookup": first.lookup.model_dump(mode="json"), "lookup_evidence": first.lookup_evidence}
    second = await agent.plan_turn("《B》", task_context=context)
    assert second.route == "new_work" and second.intent.title == "B"
    assert "《A》" not in second.lookup_evidence


@pytest.mark.asyncio
async def test_pending_title_resumes_cross_service_and_frozen_job_revalidates_after_restart(stores, monkeypatch):  # noqa: F811
    monkeypatch.setenv("DEER_FLOW_WORK_FILE_SEARCH_ENABLED", "true")
    monkeypatch.setenv("DEER_FLOW_WORK_PRODUCTION_FILE_SEARCH_SCOPE", "search:docs:read")
    model = AsyncMock(side_effect=[json.dumps(proposal(source="work", read=True)), json.dumps(proposal(title="团队日报汇总"))])
    monkeypatch.setattr(agent, "model_text", model)
    one, two, _ = stores
    first, second, third = production_service(one, turn_planner=agent.plan_turn), production_service(two, turn_planner=agent.plan_turn), None
    try:
        await first.handle(principal_message(text="读取我的Work云空间文档"))
        assert not first.jobs
        await first.close()
        await second.handle(principal_message(text="团队日报汇总", source="title-answer"))
        ticket = next(iter(second.jobs))
        second._api.assert_not_awaited()
        await second.close()
        api = AsyncMock(side_effect=[{"access_token": "synthetic-search-token", "expires_in": 7200, "scope": "search:docs:read"}, {"data": {"open_id": "ou-A"}}, {"data": {"res_units": [], "has_more": False}}])
        third = production_service(one, api, turn_planner=AsyncMock(side_effect=AssertionError("OAuth must not call the model")))
        started = await third.authorize(ticket, "")
        state = parse_qs(urlsplit(started["authorize_url"]).query)["state"][0]
        await third.callback(state=state, code="synthetic-code", browser_session=started["browser_session"])
        await third.drain()
        assert [call.args[0] for call in api.await_args_list] == ["token", "identity", "file_search"]
        assert api.await_args.kwargs["search_query"] == "团队日报汇总"
        assert model.await_count == 2
    finally:
        await first.close()
        await second.close()
        if third is not None:
            await third.close()


@pytest.mark.asyncio
async def test_new_complete_lookup_fences_old_oauth_before_token_exchange(stores, monkeypatch):  # noqa: F811
    monkeypatch.setenv("DEER_FLOW_WORK_FILE_SEARCH_ENABLED", "true")
    monkeypatch.setenv("DEER_FLOW_WORK_PRODUCTION_FILE_SEARCH_SCOPE", "search:docs:read")
    model = AsyncMock(side_effect=[json.dumps(proposal(source="work", read=True)), json.dumps(proposal(title="团队日报汇总")), json.dumps(proposal(source="work", title="新项目周报", read=True))])
    monkeypatch.setattr(agent, "model_text", model)
    one, two, _ = stores
    first, second = production_service(one, turn_planner=agent.plan_turn), production_service(two, turn_planner=agent.plan_turn)
    try:
        await first.handle(principal_message(text="读取我的Work云空间文档"))
        await second.handle(principal_message(text="团队日报汇总", source="title-answer"))
        old_ticket = next(iter(second.jobs))
        started = await second.authorize(old_ticket, "")
        state = parse_qs(urlsplit(started["authorize_url"]).query)["state"][0]
        await first.handle(principal_message(text="读取云空间的《新项目周报》", source="new-request"))
        old_ref, new_ref = second.jobs[old_ticket].task_ref, next(iter(first.jobs.values())).task_ref
        assert old_ref["task_id"] != new_ref["task_id"]
        with pytest.raises(WorkError, match="task_superseded|invalid_state_or_browser"):
            await second.callback(state=state, code="synthetic-code", browser_session=started["browser_session"])
        first._api.assert_not_awaited()
        second._api.assert_not_awaited()
    finally:
        await first.close()
        await second.close()

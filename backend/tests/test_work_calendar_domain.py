"""The calendar domain agent uses local tools, persisted patches and guardrails."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock

import pytest

from app.integrations.work import agent
from app.integrations.work.client import WorkError
from app.integrations.work.drafts import CROSS_DAY_QUESTION, TASK_RELATION_QUESTION, CalendarDraft, expand_create, missing_question

NEW = {"route": "new_work", "domain": "calendar"}
CONTINUE = {"route": "continue_work", "domain": "calendar"}
FINITE = {"operation": "create", "cardinality": "finite", "changes": {"title": "项目日常例会"}, "schedule": {"count": 3, "start_clock": "09:00", "duration_minutes": 180}}


def install(monkeypatch, *responses):
    model = AsyncMock(side_effect=[json.dumps(row, ensure_ascii=False) for row in responses])
    monkeypatch.setattr(agent, "model_text", model)
    return model


@pytest.mark.asyncio
async def test_partial_daily_reply_preserves_three_occurrences_and_only_asks_start_date(monkeypatch):
    old = CalendarDraft(operation="create", cardinality="finite", schedule={"count": 3})
    model = install(monkeypatch, CONTINUE, {"tool": "validate_draft", "patch": {"changes": FINITE["changes"], "schedule": {"start_clock": "09:00", "duration_minutes": 180}}}, {"tool": "ask_user"})
    result = await agent.plan_turn("每天的早上9点，项目日常例会，持续3小时", task_context={"domain": "calendar", "draft": old.model_dump(), "question": "请补充标题、日期、每天开始和持续多久"})
    assert result.route == "clarify" and result.draft.schedule.count == 3
    assert "具体日期" in result.question and "标题" not in result.question and "持续多久" not in result.question
    feedback = model.call_args_list[-1].args[1]["只读工具调用记录"][0]["result"]
    assert feedback["duration_minutes"] == 180 and feedback["cardinality"] == "finite"


@pytest.mark.asyncio
async def test_server_date_anchor_expands_three_days_and_feedback_lists_each_duration(monkeypatch):
    old = CalendarDraft.model_validate(FINITE)
    model = install(monkeypatch, CONTINUE, {"tool": "validate_draft", "start_day": "tomorrow", "patch": {"schedule": {"count": 3}}}, {"tool": "finish_draft"})
    result = await agent.plan_turn("从明天开始", task_context={"domain": "calendar", "draft": old.model_dump(), "question": missing_question(old)})
    assert result.route == "continue_work" and len(expand_create(result.draft)) == 3
    feedback = model.call_args_list[-1].args[1]["只读工具调用记录"][0]["result"]
    assert feedback["exact_item_count"] == 3
    assert [row["duration_minutes"] for row in feedback["items"]] == [180, 180, 180]
    tomorrow = model.call_args_list[0].args[1]["完整日期窗口（由服务端计算）"]["tomorrow"]["start"][:10]
    assert result.draft.schedule.start_date == tomorrow


@pytest.mark.asyncio
async def test_model_cannot_shortcut_calendar_domain_with_full_legacy_intent(monkeypatch):
    raw = {**NEW, "intent": {"operation": "calendar_create", "changes": {"title": "例会", "start": "2026-11-03T09:00:00+08:00", "end": "2026-11-05T12:00:00+08:00"}}}
    model = install(monkeypatch, raw, raw)
    with pytest.raises(WorkError, match="turn_model_invalid"):
        await agent.plan_turn("未来三天，每天9点开三小时会")
    assert model.await_count == 2


@pytest.mark.asyncio
async def test_finish_envelope_still_runs_complete_local_cardinality_validation(monkeypatch):
    patch = {**FINITE, "schedule": {**FINITE["schedule"], "start_date": "2026-11-03"}}
    original = agent._calendar_validation_feedback
    checked = []

    def validate(draft):
        feedback = original(draft)
        checked.append(feedback)
        return feedback

    monkeypatch.setattr(agent, "_calendar_validation_feedback", validate)
    model = install(monkeypatch, NEW, {"tool": "finish_draft", "patch": patch})
    result = await agent.plan_turn("连续三天每天9点到12点建项目日常例会")
    assert result.intent is None and len(expand_create(result.draft)) == 3 and model.await_count == 2
    assert checked[0]["exact_item_count"] == 3 and all(row["duration_minutes"] == 180 for row in checked[0]["items"])


@pytest.mark.asyncio
async def test_complete_local_validation_returns_before_any_unnecessary_model_replanning(monkeypatch):
    patch = {**FINITE, "schedule": {**FINITE["schedule"], "start_date": "2026-11-03"}}
    model = install(monkeypatch, NEW, {"tool": "validate_draft", "patch": patch}, {"tool": "finish_draft", "patch": {"schedule": {"duration_minutes": 120}}})
    result = await agent.plan_turn("建三天各三小时的例会")
    assert result.draft.schedule.duration_minutes == 180 and model.await_count == 2


@pytest.mark.asyncio
async def test_six_step_budget_never_confirms_span_when_model_keeps_omitting_choice(monkeypatch):
    model = install(monkeypatch, CONTINUE, *[{"tool": "validate_draft"}] * 6)
    result = await agent.plan_turn("暂时还没决定", task_context=cross_day_context())
    assert result.route == "clarify" and CROSS_DAY_QUESTION in result.question and model.await_count == 7
    assert not result.draft.continuous_span_confirmed
    with pytest.raises(ValueError):
        expand_create(result.draft)


@pytest.mark.asyncio
async def test_cross_day_single_span_gets_specific_question_then_conditional_confirmation(monkeypatch):
    patch = {"operation": "create", "changes": {"title": "长时间活动", "start": "2026-11-03T09:00:00+08:00", "end": "2026-11-05T12:00:00+08:00"}}
    install(monkeypatch, NEW, {"tool": "validate_draft", "patch": patch}, {"tool": "finish_draft"})
    result = await agent.plan_turn("安排11月3日9点到5日12点的活动")
    assert result.route == "clarify" and result.question == CROSS_DAY_QUESTION and not result.draft.continuous_span_confirmed
    with pytest.raises(ValueError):
        expand_create(result.draft)
    model = install(monkeypatch, CONTINUE, {"tool": "confirm_continuous_span", "confirmation_quote": "确实要一条连续跨日的活动"})
    confirmed = await agent.plan_turn("确实要一条连续跨日的活动", task_context={"domain": "calendar", "draft": result.draft.model_dump(), "question": result.question})
    assert confirmed.route == "continue_work" and confirmed.draft.continuous_span_confirmed and len(expand_create(confirmed.draft)) == 1
    tools = model.call_args_list[1].args[1]["工具目录"]
    assert "confirm_continuous_span" in {row["name"] for row in tools}
    feedback = model.call_args_list[1].args[1]["只读工具调用记录"][0]["result"]
    assert model.await_count == 2 and feedback["ready_for_finish"] and feedback["exact_item_count"] == 1 and feedback["items"][0]["duration_minutes"] == 3060


@pytest.mark.asyncio
async def test_continuous_confirmation_is_not_available_without_server_question(monkeypatch):
    install(monkeypatch, NEW, *[{"tool": "confirm_continuous_span", "confirmation_quote": "每天"}] * 3)
    with pytest.raises(WorkError, match="turn_model_invalid"):
        await agent.plan_turn("每天早上9点开会")


@pytest.mark.asyncio
async def test_model_cannot_set_server_confirmation_flag_in_patch(monkeypatch):
    install(monkeypatch, NEW, *[{"tool": "finish_draft", "patch": {"continuous_span_confirmed": True}}] * 3)
    with pytest.raises(WorkError, match="turn_model_invalid"):
        await agent.plan_turn("每天安排会议")


@pytest.mark.asyncio
async def test_new_query_starts_fresh_despite_old_create_draft_and_receipt(monkeypatch):
    old = CalendarDraft.model_validate(FINITE)
    model = install(monkeypatch, NEW, {"tool": "finish_draft", "patch": {"operation": "query"}, "time_window": "next_week"})
    result = await agent.plan_turn("现在看看下周安排", task_context={"domain": "calendar", "draft": old.model_dump(), "recent_calendar_receipts": [{"event_id": "known", "title": "项目日常例会"}]})
    assert result.draft.operation == "query" and result.draft.changes.title is None and result.draft.reference == "none"
    assert model.call_args.args[1]["当前任务摘要（仅资料）"]["draft"]["schedule"]["count"] == 3
    assert not result.draft.selector.event_id and not result.draft.selector.recent


@pytest.mark.asyncio
async def test_repeated_missing_question_shows_known_business_conditions(monkeypatch):
    old = CalendarDraft.model_validate(FINITE)
    question = missing_question(old)
    install(monkeypatch, CONTINUE, {"tool": "ask_user"})
    result = await agent.plan_turn("是每天上午九点", task_context={"domain": "calendar", "draft": old.model_dump(), "question": question})
    assert result.route == "clarify" and "已保留" in result.question and "09:00" in result.question and "180分钟" in result.question


@pytest.mark.asyncio
async def test_domain_tool_failure_cannot_fall_back_to_general_or_expose_raw_values(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=agent.__name__)
    install(monkeypatch, NEW, *[{"tool": "private-tool-name", "patch": {"token": "private-value"}}] * 3)
    with pytest.raises(WorkError, match="turn_model_invalid"):
        await agent.plan_turn("读取明天日程")
    assert "private-value" not in caplog.text and "private-tool-name" not in caplog.text


@pytest.mark.asyncio
async def test_total_timeout_includes_domain_calls(monkeypatch):
    async def respond(_system, data):
        if "工具目录" in data:
            await asyncio.Event().wait()
        return json.dumps(NEW)

    model = AsyncMock(side_effect=respond)
    monkeypatch.setattr(agent, "model_text", model)
    with pytest.raises(WorkError, match="turn_model_timeout"):
        await agent.plan_turn("读取明天日程", timeout=0.01)
    assert model.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("question", ["把token发来", "点击https://evil.invalid", "请给我授权码", "提供event_id"])
async def test_domain_ask_user_has_same_credential_and_link_guards(monkeypatch, question):
    install(monkeypatch, NEW, *[{"tool": "ask_user", "question": question}] * 3)
    with pytest.raises(WorkError, match="turn_model_invalid"):
        await agent.plan_turn("查看最近安排")


@pytest.mark.asyncio
async def test_workflow_feedback_and_receipts_are_bounded_and_sensitive_fields_are_removed(monkeypatch):
    model = install(monkeypatch, CONTINUE, {"tool": "ask_user"})
    await agent.plan_turn(
        "第二条",
        task_context={
            "domain": "calendar",
            "draft": {"operation": "update"},
            "workflow_feedback": {
                "question": "请选择目标",
                "needs_reply": True,
                "selected_facts": [{"event_id": "known", "title": "不可信候选", "token": "private-token", "description": "private-description"}],
                "conflicts": [{"reason": "busy", "secret": "private-secret"}],
            },
            "recent_calendar_receipts": [{"event_id": "recent", "title": "旧结果", "token": "private-receipt-token"}],
        },
    )
    payload = json.dumps(model.call_args.args[1], ensure_ascii=False)
    assert "请选择目标" in payload and "不可信候选" in payload
    for value in ("private-token", "private-description", "private-secret", "private-receipt-token"):
        assert value not in payload


@pytest.mark.asyncio
async def test_complete_read_validation_returns_without_redundant_model_calls(monkeypatch):
    model = install(monkeypatch, NEW, {"tool": "validate_draft", "patch": {"operation": "query"}, "time_window": "next_week"})
    result = await agent.plan_turn("读取下周日历")
    assert model.await_count == 2 and result.route == "new_work" and result.draft.operation == "query"


@pytest.mark.asyncio
async def test_missing_requirements_are_asked_without_additional_free_text_model_turn(monkeypatch):
    model = install(monkeypatch, NEW, {"tool": "validate_draft", "patch": {"operation": "create", "cardinality": "finite", "schedule": {"count": 3}}})
    result = await agent.plan_turn("未来三天安排会议")
    assert model.await_count == 2 and result.route == "clarify" and result.draft.schedule.count == 3
    assert "标题" in result.question and "具体日期" in result.question


@pytest.mark.asyncio
async def test_actual_misnested_start_day_is_rejected_then_repaired_without_losing_slots(monkeypatch):
    prior = CalendarDraft.model_validate(FINITE)
    model = install(
        monkeypatch,
        CONTINUE,
        {"tool": "ask_user", "patch": {"schedule": {"count": 3, "start_day": "tomorrow"}}},
        {"tool": "validate_draft", "start_day": "tomorrow"},
        {"tool": "finish_draft"},
    )
    result = await agent.plan_turn("从明天开始", task_context={"domain": "calendar", "draft": prior.model_dump(), "question": missing_question(prior)})
    assert len(expand_create(result.draft)) == 3 and result.draft.changes.title == "项目日常例会"
    assert "工具调用顶层" in model.call_args_list[2].args[1]["格式修复要求"]


def test_shared_tool_schema_does_not_repeat_defaults_or_allow_unknown_nested_fields():
    schema = agent._compact_schema(agent.CalendarToolCall.model_json_schema())
    assert "default" not in json.dumps(schema)
    assert all(row["input_schema"] == {"$ref": "#/工具调用schema"} for row in agent._calendar_tool_catalogue())
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        agent.CalendarToolCall.model_validate({"tool": "ask_user", "patch": {"schedule": {"start_day": "tomorrow"}}})


@pytest.mark.asyncio
async def test_actual_classifier_new_label_cannot_erase_pending_three_day_constraint(monkeypatch):
    prior = CalendarDraft(operation="create", cardinality="finite", schedule={"count": 3})
    install(monkeypatch, NEW, {"tool": "ask_user", "patch": {"operation": "create", "cardinality": "finite", "changes": FINITE["changes"], "schedule": {"start_clock": "09:00", "duration_minutes": 180}}})
    result = await agent.plan_turn("每天早上9点，项目日常例会，持续3小时", task_context={"domain": "calendar", "draft": prior.model_dump(), "question": missing_question(prior)})
    assert result.route == "clarify" and result.draft.schedule.count == 3
    assert result.draft.changes.title == "项目日常例会" and "标题" not in result.question and "持续多久" not in result.question
    install(monkeypatch, NEW, {"tool": "validate_draft", "start_day": "tomorrow"}, {"tool": "finish_draft"})
    ready = await agent.plan_turn("从明天开始", task_context={"domain": "calendar", "draft": result.draft.model_dump(), "question": result.question})
    assert ready.route == "continue_work" and len(expand_create(ready.draft)) == 3


@pytest.mark.asyncio
async def test_different_create_title_requires_one_business_choice_and_preserves_verified_draft(monkeypatch):
    prior = CalendarDraft.model_validate({**FINITE, "schedule": {**FINITE["schedule"], "start_date": "2026-11-03"}})
    install(monkeypatch, NEW, {"tool": "validate_draft", "patch": {"operation": "create", "changes": {"title": "另一场安排"}}})
    result = await agent.plan_turn("再安排一项标题另一场安排", task_context={"domain": "calendar", "draft": prior.model_dump(), "question": "请确认日期"})
    assert result.route == "clarify" and TASK_RELATION_QUESTION in result.question
    assert result.draft == prior and "标题" not in missing_question(result.draft)


@pytest.mark.asyncio
async def test_explicit_start_new_tool_starts_fresh_without_old_three_day_or_title(monkeypatch):
    prior = CalendarDraft.model_validate(FINITE)
    patch = {"operation": "create", "changes": {"title": "另一项", "start": "2026-11-03T11:00:00+08:00", "duration_minutes": 60}}
    install(monkeypatch, NEW, {"tool": "start_new_calendar_task", "reset_reason": "用户要求另建一个日程", "reset_quote": "另建一项", "patch": patch}, {"tool": "validate_draft"}, {"tool": "finish_draft"})
    result = await agent.plan_turn("另建一项，11月3日11点开始，标题另一项，一小时", task_context={"domain": "calendar", "draft": prior.model_dump(), "question": "原请求哪天开始？"})
    assert result.route == "new_work" and result.draft.schedule.count is None and result.draft.cardinality == "single"
    assert len(expand_create(result.draft)) == 1 and result.draft.changes.title == "另一项"
    assert result.draft.task_reset_reason and result.draft.task_reset_user_delta == "另建一项"


@pytest.mark.asyncio
@pytest.mark.parametrize("quote", ["", "用户并未说过的话"])
async def test_reset_tool_cannot_discard_existing_draft_without_current_evidence(monkeypatch, quote):
    prior = CalendarDraft.model_validate(FINITE)
    reset = {"tool": "start_new_calendar_task", "reset_reason": "new", "reset_quote": quote, "patch": {"operation": "create"}}
    install(monkeypatch, NEW, reset, reset, reset)
    with pytest.raises(WorkError, match="turn_model_invalid"):
        await agent.plan_turn("每天改成十点", task_context={"domain": "calendar", "draft": prior.model_dump()})


@pytest.mark.asyncio
async def test_task_relation_answer_can_amend_title_without_resetting_known_dates(monkeypatch):
    prior = CalendarDraft.model_validate({**FINITE, "schedule": {**FINITE["schedule"], "start_date": "2026-11-03"}})
    install(monkeypatch, CONTINUE, {"tool": "validate_draft", "confirmation_quote": "继续修改当前安排", "patch": {"changes": {"title": "更新的标题"}}}, {"tool": "finish_draft"})
    result = await agent.plan_turn("继续修改当前安排", task_context={"domain": "calendar", "draft": prior.model_dump(), "question": TASK_RELATION_QUESTION})
    assert result.route == "continue_work" and len(expand_create(result.draft)) == 3 and result.draft.changes.title == "更新的标题"


@pytest.mark.asyncio
async def test_query_then_explicit_candidate_edit_keeps_current_locator_context(monkeypatch):
    prior = CalendarDraft(operation="query", selector={"start": "2026-11-03T00:00:00+08:00", "end": "2026-11-04T00:00:00+08:00"})
    install(monkeypatch, NEW, {"tool": "validate_draft", "patch": {"operation": "update", "selector": {"candidate": 2}, "changes": {"title": "项目评审"}}})
    result = await agent.plan_turn("第二条改名为项目评审", task_context={"domain": "calendar", "draft": prior.model_dump(), "candidates": [{"event_id": "a"}, {"event_id": "b"}]})
    assert result.route == "continue_work" and result.draft.selector.candidate == 2 and result.draft.changes.title == "项目评审"


@pytest.mark.asyncio
async def test_completed_create_is_readonly_history_and_edit_uses_receipt_not_recreation(monkeypatch):
    prior = CalendarDraft.model_validate(FINITE)
    model = install(monkeypatch, NEW, {"tool": "validate_draft", "patch": {"changes": {"start": "2026-11-03T10:00:00+08:00", "duration_minutes": 180}}}, {"tool": "finish_draft"})
    result = await agent.plan_turn(
        "改成十点开始，时长三小时",
        task_context={"domain": "calendar", "draft": prior.model_dump(), "calendar_execution": {"state": "completed", "started": True, "complete": True, "events": [{"event_id": "completed-event", "title": "项目日常例会"}]}},
    )
    assert result.draft.operation == "repair" and result.draft.reference == "recent_receipt" and result.draft.schedule.count is None
    assert not result.calendar_task_reset_validated
    assert model.call_args_list[1].args[1]["当前草稿（只读资料）"]["operation"] == "repair"


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["started", "partial", "uncertain", "unknown"])
async def test_started_or_uncertain_requests_are_not_automatically_edited_or_copied(monkeypatch, state):
    prior = CalendarDraft.model_validate(FINITE)
    install(monkeypatch, CONTINUE, {"tool": "validate_draft", "patch": {"operation": "repair", "changes": {"title": "新名称"}}})
    result = await agent.plan_turn("刚才那条改名", task_context={"domain": "calendar", "draft": prior.model_dump(), "calendar_execution": {"state": state, "started": True}})
    assert result.route == "clarify" and result.draft.operation == "query" and "核对执行回执" in result.question


@pytest.mark.asyncio
async def test_old_audit_reset_reason_cannot_enable_recreation_without_this_turn_tool(monkeypatch):
    prior = CalendarDraft.model_validate({**FINITE, "task_reset_reason": "old", "task_reset_user_delta": "另建一条"})
    install(monkeypatch, NEW, {"tool": "validate_draft", "patch": {"operation": "create", "changes": {"title": "另一条"}}})
    result = await agent.plan_turn("另建一条", task_context={"domain": "calendar", "draft": prior.model_dump(), "calendar_execution": {"state": "completed", "started": True, "complete": True}})
    assert result.route == "clarify" and result.draft.operation != "create" and not result.calendar_task_reset_validated


@pytest.mark.asyncio
async def test_completed_task_allows_explicit_new_incomplete_task_with_runtime_reset_marker(monkeypatch):
    prior = CalendarDraft.model_validate(FINITE)
    install(monkeypatch, NEW, {"tool": "start_new_calendar_task", "reset_reason": "明确另建", "reset_quote": "另建一条", "patch": {"operation": "create", "changes": {"title": "另一条"}}})
    result = await agent.plan_turn("另建一条，标题另一条", task_context={"domain": "calendar", "draft": prior.model_dump(), "calendar_execution": {"state": "completed", "started": True, "complete": True}})
    assert result.route == "clarify" and result.draft.operation == "create" and result.calendar_task_reset_validated
    assert result.draft.schedule.count is None


@pytest.mark.asyncio
async def test_complete_query_does_not_reask_known_whole_day_for_missing_fields(monkeypatch):
    install(monkeypatch, NEW, {"tool": "ask_user", "patch": {"operation": "query"}, "time_window": "tomorrow", "question": "明天要查询全天还是某个时段？"})
    result = await agent.plan_turn("看看我明天有啥日程", task_context={"domain": "calendar", "draft": FINITE})
    assert result.route == "new_work" and result.draft.operation == "query" and not result.question


@pytest.mark.asyncio
async def test_real_business_scope_ambiguity_remains_a_question_after_date_is_known(monkeypatch):
    question = "当前只能查询个人日历，您是要查询本人安排吗？"
    install(
        monkeypatch,
        NEW,
        {
            "tool": "ask_user",
            "patch": {"operation": "query"},
            "time_window": "tomorrow",
            "question": question,
            "clarification_reason": "business_ambiguity",
            "ambiguity_reason": "用户同时提到部门整体安排，本人与部门范围不明确",
            "clarification_fields": ["scope"],
        },
    )
    result = await agent.plan_turn("帮我看看部门明天日程")
    assert result.route == "clarify" and result.question == question


@pytest.mark.asyncio
async def test_business_question_cannot_claim_known_date_is_still_missing(monkeypatch):
    invalid = {"tool": "ask_user", "patch": {"operation": "query"}, "time_window": "tomorrow", "question": "查哪天？", "clarification_reason": "business_ambiguity", "ambiguity_reason": "不确定日期", "clarification_fields": ["date_range"]}
    install(monkeypatch, NEW, invalid, {"tool": "validate_draft", "patch": {"operation": "query"}, "time_window": "tomorrow"})
    result = await agent.plan_turn("查看明天日程")
    assert result.route == "new_work" and not result.question


def cross_day_context():
    draft = CalendarDraft(operation="create", changes={"title": "长时间活动", "start": "2026-11-03T09:00:00+08:00", "end": "2026-11-05T12:00:00+08:00"})
    return {"domain": "calendar", "draft": draft.model_dump(), "question": CROSS_DAY_QUESTION}


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["我确认是一条连续跨日的长活动，不是每天分开", "一条就行，中间别分开", "连续跨日的日程"])
async def test_cross_day_choice_finishes_local_validation_without_extra_model_round(monkeypatch, answer):
    model = install(monkeypatch, CONTINUE, {"tool": "confirm_continuous_span", "confirmation_quote": answer})
    result = await agent.plan_turn(answer, task_context=cross_day_context())
    assert result.route == "continue_work" and result.draft.continuous_span_confirmed
    assert not result.question and len(expand_create(result.draft)) == 1 and model.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["validate_draft", "finish_draft", "ask_user"])
async def test_structured_span_choice_is_not_tied_to_specific_tool_or_sentence(monkeypatch, tool):
    answer = "一整条安排，期间不断开"
    model = install(monkeypatch, CONTINUE, {"tool": tool, "span_choice": "continuous", "confirmation_quote": answer, "patch": {"cardinality": "single", "changes": {"start": "2026-11-03T09:00:00+08:00"}}, "question": CROSS_DAY_QUESTION})
    result = await agent.plan_turn(answer, task_context=cross_day_context())
    assert result.route == "continue_work" and not result.question and result.draft.continuous_span_confirmed
    assert model.await_count == 2


@pytest.mark.asyncio
async def test_omitted_span_choice_gets_local_feedback_instead_of_reasking_user(monkeypatch):
    answer = "就是一整条，不要拆"
    model = install(monkeypatch, CONTINUE, {"tool": "ask_user", "question": CROSS_DAY_QUESTION}, {"tool": "validate_draft", "span_choice": "continuous", "confirmation_quote": answer})
    result = await agent.plan_turn(answer, task_context=cross_day_context())
    assert result.route == "continue_work" and result.draft.continuous_span_confirmed and model.await_count == 3
    assert model.call_args_list[2].args[1]["只读工具调用记录"][0]["result"]["required_next_tool"] == "choose_span"


@pytest.mark.asyncio
async def test_daily_choice_retains_original_dates_and_asks_only_daily_time_and_duration(monkeypatch):
    answer = "拆成每天一场吧"
    install(monkeypatch, CONTINUE, {"tool": "validate_draft", "span_choice": "daily", "confirmation_quote": answer})
    result = await agent.plan_turn(answer, task_context=cross_day_context())
    assert result.route == "clarify" and result.draft.cardinality == "finite"
    assert result.draft.schedule.start_date == "2026-11-03" and result.draft.schedule.count == 3
    assert "开始时间" in result.question and "持续多久" in result.question
    assert "标题" not in result.question and "具体日期" not in result.question and CROSS_DAY_QUESTION not in result.question


@pytest.mark.asyncio
async def test_daily_choice_with_explicit_daily_fields_validates_three_separate_intervals(monkeypatch):
    answer = "每天9点开始，三小时，拆成每天一场"
    install(monkeypatch, CONTINUE, {"tool": "ask_user", "span_choice": "daily", "confirmation_quote": answer, "patch": {"schedule": {"start_clock": "09:00", "duration_minutes": 180}}})
    result = await agent.plan_turn(answer, task_context=cross_day_context())
    actions = expand_create(result.draft)
    assert result.route == "continue_work" and len(actions) == 3
    assert all(row.changes.start[11:16] == "09:00" and row.changes.end[11:16] == "12:00" for row in actions)


@pytest.mark.asyncio
async def test_complete_finite_validation_needs_only_classifier_and_one_domain_call(monkeypatch):
    patch = {**FINITE, "schedule": {**FINITE["schedule"], "start_date": "2026-11-03"}}
    model = install(monkeypatch, NEW, {"tool": "validate_draft", "patch": patch})
    result = await agent.plan_turn("11月3日至5日每天9点项目日常例会，三小时")
    assert result.route == "new_work" and len(expand_create(result.draft)) == 3 and model.await_count == 2


@pytest.mark.asyncio
async def test_two_schema_repairs_recover_with_safe_precise_field_feedback(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=agent.__name__)
    model = install(
        monkeypatch,
        NEW,
        {"tool": "validate_draft", "patch": {"operation": "query", "selector": {"candidate": "private-invalid-value"}, "private_unknown": "private-value"}},
        {"tool": "validate_draft", "patch": {"operation": "query", "selector": {"candidate": "still-invalid"}}},
        {"tool": "validate_draft", "patch": {"operation": "query"}, "time_window": "tomorrow"},
    )
    result = await agent.plan_turn("读取明天日程")
    assert result.route == "new_work" and model.await_count == 4
    feedback = json.dumps(model.call_args_list[2].args[1]["本地校验错误"], ensure_ascii=False)
    assert "patch.selector.candidate" in feedback and "invalid_type" in feedback
    for value in ("private-invalid-value", "private_unknown", "private-value", "still-invalid"):
        assert value not in feedback and value not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", ["validate_draft", "finish_draft", "ask_user", "confirm_continuous_span"])
async def test_span_choice_needs_server_question_and_a_quote_from_current_message(monkeypatch, tool):
    call = {"tool": tool, "span_choice": "continuous", "confirmation_quote": "用户并未发送的确认"}
    install(monkeypatch, CONTINUE, call, call, call)
    with pytest.raises(WorkError, match="turn_model_invalid"):
        await agent.plan_turn("还是原来的安排", task_context=cross_day_context())
    context = cross_day_context()
    context["question"] = "请确认日期"
    call["confirmation_quote"] = "还是原来的安排"
    install(monkeypatch, CONTINUE, call, call, call)
    with pytest.raises(WorkError, match="turn_model_invalid"):
        await agent.plan_turn("还是原来的安排", task_context=context)


@pytest.mark.asyncio
async def test_continuous_choice_cannot_reconfirm_a_different_interval_in_the_same_call(monkeypatch):
    answer = "确定一整条，另外把结束推迟一天"
    invalid = {"tool": "validate_draft", "span_choice": "continuous", "confirmation_quote": answer, "patch": {"changes": {"end": "2026-11-06T12:00:00+08:00"}}}
    model = install(
        monkeypatch,
        CONTINUE,
        invalid,
        {
            "tool": "validate_draft",
            "patch": {"changes": {"end": "2026-11-06T12:00:00+08:00"}},
            "clarification_reason": "business_ambiguity",
            "ambiguity_reason": "更换了已补问的时间，需要重新核对安排规则",
            "clarification_fields": ["conflicting_requirements"],
            "question": CROSS_DAY_QUESTION,
        },
    )
    result = await agent.plan_turn(answer, task_context=cross_day_context())
    assert result.route == "clarify" and not result.draft.continuous_span_confirmed
    assert result.draft.changes.end == "2026-11-06T12:00:00+08:00" and model.await_count == 3


@pytest.mark.asyncio
async def test_already_confirmed_span_does_not_reask_known_choice_from_missing_fields_envelope(monkeypatch):
    context = cross_day_context()
    context["draft"]["continuous_span_confirmed"] = True
    model = install(monkeypatch, CONTINUE, {"tool": "ask_user", "patch": {"cardinality": "single", "changes": {"end": "2026-11-05T12:00:00+08:00"}}, "question": CROSS_DAY_QUESTION})
    result = await agent.plan_turn("维持刚才确定的一整条安排", task_context=context)
    assert result.route == "continue_work" and result.draft.continuous_span_confirmed and not result.question and model.await_count == 2


@pytest.mark.asyncio
async def test_daily_choice_without_current_evidence_does_not_replace_original_span(monkeypatch):
    invalid = {"tool": "ask_user", "span_choice": "daily"}
    install(monkeypatch, CONTINUE, invalid, invalid, invalid)
    with pytest.raises(WorkError, match="turn_model_invalid"):
        await agent.plan_turn("怎么安排都行", task_context=cross_day_context())


@pytest.mark.asyncio
async def test_invalid_complete_shape_is_not_accepted_by_local_finish_shortcut(monkeypatch):
    invalid = {"tool": "finish_draft", "patch": {"operation": "create", "changes": {"title": "活动", "start": "2026-11-03T12:00:00+08:00", "end": "2026-11-03T09:00:00+08:00"}}}
    install(monkeypatch, NEW, invalid)
    result = await agent.plan_turn("创建活动")
    assert result.route == "clarify" and result.question
    with pytest.raises(ValueError):
        expand_create(result.draft)


@pytest.mark.asyncio
async def test_daily_choice_excludes_midnight_end_day_from_the_original_span(monkeypatch):
    context = cross_day_context()
    context["draft"]["changes"].update(start="2026-11-10T09:00:00+08:00", end="2026-11-13T00:00:00+08:00")
    answer = "拆成每天独立，9点开始每场1小时"
    install(monkeypatch, CONTINUE, {"tool": "validate_draft", "span_choice": "daily", "confirmation_quote": answer, "patch": {"schedule": {"start_clock": "09:00", "duration_minutes": 60}}})
    result = await agent.plan_turn(answer, task_context=context)
    assert result.route == "continue_work"
    assert [row.changes.start[:10] for row in expand_create(result.draft)] == ["2026-11-10", "2026-11-11", "2026-11-12"]


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["continuous", "daily"])
async def test_validated_span_choice_discards_only_stale_server_choice_question(monkeypatch, choice):
    answer = "按我这条选择处理"
    call = {
        "tool": "ask_user",
        "span_choice": choice,
        "confirmation_quote": answer,
        "question": CROSS_DAY_QUESTION,
        "clarification_reason": "business_ambiguity",
        "ambiguity_reason": "还要确认连续还是逐日",
        "clarification_fields": ["conflicting_requirements"],
    }
    install(monkeypatch, CONTINUE, call)
    result = await agent.plan_turn(answer, task_context=cross_day_context())
    if choice == "continuous":
        assert result.route == "continue_work" and result.draft.continuous_span_confirmed and not result.question
    else:
        assert result.route == "clarify" and "每天的开始时间" in result.question and CROSS_DAY_QUESTION not in result.question


@pytest.mark.asyncio
async def test_span_choice_does_not_suppress_a_separate_real_business_scope_question(monkeypatch):
    answer = "整条连续的，不过我也要部门其他人的安排"
    question = "本次是只安排您个人的活动吗？部门整体安排尚未开放。"
    install(
        monkeypatch,
        CONTINUE,
        {
            "tool": "ask_user",
            "span_choice": "continuous",
            "confirmation_quote": answer,
            "question": question,
            "clarification_reason": "business_ambiguity",
            "ambiguity_reason": "用户同时要求部门安排，本人与部门范围不明确",
            "clarification_fields": ["scope"],
        },
    )
    result = await agent.plan_turn(answer, task_context=cross_day_context())
    assert result.route == "clarify" and result.question == question and result.draft.continuous_span_confirmed


@pytest.mark.asyncio
async def test_saved_real_span_question_shape_is_canonicalized_before_next_choice(monkeypatch):
    patch = cross_day_context()["draft"]
    patch = {"operation": "create", "cardinality": "single", "changes": patch["changes"]}
    install(
        monkeypatch,
        NEW,
        {
            "tool": "ask_user",
            "patch": patch,
            "question": "这是连续三天的长活动，还是每天独立安排？",
            "clarification_reason": "business_ambiguity",
            "ambiguity_reason": "用户尚未选择连续或逐日",
            "clarification_fields": ["scope", "conflicting_requirements"],
        },
    )
    initial = await agent.plan_turn("请安排11月3日9点到11月5日12点的长时间活动")
    assert initial.route == "clarify" and initial.question == CROSS_DAY_QUESTION
    answer = "我确认是一条连续跨日的长活动，不是每天分开"
    model = install(monkeypatch, CONTINUE, {"tool": "validate_draft", "patch": patch, "span_choice": "continuous", "confirmation_quote": answer})
    final = await agent.plan_turn(answer, task_context={"domain": "calendar", "draft": initial.draft.model_dump(), "question": initial.question})
    assert final.route == "continue_work" and final.draft.continuous_span_confirmed and model.await_count == 2


@pytest.mark.asyncio
async def test_saved_candidate_shift_shape_accepts_only_redundant_duration_metadata(monkeypatch):
    answer = "第二条往后挪半小时，时长不变"
    model = install(
        monkeypatch,
        CONTINUE,
        {"tool": "validate_draft", "patch": {"operation": "update", "selector": {"event_id": "synthetic-event-b", "candidate": 2}, "changes": {"shift_minutes": 30, "keep_duration": True}}, "confirmation_quote": answer},
    )
    context = {
        "domain": "calendar",
        "draft": {"operation": "query", "selector": {"start": "2026-11-04T00:00:00+08:00", "end": "2026-11-05T00:00:00+08:00"}},
        "candidates": [{"event_id": "synthetic-event-a"}, {"event_id": "synthetic-event-b"}],
    }
    result = await agent.plan_turn(answer, task_context=context)
    assert result.route == "continue_work" and result.draft.selector.candidate == 2 and result.draft.selector.event_id == "synthetic-event-b"
    assert result.draft.changes.shift_minutes == 30 and result.draft.changes.keep_duration is None and model.await_count == 2
    assert not result.draft.continuous_span_confirmed and not result.calendar_task_reset_validated


@pytest.mark.asyncio
async def test_non_authoritative_quote_still_must_come_from_current_message(monkeypatch):
    invalid = {"tool": "validate_draft", "patch": {"operation": "query"}, "time_window": "tomorrow", "confirmation_quote": "当前用户并未这样说"}
    install(monkeypatch, NEW, invalid, invalid, invalid)
    with pytest.raises(WorkError, match="turn_model_invalid"):
        await agent.plan_turn("读取明天日程")


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{"keep_duration": False}, {"keep_duration": True, "start": "2026-11-04T10:00:00+08:00"}, {"keep_duration": True, "end": "2026-11-04T11:00:00+08:00"}, {"keep_duration": True, "duration_minutes": 60}])
async def test_shift_normalization_never_drops_contradictory_or_absolute_time_fields(monkeypatch, extra):
    invalid = {"tool": "validate_draft", "patch": {"operation": "update", "selector": {"candidate": 2}, "changes": {"shift_minutes": 30, **extra}}}
    install(monkeypatch, CONTINUE, invalid, invalid, invalid)
    with pytest.raises(WorkError, match="turn_model_invalid"):
        await agent.plan_turn("第二条往后挪半小时", task_context={"domain": "calendar", "draft": {"operation": "query"}, "candidates": [{"event_id": "a"}, {"event_id": "b"}]})


@pytest.mark.asyncio
async def test_saved_recurring_request_uses_canonical_limit_without_reasking_known_operation(monkeypatch):
    model = install(
        monkeypatch,
        NEW,
        {
            "tool": "ask_user",
            "patch": {"operation": "create", "cardinality": "recurring", "changes": {"title": "项目例会"}, "schedule": {"start_clock": "09:00", "duration_minutes": 60}},
            "question": "周期未开放，请确认是否改为未来4周？",
            "clarification_reason": "business_ambiguity",
            "ambiguity_reason": "无限重复未开放",
            "clarification_fields": ["scope", "operation"],
        },
    )
    result = await agent.plan_turn("每周一上午9点建项目例会，持续一小时，一直重复")
    assert result.route == "clarify" and result.draft.cardinality == "recurring" and "周期日程尚未开放" in result.question and model.await_count == 2
    assert result.draft.changes.title == "项目例会" and result.draft.schedule.start_clock == "09:00" and result.draft.schedule.duration_minutes == 60
    assert not result.draft.schedule.start_date and result.draft.schedule.count is None
    with pytest.raises(ValueError):
        expand_create(result.draft)

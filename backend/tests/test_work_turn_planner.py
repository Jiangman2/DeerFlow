"""Semantic turn contract and failure boundaries; no real Work calls or writes."""

import asyncio
import json
import logging
from datetime import date, timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.integrations.work import agent
from app.integrations.work.calendar import CalendarIntent
from app.integrations.work.capabilities import Action
from app.integrations.work.client import WorkError
from app.integrations.work.drafts import CalendarDraft
from app.integrations.work.routing import WorkTurnDecision

DAY = {"start": "2026-11-03T00:00:00+08:00", "end": "2026-11-04T00:00:00+08:00"}
DOC = "doxKnownDocument123"


def query(**overrides):
    result = {"route": "new_work", "domain": "calendar", "intent": {"operation": "calendar_query", "selector": DAY}}
    result.update(overrides)
    return result


def calendar_tool(raw):
    intent = raw.get("intent", {"operation": "calendar_query"})
    patch = {"operation": intent["operation"].removeprefix("calendar_")}
    patch.update({name: intent[name] for name in ("selector", "changes") if name in intent})
    return {"tool": "finish_draft", "patch": patch, **{name: raw[name] for name in ("time_window", "target_clock") if name in raw}}


def calendar_model(raw):
    async def respond(_system, data):
        if "工具目录" in data:
            call = calendar_tool(raw)
            if call["patch"]["operation"] in {"create", "repair"} and not data["只读工具调用记录"]:
                call["tool"] = "validate_draft"
            return json.dumps(call)
        return json.dumps({"route": raw["route"], "domain": "calendar"})

    return AsyncMock(side_effect=respond)


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", ["查询我明天的日程", "读取我明天的日程", "瞧瞧我明天怎么安排的", "我下周都排了什么", "未来七天有啥事"])
async def test_all_natural_expressions_reach_one_semantic_planner(monkeypatch, utterance):
    model = calendar_model(query())
    monkeypatch.setattr(agent, "model_text", model)
    decision = await agent.plan_turn(utterance)
    assert decision.route == "new_work" and isinstance(decision.draft, CalendarDraft)
    assert model.await_count == 2
    # This tests absence of a lexical gate, not the real model's accuracy.
    assert model.call_args.args[1]["用户当前请求"] == utterance


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", ["换个话题，解释一下光合作用", "写一份会议纪要", "日历是怎么来的", "你是谁"])
async def test_ordinary_new_topic_is_not_captured_by_calendar_context(monkeypatch, utterance):
    model = AsyncMock(return_value='{"route":"general"}')
    monkeypatch.setattr(agent, "model_text", model)
    result = await agent.plan_turn(utterance, task_context={"domain": "calendar", "intent": query()["intent"], "question": "查哪一天？"})
    assert result.route == "general" and result.intent is None and not result.question
    assert model.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("utterance", ["下周", "半个小时", "第二条"])
async def test_short_replies_receive_bounded_current_task_facts(monkeypatch, utterance):
    raw = query(route="continue_work")
    model = calendar_model(raw)
    monkeypatch.setattr(agent, "model_text", model)
    context = {"domain": "calendar", "intent": query()["intent"], "question": "请选择时间范围", "pending_dialogue": [{"user": "看我的安排", "question": "哪段时间？"}]}
    result = await agent.plan_turn(utterance, task_context=context)
    assert result.route == "continue_work"
    assert model.call_args.args[1]["当前任务摘要（仅资料）"]["pending_dialogue"] == context["pending_dialogue"]


@pytest.mark.asyncio
async def test_complete_proposal_mislabeled_continue_without_task_starts_new_without_replanning(monkeypatch):
    raw = query(route="continue_work", intent={"operation": "calendar_update", "selector": {**DAY, "title": "技术交流"}, "changes": {"title": "项目会议"}})
    model = calendar_model(raw)
    monkeypatch.setattr(agent, "model_text", model)
    result = await agent.plan_turn("把明天的技术交流改名为项目会议")
    assert result.route == "new_work" and result.draft.changes.title == "项目会议"
    assert model.await_count == 2


@pytest.mark.asyncio
async def test_incomplete_continue_without_task_asks_instead_of_inventing_previous_state(monkeypatch):
    monkeypatch.setattr(agent, "model_text", calendar_model(query(route="continue_work", intent={"operation": "calendar_query"})))
    result = await agent.plan_turn("看看安排")
    assert result.route == "clarify" and result.question and not result.draft.selector.start


@pytest.mark.asyncio
async def test_missing_time_range_is_asked_before_oauth(monkeypatch):
    model = calendar_model(query(intent={"operation": "calendar_query"}))
    monkeypatch.setattr(agent, "model_text", model)
    result = await agent.plan_turn("帮我看看最近的行程")
    assert result.route == "clarify" and isinstance(result.draft, CalendarDraft)
    assert "哪段时间" in result.question and "授权" not in result.question
    assert model.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "intent,expected",
    [
        ({"operation": "calendar_create", "changes": {"title": "技术交流", "start": "2026-11-03T10:00:00+08:00"}}, "持续多久"),
        ({"operation": "calendar_update", "selector": DAY}, "具体变更"),
        ({"operation": "calendar_update", "selector": DAY, "changes": {"start": "2026-11-03T11:00:00+08:00"}}, "时长"),
        ({"operation": "calendar_query", "selector": {"start": "2026-11-03", "end": "2026-11-04"}}, "日期范围"),
    ],
)
async def test_missing_write_requirements_are_not_guessed(monkeypatch, intent, expected):
    monkeypatch.setattr(agent, "model_text", calendar_model(query(intent=intent)))
    result = await agent.plan_turn("帮我处理这个日程")
    assert result.route == "clarify" and expected in result.question


@pytest.mark.asyncio
async def test_current_task_cancellation_and_event_deletion_have_different_decisions(monkeypatch):
    model = AsyncMock(side_effect=['{"route":"cancel_current"}', '{"route":"unsupported","domain":"calendar","question":"当前未开放取消已创建日程；可先查询目标后到Work中取消。"}'])
    monkeypatch.setattr(agent, "model_text", model)
    cancel = await agent.plan_turn("算了，不查了", task_context={"domain": "calendar", "question": "查哪一天？"})
    delete = await agent.plan_turn("取消明天那场已经创建的会议")
    assert cancel.route == "cancel_current" and cancel.intent is None
    assert delete.route == "unsupported" and delete.intent is None


@pytest.mark.asyncio
async def test_candidate_choice_only_uses_current_principal_scoped_candidates(monkeypatch):
    raw = query(route="continue_work", intent={"operation": "calendar_update", "selector": {"candidate": 2}, "changes": {"title": "项目会议"}})
    monkeypatch.setattr(agent, "model_text", calendar_model(raw))
    result = await agent.plan_turn("第二条改名项目会议", task_context={"domain": "calendar", "candidates": [{"event_id": "first"}, {"event_id": "second"}]})
    assert result.draft.selector.candidate == 2
    with pytest.raises(WorkError, match="turn_model_invalid"):
        await agent.plan_turn("第二条改名项目会议", task_context={"domain": "calendar", "candidates": [{"event_id": "other"}]})


@pytest.mark.asyncio
@pytest.mark.parametrize("selector", [{"event_id": "foreign-event"}, {"recent": True}])
async def test_model_cannot_invent_event_id_or_recent_target(monkeypatch, selector):
    raw = query(intent={"operation": "calendar_query", "selector": selector})
    monkeypatch.setattr(agent, "model_text", calendar_model(raw))
    with pytest.raises(WorkError) as error:
        await agent.plan_turn("查一下这个安排", task_context={"domain": "calendar"})
    assert error.value.stage == "turn_model_invalid"


@pytest.mark.asyncio
async def test_model_cannot_change_document_target(monkeypatch):
    raw = {"route": "new_work", "domain": "document", "intent": {"operation": "document_read", "document_id": "doxForeignDocument"}}
    model = AsyncMock(return_value=json.dumps(raw))
    monkeypatch.setattr(agent, "model_text", model)
    with pytest.raises(WorkError) as error:
        await agent.plan_turn("读取这篇文档", links=[(DOC, "https://allowed.invalid/docx/" + DOC)])
    assert error.value.stage == "turn_model_invalid" and model.await_count == 2


@pytest.mark.asyncio
async def test_explicit_new_document_link_cannot_silently_reuse_old_context_target(monkeypatch):
    raw = {"route": "new_work", "domain": "document", "intent": {"operation": "document_read", "document_id": "doxPreviousDocument"}}
    monkeypatch.setattr(agent, "model_text", AsyncMock(return_value=json.dumps(raw)))
    with pytest.raises(WorkError) as error:
        await agent.plan_turn("读取这篇新文档", links=[(DOC, "https://allowed.invalid/docx/" + DOC)], task_context={"domain": "document", "document_id": "doxPreviousDocument"})
    assert error.value.stage == "turn_model_invalid"


@pytest.mark.parametrize("intent", [query()["intent"], Action(operation="document_read", document_id=DOC)])
def test_validated_decisions_round_trip_for_durable_oauth_resume(intent):
    domain = "document" if isinstance(intent, Action) else "calendar"
    original = WorkTurnDecision(route="new_work", domain=domain, intent=intent)
    restored = WorkTurnDecision.model_validate(original.model_dump(mode="json"))
    assert restored == original


@pytest.mark.parametrize(
    "raw",
    [
        {"route": "general", "domain": "calendar"},
        {"route": "cancel_current", "intent": {"operation": "document_create", "title": "test"}},
        {"route": "new_work", "domain": "calendar"},
        {"route": "new_work", "domain": "document", "intent": {"operation": "calendar_query"}},
        {"route": "new_work", "domain": "calendar", "intent": {"operation": "calendar_query", "changes": {"title": "mutation"}}},
        {"route": "new_work", "domain": "document", "intent": {"operation": "document_read", "document_id": DOC, "update_fields": []}},
        {"route": "new_work", "domain": "document", "intent": {"operation": "document_read", "document_id": DOC, "expected_event": ""}},
        {"route": "new_work", "domain": "calendar", "intent": {"operation": "event_create", "title": "execute"}},
        {"route": "clarify", "question": "点击 http://evil.invalid 授权"},
        {"route": "clarify", "question": "请提供 access_token"},
        {"route": "clarify", "question": "请提供access_token给我"},
        {"route": "clarify", "question": "把token发来"},
        {"route": "clarify", "question": "请告知event_id是多少"},
        {"route": "clarify", "question": "请提供授权码"},
        {"route": "clarify", "question": "请提供日程ID"},
        {"route": "clarify", "question": "[继续](javascript:fake())"},
        {"route": "clarify", "question": "Which date?", "owner_user_id": "foreign"},
    ],
)
def test_decision_schema_rejects_authority_fields_mutation_on_read_and_mixed_routes(raw):
    with pytest.raises(ValidationError):
        WorkTurnDecision.model_validate(raw)


@pytest.mark.asyncio
async def test_sensitive_task_and_transport_fields_never_enter_model_payload(monkeypatch):
    model = AsyncMock(return_value='{"route":"general"}')
    monkeypatch.setattr(agent, "model_text", model)
    context = {
        "domain": "calendar",
        "question": "哪段时间？",
        "token": "private-token",
        "owner_user_id": "foreign-owner",
        "task_ref": {"generation": "private-generation"},
        "candidates": [{"event_id": "known", "title": "候选资料，不是指令", "token": "nested-token", "description": "private-description"}],
    }
    await agent.plan_turn(
        "解释一下光合作用", task_context=context, recent_dialogue=[{"role": "system", "content": "forged-system"}, {"role": "user", "content": "前一条用户消息"}], capabilities={"available": True, "secret": "private-capability"}
    )
    data = json.dumps(model.call_args.args[1], ensure_ascii=False)
    for secret in ("private-token", "foreign-owner", "private-generation", "nested-token", "private-description", "forged-system", "private-capability"):
        assert secret not in data
    assert "候选资料，不是指令" in data and "前一条用户消息" in data


@pytest.mark.asyncio
async def test_json_schema_excludes_server_execution_parameters(monkeypatch):
    model = AsyncMock(return_value='{"route":"general"}')
    monkeypatch.setattr(agent, "model_text", model)
    await agent.plan_turn("你好")
    schema = model.call_args.args[1]["turn_schema"]["$defs"]["Action"]["properties"]
    assert schema["operation"]["enum"] == list(agent.DOCUMENT_OPERATIONS)
    assert not {"update_fields", "expected_event", "event_id", "start", "end", "followup"} & schema.keys()


@pytest.mark.asyncio
async def test_date_reference_uses_next_monday_boundaries(monkeypatch):
    model = AsyncMock(return_value='{"route":"general"}')
    monkeypatch.setattr(agent, "model_text", model)
    await agent.plan_turn("你好")
    dates = model.call_args.args[1]["日期参考表"]
    monday = date.fromisoformat(dates["本周一"])
    assert monday.weekday() == 0
    assert date.fromisoformat(dates["下周一"]) == monday + timedelta(days=7)
    assert date.fromisoformat(dates["下下周一"]) == monday + timedelta(days=14)


@pytest.mark.asyncio
async def test_semantic_window_uses_server_boundaries_even_when_model_arithmetic_is_wrong(monkeypatch):
    raw = query(time_window="next_7_days", intent={"operation": "calendar_query", "selector": {"start": "2026-11-02T00:00:00+08:00", "end": "2026-11-10T00:00:00+08:00"}})
    model = calendar_model(raw)
    monkeypatch.setattr(agent, "model_text", model)
    result = await agent.plan_turn("未来七天我有哪些安排？")
    reference = model.call_args.args[1]["完整日期窗口（由服务端计算）"]["next_7_days"]
    assert result.draft.selector.start == reference["start"]
    assert result.draft.selector.end == reference["end"]
    assert result.time_window is None


@pytest.mark.asyncio
async def test_semantic_window_does_not_require_model_to_repeat_dates(monkeypatch):
    raw = query(time_window="tomorrow", intent={"operation": "calendar_query"})
    monkeypatch.setattr(agent, "model_text", calendar_model(raw))
    result = await agent.plan_turn("读取我明天的日程")
    assert result.route == "new_work" and result.draft.selector.start and result.draft.selector.end


def test_semantic_whole_day_window_cannot_invent_an_exact_midnight_event_start():
    with pytest.raises(ValidationError):
        WorkTurnDecision.model_validate(query(time_window="tomorrow", intent={"operation": "calendar_query", "selector": {"starts_at": "2026-11-03T00:00:00+08:00"}}))


@pytest.mark.asyncio
async def test_schema_repair_explains_only_the_fixed_whole_window_conflict(monkeypatch):
    invalid = query(time_window="tomorrow", intent={"operation": "calendar_query", "selector": {"starts_at": "2026-11-03T00:00:00+08:00"}})
    model = AsyncMock(side_effect=['{"route":"new_work","domain":"calendar"}', json.dumps(calendar_tool(invalid)), json.dumps(calendar_tool(query(time_window="tomorrow", intent={"operation": "calendar_query"})))])
    monkeypatch.setattr(agent, "model_text", model)
    result = await agent.plan_turn("读取我明天的日程")
    assert result.route == "new_work" and not result.draft.selector.starts_at
    assert "仅给日期时清空starts_at" in model.call_args.args[1]["格式修复要求"]


@pytest.mark.asyncio
async def test_single_day_and_explicit_clock_are_combined_by_server_and_survive_oauth_snapshot(monkeypatch):
    raw = query(time_window="tomorrow", target_clock="10:00", intent={"operation": "calendar_update", "selector": {"title": "技术交流"}, "changes": {"title": "项目会议"}})
    model = calendar_model(raw)
    monkeypatch.setattr(agent, "model_text", model)
    result = await agent.plan_turn("明天十点的技术交流改名为项目会议")
    reference = model.call_args.args[1]["完整日期窗口（由服务端计算）"]["tomorrow"]
    assert result.draft.selector.starts_at == reference["start"][:10] + "T10:00:00+08:00"
    assert WorkTurnDecision.model_validate(result.model_dump(mode="json")) == result
    assert result.draft.changes.model_dump(exclude_none=True) == {"title": "项目会议"}


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["this_week", None])
async def test_clock_without_a_specific_day_requests_date_without_reading_private_data(monkeypatch, window):
    raw = query(time_window=window, target_clock="10:00", intent={"operation": "calendar_query"})
    monkeypatch.setattr(agent, "model_text", calendar_model(raw))
    result = await agent.plan_turn("查一下十点开始的日程")
    assert result.route == "clarify" and "哪一天" in result.question


@pytest.mark.parametrize("clock", ["25:00", "10:99", "十点", "10:00:00"])
def test_explicit_clock_has_a_bounded_unambiguous_format(clock):
    with pytest.raises(ValidationError):
        WorkTurnDecision.model_validate(query(time_window="tomorrow", target_clock=clock))


def test_clock_cannot_conflict_with_an_existing_specific_start():
    with pytest.raises(ValidationError):
        WorkTurnDecision.model_validate(query(time_window="tomorrow", target_clock="10:00", intent={"operation": "calendar_query", "selector": {"starts_at": "2026-11-03T14:00:00+08:00"}}))


@pytest.mark.asyncio
async def test_relative_shift_normalizes_redundant_true_without_weakening_shared_schema(monkeypatch):
    invalid = query(route="continue_work", intent={"operation": "calendar_update", "selector": {"candidate": 2}, "changes": {"shift_minutes": 30, "keep_duration": True}})
    with pytest.raises(ValidationError):
        CalendarIntent.model_validate(invalid["intent"])
    model = AsyncMock(side_effect=['{"route":"continue_work","domain":"calendar"}', json.dumps(calendar_tool(invalid))])
    monkeypatch.setattr(agent, "model_text", model)
    result = await agent.plan_turn("第二条往后挪半小时，时长保持不变", task_context={"domain": "calendar", "candidates": [{"event_id": "first"}, {"event_id": "second"}]})
    assert result.route == "continue_work" and result.draft.changes.model_dump(exclude_none=True) == {"shift_minutes": 30}
    assert model.await_count == 2 and "格式修复要求" not in model.call_args.args[1]


@pytest.mark.asyncio
async def test_schema_repair_is_bounded_and_does_not_echo_failed_output(monkeypatch, caplog):
    raw = {**query(), "private-unknown-field": "private-failed-output"}
    model = AsyncMock(side_effect=[json.dumps(raw), '{"route":"new_work","domain":"calendar"}', json.dumps(calendar_tool(query()))])
    monkeypatch.setattr(agent, "model_text", model)
    with caplog.at_level(logging.INFO, logger=agent.__name__):
        result = await agent.plan_turn("读取我的日程")
    assert result.route == "new_work" and model.await_count == 3
    assert "work_turn_validation" in caplog.text
    assert "private-unknown-field" not in caplog.text and "private-failed-output" not in caplog.text
    repair = model.call_args_list[1].args[1]["格式修复要求"]
    assert "private-unknown-field" not in repair and "private-failed-output" not in repair


@pytest.mark.asyncio
@pytest.mark.parametrize("result", ['{"route":"general","route":"new_work"}', "not-json"])
async def test_invalid_model_result_never_falls_back_to_general(monkeypatch, result):
    model = AsyncMock(return_value=result)
    monkeypatch.setattr(agent, "model_text", model)
    with pytest.raises(WorkError) as error:
        await agent.plan_turn("读取我的日程")
    assert error.value.stage == "turn_model_invalid" and model.await_count == 2


@pytest.mark.asyncio
async def test_model_timeout_is_separate_and_does_not_retry_or_fall_back(monkeypatch):
    async def wait(*_args):
        await asyncio.Event().wait()

    model = AsyncMock(side_effect=wait)
    monkeypatch.setattr(agent, "model_text", model)
    with pytest.raises(WorkError) as error:
        await agent.plan_turn("读取我的日程", timeout=0.01)
    assert error.value.stage == "turn_model_timeout" and model.await_count == 1


@pytest.mark.asyncio
async def test_cancellation_is_not_converted_to_unavailable(monkeypatch):
    monkeypatch.setattr(agent, "model_text", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await agent.plan_turn("读取我的日程")


@pytest.mark.asyncio
async def test_transport_failure_does_not_expose_exception_or_retry(monkeypatch, caplog):
    model = AsyncMock(side_effect=RuntimeError("private-provider-value"))
    monkeypatch.setattr(agent, "model_text", model)
    with pytest.raises(WorkError) as error:
        await agent.plan_turn("读取我的日程")
    assert error.value.stage == "turn_model_unavailable" and model.await_count == 1
    assert "private-provider-value" not in str(error.value) and "private-provider-value" not in caplog.text


@pytest.mark.asyncio
async def test_large_request_is_rejected_without_truncating_away_a_possible_negation(monkeypatch):
    model = AsyncMock(return_value='{"route":"general"}')
    monkeypatch.setattr(agent, "model_text", model)
    with pytest.raises(WorkError) as error:
        await agent.plan_turn("读取我的日程" + "x" * 12000 + "不要执行")
    assert error.value.stage == "turn_request_too_long"
    model.assert_not_awaited()

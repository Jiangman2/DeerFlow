"""Replay the user's UAT utterances through routing, task state and consent."""

import json
from unittest.mock import AsyncMock

import pytest
from test_work_semantic_service import make_service, message

from app.integrations.work import agent
from app.integrations.work.drafts import CROSS_DAY_QUESTION


@pytest.mark.asyncio
async def test_cross_day_answer_survives_service_grounding_and_requests_consent(monkeypatch):
    request = "请安排2026年11月10日9点到11月12日12点的活动，标题UAT测试-长活动"
    answer = "我确认是一条连续跨日的长活动，不是每天分开"
    changes = {"title": "UAT测试-长活动", "start": "2026-11-10T09:00:00+08:00", "end": "2026-11-12T12:00:00+08:00"}
    responses = [
        {"route": "new_work", "domain": "calendar"},
        {"tool": "validate_draft", "patch": {"operation": "create", "cardinality": "single", "changes": changes}},
        {"route": "continue_work", "domain": "calendar"},
        {"tool": "validate_draft", "span_choice": "continuous", "confirmation_quote": answer, "patch": {"cardinality": "single", "changes": changes}},
    ]
    model = AsyncMock(side_effect=[json.dumps(row, ensure_ascii=False) for row in responses])
    monkeypatch.setattr(agent, "model_text", model)
    service = make_service()
    service._turn_planner = agent.plan_turn
    try:
        assert await service.handle(message(request, "cross-day-initial"))
        assert CROSS_DAY_QUESTION in service.send.call_args.args[0].text
        assert not service.jobs
        assert await service.handle(message(answer, "cross-day-choice"))
        text = service.send.call_args.args[0].text
        assert "授权并继续" in text and CROSS_DAY_QUESTION not in text
        assert len(service.jobs) == 1 and model.await_count == 4
        draft = next(iter(service.jobs.values())).intent["draft"]
        assert draft["continuous_span_confirmed"] is True
        assert draft["changes"] == changes | {"text": None, "duration_minutes": None, "shift_minutes": None, "keep_duration": None}
        assert not service.tools.plans
        service._api.assert_not_awaited()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_complete_three_day_request_recovers_one_schema_error_without_extra_finish(monkeypatch):
    request = "请在2026年11月10日至12日每天9点创建日程，标题UAT测试-日常例会，每次3小时，不邀请其他人"
    bad = {"tool": "validate_draft", "patch": {"operation": "create", "changes": {"title": "UAT测试-日常例会"}, "schedule": {"start_date": "2026-11-10", "count": "three", "start_clock": "09:00", "duration_minutes": 180}}}
    corrected = {
        "tool": "validate_draft",
        "patch": {"operation": "create", "cardinality": "finite", "changes": {"title": "UAT测试-日常例会"}, "schedule": {"start_date": "2026-11-10", "count": 3, "start_clock": "09:00", "duration_minutes": 180}},
    }
    model = AsyncMock(side_effect=[json.dumps(row, ensure_ascii=False) for row in [{"route": "new_work", "domain": "calendar"}, bad, corrected]])
    monkeypatch.setattr(agent, "model_text", model)
    service = make_service()
    service._turn_planner = agent.plan_turn
    try:
        assert await service.handle(message(request, "three-complete-repaired"))
        assert "授权并继续" in service.send.call_args.args[0].text
        assert len(service.jobs) == 1 and model.await_count == 3
        repair_payload = model.call_args_list[2].args[1]
        assert {"field": "patch.schedule.count", "category": "invalid_type"} in repair_payload["本地校验错误"]["issues"]
        draft = agent.CalendarDraft.model_validate(next(iter(service.jobs.values())).intent["draft"])
        events = agent.expand_create(draft)
        assert [event.changes.start for event in events] == [f"2026-11-{day}T09:00:00+08:00" for day in (10, 11, 12)]
        assert [event.changes.end for event in events] == [f"2026-11-{day}T12:00:00+08:00" for day in (10, 11, 12)]
        assert not service.tools.plans
        service._api.assert_not_awaited()
    finally:
        await service.close()

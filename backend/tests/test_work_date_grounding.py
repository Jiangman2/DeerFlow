"""Date-source checks use only user anchors and server-owned task facts."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.integrations.work.date_grounding import ground_write_dates
from app.integrations.work.drafts import CalendarDraft, CalendarDraftPatch, expand_create, merge_draft, missing_question

NOW = datetime(2026, 11, 2, 12, tzinfo=ZoneInfo("Asia/Shanghai"))


def finite(day="2026-11-02"):
    return CalendarDraft(operation="create", cardinality="finite", changes={"title": "项目日常例会"}, schedule={"start_date": day, "count": 3, "start_clock": "09:00", "duration_minutes": 180})


def source(text, stamp=NOW):
    return {"text": text, "received_at": stamp}


@pytest.mark.parametrize("text", ["每天早上9点，项目日常例会，持续3小时", "未来三天每天9点开会", "接下来三天都有会议"])
def test_clock_and_count_do_not_silently_supply_today_as_start_day(text):
    result = ground_write_dates(None, finite(), [source(text)], NOW, {"same_task": False})
    assert result.question and result.draft.schedule.start_date == ""
    assert result.draft.schedule.count == 3 and result.draft.schedule.start_clock == "09:00" and result.draft.schedule.duration_minutes == 180
    assert result.draft.changes.title == "项目日常例会" and "2026-11-02" not in result.verified_days


@pytest.mark.parametrize(
    "text,day", [("从明天开始", "2026-11-03"), ("从今天开始", "2026-11-02"), ("从后天开始", "2026-11-04"), ("从下周二开始", "2026-11-10"), ("三天后开始", "2026-11-05"), ("2026年11月3日开始", "2026-11-03"), ("11月3日开始", "2026-11-03")]
)
def test_explicit_user_day_anchors_ground_the_start_and_program_expands_days(text, day):
    result = ground_write_dates(None, finite(day), [source(text)], NOW, {"same_task": False})
    assert not result.question and day in result.verified_days and len(expand_create(result.draft)) == 3


def test_user_day_is_remembered_even_if_model_did_not_put_it_in_partial_proposal():
    partial = CalendarDraft(operation="create", cardinality="finite", schedule={"count": 3})
    result = ground_write_dates(None, partial, [source("从2026年11月3日起连续三天")], NOW, {"same_task": False})
    assert "2026-11-03" in result.verified_days
    filled = ground_write_dates(partial, finite("2026-11-03"), [source("每天9点，三小时，项目日常例会")], NOW, {"same_task": True, "verified_days": result.verified_days})
    assert not filled.question and len(expand_create(filled.draft)) == 3


def test_new_task_does_not_reuse_old_verified_date_or_old_user_input():
    result = ground_write_dates(finite("2026-11-03"), finite("2026-11-03"), [source("从2026年11月3日开始", NOW - timedelta(minutes=1)), source("另建每天9点的例会三小时")], NOW, {"same_task": False, "verified_days": ["2026-11-03"]})
    assert result.question and not result.draft.schedule.start_date and not result.verified_days


@pytest.mark.parametrize("row", [{"question": "您想从2026年11月3日开始吗？"}, {"role": "assistant", "text": "2026年11月3日开始", "received_at": NOW}, {"user": "从明天开始", "received_at": None}])
def test_assistant_questions_and_undated_historical_relative_words_are_not_day_sources(row):
    result = ground_write_dates(finite("2026-11-03"), finite("2026-11-03"), [row], NOW, {"same_task": True})
    assert result.question and not result.draft.schedule.start_date


def test_historical_absolute_user_text_is_a_source_without_reinterpreting_today():
    result = ground_write_dates(None, finite("2026-11-03"), [{"user": "2026年11月3日开始", "question": "从2026年12月1日吗？"}], NOW, {"same_task": True})
    assert not result.question and "2026-12-01" not in result.verified_days


def test_historical_relative_day_uses_its_original_received_at():
    result = ground_write_dates(None, finite("2026-11-02"), [source("从明天开始", NOW - timedelta(days=1))], NOW, {"same_task": True})
    assert not result.question


@pytest.mark.parametrize("text", ["2026-11-03至2026-11-05", "2026年11月3日至5日"])
def test_explicit_range_grounds_each_listed_day(text):
    draft = CalendarDraft(operation="create", cardinality="finite", changes={"title": "例会"}, schedule={"dates": ["2026-11-03", "2026-11-04", "2026-11-05"], "start_clock": "09:00", "duration_minutes": 180})
    assert not ground_write_dates(None, draft, [source(text)], NOW).question


def test_clock_only_update_can_use_the_unique_explicitly_selected_live_event_day():
    draft = CalendarDraft(operation="update", selector={"event_id": "known"}, changes={"start": "2026-11-03T10:00:00+08:00", "duration_minutes": 180})
    result = ground_write_dates(None, draft, [source("改成十点，持续三小时")], NOW, {"candidates": [{"event_id": "known", "start": "2026-11-03T09:00:00+08:00", "end": "2026-11-03T12:00:00+08:00"}]})
    assert not result.question and result.draft.changes.start == draft.changes.start


def test_multiple_unselected_target_days_do_not_authorize_guessing_a_day():
    draft = CalendarDraft(operation="update", changes={"start": "2026-11-03T10:00:00+08:00", "duration_minutes": 180})
    result = ground_write_dates(None, draft, [source("改成十点，持续三小时")], NOW, {"candidates": [{"event_id": "a", "start": "2026-11-03T09:00:00+08:00"}, {"event_id": "b", "start": "2026-11-04T09:00:00+08:00"}]})
    assert result.question and not result.draft.changes.start and result.draft.schedule.start_clock == "10:00"


def test_finite_repair_does_not_implicitly_copy_the_original_receipt_start_day():
    draft = finite("2026-11-03").model_copy(update={"operation": "repair", "reference": "recent_receipt"})
    result = ground_write_dates(None, draft, [source("分成未来三天每天9点到12点")], NOW, {"receipt_events": [{"event_id": "a", "start": "2026-11-03T09:00:00+08:00", "end": "2026-11-05T12:00:00+08:00"}]})
    assert result.question and not result.draft.schedule.start_date and result.draft.schedule.count == 3


def test_query_selector_dates_are_unchanged_by_write_day_grounding():
    draft = CalendarDraft(operation="query", selector={"start": "2026-11-03T00:00:00+08:00", "end": "2026-11-04T00:00:00+08:00"})
    result = ground_write_dates(None, draft, [source("看看安排")], NOW)
    assert result.draft == draft and not result.question


def test_unverified_single_interval_preserves_title_clock_and_duration_but_no_day():
    draft = CalendarDraft(operation="create", changes={"title": "例会", "start": "2026-11-02T09:00:00+08:00", "end": "2026-11-02T12:00:00+08:00"})
    result = ground_write_dates(None, draft, [source("九点开始，持续三小时，标题例会")], NOW)
    assert result.question and not result.draft.changes.start and not result.draft.changes.end
    assert result.draft.changes.title == "例会" and result.draft.schedule.start_clock == "09:00" and result.draft.schedule.duration_minutes == 180


def test_bounded_natural_overnight_end_can_be_derived_from_grounded_start():
    draft = CalendarDraft(operation="create", changes={"title": "夜间工作", "start": "2026-11-03T23:00:00+08:00", "end": "2026-11-04T02:00:00+08:00"})
    result = ground_write_dates(None, draft, [source("明天23点开始，持续三小时")], NOW)
    assert not result.question and "2026-11-04" in result.verified_days


def test_longer_span_requires_both_days_in_user_sources():
    draft = CalendarDraft(operation="create", changes={"title": "长活动", "start": "2026-11-03T09:00:00+08:00", "end": "2026-11-05T12:00:00+08:00"})
    assert not ground_write_dates(None, draft, [source("2026年11月3日9点至11月5日12点")], NOW).question
    assert ground_write_dates(None, draft, [source("明天9点开始")], NOW).question


def test_explicit_negated_day_is_not_used_as_a_positive_anchor():
    assert ground_write_dates(None, finite("2026-11-03"), [source("不是明天，而是后天")], NOW).question
    assert not ground_write_dates(None, finite("2026-11-04"), [source("不是明天，而是后天")], NOW).question


def test_saved_round4_single_patch_null_count_canonicalizes_to_exactly_one():
    prior = finite("2026-11-03")
    patch = {
        "cardinality": "single",
        "cardinality_change_reason": "用户明确从多条改为一条",
        "cardinality_change_user_delta": "现在改成只建一条",
        "schedule": {"count": None, "start_clock": "09:00", "duration_minutes": 180, "start_date": "2026-11-03"},
    }
    result = merge_draft(prior, patch, user_request="现在改成只建一条，2026年11月3日9点开始，持续3小时，标题保持")
    assert result.schedule.count == 1 and len(expand_create(result)) == 1


def test_long_span_with_unverified_day_retains_server_clock_without_becoming_ready():
    draft = CalendarDraft(operation="create", changes={"title": "长活动", "start": "2026-11-03T09:00:00+08:00", "duration_minutes": 3060})
    result = ground_write_dates(None, draft, [source("9点开始，持续51小时")], NOW)
    assert result.question and not result.draft.changes.start and result.draft.pending_start_clock == "09:00"
    assert result.draft.changes.duration_minutes == 3060 and missing_question(result.draft)
    with pytest.raises(ValidationError):
        CalendarDraftPatch.model_validate({"pending_start_clock": "10:00"})
    dated = result.draft.model_copy(update={"changes": result.draft.changes.model_copy(update={"start": "2026-11-03T09:00:00+08:00"})})
    grounded = ground_write_dates(result.draft, dated, [source("2026年11月3日开始")], NOW, {"same_task": True})
    assert not grounded.question and not grounded.draft.pending_start_clock


def test_target_end_day_cannot_authorize_moving_the_start_to_a_different_day():
    draft = CalendarDraft(operation="update", selector={"event_id": "overnight"}, changes={"start": "2026-11-04T10:00:00+08:00", "duration_minutes": 60})
    result = ground_write_dates(None, draft, [source("改成十点开始")], NOW, {"candidates": [{"event_id": "overnight", "start": "2026-11-03T23:00:00+08:00", "end": "2026-11-04T02:00:00+08:00"}]})
    assert result.question and not result.draft.changes.start


@pytest.mark.parametrize("day", ["2026-01-01", "2027-01-01"])
def test_bare_past_month_day_does_not_guess_this_or_next_year(day):
    assert ground_write_dates(None, finite(day), [source("从1月1日开始")], NOW).question


def test_explicit_year_allows_a_past_month_day_without_year_guessing():
    assert not ground_write_dates(None, finite("2026-01-01"), [source("从今年1月1日开始")], NOW).question

"""Business invariants for calendar slot patches and finite occurrences."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from app.integrations.work.drafts import CROSS_DAY_QUESTION, CalendarDraft, CalendarDraftPatch, expand_create, merge_draft, missing_question
from app.integrations.work.routing import WorkTurnDecision


def finite(**extra):
    value = {"operation": "create", "cardinality": "finite", "changes": {"title": "项目日常例会"}, "schedule": {"start_date": "2026-11-03", "count": 3, "start_clock": "09:00", "duration_minutes": 180}}
    value.update(extra)
    return CalendarDraft.model_validate(value)


def test_three_daily_meetings_are_three_three_hour_proposals_not_one_51_hour_event():
    draft = finite()
    assert not missing_question(draft)
    events = expand_create(draft)
    assert len(events) == 3
    assert [event.changes.start[:10] for event in events] == ["2026-11-03", "2026-11-04", "2026-11-05"]
    for event in events:
        assert event.operation == "calendar_create"
        assert event.changes.title == "项目日常例会"
        assert event.changes.start[11:16] == "09:00" and event.changes.end[11:16] == "12:00"
        assert (datetime.fromisoformat(event.changes.end) - datetime.fromisoformat(event.changes.start)).total_seconds() == 10800


def test_omitted_slots_survive_several_partial_replies_and_explicit_null_clears():
    draft = merge_draft(None, {"operation": "create", "cardinality": "finite", "schedule": {"count": 3}})
    question = missing_question(draft)
    assert "标题" in question and "日期" in question and "开始时间" in question and "持续多久" in question
    draft = merge_draft(draft, {"changes": {"title": "项目日常例会"}, "schedule": {"start_clock": "09:00", "duration_minutes": 180}})
    question = missing_question(draft)
    assert draft.schedule.count == 3 and draft.changes.title == "项目日常例会"
    assert "具体日期" in question and "标题" not in question and "持续多久" not in question
    draft = merge_draft(draft, {"schedule": {"start_date": "2026-11-03"}})
    assert not missing_question(draft) and len(expand_create(draft)) == 3
    cleared = merge_draft(draft, {"schedule": {"duration_minutes": None}})
    assert cleared.schedule.duration_minutes is None and cleared.schedule.count == 3
    assert "持续多久" in missing_question(cleared)


def test_patch_exclude_unset_retains_explicit_null_and_does_not_emit_defaults():
    assert CalendarDraftPatch.model_validate({"changes": {"title": None}}).model_dump(exclude_unset=True) == {"changes": {"title": None}}


@pytest.mark.parametrize("field", ["token", "app_secret", "owner_user_id", "method", "endpoint", "task_ref", "confirmed", "event_ids"])
def test_drafts_and_patches_reject_authority_and_transport_fields(field):
    for cls in (CalendarDraft, CalendarDraftPatch):
        with pytest.raises(ValidationError):
            cls.model_validate({field: "untrusted"})


@pytest.mark.parametrize("changes", [{"start": "2026-11-03T09:00:00+08:00", "end": "2026-11-05T12:00:00+08:00"}, {"duration_minutes": 3060}])
@pytest.mark.parametrize("operation", ["create", "repair"])
def test_finite_requests_cannot_disguise_multiple_occurrences_as_one_interval(changes, operation):
    with pytest.raises(ValidationError, match="finite_occurrences_require_schedule"):
        finite(operation=operation, changes=changes)


def test_single_event_cannot_silently_carry_multiple_dates():
    with pytest.raises(ValidationError, match="single_event_cannot_drop_multiple_dates"):
        finite(cardinality="single")


def test_cardinality_downgrade_requires_explicit_current_user_evidence_and_cleared_schedule():
    draft = finite()
    with pytest.raises(ValueError, match="cardinality_downgrade_requires_current_user_evidence"):
        merge_draft(draft, {"cardinality": "single"})
    with pytest.raises(ValueError, match="cardinality_downgrade_requires_current_user_evidence"):
        merge_draft(draft, {"cardinality": "single", "cardinality_change_reason": "changed", "cardinality_change_user_delta": "改成一条"}, user_request="时间改成十点")
    changed = merge_draft(
        draft,
        {"cardinality": "single", "schedule": None, "changes": {"start": "2026-11-03T09:00:00+08:00", "duration_minutes": 180}, "cardinality_change_reason": "用户把三条改为一条", "cardinality_change_user_delta": "改成一条"},
        user_request="现在改成一条",
    )
    assert changed.cardinality == "single" and len(expand_create(changed)) == 1


def test_query_cannot_keep_previous_create_mutations_without_explicit_clear():
    draft = CalendarDraft(operation="create", changes={"title": "例会"})
    with pytest.raises(ValueError, match="calendar_query_cannot_mutate"):
        merge_draft(draft, {"operation": "query"})
    query = merge_draft(draft, {"operation": "query", "changes": None, "selector": {"start": "2026-11-03T00:00:00+08:00", "end": "2026-11-04T00:00:00+08:00"}})
    assert not missing_question(query)


def test_repair_keeps_receipt_reference_and_allows_live_title_to_be_resolved_later():
    draft = finite(operation="repair", changes={}, reference="recent_receipt")
    assert not missing_question(draft)
    with pytest.raises(ValueError, match="not_ready"):
        expand_create(draft)


def test_delete_and_recurring_requests_remain_unexecutable_with_useful_next_steps():
    for draft in (CalendarDraft(operation="delete"), finite(cardinality="recurring")):
        assert "尚未开放" in missing_question(draft)
        with pytest.raises(ValueError):
            expand_create(draft)


@pytest.mark.parametrize(
    "schedule",
    [{"dates": ["2026-11-03", "2026-11-03"]}, {"dates": ["2026-02-30"]}, {"dates": ["2026-11-03"], "count": 3}, {"start_date": "2026-11-3"}, {"count": 32}, {"start_clock": "25:00"}, {"duration_minutes": 1441}],
)
def test_invalid_conflicting_or_unbounded_schedule_is_rejected(schedule):
    with pytest.raises(ValidationError):
        finite(schedule=schedule)


def test_unordered_explicit_dates_expand_in_stable_chronological_order():
    draft = finite(schedule={"dates": ["2026-11-05", "2026-11-03"], "count": 2, "start_clock": "09:00", "duration_minutes": 180})
    assert [event.changes.start[:10] for event in expand_create(draft)] == ["2026-11-03", "2026-11-05"]


def test_single_schedule_and_legacy_interval_are_both_supported():
    scheduled = finite(cardinality="single", schedule={"dates": ["2026-11-03"], "start_clock": "09:00", "duration_minutes": 180})
    assert not missing_question(scheduled) and len(expand_create(scheduled)) == 1
    legacy = CalendarDraft(operation="create", changes={"title": "例会", "start": "2026-11-03T09:00:00+08:00", "duration_minutes": 180})
    assert not missing_question(legacy) and len(expand_create(legacy)) == 1


def test_more_than_ten_occurrences_is_preserved_and_asks_user_to_choose_a_batch():
    draft = finite(schedule={"start_date": "2026-11-03", "count": 12, "start_clock": "09:00", "duration_minutes": 180})
    assert draft.schedule.count == 12
    assert "最多处理10条" in missing_question(draft) and "日期" in missing_question(draft)
    with pytest.raises(ValueError):
        expand_create(draft)


def test_continuous_confirmation_is_cleared_by_time_or_cardinality_changes():
    draft = CalendarDraft(operation="create", changes={"title": "活动", "start": "2026-11-03T09:00:00+08:00", "end": "2026-11-05T12:00:00+08:00"}, continuous_span_confirmed=True)
    assert not missing_question(draft)
    changed = merge_draft(draft, {"changes": {"end": "2026-11-06T12:00:00+08:00"}})
    assert not changed.continuous_span_confirmed and missing_question(changed) == CROSS_DAY_QUESTION
    assert merge_draft(draft, {"changes": {"title": "活动新名称"}}).continuous_span_confirmed


@pytest.mark.parametrize(
    "patch",
    [
        {"cardinality": "single"},
        {"schedule": {}},
        {"changes": {"start": "2026-11-03T09:00:00+08:00", "end": "2026-11-05T12:00:00+08:00"}},
        {"changes": {"start": "2026-11-03T01:00:00+00:00"}},
        {"changes": {"end": None, "duration_minutes": 3060}},
    ],
)
def test_repeating_equivalent_span_fields_preserves_server_confirmation(patch):
    draft = CalendarDraft(operation="create", changes={"title": "活动", "start": "2026-11-03T09:00:00+08:00", "end": "2026-11-05T12:00:00+08:00"}, continuous_span_confirmed=True)
    result = merge_draft(draft, patch)
    assert result.continuous_span_confirmed and not missing_question(result)


def test_real_span_start_change_revokes_confirmation_even_when_duration_is_unchanged():
    draft = CalendarDraft(operation="create", changes={"title": "活动", "start": "2026-11-03T09:00:00+08:00", "end": "2026-11-05T12:00:00+08:00"}, continuous_span_confirmed=True)
    result = merge_draft(draft, {"changes": {"start": "2026-11-03T10:00:00+08:00", "end": "2026-11-05T13:00:00+08:00"}})
    assert not result.continuous_span_confirmed and missing_question(result) == CROSS_DAY_QUESTION


def test_schedule_and_absolute_interval_cannot_silently_disagree():
    with pytest.raises(ValidationError, match="calendar_schedule_cannot_mix_absolute_interval"):
        CalendarDraft(operation="create", changes={"title": "会议", "start": "2026-11-03T09:00:00+08:00"}, schedule={"dates": ["2026-11-04"], "start_clock": "11:00", "duration_minutes": 60})


def test_relative_edit_fields_cannot_be_silently_ignored_by_creation():
    with pytest.raises(ValidationError, match="creation_requires_explicit_times_not_relative_edit"):
        CalendarDraft(operation="create", changes={"title": "会议", "start": "2026-11-03T09:00:00+08:00", "duration_minutes": 60, "keep_duration": True})


def test_single_scheduled_repair_can_resolve_title_and_target_from_receipt():
    draft = CalendarDraft(operation="repair", reference="recent_receipt", schedule={"dates": ["2026-11-04"], "start_clock": "11:00", "duration_minutes": 60})
    assert not missing_question(draft)


@pytest.mark.parametrize("schedule", [{"count": 1}, {"start_date": None}, None, {"count": None, "start_date": None, "dates": ["2026-11-03"]}])
def test_finite_item_reduction_or_lost_dates_requires_current_user_evidence(schedule):
    with pytest.raises(ValueError, match="finite_constraints_cannot_be_silently_reduced"):
        merge_draft(finite(), {"schedule": schedule}, user_request="每天改成十点")


def test_equivalent_explicit_dates_and_counted_days_do_not_require_new_authorization():
    original = finite()
    explicit = merge_draft(original, {"schedule": {"dates": ["2026-11-03", "2026-11-04", "2026-11-05"], "start_date": None, "count": None}})
    counted = merge_draft(explicit, {"schedule": {"dates": None, "start_date": "2026-11-03", "count": 3}})
    assert [event.changes.start for event in expand_create(counted)] == [event.changes.start for event in expand_create(original)]


def test_user_can_explicitly_select_smaller_batch_without_erasing_other_slots():
    original = finite(schedule={"start_date": "2026-11-03", "count": 12, "start_clock": "09:00", "duration_minutes": 180})
    selected = merge_draft(original, {"schedule": {"count": 3}, "cardinality_change_reason": "用户明确选择本批前三天", "cardinality_change_user_delta": "本次先做前三天"}, user_request="那本次先做前三天")
    assert len(expand_create(selected)) == 3 and selected.schedule.start_clock == "09:00"


@pytest.mark.parametrize("schedule", [{"start_date": "9999-12-31", "count": 2}, {"dates": ["9999-12-31"], "start_clock": "23:00", "duration_minutes": 180}])
def test_schedule_expansion_rejects_calendar_overflow_before_planning(schedule):
    with pytest.raises(ValidationError, match="outside_supported_date_range"):
        CalendarDraft(operation="create", cardinality="finite", schedule=schedule)


def test_verified_single_day_choice_normalizes_obsolete_count_without_guessing_first_day():
    patch = {"cardinality": "single", "cardinality_change_reason": "用户明确只保留一条", "cardinality_change_user_delta": "只保留11月4日", "schedule": {"start_date": "2026-11-04", "start_clock": "09:00", "duration_minutes": 180}}
    single = merge_draft(finite(), patch, user_request="只保留11月4日")
    assert single.schedule.count == 1 and [x.changes.start[:10] for x in expand_create(single)] == ["2026-11-04"]
    patch["schedule"].pop("start_date")
    with pytest.raises(ValueError, match="single_occurrence_requires_explicit_target_date"):
        merge_draft(finite(), patch, user_request="只保留11月4日")


def test_runtime_reset_marker_is_never_model_input_or_oauth_payload():
    decision = WorkTurnDecision(route="new_work", domain="calendar", draft=finite())
    decision._calendar_task_reset = True
    assert decision.calendar_task_reset_validated and decision.model_copy().calendar_task_reset_validated
    dumped = decision.model_dump(mode="json")
    assert "_calendar_task_reset" not in dumped and "calendar_task_reset_validated" not in dumped
    assert not WorkTurnDecision.model_validate(dumped).calendar_task_reset_validated
    for key in ("_calendar_task_reset", "calendar_task_reset_validated"):
        with pytest.raises(ValidationError):
            WorkTurnDecision.model_validate({**dumped, key: True})


def test_versioned_draft_turn_round_trips_without_granting_execution():
    original = WorkTurnDecision(route="new_work", domain="calendar", draft=finite())
    restored = WorkTurnDecision.model_validate(original.model_dump(mode="json"))
    assert restored == original and restored.intent is None
    with pytest.raises(ValidationError):
        WorkTurnDecision.model_validate({**original.model_dump(mode="json"), "intent": {"operation": "calendar_query"}})

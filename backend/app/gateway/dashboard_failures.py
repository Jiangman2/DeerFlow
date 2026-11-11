"""Scalar, page-bounded failed-run projections; never deserialize run payloads."""

from sqlalchemy import desc, func, or_, select

from deerflow.persistence.models.run_event import RunEventRow
from deerflow.persistence.observability.model import OperationDispositionRow
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.persistence.user.model import UserRow

ERROR_PREVIEW_CHARS = 4096
FAILURE_WINDOW = 1000


def failed_run_statements(query: str, limit: int, offset: int):
    failed = or_(RunRow.status.in_(("error", "timeout", "interrupted")), RunRow.error.is_not(None))
    # Bound history before identity search/count; never load full error payloads here.
    recent = (
        select(
            RunRow.run_id,
            RunRow.thread_id,
            RunRow.user_id,
            RunRow.created_at,
            RunRow.updated_at,
        )
        .where(failed)
        .order_by(desc(RunRow.updated_at), desc(RunRow.created_at), desc(RunRow.run_id))
        .limit(FAILURE_WINDOW)
        .cte("recent_failed_runs")
    )
    count = select(func.count()).select_from(recent)
    page = select(recent)
    if normalized := query.strip().casefold():
        identity = or_(func.lower(UserRow.email).like(f"%{normalized}%"), func.lower(func.coalesce(UserRow.display_name, "")).like(f"%{normalized}%"))
        # Resolve legacy owners by indexed thread id only when runs.user_id is null.
        # A search must not restore the full-history outer joins that failed in production.
        legacy_owner = select(ThreadMetaRow.user_id).where(ThreadMetaRow.thread_id == recent.c.thread_id).correlate(recent).scalar_subquery()
        matching_owner = func.coalesce(recent.c.user_id, legacy_owner).in_(select(UserRow.id).where(identity))
        count = count.where(matching_owner)
        page = page.where(matching_owner)
    # LIMIT precedes owner, event and disposition lookups, including on a long history.
    selected = page.order_by(desc(recent.c.updated_at), desc(recent.c.created_at), desc(recent.c.run_id)).limit(max(1, min(limit, 100))).offset(max(0, offset)).subquery("failure_page")
    has_messages = select(RunEventRow.id).where(RunEventRow.thread_id == selected.c.thread_id, RunEventRow.category == "message").exists()
    disposition = (
        select(func.coalesce(func.nullif(OperationDispositionRow.status, ""), OperationDispositionRow.action))
        .where(OperationDispositionRow.run_id == selected.c.run_id)
        .order_by(desc(OperationDispositionRow.created_at), desc(OperationDispositionRow.id))
        .limit(1)
        .scalar_subquery()
    )
    owner = func.coalesce(selected.c.user_id, ThreadMetaRow.user_id)
    rows = (
        select(
            *(column for column in selected.c if column.name != "user_id"),
            RunRow.status,
            func.substr(RunRow.error, 1, ERROR_PREVIEW_CHARS).label("error"),
            (func.coalesce(func.length(RunRow.error), 0) > ERROR_PREVIEW_CHARS).label("error_truncated"),
            RunRow.assistant_id,
            RunRow.model_name,
            owner.label("user_id"),
            UserRow.email,
            UserRow.display_name,
            ThreadMetaRow.thread_id.is_not(None).label("thread_exists"),
            has_messages.label("has_messages"),
            disposition.label("disposition_status"),
        )
        .select_from(selected)
        .join(RunRow, RunRow.run_id == selected.c.run_id)
        .outerjoin(ThreadMetaRow, ThreadMetaRow.thread_id == selected.c.thread_id)
        .outerjoin(UserRow, UserRow.id == owner)
        .order_by(desc(selected.c.updated_at), desc(selected.c.created_at), desc(selected.c.run_id))
    )
    return count, rows

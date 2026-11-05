"""SQL-side limits for the read-only diagnostic UI, before ORM/JSON loading."""

from types import SimpleNamespace

from sqlalchemy import Text, case, cast, func, select

from deerflow.persistence.models.run_event import RunEventRow
from deerflow.persistence.observability.model import OperationDispositionRow
from deerflow.persistence.run.model import RunRow

EVENT_LIMIT = 30
TEXT_LIMIT = 4096
JSON_LIMIT = 1024
DISPOSITION_LIMIT = 20


def _text(column):
    return func.substr(column, 1, TEXT_LIMIT).label(column.key)


def _json(column):
    raw = cast(column, Text)
    # Do not transfer or decode an oversized JSON value at all.
    return case((func.length(raw) <= JSON_LIMIT, raw), else_="{}").label(column.key)


def _decode(value):
    import json

    try:
        result = json.loads(value or "{}")
    except (ValueError, TypeError, RecursionError):
        return {}
    return result if isinstance(result, dict) else {}


async def load_preview(session, run_id):
    columns = [
        getattr(RunRow, k)
        for k in (
            "run_id",
            "thread_id",
            "user_id",
            "status",
            "model_name",
            "assistant_id",
            "created_at",
            "updated_at",
            "total_tokens",
            "total_input_tokens",
            "total_output_tokens",
            "llm_call_count",
        )
    ]
    result = (
        (
            await session.execute(
                select(
                    *columns,
                    _text(RunRow.error),
                    _text(RunRow.first_human_message),
                    _json(RunRow.token_usage_by_model),
                ).where(RunRow.run_id == run_id)
            )
        )
        .mappings()
        .first()
    )
    if result is None:
        return None, [], 0
    data = dict(result)
    data["token_usage_by_model"] = _decode(data["token_usage_by_model"])
    total = await session.scalar(select(func.count()).select_from(RunEventRow).where(RunEventRow.run_id == run_id))
    # Projection matters: LIMIT alone still permits one multi-megabyte event.
    event_columns = [
        getattr(RunEventRow, k)
        for k in (
            "id",
            "thread_id",
            "run_id",
            "seq",
            "event_type",
            "category",
            "created_at",
        )
    ]
    result = (
        (
            await session.execute(
                select(
                    *event_columns,
                    _text(RunEventRow.content),
                    _json(RunEventRow.event_metadata),
                )
                .where(RunEventRow.run_id == run_id)
                .order_by(RunEventRow.seq.desc())
                .limit(EVENT_LIMIT)
            )
        )
        .mappings()
        .all()
    )
    events = []
    for event in reversed(result):
        item = dict(event)
        item["event_metadata"] = _decode(item["event_metadata"])
        events.append(SimpleNamespace(**item))
    return SimpleNamespace(**data), events, int(total or 0)


async def load_disposition_preview(session, run_id):
    model = OperationDispositionRow
    columns = [
        getattr(model, key)
        for key in (
            "id",
            "created_at",
            "run_id",
            "thread_id",
            "action",
            "status",
            "user_id",
            "user_email",
            "result",
        )
    ]
    rows = (
        (
            await session.execute(
                select(
                    *columns,
                    _text(model.note),
                    _json(model.details_json),
                )
                .where(model.run_id == run_id)
                .order_by(model.created_at.desc(), model.id.desc())
                .limit(DISPOSITION_LIMIT)
            )
        )
        .mappings()
        .all()
    )
    entries = []
    for row in rows:
        entry = dict(row)
        entry["created_at"] = entry["created_at"].isoformat() if entry["created_at"] else None
        entry["details"] = _decode(entry.pop("details_json"))
        entries.append(entry)
    # Status may predate the displayed tail; preserve it without loading notes/details.
    status = await session.scalar(
        select(model.status)
        .where(
            model.run_id == run_id,
            model.status.is_not(None),
            model.status != "",
        )
        .order_by(model.created_at.desc(), model.id.desc())
        .limit(1)
    )
    return entries, status

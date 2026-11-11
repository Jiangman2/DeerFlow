"""Behaviour of ``scripts/backfill_im_thread_registry.py``.

The backfill is the only way pre-S08 IM threads enter the registry, so its
failure modes matter: it must never invent an owner, never resurrect a deleted
thread, never write during a dry-run, and be safe to re-run (S08-X7).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from deerflow.persistence.im_routing import ImRoutingRepository
from deerflow.persistence.im_routing.model import ImMessageLinkRow
from deerflow.persistence.im_routing.registry_model import ImThreadRegistryRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from scripts.backfill_im_thread_registry import (
    REASON_NO_OWNER,
    REASON_THREAD_MISSING,
    STATUS_CONFLICT,
    STATUS_INSERTED,
    STATUS_SKIPPED,
    STATUS_WOULD_INSERT,
    backfill,
)

pytestmark = pytest.mark.anyio

OWNER_A = "u_alice"
PRIVATE_A = "oc_dm_alice_bot"
GROUP_X = "oc_group_x"
BOT = "cli_bot"


@pytest.fixture
async def env(tmp_path):
    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'backfill.db'}", sqlite_dir=str(tmp_path))
    factory = get_session_factory()
    try:
        yield factory, tmp_path / "reports"
    finally:
        await close_engine()


def _at(minutes_ago: int = 0) -> datetime:
    return datetime.now(UTC) - timedelta(minutes=minutes_ago)


async def _thread_meta(factory, thread_id: str, *, display_name: str | None = None) -> None:
    now = _at(30)
    async with factory() as session:
        session.add(
            ThreadMetaRow(
                thread_id=thread_id,
                assistant_id="assistant",
                user_id=OWNER_A,
                display_name=display_name,
                created_at=now,
                updated_at=now,
            ),
        )
        await session.commit()


async def _link(
    factory,
    *,
    thread_id: str,
    owner: str = OWNER_A,
    chat_id: str = GROUP_X,
    sender: str = "ou_sender",
    message_id: str,
    direction: str = "inbound",
    created_at: datetime | None = None,
) -> None:
    async with factory() as session:
        session.add(
            ImMessageLinkRow(
                id=f"link-{message_id}",
                provider="feishu",
                connection_id="conn-1",
                owner_user_id=owner,
                external_conversation_id=chat_id,
                message_id=message_id,
                direction=direction,
                message_kind="text",
                thread_id=thread_id,
                sender_external_id=sender,
                created_at=created_at or _at(5),
            ),
        )
        await session.commit()


async def _registry_rows(factory) -> dict[str, ImThreadRegistryRow]:
    async with factory() as session:
        rows = (await session.execute(select(ImThreadRegistryRow))).scalars().all()
    return {row.thread_id: row for row in rows}


async def test_dry_run_writes_nothing(env):
    factory, reports = env
    await _thread_meta(factory, "th_1", display_name="Hello")
    await _link(factory, thread_id="th_1", message_id="m1")

    report = await backfill(factory, apply=False, report_dir=reports)

    assert await _registry_rows(factory) == {}
    assert report["totals"][STATUS_WOULD_INSERT] == 1
    assert report["rows"][0]["status"] == STATUS_WOULD_INSERT
    assert report["report_paths"]["json"].endswith(".json")
    assert report["report_paths"]["csv"].endswith(".csv")


async def test_apply_inserts_from_links(env):
    factory, reports = env
    await _thread_meta(factory, "th_1", display_name="Hello")
    await _link(factory, thread_id="th_1", chat_id=PRIVATE_A, message_id="m1", created_at=_at(3))
    # Outbound rows are not evidence of an inbound conversation.
    await _link(factory, thread_id="th_out", message_id="m2", direction="outbound")

    report = await backfill(factory, apply=True, report_dir=reports)

    rows = await _registry_rows(factory)
    assert set(rows) == {"th_1"}
    row = rows["th_1"]
    assert (row.owner_user_id, row.external_conversation_id, row.channel_type) == (OWNER_A, PRIVATE_A, "private")
    assert row.title_snapshot == "Hello"
    assert row.status == "active"
    assert row.classification_source == "HEURISTIC"
    assert abs((row.last_active_at.replace(tzinfo=UTC) - _at(3)).total_seconds()) < 2
    assert report["totals"][STATUS_INSERTED] == 1
    assert report["rows"][0]["status"] == STATUS_INSERTED


async def test_heuristic_marks_group_when_multiple_senders(env):
    factory, reports = env
    await _thread_meta(factory, "th_group")
    await _thread_meta(factory, "th_dm")
    # Same chat, two distinct inbound senders -> looks like a group.
    await _link(factory, thread_id="th_group", chat_id=GROUP_X, sender="ou_a", message_id="m1")
    await _link(factory, thread_id="th_group", chat_id=GROUP_X, sender="ou_b", message_id="m2")
    # S08-X8: a chat with a single sender is classified private but flagged.
    await _link(factory, thread_id="th_dm", chat_id="oc_single_sender", sender="ou_a", message_id="m3")

    report = await backfill(factory, apply=True, report_dir=reports)

    rows = await _registry_rows(factory)
    assert rows["th_group"].channel_type == "group"
    assert rows["th_dm"].channel_type == "private"
    assert rows["th_group"].classification_source == "HEURISTIC"
    assert rows["th_dm"].classification_source == "HEURISTIC"
    flagged = {item["thread_id"] for item in report["heuristic"]}
    assert flagged == {"th_group", "th_dm"}


async def test_pointer_wins_over_heuristic(env):
    factory, reports = env
    await _thread_meta(factory, "th_1")
    await _link(factory, thread_id="th_1", chat_id=GROUP_X, sender="ou_a", message_id="m1")
    routing = ImRoutingRepository(factory)
    await routing.insert_pointer_if_absent(
        provider="feishu",
        owner_user_id=OWNER_A,
        chat_id=GROUP_X,
        channel_type="group",
        connection_id="conn-1",
        thread_id="th_1",
    )

    report = await backfill(factory, apply=True, report_dir=reports)

    row = (await _registry_rows(factory))["th_1"]
    assert row.channel_type == "group"
    assert row.classification_source == "BACKFILL_POINTER"
    assert report["heuristic"] == []


async def test_skips_missing_owner(env):
    factory, reports = env
    await _thread_meta(factory, "th_unowned")
    await _link(factory, thread_id="th_unowned", owner="", message_id="m1")

    report = await backfill(factory, apply=True, report_dir=reports)

    assert await _registry_rows(factory) == {}
    assert report["rows"][0]["status"] == STATUS_SKIPPED
    assert report["rows"][0]["reason"] == REASON_NO_OWNER
    assert [item["thread_id"] for item in report["no_owner"]] == ["th_unowned"]

    # --repair-owner only lists the rows; it must still never write.
    repair = await backfill(factory, repair_owner=True, report_dir=reports)
    assert await _registry_rows(factory) == {}
    assert [item["thread_id"] for item in repair["no_owner"]] == ["th_unowned"]


async def test_skips_missing_threads_meta(env):
    factory, reports = env
    # The link survived but the thread itself was deleted.
    await _link(factory, thread_id="th_gone", message_id="m1")

    report = await backfill(factory, apply=True, report_dir=reports)

    assert await _registry_rows(factory) == {}
    assert report["rows"][0]["status"] == STATUS_SKIPPED
    assert report["rows"][0]["reason"] == REASON_THREAD_MISSING


async def test_undo_batch_removes_rows(env):
    factory, reports = env
    await _thread_meta(factory, "th_1")
    await _link(factory, thread_id="th_1", message_id="m1")

    await backfill(factory, apply=True, batch_id="batch-a", report_dir=reports)
    rows = await _registry_rows(factory)
    assert rows["th_1"].classification_source.endswith("#batch-a")

    # Unrelated rows inserted by another path survive the undo.
    async with factory() as session:
        session.add(
            ImThreadRegistryRow(
                thread_id="th_runtime",
                provider="feishu",
                owner_user_id=OWNER_A,
                channel_type="group",
                external_conversation_id=GROUP_X,
                last_active_at=_at(0),
                created_at=_at(0),
                updated_at=_at(0),
            ),
        )
        await session.commit()

    report = await backfill(factory, undo_batch="batch-a", report_dir=reports)
    assert report["totals"]["deleted"] == 1
    assert set(await _registry_rows(factory)) == {"th_runtime"}


async def test_second_run_is_noop(env):
    factory, reports = env
    await _thread_meta(factory, "th_1", display_name="Hello")
    await _link(factory, thread_id="th_1", message_id="m1")

    first = await backfill(factory, apply=True, report_dir=reports)
    assert first["totals"][STATUS_INSERTED] == 1
    before = await _registry_rows(factory)

    second = await backfill(factory, apply=True, report_dir=reports)
    after = await _registry_rows(factory)

    assert set(after) == set(before)
    assert second["totals"][STATUS_INSERTED] == 0
    assert second["totals"][STATUS_CONFLICT] == 1
    assert second["rows"][0]["status"] == STATUS_CONFLICT
    # S08-X7: the registry row count is unchanged by the second run.
    async with factory() as session:
        count = int((await session.execute(select(func.count()).select_from(ImThreadRegistryRow))).scalar_one())
    assert count == len(before)

"""JSONL framing must not split Unicode separators inside event values."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deerflow.runtime.events.store.jsonl import JsonlRunEventStore

SEPARATORS = [chr(0x85), chr(0x2028), chr(0x2029)]
SEPARATOR_IDS = ["next-line", "line-separator", "paragraph-separator"]


@pytest.mark.anyio
@pytest.mark.parametrize("separator", SEPARATORS, ids=SEPARATOR_IDS)
@pytest.mark.parametrize("write_method", ["put", "put_batch"])
async def test_unicode_event_values_round_trip(
    tmp_path: Path,
    separator: str,
    write_method: str,
):
    store = JsonlRunEventStore(base_dir=tmp_path)
    content = {
        "type": "ai",
        "id": "message-1",
        "content": f"before{separator}after",
    }
    event = {
        "thread_id": "thread-1",
        "run_id": "run-1",
        "event_type": "llm.ai.response",
        "category": "message",
        "content": content,
        "metadata": {"note": f"first{separator}second"},
    }
    if write_method == "put_batch":
        saved = (await store.put_batch([event]))[0]
    else:
        saved = await store.put(**event)

    raw = (tmp_path / "threads/thread-1/runs/run-1.jsonl").read_bytes()
    assert separator.encode("utf-8") in raw
    assert json.loads(raw) == saved
    assert await store.list_messages("thread-1") == [saved]
    assert await store.list_events("thread-1", "run-1") == [saved]
    assert await store.list_messages_by_run("thread-1", "run-1") == [saved]
    assert await store.count_messages("thread-1") == 1


@pytest.mark.anyio
@pytest.mark.parametrize("separator", SEPARATORS, ids=SEPARATOR_IDS)
async def test_reopen_recovers_seq_from_existing_unicode_record(
    tmp_path: Path,
    separator: str,
):
    path = tmp_path / "threads/thread-1/runs/run-1.jsonl"
    path.parent.mkdir(parents=True)
    record = {
        "thread_id": "thread-1",
        "run_id": "run-1",
        "event_type": "llm.ai.response",
        "category": "message",
        "content": f"old{separator}message",
        "metadata": {},
        "seq": 41,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    path.write_text(
        json.dumps(record, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    reopened = JsonlRunEventStore(base_dir=tmp_path)
    following = await reopened.put(
        thread_id="thread-1",
        run_id="run-2",
        event_type="llm.ai.response",
        category="message",
        content="next message",
    )

    assert following["seq"] == 42
    assert await reopened.list_messages("thread-1") == [record, following]


@pytest.mark.anyio
@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
async def test_physical_lines_keep_controls_and_skip_malformed_records(
    tmp_path: Path,
    newline: str,
):
    store = JsonlRunEventStore(base_dir=tmp_path)
    saved = await store.put(
        thread_id="thread-1",
        run_id="run-1",
        event_type="llm.ai.response",
        category="message",
        content="ordinary\n你好\r\ntext",
    )
    path = tmp_path / "threads/thread-1/runs/run-1.jsonl"
    path.write_bytes(newline.join(["", "not-json", "  ", json.dumps(saved, ensure_ascii=False)]).encode("utf-8"))

    reopened = JsonlRunEventStore(base_dir=tmp_path)
    assert await reopened.list_messages("thread-1") == [saved]
    assert await reopened.list_events("thread-1", "run-1") == [saved]
    following = await reopened.put(
        thread_id="thread-1",
        run_id="run-2",
        event_type="llm.ai.response",
        category="message",
        content="next message",
    )
    assert following["seq"] == saved["seq"] + 1

"""Owner-isolation regressions for trusted internal run reads."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME, INTERNAL_SYSTEM_ROLE
from app.gateway.routers import thread_runs


def _request(*, metadata, raw_owner: str = "raw-owner", record=None):
    thread_store = SimpleNamespace(get=AsyncMock(return_value=metadata))
    run_manager = SimpleNamespace(get=AsyncMock(return_value=record))
    return SimpleNamespace(
        state=SimpleNamespace(user=SimpleNamespace(id="normalized-owner", system_role=INTERNAL_SYSTEM_ROLE)),
        headers={INTERNAL_OWNER_USER_ID_HEADER_NAME: raw_owner},
        app=SimpleNamespace(state=SimpleNamespace(thread_store=thread_store, run_manager=run_manager)),
    )


def test_internal_read_is_unscoped_only_after_thread_ownership_is_established():
    established = _request(metadata={"thread_id": "thread-1", "user_id": "raw-owner"})
    missing = _request(metadata=None)
    shared = _request(metadata={"thread_id": "thread-1", "user_id": None})

    assert asyncio.run(thread_runs._run_scope_user_id(established, "thread-1")) is None
    assert asyncio.run(thread_runs._run_scope_user_id(missing, "thread-1")) == "raw-owner"
    assert asyncio.run(thread_runs._run_scope_user_id(shared, "thread-1")) == "raw-owner"


def test_internal_run_subresource_rejects_another_owner_on_unowned_thread():
    request = _request(
        metadata=None,
        raw_owner="owner-a",
        record=SimpleNamespace(run_id="run-b", thread_id="thread-1", user_id="owner-b"),
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(thread_runs._require_run_visible_to_scope("run-b", "thread-1", request))

    assert exc.value.status_code == 404


def test_internal_run_subresource_accepts_matching_owner_on_unowned_thread():
    request = _request(
        metadata=None,
        raw_owner="owner-a",
        record=SimpleNamespace(run_id="run-a", thread_id="thread-1", user_id="owner-a"),
    )

    asyncio.run(thread_runs._require_run_visible_to_scope("run-a", "thread-1", request))

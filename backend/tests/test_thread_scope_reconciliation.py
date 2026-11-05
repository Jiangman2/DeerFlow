"""Regression coverage for restored owner scope and run subresource gates."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.gateway.internal_auth import INTERNAL_OWNER_USER_ID_HEADER_NAME, INTERNAL_SYSTEM_ROLE
from app.gateway.routers import thread_runs


def _request(*, role="user", user_id="session-owner", raw_owner="raw-owner", metadata=None, record=None):
    return SimpleNamespace(
        state=SimpleNamespace(user=SimpleNamespace(id=user_id, system_role=role)),
        headers={INTERNAL_OWNER_USER_ID_HEADER_NAME: raw_owner},
        app=SimpleNamespace(
            state=SimpleNamespace(
                thread_store=SimpleNamespace(get=AsyncMock(return_value=metadata)),
                run_manager=SimpleNamespace(get=AsyncMock(return_value=record), cancel=AsyncMock()),
                run_event_store=SimpleNamespace(list_messages_by_run=AsyncMock(), list_events=AsyncMock()),
                run_store=SimpleNamespace(aggregate_tokens_by_thread=AsyncMock(return_value={})),
            )
        ),
    )


@pytest.mark.parametrize(("role", "expected"), [("user", "session-owner"), ("admin", None)])
def test_thread_scope_does_not_honor_internal_owner_header_for_sessions(role, expected):
    request = _request(role=role, raw_owner="another-owner")
    assert asyncio.run(thread_runs._run_scope_user_id(request, "thread-1")) == expected
    request.app.state.thread_store.get.assert_not_awaited()


@pytest.mark.parametrize(
    "handler_name",
    ["cancel_run", "join_run", "stream_existing_run", "list_run_messages", "list_run_events", "get_run_workspace_changes"],
)
@pytest.mark.parametrize("metadata", [None, {"thread_id": "thread-1", "user_id": None}])
def test_internal_subresources_reject_foreign_run_before_read_or_control(handler_name, metadata):
    request = _request(
        role=INTERNAL_SYSTEM_ROLE,
        user_id="normalized-owner",
        metadata=metadata,
        record=SimpleNamespace(thread_id="thread-1", run_id="run-b", user_id="another-owner"),
    )
    # Authorization itself is covered separately. This callback-level test
    # makes sure the handler cannot reach event/control APIs before its gate.
    handler = getattr(thread_runs, handler_name).__wrapped__
    with pytest.raises(HTTPException) as exc:
        asyncio.run(handler(thread_id="thread-1", run_id="run-b", request=request))
    assert exc.value.status_code == 404
    request.app.state.run_manager.cancel.assert_not_awaited()
    request.app.state.run_event_store.list_messages_by_run.assert_not_awaited()
    request.app.state.run_event_store.list_events.assert_not_awaited()


@pytest.mark.parametrize("metadata", [None, {"user_id": None}, SimpleNamespace(user_id="")])
def test_internal_null_owner_thread_keeps_exact_raw_owner_scope(metadata):
    request = _request(role=INTERNAL_SYSTEM_ROLE, user_id="normalized-owner", metadata=metadata)
    assert asyncio.run(thread_runs._run_scope_user_id(request, "thread-1")) == "raw-owner"
    request.app.state.thread_store.get.assert_awaited_once_with("thread-1", user_id=None)


def test_internal_run_gate_also_rejects_mismatched_thread():
    request = _request(
        role=INTERNAL_SYSTEM_ROLE,
        record=SimpleNamespace(thread_id="thread-other", user_id="raw-owner"),
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(thread_runs._require_run_visible_to_scope("run-a", "thread-1", request))
    assert exc.value.status_code == 404


@pytest.mark.parametrize(
    ("role", "metadata", "expected"),
    [
        ("user", None, "session-owner"),
        ("admin", None, None),
        (INTERNAL_SYSTEM_ROLE, None, "raw-owner"),
        (INTERNAL_SYSTEM_ROLE, {"user_id": "raw-owner"}, None),
    ],
)
@pytest.mark.parametrize("include_active", [False, True])
def test_thread_usage_passes_owner_scope_for_completed_and_active_reads(role, metadata, expected, include_active):
    request = _request(role=role, metadata=metadata)
    response = asyncio.run(thread_runs.thread_token_usage.__wrapped__("thread-1", request, include_active=include_active))
    assert response.thread_id == "thread-1"
    kwargs = {"user_id": expected}
    if include_active:
        kwargs["include_active"] = True
    request.app.state.run_store.aggregate_tokens_by_thread.assert_awaited_once_with("thread-1", **kwargs)

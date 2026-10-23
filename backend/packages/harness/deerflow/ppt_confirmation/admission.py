"""Admit confirmation transport from persisted, authenticated initial input.

The caller has verified the service credential, exact run and thread owner.
No request body, model text, global auto flag or resumed checkpoint grants
consent. A retry reads the immutable original policy. The Host independently
checks its admission journal before enabling the native per-task protocol.
"""

from __future__ import annotations

import re

from deerflow.ppt_confirmation.lifecycle import require

FIELD = "ppt_confirmation_mode"


def _initial_request(row):
    kwargs = row.get("kwargs")
    data = kwargs.get("input") if isinstance(kwargs, dict) else None
    messages = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(messages, list) or not 1 <= len(messages) <= 64:
        return False, False, False
    marked = [message for message in messages if isinstance(message, dict) and isinstance(message.get("additional_kwargs"), dict) and FIELD in message["additional_kwargs"]]
    # Consent is only supported on one initial human message. Extra historical
    # or generated input must not be mistaken for a new task's explicit choice.
    if len(messages) != 1 or not isinstance(messages[0], dict):
        require(not marked, "confirmation_requires_single_initial_human")
        return False, False, False
    message = messages[0]
    human = message.get("type", "human" if message.get("role") == "user" else None) == "human"
    human = human and message.get("role", "user") == "user"
    require(human or not marked, "confirmation_requires_initial_human")
    if not human:
        return False, False, False
    mode = marked[0]["additional_kwargs"][FIELD] if marked else "manual"
    require(type(mode) is str and mode in {"manual", "auto_after_60s"}, "invalid_confirmation_mode")
    content = message.get("content")
    if isinstance(content, list) and len(content) <= 32:
        # Upload references are separate metadata; don't concatenate arbitrary
        # structured blocks into a synthetic Slash command.
        content = "".join(part["text"] for part in content if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str))
    explicit = isinstance(content, str) and re.match(r"^/ppt-master(?:\s|$)", content.lstrip(), re.IGNORECASE) is not None
    return explicit, mode == "auto_after_60s", bool(marked)


async def admit_initial_task(scope, row, *, run_store, repository):
    expected = {"user_id": scope.owner_id, "thread_id": scope.thread_id, "run_id": scope.task_id, "assistant_id": "ppt-master-runner"}
    require(isinstance(row, dict) and all(row.get(key) == value for key, value in expected.items()), "confirmation_run_scope_mismatch")
    require(row.get("status") in {"pending", "running"} and not row.get("cancel_action"), "confirmation_run_inactive")
    existing = await repository.get(scope)
    if existing is not None:
        require(not existing["cancelled"], "confirmation_task_cancelled")
        return existing
    explicit, opted_in, marked = _initial_request(row)
    if not explicit:
        require(not marked, "confirmation_requires_new_ppt_task")
        return None
    history = await run_store.list_by_thread(scope.thread_id, user_id=scope.owner_id, limit=2)
    fresh = isinstance(history, list) and len(history) == 1 and isinstance(history[0], dict) and all(history[0].get(key) == value for key, value in expected.items())
    if not fresh:
        require(not marked, "confirmation_requires_new_ppt_task")
        return None
    return await repository.admit(scope, runtime_profile="ppt-master-runner", skill_name="ppt-master", opted_in=opted_in, new_task=True)

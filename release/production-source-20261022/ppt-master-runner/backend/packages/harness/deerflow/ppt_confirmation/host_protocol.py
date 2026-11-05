"""Read-only fingerprint for the two source-locked PPT Host protocol versions.

Health and Skill versions do not identify confirmation support. Before a fresh
task is submitted, require its authenticated absence and the new Host's exact
missing-task continuation receipt. This is not generic HTTP version negotiation.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets

import httpx

_BODY_LIMIT = 4096
_TOTAL_TIMEOUT_SECONDS = 10.0
_REQUEST_TIMEOUT_SECONDS = 5.0
_MESSAGES = {
    "legacy_host": "当前会话复用了不支持确认协议的旧版 PPT 运行环境。本次未提交新任务，原任务和文件未改动。请新建会话后重新发起 /ppt-master。",
    "existing_task": "当前运行的 PPT 任务已存在，本次未重复提交，也未停止原任务。请先查看原任务状态，不要反复发起同一运行。",
    "unavailable": "无法核实当前 PPT 运行环境的确认协议能力，本次未提交新任务。原任务和文件未改动，请稍后重试或联系管理员。",
}


class ConfirmationHostCompatibilityError(RuntimeError):
    """Only fixed, user-safe messages may cross the durable task error boundary."""

    def __init__(self, reason: str) -> None:
        self.reason = reason if reason in _MESSAGES else "unavailable"
        super().__init__(_MESSAGES[self.reason])


def _unique_object(items: list[tuple[str, object]]) -> dict:
    result = {}
    for name, value in items:
        if name in result:
            raise ValueError("duplicate_json_key")
        result[name] = value
    return result


async def _read_fingerprint(client: httpx.AsyncClient, url: str, *, task_lookup: bool) -> tuple[int, object]:
    async with client.stream(
        "GET",
        url,
        params={"after_seq": 2147483647} if task_lookup else None,
        headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        timeout=httpx.Timeout(_REQUEST_TIMEOUT_SECONDS),
        follow_redirects=False,
    ) as response:
        # A current run collision is never permission to POST again or to take
        # ownership of the existing task. Do not download its event/result body.
        if task_lookup and response.status_code == 200:
            raise ConfirmationHostCompatibilityError("existing_task")
        if response.status_code not in (404, 409):
            raise ConfirmationHostCompatibilityError("unavailable")
        if response.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise ConfirmationHostCompatibilityError("unavailable")
        if response.headers.get("content-encoding", "identity").strip().lower() not in ("", "identity"):
            raise ConfirmationHostCompatibilityError("unavailable")
        body = bytearray()
        async for part in response.aiter_bytes(chunk_size=_BODY_LIMIT + 1):
            if len(body) + len(part) > _BODY_LIMIT:
                raise ConfirmationHostCompatibilityError("unavailable")
            body.extend(part)
        value = json.loads(bytes(body).decode("utf-8"), object_pairs_hook=_unique_object)
        return response.status_code, value


async def require_confirmation_host(*, client: httpx.AsyncClient, host_url: str, task_id: str) -> None:
    """Never submit, recover, cancel, mutate a receipt, or remove a Sandbox.

    A resumed conversation still starts a distinct current run ID. If that run
    already exists, stop without reposting it; a prior session/task is preserved.
    Calls use the caller's already authenticated, direct Host client.
    """
    if not isinstance(task_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id) is None:
        raise ConfirmationHostCompatibilityError("unavailable")
    try:
        async with asyncio.timeout(_TOTAL_TIMEOUT_SECONDS):
            status, body = await _read_fingerprint(client, f"{host_url}/v1/tasks/{task_id}", task_lookup=True)
            if status != 404 or body != {"detail": "task not found"}:
                raise ConfirmationHostCompatibilityError("unavailable")
            nonce = secrets.token_hex(32)
            status, body = await _read_fingerprint(client, f"{host_url}/v1/tasks/{task_id}/confirmation/continuation-receipts/{nonce}", task_lookup=False)
            if status == 404 and body == {"detail": "not found"}:
                raise ConfirmationHostCompatibilityError("legacy_host")
            if status != 409 or body != {"detail": "confirmation_continuation_unavailable"}:
                raise ConfirmationHostCompatibilityError("unavailable")
    except (httpx.HTTPError, TimeoutError, ValueError):
        raise ConfirmationHostCompatibilityError("unavailable") from None

"""Layer 2: typed capabilities; identity is a server-only argument, not a tool field."""

import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .auth import Principal, TokenVault
from .client import DOC_ID_RE, DOCUMENT_HOST, WorkError
from .scopes import ACTION_SCOPES, API_SCOPES

logger = logging.getLogger(__name__)
MAX_DOCUMENT_CHARS = 80_000


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    operation: Literal["document_read", "document_create", "document_rename", "document_append", "document_replace", "events_list", "event_create", "event_update", "clarify"]
    document_id: str = Field(default="", max_length=128)
    event_id: str = Field(default="", max_length=256)
    title: str = Field(default="", max_length=200)
    text: str = Field(default="", max_length=4000)
    old_text: str = Field(default="", max_length=4000)
    start: str = Field(default="", max_length=40)
    end: str = Field(default="", max_length=40)
    followup: bool = Field(default=False, description="仅clarify且需要用户继续补全上个请求时true；不支持的操作或普通闲聊为false")
    # Server-resolved metadata, never accepted as model/tool input.
    update_fields: tuple[Literal["summary", "description", "start_time", "end_time"], ...] = ()
    expected_event: str = Field(default="", max_length=24000, repr=False)

    @model_validator(mode="after")
    def validate_operation(self):
        if self.followup and self.operation != "clarify":
            raise ValueError("Only a clarification can request a follow-up")
        if self.operation.startswith("document_") and self.operation != "document_create" and not DOC_ID_RE.fullmatch(self.document_id):
            raise ValueError("A validated document target is required")
        if self.operation in {"document_create", "document_rename", "event_create", "event_update"} and not self.title.strip():
            raise ValueError("Title is required")
        if self.operation == "document_create" and self.text:
            raise ValueError("Create an empty document, then append content in a separate confirmed action")
        if self.operation in {"document_append", "document_replace"} and not self.text.strip():
            raise ValueError("Text is required")
        if self.operation == "document_replace" and not self.old_text:
            raise ValueError("Exact original paragraph is required")
        if self.operation == "event_update" and not self.event_id:
            raise ValueError("An event id from a previous user-scoped query is required")
        if (self.update_fields or self.expected_event) and self.operation != "event_update":
            raise ValueError("Resolved edit metadata is only valid for an event update")
        if len(self.update_fields) != len(set(self.update_fields)):
            raise ValueError("Duplicate update fields")
        if self.operation in {"event_create", "event_update", "events_list"}:
            begin, end = parse_time(self.start), parse_time(self.end)
            if end <= begin or end - begin > 31 * 86400:
                raise ValueError("An explicit time-zone-aware interval of at most 31 days is required")
        return self


def parse_time(value: str) -> int:
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            raise ValueError
        return int(dt.timestamp())
    except (ValueError, OverflowError):
        raise ValueError("Use an ISO date/time including a timezone") from None


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def doc_url(document_id: str, host: str = DOCUMENT_HOST) -> str:
    if not DOC_ID_RE.fullmatch(document_id):
        raise WorkError("invalid_document_id")
    return f"https://{host}/docx/{document_id}"


@dataclass
class Plan:
    id: str
    principal: Principal = field(repr=False)
    payload_json: str = field(repr=False)
    preview: str = field(repr=False)
    snapshot: str = field(repr=False)
    signature: str = field(repr=False)
    expires_at: float
    stage: str = "pending"
    receipt: str = field(default="", repr=False)


class WorkCapabilities:
    def __init__(self, vault: TokenVault, api, *, document_host=DOCUMENT_HOST):
        self.vault, self.api = vault, api
        self.document_host, self.store = document_host, vault.store
        self.plans: dict[str, Plan] = {}
        self.known_events: dict[Principal, tuple[float, set[str]]] = {}
        self._signing_key = self.store.signing_key if self.store else secrets.token_bytes(32)
        # Single-process UAT execution is serialized, including duplicate confirms.
        import asyncio

        self._write_lock = asyncio.Lock()

    async def call(self, p: Principal, op: str, **kwargs):
        scope = API_SCOPES.get(op)
        if scope is None:
            raise WorkError("unsupported_operation")
        grant = await self.vault.require(p, scope)
        try:
            result = await self.api(op, token=grant.token, **kwargs)
        except WorkError as exc:
            if exc.http_status == 401 or exc.business_code == 99991679:
                await self.vault.revoke_shared(p, expected=grant)
            raise
        # Discard results if binding changed during the network request.
        await self.vault.require(p, scope)
        return result.get("data", {})

    async def info(self, p: Principal, doc: str) -> dict:
        data = await self.call(p, "document_info", document_id=doc)
        value = data.get("document", {})
        if not isinstance(value.get("title"), str) or value.get("document_id", doc) != doc:
            raise WorkError("document_info_invalid")
        return value

    async def read_document(self, p: Principal, doc: str) -> tuple[str, str]:
        info = await self.info(p, doc)
        data = await self.call(p, "document_content", document_id=doc)
        content = data.get("content")
        if not isinstance(content, str) or len(content) > MAX_DOCUMENT_CHARS:
            raise WorkError("document_content_invalid_or_too_large")
        return info["title"], content

    async def primary(self, p: Principal) -> str:
        data = await self.call(p, "calendar_primary", payload={})
        calendars = [item.get("calendar", {}) for item in data.get("calendars", [])]
        ids = [c.get("calendar_id") for c in calendars if c.get("type") == "primary" and c.get("role") == "owner"]
        if len(ids) != 1 or not isinstance(ids[0], str):
            raise WorkError("primary_calendar_unavailable")
        return ids[0]

    async def events(self, p: Principal, action: Action) -> str:
        from .calendar import describe

        items, complete = await self.query_events(p, action.start, action.end)
        self.remember_events(p, [item.get("event_id", "") for item in items])
        return "主日历日程（北京时间）：\n" + (describe(items[:50]) or "该时间范围内没有日程。") + ("\n结果较多或未完整查询，请缩小日期范围。" if not complete or len(items) > 50 else "")

    async def query_events(self, p: Principal, start: str, end: str) -> tuple[list[dict], bool]:
        Action(operation="events_list", start=start, end=end)
        calendar = await self.primary(p)
        params = {"start_time": str(parse_time(start)), "end_time": str(parse_time(end)), "page_size": 50}
        items, seen, pages = [], set(), set()
        for _ in range(5):
            data = await self.call(p, "events", calendar_id=calendar, params=dict(params))
            batch = data.get("items", [])
            if not isinstance(batch, list) or len(batch) > 50:
                raise WorkError("event_list_invalid")
            for item in batch:
                if not isinstance(item, dict) or not isinstance(item.get("event_id"), str) or not item["event_id"]:
                    raise WorkError("event_list_invalid")
                if item.get("status") != "cancelled" and item["event_id"] not in seen:
                    items.append(item)
                    seen.add(item["event_id"])
            if not data.get("has_more"):
                return items, True
            token = data.get("page_token")
            if not isinstance(token, str) or not token or len(token) > 2000 or token in pages:
                return items, False
            pages.add(token)
            params["page_token"] = token
        return items, False

    async def editable_event(self, p: Principal, event_id: str, *, calendar: str | None = None) -> dict:
        known = self.known_events.get(p)
        if not known or known[0] <= time.monotonic() or event_id not in known[1]:
            raise WorkError("query_event_before_editing")
        calendar = calendar or await self.primary(p)
        event = (await self.call(p, "event_get", calendar_id=calendar, event_id=event_id)).get("event", {})
        if event.get("has_more_attendee") or event.get("recurrence") or event.get("is_exception"):
            raise WorkError("shared_or_recurring_event_not_supported")
        if event.get("event_id") != event_id or event.get("status") == "cancelled" or event.get("organizer_calendar_id") != calendar:
            raise WorkError("event_unavailable")
        attendees = await self.call(p, "event_attendees", calendar_id=calendar, event_id=event_id, params={"page_size": 10, "user_id_type": "open_id"})

        def only_self(items):
            return isinstance(items, list) and all(isinstance(item, dict) and item.get("type") == "user" and item.get("user_id") == p.sender and item.get("is_organizer") is True and not item.get("is_external") for item in items)

        if not only_self(attendees.get("items")) or attendees.get("has_more") or not only_self(event.get("attendees", [])):
            raise WorkError("event_with_attendees_not_supported")
        return {"calendar_id": calendar, "event": {k: event.get(k) for k in ("event_id", "summary", "description", "start_time", "end_time", "status", "organizer_calendar_id")}}

    def remember_events(self, p, ids):
        now = time.monotonic()
        self.known_events = {key: item for key, item in self.known_events.items() if item[0] > now}
        previous = self.known_events.get(p, (0, set()))[1]
        self.known_events[p] = (now + 900, set(list(previous | {i for i in ids if i})[-100:]))

    def _sign(self, p: Principal, payload: str, snapshot: str, preview: str):
        return hmac.new(self._signing_key, encode([p.__dict__, payload, snapshot, preview]).encode(), hashlib.sha256).hexdigest()

    async def _snapshot(self, p: Principal, a: Action) -> dict:
        if a.operation in {"document_rename", "document_append", "document_replace"}:
            info = await self.info(p, a.document_id)
            revision = info.get("revision_id")
            if type(revision) is not int or revision < 0:
                raise WorkError("document_revision_missing")
            snap = {"title": info["title"], "revision": revision}
            if a.operation == "document_replace":
                # Do not flatten formatted paragraphs or overwrite document bodies.
                data = await self.call(p, "blocks", document_id=a.document_id, params={"page_size": 500, "document_revision_id": revision})
                if data.get("has_more"):
                    raise WorkError("document_blocks_too_large")
                matches = []
                for block in data.get("items", []):
                    elements = block.get("text", {}).get("elements", [])
                    if block.get("block_type") == 2 and len(elements) == 1 and set(elements[0]) == {"text_run"}:
                        run = elements[0]["text_run"]
                        if run.get("content") == a.old_text and not any(run.get("text_element_style", {}).values()):
                            matches.append(block["block_id"])
                if len(matches) != 1:
                    raise WorkError("exact_plain_paragraph_not_unique")
                snap["block_id"] = matches[0]
            return snap
        if a.operation.startswith("event_"):
            calendar = await self.primary(p)
            snap = {"calendar_id": calendar}
            if a.operation == "event_update":
                return await self.editable_event(p, a.event_id, calendar=calendar)
            return snap
        if a.operation == "document_create":
            await self.vault.require(p, "docx:document")
            return {}
        raise WorkError("not_a_write_operation")

    async def prepare(self, p: Principal, action: Action) -> Plan:
        self.prune()
        if sum(plan.stage == "pending" and plan.expires_at > time.monotonic() for plan in self.plans.values()) >= 64:
            raise WorkError("plan_capacity")
        scope = ACTION_SCOPES.get(action.operation)
        if scope is None:
            raise WorkError("not_a_write_operation")
        await self.vault.require(p, scope)
        snapshot = await self._snapshot(p, action)
        if action.expected_event and action.expected_event != encode(snapshot.get("event")):
            raise WorkError("target_changed_request_new_preview")
        if action.update_fields:
            before = snapshot["event"]
            if any(k not in action.update_fields and (before.get(k) or "") != v for k, v in (("summary", action.title), ("description", action.text))):
                raise WorkError("unchanged_event_fields_mismatch")
            if any(k not in action.update_fields and str(before.get(k, {}).get("timestamp")) != str(parse_time(v)) for k, v in (("start_time", action.start), ("end_time", action.end))):
                raise WorkError("unchanged_event_fields_mismatch")
        payload = action.model_dump_json()
        names = {
            "document_create": "新建空白文档（个人云空间）",
            "document_rename": "修改文档标题",
            "document_append": "文档末尾追加纯文字",
            "document_replace": "替换唯一匹配的无格式文本段落",
            "event_create": "在个人主日历新建日程（不邀请其他人）",
            "event_update": "编辑个人主日历日程",
        }
        preview = names[action.operation] + "\n" + encode(action.model_dump(exclude_defaults=True)) + "\n当前状态：" + encode(snapshot)
        if action.operation == "event_update" and action.update_fields:
            from .calendar import event_fact

            before = snapshot["event"]
            old = event_fact(before)
            fields = {"summary": ("名称", old["title"], action.title), "description": ("描述", before.get("description") or "", action.text), "start_time": ("开始", old["start"], action.start), "end_time": ("结束", old["end"], action.end)}
            preview = (
                names[action.operation] + f"：{old['title']}\n原时间：{old['start']} 至 {old['end']}\n" + "\n".join(f"{fields[k][0]}：{fields[k][1]} → {fields[k][2]}" for k in action.update_fields) + "\n未列出的字段保持不变；不邀请他人。"
            )
        if action.document_id:
            preview += "\n" + doc_url(action.document_id, self.document_host)
        snap = encode(snapshot)
        plan = Plan(secrets.token_urlsafe(16), p, payload, preview, snap, self._sign(p, payload, snap, preview), time.monotonic() + 300)
        self.plans[plan.id] = plan
        if self.store:
            await self.store.purge()
            key = self.vault.principal_key(p)
            record = await self.store.admit("plan", plan.id, self._plan_data(plan), owner=key, ttl=300, active={"waiting", "pending"}, limit=256, owner_limit=8)
            if not record:
                self.plans.pop(plan.id, None)
                raise WorkError("plan_capacity")
            await self.store.change("plan", plan.id, record, status="pending")
            await self._audit(plan, "preview")
        logger.info("work_operation operation=%s stage=preview", action.operation)
        return plan

    async def confirm(self, p: Principal, plan_id: str) -> str:
        if not self.store:
            return await self._confirm(p, plan_id)
        import asyncio

        name, holder = "write:" + self.vault.principal_key(p), secrets.token_urlsafe(32)
        if not await self.store.acquire(name, holder, ttl=240):
            raise WorkError("another_write_in_progress")
        try:
            async with asyncio.timeout(180):
                return await self._confirm(p, plan_id)
        finally:
            await self.store.release(name, holder)

    async def _confirm(self, p: Principal, plan_id: str) -> str:
        async with self._write_lock:
            record = None
            if self.store:
                record = await self.store.read("plan", plan_id, owner=self.vault.principal_key(p))
                if record and record.status != "pending":
                    raise WorkError("confirmation_already_claimed_or_expired")
                if record:
                    plan = Plan(**{**record.value, "principal": p, "expires_at": time.monotonic() + record.expires_at - time.time(), "stage": record.status})
                    self.plans[plan_id] = plan
                else:
                    plan = None
            else:
                plan = self.plans.get(plan_id)
            if not plan or plan.principal != p or plan.stage != "pending" or plan.expires_at <= time.monotonic():
                raise WorkError("confirmation_invalid_or_expired")
            if not hmac.compare_digest(plan.signature, self._sign(p, plan.payload_json, plan.snapshot, plan.preview)):
                plan.stage = "rejected"
                raise WorkError("plan_changed")
            action = Action.model_validate_json(plan.payload_json)
            if self.store and action.operation == "event_update":
                # A persisted, signed preview is trusted discovery evidence, not
                # a user/model supplied event ID. Still re-fetch all edit guards.
                self.remember_events(p, [action.event_id])
            await self.vault.require(p, ACTION_SCOPES[action.operation])
            if encode(await self._snapshot(p, action)) != plan.snapshot:
                plan.stage = "rejected"
                raise WorkError("target_changed_request_new_preview")
            if self.store:
                record = await self.store.change("plan", plan_id, record, value={"id": plan.id, "operation": action.operation, "target": action.document_id or action.event_id or "personal"}, status="executing", ttl=90 * 86400)
                if not record:
                    raise WorkError("confirmation_already_claimed")
            plan.stage = "executing"
            await self._audit(plan, "executing")
            logger.info("work_operation operation=%s stage=executing", action.operation)
            try:
                result = await self._execute(p, action, json.loads(plan.snapshot), plan)
                plan.stage = "completed"
                return result
            except BaseException:
                # Never automatically resubmit a write, including cancellation/restart.
                plan.stage = "uncertain"
                raise
            finally:
                if self.store:
                    await self._save_execution(plan, status=plan.stage)
                    await self._audit(plan, plan.stage)
                logger.info("work_operation operation=%s stage=%s", action.operation, plan.stage)

    @staticmethod
    def _plan_data(plan):
        return {name: getattr(plan, name) for name in ("id", "payload_json", "preview", "snapshot", "signature", "receipt")}

    async def _audit(self, plan, stage):
        if self.store:
            action = Action.model_validate_json(plan.payload_json)
            await self.store.audit(plan.principal, action.operation, action.document_id or action.event_id or "personal", stage, plan_id=plan.id, receipt=plan.receipt)

    async def _save_execution(self, plan, status="executing"):
        if self.store:
            record = await self.store.read("plan", plan.id, owner=self.vault.principal_key(plan.principal))
            if not record or record.status != "executing" or not await self.store.change("plan", plan.id, record, value={**record.value, "receipt": plan.receipt}, status=status, ttl=90 * 86400):
                raise WorkError("execution_record_unavailable")

    async def cancel_shared(self, p, plan_id):
        if not self.store:
            return self.cancel(p, plan_id)
        record = await self.store.read("plan", plan_id, owner=self.vault.principal_key(p))
        if not record or record.status != "pending" or not await self.store.change("plan", plan_id, record, status="cancelled", ttl=900):
            raise WorkError("confirmation_invalid_or_expired")

    async def terminal_notice(self, p, plan_id):
        if not self.store:
            return ""
        record = await self.store.read("plan", plan_id, owner=self.vault.principal_key(p))
        if not record or record.status not in {"executing", "uncertain", "completed"}:
            return ""
        text = "该操作此前已完成，不会重复执行。" if record.status == "completed" else "先前操作的执行结果尚未确认，请到 Work 核查；不会自动重试或重复执行。"
        receipt = record.value.get("receipt", "")
        return text + ("\n回执：" + receipt if receipt else "")

    def doc_url(self, document_id):
        return doc_url(document_id, self.document_host)

    async def _execute(self, p, a, snap, plan):
        if a.operation == "document_create":
            # This API has no idempotency parameter in the deployed SDK. Never retry.
            data = await self.call(p, "document_create", payload={"title": a.title})
            doc = data.get("document", {}).get("document_id", "")
            plan.receipt = self.doc_url(doc)
            await self._save_execution(plan)
            if (await self.info(p, doc))["title"] != a.title:
                raise WorkError("write_readback_mismatch")
            return "已新建并核对空白文档：" + plan.receipt
        if a.operation.startswith("document_"):
            params = {"document_revision_id": snap["revision"], "client_token": str(uuid.uuid5(uuid.NAMESPACE_URL, plan.id))}
            elements = [{"text_run": {"content": a.title if a.operation == "document_rename" else a.text}}]
            if a.operation == "document_append":
                data = await self.call(p, "block_append", document_id=a.document_id, block_id=a.document_id, params=params, payload={"index": -1, "children": [{"block_type": 2, "text": {"elements": elements}}]})
                children = data.get("children", [])
                if len(children) != 1 or not children[0].get("block_id"):
                    raise WorkError("write_receipt_missing")
                block = children[0]["block_id"]
            else:
                block = a.document_id if a.operation == "document_rename" else snap["block_id"]
                await self.call(p, "block_update", document_id=a.document_id, block_id=block, params=params, payload={"update_text_elements": {"elements": elements}})
            plan.receipt = self.doc_url(a.document_id)
            await self._save_execution(plan)
            if a.operation == "document_rename":
                matches = (await self.info(p, a.document_id))["title"] == a.title
            else:
                data = await self.call(p, "block_get", document_id=a.document_id, block_id=block)
                matches = data.get("block", {}).get("text", {}).get("elements") == elements
                # Work may add default style fields; compare the text without discarding unexpected text elements.
                if not matches:
                    actual = data.get("block", {}).get("text", {}).get("elements", [])
                    matches = len(actual) == 1 and actual[0].get("text_run", {}).get("content") == a.text
            if not matches:
                raise WorkError("write_readback_mismatch")
            return f"已执行并回读核对：{a.title or a.text}\n{plan.receipt}"
        payload = {"summary": a.title, "description": a.text, "start_time": {"timestamp": str(parse_time(a.start)), "timezone": "Asia/Shanghai"}, "end_time": {"timestamp": str(parse_time(a.end)), "timezone": "Asia/Shanghai"}}
        expected = dict(payload)
        if a.operation == "event_update" and a.update_fields:
            payload = {k: payload[k] for k in a.update_fields}
        params = {"idempotency_key": digest(plan.id)} if a.operation == "event_create" else {}
        data = await self.call(p, a.operation, calendar_id=snap["calendar_id"], event_id=a.event_id or None, payload=payload, params=params)
        event_id = data.get("event", {}).get("event_id") or a.event_id
        if not event_id:
            raise WorkError("write_receipt_missing")
        plan.receipt = event_id
        await self._save_execution(plan)
        event = (await self.call(p, "event_get", calendar_id=snap["calendar_id"], event_id=event_id)).get("event", {})
        if any((event.get(k) or "") != expected[k] for k in ("summary", "description")) or any(str(event.get(k, {}).get("timestamp")) != expected[k]["timestamp"] for k in ("start_time", "end_time")):
            raise WorkError("write_readback_mismatch")
        self.remember_events(p, [event_id])
        return f"日程已执行并回读核对：{a.title}\n{a.start} 至 {a.end}\nevent_id：{event_id}"

    def cancel(self, p: Principal, plan_id: str):
        plan = self.plans.get(plan_id)
        if not plan or plan.principal != p or plan.stage != "pending":
            raise WorkError("confirmation_invalid_or_expired")
        plan.stage = "cancelled"

    def prune(self):
        for key, plan in list(self.plans.items()):
            if plan.expires_at + 900 < time.monotonic() and plan.stage != "executing":
                self.plans.pop(key, None)

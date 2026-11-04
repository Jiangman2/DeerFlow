"""Layer 2: typed capabilities; identity is a server-only argument, not a tool field."""

import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .auth import Principal, TokenVault
from .batch import BatchPayload, item_fact, outcome_text
from .batch import preview as batch_preview
from .client import DOC_ID_RE, DOCUMENT_HOST, WorkError
from .scopes import ACTION_SCOPES, API_SCOPES
from .tasks import TaskRef

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
    task_ref: TaskRef | None = field(default=None, repr=False)
    protocol: int = 0
    record_kind: str = field(default="plan", repr=False)
    batch_items: list[dict] | None = field(default=None, repr=False)
    batch_group: str = field(default="", repr=False)


class WorkCapabilities:
    def __init__(self, vault: TokenVault, api, *, document_host=DOCUMENT_HOST, task_fencing=False, task_guard=None):
        self.vault, self.api = vault, api
        self.document_host, self.store = document_host, vault.store
        self.plans: dict[str, Plan] = {}
        self.known_events: dict[Principal, tuple[float, set[str]]] = {}
        self._signing_key = self.store.signing_key if self.store else secrets.token_bytes(32)
        self.task_fencing, self.task_guard = task_fencing, task_guard
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

    async def _assert_task(self, p, task_ref):
        ref = TaskRef.parse(task_ref)
        if ref is None:
            if self.task_fencing:
                raise WorkError("task_reference_required")
            return None
        if self.store:
            await self.store.assert_task(self.vault.principal_key(p), ref)
        elif self.task_guard is None:
            raise WorkError("task_store_unavailable")
        if self.task_guard is not None and await self.task_guard(p, ref) is False:
            raise WorkError("task_superseded")
        return ref

    def _sign(self, p: Principal, payload: str, snapshot: str, preview: str, task_ref=None, protocol=0):
        data = [p.__dict__, payload, snapshot, preview]
        ref = TaskRef.parse(task_ref)
        if ref is not None:
            data.append(ref.as_dict())
        if protocol:
            data.append({"protocol": protocol})
        return hmac.new(self._signing_key, encode(data).encode(), hashlib.sha256).hexdigest()

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

    async def prepare(self, p: Principal, action: Action, *, task_ref=None) -> Plan:
        self.prune()
        ref = await self._assert_task(p, task_ref)
        if sum(plan.stage == "pending" and plan.expires_at > time.monotonic() for plan in self.plans.values()) >= 64:
            raise WorkError("plan_capacity")
        scope = ACTION_SCOPES.get(action.operation)
        if scope is None:
            raise WorkError("not_a_write_operation")
        await self.vault.require(p, scope)
        snapshot = await self._snapshot(p, action)
        # A slow discovery call must not preview work after a newer user turn.
        await self._assert_task(p, ref)
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
        plan = Plan(secrets.token_urlsafe(16), p, payload, preview, snap, self._sign(p, payload, snap, preview, ref, 2), time.monotonic() + 300, task_ref=ref, protocol=2)
        self.plans[plan.id] = plan
        if self.store:
            try:
                await self.store.purge()
                key = self.vault.principal_key(p)
                record = await self.store.admit("plan", plan.id, self._plan_data(plan), owner=key, ttl=300, active={"waiting", "pending"}, limit=256, owner_limit=8, task_ref=ref)
                if not record:
                    raise WorkError("plan_capacity")
                pending = await self.store.change_task_record("plan", plan.id, record, owner=key, task_ref=ref, status="pending") if ref else await self.store.change("plan", plan.id, record, status="pending")
                if not pending:
                    raise WorkError("task_superseded" if ref else "plan_state_conflict")
                await self._audit(plan, "preview")
            except BaseException:
                self.plans.pop(plan.id, None)
                raise
        logger.info("work_operation operation=%s stage=preview", action.operation)
        return plan

    @staticmethod
    def _batch_actions(payload):
        batch = BatchPayload.model_validate_json(payload) if isinstance(payload, str) else payload
        actions = [Action.model_validate_json(encode(value)) for value in batch.items]
        seen = set()
        for action in actions:
            if action.operation not in {"event_create", "event_update"} or action.document_id or action.old_text or action.followup:
                raise WorkError("batch_calendar_only")
            key = (action.operation, action.event_id) if action.operation == "event_update" else (action.operation, action.title, parse_time(action.start), parse_time(action.end))
            if key in seen:
                raise WorkError("batch_duplicate_target")
            seen.add(key)
        return actions

    @staticmethod
    def _check_edit_snapshot(action, snapshot):
        if action.expected_event and action.expected_event != encode(snapshot.get("event")):
            raise WorkError("target_changed_request_new_preview")
        if action.update_fields:
            before = snapshot["event"]
            if any(key not in action.update_fields and (before.get(key) or "") != value for key, value in (("summary", action.title), ("description", action.text))):
                raise WorkError("unchanged_event_fields_mismatch")
            if any(key not in action.update_fields and str(before.get(key, {}).get("timestamp")) != str(parse_time(value)) for key, value in (("start_time", action.start), ("end_time", action.end))):
                raise WorkError("unchanged_event_fields_mismatch")

    async def _batch_conflicts(self, p, actions):
        warnings, edited = [], {action.event_id for action in actions if action.event_id}
        for index, action in enumerate(actions):
            begin, end = parse_time(action.start), parse_time(action.end)
            for other in range(index):
                if begin < parse_time(actions[other].end) and parse_time(actions[other].start) < end:
                    warnings.append(f"时间提醒：第 {other + 1}、{index + 1} 条互相重叠。")
            events, complete = await self.query_events(p, action.start, action.end)
            count, unknown = 0, False
            for event in events:
                if event.get("event_id") in edited:
                    continue
                try:
                    event_start = int(event["start_time"]["timestamp"])
                    event_end = int(event["end_time"]["timestamp"])
                    count += event_start < end and begin < event_end
                except (KeyError, TypeError, ValueError):
                    unknown = True
            if count:
                warnings.append(f"冲突提醒：第 {index + 1} 条与现有 {count} 条日程重叠，仍需您决定是否执行。")
            if not complete or unknown:
                warnings.append(f"第 {index + 1} 条的冲突查询不完整，不能确认不存在其它安排。")
        if not warnings:
            warnings.append("预览查询未发现时间重叠；确认到执行期间仍可能出现新安排，这不是会议室或忙闲锁定。")
        return warnings

    async def prepare_batch(self, p: Principal, actions: list[Action], *, task_ref=None, summary="") -> Plan:
        self.prune()
        if not isinstance(actions, list) or any(not isinstance(action, Action) for action in actions):
            raise WorkError("batch_actions_required")
        batch = BatchPayload(items=[action.model_dump(mode="json") for action in actions], summary=summary)
        actions = self._batch_actions(batch)
        ref = await self._assert_task(p, task_ref)
        if sum(plan.stage == "pending" and plan.expires_at > time.monotonic() for plan in self.plans.values()) >= 64:
            raise WorkError("plan_capacity")
        snapshots = []
        for action in actions:
            await self.vault.require(p, ACTION_SCOPES[action.operation])
            snapshot = await self._snapshot(p, action)
            self._check_edit_snapshot(action, snapshot)
            snapshots.append(snapshot)
        warnings = await self._batch_conflicts(p, actions)
        await self._assert_task(p, ref)
        payload, snap, preview = batch.model_dump_json(), encode(snapshots), batch_preview(actions, warnings, snapshots)
        plan = Plan(secrets.token_urlsafe(16), p, payload, preview, snap, self._sign(p, payload, snap, preview, ref, 2), time.monotonic() + 300, task_ref=ref, protocol=2)
        self.plans[plan.id] = plan
        if self.store:
            try:
                await self.store.purge()
                owner = self.vault.principal_key(p)
                record = await self.store.admit("plan", plan.id, self._plan_data(plan), owner=owner, ttl=300, active={"waiting", "pending"}, limit=256, owner_limit=8, task_ref=ref)
                if not record:
                    raise WorkError("plan_capacity")
                changed = await self.store.change_task_record("plan", plan.id, record, owner=owner, task_ref=ref, status="pending") if ref else await self.store.change("plan", plan.id, record, status="pending")
                if not changed:
                    raise WorkError("task_superseded")
                await self._audit(plan, "preview")
            except BaseException:
                self.plans.pop(plan.id, None)
                raise
        return plan

    async def _confirm_batch(self, p, plan, record, ref):
        actions, snapshots = self._batch_actions(plan.payload_json), json.loads(plan.snapshot)
        if not isinstance(snapshots, list) or len(actions) != len(snapshots):
            raise WorkError("batch_snapshot_invalid")
        for action, snapshot in zip(actions, snapshots, strict=True):
            if action.operation == "event_update" and self.store:
                self.remember_events(p, [action.event_id])
            await self.vault.require(p, ACTION_SCOPES[action.operation])
            if encode(await self._snapshot(p, action)) != encode(snapshot):
                raise WorkError("target_changed_request_new_preview")
        items = [{"id": digest(plan.id + ":" + str(index)), "operation": action.operation, "title": action.title, "start": action.start, "end": action.end, "status": "pending", "receipt": ""} for index, action in enumerate(actions)]
        owner = self.vault.principal_key(p)
        if self.store:
            execution = {"id": plan.id, "operation": "calendar_batch", "batch": items, "protocol": 2}
            record = await self.store.claim_plan(plan.id, record, owner=owner, task_ref=ref, value=execution) if ref else await self.store.change("plan", plan.id, record, value=execution, status="executing", ttl=90 * 86400)
            if not record:
                raise WorkError("confirmation_already_claimed")
        else:
            await self._assert_task(p, ref)
        plan.stage, plan.batch_items = "executing", items
        try:
            for index, (action, snapshot) in enumerate(zip(actions, snapshots, strict=True)):
                item = items[index]
                try:
                    # Recheck the live target before each bounded item, then
                    # atomically fence its own execution admission in SQL.
                    await self._assert_task(p, ref)
                    if encode(await self._snapshot(p, action)) != encode(snapshot):
                        raise WorkError("target_changed_request_new_preview")
                    if self.store:
                        child = await self.store.claim_batch_item(item["id"], parent_id=plan.id, owner=owner, task_ref=ref, value={"operation": action.operation, "target": action.event_id or "personal"})
                        if child is None:
                            raise WorkError("batch_item_already_claimed")
                    else:
                        await self._assert_task(p, ref)
                except WorkError as exc:
                    item["reason"] = exc.stage
                    break  # No claim: this and remaining items are unexecuted.
                item["status"] = "executing"
                child_plan = Plan(item["id"], p, action.model_dump_json(), "", encode(snapshot), "", plan.expires_at, stage="executing", task_ref=ref, protocol=2, record_kind="batch_item", batch_group=plan.id)
                try:
                    await self._execute(p, action, snapshot, child_plan)
                    child_plan.stage = "completed"
                    item.update(status="completed", receipt=child_plan.receipt)
                except BaseException:
                    child_plan.stage = "uncertain"
                    item.update(status="uncertain", receipt=child_plan.receipt)
                    if self.store:
                        await self._save_execution(child_plan, "uncertain")
                    raise
                else:
                    if self.store:
                        await self._save_execution(child_plan, "completed")
                    else:
                        self.plans[child_plan.id] = child_plan
                finally:
                    if self.store:
                        await self._save_execution(plan)
        except BaseException:
            plan.stage = "uncertain" if any(item["status"] == "uncertain" for item in items) else "partial"
            raise
        else:
            plan.stage = "completed" if all(item["status"] == "completed" for item in items) else "partial"
            return outcome_text(items)
        finally:
            for item in items:
                if item["status"] == "pending":
                    item["status"] = "unexecuted"
            if self.store:
                await self._save_execution(plan, plan.stage)
                await self._audit(plan, plan.stage)

    async def recent_calendar_receipts(self, p: Principal) -> list[dict]:
        if not await self.vault.bound(p):
            raise WorkError("binding_changed")
        if self.store:
            records = await self.store.recent_calendar_records(self.vault.principal_key(p), limit=40)
        else:
            records = []
            for plan in reversed(list(self.plans.values())):
                if plan.principal == p and plan.stage == "completed":
                    raw = json.loads(plan.payload_json)
                    if raw.get("operation") in {"event_create", "event_update"}:
                        action = Action.model_validate_json(plan.payload_json)
                        records.append({"calendar_event": {**item_fact(action, event_id=plan.receipt, plan_id=plan.id, batch_group=plan.batch_group), "operation": action.operation, "status": "completed"}})
        result, seen = [], set()
        for value in records:
            event = value.get("calendar_event")
            if not isinstance(event, dict) and value.get("operation") in {"event_create", "event_update"} and isinstance(value.get("receipt"), str):
                event = {"event_id": value["receipt"], "title": "", "start": "", "end": "", "operation": value["operation"], "plan_id": value.get("id", ""), "batch_group": "", "status": "completed"}
            if isinstance(event, dict) and isinstance(event.get("event_id"), str) and event["event_id"] and event["event_id"] not in seen:
                seen.add(event["event_id"])
                result.append(dict(event))
            if len(result) >= 10:
                break
        return result

    async def calendar_task_execution(self, p: Principal, task_ref) -> dict:
        """Discover prior execution for this task, across continuation versions.

        A new generation fences writes, but does not erase the fact that this
        task already wrote. Explicitly starting another task creates a new ID.
        """
        ref = TaskRef.parse(task_ref)
        if ref is None:
            raise WorkError("task_reference_required")
        await self._assert_task(p, ref)
        if not await self.vault.bound(p):
            raise WorkError("binding_changed")
        owner = self.vault.principal_key(p)
        indexed_started = None
        if self.store:
            records, complete, indexed_started = await self.store.calendar_execution_records(owner, task_ref=ref)
        else:
            records, complete = [], True
            for plan in reversed(list(self.plans.values())):
                if plan.principal != p or plan.record_kind != "plan" or plan.stage not in {"executing", "completed", "partial", "uncertain"}:
                    continue
                raw = json.loads(plan.payload_json)
                value = {"id": plan.id, "operation": raw.get("operation"), "receipt": plan.receipt, "task_ref": plan.task_ref.as_dict() if plan.task_ref else None}
                if plan.batch_items is not None:
                    value["batch"] = plan.batch_items
                elif plan.stage == "completed" and raw.get("operation") in {"event_create", "event_update"}:
                    action = Action.model_validate_json(plan.payload_json)
                    value["calendar_event"] = {**item_fact(action, event_id=plan.receipt, plan_id=plan.id), "operation": action.operation, "status": "completed"}
                records.append({"status": plan.stage, "value": value})
                if len(records) > 100:
                    records, complete = records[:100], False
                    break
        plans, events, statuses, seen = [], [], [], set()

        def add_event(value):
            event = value.get("calendar_event")
            if not isinstance(event, dict) and value.get("operation") in {"event_create", "event_update"} and value.get("receipt"):
                event = {"event_id": value["receipt"], "title": "", "start": "", "end": "", "operation": value["operation"], "plan_id": value.get("id", ""), "batch_group": value.get("parent_id", ""), "status": "completed"}
            if isinstance(event, dict) and isinstance(event.get("event_id"), str) and 0 < len(event["event_id"]) <= 256 and event["event_id"] not in seen and len(events) < 10:
                seen.add(event["event_id"])
                events.append(dict(event))

        for row in records:
            value, status = row["value"], row["status"]
            previous = TaskRef.parse(value.get("task_ref"))
            if previous is None or previous.task_id != ref.task_id or value.get("operation") not in {"event_create", "event_update", "calendar_batch"}:
                continue
            if len(plans) >= 10:
                complete = False
                break
            plans.append({"plan_id": value["id"], "status": status, "operation": value["operation"]})
            statuses.append(status)
            if status == "completed" and value["operation"] != "calendar_batch":
                add_event(value)
            if value["operation"] == "calendar_batch":
                for item in value.get("batch", [])[:10]:
                    if self.store:
                        child = await self.store.read("batch_item", item["id"], owner=owner)
                        if child is not None:
                            statuses.append(child.status)
                            if child.status == "completed":
                                add_event(child.value)
                    else:
                        child = self.plans.get(item["id"])
                        if child and child.principal == p and child.stage == "completed":
                            action = Action.model_validate_json(child.payload_json)
                            add_event({"calendar_event": {**item_fact(action, event_id=child.receipt, plan_id=child.id, batch_group=value["id"]), "operation": action.operation, "status": "completed"}})
        await self._assert_task(p, ref)
        if not await self.vault.bound(p):
            raise WorkError("binding_changed")
        state = "uncertain" if "uncertain" in statuses else "started" if "executing" in statuses else "partial" if "partial" in statuses else "completed" if statuses else "none"
        if (not complete and state in {"none", "completed"}) or indexed_started is True and not statuses:
            state = "unknown"
        return {"state": state, "started": bool(plans) or indexed_started is True, "complete": complete, "plans": plans, "events": events}

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
        # SQL's owner-scoped lease and atomic claims serialize one principal;
        # unrelated users must not wait behind another person's batch HTTP.
        async with nullcontext() if self.store else self._write_lock:
            record = None
            if self.store:
                record = await self.store.read("plan", plan_id, owner=self.vault.principal_key(p))
                if record and record.status != "pending":
                    if self.task_fencing and record.status == "cancelled" and record.value.get("task_ref"):
                        raise WorkError("task_superseded")
                    raise WorkError("confirmation_already_claimed_or_expired")
                if record:
                    plan = Plan(**{**record.value, "task_ref": TaskRef.parse(record.value.get("task_ref")), "principal": p, "expires_at": time.monotonic() + record.expires_at - time.time(), "stage": record.status})
                    self.plans[plan_id] = plan
                else:
                    plan = None
            else:
                plan = self.plans.get(plan_id)
            if not plan or plan.principal != p or plan.stage != "pending" or plan.expires_at <= time.monotonic():
                raise WorkError("confirmation_invalid_or_expired")
            ref = await self._assert_task(p, plan.task_ref)
            if self.task_fencing and plan.protocol != 2:
                raise WorkError("plan_protocol_upgrade_required")
            if not hmac.compare_digest(plan.signature, self._sign(p, plan.payload_json, plan.snapshot, plan.preview, ref, plan.protocol)):
                plan.stage = "rejected"
                raise WorkError("plan_changed")
            if json.loads(plan.payload_json).get("operation") == "calendar_batch":
                return await self._confirm_batch(p, plan, record, ref)
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
                execution = {"id": plan.id, "operation": action.operation, "target": action.document_id or action.event_id or "personal"}
                record = (
                    await self.store.claim_plan(plan_id, record, owner=self.vault.principal_key(p), task_ref=ref, value=execution)
                    if ref
                    else await self.store.change("plan", plan_id, record, value=execution, status="executing", ttl=90 * 86400)
                )
                if not record:
                    raise WorkError("confirmation_already_claimed")
            else:
                # No await between the final in-memory guard and the execution
                # claim: another local turn cannot invalidate it in between.
                await self._assert_task(p, ref)
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
        data = {name: getattr(plan, name) for name in ("id", "payload_json", "preview", "snapshot", "signature", "receipt")}
        data["protocol"] = plan.protocol
        if plan.task_ref is not None:
            data["task_ref"] = TaskRef.parse(plan.task_ref).as_dict()
        return data

    async def _audit(self, plan, stage):
        if self.store:
            if json.loads(plan.payload_json).get("operation") == "calendar_batch":
                await self.store.audit(plan.principal, "calendar_batch", "personal", stage, plan_id=plan.id, receipt=plan.receipt)
                return
            action = Action.model_validate_json(plan.payload_json)
            await self.store.audit(plan.principal, action.operation, action.document_id or action.event_id or "personal", stage, plan_id=plan.id, receipt=plan.receipt)

    async def _save_execution(self, plan, status="executing"):
        if self.store:
            kind, owner = plan.record_kind, self.vault.principal_key(plan.principal)
            record = await self.store.read(kind, plan.id, owner=owner)
            value = {**record.value, "receipt": plan.receipt} if record else {}
            if plan.batch_items is not None:
                value["batch"] = plan.batch_items
            raw = json.loads(plan.payload_json)
            if status == "completed" and raw.get("operation") in {"event_create", "event_update"}:
                action = Action.model_validate_json(plan.payload_json)
                value["calendar_event"] = {**item_fact(action, event_id=plan.receipt, plan_id=plan.id, batch_group=value.get("parent_id", "")), "operation": action.operation, "status": "completed"}
            if not record or record.status != "executing" or not await self.store.change(kind, plan.id, record, value=value, status=status, ttl=90 * 86400):
                raise WorkError("execution_record_unavailable")

    async def cancel_shared(self, p, plan_id):
        if not self.store:
            plan = self.plans.get(plan_id)
            if plan is not None and plan.principal == p:
                await self._assert_task(p, plan.task_ref)
            return self.cancel(p, plan_id)
        record = await self.store.read("plan", plan_id, owner=self.vault.principal_key(p))
        if not record or record.status != "pending":
            raise WorkError("confirmation_invalid_or_expired")
        ref = await self._assert_task(p, record.value.get("task_ref"))
        changed = (
            await self.store.change_task_record("plan", plan_id, record, owner=self.vault.principal_key(p), task_ref=ref, status="cancelled", ttl=900) if ref else await self.store.change("plan", plan_id, record, status="cancelled", ttl=900)
        )
        if not changed:
            raise WorkError("confirmation_invalid_or_expired")

    async def terminal_notice(self, p, plan_id):
        if not self.store:
            plan = self.plans.get(plan_id)
            if plan and plan.principal == p and plan.batch_items is not None and plan.stage in {"executing", "uncertain", "completed", "partial"}:
                return outcome_text(plan.batch_items)
            return ""
        record = await self.store.read("plan", plan_id, owner=self.vault.principal_key(p))
        if not record or record.status not in {"executing", "uncertain", "completed", "partial"}:
            return ""
        if record.value.get("operation") == "calendar_batch":
            items = [dict(item) for item in record.value.get("batch", [])]
            for item in items:
                child = await self.store.read("batch_item", item["id"], owner=self.vault.principal_key(p))
                item.update(status=child.status if child else "unexecuted", receipt=child.value.get("receipt", "") if child else "")
            return outcome_text(items)
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

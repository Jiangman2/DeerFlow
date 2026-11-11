"""Default-off Work orchestration, shared by production SQL and local UAT."""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from datetime import datetime
from urllib.parse import urlencode

from app.channels.message_bus import InboundMessage, OutboundMessage

from .agent import plan_turn, propose_action, summarize_document
from .auth import Grant, Principal, TokenVault
from .calendar import TZ, CalendarContext, CalendarIntent, CalendarResolver
from .calendar_workflow import resolve_draft
from .capabilities import Action, Plan, WorkCapabilities
from .cards import CARD_EVENT_RECORD, CARD_PREVIEW_KEY, CARD_PREVIEW_RECORD, CARD_REPLY_TARGET_KEY, CardPreview, CardReplyTarget, CardSettings
from .client import TOKEN_RE, UAT_APP_ID, WORK_API, WorkAPI, WorkError, document_links
from .config import WorkSettings
from .date_grounding import ground_write_dates
from .drafts import CalendarDraft, draft_summary, missing_question
from .file_read import FILE_JOB_PROTOCOL, FILE_PLAN_TARGET, WorkFileRef, WorkFileSettings, file_error_explanation, file_links, read_file, requested_range
from .file_search import LEGACY_SEARCH_SCOPE, SEARCH_JOB_PROTOCOL, SEARCH_SCOPE, WorkSearchSettings, is_exact_title, search_documents
from .routing import FileLookupIntent, WorkTurnDecision, lookup_body, validate_file_lookup
from .scopes import CALENDAR_AUTH_SCOPE, CALENDAR_CONSENT_DESCRIPTION, DOCUMENT_SCOPE, FILE_GRANT_KINDS, SEARCH_GRANT_KIND, has_scopes
from .tasks import TaskRef
from .turn_state import MemoryTurns

logger = logging.getLogger(__name__)
MAX_JOBS = 32
TURN_WATCHDOG_GRACE_SECONDS = 1
_TURN_DIAGNOSTIC_ERRORS = frozenset({"turn_queue_busy", "turn_model_timeout", "turn_model_invalid", "turn_model_unavailable", "turn_request_empty", "turn_request_too_long", "turn_timeout_configuration"})
_SEARCH_CHOICE_TTL = 300
_SEARCH_CHOICE_LIMIT = 5


async def _run_turn_planner(msg, *, settings, semaphore, planner, **kwargs):
    """Bound queue admission separately from the planner's execution deadline.

    plan_turn owns the authoritative model deadline. The one-second service
    watchdog only fences an injected or stuck planner; it never adds a retry.
    Diagnostics contain a hashed provider message identity and fixed labels.
    """
    source = msg.thread_ts or msg.metadata.get("message_id") or ""
    identity = [msg.channel_name, msg.owner_user_id, msg.connection_id, msg.workspace_id, msg.chat_id, msg.user_id, source]
    message_ref = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()[:16]
    started, queue_ms, acquired = time.monotonic(), 0, False

    def diagnostic(stage, error="none"):
        logger.info("work_turn_diagnostic message_ref=%s stage=%s elapsed_ms=%d queue_ms=%d error=%s", message_ref, stage, round((time.monotonic() - started) * 1000), queue_ms, error)

    diagnostic("queued")
    try:
        try:
            async with asyncio.timeout(settings.turn_queue_timeout):
                await semaphore.acquire()
        except TimeoutError:
            queue_ms = round((time.monotonic() - started) * 1000)
            raise WorkError("turn_queue_busy") from None
        acquired = True
        queue_ms = round((time.monotonic() - started) * 1000)
        diagnostic("model_start")
        # Start only after admission: queue time cannot consume model time.
        async with asyncio.timeout(settings.turn_timeout + TURN_WATCHDOG_GRACE_SECONDS):
            decision = await planner(msg.text, timeout=settings.turn_timeout, **kwargs)
        if not isinstance(decision, WorkTurnDecision):
            raise WorkError("turn_model_invalid")
        diagnostic("done")
        return decision
    except TimeoutError:
        diagnostic("failure", "turn_model_timeout")
        raise WorkError("turn_model_timeout") from None
    except WorkError as exc:
        diagnostic("failure", exc.stage if exc.stage in _TURN_DIAGNOSTIC_ERRORS else "turn_model_unavailable")
        raise
    except asyncio.CancelledError:
        diagnostic("cancelled")
        raise
    except Exception:
        diagnostic("failure", "turn_model_unavailable")
        raise WorkError("turn_model_unavailable") from None
    finally:
        if acquired:
            semaphore.release()


@dataclass
class DocumentJob:
    message: InboundMessage = field(repr=False)
    document_id: str
    document_url: str
    expires_at: float
    scope: str = DOCUMENT_SCOPE
    stage: str = "awaiting_authorization"
    state: str = field(default="", repr=False)
    browser_session: str = field(default="", repr=False)
    intent: dict | None = field(default=None, repr=False)
    task_ref: dict | None = field(default=None, repr=False)
    protocol: int = 2
    calendar_preview: Plan | None = field(default=None, init=False, repr=False)


@dataclass
class CalendarDialogue:
    expires_at: float
    turns: list[dict] = field(default_factory=list, repr=False)


@dataclass(frozen=True, repr=False)
class _SearchHandoff:
    kind: str
    token: str
    url: str


@dataclass(frozen=True, repr=False)
class _SearchSelection:
    text: str
    choices: dict


def _job_protocol(intent):
    if isinstance(intent, dict) and intent.get("operation") == "file_lookup":
        return SEARCH_JOB_PROTOCOL
    return FILE_JOB_PROTOCOL if WorkFileRef.from_intent(intent) else 2


class WorkDocumentService:
    def __init__(self, *, app_id, app_secret, allowed_owners, repo, send, api=None, summarize=None, planner=None, turn_planner=None, runtime_guard=None, settings=None, store=None):
        self.settings = settings or WorkSettings.local_uat()
        if not app_id or not app_secret or self.settings.profile == "uat" and (not allowed_owners or settings is None and app_id != UAT_APP_ID) or self.settings.profile == "production" and (app_id == UAT_APP_ID or not store):
            raise ValueError("Local integration requires the UAT app and explicit test owners")
        self.app_id, self._secret = app_id, app_secret
        self.allowed_owners, self.repo, self.send = frozenset(allowed_owners) if allowed_owners is not None else None, repo, send
        self.store = store
        self._api, self._owns_api = api or WorkAPI(api_url=self.settings.api_url), api is None
        self._summarize, self._planner = summarize or summarize_document, planner or propose_action
        self._turn_planner = turn_planner or plan_turn
        self._turn_state = store or MemoryTurns()
        self.vault = TokenVault(app_id, repo, allowed_owners, runtime_guard, store=store)
        self._file_settings = WorkFileSettings.from_env(self.settings.profile)
        self._file_vaults = {kind: TokenVault(app_id, repo, allowed_owners, runtime_guard, store=store, grant_kind=grant_kind) for kind, grant_kind in FILE_GRANT_KINDS.items()}
        self._search_settings = WorkSearchSettings.from_env(self.settings.profile)
        self._search_vault = TokenVault(app_id, repo, allowed_owners, runtime_guard, store=store, grant_kind=SEARCH_GRANT_KIND)
        self.tools = WorkCapabilities(self.vault, self._api, document_host=self.settings.document_host, task_fencing=True, task_guard=self._assert_task)
        self.calendar = CalendarResolver(self.tools)
        self.jobs: OrderedDict[str, DocumentJob] = OrderedDict()
        self.grants = self.vault.grants
        self._states: dict[str, str] = {}
        self._seen: dict[tuple, str] = {}
        self._contexts: dict[Principal, tuple[float, str]] = {}
        self._calendar_dialogues: dict[Principal, CalendarDialogue] = {}
        self._workflow_feedback: dict[Principal, tuple[float, dict]] = {}
        self._verified_calendar_days: dict[Principal, tuple[float, list[str]]] = {}
        self._principal_locks: dict[Principal, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()
        self._card_tasks: set[asyncio.Task] = set()
        self._missing_card_tenant_logged = False
        self._job_tasks: dict[int, asyncio.Task] = {}
        self._semaphore = asyncio.Semaphore(self.settings.concurrency)
        self._turn_semaphore = asyncio.Semaphore(self.settings.concurrency)
        self._closed = False

    def _identity(self, msg):
        return Principal.from_message(self.app_id, msg)

    async def _bound(self, msg):
        try:
            return await self.vault.bound(self._identity(msg))
        except WorkError:
            return False

    def _prune(self):
        now = time.monotonic()
        for ticket, job in list(self.jobs.items()):
            if job.expires_at < now and job.stage != "processing":
                self.jobs.pop(ticket)
                self._states.pop(job.state, None)
        self._seen = {key: value for key, value in self._seen.items() if value in self.jobs}
        self._contexts = {key: value for key, value in self._contexts.items() if value[0] > now}
        self._calendar_dialogues = {key: value for key, value in self._calendar_dialogues.items() if value.expires_at > now}
        self._workflow_feedback = {key: value for key, value in self._workflow_feedback.items() if value[0] > now}
        self._verified_calendar_days = {key: value for key, value in self._verified_calendar_days.items() if value[0] > now}
        self.calendar.prune()
        self._principal_locks = {p: lock for p, lock in self._principal_locks.items() if lock.locked() or any(self._identity(j.message) == p and j.stage == "processing" for j in self.jobs.values())}
        self.vault.prune()
        for vault in self._special_grant_vaults():
            vault.prune()
        self.tools.prune()

    def _special_grant_vaults(self):
        return (*self._file_vaults.values(), self._search_vault)

    def _reply_text(self, text):
        if self.settings.profile == "production":
            substitutions = {
                "【本地 UAT】": "【海能 Work】",
                "同一 UAT 账号": "同一 Work 账号",
                "UAT 账号": "Work 账号",
                "UAT 机器人": "Work 机器人",
                "UAT 应用": "当前应用",
                "本机浏览器": "浏览器",
                "本地测试范围": "允许使用的范围",
                "本地仅开放": "当前仅开放",
                "本地个人授权": "平台内个人授权",
            }
            for old, new in substitutions.items():
                text = text.replace(old, new)
        for secret in [self._secret, *(g.token for vault in (self.vault, *self._special_grant_vaults()) for g in vault.grants.values())]:
            text = text.replace(secret, "[已隐藏]")
        return text

    async def _reply(self, msg, text, *, final=True, card_preview=None):
        metadata = {"user_id": msg.user_id, "message_id": msg.thread_ts}
        target = msg.metadata.get(CARD_REPLY_TARGET_KEY)
        if isinstance(target, CardReplyTarget):
            metadata[CARD_REPLY_TARGET_KEY] = target
        if isinstance(card_preview, CardPreview) and final:
            metadata[CARD_PREVIEW_KEY] = replace(card_preview, text=self._reply_text(card_preview.text))
        await self.send(
            OutboundMessage(
                channel_name="feishu",
                chat_id=msg.chat_id,
                thread_id="",
                text=self._reply_text(text),
                is_final=final,
                thread_ts=msg.thread_ts,
                connection_id=msg.connection_id,
                owner_user_id=msg.owner_user_id,
                metadata=metadata,
            )
        )

    async def _assert_task(self, p, ref):
        await self._turn_state.assert_task(self.vault.principal_key(p), TaskRef.parse(ref))

    def _context_data(self, p):
        context = self.calendar.contexts.get(p)
        dialogue = self._calendar_dialogues.get(p)
        return {
            "document": self._contexts.get(p, (0, ""))[1],
            "dialogue": dialogue.turns if dialogue else [],
            "calendar": {"candidates": context.candidates, "recent_id": context.recent_id} if context else {},
            "workflow_feedback": self._workflow_feedback.get(p, (0, {}))[1],
            "verified_calendar_days": self._verified_calendar_days.get(p, (0, []))[1],
        }

    def _hydrate_context(self, p, value):
        self._contexts.pop(p, None)
        self._calendar_dialogues.pop(p, None)
        self.calendar.contexts.pop(p, None)
        self.tools.known_events.pop(p, None)
        self._workflow_feedback.pop(p, None)
        self._verified_calendar_days.pop(p, None)
        expires = time.monotonic() + 600
        if value.get("document"):
            self._contexts[p] = (expires, value["document"])
        if value.get("dialogue"):
            self._calendar_dialogues[p] = CalendarDialogue(expires, value["dialogue"])
        if value.get("calendar"):
            data = value["calendar"]
            self.calendar.contexts[p] = CalendarContext(expires, data.get("candidates", []), data.get("recent_id", ""))
            self.tools.remember_events(p, [item["event_id"] for item in data.get("candidates", [])])
        if isinstance(value.get("workflow_feedback"), dict) and value["workflow_feedback"]:
            self._workflow_feedback[p] = (expires, value["workflow_feedback"])
        if isinstance(value.get("verified_calendar_days"), list):
            days = [day for day in value["verified_calendar_days"][:31] if isinstance(day, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day)]
            self._verified_calendar_days[p] = (expires, days)

    async def _sync_task_context(self, job, p):
        ref = TaskRef.parse(job.task_ref)
        await self._assert_task(p, ref)
        record = await self._turn_state.read_task_context(self.vault.principal_key(p), ref)
        self._hydrate_context(p, record.value if record else {})

    async def _save_task_context(self, p, ref):
        await self._turn_state.save_task_context(self.vault.principal_key(p), ref, self._context_data(p))

    def _cancel_local_pending(self, p):
        for job in self.jobs.values():
            if self._identity(job.message) == p and job.stage in {"awaiting_authorization", "authorizing", "processing"}:
                job.stage = "cancelled"
                self._states.pop(job.state, None)
                task = self._job_tasks.get(id(job))
                if task:
                    task.cancel()
        for plan in self.tools.plans.values():
            if plan.principal == p and plan.stage == "pending":
                plan.stage = "cancelled"
        self._hydrate_context(p, {})

    async def handle(self, msg):
        if self._closed or msg.channel_name != "feishu":
            return False
        received_at = datetime.now(TZ)
        self._prune()
        links = document_links(msg.text, self.settings.document_host)
        resources = file_links(msg.text, self.settings.document_host)
        explicit = bool(re.match(r"^/(?:work|doc_skill)(?:\s|$)", msg.text.strip()))
        try:
            p = self._identity(msg)
        except WorkError:
            if links or resources or explicit or msg.metadata.get("chat_type") == "p2p":
                await self._reply(msg, "【本地 UAT】当前消息身份尚未核对，未访问个人数据。请检查渠道连接，并在本人 Work 私聊中继续。")
                return True
            return False
        source = msg.thread_ts or msg.metadata.get("message_id")
        if not source:
            await self._reply(msg, "【本地 UAT】本次消息标识不完整，请重新发送请求；尚未执行操作。")
            return True
        owner = self.vault.principal_key(p)
        try:
            turn = await self._turn_state.reserve_turn(owner, source)
            if turn is None:
                return True
            self._hydrate_context(p, turn.context)
            control = re.fullmatch(r"/work\s+(确认|取消)\s+([A-Za-z0-9_-]{22})", msg.text.strip())
            help_text = msg.text.strip().strip('“”"')
            if control or help_text in {"/work", "/work 帮助"} or msg.text.strip() in {"/work 撤销授权", "/work 退出授权"}:
                if not await self._bound(msg):
                    await self._reply(msg, "【本地 UAT】渠道绑定已变更或当前账号不在允许范围，请检查连接后继续。")
                    return True
                if control:
                    await self._control(msg, p, *control.groups())
                elif help_text in {"/work", "/work 帮助"}:
                    search_help = "也可以按标题查找本人有权访问的 Work 云文件，同名时会请您选择。" if self._search_settings.scope else ""
                    await self._reply(
                        msg,
                        "【本地 UAT】可以读取/总结云文档，新建空白文档、改名、追加文字或替换完整无格式段落；也可以查询、新建和编辑个人主日历日程。"
                        "直接说明需求即可，缺少信息时会补问，缺少授权时会给出授权链接。写入先预览，再回复 /work 确认 确认码；"
                        "取消预览用 /work 取消 确认码，撤销个人授权用 /work 撤销授权。暂不支持删除、分享、邀请和会议室预约。" + search_help,
                    )
                else:
                    await self._turn_state.finish_turn(owner, turn.turn_id, mode="exit")
                    self._cancel_local_pending(p)
                    await self.vault.revoke_shared(p)
                    for vault in self._special_grant_vaults():
                        await vault.revoke_shared(p)
                    await self._reply(msg, "【本地 UAT】个人授权及待确认操作已清除。Work 侧撤销请到应用授权管理操作。")
                return True
            if msg.files:
                # Attachments retain the ordinary platform's upload lifecycle.
                await self._turn_state.finish_turn(owner, turn.turn_id, mode="exit")
                self._cancel_local_pending(p)
                if explicit or links or resources:
                    await self._reply(msg, "【本地 UAT】请将 Work 文档链接与上传附件分开发送。上传附件可通过普通对话处理，本次未访问或修改 Work 数据。")
                    return True
                return False
            if resources:
                return await self._handle_file_turn(msg, p, owner, source, turn, resources, links)
            choice_text = msg.text.strip()
            number = re.fullmatch(r"[0-9]", choice_text)
            choice_command = re.match(r"^/work\s+选择(?:\s|$)", choice_text)
            if not links and (number or choice_command) and turn.task.get("domain") == "file_search_read":
                await self._reply(msg, "【本地 UAT】上次选择已受理，请查看原卡片或读取结果。需要重选时，请重新搜索。")
                return True
            numbered_choice = number and turn.context.get("file_search_selection") and turn.task.get("domain") == "file_lookup"
            if not links and (numbered_choice or choice_command):
                return await self._handle_search_choice(msg, p, owner, source, turn)
            task_context = {}
            pending_lookup = (
                turn.task_ref is not None
                and turn.task.get("domain") == "file_lookup"
                and turn.task.get("stage") == "awaiting_input"
                and type(turn.task.get("lookup_expires_at")) in {int, float}
                and turn.task["lookup_expires_at"] > time.time()
                and bool(turn.context)
            )
            if pending_lookup:
                task_context = dict(turn.task)
            elif turn.task_ref is not None and turn.task.get("domain") not in {"file_lookup", "file_search_read"}:
                task_context = dict(turn.task)
                task_context.update(self.calendar.facts(p))
                task_context["pending_dialogue"] = turn.context.get("dialogue", [])
                task_context["document_id"] = turn.context.get("document", task_context.get("document_id", ""))
                known = self.tools.known_events.get(p, (0, set()))
                task_context["known_event_ids"] = sorted(known[1]) if known[0] > time.monotonic() else []
                task_context["workflow_feedback"] = turn.context.get("workflow_feedback", {})
                if turn.task.get("domain") == "calendar":
                    execution = await self.tools.calendar_task_execution(p, turn.task_ref)
                    task_context["calendar_execution"] = {key: execution[key] for key in ("state", "started", "complete", "events")}
            decision = await _run_turn_planner(msg, settings=self.settings, semaphore=self._turn_semaphore, planner=self._turn_planner, links=links, task_context=task_context)
            if isinstance(decision.intent, FileLookupIntent):
                if decision.route != "new_work" or links:
                    raise WorkError("invalid_search_request")
                return await self._handle_search_turn(msg, p, owner, source, turn, decision.intent, evidence=decision.lookup_evidence, continuing=decision.lookup_continuation and pending_lookup)
            execution = task_context.get("calendar_execution", {})
            blocked_recreate = decision.draft is not None and decision.draft.operation == "create" and execution.get("state", "none") != "none" and not decision.calendar_task_reset_validated
            if blocked_recreate:
                # A retained draft is not permission to recreate already-started
                # work. Read the durable plan history even if a previous worker
                # exited before saving its conversational completion feedback.
                question = (
                    "之前的日程操作已有执行记录，不能把原新建草稿再次提交。您是要修改已创建的日程，还是另外新建？请说明要修改哪一条或明确另建；已执行结果会保留。"
                    if execution["state"] == "completed"
                    else "之前的日程操作已开始、部分完成或结果尚待核查，本次不会重复创建。请先查询核对已有日程，再说明要修改哪一条或明确另外新建。"
                )
                decision = decision.model_copy(update={"route": "clarify", "question": question})
            mode = "continue" if turn.task_ref and not decision.calendar_task_reset_validated and (decision.route == "continue_work" or decision.route == "clarify" and decision.domain == turn.task.get("domain")) else "new"
            if decision.lookup is not None:
                mode = "continue" if decision.lookup_continuation and pending_lookup else "new"
            prior_operation = (turn.task.get("draft") or {}).get("operation") or (turn.task.get("intent") or {}).get("operation")
            if (
                turn.task_ref
                and turn.task.get("domain") == "calendar"
                and decision.draft is not None
                and decision.draft.operation == "create"
                and prior_operation in {"create", "update", "repair", "calendar_create", "calendar_update"}
                and not decision.calendar_task_reset_validated
            ):
                # A label cannot bypass execution history by issuing a new ID
                # for a retained write draft. Only this turn's reset tool can.
                mode = "continue"
            grounded = None
            if decision.draft is not None and decision.draft.operation in {"create", "update", "repair"} and not blocked_recreate:
                now = received_at
                sources = [{"text": msg.text, "received_at": now}]
                if mode == "continue":
                    sources.extend({"text": row["user"], "received_at": None} for row in turn.context.get("dialogue", []) if isinstance(row, dict) and isinstance(row.get("user"), str))
                    sources.append({"text": turn.task.get("original_request", ""), "received_at": None})
                grounded = ground_write_dates(
                    CalendarDraft.model_validate(turn.task.get("draft") or {}) if mode == "continue" else CalendarDraft(),
                    decision.draft,
                    sources,
                    now,
                    {
                        "same_task": mode == "continue",
                        "verified_days": turn.context.get("verified_calendar_days", []) if mode == "continue" else [],
                        "candidates": task_context.get("candidates", []) if mode == "continue" else [],
                        "receipt_events": execution.get("events", []) if mode == "continue" else [],
                    },
                )
                decision = decision.model_copy(update={"draft": grounded.draft, **({"route": "clarify", "question": grounded.question} if grounded.question else {})})
            if decision.draft is not None and decision.route in {"new_work", "continue_work", "clarify"}:
                # The model proposes explicit slot updates. The canonical draft
                # is checked again here before any OAuth or provider access.
                question = missing_question(decision.draft)
                if question and not blocked_recreate and not (grounded is not None and grounded.question):
                    previous = turn.task.get("question") or ""
                    if previous.endswith(question):
                        facts = draft_summary(decision.draft)
                        known = []
                        if facts["title"]:
                            try:
                                safe_title = WorkTurnDecision.bounded_question(facts["title"][:120])
                            except ValueError:
                                pass
                            else:
                                known.append("标题“" + safe_title + "”")
                        if facts["dates"]:
                            known.append("日期" + "、".join(facts["dates"][:10]))
                        if facts["start_clock"]:
                            known.append("每天" + facts["start_clock"] + "开始")
                        if facts["duration_minutes"] is not None:
                            known.append("每条" + str(facts["duration_minutes"]) + "分钟")
                        if known:
                            question = "已保留：" + "；".join(known) + "。" + question
                    question = WorkTurnDecision.bounded_question(question)
                    decision = decision.model_copy(update={"route": "clarify", "question": question})
            logger.info("work_turn route=%s", decision.route)
            if decision.route in {"general", "cancel_current", "unsupported"}:
                await self._turn_state.finish_turn(owner, turn.turn_id, mode="exit")
                had_preview = any(plan.principal == p and plan.stage == "pending" for plan in self.tools.plans.values())
                self._cancel_local_pending(p)
                if decision.route == "general":
                    if had_preview:
                        await self._reply(msg, "【本地 UAT】已切换任务，上一条尚未执行的修改预览已失效。", final=False)
                    return False
                text = (
                    "已结束本次请求，未开始新的写入；已确认开始的操作会单独返回回执。有效个人授权和渠道连接保留。"
                    if decision.route == "cancel_current"
                    else "当前开放个人日程查询、新建、编辑，以及云文档读取、总结和受限编辑；删除、分享、邀请和会议室操作尚未开放。可以继续说明您要查询或编辑的对象、日期和具体要求。"
                )
                await self._reply(msg, "【本地 UAT】" + text)
                return True
            if not await self._bound(msg):
                await self._turn_state.finish_turn(owner, turn.turn_id, mode="exit")
                self._cancel_local_pending(p)
                await self._reply(msg, "【本地 UAT】当前账号不在允许范围或渠道绑定已变更，未访问个人数据。请检查渠道连接后继续。")
                return True
            if len(links) > 1:
                await self._turn_state.finish_turn(owner, turn.turn_id, mode="exit")
                self._cancel_local_pending(p)
                await self._reply(msg, "【本地 UAT】请一次处理一篇文档，以便核对目标。")
                return True
            # The validated semantic proposal is reused after consent, never
            # reinterpreted by a second planner or reconstructed from a URL.
            snapshot = decision.model_dump(mode="json")
            task_data = {
                "protocol": 2,
                "domain": decision.domain,
                "intent": snapshot["intent"],
                "draft": snapshot.get("draft"),
                "lookup": snapshot.get("lookup"),
                "question": decision.question,
                "stage": "awaiting_input" if decision.route == "clarify" else "requested",
                "original_request": turn.task.get("original_request", msg.text) if mode == "continue" else msg.text,
            }
            if decision.lookup is not None:
                rounds = turn.task.get("lookup_rounds", 0) if mode == "continue" else 0
                if type(rounds) is not int or rounds >= 4:
                    await self._turn_state.finish_turn(owner, turn.turn_id, mode="exit")
                    self._cancel_local_pending(p)
                    await self._reply(msg, "【本地 UAT】这次文件信息尚未核对完整。请重新发送来源、完整标题和希望读取还是查找；尚未访问 Work 数据。")
                    return True
                task_data.update(lookup_evidence=decision.lookup_evidence, lookup_rounds=rounds + 1, lookup_expires_at=turn.task["lookup_expires_at"] if mode == "continue" else time.time() + 600)
            task_data["document_id"] = links[0][0] if links else turn.context.get("document", "") if mode == "continue" else ""
            ref = await self._turn_state.finish_turn(owner, turn.turn_id, mode=mode, task=task_data)
            self._cancel_local_pending(p)
            if mode == "continue":
                current_context = await self._turn_state.read_task_context(owner, ref)
                self._hydrate_context(p, current_context.value if current_context else {})
            if grounded is not None:
                self._verified_calendar_days[p] = (time.monotonic() + 600, grounded.verified_days)
            if links:
                self._contexts[p] = (time.monotonic() + 600, links[0][0])
            if decision.route == "clarify":
                self._remember_question(p, msg.text, decision.question)
                await self._save_task_context(p, ref)
                await self._assert_task(p, ref)
                await self._reply(msg, "【本地 UAT】" + decision.question)
                return True
            if decision.draft is not None and decision.draft.operation == "create" and mode == "continue":
                # A different worker could claim the old preview while this
                # turn awaited its model. Rotating the generation and then
                # reading the shared index closes that admission race.
                latest = await self.tools.calendar_task_execution(p, ref)
                if latest["state"] != "none":
                    question = "之前的日程操作已有执行记录，本次不会重复创建。请先查询核对已有日程，再说明要修改哪一条或明确另外新建。"
                    self._remember_question(p, msg.text, question)
                    await self._save_task_context(p, ref)
                    await self._assert_task(p, ref)
                    await self._reply(msg, "【本地 UAT】" + question)
                    return True
            calendar = decision.domain == "calendar"
            doc = "" if calendar else decision.intent.document_id
            allowed_doc = {link[0] for link in links}
            if not links and mode == "continue" and turn.context.get("document"):
                allowed_doc.add(turn.context["document"])
            if doc and doc not in allowed_doc:
                raise WorkError("model_changed_document_target")
            await self._save_task_context(p, ref)
            scope = CALENDAR_AUTH_SCOPE if calendar else DOCUMENT_SCOPE
            job = DocumentJob(replace(msg, metadata=dict(msg.metadata), files=[]), doc, self.tools.doc_url(doc) if doc else "", time.monotonic() + 600, scope, intent=decision.model_dump(mode="json"), task_ref=ref.as_dict())
            consent = f"本次申请日历读取和更新权限：{CALENDAR_AUTH_SCOPE}。\n当前开放：{CALENDAR_CONSENT_DESCRIPTION}。\n\n" if calendar else ""
            await self._submit_job(job, p, source, consent=consent)
            return True
        except WorkError as exc:
            if exc.stage != "task_superseded":
                kept_draft = False
                if "turn" in locals() and turn is not None:
                    preserve = (exc.stage.startswith("turn_model_") or exc.stage == "turn_queue_busy") and turn.task_ref is not None and (bool(turn.task.get("draft")) or locals().get("pending_lookup", False))
                    try:
                        await self._turn_state.finish_turn(owner, turn.turn_id, mode="continue" if preserve else "exit", task=turn.task if preserve else None)
                    except WorkError:
                        pass
                    else:
                        self._cancel_local_pending(p)
                        if preserve:
                            # The durable snapshot is restored at the next user
                            # turn. Recovery itself needs no second SQL read.
                            kept_draft = True
                text = "本次请求暂未完成，尚未执行新的写入。请重试，或补充要处理的对象、日期和具体要求；无需提供 token 或内部 ID。"
                if exc.stage.startswith("turn_model_"):
                    text = "本次意图识别暂未完成，尚未访问 Work 数据或执行写入。本次输入尚未保存，请重新发送完整请求；平台支持个人日历和云文档操作。"
                    if kept_draft:
                        text = "本次意图识别暂未完成，已保留之前核对的条件。请重发本条补充；旧授权链接和待执行预览已失效，尚未开始新的写入。"
                elif exc.stage == "turn_queue_busy":
                    text = (
                        "当前服务繁忙，已保留之前核对的条件。请稍后重发本条补充；旧授权链接和待执行预览已失效，尚未开始新的写入。"
                        if kept_draft
                        else "当前服务繁忙，排队暂未完成。本次输入尚未保存，请稍后重新发送完整请求；尚未访问 Work 数据或执行写入。"
                    )
                await self._reply(msg, "【本地 UAT】" + text + f"（{exc.stage}）")
            return True
        except Exception:
            logger.warning("work_turn stage=service_unavailable")
            await self._reply(msg, "【本地 UAT】本次 Work 请求暂未确认完成，请稍后重试或联系平台管理员。如果此前已确认修改，请先到 Work 核查结果；系统不会自动重复写入。")
            return True

    async def _handle_search_turn(self, msg, p, owner, source, turn, intent, *, evidence="", continuing=False):
        request = msg
        if evidence and evidence != msg.text:
            prior = turn.task.get("lookup") or {}
            if not continuing or turn.task.get("stage") != "awaiting_input" or not evidence.endswith(lookup_body(msg.text)) or intent.title not in msg.text and prior.get("title") != intent.title:
                raise WorkError("invalid_search_request")
            request = replace(msg, text=evidence, metadata=dict(msg.metadata), files=[])
        try:
            validate_file_lookup(intent, request.text, semantic_scope=bool(evidence), allow_title_only=continuing)
        except ValueError:
            raise WorkError("invalid_search_request") from None
        scope = self._search_settings.scope
        if not scope or not await self._bound(msg):
            await self._turn_state.finish_turn(owner, turn.turn_id, mode="exit")
            self._cancel_local_pending(p)
            detail = "当前环境尚未开放按标题搜索 Work 云文档，可直接发送有权访问的链接。" if not scope else "渠道绑定已变更，请检查连接后继续。"
            await self._reply(msg, "【本地 UAT】" + detail)
            return True
        intent_data = intent.model_dump(mode="json")
        task = {"protocol": SEARCH_JOB_PROTOCOL, "domain": "file_lookup", "intent": intent_data, "stage": "requested", "original_request": request.text[:1500]}
        ref = await self._turn_state.finish_turn(owner, turn.turn_id, mode="continue" if continuing else "new", task=task)
        self._cancel_local_pending(p)
        job = DocumentJob(replace(request, metadata=dict(msg.metadata), files=[]), "", "", time.monotonic() + 600, scope, intent=intent_data, task_ref=ref.as_dict(), protocol=SEARCH_JOB_PROTOCOL)
        consent = "本次申请 Work 云空间的查看、评论和下载权限，用于查找文件标题。" if scope == LEGACY_SEARCH_SCOPE else "本次只申请按标题搜索 Work 文件的用户权限。"
        await self._submit_job(job, p, source, consent=consent + "读取正文时会另行核对对应权限。\n\n")
        return True

    async def _handle_search_choice(self, msg, p, owner, source, turn, *, card_choice=None):
        selection = turn.context.get("file_search_selection") if turn.task_ref and turn.task.get("domain") == "file_lookup" else None
        if not selection or selection["expires_at"] <= time.time() or selection.get("scope", SEARCH_SCOPE) != self._search_settings.scope or not await self._bound(msg):
            await self._reply(msg, "【本地 UAT】当前没有可选的 Work 搜索结果。请重新发送标题搜索请求。")
            return True
        if card_choice:
            index, code = card_choice
        else:
            text = msg.text.strip()
            legacy = re.fullmatch(r"/work\s+选择\s+([1-5])\s+([A-Za-z0-9_-]{11})", text)
            index, code = (int(legacy[1]), legacy[2]) if legacy else (0, "")
            if re.fullmatch(r"[0-9]", text):
                parent = msg.metadata.get("parent_id") or msg.metadata.get("root_id")
                if parent and parent != selection.get("source_message_id"):
                    reference = selection.get("card_reference")
                    card = await self.store.read(CARD_PREVIEW_RECORD, reference, owner=owner) if self.store and reference else None
                    if not card or card.status != "ready" or card.value.get("message_id") != parent:
                        await self._reply(msg, "【本地 UAT】这条回复没有对应最新候选。请点击最新卡片，或直接在私聊中发送序号。")
                        return True
                index, code = int(text), selection["code"]
        options = selection.get("items", [])
        if not 1 <= index <= len(options) or not hmac.compare_digest(code, selection["code"]):
            await self._reply(msg, "【本地 UAT】请选择最新列表中的有效序号。")
            return True
        target = _SearchHandoff(**options[index - 1])
        request = replace(msg, text=turn.task["original_request"], metadata=dict(msg.metadata), files=[])
        try:
            await self._search_vault.require(p, self._search_settings.scope)
            job = self._search_read_job(request, target, turn.task_ref)
        except WorkError as exc:
            if exc.stage not in {"authorization_required", "scope_not_granted", "file_read_unavailable"}:
                raise
            detail = "当前环境尚未开通这类文件的正文读取权限。" if exc.stage == "file_read_unavailable" else "原搜索授权已失效，请重新发送标题搜索请求。"
            await self._reply(msg, "【本地 UAT】" + detail)
            return True
        if selection["expires_at"] <= time.time():
            await self._reply(msg, "【本地 UAT】搜索结果已过期，请重新搜索。")
            return True
        ref = await self._turn_state.finish_turn(owner, turn.turn_id, mode="continue", task={"protocol": job.protocol, "domain": "file_search_read", "stage": "requested"})
        self._cancel_local_pending(p)
        await self._save_task_context(p, ref)
        job.task_ref = ref.as_dict()
        if card_choice:
            await self._reply(msg, f"【本地 UAT】已选择第 {index} 项，正在准备读取。")
        await self._submit_job(job, p, source, consent="本次申请所选 Work 文件的正文读取所需权限。\n\n")
        return True

    def _search_read_job(self, msg, target, ref):
        scope = self._file_settings.scope_for(target.kind)
        if not scope:
            raise WorkError("file_read_unavailable")
        resource = WorkFileRef(target.kind, target.token)
        if target.url != resource.url(self.settings.document_host):
            raise WorkError("invalid_search_result")
        resource = WorkFileRef.from_intent(requested_range(resource, msg.text).intent())
        return DocumentJob(msg, resource.token, resource.url(self.settings.document_host), time.monotonic() + 600, scope, intent=resource.intent(), task_ref=ref.as_dict(), protocol=FILE_JOB_PROTOCOL)

    async def _handle_file_turn(self, msg, p, owner, source, turn, resources, links):
        resource = resources[0]
        scope = self._file_settings.scope_for(resource.kind)
        error = ""
        if len(resources) != 1 or links:
            error = "请一次发送一个 Work 文档、在线表格或云盘文件链接，便于核对读取目标。"
        elif not scope:
            error = "当前环境尚未开放该类 Work 文件读取，请联系管理员核对应用的用户身份只读权限。"
        elif not await self._bound(msg):
            error = "渠道绑定已变更或当前账号不在允许范围，请检查连接后继续。"
        if error:
            await self._turn_state.finish_turn(owner, turn.turn_id, mode="exit")
            self._cancel_local_pending(p)
            await self._reply(msg, "【本地 UAT】" + error)
            return True

        url = resource.url(self.settings.document_host)
        decision = await _run_turn_planner(
            msg,
            settings=self.settings,
            semaphore=self._turn_semaphore,
            planner=self._turn_planner,
            links=[(FILE_PLAN_TARGET, url)],
            task_context={},
            capabilities={"available": True, "read_only_resource": resource.kind},
        )
        action = decision.intent
        if decision.route not in {"new_work", "continue_work"} or not isinstance(action, Action) or action.operation != "document_read" or action.document_id != FILE_PLAN_TARGET:
            await self._turn_state.finish_turn(owner, turn.turn_id, mode="exit")
            self._cancel_local_pending(p)
            if decision.route == "general":
                return False
            detail = decision.question if decision.route == "clarify" else "这类文件支持读取、分析和总结。请重新发送链接并说明要读取的内容。"
            await self._reply(msg, "【本地 UAT】" + detail)
            return True
        try:
            resource = requested_range(resource, msg.text)
        except WorkError:
            await self._turn_state.finish_turn(owner, turn.turn_id, mode="exit")
            self._cancel_local_pending(p)
            await self._reply(msg, "【本地 UAT】请指定一个有效的单元格范围，例如 A150:D165。每次最多读取 100 行、16 列。")
            return True

        intent = resource.intent()
        task = {"protocol": FILE_JOB_PROTOCOL, "domain": "file_read", "intent": intent, "stage": "requested", "original_request": msg.text}
        ref = await self._turn_state.finish_turn(owner, turn.turn_id, mode="new", task=task)
        self._cancel_local_pending(p)
        job = DocumentJob(
            replace(msg, metadata=dict(msg.metadata), files=[]),
            resource.token,
            resource.url(self.settings.document_host),
            time.monotonic() + 600,
            scope,
            intent=intent,
            task_ref=ref.as_dict(),
            protocol=FILE_JOB_PROTOCOL,
        )
        await self._submit_job(job, p, source, consent="本次申请读取该类 Work 文件所需的用户权限。\n\n")
        return True

    def _grant_vault(self, job):
        if job.protocol == SEARCH_JOB_PROTOCOL or _job_protocol(job.intent) == SEARCH_JOB_PROTOCOL:
            scope = self._search_settings.scope
            if job.protocol != SEARCH_JOB_PROTOCOL or not scope or job.scope != scope or job.document_id or job.document_url:
                raise WorkError("file_search_unavailable")
            try:
                validate_file_lookup(FileLookupIntent.model_validate(job.intent), job.message.text, semantic_scope=True)
            except ValueError:
                raise WorkError("invalid_search_request") from None
            return self._search_vault
        resource = WorkFileRef.from_intent(job.intent)
        if resource is None:
            return self.vault
        if resource.kind == "docx" and not self._search_settings.scope:
            # This resource kind can only originate from title search. A
            # queued consent may not re-enable it after an operator turns it off.
            raise WorkError("file_read_unavailable")
        scope = self._file_settings.scope_for(resource.kind)
        if job.protocol != FILE_JOB_PROTOCOL or not scope or job.scope != scope or job.document_id != resource.token or job.document_url != resource.url(self.settings.document_host):
            raise WorkError("file_read_unavailable")
        return self._file_vaults[resource.kind]

    async def _submit_job(self, job, p, source, *, consent):
        if not self.store and sum(item.stage in {"awaiting_authorization", "authorizing", "processing"} for item in self.jobs.values()) >= self.settings.max_jobs:
            raise WorkError("job_capacity")
        vault = self._grant_vault(job)
        ticket = secrets.token_urlsafe(32)
        if not await self._register_job(ticket, job, p, source):
            return
        self.jobs[ticket] = job
        self._seen[(p, source)] = ticket
        try:
            grant = await vault.require(p, job.scope)
        except WorkError as exc:
            if exc.stage not in {"authorization_required", "scope_not_granted"}:
                raise
            await self._assert_task(p, TaskRef.parse(job.task_ref))
            await self._reply(
                job.message,
                "【本地 UAT】可以处理这次请求，需要您个人授权；这不是重新绑定渠道。请使用与消息发送者相同的 UAT 账号授权。\n\n"
                + consent
                + f"[授权并继续]({self.settings.authorize_entry}?{urlencode({'ticket': ticket})})\n\n链接10分钟有效。授权后继续本次请求；写入仍需单独预览和本人确认。",
            )
        else:
            self._schedule(job, grant)

    async def _register_job(self, ticket, job, principal, source):
        return True

    async def _forget_context(self, p):
        self._contexts.pop(p, None)
        self._calendar_dialogues.pop(p, None)
        self.calendar.contexts.pop(p, None)
        self.tools.known_events.pop(p, None)
        self._workflow_feedback.pop(p, None)
        self._verified_calendar_days.pop(p, None)

    async def _card_preview(self, msg, p, plan):
        if plan is None:
            return None
        return await self._make_card_preview(
            msg,
            p,
            {"plan_id": plan.id},
            min(300, plan.expires_at - time.monotonic()),
            "【本地 UAT】待确认修改（5 分钟内有效）\n" + plan.preview,
        )

    async def _search_card_preview(self, msg, p, ref, answer):
        choices = answer.choices
        return await self._make_card_preview(
            msg,
            p,
            {"search_code": choices["code"], "task_ref": ref.as_dict(), "choice_count": len(choices["items"])},
            choices["expires_at"] - time.time(),
            "【本地 UAT】" + answer.text + "\n\n点击按钮读取对应文件，也可直接回复序号。候选保留 5 分钟。",
            reference=choices["card_reference"],
        )

    async def _make_card_preview(self, msg, p, details, ttl, text, *, reference=None):
        settings = CardSettings.from_env(self.settings.profile, app_id=self.app_id)
        if not settings or settings.app_id != self.app_id or not self.store or ttl <= 0 or not msg.thread_ts or msg.metadata.get("chat_type") != "p2p":
            return None
        tenant = settings.preview_tenant(msg.metadata)
        if tenant is None:
            if settings.transport == "websocket" and not self._missing_card_tenant_logged:
                self._missing_card_tenant_logged = True
                logger.warning("work_card transport=websocket stage=preview_text_fallback reason=unverified_tenant")
            return None
        owner = self.vault.principal_key(p)
        reference = reference or secrets.token_urlsafe(24)
        data = {**details, "app_id": self.app_id, "tenant_key": tenant, "transport": settings.transport, "chat_id": p.chat, "sender": p.sender, "source_message_id": msg.thread_ts}
        try:
            issued = await self.store.create(CARD_PREVIEW_RECORD, reference, data, owner=owner, status="issued", ttl=ttl)
        except Exception:
            logger.warning("work_card stage=preview_record_unavailable")
            return None
        if not issued:
            return None

        async def bind(message_id):
            if await self.store.change(CARD_PREVIEW_RECORD, reference, issued, value={**data, "message_id": message_id}, status="ready"):
                return True
            saved = await self.store.read(CARD_PREVIEW_RECORD, reference, owner=owner)
            return bool(saved and saved.status == "ready" and saved.value.get("message_id") == message_id)

        return CardPreview(reference, text, bind, choice_count=details.get("choice_count", 0))

    async def enqueue_card_click(self, click, settings, *, authority=None):
        """Admit an authenticated platform action without reserving a chat turn."""
        if self._closed or not self.store or not self.repo or settings.app_id != self.app_id:
            return "unavailable"
        if settings.transport == "websocket" and authority is None:
            return "unavailable"
        if not click.tenant_key or settings.tenant_key and click.tenant_key != settings.tenant_key:
            return "invalid"
        if len(self._card_tasks) >= self.settings.concurrency:
            return "busy"
        row = await self.repo.find_connection_by_external_identity(provider="feishu", external_account_id=click.sender, workspace_id=click.chat_id)
        if not row or row.get("status") != "connected" or not row.get("owner_user_id") or not row.get("id"):
            return "invalid"
        p = Principal(self.app_id, click.sender, row["owner_user_id"], row["id"], click.chat_id, click.chat_id)
        if not await self.vault.bound(p):
            return "invalid"
        owner = self.vault.principal_key(p)
        card = await self.store.read(CARD_PREVIEW_RECORD, click.reference, owner=owner)
        expected = {"app_id": self.app_id, "tenant_key": click.tenant_key, "chat_id": click.chat_id, "sender": click.sender, "message_id": click.message_id}
        if not card or card.status != "ready" or card.value.get("transport", "http") != settings.transport or any(card.value.get(key) != value for key, value in expected.items()):
            return "invalid"
        search_action = re.fullmatch(r"select_([1-5])", click.verb)
        if search_action:
            index = int(search_action[1])
            ref = TaskRef.parse(card.value.get("task_ref"))
            code = card.value.get("search_code")
            if ref is None or not isinstance(code, str) or index > card.value.get("choice_count", 0):
                return "invalid"
            try:
                await self.store.assert_task(owner, ref)
            except WorkError as exc:
                if exc.stage == "task_superseded":
                    return "invalid"
                raise
        else:
            plan_id = card.value.get("plan_id")
            if click.verb not in {"confirm", "cancel"} or not plan_id:
                return "invalid"
            plan = await self.store.read("plan", plan_id, owner=owner)
            if not plan or plan.status != "pending":
                return "invalid"
        event_value = {"reference": click.reference, "verb": click.verb}
        event = await self.store.create(CARD_EVENT_RECORD, click.event_id, event_value, owner=owner, ttl=600)
        if not event:
            event = await self.store.read(CARD_EVENT_RECORD, click.event_id, owner=owner)
            if not event or event.value != event_value or event.status != "waiting":
                return "duplicate"
        # A receiver can lose its lease during SQL admission. Leave its event
        # waiting so the replacement can receive a retry without a plan claim.
        if authority is not None and not await authority():
            return "unavailable"
        if self._closed or len(self._card_tasks) >= self.settings.concurrency:
            return "busy"
        msg = InboundMessage(
            channel_name="feishu",
            chat_id=p.chat,
            user_id=p.sender,
            text="",
            thread_ts=card.value["source_message_id"],
            connection_id=p.connection,
            owner_user_id=p.owner,
            workspace_id=p.workspace,
            metadata={"chat_type": "p2p", CARD_REPLY_TARGET_KEY: CardReplyTarget(click.message_id)},
        )
        action = self._run_search_card_click(msg, p, click, event, ref, index, code) if search_action else self._run_card_click(msg, p, click, plan_id, event)
        task = asyncio.create_task(action)
        self._card_tasks.add(task)
        self._tasks.add(task)
        task.add_done_callback(self._card_tasks.discard)
        task.add_done_callback(self._done)
        return "accepted"

    async def _run_search_card_click(self, msg, p, click, event, ref, index, code):
        owner = self.vault.principal_key(p)
        source = "search-card:" + click.event_id
        try:
            if not await self._bound(msg) or not await self.store.change(CARD_EVENT_RECORD, click.event_id, event, status="handling"):
                return
            turn = await self.store.reserve_turn(owner, source, expected_ref=ref)
            if turn is not None:
                await self._handle_search_choice(msg, p, owner, source, turn, card_choice=(index, code))
        except Exception as exc:
            if isinstance(exc, WorkError) and exc.stage == "task_superseded":
                return
            logger.warning("work_card stage=search_choice_failed")
            if await self._bound(msg):
                await self._reply(msg, "【本地 UAT】未能完成这次读取。请重新搜索后选择。")

    async def _run_card_click(self, msg, p, click, plan_id, event):
        # Independent of the write lease: serialize competing card buttons,
        # including cancel, while text confirmations still use the same plan CAS.
        lease = "card-control:" + plan_id
        holder = secrets.token_urlsafe(24)
        acquired = False
        try:
            async with asyncio.timeout(200):
                acquired = bool(await self.store.acquire(lease, holder, ttl=210))
                if not acquired or self._closed or not await self._bound(msg):
                    return
                if not await self.store.change(CARD_EVENT_RECORD, click.event_id, event, status="handling"):
                    return
                await self._control(msg, p, "确认" if click.verb == "confirm" else "取消", plan_id)
        finally:
            if acquired:
                try:
                    async with asyncio.timeout(3):
                        await self.store.release(lease, holder)
                except Exception:
                    logger.warning("work_card stage=control_lease_release_deferred")

    async def _control(self, msg, p, verb, plan_id):
        try:
            if verb == "取消":
                await self.tools.cancel_shared(p, plan_id)
                await self._reply(msg, "【本地 UAT】已取消；未修改数据。")
                return
            await self._reply(msg, "【本地 UAT】正在核对确认记录和目标最新状态……", final=False)
            async with asyncio.timeout(180):
                result = await self.tools.confirm(p, plan_id)
            plan = self.tools.plans.get(plan_id)
            if plan and plan.stage == "completed" and json.loads(plan.payload_json).get("operation") != "calendar_batch":
                action = Action.model_validate_json(plan.payload_json)
                if action.operation.startswith("event_"):
                    from .capabilities import parse_time

                    try:
                        await self._assert_task(p, plan.task_ref)
                    except WorkError:
                        pass  # The confirmed write still reports its own receipt.
                    else:
                        self.calendar.remember(
                            p,
                            [{"event_id": plan.receipt, "summary": action.title, "description": action.text, "start_time": {"timestamp": str(parse_time(action.start))}, "end_time": {"timestamp": str(parse_time(action.end))}}],
                            recent_id=plan.receipt,
                        )
            if await self._bound(msg):
                await self._reply(msg, "【本地 UAT】" + result)
        except Exception as exc:
            notice = await self.tools.terminal_notice(p, plan_id)
            if notice and await self._bound(msg):
                await self._reply(msg, "【海能 Work】" + notice)
                return
            plan = self.tools.plans.get(plan_id)
            uncertain = plan and plan.principal == p and plan.stage in {"uncertain", "executing", "completed"}
            safe = exc.stage if isinstance(exc, WorkError) else "operation_timeout_or_transport"
            receipt = next((url for _, url in document_links(plan.receipt, self.settings.document_host) if url == plan.receipt), "") if uncertain and plan.receipt else ""
            if await self._bound(msg):
                await self._reply(msg, f"【本地 UAT】操作未确认成功（{safe}）。" + ("请求可能已经生效，请到 Work 核查；不会自动重试。" + ("\n回执：" + receipt if receipt else "") if uncertain else "未执行新的写入，请重新请求预览。"))

    async def authorize(self, ticket, browser_session):
        self._prune()
        job = self.jobs.get(ticket)
        if not job or job.stage != "awaiting_authorization" or not await self._bound(job.message):
            raise WorkError("invalid_or_expired_ticket")
        if job.stage != "awaiting_authorization":
            raise WorkError("invalid_or_expired_ticket")
        await self._assert_task(self._identity(job.message), TaskRef.parse(job.task_ref))
        self._grant_vault(job)
        job.stage = "authorizing"
        job.state = secrets.token_urlsafe(32)
        job.browser_session = browser_session if TOKEN_RE.fullmatch(browser_session) else secrets.token_urlsafe(32)
        self._states[job.state] = ticket
        return {
            "authorize_url": self.settings.authorize_url + "?" + urlencode({"client_id": self.app_id, "response_type": "code", "redirect_uri": self.settings.callback_url, "scope": job.scope, "state": job.state}),
            "browser_session": job.browser_session,
        }

    async def callback(self, *, state, code, browser_session, error=""):
        self._prune()
        job = self.jobs.get(self._states.get(state, ""))
        if not job or not browser_session or not hmac.compare_digest(job.browser_session, browser_session):
            raise WorkError("invalid_state_or_browser")
        self._states.pop(state, None)
        try:
            await self._assert_task(self._identity(job.message), TaskRef.parse(job.task_ref))
            self._grant_vault(job)
            if error or not code or not await self._bound(job.message):
                raise WorkError("authorization_or_binding_invalid")
            data = await self._api("token", payload={"grant_type": "authorization_code", "client_id": self.app_id, "client_secret": self._secret, "code": code, "redirect_uri": self.settings.callback_url})
            await self._assert_task(self._identity(job.message), TaskRef.parse(job.task_ref))
            tokens = data.get("data") or data
            token, expires = tokens.get("access_token"), tokens.get("expires_in")
            if not isinstance(token, str) or not token or type(expires) not in {int, float} or not 30 < expires <= 86400:
                raise WorkError("token_response_invalid")
            info = await self._api("identity", token=token)
            await self._assert_task(self._identity(job.message), TaskRef.parse(job.task_ref))
            sender = (info.get("data") or {}).get("open_id")
            if not isinstance(sender, str) or not hmac.compare_digest(sender, job.message.user_id):
                raise WorkError("authorization_user_mismatch")
            if job.stage != "authorizing" or not await self._bound(job.message):
                raise WorkError("binding_changed")
            scopes = tokens.get("scope", "")
            if not isinstance(scopes, str) or not has_scopes(frozenset(scopes.split()), job.scope):
                raise WorkError("scope_not_granted")
            grant = Grant(token, time.monotonic() + min(expires, 7200) - 30, frozenset(scopes.split()))
            await self._install_grant(job, grant)
            self._schedule(job, grant)
            return {"stage": "accepted", "message": "授权账号已核对，请回到 UAT 机器人查看结果或待确认操作。授权本身不会执行写入。"}
        except Exception as exc:
            try:
                await self._assert_task(self._identity(job.message), TaskRef.parse(job.task_ref))
            except WorkError as stale:
                if stale.stage != "task_superseded":
                    raise
                job.stage = "cancelled"
                raise stale from None
            if isinstance(exc, WorkError) and exc.stage == "task_superseded":
                job.stage = "cancelled"
                raise
            job.stage = "failed"
            if await self._bound(job.message):
                if isinstance(exc, WorkError) and exc.stage == "scope_not_granted" and job.scope == CALENDAR_AUTH_SCOPE:
                    await self._reply(
                        job.message,
                        f"【本地 UAT】未获得完整的日历权限（{CALENDAR_CONSENT_DESCRIPTION}）；未执行操作。请让 ITC 确认 UAT 应用已开通对应用户身份权限并发布，然后重新发送日历请求并授权。",
                    )
                else:
                    await self._reply(job.message, "【本地 UAT】授权未完成、权限未授予或账号与消息发送者不一致；未执行操作。请使用同一 UAT 账号重新请求。")
            if isinstance(exc, WorkError):
                raise
            raise WorkError("authorization_transport") from None

    async def _install_grant(self, job, grant):
        p = self._identity(job.message)
        if not await self.vault.bound(p):
            raise WorkError("binding_changed")
        await self._assert_task(p, TaskRef.parse(job.task_ref))
        self._grant_vault(job).put(p, grant)

    def _schedule(self, job, grant):
        job.stage = "processing"
        task = asyncio.create_task(self._run_and_continue(job))
        self._job_tasks[id(job)] = task
        self._tasks.add(task)
        task.add_done_callback(self._done)
        task.add_done_callback(lambda _: self._job_tasks.pop(id(job), None))

    async def _run_and_continue(self, job):
        handoff = await self._run(job)
        if handoff is None:
            return
        p, ref = self._identity(job.message), TaskRef.parse(job.task_ref)
        source = job.message.thread_ts or job.message.metadata.get("message_id")
        try:
            await self._assert_task(p, ref)
            await self._search_vault.require(p, job.scope)
            read_job = self._search_read_job(job.message, handoff, ref)
            await self._submit_job(read_job, p, source + ":search-result-read", consent="已定位到唯一同名 Work 文件。本次申请正文读取所需权限。\n\n")
        except WorkError as exc:
            if exc.stage == "task_superseded":
                return
            await self._assert_task(p, ref)
            if await self._bound(job.message):
                await self._reply(job.message, "【本地 UAT】已定位到文件，未读取正文。请重新发送原请求或直接发送文件链接。")

    def _done(self, task):
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            logger.error("work_operation stage=background_failure")

    async def _heartbeat(self, job, stop):
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=15)
                return
            except TimeoutError:
                pass
            try:
                await self._assert_task(self._identity(job.message), TaskRef.parse(job.task_ref))
            except WorkError:
                return
            if await self._bound(job.message):
                await self._reply(job.message, "【本地 UAT】仍在处理，请稍候；若需要写入，会先给出修改预览，不会自行修改。", final=False)

    def _remember_question(self, p, request, question):
        old = self._calendar_dialogues.get(p)
        turns = [*(old.turns if old else []), {"user": request[:1500], "question": question[:1500]}][-4:]
        self._calendar_dialogues[p] = CalendarDialogue(time.monotonic() + 600, turns)

    async def _plan_answer(self, job, p):
        if not job.intent:
            raise WorkError("task_reference_required")
        if job.protocol == SEARCH_JOB_PROTOCOL:
            return await self._answer_search(job, p)
        resource = WorkFileRef.from_intent(job.intent)
        if resource:
            return await self._answer_file(job, p, resource)
        decision = WorkTurnDecision.model_validate(job.intent)
        action = decision.intent
        await self._assert_task(p, TaskRef.parse(job.task_ref))
        if decision.draft is not None:
            if job.scope != CALENDAR_AUTH_SCOPE:
                raise WorkError("calendar_draft_scope_mismatch")
            # These tools read only. Every write still passes the immutable
            # preview and the existing sender-bound confirmation executor.
            resolved = await resolve_draft(p, decision.draft, self.calendar, self.tools, task_ref=TaskRef.parse(job.task_ref))
            await self._assert_task(p, TaskRef.parse(job.task_ref))
            self._workflow_feedback[p] = (
                time.monotonic() + 600,
                {
                    "question": resolved.text[:3000] if resolved.needs_reply else "",
                    "needs_reply": resolved.needs_reply,
                    "selected_facts": resolved.selected_facts[:10],
                    "conflicts": resolved.conflicts[:20],
                },
            )
            if not resolved.actions:
                if resolved.needs_reply:
                    self._remember_question(p, job.message.text, resolved.text)
                else:
                    self._calendar_dialogues.pop(p, None)
                return resolved.text
            self._calendar_dialogues.pop(p, None)
            # Even one draft-derived write uses the same immutable plan
            # envelope, including any conflict notes that the user reviews.
            plan = await self.tools.prepare_batch(p, resolved.actions, task_ref=TaskRef.parse(job.task_ref), summary=resolved.text[:500])
            job.calendar_preview = plan
            return self._preview_reply(plan)
        if isinstance(action, CalendarIntent):
            if job.scope != CALENDAR_AUTH_SCOPE:
                return "请明确说明日历操作，重新发送日程请求以核对对应授权；本次未修改。"
            resolved = await self.calendar.resolve(p, action)
            await self._assert_task(p, TaskRef.parse(job.task_ref))
            if not resolved.action:
                if resolved.needs_reply:
                    self._remember_question(p, job.message.text, resolved.text)
                else:
                    self._calendar_dialogues.pop(p, None)
                return resolved.text
            action = resolved.action
        if not isinstance(action, Action):
            raise WorkError("invalid_model_plan")
        if action.document_id and action.document_id != job.document_id:
            raise WorkError("model_changed_document_target")
        if action.operation == "clarify":
            answer = action.text or "请补充明确的文档目标、操作内容或日程起止时间。"
            if job.scope == CALENDAR_AUTH_SCOPE and action.followup:
                self._remember_question(p, job.message.text, answer)
            else:
                self._calendar_dialogues.pop(p, None)
            return answer
        self._calendar_dialogues.pop(p, None)
        if action.operation == "document_read":
            title, content = await self.tools.read_document(p, action.document_id)
            await self._assert_task(p, TaskRef.parse(job.task_ref))
            answer = await self._summarize(job.message.text, title, content) if content.strip() else "文档正文为空。"
            return f"《{title[:200]}》\n{answer}\n来源：{self.tools.doc_url(action.document_id)}\n（未修改原文档）"
        if action.operation == "events_list":
            return await self.tools.events(p, action)
        plan = await self.tools.prepare(p, action, task_ref=TaskRef.parse(job.task_ref))
        if action.operation in {"event_create", "event_update"}:
            job.calendar_preview = plan
        return self._preview_reply(plan)

    async def _answer_search(self, job, p):
        vault, ref = self._grant_vault(job), TaskRef.parse(job.task_ref)
        intent = FileLookupIntent.model_validate(job.intent)
        grant = await vault.require(p, job.scope)

        async def guard():
            await self._assert_task(p, ref)
            current = await vault.require(p, job.scope)
            if current.token != grant.token or current.revision != grant.revision:
                raise WorkError("search_authorization_changed")

        await guard()
        try:
            results = await search_documents(self._api, grant.token, intent.title, host=self.settings.document_host, guard=guard, scope=job.scope)
        except WorkError as exc:
            if exc.http_status == 401 or exc.business_code == 99991679:
                await vault.revoke_shared(p, expected=grant)
            raise
        await guard()
        items = results.items[:_SEARCH_CHOICE_LIMIT]
        if not items:
            return "没有找到匹配的 Work 文件。请核对完整标题，或直接发送有权访问的 Work 链接。" if results.complete else "本次 Work 搜索结果不完整，暂无法确认是否存在匹配文件。请缩小标题范围或发送文件链接。"
        exact = [item for item in results.items if is_exact_title(item.title, intent.title)]
        if intent.read and results.complete and len(exact) == 1:
            target = exact[0]
            if self._file_settings.scope_for(target.kind):
                return _SearchHandoff(target.kind, target.token, target.url)
            return f"已找到唯一同名文件。当前环境尚未开通这类文件的正文读取权限。\n{target.url}"

        code = secrets.token_urlsafe(8)
        choices = {
            "code": code,
            "expires_at": time.time() + _SEARCH_CHOICE_TTL,
            "scope": job.scope,
            "source_message_id": job.message.thread_ts,
            "card_reference": secrets.token_urlsafe(24),
            "items": [{"kind": item.kind, "token": item.token, "url": item.url} for item in items],
        }
        labels = {"docx": "云文档", "sheet": "在线表格", "drive": "云盘文件", "bitable": "多维表格"}
        lines = ["Work 搜索结果（尚未读取正文）"]
        for index, item in enumerate(items, 1):
            title = re.sub(r"[A-Za-z][A-Za-z0-9+.-]*://\S+", "［链接已省略］", item.title)
            title = " ".join(title.split())[:150].translate(str.maketrans("[]()<>", "［］（）〈〉"))
            modified = f" · 更新 {item.updated_at}" if item.updated_at else ""
            lines.append(f"{index}. {title} · {labels[item.kind]}{modified}\n{item.url}")
        if not results.complete or len(results.items) > _SEARCH_CHOICE_LIMIT:
            lines.append("这里只列出部分结果。如未找到目标，请补充更准确的标题。")
        return _SearchSelection("\n\n".join(lines), choices)

    async def _answer_file(self, job, p, resource):
        vault, ref = self._grant_vault(job), TaskRef.parse(job.task_ref)
        operations = {"docx": {"document_info", "document_content"}, "sheet": {"sheet_tabs", "sheet_values"}, "drive": {"drive_file_download"}, "bitable": {"bitable_tables", "bitable_records"}}[resource.kind]

        async def request(operation, **kwargs):
            if operation not in operations or kwargs.get("document_id") != resource.token:
                raise WorkError("invalid_file_reference")
            await self._assert_task(p, ref)
            grant = await vault.require(p, job.scope)
            try:
                result = await self._api(operation, token=grant.token, **kwargs)
            except WorkError as exc:
                if exc.http_status == 401 or exc.business_code == 99991679:
                    await vault.revoke_shared(p, expected=grant)
                raise
            await self._assert_task(p, ref)
            await vault.require(p, job.scope)
            return result

        title, content, truncated = await read_file(resource, request)
        if not content.strip():
            raise WorkError("file_no_readable_text")
        await self._assert_task(p, ref)
        await vault.require(p, job.scope)
        answer = await self._summarize(job.message.text, title, content)
        notice = "\n（仅读取了部分内容，结论限于本次读取范围。）" if truncated else ""
        return f"《{title[:200]}》\n{answer}\n来源：{job.document_url}{notice}"

    @staticmethod
    def _preview_reply(plan):
        return "待确认修改（尚未执行，5 分钟有效）：\n" + plan.preview + f"\n\n确认请单独回复：\n/work 确认 {plan.id}\n取消：\n/work 取消 {plan.id}"

    async def _run(self, job):
        heartbeat = None
        stop = asyncio.Event()
        try:
            async with asyncio.timeout(240):
                p = self._identity(job.message)
                await self._assert_task(p, TaskRef.parse(job.task_ref))
                await self._reply(job.message, "【本地 UAT】请求已接收，正在核对个人授权并处理……", final=False)
                heartbeat = asyncio.create_task(self._heartbeat(job, stop))
                p = self._identity(job.message)
                lock = self._principal_locks.setdefault(p, asyncio.Lock())
                async with lock, self._semaphore:
                    await self._sync_task_context(job, p)
                    vault = self._grant_vault(job)
                    await vault.require(p, job.scope)
                    answer = await self._plan_answer(job, p)
                    stop.set()
                    await asyncio.gather(heartbeat, return_exceptions=True)
                    heartbeat = None
                    ref = TaskRef.parse(job.task_ref)
                    search_selection = answer if isinstance(answer, _SearchSelection) else None
                    if search_selection:
                        await self._turn_state.save_task_context(self.vault.principal_key(p), ref, {"file_search_selection": search_selection.choices}, ttl=_SEARCH_CHOICE_TTL)
                        answer = search_selection.text + "\n\n直接回复序号，例如 1。候选保留 5 分钟。"
                    else:
                        await self._save_task_context(p, ref)
                    await vault.require(p, job.scope)
                    await self._assert_task(p, ref)
                    if await self._bound(job.message):
                        if isinstance(answer, _SearchHandoff):
                            job.stage = "completed"
                            return answer
                        card_preview = await self._search_card_preview(job.message, p, ref, search_selection) if search_selection else await self._card_preview(job.message, p, job.calendar_preview)
                        if card_preview is None:
                            await self._reply(job.message, "【本地 UAT】" + answer)
                        else:
                            await self._reply(job.message, "【本地 UAT】" + answer, card_preview=card_preview)
                    job.stage = "completed"
        except asyncio.CancelledError:
            job.stage = "cancelled"
            raise
        except Exception as exc:
            try:
                await self._assert_task(self._identity(job.message), TaskRef.parse(job.task_ref))
            except WorkError as stale:
                if stale.stage != "task_superseded":
                    raise
                job.stage = "cancelled"
                return
            if isinstance(exc, WorkError) and exc.stage == "task_superseded":
                job.stage = "cancelled"
                return
            job.stage = "failed"
            if heartbeat:
                stop.set()
                await asyncio.gather(heartbeat, return_exceptions=True)
                heartbeat = None
            facts = f"阶段：{exc.stage}" if isinstance(exc, WorkError) else "阶段：model_or_transport"
            if isinstance(exc, WorkError):
                facts += f"；HTTP：{exc.http_status}；错误码：{exc.business_code}"
                if job.intent and job.intent.get("operation") == "file_read":
                    facts += file_error_explanation(exc.stage, exc.http_status)
                if exc.stage == "authorization_required":
                    facts += "；个人授权已过期，请重新发送请求获取授权（不需要解绑渠道）"
                elif exc.business_code == 99991679:
                    facts += "；当前用户授权未获接口所需权限，请重新发送请求进行重新授权。如仍失败，请让 ITC 核对 UAT 应用的对应用户身份权限和发布状态。"
                elif exc.business_code == 99991672:
                    facts += "；应用未开通接口权限，请让 ITC 核对 UAT 应用权限和发布状态，不能仅靠重新授权解决。"
                elif exc.business_code == 99991401:
                    facts += "；请求出口 IP 被应用白名单拦截，请管理员核对当前出口和白名单后重试；平台已接入该能力。"
                elif exc.stage == "target_changed_request_new_preview":
                    facts += "；日程或文档已发生变化，请重新请求生成预览。"
            if await self._bound(job.message):
                await self._reply(job.message, "【本地 UAT】本次处理未完成，未执行写入。" + facts)
        finally:
            if heartbeat:
                stop.set()
                await asyncio.gather(heartbeat, return_exceptions=True)
            job.expires_at = time.monotonic() + 900

    async def drain(self):
        while self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def close(self):
        self._closed = True
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self.grants.clear()
        for vault in self._special_grant_vaults():
            vault.grants.clear()
            vault._records.clear()
        self.jobs.clear()
        self._states.clear()
        self._seen.clear()
        self._job_tasks.clear()
        if isinstance(self._turn_state, MemoryTurns):
            self._turn_state.clear()
        self._contexts.clear()
        self._calendar_dialogues.clear()
        self._workflow_feedback.clear()
        self._verified_calendar_days.clear()
        self.calendar.contexts.clear()
        self._principal_locks.clear()
        self.tools.plans.clear()
        self.tools.known_events.clear()
        if self._owns_api:
            await self._api.close()


async def handle_unavailable_work(msg, *, send, semaphore=None):
    """Preserve ordinary chat while reporting a verified Work-entry outage.

    Called only after ChannelManager's bound-identity gate. It creates no task,
    consent link, grant or provider request, and cannot accept browser chat.
    """
    if msg.channel_name != "feishu" or msg.metadata.get("chat_type") != "p2p" or not all((msg.user_id, msg.owner_user_id, msg.connection_id, msg.workspace_id, msg.chat_id)):
        return False
    try:
        settings = WorkSettings.from_env()
    except ValueError:
        if os.getenv("DEER_FLOW_WORK_ENABLED") != "true" and os.getenv("DEER_FLOW_WORK_DOCS_UAT_ENABLED") != "true":
            return False
        settings = WorkSettings()
    else:
        if not settings.enabled:
            return False
    if msg.files:
        return False
    try:
        decision = await _run_turn_planner(
            msg, settings=settings, semaphore=semaphore if semaphore is not None else asyncio.Semaphore(2), planner=plan_turn, links=document_links(msg.text, settings.document_host), capabilities={"available": False}
        )
        if decision.route == "general":
            return False
        text = "【海能 Work】本次 Work 文档或日历服务暂不可用，尚未访问个人数据或执行修改。请稍后重试，或联系平台管理员检查集成服务；当前平台支持这些能力。"
    except WorkError as exc:
        text = (
            "【海能 Work】当前服务繁忙，排队暂未完成。本次输入尚未保存，请稍后重新发送完整请求；尚未执行 Work 操作。"
            if exc.stage == "turn_queue_busy"
            else "【海能 Work】本次请求识别暂未完成，尚未执行 Work 操作。本次输入尚未保存，请重新发送完整请求。"
        )
    await send(
        OutboundMessage(
            channel_name=msg.channel_name,
            chat_id=msg.chat_id,
            thread_id="",
            text=text,
            is_final=True,
            thread_ts=msg.thread_ts,
            connection_id=msg.connection_id,
            owner_user_id=msg.owner_user_id,
            metadata={"user_id": msg.user_id, "message_id": msg.thread_ts},
        )
    )
    return True


def build_work_document_service(manager):
    settings = WorkSettings.from_env()
    if not settings.enabled:
        return None
    owners = settings.allowed_owners
    if settings.profile == "uat" and not owners:
        return None
    if not settings.persistent and len(os.getenv("DEER_FLOW_WORK_DOCS_RELAY_KEY", "")) < 32:
        return None
    from app.channels.service import get_channel_service

    channels = get_channel_service()
    config = channels._config.get("feishu", {}) if channels else {}
    app_id = config.get("app_id", "")
    if not config.get("enabled") or not app_id or config.get("domain", WORK_API) != settings.api_url or not manager._connection_repo:
        return None
    expected_secret = config.get("app_secret", "")

    def runtime_guard():
        current = get_channel_service()
        values = current._config.get("feishu", {}) if current else {}
        return bool(
            WorkSettings.from_env() == settings
            and current is channels
            and values.get("enabled")
            and values.get("app_id") == app_id
            and values.get("domain", WORK_API) == settings.api_url
            and hmac.compare_digest(values.get("app_secret", ""), expected_secret)
        )

    kwargs = dict(app_id=app_id, app_secret=expected_secret, allowed_owners=owners, repo=manager._connection_repo, send=manager.bus.publish_outbound, runtime_guard=runtime_guard, settings=settings)
    if settings.persistent:
        from deerflow.persistence import get_session_factory

        from .persistent import PersistentWorkService
        from .store import WorkStore

        sf = get_session_factory()
        if sf is None:
            raise ValueError("Production Work requires shared SQL persistence")
        origins = repr((settings.profile, settings.api_url, settings.authorize_url, settings.callback_url, settings.document_host))
        namespace = settings.profile + ":" + app_id + ":" + hmac.new(expected_secret.encode(), ("work-credential-generation:" + origins).encode(), "sha256").hexdigest()
        kwargs["store"] = WorkStore(sf, key=os.getenv("DEER_FLOW_WORK_STATE_KEY", ""), namespace=namespace)
        return PersistentWorkService(**kwargs)
    return WorkDocumentService(**kwargs)


async def active_work_service(manager):
    """Rebuild cached integration on authoritative config changes, including UI saves."""
    lock = getattr(manager, "_work_service_lock", None)
    if lock is None:
        lock = manager._work_service_lock = asyncio.Lock()
    async with lock:
        old = manager._work_documents
        if old is not None:
            try:
                valid = not old._closed and old.vault.runtime_guard() is True
            except Exception:
                valid = False
            if valid:
                return old
            await old.close()
            manager._work_documents = None
        manager._work_documents = build_work_document_service(manager)
        return manager._work_documents

"""Default-off Work orchestration, shared by production SQL and local UAT."""

import asyncio
import hmac
import logging
import os
import re
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from urllib.parse import urlencode

from app.channels.message_bus import InboundMessage, OutboundMessage

from .agent import propose_action, summarize_document
from .auth import Grant, Principal, TokenVault
from .calendar import CalendarIntent, CalendarResolver
from .capabilities import Action, WorkCapabilities
from .client import TOKEN_RE, UAT_APP_ID, WORK_API, WorkAPI, WorkError, document_links
from .config import WorkSettings
from .scopes import CALENDAR_AUTH_SCOPE, CALENDAR_CONSENT_DESCRIPTION, DOCUMENT_SCOPE, has_scopes

logger = logging.getLogger(__name__)
MAX_JOBS = 32


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


@dataclass
class CalendarDialogue:
    expires_at: float
    turns: list[dict] = field(default_factory=list, repr=False)


class WorkDocumentService:
    def __init__(self, *, app_id, app_secret, allowed_owners, repo, send, api=None, summarize=None, planner=None, runtime_guard=None, settings=None, store=None):
        self.settings = settings or WorkSettings.local_uat()
        if not app_id or not app_secret or self.settings.profile == "uat" and (not allowed_owners or settings is None and app_id != UAT_APP_ID) or self.settings.profile == "production" and (app_id == UAT_APP_ID or not store):
            raise ValueError("Local integration requires the UAT app and explicit test owners")
        self.app_id, self._secret = app_id, app_secret
        self.allowed_owners, self.repo, self.send = frozenset(allowed_owners) if allowed_owners is not None else None, repo, send
        self.store = store
        self._api, self._owns_api = api or WorkAPI(api_url=self.settings.api_url), api is None
        self._summarize, self._planner = summarize or summarize_document, planner or propose_action
        self.vault = TokenVault(app_id, repo, allowed_owners, runtime_guard, store=store)
        self.tools = WorkCapabilities(self.vault, self._api, document_host=self.settings.document_host)
        self.calendar = CalendarResolver(self.tools)
        self.jobs: OrderedDict[str, DocumentJob] = OrderedDict()
        self.grants = self.vault.grants
        self._states: dict[str, str] = {}
        self._seen: dict[tuple, str] = {}
        self._contexts: dict[Principal, tuple[float, str]] = {}
        self._calendar_dialogues: dict[Principal, CalendarDialogue] = {}
        self._principal_locks: dict[Principal, asyncio.Lock] = {}
        self._tasks: set[asyncio.Task] = set()
        self._semaphore = asyncio.Semaphore(self.settings.concurrency)
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
        self.calendar.prune()
        self._principal_locks = {p: lock for p, lock in self._principal_locks.items() if lock.locked() or any(self._identity(j.message) == p and j.stage == "processing" for j in self.jobs.values())}
        self.vault.prune()
        self.tools.prune()

    async def _reply(self, msg, text, *, final=True):
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
        for secret in [self._secret, *(g.token for g in self.grants.values())]:
            text = text.replace(secret, "[已隐藏]")
        await self.send(
            OutboundMessage(
                channel_name="feishu",
                chat_id=msg.chat_id,
                thread_id="",
                text=text,
                is_final=final,
                thread_ts=msg.thread_ts,
                connection_id=msg.connection_id,
                owner_user_id=msg.owner_user_id,
                metadata={"user_id": msg.user_id, "message_id": msg.thread_ts},
            )
        )

    async def handle(self, msg):
        if self._closed:
            return False
        self._prune()
        if msg.channel_name != "feishu":
            return False
        links = document_links(msg.text, self.settings.document_host)
        explicit = bool(re.match(r"^/(?:work|doc_skill)(?:\s|$)", msg.text.strip()))
        try:
            p = self._identity(msg)
        except WorkError:
            if links or explicit:
                await self._reply(msg, "【本地 UAT】仅支持指定账号的已绑定私聊；未访问 Work 数据。")
                return True
            return False
        context = self._contexts.get(p)
        document_intent = bool(links or re.search(r"文档|会议纪要|这篇|这个文件", msg.text))
        calendar_intent = not document_intent and bool(
            re.search(r"日历|日程|会议|开会|(?:今天|明天|后天|本周|下周|周[一二三四五六日天]).*(?:安排|空闲|忙)|(?:查|看).*(?:安排|忙闲)", msg.text)
            or p in self._calendar_dialogues
            or p in self.calendar.contexts
            and re.search(r"刚才|那条|那个|第.{1,3}[条个项]|改|挪|延|提前|推后|取消|删除|安排|查|看看", msg.text)
        )
        work_intent = document_intent or calendar_intent or bool(context and re.search(r"改名|改成|追加|替换|总结|刚才", msg.text))
        if not (links or explicit or work_intent):
            return False
        if not await self._bound(msg):
            await self._reply(msg, "【本地 UAT】当前账号不在本地测试范围或渠道绑定已变更，未访问 Work 数据。")
            return True
        if len(links) > 1:
            await self._reply(msg, "【本地 UAT】请一次处理一篇文档，以免修改目标不明确。")
            return True
        control = re.fullmatch(r"/work\s+(确认|取消)\s+([A-Za-z0-9_-]{22})", msg.text.strip())
        if control:
            await self._control(msg, p, *control.groups())
            return True
        if msg.text.strip() in {"/work 撤销授权", "/work 退出授权"}:
            await self.vault.revoke_shared(p)
            self._contexts.pop(p, None)
            self._calendar_dialogues.pop(p, None)
            self.calendar.contexts.pop(p, None)
            self.tools.known_events.pop(p, None)
            for pending in self.jobs.values():
                if self._identity(pending.message) == p and pending.stage in {"awaiting_authorization", "authorizing"}:
                    self._states.pop(pending.state, None)
                    pending.stage = "cancelled"
            for plan in self.tools.plans.values():
                if plan.principal == p and plan.stage == "pending":
                    plan.stage = "cancelled"
            await self._reply(msg, "【本地 UAT】本地个人授权和待确认操作已清除。Work 侧撤销请到应用授权管理操作。")
            return True
        if explicit and msg.text.strip() in {"/work", "/work 帮助"}:
            await self._reply(
                msg,
                "【本地 UAT】支持文档读取/总结、新建空白文档、改名、追加文字、替换完整无格式段落；以及个人主日历的日程查询/新建/编辑。"
                "发送链接及具体要求即可；写入先预览，再回复 /work 确认 确认码。取消用 /work 取消 确认码；清除本地授权用 /work 撤销授权。暂不支持删除、分享、参会邀请和会议室预约。",
            )
            return True
        source = msg.thread_ts or msg.metadata.get("message_id")
        if not source:
            raise WorkError("missing_message_identity")
        seen_key = (p, source)
        if seen_key in self._seen:
            return True
        active = [j for j in self.jobs.values() if j.stage in {"awaiting_authorization", "authorizing", "processing"}]
        if not self.store and (len(active) >= self.settings.max_jobs or sum(self._identity(j.message) == p for j in active) >= self.settings.per_user_jobs):
            await self._reply(msg, "【本地 UAT】任务已达上限，请稍后再试。")
            return True
        doc = "" if calendar_intent else links[0][0] if links else context[1] if context else ""
        if document_intent:
            self._calendar_dialogues.pop(p, None)
            self.calendar.contexts.pop(p, None)
        if links:
            self._contexts[p] = (time.monotonic() + 900, doc)
        scope = CALENDAR_AUTH_SCOPE if calendar_intent else DOCUMENT_SCOPE
        job = DocumentJob(replace(msg, metadata=dict(msg.metadata), files=[]), doc, self.tools.doc_url(doc) if doc else "", time.monotonic() + 600, scope)
        ticket = secrets.token_urlsafe(32)
        if not await self._register_job(ticket, job, p, source):
            return True
        self.jobs[ticket] = job
        self._seen[seen_key] = ticket
        try:
            grant = await self.vault.require(p, scope)
        except WorkError as exc:
            if exc.stage not in {"authorization_required", "scope_not_granted"}:
                raise
            await self._reply(
                msg,
                "【本地 UAT】需要您个人授权；这不是重新绑定渠道。请在本机浏览器使用与消息发送者相同的 UAT 账号授权。\n\n"
                + (f"本次申请日历读取和更新权限：{CALENDAR_AUTH_SCOPE}。\n本地仅开放：{CALENDAR_CONSENT_DESCRIPTION}。Work 的更新权限较宽，但本地不执行删除、邀请他人或会议室操作。\n\n" if scope == CALENDAR_AUTH_SCOPE else "")
                + f"[授权并继续]({self.settings.authorize_entry}?{urlencode({'ticket': ticket})})\n\n链接 10 分钟有效。授权不会直接执行写入，所有修改仍需单独确认。",
                final=True,
            )
        else:
            self._schedule(job, grant)
        return True

    async def _register_job(self, ticket, job, principal, source):
        return True

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
            if plan and plan.stage == "completed":
                action = Action.model_validate_json(plan.payload_json)
                if action.operation.startswith("event_"):
                    from .capabilities import parse_time

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
            if await self._bound(msg):
                await self._reply(
                    msg, f"【本地 UAT】操作未确认成功（{safe}）。" + ("请求可能已经生效，请到 Work 核查；不会自动重试。" + ("\n回执：" + plan.receipt if plan.receipt else "") if uncertain else "未执行新的写入，请重新请求预览。")
                )

    async def authorize(self, ticket, browser_session):
        self._prune()
        job = self.jobs.get(ticket)
        if not job or job.stage != "awaiting_authorization" or not await self._bound(job.message):
            raise WorkError("invalid_or_expired_ticket")
        if job.stage != "awaiting_authorization":
            raise WorkError("invalid_or_expired_ticket")
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
            if error or not code or not await self._bound(job.message):
                raise WorkError("authorization_or_binding_invalid")
            data = await self._api("token", payload={"grant_type": "authorization_code", "client_id": self.app_id, "client_secret": self._secret, "code": code, "redirect_uri": self.settings.callback_url})
            tokens = data.get("data") or data
            token, expires = tokens.get("access_token"), tokens.get("expires_in")
            if not isinstance(token, str) or not token or type(expires) not in {int, float} or not 30 < expires <= 86400:
                raise WorkError("token_response_invalid")
            info = await self._api("identity", token=token)
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
        await self.vault.install(self._identity(job.message), grant)

    def _schedule(self, job, grant):
        job.stage = "processing"
        task = asyncio.create_task(self._run(job))
        self._tasks.add(task)
        task.add_done_callback(self._done)

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
            if await self._bound(job.message):
                await self._reply(job.message, "【本地 UAT】仍在处理，请稍候；若需要写入，会先给出修改预览，不会自行修改。", final=False)

    def _remember_question(self, p, request, question):
        old = self._calendar_dialogues.get(p)
        turns = [*(old.turns if old else []), {"user": request[:1500], "question": question[:1500]}][-4:]
        self._calendar_dialogues[p] = CalendarDialogue(time.monotonic() + 600, turns)

    async def _plan_answer(self, job, p):
        known = self.tools.known_events.get(p, (0, set()))
        dialogue = self._calendar_dialogues.get(p)
        calendar_context = {**self.calendar.facts(p), "pending_dialogue": dialogue.turns if dialogue and dialogue.expires_at > time.monotonic() else []}
        action = await self._planner(job.message.text, job.document_id, sorted(known[1]) if known[0] > time.monotonic() else [], calendar_context=calendar_context if job.scope == CALENDAR_AUTH_SCOPE else {})
        if isinstance(action, CalendarIntent):
            if job.scope != CALENDAR_AUTH_SCOPE:
                return "请明确说明日历操作，重新发送日程请求以核对对应授权；本次未修改。"
            resolved = await self.calendar.resolve(p, action)
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
            answer = await self._summarize(job.message.text, title, content) if content.strip() else "文档正文为空。"
            return f"《{title[:200]}》\n{answer}\n来源：{self.tools.doc_url(action.document_id)}\n（未修改原文档）"
        if action.operation == "events_list":
            return await self.tools.events(p, action)
        plan = await self.tools.prepare(p, action)
        return "待确认修改（尚未执行，5 分钟有效）：\n" + plan.preview + f"\n\n确认请单独回复：\n/work 确认 {plan.id}\n取消：\n/work 取消 {plan.id}"

    async def _run(self, job):
        heartbeat = None
        stop = asyncio.Event()
        try:
            async with asyncio.timeout(240):
                await self._reply(job.message, "【本地 UAT】请求已接收，正在核对个人授权并处理……", final=False)
                heartbeat = asyncio.create_task(self._heartbeat(job, stop))
                p = self._identity(job.message)
                lock = self._principal_locks.setdefault(p, asyncio.Lock())
                async with lock, self._semaphore:
                    await self.vault.require(p, job.scope)
                    answer = await self._plan_answer(job, p)
                    stop.set()
                    await asyncio.gather(heartbeat, return_exceptions=True)
                    heartbeat = None
                    await self.vault.require(p, job.scope)
                    if await self._bound(job.message):
                        await self._reply(job.message, "【本地 UAT】" + answer)
                    job.stage = "completed"
        except asyncio.CancelledError:
            job.stage = "cancelled"
            raise
        except Exception as exc:
            job.stage = "failed"
            if heartbeat:
                stop.set()
                await asyncio.gather(heartbeat, return_exceptions=True)
                heartbeat = None
            facts = f"阶段：{exc.stage}" if isinstance(exc, WorkError) else "阶段：model_or_transport"
            if isinstance(exc, WorkError):
                facts += f"；HTTP：{exc.http_status}；错误码：{exc.business_code}"
                if exc.stage == "authorization_required":
                    facts += "；个人授权已过期，请重新发送请求获取授权（不需要解绑渠道）"
                elif exc.business_code == 99991679:
                    facts += "；当前用户授权未获接口所需权限，请重新发送请求进行重新授权。如仍失败，请让 ITC 核对 UAT 应用的对应用户身份权限和发布状态。"
                elif exc.business_code == 99991672:
                    facts += "；应用未开通接口权限，请让 ITC 核对 UAT 应用权限和发布状态，不能仅靠重新授权解决。"
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
        self.jobs.clear()
        self._states.clear()
        self._seen.clear()
        self._contexts.clear()
        self._calendar_dialogues.clear()
        self.calendar.contexts.clear()
        self._principal_locks.clear()
        self.tools.plans.clear()
        self.tools.known_events.clear()
        if self._owns_api:
            await self._api.close()


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

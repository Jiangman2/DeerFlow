"""Feishu/Lark channel — connects to Feishu via WebSocket (no public IP needed)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Any, Literal

from app.channels.audit import (
    ACTION_MESSAGE_SKIPPED,
    schedule_channel_audit,
)
from app.channels.base import Channel
from app.channels.commands import is_known_channel_command, strip_leading_mentions
from app.channels.connection_identity import attach_connection_identity
from app.channels.feishu_transport import observed_client
from app.channels.message_bus import (
    PENDING_CLARIFICATION_METADATA_KEY,
    RESOLVED_FROM_PENDING_CLARIFICATION_METADATA_KEY,
    InboundMessage,
    InboundMessageType,
    MessageBus,
    OutboundMessage,
    ResolvedAttachment,
)
from deerflow.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from deerflow.constants import ENTERPRISE_PRODUCT_NAME
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.sandbox.sandbox_provider import get_sandbox_provider

logger = logging.getLogger(__name__)
PENDING_CLARIFICATION_TTL_SECONDS = 30 * 60
FEISHU_INBOUND_BATCH_WINDOW_SECONDS = 0.75
SOURCE_PREVIEW_METADATA_KEY = "feishu_source_preview"
# A quoted message is injected as plain text so a cross-thread reply ("what does
# this mean?") reaches a model that has never seen the message it points at.
QUOTED_MESSAGE_TEXT_LIMIT = 400


def _is_feishu_command(text: str) -> bool:
    return is_known_channel_command(text)


class FeishuChannel(Channel):
    """Feishu/Lark IM channel using the ``lark-oapi`` WebSocket client.

    Configuration keys (in ``config.yaml`` under ``channels.feishu``):
        - ``app_id``: Feishu app ID.
        - ``app_secret``: Feishu app secret.
        - ``verification_token``: (optional) Event verification token.

    The channel uses WebSocket long-connection mode so no public IP is required.

    Message flow:
        1. User sends a message → bot adds "OK" emoji reaction
        2. Bot replies with a card: "Working on it......"
        3. Agent processes the message and returns a result
        4. Bot updates the card with the result
        5. Bot adds "DONE" emoji reaction to the original message
    """

    # Bot-identity lookup policy. The group @-mention gate fails closed while
    # our own ``open_id`` is unknown, so a transient ``bot/v3/info`` failure
    # must heal on its own instead of muting the bot until the next restart.
    _BOT_OPEN_ID_FETCH_ATTEMPTS = 3
    _BOT_OPEN_ID_RETRY_BACKOFF_SECONDS = 2.0
    _BOT_OPEN_ID_RETRY_COOLDOWN_SECONDS = 60.0

    def __init__(self, bus: MessageBus, config: dict[str, Any]) -> None:
        super().__init__(name="feishu", bus=bus, config=config)
        self._thread: threading.Thread | None = None
        self._ws_loop: asyncio.AbstractEventLoop | None = None
        self._ws_client = None
        self._ws_connected = threading.Event()
        self._ws_stop_requested = threading.Event()
        self._ws_failure = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._api_client = None
        self._CreateMessageReactionRequest = None
        self._CreateMessageReactionRequestBody = None
        self._Emoji = None
        self._PatchMessageRequest = None
        self._PatchMessageRequestBody = None
        self._background_tasks: set[asyncio.Task] = set()
        self._running_card_ids: dict[str, str] = {}
        self._running_card_tasks: dict[str, asyncio.Task] = {}
        self._prepared_inbound: OrderedDict[tuple[str, str, str], float] = OrderedDict()
        self._pending_clarifications: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._pending_inbound_batches: dict[tuple[str, str], dict[str, Any]] = {}
        self._CreateFileRequest = None
        self._CreateFileRequestBody = None
        self._CreateImageRequest = None
        self._CreateImageRequestBody = None
        self._GetMessageResourceRequest = None
        self._GetMessageRequest = None
        self._thread_lock = threading.Lock()
        # In group chats, only respond when the bot itself is @-mentioned.
        # Defaults to True because the majority of Feishu group bots are
        # expected to stay silent unless explicitly addressed. Set to False
        # in ``channels.feishu`` to opt back into "reply to everything".
        self._mention_only_in_group: bool = bool(config.get("mention_only_in_group", True))
        # Bot's own open_id, resolved via ``/open-apis/bot/v3/info``.
        # Used to precisely detect whether an inbound ``mentions[]`` entry
        # targets this bot. ``None`` before resolution — the group gate then
        # fails closed (see ``_message_mentions_bot``) and schedules a retry.
        self._bot_open_id: str | None = None
        self._bot_open_id_lock = threading.Lock()
        self._bot_open_id_next_retry_at: float = 0.0
        self._bot_credentials: tuple[str, str, str] | None = None

    @staticmethod
    def _non_empty_str(value: Any) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    @staticmethod
    def _pending_key(chat_id: str, user_id: str) -> tuple[str, str]:
        return (chat_id, user_id)

    @staticmethod
    def _should_include_source_preview(
        *,
        chat_type: str | None,
        root_id: str | None,
        parent_id: str | None,
        thread_id: str | None,
    ) -> bool:
        if chat_type == "p2p":
            return False
        return bool(root_id or parent_id or thread_id)

    @staticmethod
    def _compact_source_preview(text: str) -> str | None:
        stripped = text.strip()
        if not stripped:
            return None

        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        if not lines:
            return None
        preview = "\n".join(lines[:3])
        if len(preview) > 240:
            preview = preview[:237].rstrip() + "..."
        return preview

    @classmethod
    def _compose_card_text(cls, text: str, metadata: dict[str, Any] | None = None) -> str:
        """Compose the final card markdown body.

        Two decorations are applied on top of the raw *text* when the
        corresponding metadata is present:

        1. **Group-reply @ prefix**: when ``metadata['chat_type'] == 'group'``
           and ``metadata['user_id']`` holds the asker's ``open_id``, prepend
           ``<at id={open_id}></at>`` so the asker gets a mention notification.
           This is the Feishu card-markdown @ syntax; P2P replies skip it to
           avoid noise in DMs.
        2. **Quoted source preview**: when ``metadata[SOURCE_PREVIEW_METADATA_KEY]``
           is set (a reply/topic continuation in a group), inline the first few
           lines of the source message as a markdown blockquote so the reader
           can see which turn we're answering.
        """
        preview = None
        at_prefix = ""
        if isinstance(metadata, dict):
            raw_preview = metadata.get(SOURCE_PREVIEW_METADATA_KEY)
            if isinstance(raw_preview, str) and raw_preview.strip():
                preview = raw_preview.strip()

            chat_type = metadata.get("chat_type")
            sender_open_id = metadata.get("user_id")
            if chat_type == "group" and isinstance(sender_open_id, str) and sender_open_id:
                # Card markdown @ syntax: ``<at id=ou_xxx></at>``. The trailing
                # blank line keeps the mention visually separated from the body.
                at_prefix = f"<at id={sender_open_id}></at>\n\n"

        if preview:
            quoted_preview = "\n".join(f"> {line}" for line in preview.splitlines())
            return f"{at_prefix}{quoted_preview}\n\n{text}"
        if at_prefix:
            return f"{at_prefix}{text}"
        return text

    async def _request_bot_open_id(self, app_id: str, app_secret: str, domain: str) -> str | None:
        """Single ``bot/v3/info`` attempt. Returns ``None`` on any failure."""
        try:
            import httpx
        except ImportError:
            logger.warning("[Feishu] httpx not available; cannot resolve bot open_id")
            return None

        base = (domain or "https://open.feishu.cn").rstrip("/")
        async with httpx.AsyncClient(timeout=10.0) as client:
            token_resp = await client.post(
                f"{base}/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
            token_data = token_resp.json()
            if token_data.get("code") != 0:
                logger.warning(
                    "[Feishu] tenant_access_token fetch failed: code=%s msg=%s",
                    token_data.get("code"),
                    token_data.get("msg"),
                )
                return None
            token = token_data.get("tenant_access_token")
            if not isinstance(token, str) or not token:
                logger.warning("[Feishu] tenant_access_token missing in response")
                return None

            info_resp = await client.get(
                f"{base}/open-apis/bot/v3/info",
                headers={"Authorization": f"Bearer {token}"},
            )
            info_data = info_resp.json()
            if info_data.get("code") != 0:
                logger.warning(
                    "[Feishu] bot/v3/info failed: code=%s msg=%s",
                    info_data.get("code"),
                    info_data.get("msg"),
                )
                return None
            bot = info_data.get("bot") or {}
            open_id = bot.get("open_id")
            if not isinstance(open_id, str) or not open_id:
                logger.warning("[Feishu] bot/v3/info returned no open_id: %s", info_data)
                return None
            logger.info(
                "[Feishu] bot open_id resolved: %s (app_name=%s)",
                open_id,
                bot.get("app_name"),
            )
            return open_id

    async def _fetch_bot_open_id(self, app_id: str, app_secret: str, domain: str) -> None:
        """Resolve the bot's own ``open_id`` via ``/open-apis/bot/v3/info``.

        Cached in ``self._bot_open_id`` and used by ``_message_mentions_bot`` to
        precisely detect whether an inbound group message @-mentions this bot.
        Transient failures are retried a bounded number of times; while the id
        stays unknown the group gate fails closed rather than guessing.
        """
        for attempt in range(1, self._BOT_OPEN_ID_FETCH_ATTEMPTS + 1):
            try:
                open_id = await self._request_bot_open_id(app_id, app_secret, domain)
            except Exception:
                logger.exception(
                    "[Feishu] failed to fetch bot open_id (attempt %d/%d)",
                    attempt,
                    self._BOT_OPEN_ID_FETCH_ATTEMPTS,
                )
                open_id = None
            if open_id:
                with self._bot_open_id_lock:
                    self._bot_open_id = open_id
                return
            if attempt < self._BOT_OPEN_ID_FETCH_ATTEMPTS:
                await asyncio.sleep(self._BOT_OPEN_ID_RETRY_BACKOFF_SECONDS * attempt)
        logger.warning(
            "[Feishu] bot open_id unresolved after %d attempts; group messages stay ignored until a retry succeeds",
            self._BOT_OPEN_ID_FETCH_ATTEMPTS,
        )

    def _schedule_bot_open_id_refresh(self) -> None:
        """Retry the bot-identity lookup in the background (cooldown-bounded).

        Called from the group gate whenever our own ``open_id`` is unknown.
        That gate fails closed, so without a retry a transient ``bot/v3/info``
        failure would silently mute the bot in every group chat until the
        process restarted. The cooldown keeps a persistently broken deployment
        from hammering the Feishu API.
        """
        with self._bot_open_id_lock:
            if self._bot_open_id:
                return
            credentials = self._bot_credentials
            now = time.monotonic()
            if credentials is None or now < self._bot_open_id_next_retry_at:
                return
            self._bot_open_id_next_retry_at = now + self._BOT_OPEN_ID_RETRY_COOLDOWN_SECONDS

        loop = self._main_loop
        if loop is None or not loop.is_running():
            return
        try:
            future = asyncio.run_coroutine_threadsafe(self._fetch_bot_open_id(*credentials), loop)
        except RuntimeError:
            logger.debug("[Feishu] cannot schedule bot open_id retry", exc_info=True)
            return
        future.add_done_callback(lambda done: self._log_future_error(done, "fetch_bot_open_id", "<retry>"))

    def _message_mentions_bot(self, message) -> bool:
        """Return True only when *message* @-mentions *this* bot.

        The match is an exact ``mention.id.open_id == self._bot_open_id``
        comparison. While our own ``open_id`` is unknown the answer is
        ``False``: heuristics such as "a bot mention carries no ``user_id``"
        also match every *other* bot in the group, which would make this bot
        answer messages addressed to someone else. ``_on_message`` schedules a
        background retry of the lookup in that state.
        """
        if not self._bot_open_id:
            return False

        mentions = getattr(message, "mentions", None) or []
        if not mentions:
            return False

        bot_open_id = self._bot_open_id
        for mention in mentions:
            mention_id = getattr(mention, "id", None)
            if mention_id is None:
                continue
            if getattr(mention_id, "open_id", None) == bot_open_id:
                return True
        return False

    def _content_mentions_bot(self, content: Any) -> bool:
        """Return True when a rich-text (post) body @-mentions this bot.

        ``im.message.receive_v1`` fills ``message.mentions`` for plain text
        messages, but rich-text bodies carry the @ inline as
        ``{"tag": "at", "user_id": "<open_id>"}`` elements. Without this
        fallback an @-mention written inside a post would be dropped by the
        group gate even though the bot was explicitly addressed.
        """
        if not self._bot_open_id or not isinstance(content, dict):
            return False

        bot_open_id = self._bot_open_id
        for paragraph in content.get("content") or []:
            if not isinstance(paragraph, list):
                continue
            for element in paragraph:
                if not isinstance(element, dict) or element.get("tag") != "at":
                    continue
                if element.get("user_id") == bot_open_id or element.get("open_id") == bot_open_id:
                    return True
        return False

    @property
    def supports_streaming(self) -> bool:
        return True

    @property
    def is_running(self) -> bool:
        if not self._running:
            return False
        return bool(self._api_client) if self.config.get("_work_send_only") else self._thread is not None and self._thread.is_alive()

    @property
    def transport_connected(self) -> bool:
        """Actual successful handshake, not merely an alive SDK retry thread."""
        return bool(self.config.get("_work_receiver_ready")) if self.config.get("_work_send_only") else self._ws_connected.is_set()

    def _build_event_handler(self, lark):
        return (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_message)
            .register_p2_im_message_message_read_v1(self._on_ignored_message_event)
            .register_p2_im_message_reaction_created_v1(self._on_ignored_message_event)
            .register_p2_im_message_reaction_deleted_v1(self._on_ignored_message_event)
            .register_p2_im_message_recalled_v1(self._on_ignored_message_event)
            .build()
        )

    async def start(self) -> None:
        if self._running:
            return
        if self._thread and self._thread.is_alive():
            raise RuntimeError("Previous Work receiver has not stopped; refusing duplicate worker")
        self._ws_stop_requested.clear()
        self._ws_connected.clear()

        try:
            import lark_oapi as lark
            from lark_oapi.api.im.v1 import (
                CreateFileRequest,
                CreateFileRequestBody,
                CreateImageRequest,
                CreateImageRequestBody,
                CreateMessageReactionRequest,
                CreateMessageReactionRequestBody,
                CreateMessageRequest,
                CreateMessageRequestBody,
                Emoji,
                GetMessageRequest,
                GetMessageResourceRequest,
                PatchMessageRequest,
                PatchMessageRequestBody,
                ReplyMessageRequest,
                ReplyMessageRequestBody,
            )
        except ImportError:
            logger.error("lark-oapi is not installed. Install it with: uv add lark-oapi")
            return

        self._lark = lark
        self._CreateMessageRequest = CreateMessageRequest
        self._CreateMessageRequestBody = CreateMessageRequestBody
        self._ReplyMessageRequest = ReplyMessageRequest
        self._ReplyMessageRequestBody = ReplyMessageRequestBody
        self._CreateMessageReactionRequest = CreateMessageReactionRequest
        self._CreateMessageReactionRequestBody = CreateMessageReactionRequestBody
        self._Emoji = Emoji
        self._PatchMessageRequest = PatchMessageRequest
        self._PatchMessageRequestBody = PatchMessageRequestBody
        self._CreateFileRequest = CreateFileRequest
        self._CreateFileRequestBody = CreateFileRequestBody
        self._CreateImageRequest = CreateImageRequest
        self._CreateImageRequestBody = CreateImageRequestBody
        self._GetMessageResourceRequest = GetMessageResourceRequest
        self._GetMessageRequest = GetMessageRequest

        app_id = self.config.get("app_id", "")
        app_secret = self.config.get("app_secret", "")
        domain = self.config.get("domain", "https://open.feishu.cn")

        if not app_id or not app_secret:
            logger.error("Feishu channel requires app_id and app_secret")
            return

        self._bot_credentials = (app_id, app_secret, domain)
        self._api_client = lark.Client.builder().app_id(app_id).app_secret(app_secret).domain(domain).build()
        logger.info("[Feishu] using domain: %s", domain)
        self._main_loop = asyncio.get_event_loop()

        self._running = True
        self.bus.subscribe_outbound(self._on_outbound)

        if self.config.get("_work_send_only"):
            logger.info("Work HTTP sender ready; another process owns the receiver lease")
            return

        # Resolve the bot's own open_id so ``_message_mentions_bot`` can do an
        # exact ``message.mentions[].id.open_id`` match. Fire-and-forget; while
        # it is unknown the group gate fails closed and schedules a retry, so a
        # transient failure here never lets the bot answer another bot's @.
        if self._mention_only_in_group:
            bot_info_task = asyncio.create_task(self._fetch_bot_open_id(app_id, app_secret, domain))
            self._track_background_task(bot_info_task, name="fetch_bot_open_id", msg_id="<startup>")

        # Both ws.Client construction and start() must happen in a dedicated
        # thread with its own event loop.  lark-oapi caches the running loop
        # at construction time and later calls loop.run_until_complete(),
        # which conflicts with an already-running uvloop.
        self._thread = threading.Thread(
            target=self._run_ws,
            args=(app_id, app_secret, domain),
            daemon=True,
        )
        self._thread.start()
        logger.info("Feishu channel started")

    def _run_ws(self, app_id: str, app_secret: str, domain: str) -> None:
        """Construct and run the lark WS client in a thread with a fresh event loop.

        The lark-oapi SDK captures a module-level event loop at import time
        (``lark_oapi.ws.client.loop``).  When uvicorn uses uvloop, that
        captured loop is the *main* thread's uvloop — which is already
        running, so ``loop.run_until_complete()`` inside ``Client.start()``
        raises ``RuntimeError``.

        We work around this by creating a plain asyncio event loop for this
        thread and patching the SDK's module-level reference before calling
        ``start()``.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._ws_loop = loop

        def guarded_task_factory(worker_loop, coroutine, **kwargs):
            task = asyncio.Task(coroutine, loop=worker_loop, **kwargs)
            # SDK start() creates ping/select tasks *after* its handshake
            # run_until_complete returns. stop() can race in that gap and
            # otherwise cancel only the old task snapshot, leaving new tasks
            # running forever. Fence all newly created SDK tasks after stop.
            if self._ws_stop_requested.is_set():
                task.cancel()
            return task

        loop.set_task_factory(guarded_task_factory)
        try:
            import lark_oapi as lark
            import lark_oapi.ws.client as _ws_client_mod

            # Replace the SDK's module-level loop so Client.start() uses
            # this thread's (non-running) event loop instead of the main
            # thread's uvloop.
            _ws_client_mod.loop = loop

            event_handler = self._build_event_handler(lark)
            client_type = observed_client(lark.ws.Client, self)
            ws_client = client_type(
                app_id=app_id,
                app_secret=app_secret,
                event_handler=event_handler,
                log_level=lark.LogLevel.INFO,
                domain=domain,
            )
            self._ws_client = ws_client
            if self._ws_stop_requested.is_set():
                return
            ws_client.start()
        except asyncio.CancelledError:
            pass
        except Exception:
            if self._running:
                logger.error("Feishu WebSocket worker stopped; handshake=%s", self._ws_failure)
        finally:
            loop.set_task_factory(None)  # Cleanup coroutines must still run.
            self._ws_connected.clear()
            self._running = False
            if self._ws_client is not None:
                self._ws_client._auto_reconnect = False
            tasks = asyncio.all_tasks(loop)
            for task in tasks:
                task.cancel()
            if tasks:
                loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            if self._ws_client is not None:
                connection = getattr(self._ws_client, "_conn", None)
                try:
                    loop.run_until_complete(asyncio.wait_for(self._ws_client._disconnect(), timeout=5))
                except Exception:
                    logger.warning("Work WebSocket shutdown required transport abort")
                finally:
                    transport = getattr(connection, "transport", None)
                    if transport is not None:
                        transport.abort()
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            self._ws_client = None

    def _on_ignored_message_event(self, event) -> None:
        logger.debug("[Feishu] ignoring non-content message event: %s", type(event).__name__)

    async def stop(self) -> None:
        self._running = False
        self._ws_stop_requested.set()
        self._ws_connected.clear()
        self.bus.unsubscribe_outbound(self._on_outbound)
        for task in list(self._background_tasks):
            task.cancel()
        self._background_tasks.clear()
        for task in list(self._running_card_tasks.values()):
            task.cancel()
        self._running_card_tasks.clear()
        if self._thread:

            def cancel_worker():
                if self._ws_client is not None:
                    self._ws_client._auto_reconnect = False
                for task in asyncio.all_tasks(self._ws_loop):
                    task.cancel()

            if self._ws_loop is not None and not self._ws_loop.is_closed():
                try:
                    self._ws_loop.call_soon_threadsafe(cancel_worker)
                except RuntimeError:
                    pass  # The worker completed between the check and scheduling.
            await asyncio.to_thread(self._thread.join, 15)
            if self._thread.is_alive():
                raise RuntimeError("Work receiver did not stop; refusing a second worker")
            self._thread = None
        logger.info("Feishu channel stopped")

    async def send(self, msg: OutboundMessage, *, _max_retries: int = 3) -> None:
        if not self._api_client:
            logger.warning("[Feishu] send called but no api_client available")
            return

        logger.info(
            "[Feishu] sending reply: chat_id=%s, thread_ts=%s, text_len=%d",
            msg.chat_id,
            msg.thread_ts,
            len(msg.text),
        )

        await self._send_with_retry(
            lambda: self._send_card_message(msg),
            max_retries=_max_retries,
            log_prefix="[Feishu]",
        )

    async def send_file(self, msg: OutboundMessage, attachment: ResolvedAttachment) -> bool:
        if not self._api_client:
            return False

        # Check size limits (image: 10MB, file: 30MB)
        if attachment.is_image and attachment.size > 10 * 1024 * 1024:
            logger.warning("[Feishu] image too large (%d bytes), skipping: %s", attachment.size, attachment.filename)
            return False
        if not attachment.is_image and attachment.size > 30 * 1024 * 1024:
            logger.warning("[Feishu] file too large (%d bytes), skipping: %s", attachment.size, attachment.filename)
            return False

        try:
            if attachment.is_image:
                file_key = await self._upload_image(attachment.actual_path)
                msg_type = "image"
                content = json.dumps({"image_key": file_key})
            else:
                file_key = await self._upload_file(attachment.actual_path, attachment.filename)
                msg_type = "file"
                content = json.dumps({"file_key": file_key})

            if msg.thread_ts:
                request = self._ReplyMessageRequest.builder().message_id(msg.thread_ts).request_body(self._ReplyMessageRequestBody.builder().msg_type(msg_type).content(content).build()).build()
                await asyncio.to_thread(self._api_client.im.v1.message.reply, request)
            else:
                request = self._CreateMessageRequest.builder().receive_id_type("chat_id").request_body(self._CreateMessageRequestBody.builder().receive_id(msg.chat_id).msg_type(msg_type).content(content).build()).build()
                await asyncio.to_thread(self._api_client.im.v1.message.create, request)

            logger.info("[Feishu] file sent: %s (type=%s)", attachment.filename, msg_type)
            return True
        except Exception:
            logger.exception("[Feishu] failed to upload/send file: %s", attachment.filename)
            return False

    async def _upload_image(self, path) -> str:
        """Upload an image to Feishu and return the image_key."""
        with open(str(path), "rb") as f:
            request = self._CreateImageRequest.builder().request_body(self._CreateImageRequestBody.builder().image_type("message").image(f).build()).build()
            response = await asyncio.to_thread(self._api_client.im.v1.image.create, request)
        if not response.success():
            raise RuntimeError(f"Feishu image upload failed: code={response.code}, msg={response.msg}")
        return response.data.image_key

    async def _upload_file(self, path, filename: str) -> str:
        """Upload a file to Feishu and return the file_key."""
        suffix = path.suffix.lower() if hasattr(path, "suffix") else ""
        if suffix in (".xls", ".xlsx", ".csv"):
            file_type = "xls"
        elif suffix in (".ppt", ".pptx"):
            file_type = "ppt"
        elif suffix == ".pdf":
            file_type = "pdf"
        elif suffix in (".doc", ".docx"):
            file_type = "doc"
        else:
            file_type = "stream"

        with open(str(path), "rb") as f:
            request = self._CreateFileRequest.builder().request_body(self._CreateFileRequestBody.builder().file_type(file_type).file_name(filename).file(f).build()).build()
            response = await asyncio.to_thread(self._api_client.im.v1.file.create, request)
        if not response.success():
            raise RuntimeError(f"Feishu file upload failed: code={response.code}, msg={response.msg}")
        return response.data.file_key

    async def receive_file(self, msg: InboundMessage, thread_id: str, *, user_id: str | None = None) -> InboundMessage:
        """Download a Feishu file into the thread uploads directory.

        Returns the sandbox virtual path when the image is persisted successfully.
        """
        if not msg.thread_ts:
            logger.warning("[Feishu] received file message without thread_ts, cannot associate with conversation: %s", msg)
            return msg
        files = msg.files
        if not files:
            logger.warning("[Feishu] received message with no files: %s", msg)
            return msg
        text = msg.text
        for file in files:
            if file.get("image_key"):
                virtual_path = await self._receive_single_file(msg.thread_ts, file["image_key"], "image", thread_id, user_id=user_id)
                text = text.replace("[image]", virtual_path, 1)
            elif file.get("file_key"):
                virtual_path = await self._receive_single_file(msg.thread_ts, file["file_key"], "file", thread_id, user_id=user_id)
                text = text.replace("[file]", virtual_path, 1)
        msg.text = text
        return msg

    async def _receive_single_file(
        self,
        message_id: str,
        file_key: str,
        type: Literal["image", "file"],
        thread_id: str,
        *,
        user_id: str | None = None,
    ) -> str:
        request = self._GetMessageResourceRequest.builder().message_id(message_id).file_key(file_key).type(type).build()

        def inner():
            return self._api_client.im.v1.message_resource.get(request)

        try:
            response = await asyncio.to_thread(inner)
        except Exception:
            logger.exception("[Feishu] resource get request failed for resource_key=%s type=%s", file_key, type)
            return f"Failed to obtain the [{type}]"

        if not response.success():
            logger.warning(
                "[Feishu] resource get failed: resource_key=%s, type=%s, code=%s, msg=%s, log_id=%s ",
                file_key,
                type,
                response.code,
                response.msg,
                response.get_log_id(),
            )
            return f"Failed to obtain the [{type}]"

        image_stream = getattr(response, "file", None)
        if image_stream is None:
            logger.warning("[Feishu] resource get returned no file stream: resource_key=%s, type=%s", file_key, type)
            return f"Failed to obtain the [{type}]"

        try:
            content: bytes = await asyncio.to_thread(image_stream.read)
        except Exception:
            logger.exception("[Feishu] failed to read resource stream: resource_key=%s, type=%s", file_key, type)
            return f"Failed to obtain the [{type}]"

        if not content:
            logger.warning("[Feishu] empty resource content: resource_key=%s, type=%s", file_key, type)
            return f"Failed to obtain the [{type}]"

        paths = get_paths()
        effective_user_id = user_id or get_effective_user_id()
        paths.ensure_thread_dirs(thread_id, user_id=effective_user_id)
        uploads_dir = paths.sandbox_uploads_dir(thread_id, user_id=effective_user_id).resolve()

        ext = "png" if type == "image" else "bin"
        raw_filename = getattr(response, "file_name", "") or f"feishu_{file_key[-12:]}.{ext}"

        # Sanitize filename: preserve extension, replace path chars in name part
        if "." in raw_filename:
            name_part, ext = raw_filename.rsplit(".", 1)
            name_part = re.sub(r"[./\\]", "_", name_part)
            filename = f"{name_part}.{ext}"
        else:
            filename = re.sub(r"[./\\]", "_", raw_filename)
        resolved_target = uploads_dir / filename

        def down_load():
            # use thread_lock to avoid filename conflicts when writing
            with self._thread_lock:
                resolved_target.write_bytes(content)

        try:
            await asyncio.to_thread(down_load)
        except Exception:
            logger.exception("[Feishu] failed to persist downloaded resource: %s, type=%s", resolved_target, type)
            return f"Failed to obtain the [{type}]"

        virtual_path = f"{VIRTUAL_PATH_PREFIX}/uploads/{resolved_target.name}"

        try:
            sandbox_provider = get_sandbox_provider()
            sandbox_id = sandbox_provider.acquire(thread_id, user_id=effective_user_id)
            if sandbox_id != "local":
                sandbox = sandbox_provider.get(sandbox_id)
                if sandbox is None:
                    logger.warning("[Feishu] sandbox not found for thread_id=%s", thread_id)
                    return f"Failed to obtain the [{type}]"
                sandbox.update_file(virtual_path, content)
        except Exception:
            logger.exception("[Feishu] failed to sync resource into non-local sandbox: %s", virtual_path)
            return f"Failed to obtain the [{type}]"

        logger.info("[Feishu] downloaded resource mapped: file_key=%s -> %s", file_key, virtual_path)
        return virtual_path

    # -- message formatting ------------------------------------------------

    @staticmethod
    def _build_card_content(text: str) -> str:
        """Build a Feishu interactive card with markdown content.

        Feishu's interactive card format natively renders markdown, including
        headers, bold/italic, code blocks, lists, and links.
        """
        card = {
            "config": {"wide_screen_mode": True, "update_multi": True},
            "elements": [{"tag": "markdown", "content": text}],
        }
        return json.dumps(card)

    # -- reaction helpers --------------------------------------------------

    async def _add_reaction(self, message_id: str, emoji_type: str = "THUMBSUP") -> None:
        """Add an emoji reaction to a message."""
        if not self._api_client or not self._CreateMessageReactionRequest:
            return
        try:
            request = self._CreateMessageReactionRequest.builder().message_id(message_id).request_body(self._CreateMessageReactionRequestBody.builder().reaction_type(self._Emoji.builder().emoji_type(emoji_type).build()).build()).build()
            await asyncio.to_thread(self._api_client.im.v1.message_reaction.create, request)
            logger.info("[Feishu] reaction '%s' added to message %s", emoji_type, message_id)
        except Exception:
            logger.exception("[Feishu] failed to add reaction '%s' to message %s", emoji_type, message_id)

    async def _reply_card(self, message_id: str, text: str) -> str | None:
        """Reply with an interactive card and return the created card message ID."""
        if not self._api_client:
            return None

        content = self._build_card_content(text)
        request = self._ReplyMessageRequest.builder().message_id(message_id).request_body(self._ReplyMessageRequestBody.builder().msg_type("interactive").content(content).build()).build()
        response = await asyncio.to_thread(self._api_client.im.v1.message.reply, request)
        code = getattr(response, "code", None)
        if isinstance(code, int) and code != 0:
            raise RuntimeError(f"Work card reply was rejected (code={code})")
        response_data = getattr(response, "data", None)
        return getattr(response_data, "message_id", None)

    async def _create_card(self, chat_id: str, text: str) -> None:
        """Create a new card message in the target chat."""
        if not self._api_client:
            return

        content = self._build_card_content(text)
        request = self._CreateMessageRequest.builder().receive_id_type("chat_id").request_body(self._CreateMessageRequestBody.builder().receive_id(chat_id).msg_type("interactive").content(content).build()).build()
        await asyncio.to_thread(self._api_client.im.v1.message.create, request)

    async def _update_card(self, message_id: str, text: str) -> None:
        """Patch an existing card message in place."""
        if not self._api_client or not self._PatchMessageRequest:
            return

        content = self._build_card_content(text)
        request = self._PatchMessageRequest.builder().message_id(message_id).request_body(self._PatchMessageRequestBody.builder().content(content).build()).build()
        response = await asyncio.to_thread(self._api_client.im.v1.message.patch, request)
        # SDK returns business failures rather than raising; do not claim a card
        # was updated while it actually remains stuck at its initial placeholder.
        code = getattr(response, "code", None)
        if isinstance(code, int) and code != 0:
            raise RuntimeError("Work card update was rejected")

    def _track_background_task(self, task: asyncio.Task, *, name: str, msg_id: str) -> None:
        """Keep a strong reference to fire-and-forget tasks and surface errors."""
        self._background_tasks.add(task)
        task.add_done_callback(lambda done_task, task_name=name, mid=msg_id: self._finalize_background_task(done_task, task_name, mid))

    def _finalize_background_task(self, task: asyncio.Task, name: str, msg_id: str) -> None:
        self._background_tasks.discard(task)
        self._log_task_error(task, name, msg_id)

    async def _create_running_card(
        self,
        source_message_id: str,
        text: str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Create the running card and cache its message ID when available."""
        running_card_id = await self._reply_card(source_message_id, self._compose_card_text(text, metadata))
        if running_card_id:
            self._running_card_ids[source_message_id] = running_card_id
            logger.info("[Feishu] running card created: source=%s card=%s", source_message_id, running_card_id)
        else:
            logger.warning("[Feishu] running card creation returned no message_id for source=%s, subsequent updates will fall back to new replies", source_message_id)
        return running_card_id

    def _ensure_running_card_started(
        self,
        source_message_id: str,
        text: str = "thinking...",
        *,
        metadata: dict[str, Any] | None = None,
    ) -> asyncio.Task | None:
        """Start running-card creation once per source message."""
        running_card_id = self._running_card_ids.get(source_message_id)
        if running_card_id:
            return None

        running_card_task = self._running_card_tasks.get(source_message_id)
        if running_card_task:
            return running_card_task

        running_card_task = asyncio.create_task(self._create_running_card(source_message_id, text, metadata=metadata))
        self._running_card_tasks[source_message_id] = running_card_task
        running_card_task.add_done_callback(lambda done_task, mid=source_message_id: self._finalize_running_card_task(mid, done_task))
        return running_card_task

    def _finalize_running_card_task(self, source_message_id: str, task: asyncio.Task) -> None:
        if self._running_card_tasks.get(source_message_id) is task:
            self._running_card_tasks.pop(source_message_id, None)
        self._log_task_error(task, "create_running_card", source_message_id)

    async def _ensure_running_card(
        self,
        source_message_id: str,
        text: str = "thinking...",
        *,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Ensure the in-thread running card exists and track its message ID."""
        running_card_id = self._running_card_ids.get(source_message_id)
        if running_card_id:
            return running_card_id

        running_card_task = self._ensure_running_card_started(
            source_message_id,
            text,
            metadata=metadata,
        )
        if running_card_task is None:
            return self._running_card_ids.get(source_message_id)
        return await running_card_task

    async def _send_running_reply(self, message_id: str, *, metadata: dict[str, Any] | None = None) -> None:
        """Reply to a message in-thread with a running card."""
        try:
            await self._ensure_running_card(message_id, metadata=metadata)
        except Exception:
            logger.exception("[Feishu] failed to send running reply for message %s", message_id)

    async def _send_card_message(self, msg: OutboundMessage) -> None:
        """Send or update the Feishu card tied to the current request."""
        source_message_id = msg.thread_ts
        if source_message_id:
            running_card_id = self._running_card_ids.get(source_message_id)
            awaited_running_card_task = False

            if not running_card_id:
                running_card_task = self._running_card_tasks.get(source_message_id)
                if running_card_task:
                    awaited_running_card_task = True
                    running_card_id = await running_card_task

            if running_card_id:
                card_text = self._compose_card_text(msg.text, msg.metadata)
                try:
                    await self._update_card(running_card_id, card_text)
                except Exception:
                    if not msg.is_final:
                        raise
                    logger.exception(
                        "[Feishu] failed to patch running card %s, falling back to final reply",
                        running_card_id,
                    )
                    fallback_card_id = await self._reply_card(source_message_id, card_text)
                    self._remember_thread_mapping(msg, source_message_id, fallback_card_id)
                    self._remember_pending_clarification(msg, fallback_card_id)
                else:
                    self._remember_thread_mapping(msg, source_message_id, running_card_id)
                    self._remember_pending_clarification(msg, running_card_id)
                    logger.info("[Feishu] running card updated: source=%s card=%s", source_message_id, running_card_id)
            elif msg.is_final:
                final_card_id = await self._reply_card(
                    source_message_id,
                    self._compose_card_text(msg.text, msg.metadata),
                )
                self._remember_thread_mapping(msg, source_message_id, final_card_id)
                self._remember_pending_clarification(msg, final_card_id)
            elif awaited_running_card_task:
                logger.warning(
                    "[Feishu] running card task finished without message_id for source=%s, skipping duplicate non-final creation",
                    source_message_id,
                )
            else:
                created_card_id = await self._ensure_running_card(
                    source_message_id,
                    msg.text,
                    metadata=msg.metadata,
                )
                self._remember_thread_mapping(msg, source_message_id, created_card_id)

            if msg.is_final:
                self._running_card_ids.pop(source_message_id, None)
                await self._add_reaction(source_message_id, "DONE")
            return

        await self._create_card(msg.chat_id, msg.text)

    # -- internal ----------------------------------------------------------

    def _remember_thread_mapping(self, msg: OutboundMessage, *topic_ids: str | None) -> None:
        store = self.config.get("channel_store")
        if store is None or not msg.thread_id:
            return

        metadata_topic_ids = [
            msg.metadata.get("message_id"),
            msg.metadata.get("root_id"),
            msg.metadata.get("parent_id"),
            msg.metadata.get("thread_id"),
            msg.metadata.get("topic_id"),
        ]
        user_id = ""
        raw_user_id = msg.metadata.get("user_id")
        if isinstance(raw_user_id, str):
            user_id = raw_user_id
        chat_type = self._non_empty_str(msg.metadata.get("chat_type"))

        seen: set[str] = set()
        for topic_id in [*topic_ids, *metadata_topic_ids]:
            topic_id = self._scoped_topic_id(chat_type, user_id, topic_id)
            if not topic_id or topic_id in seen:
                continue
            seen.add(topic_id)
            try:
                store.set_thread_id(
                    self.name,
                    msg.chat_id,
                    msg.thread_id,
                    topic_id=topic_id,
                    user_id=user_id,
                )
            except Exception:
                logger.exception("[Feishu] failed to remember thread mapping for topic_id=%s", topic_id)

    def _remember_pending_clarification(self, msg: OutboundMessage, card_message_id: str | None) -> None:
        if not msg.is_final or msg.metadata.get(PENDING_CLARIFICATION_METADATA_KEY) is not True:
            return

        user_id = self._non_empty_str(msg.metadata.get("user_id"))
        topic_id = self._non_empty_str(msg.metadata.get("topic_id"))
        source_message_id = self._non_empty_str(msg.thread_ts) or self._non_empty_str(msg.metadata.get("message_id"))
        if not (user_id and topic_id and msg.thread_id and source_message_id and card_message_id):
            return

        key = self._pending_key(msg.chat_id, user_id)
        pending = {
            "thread_id": msg.thread_id,
            "topic_id": topic_id,
            "source_message_id": source_message_id,
            "card_message_id": card_message_id,
            "created_at": time.time(),
        }
        with self._thread_lock:
            # Plain-message clarification continuity is a short-lived in-memory
            # hint; explicit Feishu replies are still covered by persisted
            # message-id mappings.
            self._pending_clarifications.setdefault(key, []).append(pending)
        logger.info(
            "[Feishu] pending clarification remembered: chat_id=%s user_id=%s topic_id=%s thread_id=%s",
            msg.chat_id,
            user_id,
            topic_id,
            msg.thread_id,
        )

    def _consume_pending_clarification(self, chat_id: str, user_id: str) -> dict[str, Any] | None:
        key = self._pending_key(chat_id, user_id)
        with self._thread_lock:
            pending_items = self._pending_clarifications.get(key)
            if not pending_items:
                return None

            now = time.time()
            while pending_items:
                pending = pending_items.pop(0)
                created_at = pending.get("created_at")
                if isinstance(created_at, (int, float)) and now - created_at <= PENDING_CLARIFICATION_TTL_SECONDS:
                    if pending_items:
                        self._pending_clarifications[key] = pending_items
                    else:
                        self._pending_clarifications.pop(key, None)
                    return pending
                logger.info("[Feishu] pending clarification expired: chat_id=%s user_id=%s", chat_id, user_id)

            self._pending_clarifications.pop(key, None)
            return None

    def _ensure_pending_thread_mapping(self, chat_id: str, user_id: str, pending: dict[str, Any]) -> None:
        store = self.config.get("channel_store")
        topic_id = self._non_empty_str(pending.get("topic_id"))
        thread_id = self._non_empty_str(pending.get("thread_id"))
        if store is None or not topic_id or not thread_id:
            return
        try:
            store.set_thread_id(self.name, chat_id, thread_id, topic_id=topic_id, user_id=user_id)
        except Exception:
            logger.exception("[Feishu] failed to restore pending clarification mapping for topic_id=%s", topic_id)

    def _scoped_topic_id(self, chat_type: str | None, user_id: str | None, topic_id: str | None) -> str | None:
        """Namespace a Feishu thread key by the person who started it.

        A Feishu group has ONE shared thread id for everybody who posts in the
        same topic (``thread_id``/``root_id`` are group-wide). Using that id as
        the DeerFlow topic made every participant share one conversation, so
        Bob's follow-up question was answered with Alice's history — and the
        bot replied "in Alice's @-message". Group topics are therefore scoped
        per sender: ``<sender_open_id>:<feishu_topic>``. P2P chats already have
        a per-user ``chat_id`` and keep their unscoped key.
        """
        topic_id = self._non_empty_str(topic_id)
        if not topic_id:
            return None
        user_id = self._non_empty_str(user_id)
        if chat_type != "group" or not user_id:
            return topic_id
        prefix = f"{user_id}:"
        if topic_id.startswith(prefix):
            return topic_id
        return f"{prefix}{topic_id}"

    def _resolve_topic_id(
        self,
        chat_id: str,
        msg_id: str,
        *,
        root_id: str | None,
        parent_id: str | None,
        thread_id: str | None,
        chat_type: str | None = None,
        user_id: str | None = None,
    ) -> tuple[str, bool]:
        store = self.config.get("channel_store")
        candidates = [root_id, parent_id, thread_id]

        if store is not None:
            for candidate in candidates:
                candidate = self._non_empty_str(candidate)
                if not candidate:
                    continue
                candidate = self._scoped_topic_id(chat_type, user_id, candidate)
                if not candidate:
                    continue
                try:
                    if store.get_thread_id(self.name, chat_id, topic_id=candidate):
                        return candidate, True
                except Exception:
                    logger.exception("[Feishu] failed to resolve stored topic mapping for topic_id=%s", candidate)

        fallback = self._scoped_topic_id(chat_type, user_id, root_id or msg_id) or msg_id
        return fallback, False

    @staticmethod
    def _is_batchable_file_inbound(
        *,
        msg_type: InboundMessageType,
        text: str,
        files: list[dict[str, Any]],
        root_id: str | None,
        parent_id: str | None,
        thread_id: str | None,
    ) -> bool:
        return msg_type == InboundMessageType.CHAT and text in {"[file]", "[image]"} and len(files) == 1 and not (root_id or parent_id or thread_id)

    def _schedule_prepare_inbound(
        self,
        msg_id: str,
        inbound: InboundMessage,
        *,
        source_message_ids: list[str] | None = None,
    ) -> None:
        if self._main_loop and self._main_loop.is_running():
            logger.info("[Feishu] publishing inbound message to bus (type=%s, msg_id=%s)", inbound.msg_type.value, msg_id)
            fut = asyncio.run_coroutine_threadsafe(
                self._prepare_inbound(msg_id, inbound, source_message_ids=source_message_ids),
                self._main_loop,
            )
            fut.add_done_callback(lambda f, mid=msg_id: self._log_future_error(f, "prepare_inbound", mid))
        else:
            logger.warning("[Feishu] main loop not running, cannot publish inbound message")

    def _schedule_batch_flush(self, key: tuple[str, str], source_message_id: str) -> None:
        if self._main_loop and self._main_loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self._flush_pending_inbound_batch_after(key, source_message_id), self._main_loop)
            fut.add_done_callback(lambda f, mid=source_message_id: self._log_future_error(f, "flush_inbound_batch", mid))
        else:
            logger.warning("[Feishu] main loop not running, cannot flush inbound batch")

    def _queue_file_inbound_batch(self, msg_id: str, inbound: InboundMessage) -> bool:
        key = self._pending_key(inbound.chat_id, inbound.user_id)
        should_schedule_flush = False
        expired_batch: tuple[str, InboundMessage, list[str]] | None = None

        with self._thread_lock:
            batch = self._pending_inbound_batches.get(key)
            now = time.time()
            if batch:
                if now - batch["created_at"] <= FEISHU_INBOUND_BATCH_WINDOW_SECONDS:
                    if msg_id in batch["message_ids"]:
                        return True
                    batched_inbound = batch["inbound"]
                    batch["message_ids"].append(msg_id)
                    batch["text_parts"].append(inbound.text)
                    batched_inbound.text = "\n\n".join(part for part in batch["text_parts"] if part)
                    batched_inbound.files.extend(inbound.files)
                    batched_inbound.metadata["batched_message_ids"] = list(batch["message_ids"])
                    logger.info(
                        "[Feishu] batched inbound file message: chat_id=%s user_id=%s anchor=%s msg_id=%s files=%d",
                        inbound.chat_id,
                        inbound.user_id,
                        batch["anchor_message_id"],
                        msg_id,
                        len(batched_inbound.files),
                    )
                    return True

                expired_batch = (batch["anchor_message_id"], batch["inbound"], list(batch["message_ids"]))

            self._pending_inbound_batches[key] = {
                "anchor_message_id": msg_id,
                "created_at": now,
                "inbound": inbound,
                "message_ids": [msg_id],
                "text_parts": [inbound.text],
            }
            inbound.metadata["batched_message_ids"] = [msg_id]
            should_schedule_flush = True

        if should_schedule_flush:
            self._schedule_batch_flush(key, msg_id)
        if expired_batch:
            anchor_message_id, expired_inbound, source_message_ids = expired_batch
            self._schedule_prepare_inbound(anchor_message_id, expired_inbound, source_message_ids=source_message_ids)
        return True

    def _pop_pending_inbound_batch(self, key: tuple[str, str], *, anchor_message_id: str | None = None) -> tuple[str, InboundMessage, list[str]] | None:
        with self._thread_lock:
            batch = self._pending_inbound_batches.get(key)
            if not batch:
                return None
            if anchor_message_id is not None and batch["anchor_message_id"] != anchor_message_id:
                return None
            self._pending_inbound_batches.pop(key, None)
            return batch["anchor_message_id"], batch["inbound"], list(batch["message_ids"])

    async def _flush_pending_inbound_batch_after(self, key: tuple[str, str], anchor_message_id: str) -> None:
        await asyncio.sleep(FEISHU_INBOUND_BATCH_WINDOW_SECONDS)
        batch = self._pop_pending_inbound_batch(key, anchor_message_id=anchor_message_id)
        if not batch:
            return
        anchor_message_id, inbound, source_message_ids = batch
        logger.info(
            "[Feishu] flushing inbound file batch: chat_id=%s user_id=%s anchor=%s messages=%d files=%d",
            inbound.chat_id,
            inbound.user_id,
            anchor_message_id,
            len(source_message_ids),
            len(inbound.files),
        )
        await self._prepare_inbound(anchor_message_id, inbound, source_message_ids=source_message_ids)

    @staticmethod
    def _log_task_error(task: asyncio.Task, name: str, msg_id: str) -> None:
        """Callback for background asyncio tasks to surface errors."""
        try:
            exc = task.exception()
            if exc:
                logger.error("[Feishu] %s failed for msg_id=%s: %s", name, msg_id, exc)
        except asyncio.CancelledError:
            logger.info("[Feishu] %s cancelled for msg_id=%s", name, msg_id)
        except Exception:
            pass

    async def _prepare_inbound(self, msg_id: str, inbound, *, source_message_ids: list[str] | None = None) -> None:
        """Deduplicate SDK retries before any visible reaction/placeholder."""
        guard = self.config.get("_work_receiver_guard")
        if guard is not None and not await guard():
            return
        now = time.monotonic()
        while self._prepared_inbound:
            oldest, at = next(iter(self._prepared_inbound.items()))
            if now - at <= 600 and len(self._prepared_inbound) <= 4096:
                break
            self._prepared_inbound.pop(oldest, None)
        keys = [(inbound.chat_id, inbound.user_id, value) for value in (source_message_ids or [msg_id])]
        if any(key in self._prepared_inbound for key in keys):
            return
        # Claim synchronously, before awaits, including concurrent SDK deliveries.
        for key in keys:
            self._prepared_inbound[key] = now
        try:
            inbound = await self._attach_connection_identity(inbound)
            await self._prepend_quoted_context(inbound)
            reaction_message_ids = source_message_ids or [msg_id]
            for reaction_message_id in reaction_message_ids:
                reaction_task = asyncio.create_task(self._add_reaction(reaction_message_id, "OK"))
                self._track_background_task(reaction_task, name="add_reaction", msg_id=reaction_message_id)
            self._ensure_running_card_started(msg_id, metadata=inbound.metadata)
            await self.bus.publish_inbound(inbound)
        except BaseException:
            for key in keys:
                self._prepared_inbound.pop(key, None)
            raise

    async def _attach_connection_identity(self, inbound: InboundMessage) -> InboundMessage:
        return await attach_connection_identity(
            inbound,
            repo=self._connection_repo,
            provider="feishu",
            workspace_id=inbound.chat_id,
        )

    async def _prepend_quoted_context(self, inbound: InboundMessage) -> None:
        """Give a cross-thread reply the text of the message it quotes.

        Group threads are namespaced per sender, so a colleague replying to
        somebody else's answer starts an empty DeerFlow thread. Without this
        the model would only see "what does this mean?" and have nothing to
        interpret. The quote is skipped whenever the reply already resolves
        into a thread that holds the context.
        """
        metadata = inbound.metadata or {}
        if metadata.get("chat_type") != "group":
            return
        if inbound.msg_type != InboundMessageType.CHAT or not inbound.text:
            return
        if metadata.get("resolved_from_stored_mapping"):
            return
        if metadata.get(RESOLVED_FROM_PENDING_CLARIFICATION_METADATA_KEY):
            return

        quoted_message_id = self._non_empty_str(metadata.get("parent_id")) or self._non_empty_str(metadata.get("root_id"))
        if not quoted_message_id:
            return
        quoted, from_bot = await self._fetch_quoted_text(quoted_message_id)
        if not quoted:
            return

        author = "本机器人先前的回复" if from_bot else "群里另一位同事的消息"
        inbound.text = f"[引用] {author}：{quoted}\n\n{inbound.text}"
        metadata["quoted_message_id"] = quoted_message_id

    async def _fetch_quoted_text(self, message_id: str) -> tuple[str | None, bool]:
        """Return ``(text, from_bot)`` for a quoted message."""
        if not self._api_client or not self._GetMessageRequest or not message_id:
            return None, False
        try:
            request = self._GetMessageRequest.builder().message_id(message_id).build()
            response = await asyncio.to_thread(self._api_client.im.v1.message.get, request)
            code = getattr(response, "code", None)
            if isinstance(code, int) and code != 0:
                logger.info("[Feishu] quoted message %s is unreadable (code=%s)", message_id, code)
                return None, False
            items = getattr(getattr(response, "data", None), "items", None) or []
            if not items:
                return None, False
            item = items[0]
            body = getattr(item, "body", None)
            raw = getattr(body, "content", None)
            text = self._quote_text_from_content(getattr(item, "msg_type", None), raw)
            if not text:
                return None, False
            sender = getattr(getattr(item, "sender", None), "sender_type", None)
            return text, sender == "app"
        except Exception:
            logger.warning("[Feishu] failed to read quoted message %s", message_id, exc_info=True)
            return None, False

    def _quote_text_from_content(self, msg_type: str | None, raw: Any) -> str | None:
        if not isinstance(raw, str) or not raw:
            return None
        try:
            content = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if msg_type == "text" and isinstance(content, dict):
            text = str(content.get("text") or "")
        else:
            parts: list[str] = []
            text_keys = ("text", "content", "title", "label")

            def _walk(node: Any, key: str | None = None, depth: int = 0) -> None:
                if depth > 8 or len(" ".join(parts)) > QUOTED_MESSAGE_TEXT_LIMIT * 2:
                    return
                if isinstance(node, dict):
                    for child_key, child in node.items():
                        _walk(child, child_key, depth + 1)
                elif isinstance(node, list):
                    for child in node:
                        _walk(child, key, depth + 1)
                elif isinstance(node, str) and key in text_keys:
                    parts.append(node)

            _walk(content)
            text = " ".join(part.strip() for part in parts if part and part.strip())

        collapsed = re.sub(r"\s+", " ", text).strip()
        if not collapsed:
            return None
        if len(collapsed) > QUOTED_MESSAGE_TEXT_LIMIT:
            collapsed = collapsed[: QUOTED_MESSAGE_TEXT_LIMIT - 1].rstrip() + "…"
        return collapsed

    async def _bind_connection_from_connect_code(
        self,
        *,
        message_id: str,
        chat_id: str,
        user_id: str,
        code: str,
        chat_type: str | None = None,
    ) -> bool:
        guard = self.config.get("_work_receiver_guard")
        if guard is not None and not await guard():
            return True
        if self._connection_repo is None or not code:
            return False

        state = await self._connection_repo.consume_oauth_state(provider="feishu", state=code)
        if state is None:
            await self._reply_card(message_id, "海能 Work连接码无效或已过期。")
            return True

        if not user_id or not chat_id:
            await self._reply_card(message_id, "无法通过此消息完成海能 Work连接。")
            return True

        await self._connection_repo.upsert_connection(
            owner_user_id=state["owner_user_id"],
            provider="feishu",
            external_account_id=user_id,
            workspace_id=chat_id,
            metadata={
                "chat_id": chat_id,
                "message_id": message_id,
                # Chat type is what lets the browser-side export default to the
                # caller's private chat instead of guessing from recency (a
                # group binding is often the newest row).
                "chat_type": chat_type or "",
            },
            status="connected",
        )
        await self._reply_card(message_id, f"海能 Work已连接至{ENTERPRISE_PRODUCT_NAME}。")
        return True

    def _on_message(self, event) -> None:
        """Called by lark-oapi when a message is received (runs in lark thread)."""
        if self._ws_stop_requested.is_set():
            return
        try:
            logger.info("[Feishu] raw event received: type=%s", type(event).__name__)
            message = event.event.message
            chat_id = message.chat_id
            msg_id = message.message_id
            sender_id = event.event.sender.sender_id.open_id

            root_id = getattr(message, "root_id", None) or None
            chat_type = getattr(message, "chat_type", None)
            parent_id = self._non_empty_str(getattr(message, "parent_id", None))
            feishu_thread_id = self._non_empty_str(getattr(message, "thread_id", None))

            # Parse message content
            content = json.loads(message.content)

            # files_list store the any-file-key in feishu messages, which can be used to download the file content later
            # In Feishu channel, image_keys are independent of file_keys.
            # The file_key includes files, videos, and audio, but does not include stickers.
            files_list = []

            if "text" in content:
                # Handle plain text messages
                text = content["text"]
            elif "file_key" in content:
                file_key = content.get("file_key")
                if isinstance(file_key, str) and file_key:
                    files_list.append({"file_key": file_key})
                    text = "[file]"
                else:
                    text = ""
            elif "image_key" in content:
                image_key = content.get("image_key")
                if isinstance(image_key, str) and image_key:
                    files_list.append({"image_key": image_key})
                    text = "[image]"
                else:
                    text = ""
            elif "content" in content and isinstance(content["content"], list):
                # Handle rich-text messages with a top-level "content" list (e.g., topic groups/posts)
                text_paragraphs: list[str] = []
                for paragraph in content["content"]:
                    if isinstance(paragraph, list):
                        paragraph_text_parts: list[str] = []
                        for element in paragraph:
                            if isinstance(element, dict):
                                # Include both normal text and @ mentions
                                if element.get("tag") in ("text", "at"):
                                    text_value = element.get("text", "")
                                    if text_value:
                                        paragraph_text_parts.append(text_value)
                                elif element.get("tag") == "a":
                                    href = element.get("href")
                                    label = element.get("text")
                                    if isinstance(label, str) and label:
                                        paragraph_text_parts.append(label)
                                    if isinstance(href, str) and href:
                                        paragraph_text_parts.append(href)
                                elif element.get("tag") == "img":
                                    image_key = element.get("image_key")
                                    if isinstance(image_key, str) and image_key:
                                        files_list.append({"image_key": image_key})
                                        paragraph_text_parts.append("[image]")
                                elif element.get("tag") in ("file", "media"):
                                    file_key = element.get("file_key")
                                    if isinstance(file_key, str) and file_key:
                                        files_list.append({"file_key": file_key})
                                        paragraph_text_parts.append("[file]")
                        if paragraph_text_parts:
                            # Join text segments within a paragraph with spaces to avoid "helloworld"
                            text_paragraphs.append(" ".join(paragraph_text_parts))

                # Join paragraphs with blank lines to preserve paragraph boundaries
                text = "\n\n".join(text_paragraphs)
            else:
                text = ""
            text = text.strip()

            logger.info(
                "[Feishu] parsed message: chat_id=%s, msg_id=%s, root_id=%s, parent_id=%s, thread_id=%s, chat_type=%s, sender=%s, text_len=%d",
                chat_id,
                msg_id,
                root_id,
                parent_id,
                feishu_thread_id,
                chat_type,
                sender_id,
                len(text or ""),
            )

            if not (text or files_list):
                logger.info("[Feishu] empty text, ignoring message")
                return

            # Connect-code binding MUST be checked before the group @-mention
            # gate (see Channel._pending_connect_code contract): a browser-
            # initiated ``/connect <code>`` bootstraps an external identity the
            # bot has never seen, so it cannot be required to @-mention first.
            # All other adapters (dingtalk/discord/slack/wechat/wecom) likewise
            # consume the connect code before any allowed_users/mention gate.
            connect_code = self._pending_connect_code(text)
            if connect_code:
                if self._main_loop and self._main_loop.is_running():
                    fut = asyncio.run_coroutine_threadsafe(
                        self._bind_connection_from_connect_code(
                            message_id=msg_id,
                            chat_id=chat_id,
                            user_id=sender_id,
                            code=connect_code,
                            chat_type=chat_type,
                        ),
                        self._main_loop,
                    )
                    fut.add_done_callback(lambda f, mid=msg_id: self._log_future_error(f, "bind_connection", mid))
                else:
                    logger.warning("[Feishu] main loop not running, cannot bind channel connection")
                return

            # Group-chat gate: when ``mention_only_in_group`` is enabled, drop
            # any group message that does not @-mention the bot. This is the
            # earliest safe point — we've parsed the message enough to inspect
            # ``mentions`` but have not yet emitted any side effects (no
            # reaction, no running card, no bus publish). P2P chats skip this
            # check entirely so DMs still work without @.
            #
            # Both mention shapes are accepted: ``message.mentions[]`` (plain
            # text) and ``{"tag": "at"}`` elements inside a rich-text body.
            # ``@all`` carries no matching open_id, so it never opens the gate.
            # NOTE: there is deliberately NO "pending clarification" bypass here.
            # A previous version exempted this ``(chat_id, user_id)`` from the
            # @-requirement whenever a clarification was remembered, so the user
            # could answer the bot's question without re-@ing. But the LLM ends
            # most turns with a fresh clarifying question, which re-armed the
            # exemption every turn (its TTL refreshed each time) and permanently
            # disabled the gate — the bot then answered *every* group message
            # with no @ at all. Group multi-turn clarification now requires an
            # explicit @ each turn. P2P still continues clarification answers
            # without @ because it skips this gate entirely.
            mentioned = self._message_mentions_bot(message) or self._content_mentions_bot(content)
            if chat_type == "group" and self._mention_only_in_group and not mentioned:
                if self._bot_open_id:
                    skip_reason = "mention_only_in_group"
                    audit_error = "group message without @-mention"
                else:
                    # Our own open_id is unknown, so an exact match is
                    # impossible and the gate fails closed instead of treating
                    # "any bot mention" as addressed to us. Retry the identity
                    # lookup in the background so this heals on its own.
                    skip_reason = "bot_identity_unavailable"
                    audit_error = "group message skipped while bot identity is unresolved"
                    self._schedule_bot_open_id_refresh()
                logger.debug(
                    "[Feishu] skipping group message (%s): chat_id=%s msg_id=%s sender=%s",
                    skip_reason,
                    chat_id,
                    msg_id,
                    sender_id,
                )
                # This drop happens inside the adapter, before the manager ever
                # sees the message, so it has to be audited here or it leaves no
                # trace at all. The body is deliberately NOT recorded: the
                # message was never addressed to this bot, so only identifiers
                # and the skip reason belong in the audit trail.
                schedule_channel_audit(
                    action=ACTION_MESSAGE_SKIPPED,
                    channel_name=self.name,
                    result="failure",
                    status_code=204,
                    error=audit_error,
                    details={
                        "reason": skip_reason,
                        "chat_id": chat_id,
                        "chat_type": chat_type,
                        "platform_user_id": sender_id,
                        "message_id": msg_id,
                    },
                )
                return

            # Only treat known slash commands as commands; absolute paths and
            # other slash-prefixed text should be handled as normal chat.
            command_text = strip_leading_mentions(text)
            if _is_feishu_command(command_text):
                msg_type = InboundMessageType.COMMAND
                text = command_text
            else:
                msg_type = InboundMessageType.CHAT

            # topic_id determines which LangGraph thread the message maps to.
            # P2P chats: topic_id=None so all messages share one thread (like Telegram DMs).
            # But check stored mappings first for backward compatibility with pre-upgrade P2P threads.
            # Group chats: the Feishu thread ids are group-wide, so the topic
            # is namespaced by the sender (``<open_id>:<feishu_topic>``). Two
            # people who reply in the same Feishu topic therefore land in two
            # separate DeerFlow threads and never see each other's history.
            topic_id, resolved_from_stored_mapping = self._resolve_topic_id(
                chat_id,
                msg_id,
                root_id=root_id,
                parent_id=parent_id,
                thread_id=feishu_thread_id,
                chat_type=chat_type,
                user_id=sender_id,
            )
            if chat_type == "p2p" and not resolved_from_stored_mapping:
                topic_id = None
            resolved_from_pending = False
            if msg_type == InboundMessageType.CHAT and not resolved_from_stored_mapping:
                pending = self._consume_pending_clarification(chat_id, sender_id)
                pending_topic_id = self._non_empty_str(pending.get("topic_id")) if pending else None
                if pending_topic_id:
                    topic_id = pending_topic_id
                    self._ensure_pending_thread_mapping(chat_id, sender_id, pending)
                    resolved_from_pending = True

            source_preview = None
            if self._should_include_source_preview(
                chat_type=chat_type,
                root_id=root_id,
                parent_id=parent_id,
                thread_id=feishu_thread_id,
            ):
                source_preview = self._compact_source_preview(text)

            metadata = {
                "message_id": msg_id,
                "root_id": root_id,
                "parent_id": parent_id,
                "thread_id": feishu_thread_id,
                "topic_id": topic_id,
                "user_id": sender_id,
                "chat_type": chat_type,
                RESOLVED_FROM_PENDING_CLARIFICATION_METADATA_KEY: resolved_from_pending,
                "resolved_from_stored_mapping": resolved_from_stored_mapping,
            }
            if source_preview:
                metadata[SOURCE_PREVIEW_METADATA_KEY] = source_preview

            inbound = self._make_inbound(
                chat_id=chat_id,
                user_id=sender_id,
                text=text,
                msg_type=msg_type,
                thread_ts=msg_id,
                files=files_list,
                metadata=metadata,
            )
            inbound.topic_id = topic_id

            if self._is_batchable_file_inbound(
                msg_type=msg_type,
                text=text,
                files=files_list,
                root_id=root_id,
                parent_id=parent_id,
                thread_id=feishu_thread_id,
            ):
                self._queue_file_inbound_batch(msg_id, inbound)
                return

            self._schedule_prepare_inbound(msg_id, inbound)
        except Exception:
            logger.exception("[Feishu] error processing message")

"""ChannelService — manages the lifecycle of all IM channels."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any

from app.channels.base import Channel
from app.channels.manager import DEFAULT_GATEWAY_URL, DEFAULT_LANGGRAPH_URL, ChannelManager
from app.channels.message_bus import MessageBus
from app.channels.runtime_config_store import merge_runtime_channel_configs
from app.channels.store import ChannelStore

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig
    from deerflow.config.channel_connections_config import ChannelConnectionsConfig

# Channel name → import path for lazy loading
_CHANNEL_REGISTRY: dict[str, str] = {
    "dingtalk": "app.channels.dingtalk:DingTalkChannel",
    "discord": "app.channels.discord:DiscordChannel",
    "feishu": "app.channels.feishu:FeishuChannel",
    "github": "app.channels.github:GitHubChannel",
    "slack": "app.channels.slack:SlackChannel",
    "telegram": "app.channels.telegram:TelegramChannel",
    "wechat": "app.channels.wechat:WechatChannel",
    "wecom": "app.channels.wecom:WeComChannel",
}

# Keys that indicate a user has configured credentials for a channel.
_CHANNEL_CREDENTIAL_KEYS: dict[str, list[str]] = {
    "dingtalk": ["client_id", "client_secret"],
    "discord": ["bot_token"],
    "feishu": ["app_id", "app_secret"],
    "slack": ["bot_token", "app_token"],
    "telegram": ["bot_token"],
    "wecom": ["bot_id", "bot_secret"],
    "wechat": ["bot_token"],
}

_CHANNELS_LANGGRAPH_URL_ENV = "DEER_FLOW_CHANNELS_LANGGRAPH_URL"
_CHANNELS_GATEWAY_URL_ENV = "DEER_FLOW_CHANNELS_GATEWAY_URL"


def _channel_has_credentials(name: str, channel_config: dict[str, Any]) -> bool:
    cred_keys = _CHANNEL_CREDENTIAL_KEYS.get(name, [])
    return any(not isinstance(channel_config.get(key), bool) and channel_config.get(key) is not None and str(channel_config[key]).strip() for key in cred_keys)


def _resolve_service_url(config: dict[str, Any], config_key: str, env_key: str, default: str) -> str:
    value = config.pop(config_key, None)
    if isinstance(value, str) and value.strip():
        return value
    env_value = os.getenv(env_key, "").strip()
    if env_value:
        return env_value
    return default


def _merge_channel_connection_runtime_config(channels_config: dict[str, Any], app_config: AppConfig) -> None:
    connection_config = getattr(app_config, "channel_connections", None)
    merge_runtime_channel_configs(channels_config, connection_config)
    from app.integrations.work.config import apply_work_credential_defaults

    apply_work_credential_defaults(channels_config)


def _make_connection_repo(connection_config: ChannelConnectionsConfig | None):
    if connection_config is None or not getattr(connection_config, "enabled", False):
        return None

    try:
        from deerflow.persistence.channel_connections import ChannelConnectionRepository
        from deerflow.persistence.engine import get_session_factory
    except Exception:
        logger.exception("Failed to import channel connection repository")
        return None

    session_factory = get_session_factory()
    if session_factory is None:
        logger.warning("Channel connections are enabled but database persistence is not available")
        return None
    return ChannelConnectionRepository(session_factory)


class ChannelService:
    """Manages the lifecycle of all configured IM channels.

    Reads configuration from ``config.yaml`` under the ``channels`` key,
    instantiates enabled channels, and starts the ChannelManager dispatcher.
    """

    def __init__(
        self,
        channels_config: dict[str, Any] | None = None,
        *,
        connection_repo: Any | None = None,
        require_bound_identity: bool = False,
    ) -> None:
        self.bus = MessageBus()
        self.store = ChannelStore()
        self._connection_repo = connection_repo
        config = dict(channels_config or {})
        langgraph_url = _resolve_service_url(config, "langgraph_url", _CHANNELS_LANGGRAPH_URL_ENV, DEFAULT_LANGGRAPH_URL)
        gateway_url = _resolve_service_url(config, "gateway_url", _CHANNELS_GATEWAY_URL_ENV, DEFAULT_GATEWAY_URL)
        default_session = config.pop("session", None)
        channel_sessions = {name: channel_config.get("session") for name, channel_config in config.items() if isinstance(channel_config, dict)}
        self.manager = ChannelManager(
            bus=self.bus,
            store=self.store,
            langgraph_url=langgraph_url,
            gateway_url=gateway_url,
            default_session=default_session if isinstance(default_session, dict) else None,
            channel_sessions=channel_sessions,
            connection_repo=connection_repo,
            require_bound_identity=require_bound_identity,
        )
        self._channels: dict[str, Any] = {}  # name -> Channel instance
        self._config = config
        self._running = False
        self._readiness_locks: dict[str, asyncio.Lock] = {}
        self._work_receivers: dict[str, Any] = {}

    @classmethod
    def from_app_config(cls, app_config: AppConfig | None = None) -> ChannelService:
        """Create a ChannelService from the application config."""
        if app_config is None:
            from deerflow.config.app_config import get_app_config

            app_config = get_app_config()
        channels_config = {}
        # extra fields are allowed by AppConfig (extra="allow")
        extra = app_config.model_extra or {}
        if "channels" in extra:
            channels_config = dict(extra["channels"] or {})
        _merge_channel_connection_runtime_config(channels_config, app_config)
        connection_config = getattr(app_config, "channel_connections", None)
        connections_enabled = connection_config is not None and getattr(connection_config, "enabled", False)
        require_bound_identity = bool(connections_enabled and getattr(connection_config, "require_bound_identity", True))
        return cls(
            channels_config=channels_config,
            connection_repo=_make_connection_repo(connection_config),
            require_bound_identity=require_bound_identity,
        )

    async def start(self) -> None:
        """Start the manager and all enabled channels."""
        if self._running:
            return

        await self.manager.start()
        self._running = True

        ready_status = await self.ensure_ready_channels(attempts=2)
        ready_count = sum(1 for ready in ready_status.values() if ready)
        logger.info("ChannelService started with %d/%d ready channels", ready_count, len(ready_status))

    async def ensure_ready_channels(self, *, attempts: int = 1) -> dict[str, bool]:
        """Start or restart enabled configured channels that are not ready."""
        ready_status: dict[str, bool] = {}
        for name, channel_config in self._config.items():
            if not isinstance(channel_config, dict):
                continue
            if not channel_config.get("enabled", False):
                if _channel_has_credentials(name, channel_config):
                    logger.warning(
                        "A configured channel has credentials configured but is disabled. Set enabled: true under its channels entry in config.yaml to activate it.",
                    )
                else:
                    logger.info("A configured channel is disabled, skipping")
                continue

            ready_status[name] = await self.ensure_channel_ready(name, attempts=attempts)
        return ready_status

    async def ensure_channel_ready(
        self,
        name: str,
        config: dict[str, Any] | None = None,
        *,
        attempts: int = 1,
    ) -> bool:
        """Ensure a single enabled channel is running using its current config."""
        if not self._running:
            logger.warning("ChannelService is not running; cannot ensure channel readiness")
            return False

        if config is not None:
            self._config[name] = dict(config)

        # Serialize per channel: readiness is polled from request handlers, so
        # concurrent calls must not stop/start the same channel worker twice.
        lock = self._readiness_locks.setdefault(name, asyncio.Lock())
        async with lock:
            channel_config = self._config.get(name)
            if not channel_config or not isinstance(channel_config, dict):
                logger.warning("No config for requested channel")
                return False
            if not channel_config.get("enabled", False):
                return False

            channel = self._channels.get(name)
            if channel is not None and channel.is_running:
                return True

            if channel is not None:
                try:
                    await self._stop_channel(name, channel)
                except Exception:
                    logger.exception("Error stopping non-running channel before readiness retry")
                    return False
                self._channels.pop(name, None)

            max_attempts = max(1, attempts)
            for attempt in range(max_attempts):
                if attempt > 0:
                    logger.info("Retrying channel startup after readiness check")
                if await self._start_channel(name, channel_config):
                    return True
                if name in self._channels:
                    # Failed cleanup still owns a worker; never retry over it.
                    return False
            return False

    async def stop(self) -> None:
        """Stop all channels and the manager."""
        self._running = False
        for name, channel in list(self._channels.items()):
            try:
                async with self._readiness_locks.setdefault(name, asyncio.Lock()):
                    channel = self._channels.get(name)
                    if channel is not None:
                        await self._stop_channel(name, channel)
                        self._channels.pop(name, None)
                logger.info("Channel stopped")
            except Exception:
                logger.exception("Error stopping channel")

        await self.manager.stop()
        self._running = False
        logger.info("ChannelService stopped")

    def _load_channel_config(self, name: str) -> dict[str, Any] | None:
        """Load the latest config for a specific channel from disk.

        Uses ``get_app_config()`` which detects file changes via config
        signature, so edits to ``config.yaml`` are picked up without a process
        restart.
        The UI runtime-config overlay applied at startup is re-applied here
        so a file-driven reload neither drops credentials entered from the
        browser nor resurrects a channel disconnected from it.
        Falls back to the cached ``self._config`` when config loading fails.
        """
        try:
            from deerflow.config.app_config import get_app_config

            app_config = get_app_config()
            extra = app_config.model_extra or {}
            channels_config = dict(extra.get("channels") or {})
            _merge_channel_connection_runtime_config(channels_config, app_config)
            channel_config = channels_config.get(name)
            if isinstance(channel_config, dict):
                # Update the cached config so get_status() stays consistent.
                self._config[name] = channel_config
                return channel_config
        except Exception:
            logger.exception("Failed to reload config for channel %s, using cached version", name)
        return self._config.get(name)

    async def restart_channel(self, name: str, *, reload_config: bool = True) -> bool:
        """Restart a specific channel. Returns True if successful."""
        lock = self._readiness_locks.setdefault(name, asyncio.Lock())
        async with lock:
            return await self._restart_channel_locked(name, reload_config=reload_config)

    async def _restart_channel_locked(self, name: str, *, reload_config: bool) -> bool:
        """Share the readiness lock with reconfiguration/removal requests."""
        await self._invalidate_work_integration(name)
        if name in self._channels:
            try:
                await self._stop_channel(name, self._channels[name])
            except Exception:
                logger.exception("Error stopping channel for restart")
                return False
            del self._channels[name]

        if reload_config:
            # Reading config.yaml and the runtime store is disk IO; keep it
            # off the event loop.
            config = await asyncio.to_thread(self._load_channel_config, name)
        else:
            config = self._config.get(name)
        if not config or not isinstance(config, dict):
            logger.warning("No config for requested channel")
            return False

        if not config.get("enabled", False):
            logger.info("Channel %s is disabled, skipping restart", name)
            return True

        return await self._start_channel(name, config)

    async def configure_channel(self, name: str, config: dict[str, Any]) -> bool:
        """Apply runtime config for a channel and restart it if the service is running."""
        if name == "feishu":
            from app.integrations.work.config import apply_work_credential_defaults

            selected = {name: dict(config)}
            apply_work_credential_defaults(selected)
            config = selected[name]
        self._config[name] = dict(config)
        if not self._running:
            return True
        # The caller just supplied the authoritative config (e.g. credentials
        # entered in the browser that are never written to config.yaml) — a
        # file reload here would clobber it with the stale on-disk entry.
        return await self.restart_channel(name, reload_config=False)

    async def remove_channel(self, name: str) -> bool:
        """Remove runtime config for a channel and stop it if currently running."""
        lock = self._readiness_locks.setdefault(name, asyncio.Lock())
        async with lock:
            return await self._remove_channel_locked(name)

    async def _remove_channel_locked(self, name: str) -> bool:
        await self._invalidate_work_integration(name)
        self._config.pop(name, None)
        channel = self._channels.get(name)
        if channel is None:
            return True
        try:
            await self._stop_channel(name, channel)
            self._channels.pop(name, None)
            logger.info("Channel stopped and removed")
            return True
        except Exception:
            logger.exception("Error stopping channel for removal")
            return False

    async def _invalidate_work_integration(self, name):
        if name != "feishu":
            return
        lock = getattr(self.manager, "_work_service_lock", None)
        if lock is None:
            lock = self.manager._work_service_lock = asyncio.Lock()
        async with lock:
            old = self.manager._work_documents
            self.manager._work_documents = None
            if old is not None:
                await old.close()

    async def _stop_channel(self, name, channel):
        await channel.stop()
        receiver = self._work_receivers.pop(name, None)
        if receiver:
            # Do not release the lease until the SDK thread has really joined.
            await receiver.close()

    async def _start_channel(self, name: str, config: dict[str, Any]) -> bool:
        """Instantiate and start a single channel."""
        import_path = _CHANNEL_REGISTRY.get(name)
        if not import_path:
            logger.warning("Unknown channel type")
            return False

        try:
            from deerflow.reflection import resolve_class

            channel_cls = resolve_class(import_path, base_class=None)
        except Exception:
            logger.exception("Failed to import channel class")
            return False

        channel = None
        try:
            config = dict(config)
            if name == "feishu":
                from app.integrations.work.receiver import WorkReceiver

                receiver = await WorkReceiver.prepare(self, config)
                if receiver:
                    self._work_receivers[name] = receiver
                    config = receiver.channel_config(config)
            config["channel_store"] = self.store
            if self._connection_repo is not None:
                config["connection_repo"] = self._connection_repo
            channel = channel_cls(bus=self.bus, config=config)
            self._channels[name] = channel
            await channel.start()
            if channel.is_running:
                if name in self._work_receivers:
                    self._work_receivers[name].start(channel)
                logger.info("Channel started")
                return True
            logger.error("Channel did not enter a running state after start()")
        except Exception:
            logger.exception("Failed to start channel")
        if channel is not None:
            try:
                await self._stop_channel(name, channel)
            except Exception:
                logger.exception("Failed to clean up channel startup; retaining worker to prevent duplicates")
                return False
        self._channels.pop(name, None)
        receiver = self._work_receivers.pop(name, None)
        if receiver:
            await receiver.close()
        return False

    def get_status(self) -> dict[str, Any]:
        """Return status information for all channels."""
        channels_status = {}
        for name in _CHANNEL_REGISTRY:
            config = self._config.get(name, {})
            enabled = isinstance(config, dict) and config.get("enabled", False)
            running = name in self._channels and self._channels[name].is_running
            channels_status[name] = {
                "enabled": enabled,
                "running": running,
            }
            channel = self._channels.get(name)
            if channel is not None and isinstance(getattr(channel, "transport_connected", None), bool):
                channels_status[name]["connected"] = channel.transport_connected
                failure = getattr(channel, "_ws_failure", None)
                channels_status[name]["transport_business_code"] = failure.business_code if failure else None
        return {
            "service_running": self._running,
            "channels": channels_status,
        }

    def get_channel(self, name: str) -> Channel | None:
        """Return a running channel instance by name when available."""
        return self._channels.get(name)

    def is_channel_enabled(self, name: str) -> bool:
        """Return whether ``channels.<name>.enabled`` is truthy in the live config.

        Tracks the runtime-authoritative ``_config`` dict, which
        :meth:`configure_channel` updates when the UI flips the
        enabled flag — so callers that read this between requests get
        the current effective setting without re-reading config.yaml.
        Used by the GitHub webhook router as a fan-out kill-switch:
        ``channels.github.enabled: false`` skips dispatch even though
        the webhook route itself remains mounted (which is governed by
        ``GITHUB_WEBHOOK_SECRET``, not this flag).
        """
        config = self._config.get(name)
        if not isinstance(config, dict):
            return False
        return bool(config.get("enabled", False))

    def get_channel_config(self, name: str) -> dict[str, Any] | None:
        """Return a shallow copy of the live ``channels.<name>`` block, or None.

        Mirrors :meth:`is_channel_enabled` in tracking the runtime-
        authoritative ``_config`` dict, so callers see the same effective
        configuration the manager sees — including any updates pushed via
        :meth:`configure_channel` from the UI. Returns ``None`` when no
        config exists for ``name`` (rather than an empty dict) so callers
        can distinguish "not configured" from "configured with defaults".
        The shallow copy keeps callers from accidentally mutating live
        config state.
        """
        config = self._config.get(name)
        if not isinstance(config, dict):
            return None
        return dict(config)


# -- singleton access -------------------------------------------------------

_channel_service: ChannelService | None = None


def get_channel_service() -> ChannelService | None:
    """Get the singleton ChannelService instance (if started)."""
    return _channel_service


async def start_channel_service(app_config: AppConfig | None = None) -> ChannelService:
    """Create and start the global ChannelService from app config."""
    global _channel_service
    if _channel_service is not None:
        return _channel_service
    # from_app_config reads the JSON channel store and runtime config files;
    # keep that disk IO off the event loop.
    _channel_service = await asyncio.to_thread(ChannelService.from_app_config, app_config)
    await _channel_service.start()
    return _channel_service


async def stop_channel_service() -> None:
    """Stop the global ChannelService."""
    global _channel_service
    if _channel_service is not None:
        await _channel_service.stop()
        _channel_service = None

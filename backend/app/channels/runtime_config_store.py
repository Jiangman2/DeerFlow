"""Local persistence for runtime IM channel configuration."""

from __future__ import annotations

import json
import logging
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

RUNTIME_CHANNEL_DISABLED_FLAG = "_runtime_disabled"

# Feishu domains reviewed by operations. ``DEERFLOW_FEISHU_DOMAIN`` in the root
# .env is the single switch that selects the active domain: when it is set,
# _policy() below forces this value over whatever config.yaml / runtime-config.json
# hold, so flipping that one line and restarting the Gateway switches platforms.
# Any value outside this set is rejected so unreviewed domains cannot slip in.
# NOTE: the domain must match the platform the app_id/app_secret belong to,
# otherwise Feishu returns "app id not exists" (code 10014).
_ALLOWED_FEISHU_DOMAINS = frozenset({
    "https://open.feishu.cn",        # 官方飞书国内版
    "https://open.w.cnooc.com.cn",   # 海油企业飞书
    "https://open.larksuite.com",    # 国际版 Lark
})


class ChannelRuntimeConfigConflict(RuntimeError):
    """Another writer changed this provider; do not overwrite its settings."""


class ChannelRuntimeConfigStore:
    """Fresh file reads and cooperative cross-process locked mutations.

    Linux/NFS release candidate. All writers must use this protocol; legacy
    Gateway processes and manual file edits do not participate in its lock.
    """

    def __init__(self, path: str | Path | None = None):
        if path is None:
            from deerflow.config.paths import get_paths

            path = Path(get_paths().base_dir) / "channels/runtime-config.json"
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data = {}

    @staticmethod
    def _policy(provider, config):
        import copy
        import os

        result = copy.deepcopy(config)
        domain = os.environ.get("DEERFLOW_FEISHU_DOMAIN")
        if provider == "feishu" and domain:
            normalized = domain.strip().rstrip("/")
            if normalized not in _ALLOWED_FEISHU_DOMAINS:
                raise ValueError(
                    "Unreviewed Feishu channel domain: "
                    f"{normalized!r}. Allowed: {', '.join(sorted(_ALLOWED_FEISHU_DOMAINS))}"
                )
            result["domain"] = normalized
        return result

    def _load(self):
        if not self._path.exists():
            return {}
        if self._path.is_symlink() or self._path.stat().st_size > 1048576:
            raise ValueError("Channel configuration file requires review")
        # Corrupt/unreadable files fail closed, never get replaced with {}.
        raw = json.loads(self._path.read_bytes())
        if not isinstance(raw, dict) or not all(isinstance(v, dict) for v in raw.values()):
            raise ValueError("Invalid channel configuration shape")
        return raw

    @contextmanager
    def _transaction(self):
        import os
        import stat
        import time

        with self._lock:
            if os.name == "nt":
                # Windows 无 fcntl/O_NOFOLLOW；本地为单 Gateway 进程，进程内 RLock 已能保证互斥，
                # 跨进程文件锁仅面向 Linux 多进程生产部署，此处直接跳过。
                self._data = self._load()
                yield
                return
            import fcntl
            lock_path = self._path.with_name(self._path.name + ".lock")
            fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise ValueError("Invalid channel lock file")
                deadline = time.monotonic() + 5
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Channel configuration lock busy") from None
                        time.sleep(0.025)
                self._data = self._load()
                yield
            finally:
                os.close(fd)

    def _save(self):
        import os

        fd = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self._path.parent, suffix=".tmp", delete=False)
        temp_path = Path(fd.name)
        try:
            os.chmod(temp_path, 0o600)
            json.dump(self._data, fd, indent=2, ensure_ascii=False)
            fd.flush()
            os.fsync(fd.fileno())
            fd.close()
            temp_path.replace(self._path)
        except BaseException:
            fd.close()
            temp_path.unlink(missing_ok=True)
            raise

    def load_all(self):
        with self._transaction():
            return {name: self._policy(name, cfg) for name, cfg in self._data.items()}

    def get_provider_config(self, provider):
        return self.load_all().get(provider)

    def set_provider_config(self, provider, config):
        with self._transaction():
            self._data[provider] = self._policy(provider, config)
            self._save()

    def commit_provider_config(self, provider, config, expected):
        with self._transaction():
            saved = self._data.get(provider)
            current = self._policy(provider, saved) if saved is not None else None
            if current != expected:
                raise ChannelRuntimeConfigConflict("Channel configuration changed; reload before saving")
            updated = self._policy(provider, config)
            self._data[provider] = updated
            self._save()
            return self._policy(provider, updated)

    def set_provider_disconnected(self, provider):
        self.set_provider_config(provider, {"enabled": False, RUNTIME_CHANNEL_DISABLED_FLAG: True})

    def remove_provider_config(self, provider):
        with self._transaction():
            if provider not in self._data:
                return False
            del self._data[provider]
            self._save()
            return True


def _provider_enabled(channel_connections_config: Any, provider: str) -> bool:
    provider_config = getattr(channel_connections_config, provider, None)
    return bool(getattr(provider_config, "enabled", False))


def _runtime_channel_disconnected(runtime_config: dict[str, Any]) -> bool:
    return runtime_config.get(RUNTIME_CHANNEL_DISABLED_FLAG) is True and runtime_config.get("enabled") is False


def merge_runtime_channel_configs(
    channels_config: dict[str, Any],
    channel_connections_config: Any,
    *,
    store: ChannelRuntimeConfigStore | None = None,
) -> None:
    """Merge persisted runtime provider config into ``channels_config`` in-place."""
    if channel_connections_config is None or not getattr(channel_connections_config, "enabled", False):
        return

    runtime_store = store or ChannelRuntimeConfigStore()
    for provider, runtime_config in runtime_store.load_all().items():
        if not _provider_enabled(channel_connections_config, provider):
            continue
        if _runtime_channel_disconnected(runtime_config):
            if provider == "feishu":
                # Preserve authoritative Work disable across credential defaults
                # and cross-Pod reloads; absence is not an enabled fallback.
                channels_config[provider] = dict(runtime_config)
            else:
                channels_config.pop(provider, None)
            continue
        existing = channels_config.get(provider)
        merged = dict(existing) if isinstance(existing, dict) else {}
        merged.update(runtime_config)
        channels_config[provider] = merged


def apply_runtime_connection_config(
    channel_connections_config: Any,
    *,
    store: ChannelRuntimeConfigStore | None = None,
) -> Any:
    """Apply persisted connection metadata that lives outside ``channels``.

    Telegram uses a bot username for deep links; UI-entered values are stored
    with the runtime channel config so local restarts keep the provider
    configured.
    """
    if channel_connections_config is None or not getattr(channel_connections_config, "enabled", False):
        return channel_connections_config

    runtime_store = store or ChannelRuntimeConfigStore()
    telegram_runtime_config = runtime_store.get_provider_config("telegram")
    bot_username = ""
    if isinstance(telegram_runtime_config, dict):
        bot_username = str(telegram_runtime_config.get("bot_username") or "").strip()
    if not bot_username or not _provider_enabled(channel_connections_config, "telegram"):
        return channel_connections_config

    config = channel_connections_config.model_copy(deep=True)
    config.telegram.bot_username = bot_username
    return config

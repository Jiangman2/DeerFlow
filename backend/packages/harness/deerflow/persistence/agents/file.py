"""Filesystem-backed agent store — today's per-user layout, behaviour-preserving.

The read methods are the pre-refactor bodies of ``load_agent_config`` /
``load_agent_soul`` / ``list_custom_agents`` (so the free functions in
:mod:`deerflow.config.agents_config` dispatch here without changing behaviour).
Writes use a staged temp-file + atomic ``os.replace`` commit — the crash-safety
the ``update_agent`` tool already had, applied uniformly to create/update.

Path/user resolution is done through the :mod:`deerflow.config.agents_config`
module object (``_ac.get_paths`` / ``_ac.get_effective_user_id``) rather than
direct imports, so it honours the same monkeypatch seams the existing agent
tests target.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Hashable
from pathlib import Path
from typing import Any

import yaml

from deerflow.config import agents_config as _ac
from deerflow.config.agents_config import (
    SOUL_FILENAME,
    AgentConfig,
    resolve_agent_dir,
    validate_agent_name,
)
from deerflow.persistence.agents.base import (
    AgentDeleteOutcome,
    AgentExistsError,
    AgentStore,
    parse_agent_config,
)
from deerflow.runtime.user_context import DEFAULT_USER_ID

logger = logging.getLogger(__name__)


class FileAgentStore(AgentStore):
    def read_definition(self, name: str, *, user_id: str | None = None):
        from deerflow.persistence.agents.drafts import file_draft_transaction, read_file_definition

        name = validate_agent_name(name)
        root = _ac.get_paths().user_agent_dir(user_id or _ac.get_effective_user_id(), name)
        with file_draft_transaction(root):
            return read_file_definition(root, name)

    def patch_definition(self, name: str, updates: dict, soul: str | None, *, user_id: str | None = None, expected_revision: str | None):
        from deerflow.persistence.agents.drafts import commit_file_definition, file_draft_transaction, patched_definition, read_file_definition

        name = validate_agent_name(name)
        root = _ac.get_paths().user_agent_dir(user_id or _ac.get_effective_user_id(), name)
        with file_draft_transaction(root):
            before = read_file_definition(root, name)
            after = patched_definition(before, updates, soul, expected_revision)
            if before != after:
                commit_file_definition(root, after)
            return after

    def get(self, name: str, *, user_id: str | None = None, allow_shared: bool = True) -> AgentConfig:
        name = validate_agent_name(name)
        agent_dir = resolve_agent_dir(name, user_id=user_id) if allow_shared else _ac.get_paths().user_agent_dir(user_id or _ac.get_effective_user_id(), name)
        personal_dir = _ac.get_paths().user_agent_dir(user_id or _ac.get_effective_user_id(), name)
        if agent_dir == personal_dir and (agent_dir / "config.yaml").is_file():
            return parse_agent_config(self.read_definition(name, user_id=user_id).config, name)
        config_file = agent_dir / "config.yaml"
        if not agent_dir.exists():
            raise FileNotFoundError(f"Agent directory not found: {agent_dir}")
        if not config_file.exists():
            raise FileNotFoundError(f"Agent config not found: {config_file}")
        try:
            with open(config_file, encoding="utf-8") as f:
                data: dict[str, Any] = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ValueError(f"Failed to parse agent config {config_file}: {e}") from e
        return parse_agent_config(data, name)

    def exists(self, name: str, *, user_id: str | None = None) -> bool:
        name = validate_agent_name(name)
        paths = _ac.get_paths()
        effective_user = user_id or _ac.get_effective_user_id()
        return paths.user_agent_dir(effective_user, name).exists() or paths.agent_dir(name).exists()

    def get_soul(self, name: str, *, user_id: str | None = None, allow_shared: bool = True) -> str | None:
        name = validate_agent_name(name)
        agent_dir = resolve_agent_dir(name, user_id=user_id) if allow_shared else _ac.get_paths().user_agent_dir(user_id or _ac.get_effective_user_id(), name)
        personal_dir = _ac.get_paths().user_agent_dir(user_id or _ac.get_effective_user_id(), name)
        if agent_dir == personal_dir and (agent_dir / "config.yaml").is_file():
            return self.read_definition(name, user_id=user_id).soul.strip() or None
        if not allow_shared and not (agent_dir / "config.yaml").is_file():
            raise FileNotFoundError("Personal agent is no longer available")
        soul_path = agent_dir / SOUL_FILENAME
        # resolve_agent_dir requires config.yaml; SOUL.md loading does not, so
        # when the resolver fell back to its default path (no qualifying dir),
        # check the per-user and legacy dirs directly (#4135). The config.yaml
        # guard keeps this fallback from firing for a properly-resolved per-user
        # agent that merely lacks SOUL.md (preserves per-user-shadows-legacy).
        if allow_shared and not soul_path.exists() and not (agent_dir / "config.yaml").exists():
            paths = _ac.get_paths()
            effective_user = user_id or _ac.get_effective_user_id()
            for candidate in (
                paths.user_agent_dir(effective_user, name),
                paths.agent_dir(name),
            ):
                if (candidate / SOUL_FILENAME).exists():
                    soul_path = candidate / SOUL_FILENAME
                    break
        if not soul_path.exists():
            return None
        content = soul_path.read_text(encoding="utf-8").strip()
        return content or None

    def list(self, *, user_id: str | None = None) -> list[AgentConfig]:
        paths = _ac.get_paths()
        effective_user = user_id or _ac.get_effective_user_id()
        seen: set[str] = set()
        agents: list[AgentConfig] = []
        for root in (paths.user_agents_dir(effective_user), paths.agents_dir):
            if not root.exists():
                continue
            for entry in sorted(root.iterdir()):
                if not entry.is_dir() or entry.name in seen:
                    continue
                if not (entry / "config.yaml").exists():
                    logger.debug("Skipping %s: no config.yaml", entry.name)
                    continue
                try:
                    agents.append(self.get(entry.name, user_id=effective_user))
                    seen.add(entry.name)
                except Exception as e:  # noqa: BLE001 — one bad agent must not hide the rest
                    logger.warning("Skipping agent '%s': %s", entry.name, e)
        agents.sort(key=lambda a: a.name)
        return agents

    def list_all(self) -> list[tuple[str, AgentConfig]]:
        result: list[tuple[str, AgentConfig]] = []
        for user_id, name in self._discover():
            try:
                result.append((user_id, self.get(name, user_id=user_id)))
            except Exception as e:  # noqa: BLE001
                logger.warning("list_all: skipping agent %s/%s: %s", user_id, name, e)
        return result

    def create(self, name: str, config: dict, soul: str, *, user_id: str | None = None) -> None:
        name = validate_agent_name(name)
        paths = _ac.get_paths()
        effective_user = user_id or _ac.get_effective_user_id()
        agent_dir = paths.user_agent_dir(effective_user, name)
        # Refuse if a per-user directory OR a legacy shared directory already
        # owns the name — the agents router's 409 semantics (a legacy agent must
        # not be shadowed; a per-user dir may be memory-only but still blocks).
        if agent_dir.exists() or paths.agent_dir(name).exists():
            raise AgentExistsError(f"Agent '{name}' already exists for user '{effective_user}'")
        try:
            agent_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError as e:
            # A concurrent create passed the existence check above and reached
            # mkdir first. Surface the router's 409 (via AgentExistsError) rather
            # than a generic 500 — mirrors SqlAgentStore's IntegrityError path.
            raise AgentExistsError(f"Agent '{name}' already exists for user '{effective_user}'") from e
        try:
            self._write(agent_dir, config, soul)
        except Exception:
            # The directory was newly created for this call; a failed write
            # must not leave an empty/partial agent dir behind.
            shutil.rmtree(agent_dir, ignore_errors=True)
            raise

    def update(self, name: str, config: dict | None, soul: str | None, *, user_id: str | None = None) -> None:
        name = validate_agent_name(name)
        effective_user = user_id or _ac.get_effective_user_id()
        agent_dir = _ac.get_paths().user_agent_dir(effective_user, name)
        pre_existing = agent_dir.exists()
        agent_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._write(agent_dir, config, soul)
        except Exception:
            # Only clean up a directory this call created — never delete a
            # pre-existing agent on a failed write.
            if not pre_existing:
                shutil.rmtree(agent_dir, ignore_errors=True)
            raise

    def patch_config(self, name: str, updates: dict, *, user_id: str | None = None) -> None:
        """Atomically merge fields without normalizing away unknown YAML keys."""
        from deerflow.persistence.agents.drafts import commit_file_definition, file_draft_transaction, patched_definition, read_file_definition

        name = validate_agent_name(name)
        effective_user = user_id or _ac.get_effective_user_id()
        config_file = _ac.get_paths().user_agent_dir(effective_user, name) / "config.yaml"
        if not config_file.is_file():
            raise FileNotFoundError(f"Agent config not found: {config_file}")
        with file_draft_transaction(config_file.parent):
            before = read_file_definition(config_file.parent, name)
            after = patched_definition(before, updates, None, before.revision)
            if after != before:
                commit_file_definition(config_file.parent, after)

    def delete_definition(self, name: str, *, user_id: str | None = None, expected_revision: str, before_delete=None) -> AgentDeleteOutcome:
        from deerflow.persistence.agents.deletion import retain_deleted_definition
        from deerflow.persistence.agents.drafts import file_draft_transaction, patched_definition, read_file_definition

        name = validate_agent_name(name)
        effective_user = user_id or _ac.get_effective_user_id()
        root = _ac.get_paths().user_agent_dir(effective_user, name)
        with file_draft_transaction(root):
            current = read_file_definition(root, name)
            patched_definition(current, {}, None, expected_revision)
            if before_delete is not None:
                before_delete()
            retain_deleted_definition(root, current, owner=effective_user, backend="file")
        return "deleted"

    def delete(self, name: str, *, user_id: str | None = None) -> AgentDeleteOutcome:
        name = validate_agent_name(name)
        paths = _ac.get_paths()
        effective_user = user_id or _ac.get_effective_user_id()
        agent_dir = paths.user_agent_dir(effective_user, name)
        if not agent_dir.exists():
            # A legacy shared-layout agent is intentionally left in place (the
            # write path never targets it); report it distinctly.
            return "legacy" if paths.agent_dir(name).exists() else "missing"
        if not (agent_dir / "config.yaml").is_file():
            # The directory holds memory/facts data but is not a custom agent
            # (no config.yaml) — preserve it rather than deleting a user's memory
            # (#4279). rmtree below would otherwise take the whole tree.
            return "not-custom-agent"
        # rmtree removes config.yaml, SOUL.md and the co-located memory.json in
        # one shot — the historical behaviour.
        shutil.rmtree(agent_dir)
        return "deleted"

    def signature(self) -> Hashable:
        sig: list[tuple[str, str, float]] = []
        for user_id, name in self._discover():
            config = resolve_agent_dir(name, user_id=user_id) / "config.yaml"
            try:
                sig.append((user_id, name, config.stat().st_mtime))
            except OSError:
                continue
        return tuple(sig)

    # -- internals --

    def _discover(self) -> list[tuple[str, str]]:
        """Enumerate ``(user_id, name)`` across per-user and legacy layouts.

        A legacy shared-layout agent is attributed to ``DEFAULT_USER_ID`` and is
        shadowed only by a ``users/default/`` agent of the same name — not by an
        agent another user happens to own — matching the GitHub registry's
        historical discovery (``load_agent_config(name)`` resolves a legacy agent
        under ``DEFAULT_USER_ID``).
        """
        paths = _ac.get_paths()
        discovered: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        users_root = paths.base_dir / "users"
        if users_root.exists():
            for user_entry in sorted(users_root.iterdir()):
                if not user_entry.is_dir():
                    continue
                agents_root = paths.user_agents_dir(user_entry.name)
                if not agents_root.exists():
                    continue
                for entry in sorted(agents_root.iterdir()):
                    if entry.is_dir() and (entry / "config.yaml").exists():
                        key = (user_entry.name, entry.name)
                        discovered.append(key)
                        seen.add(key)
        legacy_root = paths.agents_dir
        if legacy_root.exists():
            for entry in sorted(legacy_root.iterdir()):
                if entry.is_dir() and (entry / "config.yaml").exists() and (DEFAULT_USER_ID, entry.name) not in seen:
                    discovered.append((DEFAULT_USER_ID, entry.name))
        return discovered

    @staticmethod
    def _write(agent_dir: Path, config: dict | None, soul: str | None) -> None:
        """Legacy upsert writer, sharing recovery/locking with draft edits.

        Merge supplied keys, never erase unknown fields. New editing surfaces
        use patch_definition with an explicit revision instead of this method.
        """
        from deerflow.persistence.agents.drafts import AgentDefinition, commit_file_definition, file_draft_transaction, read_file_definition

        with file_draft_transaction(agent_dir):
            before = read_file_definition(agent_dir, agent_dir.name) if (agent_dir / "config.yaml").is_file() else AgentDefinition.from_parts(agent_dir.name, {}, "")
            after = AgentDefinition.from_parts(agent_dir.name, {**before.config, **(config or {})}, before.soul if soul is None else soul)
            if after != before or not (agent_dir / "config.yaml").exists():
                commit_file_definition(agent_dir, after)

"""Per-user SkillHub installation state and runtime lifecycle operations."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deerflow.skills.storage.user_scoped_skill_storage import UserScopedSkillStorage

_REGISTRY_FILENAME = "_registry_installations.json"
_LOCK_DIRECTORY = ".registry-operation.lock"
_LOCK_TIMEOUT_SECONDS = 15.0
_STALE_LOCK_SECONDS = 300.0
_NAMESPACE_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$")


class RegistrySkillConflictError(ValueError):
    """The requested registry mutation would overwrite unmanaged content."""


class RegistrySkillNotFoundError(FileNotFoundError):
    """No matching per-user registry installation exists."""


def normalize_registry_identity(namespace: str, slug: str, version: str | None = None) -> tuple[str, str, str | None]:
    clean_namespace = namespace.strip().lstrip("@").lower()
    if len(clean_namespace) > 64 or not _NAMESPACE_PATTERN.fullmatch(clean_namespace):
        raise ValueError("SkillHub namespace must be lowercase hyphen-case and at most 64 characters.")
    clean_slug = UserScopedSkillStorage.validate_skill_name(slug)
    clean_version = version.strip() if version is not None else None
    if clean_version is not None and not _VERSION_PATTERN.fullmatch(clean_version):
        raise ValueError("SkillHub version contains unsupported characters or is too long.")
    return clean_namespace, clean_slug, clean_version


def registry_key(namespace: str, slug: str) -> str:
    return f"{namespace}/{slug}"


def _registry_path(storage: UserScopedSkillStorage) -> Path:
    return storage.get_user_skills_root() / _REGISTRY_FILENAME


def _read_registry(storage: UserScopedSkillStorage) -> dict[str, Any]:
    path = _registry_path(storage)
    if not path.exists():
        return {"schema_version": 1, "installations": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"SkillHub installation registry is unreadable: {path}") from exc
    installations = payload.get("installations")
    if payload.get("schema_version") != 1 or not isinstance(installations, dict):
        raise RuntimeError(f"SkillHub installation registry has an unsupported format: {path}")
    return payload


def _write_registry(storage: UserScopedSkillStorage, payload: dict[str, Any]) -> None:
    path = _registry_path(storage)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".registry-", suffix=".json.tmp", dir=str(path.parent))
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        temp_path.replace(path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _try_acquire_lock(lock_path: Path, token: str) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_path.mkdir()
    except FileExistsError:
        try:
            age = time.time() - lock_path.stat().st_mtime
        except FileNotFoundError:
            return False
        if age <= _STALE_LOCK_SECONDS:
            return False
        stale_path = lock_path.with_name(f"{lock_path.name}.stale-{uuid.uuid4().hex}")
        try:
            lock_path.replace(stale_path)
        except (FileNotFoundError, FileExistsError, OSError):
            return False
        shutil.rmtree(stale_path, ignore_errors=True)
        return False
    (lock_path / "owner").write_text(token, encoding="utf-8")
    return True


def _release_lock(lock_path: Path, token: str) -> None:
    try:
        if (lock_path / "owner").read_text(encoding="utf-8") != token:
            return
    except (FileNotFoundError, OSError):
        return
    shutil.rmtree(lock_path, ignore_errors=True)


@asynccontextmanager
async def registry_operation_lock(storage: UserScopedSkillStorage) -> AsyncIterator[None]:
    lock_path = storage.get_user_skills_root() / _LOCK_DIRECTORY
    token = uuid.uuid4().hex
    deadline = asyncio.get_running_loop().time() + _LOCK_TIMEOUT_SECONDS
    while not await asyncio.to_thread(_try_acquire_lock, lock_path, token):
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("Another SkillHub runtime operation is still in progress. Please retry.")
        await asyncio.sleep(0.05)
    try:
        yield
    finally:
        await asyncio.to_thread(_release_lock, lock_path, token)


def _record_with_live_state(storage: UserScopedSkillStorage, record: dict[str, Any]) -> dict[str, Any]:
    skill_name = str(record["skill_name"])
    return {
        **record,
        "enabled": storage.get_skill_enabled_state(skill_name),
        "installed": storage.custom_skill_exists(skill_name),
    }


async def list_registry_skills(storage: UserScopedSkillStorage) -> list[dict[str, Any]]:
    def _load() -> list[dict[str, Any]]:
        records = _read_registry(storage)["installations"].values()
        return sorted(
            (_record_with_live_state(storage, dict(record)) for record in records),
            key=lambda record: (record["namespace"], record["slug"]),
        )

    return await asyncio.to_thread(_load)


async def install_registry_skill(
    storage: UserScopedSkillStorage,
    *,
    namespace: str,
    slug: str,
    version: str,
    archive_path: Path,
    sha256: str,
) -> dict[str, Any]:
    namespace, slug, normalized_version = normalize_registry_identity(namespace, slug, version)
    assert normalized_version is not None
    key = registry_key(namespace, slug)

    async with registry_operation_lock(storage):
        payload = await asyncio.to_thread(_read_registry, storage)
        installations: dict[str, dict[str, Any]] = payload["installations"]
        previous = installations.get(key)
        target_exists = await asyncio.to_thread(storage.custom_skill_exists, slug)

        owner_for_name = next(
            (record for existing_key, record in installations.items() if existing_key != key and record.get("skill_name") == slug),
            None,
        )
        if owner_for_name is not None:
            raise RegistrySkillConflictError(f"Custom skill '{slug}' is already managed by another SkillHub namespace.")
        if target_exists and previous is None:
            raise RegistrySkillConflictError(f"Custom skill '{slug}' already exists and is not managed by SkillHub.")
        if target_exists and previous is not None and previous.get("version") == normalized_version:
            return {
                **_record_with_live_state(storage, dict(previous)),
                "action": "already_current",
            }

        result = await storage.ainstall_or_upgrade_skill_from_archive(
            archive_path,
            expected_name=slug,
        )
        now = datetime.now(UTC).isoformat()
        record = {
            "namespace": namespace,
            "slug": slug,
            "version": normalized_version,
            "skill_name": result["skill_name"],
            "sha256": sha256,
            "installed_at": previous.get("installed_at", now) if previous else now,
            "updated_at": now,
        }
        installations[key] = record
        await asyncio.to_thread(_write_registry, storage, payload)
        await asyncio.to_thread(
            storage.append_history,
            slug,
            {
                "action": f"skillhub_{result['action']}",
                "author": "skillhub-runtime-adapter",
                "thread_id": None,
                "file_path": "SKILL.md",
                "registry": {
                    "namespace": namespace,
                    "slug": slug,
                    "version": normalized_version,
                    "sha256": sha256,
                },
                "scanner": {"decision": "allow", "reason": "Archive passed DeerFlow install scans."},
            },
        )
        return {
            **_record_with_live_state(storage, record),
            "action": result["action"],
        }


async def set_registry_skill_enabled(
    storage: UserScopedSkillStorage,
    *,
    namespace: str,
    slug: str,
    enabled: bool,
) -> dict[str, Any]:
    namespace, slug, _ = normalize_registry_identity(namespace, slug)
    key = registry_key(namespace, slug)
    async with registry_operation_lock(storage):
        payload = await asyncio.to_thread(_read_registry, storage)
        record = payload["installations"].get(key)
        if record is None:
            raise RegistrySkillNotFoundError(f"SkillHub runtime skill '{key}' is not installed.")
        if not await asyncio.to_thread(storage.custom_skill_exists, record["skill_name"]):
            raise RegistrySkillConflictError(f"Installed skill tree for '{key}' is missing.")
        await asyncio.to_thread(storage.set_skill_enabled_state, record["skill_name"], enabled)
        record["updated_at"] = datetime.now(UTC).isoformat()
        await asyncio.to_thread(_write_registry, storage, payload)
        return _record_with_live_state(storage, dict(record))


async def uninstall_registry_skill(
    storage: UserScopedSkillStorage,
    *,
    namespace: str,
    slug: str,
) -> None:
    namespace, slug, _ = normalize_registry_identity(namespace, slug)
    key = registry_key(namespace, slug)
    async with registry_operation_lock(storage):
        payload = await asyncio.to_thread(_read_registry, storage)
        record = payload["installations"].get(key)
        if record is None:
            raise RegistrySkillNotFoundError(f"SkillHub runtime skill '{key}' is not installed.")
        skill_name = record["skill_name"]
        if await asyncio.to_thread(storage.custom_skill_exists, skill_name):
            await asyncio.to_thread(
                storage.delete_custom_skill,
                skill_name,
                history_meta={
                    "action": "skillhub_uninstalled",
                    "author": "skillhub-runtime-adapter",
                    "thread_id": None,
                    "file_path": "SKILL.md",
                    "registry": {
                        "namespace": namespace,
                        "slug": slug,
                        "version": record["version"],
                        "sha256": record["sha256"],
                    },
                    "scanner": {"decision": "allow", "reason": "User requested uninstall."},
                },
            )
        await asyncio.to_thread(storage.clear_skill_enabled_state, skill_name)
        del payload["installations"][key]
        await asyncio.to_thread(_write_registry, storage, payload)

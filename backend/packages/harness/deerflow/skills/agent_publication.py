"""Immutable Skill dependencies for a published Agent.

This is a publication/projection primitive, not a permission bypass. Callers
must authorize publication and pin the publication identity to a thread before
using it. The projected root must replace (not augment) the shared employee's
custom Skill mount, read-only. No API exposes this tree for installation or
download, and no publisher credentials or personal workspace are projected.

The physical-copy and package-boundary rules follow upstream #5077. Enterprise
publication/versioning is an adaptation, not an upstream permission grant.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import stat
import tempfile
from pathlib import Path

import yaml

from deerflow.config.paths import get_paths
from deerflow.skills.parser import parse_skill_file
from deerflow.skills.permissions import make_skill_tree_sandbox_readable
from deerflow.skills.skillscan import scan_skill_dir
from deerflow.skills.storage import get_or_new_user_skill_storage
from deerflow.skills.storage.local_skill_storage import LocalSkillStorage
from deerflow.skills.types import SkillCategory

_MAX_BYTES = 128 * 1024 * 1024
_MAX_FILE_BYTES = 32 * 1024 * 1024
_MAX_FILES = 4096
_MANIFEST = "skills.manifest.json"
_REVISION = re.compile(r"[0-9a-f]{64}")


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _read_manifest(root: Path) -> dict:
    path = root / _MANIFEST
    if root.is_symlink() or getattr(root, "is_junction", lambda: False)():
        raise ValueError("Unsafe Skill manifest directory")
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise ValueError("Missing Skill bundle manifest") from None
    if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
        raise ValueError("Unsafe or oversized Skill bundle manifest")
    with path.open("rb") as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("Skill bundle manifest exceeds limit")
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or manifest.get("version") != 1 or not isinstance(manifest.get("skills"), dict):
        raise ValueError("Invalid Skill bundle manifest")
    return manifest


def _read_tree(root: Path) -> dict[str, tuple[bytes, int]]:
    """Bounded regular-file snapshot; never follow links or copy credentials."""
    files: dict[str, tuple[bytes, int]] = {}
    folded: set[str] = set()
    size = 0
    if root.is_symlink() or getattr(root, "is_junction", lambda: False)() or not root.is_dir():
        raise ValueError("Published Skill root must be a regular directory")

    def fail_walk(error):
        raise error

    for directory, directories, filenames in os.walk(root, followlinks=False, onerror=fail_walk):
        for name in sorted(directories + filenames):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                raise ValueError("Published Skill packages must not contain links")
            if name.casefold() in {".git", ".ssh", ".history", ".env", "auth.json", "credentials.json"} or name.casefold().startswith(".env."):
                raise ValueError("Published Skill package contains a private/secret path")
            if ":" in name or "\\" in name or any(ord(char) < 32 for char in name):
                raise ValueError("Published Skill package contains a non-portable path")
            if path.is_dir():
                continue
            info = path.stat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_FILE_BYTES:
                raise ValueError("Published Skill file type or size is unsupported")
            if relative.casefold() in folded or len(files) >= _MAX_FILES:
                raise ValueError("Published Skill package contains duplicate paths or too many files")
            with path.open("rb") as stream:
                content = stream.read(_MAX_FILE_BYTES + 1)
            size += len(content)
            if len(content) > _MAX_FILE_BYTES or size > _MAX_BYTES:
                raise ValueError("Published Skill package exceeds the byte limit")
            if b"PRIVATE KEY-----" in content:
                raise ValueError("Published Skill package contains secret key material")
            files[relative] = (content, stat.S_IMODE(info.st_mode) & 0o111)
            folded.add(relative.casefold())
    return files


def _entries(files):
    return {name: {"sha256": hashlib.sha256(content).hexdigest(), "size": len(content), "executable": executable} for name, (content, executable) in sorted(files.items())}


def _write_tree(root: Path, files) -> None:
    root.mkdir(parents=True, exist_ok=False)
    for relative, (content, executable) in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        target.chmod(0o644 | executable)
    make_skill_tree_sandbox_readable(root)


def stage_agent_skills(staging: Path, names: list[str] | None, *, user_id: str, app_config=None) -> str:
    """Stage only explicitly chosen available packages in a NEW publication."""
    if names is None:
        raise ValueError("Publishing requires an explicit skill selection; [] publishes no skills")
    if len(names) > 128 or len(names) != len(set(names)):
        raise ValueError("Invalid published skill selection")
    storage = get_or_new_user_skill_storage(user_id, app_config=app_config)
    available = {skill.name: skill for skill in storage.load_skills(enabled_only=True)} if names else {}
    if any(name not in available for name in names):
        raise ValueError("Selected skills are unavailable or disabled for the publisher")
    snapshots = {}
    total = 0
    count = 0
    for name in names:
        storage.validate_skill_name(name)
        selected = available[name]
        storage.validate_skill_file_path(selected.skill_file)
        package = _read_tree(selected.skill_dir)
        if "SKILL.md" not in package:
            raise ValueError("Published Skill package is missing SKILL.md")
        total += sum(len(content) for content, _ in package.values())
        count += len(package)
        if total > _MAX_BYTES or count > _MAX_FILES:
            raise ValueError("Published dependency set exceeds the size limit")
        snapshots[name] = package
    # All validation is complete before changing even the staging directory.
    staging.mkdir(parents=True, exist_ok=True)
    root = staging / "skills"
    root.mkdir(exist_ok=False)
    for name, package in snapshots.items():
        _write_tree(root / name, package)
        # Scan the frozen bytes that will be published, never the mutable
        # publisher directory. This cross-user boundary fails closed even if
        # the ordinary local editing scanner has been disabled by an operator.
        result = scan_skill_dir(root / name)
        if result["blocked"] or result["scanner_errors"]:
            # Do not put scanner evidence (which can include a secret) in an
            # HTTP exception. A failed staging directory has no valid manifest.
            raise ValueError("Published Skill security scan did not pass")
    manifest = {"version": 1, "skills": {name: _entries(package) for name, package in snapshots.items()}}
    digest = hashlib.sha256(_canonical(manifest)).hexdigest()
    (staging / _MANIFEST).write_bytes(_canonical(manifest))
    return digest


def read_skill_bundle(root: Path, *, names: list[str], digest: str | None = None):
    """Verify the complete frozen dependency set, not just its manifest file."""
    if root.is_symlink() or getattr(root, "is_junction", lambda: False)():
        raise ValueError("Unsafe Skill bundle path")
    manifest = _read_manifest(root)
    actual = hashlib.sha256(_canonical(manifest)).hexdigest()
    if digest is not None and actual != digest:
        raise ValueError("Skill bundle manifest digest mismatch")
    if not isinstance(names, list) or len(names) > 128 or len(names) != len(set(names)) or set(names) != set(manifest["skills"]):
        raise ValueError("Skill bundle selection mismatch")
    skills_root = root / "skills"
    if skills_root.is_symlink() or getattr(skills_root, "is_junction", lambda: False)() or not skills_root.is_dir():
        raise ValueError("Unsafe Skill bundle directory")
    if set(path.name for path in skills_root.iterdir()) != set(names):
        raise ValueError("Unexpected Skill bundle contents")
    packages = {}
    total_bytes = total_files = 0
    for name in names:
        LocalSkillStorage.validate_skill_name(name)
        files = _read_tree(skills_root / name)
        total_bytes += sum(len(content) for content, _ in files.values())
        total_files += len(files)
        if total_bytes > _MAX_BYTES or total_files > _MAX_FILES or _entries(files) != manifest["skills"][name]:
            raise ValueError("Skill bundle size or content digest mismatch")
        if "SKILL.md" not in files:
            raise ValueError("Skill bundle missing SKILL.md")
        packages[name] = files
    return actual, manifest, packages


def copy_skill_bundle(source: Path, target: Path, *, names: list[str], digest: str) -> None:
    """Copy verified bytes into an invisible revision before its head commits."""
    _, manifest, packages = read_skill_bundle(source, names=names, digest=digest)
    (target / "skills").mkdir(exist_ok=False)
    for name, files in packages.items():
        _write_tree(target / "skills" / name, files)
    with (target / _MANIFEST).open("xb") as stream:
        stream.write(_canonical(manifest))
        stream.flush()
        os.fsync(stream.fileno())


class PublishedAgentSkillStorage(LocalSkillStorage):
    """Read-only catalog for ONE viewer thread and ONE admitted publication.

    No files enter that user's personal Skill catalog. The caller must arrange
    a read-only sandbox mount from ``projected_custom_root`` to
    ``/mnt/skills/custom`` before using the returned container paths.
    """

    def __init__(self, user_id: str, thread_id: str, *, publication: Path, revision: str, app_config=None, validate_admission=None):
        if not _REVISION.fullmatch(revision):
            raise ValueError("Invalid publication revision")
        self.publication = publication
        self.revision = revision
        self._validate_admission = validate_admission
        identity = hashlib.sha256((str(publication.resolve()) + ":" + revision).encode("utf-8")).hexdigest()
        # The full digest is checked in the manifest. A bounded directory key
        # also keeps ordinary test/workspace paths usable on Windows.
        root = get_paths().thread_dir(thread_id, user_id=user_id) / "agent-skills" / identity[:24]
        from deerflow.constants import DEFAULT_SKILLS_CONTAINER_PATH

        container_path = app_config.skills.container_path if app_config is not None else DEFAULT_SKILLS_CONTAINER_PATH
        super().__init__(host_path=str(root), container_path=container_path, app_config=app_config)

    @property
    def projected_custom_root(self) -> Path:
        return self._host_root / "custom"

    def _check_publication(self) -> dict:
        from deerflow.persistence.agents.publications import _read_file

        if self._validate_admission is not None:
            self._validate_admission()
        config_path = self.publication / "config.yaml"
        if not config_path.is_file() or config_path.is_symlink():
            raise ValueError("The admitted publication is no longer available")
        config = yaml.safe_load(_read_file(config_path).decode("utf-8"))
        if not isinstance(config, dict) or config.get("skills_revision") != self.revision:
            raise ValueError("The publication changed; start a new admitted run")
        manifest = _read_manifest(self.publication)
        if hashlib.sha256(_canonical(manifest)).hexdigest() != self.revision:
            raise ValueError("Publication manifest digest mismatch")
        if manifest.get("version") != 1 or set(config.get("skills") or []) != set(manifest["skills"]):
            raise ValueError("Publication dependency selection mismatch")
        return manifest

    def load_skills(self, enabled_only: bool = False):
        manifest = self._check_publication()
        packages = {}
        for name, expected in manifest["skills"].items():
            self.validate_skill_name(name)
            package = _read_tree(self.publication / "skills" / name)
            if _entries(package) != expected:
                raise ValueError("Published Skill digest mismatch")
            packages[name] = package
        if not self._host_root.exists():
            self._host_root.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".projection-", dir=self._host_root.parent) as temporary:
                staged = Path(temporary) / "view"
                (staged / "custom").mkdir(parents=True)
                for name, package in packages.items():
                    _write_tree(staged / "custom" / name, package)
                self._check_publication()
                try:
                    staged.rename(self._host_root)
                except OSError:
                    if not self._host_root.is_dir():
                        raise
        result = []
        if self._host_root.is_symlink() or self.projected_custom_root.is_symlink():
            raise ValueError("Publication projection must not be a symlink")
        if set(path.name for path in self.projected_custom_root.iterdir()) != set(packages):
            raise ValueError("Publication projection contains unexpected packages")
        for name in packages:
            root = self.projected_custom_root / name
            if _entries(_read_tree(root)) != manifest["skills"][name]:
                raise ValueError("Publication projection digest mismatch")
            selected = parse_skill_file(root / "SKILL.md", category=SkillCategory.PUBLISHED, relative_path=Path(name))
            if selected is None or selected.name != name:
                raise ValueError("Invalid published Skill metadata")
            result.append(dataclasses.replace(selected, enabled=True))
        self._check_publication()
        return result

    @staticmethod
    def _deny(*args, **kwargs):
        raise PermissionError("Published Agent skills are read-only and cannot be installed or edited")

    read_custom_skill = _deny
    write_custom_skill = _deny
    delete_custom_skill = _deny
    append_history = _deny
    read_history = _deny
    ensure_custom_skill_is_editable = _deny

    async def ainstall_skill_from_archive(self, archive_path):
        self._deny()

    async def ainstall_or_upgrade_skill_from_archive(self, archive_path, *, expected_name):
        self._deny()


def get_thread_published_skill_storage(thread_id: str | None, *, user_id: str, app_config=None):
    """Resolve the catalog from the authenticated owner's server-side receipt.

    Never accept a browser-supplied revision, source path, or publisher user id.
    A missing receipt/legacy bundle retains the old catalog behavior; a broken
    or withdrawn receipt raises and MUST NOT fall back to the viewer catalog.
    """
    if not thread_id:
        return None
    from deerflow.persistence.agents.runtime import EmployeePublicationBindings

    bindings = EmployeePublicationBindings(get_paths())
    admitted = bindings.read(thread_id, user_id=user_id)
    if admitted is None or "skills_revision" not in admitted.config:
        return None
    if app_config is None:
        from deerflow.config import get_app_config

        app_config = get_app_config()

    def validate():
        current = bindings.read(thread_id, user_id=user_id)
        if current is None or (current.publication_id, current.revision, current.digest) != (admitted.publication_id, admitted.revision, admitted.digest):
            raise ValueError("Employee Skill admission changed; start a new conversation")

    return PublishedAgentSkillStorage(
        user_id,
        thread_id,
        publication=admitted.directory,
        revision=admitted.config["skills_revision"],
        app_config=app_config,
        validate_admission=validate,
    )

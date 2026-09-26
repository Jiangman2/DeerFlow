"""Server-owned employee version receipts outside downloadable thread data.

No config, paths, publisher credential or browser-supplied revision is trusted
here. Every entry resolves the same admitted immutable definition. Resource
delegation remains a separate, live authorization boundary.
"""

from __future__ import annotations

import json
import os
import tempfile

from deerflow.config.agents_config import is_personal_agent, validate_agent_name, validate_agent_source
from deerflow.config.paths import Paths
from deerflow.persistence.agents.publications import PublicationConflict, PublicationSnapshot, TeamPublicationStore, _publication_lock, _read_file, _sync_dir


class EmployeePublicationBindings:
    def __init__(self, paths: Paths):
        self.paths = paths
        self.publications = TeamPublicationStore(paths)

    def _authorize_resources(self, snapshot):
        from deerflow.persistence.agents.resource_grants import EmployeeGrantStore, require_publication_grants

        require_publication_grants(EmployeeGrantStore(self.paths), snapshot)
        return snapshot

    def describe(self, thread_id: str, *, user_id: str) -> dict:
        """Read-only UI receipt; never expose SOUL, Skill source or grant keys."""
        snapshot = self.read(thread_id, user_id=user_id)
        if snapshot is None:
            return {"bound": False}
        from deerflow.config.agent_knowledge_config import AgentKnowledgeScope

        # Use the receipt's verified name, not a client-selected namesake.
        receipt = json.loads(_read_file(self.paths.thread_dir(thread_id, user_id=user_id) / "employee-publication.json"))
        head = self.publications.read(receipt["name"])
        if head.publication_id != snapshot.publication_id:
            raise PublicationConflict("Employee identity changed")
        return {
            "bound": True,
            "name": receipt["name"],
            "revision": snapshot.revision,
            "current_revision": head.revision,
            "update_available": head.revision != snapshot.revision,
            "knowledge_mode": AgentKnowledgeScope.model_validate(snapshot.config.get("knowledge_scope") or {}).mode,
        }

    def read(self, thread_id: str, *, user_id: str) -> PublicationSnapshot | None:
        """Resolve only an existing server receipt; never create or switch it."""
        if not thread_id or not user_id:
            raise ValueError("Employee publication requires an owner and thread")
        root = self.paths.thread_dir(thread_id, user_id=user_id)
        receipt = root / "employee-publication.json"
        if root.is_symlink() or receipt.is_symlink():
            raise ValueError("Unsafe employee conversation receipt")
        try:
            raw = _read_file(receipt)
        except FileNotFoundError:
            return None
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or value.get("schema") != 1 or value.get("source") != "team":
                raise ValueError("Invalid employee conversation receipt")
            name = validate_agent_name(value["name"])
            if not name:
                raise ValueError("Invalid employee conversation receipt")
            return self._authorize_resources(self.publications.read_admitted(name, publication_id=value["publication_id"], revision=value["revision"], digest=value["digest"]))
        except (KeyError, TypeError, json.JSONDecodeError):
            raise ValueError("Invalid employee conversation receipt") from None

    def admit(self, thread_id: str, *, user_id: str, name: str | None, source: str | None, is_bootstrap: bool = False, commit: bool = True, expected_revision: str | None = None) -> PublicationSnapshot | None:
        source = validate_agent_source(source)
        name = validate_agent_name(name)
        root = self.paths.thread_dir(thread_id, user_id=user_id)
        receipt = root / "employee-publication.json"
        if root.is_symlink() or receipt.is_symlink():
            raise ValueError("Unsafe employee conversation receipt")
        # Old clients omit the namespace. Resolve only on FIRST admission;
        # a later same-name personal agent must not steal an existing binding.
        if source is None and not receipt.exists() and name and self.publications.has_record(name) and not is_personal_agent(name, user_id=user_id):
            source = "team"
        if is_bootstrap and (source in ("team", "legacy") or receipt.exists()):
            raise ValueError("Shared employee cannot enter personal bootstrap")
        if source != "team" and not receipt.exists():
            return None
        if not user_id or not thread_id:
            raise ValueError("Employee publication requires an owner and thread")
        # Validate resource authorization before persisting a new binding.
        # At commit compare the exact revision previewed by the server, not a
        # browser-supplied revision or a potentially newer publication head.
        if not commit and not receipt.exists():
            if not name:
                raise ValueError("Published employee name required")
            return self._authorize_resources(self.publications.read(name))
        with _publication_lock(root / ".employee-publication.lock"):
            if receipt.exists():
                try:
                    value = json.loads(_read_file(receipt))
                    if not isinstance(value, dict) or value.get("schema") != 1 or value.get("source") != "team":
                        raise ValueError("Invalid employee conversation receipt")
                    if (name is not None and value["name"] != name) or source not in (None, "team"):
                        raise ValueError("Conversation is bound to a different employee; start a new conversation")
                    snapshot = self.publications.read_admitted(value["name"], publication_id=value["publication_id"], revision=value["revision"], digest=value["digest"])
                    if expected_revision is not None and snapshot.revision != expected_revision:
                        raise PublicationConflict("Employee admission changed during validation")
                    return self._authorize_resources(snapshot)
                except (KeyError, TypeError, json.JSONDecodeError):
                    raise ValueError("Invalid employee conversation receipt") from None
            if not name:
                raise ValueError("Published employee name required")
            snapshot = self.publications.freeze_current(name)
            self._authorize_resources(snapshot)
            if expected_revision is not None and snapshot.revision != expected_revision:
                raise PublicationConflict("Employee publication changed during validation")
            value = {"schema": 1, "name": name, "source": "team", **{key: getattr(snapshot, key) for key in ("publication_id", "revision", "digest")}}
            self._write_receipt(root, value)
            return snapshot

    def branch(self, source_thread_id: str, target_thread_id: str, *, user_id: str) -> PublicationSnapshot | None:
        """Copy a same-owner server receipt, never a Skill tree or a newer head.

        The caller must authorize the source thread and allocate a fresh target
        thread. This method never accepts a browser-provided publication id.
        A failed branch may leave an unreachable receipt for its unused UUID;
        it cannot expose another user's binding or switch an existing one.
        """
        if source_thread_id == target_thread_id:
            raise ValueError("Employee branch requires a different thread")
        snapshot = self.read(source_thread_id, user_id=user_id)
        if snapshot is None:
            return None
        root = self.paths.thread_dir(target_thread_id, user_id=user_id)
        receipt = root / "employee-publication.json"
        if root.is_symlink() or receipt.is_symlink():
            raise ValueError("Unsafe employee conversation receipt")
        with _publication_lock(root / ".employee-publication.lock"):
            # Repeat the live withdrawal/grant check immediately before commit.
            snapshot = self.read(source_thread_id, user_id=user_id)
            if snapshot is None:
                raise PublicationConflict("Source employee binding disappeared")
            current = self.read(target_thread_id, user_id=user_id)
            if current is not None:
                if (current.publication_id, current.revision, current.digest) != (snapshot.publication_id, snapshot.revision, snapshot.digest):
                    raise ValueError("Target conversation has a different employee binding")
                return current
            value = {"schema": 1, "source": "team", **{key: getattr(snapshot, key) for key in ("name", "publication_id", "revision", "digest")}}
            self._write_receipt(root, value)
            return snapshot

    @staticmethod
    def _write_receipt(root, value):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=root, prefix=".employee-", delete=False) as stream:
                temporary = stream.name
                stream.write(json.dumps(value, sort_keys=True).encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, root / "employee-publication.json")
            _sync_dir(root)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

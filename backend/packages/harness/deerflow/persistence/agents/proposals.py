"""Owner-only change proposals. Models may propose, but never confirm them."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime

from deerflow.config.agents_config import validate_agent_name
from deerflow.persistence.agents.drafts import _replace_bytes, patched_definition
from deerflow.persistence.agents.publications import _publication_lock, _read_file, _sync_dir

EDITABLE_FIELDS = frozenset({"display_name", "description", "model", "tool_groups", "skills", "knowledge_scope", "category_id", "tags", "avatar_id", "model_settings", "thinking_enabled", "reasoning_effort"})


class EmployeeProposalStore:
    MAX_PENDING = 20
    MAX_HISTORY = 1000

    def __init__(self, paths):
        self.paths = paths

    def _root(self, name, owner):
        name = validate_agent_name(name)
        if not name or not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", owner):
            raise ValueError("Invalid proposal owner")
        root = self.paths.user_agent_dir(owner, name) / ".change-proposals"
        if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)() for p in (root, root.parent)):
            raise ValueError("Unsafe employee proposal path")
        return root

    def _path(self, name, owner, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("Invalid proposal identifier")
        path = self._root(name, owner) / (identifier + ".json")
        if path.is_symlink():
            raise ValueError("Unsafe employee proposal path")
        return path

    def get(self, name, owner, identifier):
        record = json.loads(_read_file(self._path(name, owner, identifier)))
        if not isinstance(record, dict) or record.get("schema") != 1 or record.get("id") != identifier or record.get("owner") != owner or record.get("name") != name or record.get("state") not in ("pending", "applied", "rejected"):
            raise ValueError("Invalid employee proposal record")
        if not isinstance(record.get("updates"), dict) or set(record["updates"]) - EDITABLE_FIELDS:
            raise ValueError("Invalid employee proposal updates")
        return record

    def create(self, name, owner, definition, updates, soul):
        if not updates and soul is None:
            raise ValueError("Empty employee proposal")
        if set(updates) - EDITABLE_FIELDS or definition.config["name"] != name:
            raise ValueError("Employee proposal may not change identity or private fields")
        target = patched_definition(definition, updates, soul, definition.revision)
        root = self._root(name, owner)
        record = {
            "schema": 1,
            "id": uuid.uuid4().hex,
            "name": name,
            "owner": owner,
            "state": "pending",
            "created_at": datetime.now(UTC).isoformat(),
            "expected_draft_revision": definition.revision,
            "target_revision": target.revision,
            "before": {key: definition.config.get(key) for key in updates},
            "before_soul": definition.soul if soul is not None else None,
            "updates": updates,
            "soul": soul,
        }
        payload = json.dumps(record, ensure_ascii=False, sort_keys=True).encode()
        if len(payload) > 2 * 1024 * 1024:
            raise ValueError("Employee proposal exceeds size limit")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with _publication_lock(root / ".proposals.lock"):
            pending = 0
            for index, existing_path in enumerate(root.glob("*.json")):
                if index >= self.MAX_HISTORY:
                    raise ValueError("Employee proposal history limit reached; administrator review required")
                existing = self.get(name, owner, existing_path.stem)
                if existing["state"] == "pending":
                    if existing["expected_draft_revision"] == definition.revision and existing["target_revision"] == target.revision:
                        return existing
                    pending += 1
            if pending >= self.MAX_PENDING:
                raise ValueError("Too many pending employee proposals; review or reject them before proposing more")
            _replace_bytes(self._path(name, owner, record["id"]), payload)
            _sync_dir(root)
        return record

    def list(self, name, owner):
        records = []
        for index, path in enumerate(self._root(name, owner).glob("*.json")):
            if index >= self.MAX_HISTORY:
                raise ValueError("Employee proposal history limit exceeded")
            record = self.get(name, owner, path.stem)
            records.append({key: record[key] for key in ("id", "state", "created_at")})
        records.sort(key=lambda r: (r["state"] == "pending", r["created_at"], r["id"]), reverse=True)
        return records

    def finish(self, name, owner, identifier, *, state, current_revision):
        path = self._path(name, owner, identifier)
        with _publication_lock(path.with_suffix(".lock")):
            record = self.get(name, owner, identifier)
            if state not in ("applied", "rejected") or record["state"] not in ("pending", state):
                raise ValueError("Employee proposal decision is terminal")
            if state == "applied" and current_revision != record["target_revision"]:
                raise ValueError("Employee proposal has not been applied")
            record = {**record, "state": state}
            _replace_bytes(path, json.dumps(record, ensure_ascii=False, sort_keys=True).encode())
            _sync_dir(path.parent)
            return record

    def apply(self, name, owner, identifier, *, agent_store, validated_updates, validated_soul):
        path = self._path(name, owner, identifier)
        with _publication_lock(path.with_suffix(".lock")):
            record = self.get(name, owner, identifier)
            if record["state"] == "rejected":
                raise ValueError("Employee proposal was rejected")
            current = agent_store.read_definition(name, user_id=owner)
            if current.revision != record["target_revision"]:
                if record["state"] != "pending":
                    raise ValueError("Applied proposal may not overwrite a later edit")
                target = patched_definition(current, validated_updates, validated_soul, record["expected_draft_revision"])
                if target.revision != record["target_revision"]:
                    raise ValueError("Proposal resources changed; a new review is required")
                current = agent_store.patch_definition(name, validated_updates, validated_soul, user_id=owner, expected_revision=record["expected_draft_revision"])
            # If a crash happened after the definition commit, the exact target
            # digest above reconciles the result without replaying a mutation.
            record = {**record, "state": "applied"}
            _replace_bytes(path, json.dumps(record, ensure_ascii=False, sort_keys=True).encode())
            _sync_dir(path.parent)
            return current

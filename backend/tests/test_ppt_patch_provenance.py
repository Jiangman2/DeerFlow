"""Negative scope checks against small local Git repositories, without history."""

import copy
import hashlib
from pathlib import Path

import pytest
from support import ppt_patch_provenance as provenance

VENDOR = "vendor/ppt-master-v6.6.0"
PUBLIC = "skills/public"


def put(root: Path, path: str, value: bytes) -> None:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(value)


@pytest.fixture
def skill_repo(tmp_path):
    provenance.git(tmp_path, "init", "-q")
    provenance.git(tmp_path, "config", "core.autocrlf", "false")
    provenance.git(tmp_path, "config", "core.filemode", "true")
    old_files = {"scripts/confirm_ui/server.py": b"old server\n", "scripts/confirm_ui/static/app.js": b"old app\n"}
    for relative, raw in old_files.items():
        put(tmp_path, VENDOR + "/" + relative, raw)
    # These names exercise Git's directory suffix sorting and UTF-8 byte order.
    for path in ("authority.txt", "order/item", "order.txt", "order0", "\u4e2d\u6587.txt"):
        put(tmp_path, VENDOR + "/" + path, b"ABC\n")
    put(tmp_path, PUBLIC + "/fixture/SKILL.md", b"public authority\n")
    provenance.git(tmp_path, "add", "--", VENDOR, PUBLIC)
    original_tree = provenance.git(tmp_path, "write-tree").decode().strip()
    baseline = {
        "vendor": {
            "path": VENDOR,
            "tree_oid": provenance.git(tmp_path, "rev-parse", original_tree + ":" + VENDOR).decode().strip(),
            "allowed_files": {relative: {"mode": "100644", "blob_oid": provenance.object_oid("blob", raw), "base_sha256": hashlib.sha256(raw).hexdigest()} for relative, raw in old_files.items()},
        },
        "public": {"path": PUBLIC, "tree_oid": provenance.git(tmp_path, "rev-parse", original_tree + ":" + PUBLIC).decode().strip()},
    }
    baseline["vendor"]["allowed_files"]["scripts/confirm_ui/conditional_protocol.py"] = {"mode": None, "blob_oid": None, "base_sha256": None}
    for relative in provenance.ALLOWED_VENDOR_FILES:
        put(tmp_path, VENDOR + "/" + relative, b"authorized new implementation\n")
    provenance.git(tmp_path, "add", "--", VENDOR)
    return tmp_path, baseline


def test_scope_reconstructs_git_tree_after_only_authorized_changes(skill_repo):
    root, baseline = skill_repo
    provenance.assert_worktree_scope(root, baseline)
    # The expected trees above were written by Git, not by the implementation.
    changed = copy.deepcopy(baseline)
    changed["vendor"]["tree_oid"] = "0" * 40
    with pytest.raises(AssertionError, match="unauthorized staged"):
        provenance.assert_worktree_scope(root, changed)


@pytest.mark.parametrize("change", ["same_length", "mode_only"])
def test_unauthorized_staged_vendor_change_is_rejected(skill_repo, change):
    root, baseline = skill_repo
    path = VENDOR + "/authority.txt"
    if change == "same_length":
        put(root, path, b"XYZ\n")
        provenance.git(root, "add", "--", path)
    else:
        provenance.git(root, "update-index", "--chmod=+x", "--", path)
    with pytest.raises(AssertionError, match="unauthorized staged"):
        provenance.assert_worktree_scope(root, baseline)


def test_unstaged_vendor_change_is_not_hidden_by_matching_index(skill_repo):
    root, baseline = skill_repo
    put(root, VENDOR + "/authority.txt", b"unapproved change\n")
    with pytest.raises(AssertionError, match="unauthorized unstaged"):
        provenance.assert_worktree_scope(root, baseline)


@pytest.mark.parametrize("change", ["staged_add", "staged_delete", "unstaged_delete", "untracked_add"])
def test_public_skill_additions_and_deletions_are_rejected(skill_repo, change):
    root, baseline = skill_repo
    if change.endswith("add"):
        put(root, PUBLIC + "/new/SKILL.md", b"not part of the reviewed public tree\n")
    else:
        (root / PUBLIC / "fixture/SKILL.md").unlink()
    if change.startswith("staged"):
        provenance.git(root, "add", "-A", "--", PUBLIC)
    with pytest.raises(AssertionError, match="public Skills|unauthorized unstaged|unauthorized untracked"):
        provenance.assert_worktree_scope(root, baseline)


def test_authorized_path_does_not_authorize_mode_change(skill_repo):
    root, baseline = skill_repo
    provenance.git(root, "update-index", "--chmod=+x", "--", VENDOR + "/scripts/confirm_ui/server.py")
    with pytest.raises(AssertionError, match="authorized file mode"):
        provenance.assert_worktree_scope(root, baseline)


@pytest.mark.parametrize(
    "raw",
    [
        b"100644 " + b"a" * 40 + b" 1\tvendor/ppt-master-v6.6.0/x\0",
        (b"100644 " + b"a" * 40 + b" 0\tvendor/ppt-master-v6.6.0/x\0") * 2,
        b"100600 " + b"a" * 40 + b" 0\tvendor/ppt-master-v6.6.0/x\0",
        b"100644 not-an-oid 0\tvendor/ppt-master-v6.6.0/x\0",
    ],
)
def test_index_conflicts_duplicates_modes_and_ids_are_rejected(raw):
    with pytest.raises(AssertionError):
        provenance.index_entries(raw, (VENDOR, PUBLIC))


def test_pinned_source_and_manifest_tampering_are_rejected(tmp_path, monkeypatch):
    baseline = provenance.manifest()
    path = "docker/sandbox-ppt-master/ppt_research_stdio.py"
    original = provenance.original_source(path)
    (tmp_path / "manifest.json").write_bytes((provenance.FIXTURES / "manifest.json").read_bytes())
    fixture_name = baseline["runtime_sources"][path]["fixture"]
    (tmp_path / fixture_name).write_bytes(original + b"\n")
    monkeypatch.setattr(provenance, "FIXTURES", tmp_path)
    with pytest.raises(AssertionError, match="baseline source changed"):
        provenance.original_source(path)
    (tmp_path / "manifest.json").write_bytes(b"{}\n")
    with pytest.raises(AssertionError, match="baseline manifest changed"):
        provenance.manifest()

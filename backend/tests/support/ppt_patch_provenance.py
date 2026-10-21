"""Portable, pinned PPT baselines without historical Git-object availability.

The source fixtures preserve reviewable Python, parsed by the *current* Python
interpreter. Whole-tree scope is checked from the current Git index, reversing
only the three authorized vendor entries before reproducing the old tree OID.
No index or Git object is written by these checks.
"""

import hashlib
import json
import re
import subprocess
from pathlib import Path

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/ppt_patch_provenance"
MANIFEST_SHA256 = "fccd8bb54bb736108cce55cee72d93a1086badff69a5f01577e8a84d49dcec48"
SOURCE_COMMIT = "0044d221d4338ddf455f57c46435920d896a5824"
PUBLIC_SKILLS_BASE = "ff96696aebd7092af395fbcafc423499d9a8e729"
GIT_TIMEOUT = 30
ALLOWED_VENDOR_FILES = {"scripts/confirm_ui/server.py", "scripts/confirm_ui/conditional_protocol.py", "scripts/confirm_ui/static/app.js"}
GIT_MODES = {"100644", "100755", "120000", "160000"}


def object_oid(kind: str, raw: bytes) -> str:
    return hashlib.sha1(kind.encode("ascii") + b" " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def manifest() -> dict:
    raw = (FIXTURES / "manifest.json").read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(raw).hexdigest() == MANIFEST_SHA256, "reviewed PPT baseline manifest changed"
    value = json.loads(raw)
    assert value["schema"] == "ppt-patch-provenance-baselines.v1"
    assert value["source_commit"] == value["vendor"]["source_commit"] == SOURCE_COMMIT
    assert value["public"]["source_commit"] == PUBLIC_SKILLS_BASE
    assert value["vendor"]["allowed_files"].keys() == ALLOWED_VENDOR_FILES
    return value


def original_source(path: str) -> bytes:
    record = manifest()["runtime_sources"][path]
    assert record["source_commit"] == SOURCE_COMMIT and record["source_path"] == path
    raw = (FIXTURES / record["fixture"]).read_bytes().replace(b"\r\n", b"\n")
    assert len(raw) == record["bytes"] and hashlib.sha256(raw).hexdigest() == record["sha256"], (path, "baseline source changed")
    assert object_oid("blob", raw) == record["blob_oid"], (path, "baseline Git blob identity changed")
    return raw


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True, timeout=GIT_TIMEOUT).stdout


def index_entries(raw: bytes, prefixes: tuple[str, ...]) -> dict[str, tuple[str, str]]:
    result = {}
    assert not raw or raw.endswith(b"\0"), "truncated Git index projection"
    for entry in raw.split(b"\0")[:-1]:
        fields, path_raw = entry.split(b"\t", 1)
        mode, oid, stage = fields.decode("ascii").split(" ")
        path = path_raw.decode("utf-8")
        assert stage == "0", (path, "unmerged index entry")
        assert mode in GIT_MODES and re.fullmatch(r"[0-9a-f]{40}", oid), (path, "invalid mode or object ID")
        assert all(part not in ("", ".", "..") for part in path.split("/")) and "\\" not in path, "invalid index path"
        assert any(path.startswith(prefix + "/") for prefix in prefixes), "out-of-scope index projection"
        assert path not in result, (path, "duplicate index entry")
        result[path] = mode, oid
    return result


def tree_oid(entries: dict[str, tuple[str, str]]) -> str:
    """Recreate Git's tree encoding, including mode and bytewise directory order."""
    root = {}
    for path, value in entries.items():
        parts = path.split("/")
        assert all(part not in ("", ".", "..") for part in parts), "invalid relative tree path"
        node = root
        for part in parts[:-1]:
            node = node.setdefault(part, {})
            assert isinstance(node, dict), (path, "file/directory collision")
        assert parts[-1] not in node, (path, "duplicate tree path")
        mode, oid = value
        assert mode in GIT_MODES and re.fullmatch(r"[0-9a-f]{40}", oid), "invalid tree leaf"
        node[parts[-1]] = value

    def encode(node):
        records = []
        for name, value in node.items():
            raw_name = name.encode("utf-8")
            assert b"\0" not in raw_name and b"/" not in raw_name, "invalid Git tree name"
            directory = isinstance(value, dict)
            mode, oid = ("40000", encode(value)) if directory else value
            records.append((raw_name + (b"/" if directory else b""), mode.encode("ascii") + b" " + raw_name + b"\0" + bytes.fromhex(oid)))
        return object_oid("tree", b"".join(record for _, record in sorted(records)))

    return encode(root)


def assert_index_scope(entries: dict[str, tuple[str, str]], baseline: dict) -> None:
    vendor = baseline["vendor"]
    original = {path[len(vendor["path"]) + 1 :]: record for path, record in entries.items() if path.startswith(vendor["path"] + "/")}
    assert vendor["allowed_files"].keys() == ALLOWED_VENDOR_FILES
    for relative, old in vendor["allowed_files"].items():
        if relative in original:
            assert original[relative][0] == (old["mode"] or "100644"), (relative, "authorized file mode changed")
        if old["blob_oid"] is None:
            assert old["mode"] is None and old["base_sha256"] is None
            original.pop(relative, None)
        else:
            original[relative] = old["mode"], old["blob_oid"]
    assert tree_oid(original) == vendor["tree_oid"], "unauthorized staged vendor file, mode, or authority change"
    public = baseline["public"]
    entries = {path[len(public["path"]) + 1 :]: record for path, record in entries.items() if path.startswith(public["path"] + "/")}
    assert tree_oid(entries) == public["tree_oid"], "public Skills differ from their reviewed tree"


def assert_worktree_scope(root: Path, baseline: dict) -> None:
    prefixes = baseline["vendor"]["path"], baseline["public"]["path"]
    # One current-index inventory covers both trees; no historical commit lookup.
    entries = index_entries(git(root, "ls-files", "--stage", "-z", "--", *prefixes), prefixes)
    assert_index_scope(entries, baseline)
    allowed = {prefixes[0] + "/" + relative for relative in ALLOWED_VENDOR_FILES}
    changed = git(root, "diff", "--no-ext-diff", "--name-only", "--no-renames", "-z", "--ignore-submodules=none", "--", *prefixes)
    assert {path.decode("utf-8") for path in changed.split(b"\0") if path} <= allowed, "unauthorized unstaged Skill change"
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z", "--", *prefixes)
    assert {path.decode("utf-8") for path in untracked.split(b"\0") if path} <= allowed, "unauthorized untracked Skill file"

"""The authorized local Skill exception is exactly the confirmation transport."""

import hashlib
import json
from pathlib import Path

from support.ppt_patch_provenance import assert_worktree_scope, manifest

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "vendor/ppt-master-v6.6.0"
LOCK = ROOT / "docker/sandbox-ppt-master/PPT_MASTER_CONFIRMATION_PATCH.json"


def test_native_patch_has_exact_allowed_files_and_upstream_identity():
    lock = json.loads(LOCK.read_bytes())
    baseline = manifest()
    assert lock["release_worktree_base"] == baseline["source_commit"]
    assert lock["scope"] == "isolated_ppt_master_confirmation_only"
    assert lock["upstream_commit"] == "a50758ac29ec027e85966db33e2ae80031446756"
    assert lock["upstream_skill_tree"] == "e2d34480203f71c44b5465e194a8d232c6e5c8be"
    assert lock["user_consent_is_separate"] is True
    assert lock["files"].keys() == {"scripts/confirm_ui/server.py", "scripts/confirm_ui/conditional_protocol.py", "scripts/confirm_ui/static/app.js"}
    for relative, record in lock["files"].items():
        raw = (VENDOR / relative).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(raw).hexdigest() == record["patched_sha256"]
        assert record["base_sha256"] == baseline["vendor"]["allowed_files"][relative]["base_sha256"]


def test_native_patch_provenance_is_carried_by_host_image():
    dockerfile = (ROOT / "docker/sandbox-ppt-master/Dockerfile").read_text()
    assert "COPY docker/sandbox-ppt-master/PPT_MASTER_CONFIRMATION_PATCH.json /opt/ppt-master-agent-host/PPT_MASTER_CONFIRMATION_PATCH.json" in dockerfile


def test_native_changes_do_not_include_other_skill_or_authority_files():
    lock = json.loads(LOCK.read_bytes())
    baseline = manifest()
    assert lock["release_worktree_base"] == baseline["vendor"]["source_commit"]
    assert lock["files"].keys() == baseline["vendor"]["allowed_files"].keys()
    assert_worktree_scope(ROOT, baseline)

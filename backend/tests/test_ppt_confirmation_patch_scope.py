"""The authorized local Skill exception is exactly the confirmation transport."""

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ROOT / "vendor/ppt-master-v6.6.0"
LOCK = ROOT / "docker/sandbox-ppt-master/PPT_MASTER_CONFIRMATION_PATCH.json"
# Local Windows repository/antivirus I/O can exceed five seconds. This timeout
# bounds read-only Git evidence checks only; no runtime/network gate is relaxed.
GIT_TIMEOUT = 30
# The reviewed portal base already contains the restored persona-analyzer Skill.
# It is not part of this PPT patch. Keep the vendored native patch compared to
# its own older upstream/release base, and reject ANY later public-Skill delta.
PUBLIC_SKILLS_BASE = "ff96696aebd7092af395fbcafc423499d9a8e729"


def test_native_patch_has_exact_allowed_files_and_upstream_identity():
    lock = json.loads(LOCK.read_bytes())
    assert lock["scope"] == "isolated_ppt_master_confirmation_only"
    assert lock["upstream_commit"] == "a50758ac29ec027e85966db33e2ae80031446756"
    assert lock["upstream_skill_tree"] == "e2d34480203f71c44b5465e194a8d232c6e5c8be"
    assert lock["user_consent_is_separate"] is True
    assert lock["files"].keys() == {"scripts/confirm_ui/server.py", "scripts/confirm_ui/conditional_protocol.py", "scripts/confirm_ui/static/app.js"}
    for relative, record in lock["files"].items():
        raw = (VENDOR / relative).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(raw).hexdigest() == record["patched_sha256"]
        ref = lock["release_worktree_base"] + ":vendor/ppt-master-v6.6.0/" + relative
        result = subprocess.run(["git", "show", ref], cwd=ROOT, capture_output=True, timeout=GIT_TIMEOUT)
        if record["base_sha256"] is None:
            assert result.returncode != 0
        else:
            assert result.returncode == 0
            assert hashlib.sha256(result.stdout.replace(b"\r\n", b"\n")).hexdigest() == record["base_sha256"]


def test_native_patch_provenance_is_carried_by_host_image():
    dockerfile = (ROOT / "docker/sandbox-ppt-master/Dockerfile").read_text()
    assert "COPY docker/sandbox-ppt-master/PPT_MASTER_CONFIRMATION_PATCH.json /opt/ppt-master-agent-host/PPT_MASTER_CONFIRMATION_PATCH.json" in dockerfile


def test_native_changes_do_not_include_other_skill_or_authority_files():
    lock = json.loads(LOCK.read_bytes())
    subprocess.run(["git", "merge-base", "--is-ancestor", PUBLIC_SKILLS_BASE, "HEAD"], cwd=ROOT, check=True, timeout=GIT_TIMEOUT)
    changed = subprocess.check_output(["git", "diff", "--name-only", lock["release_worktree_base"], "--", "vendor/ppt-master-v6.6.0"], cwd=ROOT, timeout=GIT_TIMEOUT).decode().splitlines()
    allowed = {"vendor/ppt-master-v6.6.0/" + relative for relative in lock["files"]}
    assert set(changed) <= allowed
    public_changes = subprocess.check_output(["git", "diff", "--name-only", PUBLIC_SKILLS_BASE, "--", "skills/public"], cwd=ROOT, timeout=GIT_TIMEOUT).decode().splitlines()
    assert public_changes == []
    untracked = subprocess.check_output(["git", "ls-files", "--others", "--exclude-standard", "--", "vendor/ppt-master-v6.6.0", "skills/public"], cwd=ROOT, timeout=GIT_TIMEOUT).decode().splitlines()
    # The new protocol is deliberately untracked until this candidate commits.
    assert set(untracked) <= allowed

"""Verify the committed financial Skill against the accepted production receipt."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
LOCK = "deploy/enterprise/research-fund-production-source-lock-20261027.json"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD")
    args = parser.parse_args()
    revision = subprocess.check_output(
        ["git", "rev-parse", "--verify", args.ref + "^{commit}"], cwd=ROOT
    ).decode().strip()

    def read(path):
        return subprocess.check_output(["git", "show", revision + ":" + path], cwd=ROOT)

    lock = json.loads(read(LOCK))
    manifest = json.loads(read(lock["candidate_manifest"]))
    root = lock["skill_root"]
    actual = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", revision, "--", root], cwd=ROOT
    ).decode().splitlines()
    expected = [root + "/" + row["path"] for row in manifest["files"]]
    assert sorted(actual) == sorted(expected), "SKILL_FILE_SET_CHANGED"
    for row in manifest["files"]:
        data = read(root + "/" + row["path"])
        assert len(data) == row["bytes"], row["path"]
        assert hashlib.sha256(data).hexdigest() == row["sha256"], row["path"]
    tree = hashlib.sha256(json.dumps(
        manifest["files"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()).hexdigest()
    assert tree == manifest["tree_sha256"] == lock["deployed_tree_sha256"]
    for row in lock["evidence_files"]:
        data = read(row["path"])
        assert len(data) == row["bytes"], row["path"]
        assert hashlib.sha256(data).hexdigest() == row["sha256"], row["path"]
    data = read(lock["production_report"])
    assert hashlib.sha256(data).hexdigest() == lock["production_report_sha256"]
    receipt = json.loads(data)
    assert receipt["skill_updated"] and receipt["runtime_node_views_accepted"]
    assert receipt["publication"]["live_manifest_verified"]
    assert receipt["after_tree_sha256"] == tree
    assert len(receipt["after_consumers"]) == 5
    assert all(row["status"] == "PASS" and row["tree_sha256"] == tree
               for row in receipt["after_consumers"])
    assert receipt["status"] == "INCOMPLETE"  # Preserve the original cleanup failure.
    cleanup = json.loads(read(lock["cleanup_receipt"]))
    assert cleanup["delete_command_acknowledged"]
    assert cleanup["original_update_report_sha256"] == lock["production_report_sha256"]
    print(json.dumps({"status": "COMMITTED_SKILL_MATCHES_ACCEPTED_PRODUCTION_FILES",
                      "revision": revision, "skill_files": len(expected),
                      "tree_sha256": tree, "business_acceptance_pending": True}, sort_keys=True))


if __name__ == "__main__":
    main()

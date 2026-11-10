"""Verify the committed financial Skill against the accepted production receipt."""

import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_fund_current_source_20261110 as current


ROOT = Path(__file__).resolve().parents[2]
LOCK = "deploy/enterprise/research-fund-production-source-lock-20261027.json"
_spec = importlib.util.spec_from_file_location(
    "research_fund_source_20261105", Path(__file__).with_name("research_fund_source_20261105.py")
)
successor = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(successor)
_late_spec = importlib.util.spec_from_file_location(
    "research_fund_late_source_20261105", Path(__file__).with_name("research_fund_late_source_20261105.py")
)
later = importlib.util.module_from_spec(_late_spec)
_late_spec.loader.exec_module(later)


def verify(read, actual):
    """Validate current successor files, then the unchanged October 27 evidence."""
    current_result, current_projection = current.qualify(read, actual)
    if current_projection is not None:
        current_read = read
        read = lambda path: current_projection[path] if path in current_projection else current_read(path)
        actual = [p for p in current_projection if p.startswith(current.SKILL)]
    latest, projection = later.qualify(read, actual)
    if projection is not None:
        late_read = read
        read = lambda path: projection[path] if path in projection else late_read(path)
    lock = json.loads(read(LOCK))
    manifest = json.loads(read(lock["candidate_manifest"]))
    root = lock["skill_root"]
    qualification, original = successor.qualify(read, actual)
    skill_read = original.__getitem__ if original is not None else read
    historical_paths = original if original is not None else actual
    expected = [root + "/" + row["path"] for row in manifest["files"]]
    assert sorted(historical_paths) == sorted(expected), "SKILL_FILE_SET_CHANGED"
    for row in manifest["files"]:
        data = skill_read(root + "/" + row["path"])
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
    result = {"status": "COMMITTED_SKILL_MATCHES_ACCEPTED_PRODUCTION_FILES",
              "skill_files": len(expected), "tree_sha256": tree,
              "business_acceptance_pending": True}
    if qualification:
        result.update(qualification, historical_skill_files=len(expected),
                      historical_tree_sha256=tree)
    if latest:
        result.update(latest)
    if current_result:
        result.update(current_result)
    return result


def snapshot(ref):
    if ref is None:
        paths = [p.relative_to(ROOT).as_posix() for p in (ROOT / successor.SKILL).rglob("*") if p.is_file()]
        return lambda path: (ROOT / path).read_bytes(), paths, "WORKTREE"
    revision = subprocess.check_output(
        ["git", "rev-parse", "--verify", ref + "^{commit}"], cwd=ROOT
    ).decode().strip()
    roots = [LOCK, successor.SKILL, successor.ARCHIVE, successor.NOV04, successor.NOV05,
             "release/production-source-20261027/research-fund-settlement", *successor.SUPPORT_PATHS,
             later.ARCHIVE, later.ROUND3, later.PROJECT_TYPE, later.INSPECTOR, *later.SUPPORT_PATHS]
    roots.extend((current.ARCHIVE, current.OLD, current.NEW, 'tests/skills',
                  current.adjusted.ARCHIVE,
                  'tests/test_research_fund_expense_purposes_release_20261110.py',
                  'tests/test_research_fund_expense_purposes_preflight_20261110.py',
                  'deploy/enterprise/inspect-portal-research-fund-expense-purposes-20261110-r1.py'))
    names = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", revision, "--", *roots], cwd=ROOT
    ).decode().splitlines()
    data = subprocess.check_output(
        ["git", "cat-file", "--batch"], cwd=ROOT,
        input="".join(revision + ":" + path + "\n" for path in names).encode(),
    )
    stream, files = io.BytesIO(data), {}
    for path in names:
        header = stream.readline().split()
        successor.require(len(header) == 3 and header[1] == b"blob", "FINANCE_GIT_BLOB_MISSING")
        files[path] = stream.read(int(header[2]))
        successor.require(stream.read(1) == b"\n", "FINANCE_GIT_BATCH_INVALID")
    return files.__getitem__, [p for p in names if p.startswith(successor.SKILL + "/")], revision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--ref", default="HEAD")
    mode.add_argument("--worktree", action="store_true", help="Verify local files before committing")
    args = parser.parse_args()
    read, paths, revision = snapshot(None if args.worktree else args.ref)
    result = verify(read, paths)
    result["revision"] = revision
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()

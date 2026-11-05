"""Offline provenance for the November 4/5 shared financial Skill updates.

Existing operator records remain byte-identical. No archived script is executed.
"""

import base64
import hashlib
import io
import json
from pathlib import PurePosixPath
import tarfile

ARCHIVE = "release/research-fund-source-20261105/"
MANIFEST = ARCHIVE + "SOURCE.json"
MANIFEST_SHA256 = "ba3eb7fe8a8c02e6c2b92c62021c33649bae76a2ef7b742744ea81835edee8a7"
LOCK = "deploy/enterprise/research-fund-production-source-lock-20261027.json"
SKILL = "skills/public/research-fund-settlement"
NOV04 = "release/research-fund-feedback-20261104/"
NOV05 = "release/research-fund-feedback-20261105/"
SUPPORT_PATHS = frozenset(['deploy/enterprise/cleanup-portal-research-fund-skill-20261104-r1.py', 'deploy/enterprise/inspect-portal-research-fund-skill-20261104-r1.py', 'deploy/enterprise/inspect-portal-research-fund-skill-20261105-r2.py', 'deploy/enterprise/update-portal-research-fund-skill-20261104-r1.py', 'deploy/enterprise/update-portal-research-fund-skill-20261105-r1.py', 'scripts/review_research_fund_templates.py', 'tests/skills/test_research_fund_feedback.py', 'tests/skills/test_research_fund_nov05_import.py', 'tests/skills/test_research_fund_nov05_model.py', 'tests/skills/test_research_fund_nov05_roundtrip.py'])


def require(condition, code):
    if not condition:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_path(path):
    value = PurePosixPath(path)
    require(not value.is_absolute() and ".." not in value.parts and "\\" not in path
            and value.as_posix() == path, "FINANCE_UNSAFE_PATH")


def inventory(manifest):
    rows = manifest["files"]
    names = [row["path"] for row in rows]
    for name in names:
        safe_path(name)
    require(names == sorted(set(names)), "FINANCE_MANIFEST_FILE_SET")
    tree = sha(json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode())
    require(tree == manifest["tree_sha256"], "FINANCE_TREE_CHANGED")
    if "file_count" in manifest:
        require(len(rows) == manifest["file_count"], "FINANCE_FILE_COUNT")
    if "bytes" in manifest:
        require(sum(row["bytes"] for row in rows) == manifest["bytes"], "FINANCE_TOTAL_BYTES")
    return rows, tree


def match_files(read, manifest, paths, prefix):
    rows, tree = inventory(manifest)
    expected = {prefix + row["path"] for row in rows}
    require(set(paths) == expected, "FINANCE_SKILL_FILE_SET_CHANGED")
    for row in rows:
        raw = read(prefix + row["path"])
        require(len(raw) == row["bytes"] and sha(raw) == row["sha256"],
                "FINANCE_SOURCE_CHANGED: " + row["path"])
    return tree


def historical_files(read, lock):
    bundle = json.loads(read(NOV04 + "old-bundle.json"))
    parent = json.loads(read(lock["candidate_manifest"]))
    require(bundle["after"] == parent, "FINANCE_HISTORICAL_PARENT_CHANGED")
    raw = base64.b64decode(bundle["archive_b64"], validate=True)
    require(sha(raw) == bundle["archive_sha256"] == lock["archive_sha256"],
            "FINANCE_HISTORICAL_ARCHIVE_CHANGED")
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in archive:
            safe_path(member.name)
            prefix = "research-fund-settlement/"
            require(member.isfile() and member.name.startswith(prefix), "FINANCE_ARCHIVE_MEMBER")
            path = SKILL + "/" + member.name[len(prefix):]
            require(path not in files, "FINANCE_DUPLICATE_MEMBER")
            files[path] = archive.extractfile(member).read()
    require(match_files(files.__getitem__, parent, files, SKILL + "/")
            == lock["deployed_tree_sha256"], "FINANCE_HISTORICAL_TREE_CHANGED")
    return files


def check_receipt(receipt, before, after):
    require(receipt["skill_updated"] and receipt["runtime_node_views_accepted"]
            and receipt["publication"]["live_manifest_verified"], "FINANCE_RUNTIME_NOT_ACCEPTED")
    require(receipt["before_tree_sha256"] == before and receipt["after_tree_sha256"] == after,
            "FINANCE_RECEIPT_TREE_CHANGED")
    require(receipt["browser_business_acceptance_pending"] is True
            and receipt["production_database_access"] is False
            and receipt["production_workload_mutation"] is False
            and receipt["workload_patch_requests"] == 0 and receipt["provider_requests"] == 0,
            "FINANCE_RECEIPT_SCOPE_CHANGED")
    for key, tree in (("before_consumers", before), ("after_consumers", after)):
        rows = receipt[key]
        require(len(rows) == 5 and len({row["node"] for row in rows}) == 5
                and all(row["status"] == "PASS" and row["tree_sha256"] == tree for row in rows),
                "FINANCE_NODE_RECEIPT_CHANGED")


def qualify(read, skill_paths):
    try:
        raw = read(MANIFEST)
    except (FileNotFoundError, KeyError):
        return None, None
    require(sha(raw) == MANIFEST_SHA256, "FINANCE_SOURCE_SEAL_CHANGED")
    manifest = json.loads(raw)
    require(manifest["schema"] == "research-fund-source-successor.v1"
            and manifest["status"] == "SOURCE_MATCHES_OPERATOR_ACCEPTED_SKILL_FILES",
            "FINANCE_SOURCE_SCHEMA")
    require(manifest["skill_root"] == SKILL and manifest["parent_lock"] == LOCK
            and manifest["candidate_manifest"] == NOV05 + "candidate-manifest.json"
            and manifest["historical_bundle"] == NOV04 + "old-bundle.json",
            "FINANCE_SOURCE_PATH_CHANGED")
    require(manifest["scope"] == "ONE_SHARED_PUBLIC_SKILL_NO_DEPLOYMENT_ROLLOUT"
            and manifest["business_acceptance_pending"] is True
            and manifest["production_commands_executed_by_verifier"] is False,
            "FINANCE_ACCEPTANCE_OVERCLAIM")
    require(sha(read(LOCK)) == manifest["parent_lock_sha256"], "FINANCE_PARENT_LOCK_CHANGED")
    require(set(manifest["support_files"]) == SUPPORT_PATHS, "FINANCE_SUPPORT_SET_CHANGED")
    for collection in ("evidence_files", "support_files"):
        for path, row in manifest[collection].items():
            safe_path(path)
            require(collection != "evidence_files" or path.startswith((NOV04, NOV05)),
                    "FINANCE_EVIDENCE_SCOPE")
            current = read(path)
            require(len(current) == row["bytes"] and sha(current) == row["sha256"],
                    "FINANCE_EVIDENCE_CHANGED: " + path)
    lock = json.loads(read(LOCK))
    current = json.loads(read(manifest["candidate_manifest"]))
    current_tree = match_files(read, current, skill_paths, SKILL + "/")
    prior = json.loads(read(NOV04 + "candidate-manifest.json"))
    _, prior_tree = inventory(prior)
    nov04 = json.loads(read(NOV04 + "production-update-20261104.json"))
    nov05 = json.loads(read(NOV05 + "production-update-20261105.json"))
    check_receipt(nov04, lock["deployed_tree_sha256"], prior_tree)
    check_receipt(nov05, prior_tree, current_tree)
    # November 4's original cleanup failure remains a failed receipt.
    require(nov04["status"] == "INCOMPLETE" and nov04["exitcode"] == 1,
            "FINANCE_NOV04_RECEIPT_REWRITTEN")
    cleanup = json.loads(read(NOV04 + "production-cleanup-20261104.json"))
    require(cleanup["status"] == "MAINTENANCE_POD_CLEANED" and cleanup["maintenance_pod_absent"]
            and cleanup["original_report_sha256"] == nov04["report_sha256"]
            and cleanup["original_receipt_modified"] is False,
            "FINANCE_NOV04_CLEANUP_CHANGED")
    require(nov05["status"] == "RESEARCH_FUND_SKILL_UPDATED_RUNTIME_ACCEPTED"
            and nov05["exitcode"] == 0 and nov05["maintenance_pod_absent"]
            and nov05["maintenance_pod_delete_acknowledged"], "FINANCE_NOV05_RECEIPT_CHANGED")
    reconciliation = json.loads(read(NOV05 + "production-update-reconciliation-20261105.json"))
    report = read(NOV05 + "production-update-20261105.json")
    require(reconciliation["operator_report_bytes"] == len(report)
            and reconciliation["operator_report_sha256"] == sha(report)
            and reconciliation["reconstructed_report_exact_hash_match"] is True
            and reconciliation["production_report_downloaded_directly"] is False
            and reconciliation["business_acceptance_pending"] is True,
            "FINANCE_OPERATOR_REPORT_BINDING")
    delta = json.loads(read(NOV05 + "candidate-delta.json"))
    require(delta["baseline_tree"] == prior_tree and delta["candidate_tree"] == current_tree,
            "FINANCE_DELTA_PARENT_CHANGED")
    old_rows = {row["path"]: row for row in prior["files"]}
    new_rows = {row["path"]: row for row in current["files"]}
    expected = [{"path": path, "before": old_rows.get(path), "after": new_rows.get(path)}
                for path in sorted(old_rows.keys() | new_rows.keys())
                if old_rows.get(path) != new_rows.get(path)]
    require(delta["files"] == expected, "FINANCE_DELTA_CHANGED")
    original = historical_files(read, lock)
    return {"status": manifest["status"], "skill_files": len(current["files"]),
            "tree_sha256": current_tree, "evidence_files_verified": len(manifest["evidence_files"]),
            "support_files_verified": len(manifest["support_files"]),
            "business_acceptance_pending": True, "production_commands_executed": False}, original

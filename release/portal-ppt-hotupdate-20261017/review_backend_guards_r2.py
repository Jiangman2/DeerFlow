"""Reconcile the completed real POSIX test run; do not rerun tests."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(r"C:/Users/15285/Documents/deer-flow/portal-ppt-merge-20261017/backend-guards-r2")


def ref(path):
    raw = path.read_bytes()
    return {"path": str(path), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


report_path = ROOT / "INDEPENDENT-POSIX-QUALIFICATION.json"
report = json.loads(report_path.read_text())
assert report["exit_code"] == 0 and report["inputs_stable"]
assert all(ref(ROOT / name)["sha256"] == expected for name, expected in report["inputs"].items())
stderr = ROOT / "INDEPENDENT-POSIX.stderr.log"
assert ref(stderr)["sha256"] == report["stderr_sha256"]
assert "Ran 12 tests" in stderr.read_text() and stderr.read_text().rstrip().endswith("OK")
assert "skipped" not in stderr.read_text()
name = report["command"][report["command"].index("--name") + 1]
command = ["docker", "container", "inspect", name, "--format", "{{.Id}}"]
check = subprocess.run(command, capture_output=True, timeout=30)
expected = ("No such container: " + name).encode()
assert check.returncode == 1 and not check.stdout.strip() and expected in check.stderr
result = {
    "schema": "portal-ppt-backend-guards-independent-review.v1",
    "status": "REVIEW_AND_12_REAL_POSIX_TESTS_PASS",
    "preparation": ref(ROOT / "PREPARATION.json"),
    "original_test_receipt": ref(report_path),
    "test_log": ref(stderr),
    "test_cases_passed": 12,
    "test_skips": 0,
    "test_rerun": False,
    "input_hashes_reconciled": True,
    "cleanup_followup": {"command": command, "exit_code": check.returncode,
                         "stderr_sha256": hashlib.sha256(check.stderr).hexdigest(),
                         "exact_container_absent": True,
                         "original_incomplete_cleanup_receipt_preserved": True},
    "review_scope": [
        "Compared apply helper and both auditors with frozen r1 implementations",
        "Only exact empty regular single-link versions/.gitkeep is inert; global Python-only payload paths unchanged",
        "No-follow directory-fd read checks size, inode, type, links and stability",
        "Reject nonempty, symlink, hardlink, directory, unknown migration entry and hardlink aliases",
        "Full migration hash/AST/connected 20-revision checks retained",
        "Auditor failure retains safe precondition projections without false pass status",
        "Existing candidate and all four payload/manifests remain unchanged"],
    "real_oci_r2_audit": "PENDING", "production_mutation": False,
    "ready_for_build": False, "ready_for_rollout": False}
with (ROOT / "INDEPENDENT-REVIEW.json").open("x", encoding="utf-8") as stream:
    json.dump(result, stream, indent=2)
    stream.write("\n")
print(json.dumps({"status": result["status"], "receipt": ref(ROOT / "INDEPENDENT-REVIEW.json")}))

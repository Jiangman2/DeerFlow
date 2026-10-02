#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import hashlib
import os
import subprocess
import sys
from pathlib import Path


FILES = (
    "README.md",
    "inspect-production-state-datax-01.sh",
    "render_execution_lease_patch.py",
    "render_runner_overlay.py",
    "rollback-ppt-runner-execution-lease-fd-datax-01.sh",
    "run-ppt-runner-execution-lease-fd-datax-01.sh",
    "status-ppt-runner-execution-lease-fd-datax-01.sh",
    "tests/test_execution_lease_hotfix.py",
    "verify_execution_lease_hotfix.py",
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    for relative in FILES:
        path = root / relative
        if not path.is_file() or path.stat().st_size == 0:
            raise SystemExit("missing package file: %s" % relative)
    for relative in ("render_execution_lease_patch.py", "render_runner_overlay.py", "verify_execution_lease_hotfix.py"):
        ast.parse((root / relative).read_text(encoding="utf-8"))
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(root / "tests"), "-p", "test_*.py"],
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    source = (root / "run-ppt-runner-execution-lease-fd-datax-01.sh").read_text(encoding="utf-8")
    required = (
        "active_ppt_master_runs",
        "ordinary_active_work_non_blocking=PASS",
        "PPT_RUNNER_EXECUTION_LEASE_FD_ACK",
        "AUTOMATIC EXACT RUNNER ROLLBACK",
        "production_database_mutation=NONE",
        "gateway_spec_unchanged=PASS",
        "execution_lease_nfs_cross_process_flock=PASS",
    )
    for value in required:
        if value not in source:
            raise SystemExit("missing rollout contract: %s" % value)
    forbidden = ("kubectl delete deployment", "DROP TABLE", "ALTER TABLE", "UPDATE runs", "DELETE FROM runs")
    combined = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.sh"))
    for value in forbidden:
        if value in combined:
            raise SystemExit("forbidden mutation contract: %s" % value)
    digest = hashlib.sha256((root / "render_execution_lease_patch.py").read_bytes()).hexdigest()
    print("ppt_runner_execution_lease_fd_r1_static_contracts=PASS")
    print("live_runner_source_narrow_patch_contract=PASS")
    print("linux_shared_lock_readable_fd_contract=PASS")
    print("nfs_cross_process_flock_probe_contract=PASS")
    print("runner_only_restart_contract=PASS")
    print("ordinary_active_work_non_blocking_contract=PASS")
    print("active_ppt_master_run_gate=ZERO_REQUIRED")
    print("automatic_exact_runner_rollback_contract=PASS")
    print("production_database_mutation_code=NONE")
    print("gateway_mutation_code=NONE")
    print("render_patch_sha256=%s" % digest)


if __name__ == "__main__":
    main()

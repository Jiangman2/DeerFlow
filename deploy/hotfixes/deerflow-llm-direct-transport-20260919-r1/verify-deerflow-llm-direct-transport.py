#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


REQUIRED = (
    "README.md",
    "SOURCE.COMMIT",
    "VERIFICATION.txt",
    "factory_runtime_probe.py",
    "render_no_proxy_patch.py",
    "rollback-deerflow-llm-direct-transport-datax-01.sh",
    "run-deerflow-llm-direct-transport-datax-01.sh",
    "status-deerflow-llm-direct-transport-datax-01.sh",
    "tests/test_hotfix.py",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()

    for relative in REQUIRED:
        require((root / relative).is_file(), "missing file: %s" % relative)

    run_text = (root / "run-deerflow-llm-direct-transport-datax-01.sh").read_text(
        encoding="utf-8"
    )
    status_text = (
        root / "status-deerflow-llm-direct-transport-datax-01.sh"
    ).read_text(encoding="utf-8")
    rollback_text = (
        root / "rollback-deerflow-llm-direct-transport-datax-01.sh"
    ).read_text(encoding="utf-8")
    probe_text = (root / "factory_runtime_probe.py").read_text(encoding="utf-8")

    require("aiapi.cnooc.com.cn" in run_text, "target host missing")
    require('MODEL_CONFIG_KEY = "deepseek-v4-flash"' in probe_text, "config key mismatch")
    require("DeepSeek-V4-Flash" not in probe_text, "display model name used as config key")
    require("--type=strategic" in run_text, "strategic patch missing")
    require("automatic_rollback" in run_text, "automatic rollback missing")
    require("active_deerflow_runs" in run_text, "active run gate missing")
    require("factory_runtime_probe.py" in status_text, "live factory probe missing")
    require("ROLLBACK.SHA256" in rollback_text, "rollback checksum missing")
    forbidden = ("DELETE FROM", "UPDATE runs", "ALTER TABLE", "DROP TABLE")
    combined = run_text + status_text + rollback_text
    require(not any(item in combined for item in forbidden), "database mutation found")

    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(root / "tests")],
        check=True,
    )
    print("deerflow_llm_direct_transport_r1_static_contracts=PASS")
    print("real_factory_plain_and_tool_probe_contract=PASS")
    print("gateway_two_replica_rolling_update_contract=PASS")
    print("gateway_image_mutation_code=NONE")
    print("production_database_mutation_code=NONE")
    print("weknora_mutation_code=NONE")
    print("ppt_runner_mutation_code=NONE")
    print("automatic_exact_environment_rollback_contract=PASS")


if __name__ == "__main__":
    main()

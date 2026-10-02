#!/usr/bin/env python3
"""Fail-closed normalization for retained file-backed Agent configurations."""

from __future__ import print_function

import yaml

from app.gateway.routers.agents import (
    CORPORATE_DIGITAL_EMPLOYEE_CATEGORIES,
    _write_category_to_config_file,
)
from deerflow.config.paths import get_paths


def main():
    paths = get_paths()
    config_files = set((paths.base_dir / "users").glob("*/agents/*/config.yaml"))
    config_files.update(paths.team_agents_dir.glob("*/config.yaml"))
    config_files.update(paths.agents_dir.glob("*/config.yaml"))
    files = sorted(path for path in config_files if path.is_file())

    migrated = 0
    for path in files:
        migrated += int(_write_category_to_config_file(path))

    allowed = {item.id for item in CORPORATE_DIGITAL_EMPLOYEE_CATEGORIES.items}
    invalid = []
    for path in files:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(payload, dict) or payload.get("category_id") not in allowed:
            invalid.append(str(path))
    if invalid:
        raise RuntimeError(
            "file category normalization audit failed: " + ", ".join(invalid[:20])
        )

    print("file_agent_category_rows_seen=%d" % len(files))
    print("file_agent_category_rows_migrated=%d" % migrated)
    print("file_agent_category_normalization=PASS")


if __name__ == "__main__":
    main()

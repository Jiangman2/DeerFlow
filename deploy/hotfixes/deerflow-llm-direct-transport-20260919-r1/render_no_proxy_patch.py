#!/usr/bin/env python3
"""Render narrow strategic-merge patches for Gateway NO_PROXY variables."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


TARGET_NAMES = ("NO_PROXY", "no_proxy")


def append_host(value: str, host: str) -> str:
    tokens = [item.strip() for item in value.split(",") if item.strip()]
    if host not in tokens:
        tokens.append(host)
    return ",".join(tokens)


def container_for(deployment: dict, name: str) -> dict:
    containers = deployment["spec"]["template"]["spec"]["containers"]
    rows = [item for item in containers if item.get("name") == name]
    if len(rows) != 1:
        raise ValueError("expected exactly one container named %s" % name)
    return rows[0]


def original_env(container: dict, name: str) -> dict | None:
    rows = [item for item in container.get("env") or [] if item.get("name") == name]
    if len(rows) > 1:
        raise ValueError("duplicate environment variable: %s" % name)
    return rows[0] if rows else None


def target_env(name: str, value: str) -> dict:
    return {"name": name, "value": value, "valueFrom": None}


def rollback_env(original: dict | None, name: str) -> dict:
    if original is None:
        return {"name": name, "$patch": "delete"}
    row = dict(original)
    if "value" in row:
        row["valueFrom"] = None
    elif "valueFrom" in row:
        row["value"] = None
    else:
        raise ValueError("unsupported environment entry: %s" % name)
    return row


def patch_document(rows: list[dict]) -> dict:
    return {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "gateway",
                            "env": rows,
                        }
                    ]
                }
            }
        }
    }


def render(
    deployment: dict,
    upper_effective: str,
    lower_effective: str,
    host: str,
) -> tuple[dict, dict]:
    container = container_for(deployment, "gateway")
    if not upper_effective and not lower_effective:
        raise ValueError("both effective NO_PROXY variables are empty")
    shared_fallback = upper_effective or lower_effective
    target_rows = [
        target_env("NO_PROXY", append_host(upper_effective or shared_fallback, host)),
        target_env("no_proxy", append_host(lower_effective or shared_fallback, host)),
    ]
    rollback_rows = [
        rollback_env(original_env(container, name), name) for name in TARGET_NAMES
    ]
    return patch_document(target_rows), patch_document(rollback_rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--upper-effective-file", type=Path, required=True)
    parser.add_argument("--lower-effective-file", type=Path, required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--target-output", type=Path, required=True)
    parser.add_argument("--rollback-output", type=Path, required=True)
    args = parser.parse_args()

    deployment = json.loads(args.deployment.read_text(encoding="utf-8"))
    target, rollback = render(
        deployment,
        args.upper_effective_file.read_text(encoding="utf-8"),
        args.lower_effective_file.read_text(encoding="utf-8"),
        args.host,
    )
    args.target_output.write_text(
        json.dumps(target, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    args.rollback_output.write_text(
        json.dumps(rollback, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print("gateway_no_proxy_patch_render=PASS")
    print("target_host=%s" % args.host)


if __name__ == "__main__":
    main()

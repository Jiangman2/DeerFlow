#!/usr/bin/env python3
"""Render or verify the narrow WeKnora model-network deployment patch."""

from __future__ import print_function

import argparse
import json
import sys


APPROVED_MODEL_HOST = "aiapi.cnooc.com.cn"
REQUIRED_EXISTING_SSRF_HOSTS = (
    "deer-flow-mineru-adapter",
    "model.ai.cnooc",
    "gateway",
)
TARGET_ENV_NAMES = (
    "SSRF_WHITELIST_EXTRA",
    "NO_PROXY",
    "no_proxy",
)


def fail(message):
    raise ValueError(message)


def load_document(path):
    if path == "-":
        return json.load(sys.stdin)
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def target_container(document):
    try:
        containers = document["spec"]["template"]["spec"]["containers"]
    except (KeyError, TypeError):
        fail("invalid deployment document")
    matches = [row for row in containers if row.get("name") == "app"]
    if len(matches) != 1:
        fail("weknora app container not found exactly once")
    return matches[0]


def literal_environment(container):
    result = {}
    for row in container.get("env") or []:
        name = row.get("name")
        if name not in TARGET_ENV_NAMES:
            continue
        value = row.get("value")
        if not isinstance(value, str):
            fail("%s must be a literal string" % name)
        if name in result:
            fail("duplicate environment variable: %s" % name)
        result[name] = value
    missing = [name for name in TARGET_ENV_NAMES if name not in result]
    if missing:
        fail("missing environment variables: %s" % ",".join(missing))
    return result


def csv_values(value):
    values = []
    seen = set()
    for raw in value.split(","):
        item = raw.strip()
        if item and item not in seen:
            values.append(item)
            seen.add(item)
    return values


def append_exact(values, hostname):
    if hostname not in values:
        values.append(hostname)
    return values


def render_patch(document):
    container = target_container(document)
    environment = literal_environment(container)
    whitelist = csv_values(environment["SSRF_WHITELIST_EXTRA"])
    missing = [name for name in REQUIRED_EXISTING_SSRF_HOSTS if name not in whitelist]
    if missing:
        fail("baseline SSRF whitelist drift: %s" % ",".join(missing))

    target = {}
    for name in TARGET_ENV_NAMES:
        values = csv_values(environment[name])
        target[name] = ",".join(append_exact(values, APPROVED_MODEL_HOST))

    return {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "app",
                            "env": [
                                {"name": name, "value": target[name]}
                                for name in TARGET_ENV_NAMES
                            ],
                        }
                    ]
                }
            }
        }
    }


def verify_current(document):
    environment = literal_environment(target_container(document))
    for name in TARGET_ENV_NAMES:
        values = csv_values(environment[name])
        if values.count(APPROVED_MODEL_HOST) != 1:
            fail("%s does not contain the approved hostname exactly once" % name)
    whitelist = csv_values(environment["SSRF_WHITELIST_EXTRA"])
    for name in REQUIRED_EXISTING_SSRF_HOSTS:
        if name not in whitelist:
            fail("required SSRF whitelist hostname missing: %s" % name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("render", "check"), required=True)
    parser.add_argument("--deployment", required=True)
    args = parser.parse_args()
    document = load_document(args.deployment)
    if args.mode == "render":
        print(json.dumps(render_patch(document), separators=(",", ":")))
        return
    verify_current(document)
    print(
        "weknora_document_postprocess_model_network=PASS "
        "hostname=%s ssrf=EXACT proxy_bypass=BOTH" % APPROVED_MODEL_HOST
    )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("weknora_document_postprocess_model_network=FAIL detail=%s" % exc, file=sys.stderr)
        raise SystemExit(1)

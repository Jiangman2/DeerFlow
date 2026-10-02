#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

from extract_model_config import load_runtime


SECRET_KEY = "DeepSeek_V4_API_KEY"


def _read_document(path: Path | None) -> dict[str, object]:
    if path is None:
        document = json.load(sys.stdin)
    else:
        document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("Kubernetes Secret document must be an object")
    return document


def _encoded_value(document: dict[str, object]) -> str:
    data = document.get("data")
    if not isinstance(data, dict):
        raise ValueError("Kubernetes Secret data is missing")
    value = data.get(SECRET_KEY)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Kubernetes Secret key {SECRET_KEY} is missing")
    decoded = base64.b64decode(value, validate=True)
    if len(decoded) < 32:
        raise ValueError(f"Kubernetes Secret key {SECRET_KEY} is invalid")
    return value


def _merge_patch(encoded: str) -> str:
    return json.dumps({"data": {SECRET_KEY: encoded}}, separators=(",", ":"))


def main() -> None:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--inspect", action="store_true")
    group.add_argument("--patch-from-model-config", type=Path)
    group.add_argument("--restore-patch", type=Path)
    group.add_argument("--verify", action="store_true")
    parser.add_argument("--model-config", type=Path)
    args = parser.parse_args()

    if args.inspect:
        document = _read_document(None)
        _encoded_value(document)
        metadata = document.get("metadata") or {}
        if not isinstance(metadata, dict) or metadata.get("name") != "deer-flow-env":
            raise ValueError("unexpected Kubernetes Secret")
        print("shared_deepseek_secret_scope=PASS key=DeepSeek_V4_API_KEY")
        return

    if args.patch_from_model_config:
        runtime = load_runtime(args.patch_from_model_config.resolve())
        encoded = base64.b64encode(str(runtime["api_key"]).encode("utf-8")).decode("ascii")
        print(_merge_patch(encoded))
        return

    if args.restore_patch:
        document = _read_document(args.restore_patch.resolve())
        print(_merge_patch(_encoded_value(document)))
        return

    if not args.model_config:
        raise SystemExit("--model-config is required with --verify")
    document = _read_document(None)
    encoded = _encoded_value(document)
    actual = base64.b64decode(encoded, validate=True).decode("utf-8")
    expected = str(load_runtime(args.model_config.resolve())["api_key"])
    if actual != expected:
        raise ValueError("shared DeepSeek API key does not match the locked model configuration")
    print("shared_deepseek_secret_runtime=PASS key=DeepSeek_V4_API_KEY")


if __name__ == "__main__":
    main()

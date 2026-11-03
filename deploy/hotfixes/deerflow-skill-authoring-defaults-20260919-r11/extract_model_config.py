#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path


EXPECTED_FILE_SHA256 = "76887d454606bcb2860db40c43eb8036f86a24cdc6908fd7375f0b76741824ae"
EXPECTED_MODEL = "DeepSeek-V4-Flash"
EXPECTED_BASE_URL = "https://aiapi.cnooc.com.cn/DeepSeek-V4-Flash/FEAT-511-001/v1"


def _literal(node: ast.AST) -> object:
    return ast.literal_eval(node)


def load_runtime(path: Path) -> dict[str, object]:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_FILE_SHA256:
        raise ValueError(f"unexpected model configuration sha256={digest}")
    tree = ast.parse(raw.decode("utf-8-sig"), filename=str(path))

    client_calls: list[ast.Call] = []
    chat_functions: list[ast.FunctionDef] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "OpenAI":
            client_calls.append(node)
        if isinstance(node, ast.FunctionDef) and node.name == "chat_stream":
            chat_functions.append(node)
    if len(client_calls) != 1 or len(chat_functions) != 1:
        raise ValueError("expected exactly one OpenAI client and one chat_stream function")

    client_kwargs = {item.arg: item.value for item in client_calls[0].keywords if item.arg}
    api_key = _literal(client_kwargs["api_key"])
    base_url = _literal(client_kwargs["base_url"])
    if not isinstance(api_key, str) or len(api_key) < 32:
        raise ValueError("invalid API key")
    if base_url != EXPECTED_BASE_URL:
        raise ValueError(f"unexpected base_url={base_url!r}")

    function = chat_functions[0]
    arguments = getattr(function.args, "posonlyargs", []) + function.args.args
    defaults = [None] * (len(arguments) - len(function.args.defaults)) + list(function.args.defaults)
    default_map = {
        argument.arg: None if default is None else _literal(default)
        for argument, default in zip(arguments, defaults)
    }
    if default_map.get("model") != EXPECTED_MODEL:
        raise ValueError(f"unexpected model={default_map.get('model')!r}")
    if default_map.get("enable_thinking") is not True:
        raise ValueError("the example must default enable_thinking to true")

    requests = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "create":
            continue
        keyword_nodes = {item.arg: item.value for item in node.keywords if item.arg}
        if "stream" in keyword_nodes and _literal(keyword_nodes["stream"]) is True:
            requests.append(keyword_nodes)
    if len(requests) != 1:
        raise ValueError("expected exactly one streaming chat completion request")
    extra_body = requests[0].get("extra_body")
    if not isinstance(extra_body, ast.Dict) or len(extra_body.keys) != 1:
        raise ValueError("unexpected extra_body structure")
    if _literal(extra_body.keys[0]) != "enable_thinking":
        raise ValueError("enable_thinking is not forwarded to the request")
    if not isinstance(extra_body.values[0], ast.Name) or extra_body.values[0].id != "enable_thinking":
        raise ValueError("enable_thinking request value is not the function argument")

    return {
        "api_key": api_key,
        "base_url": base_url,
        "file_sha256": digest,
        "model": EXPECTED_MODEL,
        "stream": True,
        "supports_thinking_toggle": True,
        "temperature": float(default_map.get("temperature", 0.0)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--metadata", action="store_true")
    group.add_argument("--runtime-json", action="store_true")
    group.add_argument("--write-key", type=Path)
    args = parser.parse_args()
    runtime = load_runtime(args.input.resolve())
    if args.metadata:
        public = {key: value for key, value in runtime.items() if key != "api_key"}
        print(json.dumps(public, ensure_ascii=False, sort_keys=True))
        return
    if args.runtime_json:
        print(json.dumps(runtime, ensure_ascii=False, sort_keys=True))
        return
    target = args.write_key.resolve()
    if target.exists():
        raise SystemExit(f"refusing to overwrite {target}")
    target.write_text(str(runtime["api_key"]), encoding="utf-8")
    os.chmod(target, 0o600)
    print(f"model_api_key_file=CREATED path={target}")


if __name__ == "__main__":
    main()

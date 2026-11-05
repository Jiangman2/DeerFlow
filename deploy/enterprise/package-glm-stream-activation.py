"""Build the one-image activation from pinned controller and actual receipts."""

import argparse
import ast
import hashlib
import json
import pathlib

SOURCE_SHA = "a61b258be579f4099829759dbda74757c24b55d76e5211bd33de0a8fa7799b34"
CORE_NAMES = (
    "Refused",
    "require",
    "canonical",
    "pin",
    "binding",
    "private_read",
    "all_ready",
    "node_gate",
    "active_pods",
    "normalized_containers",
    "endpoint_gate",
    "Controller",
    "selected_two",
    "ActivationController",
    "plan",
    "configuration_gate",
)


def assignments(source):
    out = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    try:
                        out[target.id] = ast.literal_eval(node.value)
                    except (ValueError, TypeError):
                        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "dict" and not node.value.args:
                            out[target.id] = {k.arg: ast.literal_eval(k.value) for k in node.value.keywords}
    return out


def build(source, release, receipts, folder):
    assert hashlib.sha256(source).hexdigest() == SOURCE_SHA, "controller_source_changed"
    text = source.decode("utf-8")
    values = assignments(text)
    profile = release["profile"]
    role = profile["roles"][0]
    assert role["manifest_digest"] == "sha256:3a935533a484bd26e9bb8798ab8e446d92e9a852b593a3ce92f58c0496dd83ea"
    runtime = values["SOURCES"]["runtime"].decode()
    insertion = "value['glm'] = glm.accept(env,require)"
    assert runtime.count(insertion) == 1
    runtime = runtime.replace(
        insertion,
        """if payload.get('stream_fixed') is True:
            glm.ADAPTER_SHA = STREAM_FILES['app/backend/packages/harness/deerflow/models/enterprise_glm.py']['sha256']
        value['glm'] = glm.accept(env,require)
        value['stream_completion'] = check_stream_runtime(payload.get('stream_fixed') is True, STREAM_FILES, require)""",
    )
    runtime = "STREAM_FILES = " + repr(profile["tail_files"]["gateway-forward"]["files"]) + "\n" + (folder / "check-glm-stream-runtime.py").read_text(encoding="utf-8") + "\n" + runtime
    sources = dict(helper=values["SOURCES"]["helper"], runtime=runtime.encode())
    constants = {k: values[k] for k in ("UID", "BASE", "ROLES", "BACKEND", "NODES")}
    constants.update(
        SOURCES=sources,
        PINS={k: dict(bytes=len(v), sha256=hashlib.sha256(v).hexdigest()) for k, v in sources.items()},
        OLD="deerflow.local/deer-flow-gateway@" + release["field_acceptance"]["base_manifest"],
        MD=role["manifest_digest"],
        CD=role["config_digest"],
        TAG=role["tag"],
        NEW="deerflow.local/deer-flow-gateway@" + role["manifest_digest"],
        CURRENT_BINDING=release["field_acceptance"]["gateway"],
        FIELD=release["field_acceptance"],
        PRELOADS=receipts,
        CONTROLLER_SOURCE_SHA256=SOURCE_SHA,
    )
    functions = {}
    for node in ast.parse(text).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in CORE_NAMES:
            functions[node.name] = ast.get_source_segment(text, node)
    assert set(functions) == set(CORE_NAMES)
    assert functions["Controller"].count("< 4600") == 1
    functions["Controller"] = functions["Controller"].replace("< 4600", "< 7200")
    result = "import os, sys, stat, socket, hashlib, json, re, copy, time, uuid, signal, shutil, types\n"
    result += "\n".join(k + " = " + repr(v) for k, v in constants.items()) + "\n\n"
    result += "\n\n".join(functions[name] for name in CORE_NAMES) + "\n\n"
    result += (folder / "activate-glm-stream.py").read_text(encoding="utf-8")
    scope = {"__name__": "packaging_validation"}
    exec(compile(result, "<activation>", "exec"), scope)
    scope["preload_gate"]()
    compile(runtime, "<runtime>", "exec")
    return result.encode("utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--controller", required=True, type=pathlib.Path)
    parser.add_argument("--receipts", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    args = parser.parse_args()
    folder = pathlib.Path(__file__).parent
    release = json.loads((folder / "glm-stream-release-20261021-r1.json").read_text(encoding="utf-8"))
    receipts = json.loads(args.receipts.read_text(encoding="utf-8"))
    raw = build(args.controller.read_bytes(), release, receipts, folder)
    args.output.write_bytes(raw)
    print(json.dumps(dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), output=str(args.output))))


if __name__ == "__main__":
    main()

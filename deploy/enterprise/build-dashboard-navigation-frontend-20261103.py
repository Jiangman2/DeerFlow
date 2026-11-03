"""Build the dashboard/history delta on the sealed Frontend 239 snapshot.

Local build only. Never connects to Kubernetes, pushes an image, or starts a rollout.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import frontend_slim as slim

BASE_INVENTORY = "3e2c101edb9ca05ecb0573149af6a8e4b9d839bc896d8e0a1d795263bdf181b1"
BASE_IMAGE = "sha256:97a84226f1c9f77871c05cebaae1fa42b7525644edc844bccf75e29518ba44c1"
NODE = "node@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402"
TAG = "deerflow.local/deer-flow-frontend:20261103-dashboard-navigation-r1"
FILES = (
    "frontend/src/app/workspace/admin/dashboard/page.tsx",
    "frontend/src/components/workspace/dashboard/section-navigation.tsx",
    "frontend/src/components/workspace/model-monitor-panel.tsx",
    "frontend/src/core/console/model-monitor.ts",
    "frontend/src/core/dashboard/hooks.ts",
    "frontend/src/core/i18n/dashboard-navigation.ts",
    "frontend/src/core/i18n/locales/en-US.ts",
    "frontend/src/core/i18n/locales/zh-CN.ts",
    "frontend/src/app/workspace/chats/page.tsx",
)
NEW_FILES = {FILES[1], FILES[5]}


def prepare(base, inventory, out):
    raw_inventory = inventory.read_bytes()
    slim.require(slim.sha(raw_inventory) == BASE_INVENTORY, "accepted_inventory_changed")
    spec = importlib.util.spec_from_file_location("navigation_qualification", ROOT / "deploy/enterprise/dashboard_navigation_source_20261103.py")
    qualification = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qualification)
    manifest = qualification.qualify(lambda name: (ROOT / name).read_bytes())
    slim.require(set(p for p in manifest["changes"] if p.startswith("frontend/src/")) == set(FILES), "candidate_source_scope")
    rows = json.loads(raw_inventory)
    slim.require(len(rows) == 979, "accepted_source_count")
    context = out / "context"
    context.mkdir()
    originals = {}
    for row in rows:
        name = row["path"]
        slim.require(slim.selected(name) and ".." not in Path(name).parts and name not in originals, "invalid_source_path")
        source = base / name
        slim.require(not source.is_symlink() and source.resolve().is_relative_to(base.resolve()), "base_path_escape")
        raw = source.read_bytes()
        slim.require(len(raw) == row["bytes"] and slim.sha(raw) == row["sha256"], "accepted_source_changed:" + name)
        target = context / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        originals[name] = raw
    changes = []
    for name in FILES:
        original = originals.get(name)
        slim.require((original is None) == (name in NEW_FILES), "overlay_base_presence:" + name)
        source = ROOT / name
        slim.require(not source.is_symlink() and source.resolve().is_relative_to(ROOT), "overlay_path_escape")
        raw = source.read_bytes().replace(b"\r\n", b"\n")
        slim.require(raw != original, "empty_patch:" + name)
        target = context / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        changes.append({"path": name, "base_sha256": slim.sha(original) if original else None,
                        "candidate_sha256": slim.sha(raw), "bytes": len(raw)})
    final = []
    for name in sorted(set(originals) | set(FILES)):
        raw = (context / name).read_bytes()
        if name not in FILES:
            slim.require(raw == originals[name], "unrelated_source_changed:" + name)
        final.append({"path": name, "bytes": len(raw), "sha256": slim.sha(raw)})
    slim.write_json(out / "SOURCE.json", final)
    slim.write_json(out / "SOURCE-CHANGE.json", {
        "schema": "portal-dashboard-navigation-frontend-source.v1", "status": "PREPARED_NOT_DEPLOYED",
        "base_image": BASE_IMAGE, "accepted_source_inventory_sha256": BASE_INVENTORY,
        "changes": changes, "runtime_files_changed": len(changes),
        "other_source_files_preserved": len(originals) - len(FILES) + len(NEW_FILES),
        "production_writes": 0, "gateway_modified": False, "database_modified": False,
    })
    return {"files": len(final), "bytes": sum(r["bytes"] for r in final),
            "sha256": slim.sha(json.dumps(final, sort_keys=True).encode()), "path": str(context)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-context", type=Path, required=True)
    parser.add_argument("--base-inventory", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--build", action="store_true")
    opts = parser.parse_args()
    slim.require(not socket.gethostname().split(".")[0].startswith(("datax-", "ecs-zdz-pro-"))
                 and not Path("/etc/kubernetes/admin.conf").exists(), "local_build_host_required")
    out = opts.out.absolute()
    slim.require(not out.exists() and not out.is_relative_to(ROOT), "fresh_external_output_required")
    if opts.build:
        existing = subprocess.run(["docker", "image", "inspect", TAG], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        slim.require(existing.returncode != 0, "candidate_tag_already_exists")
    out.mkdir(parents=True)
    result = {"frontend_slim": "INCOMPLETE", "production_writes": 0, "images_pushed": False,
              "ready_for_rollout": False, "platform": "linux/arm64", "node_image": NODE}
    try:
        result["source"] = prepare(opts.base_context, opts.base_inventory, out)
        print(json.dumps({"source_snapshot": "PASS", "files": result["source"]["files"], "runtime_files_changed": len(FILES)}), flush=True)
        if not opts.build:
            result["frontend_slim"] = "PREPARED_ONLY"
            return
        args = SimpleNamespace(node_image=NODE, tag=TAG, platform="linux/arm64", builder="desktop-linux",
            gateway_url="http://gateway:8001", app_version="", npm_registry="", enterprise_ca=None, enterprise_proxy=None)
        context = Path(result["source"]["path"])
        scope = "deerflow-slim-v1-" + slim.sha((NODE + slim.sha((context / "frontend/pnpm-lock.yaml").read_bytes())).encode())[:20]
        result["builder"] = args.builder
        result["build_seconds"] = slim.run_build(slim.build_command(args, context, out / "METADATA.json", scope), out)
        image = json.loads(slim.capture(["docker", "image", "inspect", TAG]))[0]
        slim.require(image["Architecture"] == "arm64" and image["Size"] <= 1536 * 1024**2, "runtime_scope")
        result["image"] = {"tag": TAG, "id": image["Id"], "bytes": image["Size"], "layers": len(image["RootFS"]["Layers"])}
        result["fresh_runtime"] = slim.verify_fresh_layers(slim.inspect_platform_base(NODE, args.platform), image)
        spec = importlib.util.spec_from_file_location("bounded_qualification", ROOT / "deploy/enterprise/build-nonconfidential-notice-frontend.py")
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)
        result["runtime"] = runtime.qualify(image["Id"], args.platform, out)
        command = slim.build_command(args, context, out / "EXPORT-METADATA.json", scope)
        command.remove("--load")
        command[-1:-1] = ["--output", "type=oci,dest=" + str(out / "frontend.oci.tar")]
        result["export_seconds"] = slim.run_build(command, out, "EXPORT.log")
        result["export"] = slim.audit_oci(out / "frontend.oci.tar", image)
        result["frontend_slim"] = "LOCAL_PASS"
    except Exception as exc:
        result["frontend_slim"] = "FAIL"
        result["failure"] = str(exc) if isinstance(exc, slim.CheckFailure) else type(exc).__name__
        raise
    finally:
        slim.write_json(out / "RESULT.json", result)
        print(json.dumps({"result": str(out / "RESULT.json"), "status": result["frontend_slim"], "production_writes": 0}), flush=True)


if __name__ == "__main__":
    main()

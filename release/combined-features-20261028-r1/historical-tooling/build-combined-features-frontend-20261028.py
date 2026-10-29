"""Build the sealed post-230 frontend on the local ARM64 builder; no push/deploy."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import frontend_slim as slim

NODE = "node@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402"
TAG = "deerflow.local/deer-flow-frontend:20261028-combined-features-r1"


def build(snapshot):
    raw = (snapshot / "SOURCE.json").read_bytes(); seal = json.loads(raw)
    slim.require(seal["schema"] == "portal-combined-features-source.v1" and seal["frontend_base"]["generation"] == 230
                 and seal["work_update_included"] is False, "invalid_source_scope")
    context = snapshot / "frontend/source"
    def verify():
        for row in seal["source_inventories"]["frontend"]:
            path = context / row["path"]
            slim.require(not path.is_symlink() and slim.sha(path.read_bytes()) == row["sha256"], "sealed_input_changed:" + row["path"])
    verify()
    out = snapshot / "frontend/build"
    slim.require(not out.exists(), "fresh_build_required"); out.mkdir()
    args = SimpleNamespace(builder="desktop-linux", platform="linux/arm64", node_image=NODE,
        gateway_url="http://gateway:8001", app_version="", tag=TAG, npm_registry="", enterprise_ca=None, enterprise_proxy=None)
    scope = "deerflow-slim-v1-" + slim.sha((NODE + slim.sha((context / "frontend/pnpm-lock.yaml").read_bytes())).encode())[:20]
    result = dict(status="INCOMPLETE", source_sha256=slim.sha(raw), tag=TAG, production_modified=False, images_pushed=False, ready_for_rollout=False)
    try:
        command = slim.build_command(args, context, out / "METADATA.json", scope)
        result["build_seconds"] = slim.run_build(command, out)
        image = json.loads(slim.capture(["docker", "image", "inspect", TAG], timeout=60))[0]
        slim.require(image["Architecture"] == "arm64", "architecture_mismatch")
        result["image"] = {"id": image["Id"], "bytes": image["Size"], "layers": len(image["RootFS"]["Layers"])}
        result["fresh_runtime"] = slim.verify_fresh_layers(slim.inspect_platform_base(NODE, "linux/arm64"), image)
        spec = importlib.util.spec_from_file_location("polish_qa", ROOT / "deploy/enterprise/build-frontend-polish-20261028.py")
        helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
        result["runtime"] = helper.qualify_emulated(image["Id"], out)
        command = slim.build_command(args, context, out / "EXPORT-METADATA.json", scope)
        command.remove("--load")
        command[-1:-1] = ["--output", "type=oci,dest=" + str(out / "frontend.oci.tar")]
        result["export_seconds"] = slim.run_build(command, out, "EXPORT.log")
        result["export"] = slim.audit_oci(out / "frontend.oci.tar", image)
        verify(); result["status"] = "LOCAL_ARM64_FRONTEND_AND_OCI_ACCEPTED_NOT_DEPLOYED"
    except Exception as exc:
        result["error_class"] = type(exc).__name__
        if isinstance(exc, slim.CheckFailure): result["error_code"] = str(exc)
        raise
    finally:
        slim.write_json(out / "RESULT.json", result)
        print(json.dumps({"status": result["status"], "report": str(out / "RESULT.json")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    build(parser.parse_args().snapshot)

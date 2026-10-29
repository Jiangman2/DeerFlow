"""Seal every post-Frontend-230 input and the reviewed Gateway source delta.

Local only. Does not change historical inventories, deployment or credentials.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import frontend_slim as slim


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def equivalent(a, b):
    if a == b:
        return True
    try:
        a.decode("utf-8"); b.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return a.replace(b"\r\n", b"\n") == b.replace(b"\r\n", b"\n")


def write(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(raw)


def main(output):
    require(not output.exists(), "fresh_output_required")
    candidate_path = ROOT / "release/model-square-account-20261028-r1/CANDIDATE.json"
    candidate_raw = candidate_path.read_bytes()
    candidate = json.loads(candidate_raw)
    approved = {}
    for row in candidate["source_files"]:
        raw = (ROOT / row["path"]).read_bytes()
        require(len(raw) == row["bytes"] and sha(raw) == row["sha256"], "candidate_changed:" + row["path"])
        approved[row["path"]] = raw

    lock_path = ROOT / "deploy/enterprise/frontend-production-source-lock-20261028.json"
    lock_raw = lock_path.read_bytes(); lock = json.loads(lock_raw)
    source_record_raw = (ROOT / lock["source_manifest"]["path"]).read_bytes()
    require(sha(source_record_raw) == lock["source_manifest"]["sha256"], "frontend_history_changed")
    record = json.loads(source_record_raw); old_root = Path(record["source"])
    old = {}
    for row in lock["source_inventory"]:
        raw = (old_root / row["path"]).read_bytes()
        require(sha(raw) == row["sha256"] and len(raw) == row["bytes"], "frontend_archive_changed")
        old[row["path"]] = raw
    current = slim.source_inventory(ROOT)
    require(set(old) <= {r["path"] for r in current}, "historical_frontend_input_removed")
    frontend, differences = {}, []
    for row in current:
        name = row["path"]; raw = (ROOT / name).read_bytes()
        require(sha(raw) == row["sha256"], "frontend_changed_during_snapshot")
        if name in old and equivalent(raw, old[name]):
            raw = old[name]
        else:
            require(name in approved or name.startswith("frontend/tests/"), "unreviewed_frontend_change:" + name)
            differences.append(dict(path=name, bytes=len(raw), sha256=sha(raw),
                                    prior_sha256=sha(old[name]) if name in old else None))
        frontend[name] = raw

    backend_lock_path = ROOT / "deploy/enterprise/production-source-lock-20261026.json"
    backend_lock_raw = backend_lock_path.read_bytes(); backend_lock = json.loads(backend_lock_raw)
    spec = importlib.util.spec_from_file_location("release_verify", ROOT / "deploy/enterprise/verify-release-source-20261023.py")
    verifier = importlib.util.module_from_spec(spec); spec.loader.exec_module(verifier)
    backend, overlay, normalizations = {}, {}, []
    for name, digest in backend_lock["source_sets"]["gateway"].items():
        raw = (ROOT / name).read_bytes().replace(b"\r\n", b"\n")
        if name in approved:
            require(sha(raw) == sha(approved[name].replace(b"\r\n", b"\n")), "backend_changed")
            if sha(raw) != digest:
                overlay[name] = raw
        elif sha(raw) != digest:
            normalization = backend_lock.get("format_normalizations", {}).get(name)
            require(normalization and sha(raw) == normalization["formatted_sha256"], "unreviewed_backend_change:" + name)
            original = (ROOT / verifier.FORMAT_BASELINE / name).read_bytes().replace(b"\r\n", b"\n")
            require(sha(original) == digest, "backend_archive_changed:" + name)
            ordered = normalization["kind"] == "layout_and_import_order"
            require(verifier.syntax_fingerprint(original, import_order=ordered) == verifier.syntax_fingerprint(raw, import_order=ordered), "normalization_logic_change")
            raw = original; normalizations.append(name)
        backend[name] = raw
    for name, raw in approved.items():
        if name.startswith(("backend/app/", "backend/packages/harness/")) and name not in backend:
            backend[name] = raw.replace(b"\r\n", b"\n"); overlay[name] = backend[name]
    actual = {p.relative_to(ROOT).as_posix() for base in (ROOT / "backend/app", ROOT / "backend/packages/harness")
              for p in base.rglob("*.py") if "__pycache__" not in p.parts}
    require(actual == set(backend), "unreviewed_runtime_file_set:" + repr(sorted(actual ^ set(backend))))
    require(not any("/work/" in p or "0022_" in p or "work_oauth" in p for p in backend), "paused_work_sources_found")
    require(all(p.startswith(("backend/app/gateway/", "backend/packages/harness/deerflow/")) for p in overlay), "unexpected_overlay")

    inventories = {}
    for component, files in (("frontend", frontend), ("gateway", backend)):
        rows = []
        for name, raw in sorted(files.items()):
            write(output / component / "source" / name, raw)
            rows.append(dict(path=name, bytes=len(raw), sha256=sha(raw)))
        inventories[component] = rows
    for name, raw in sorted(overlay.items()):
        write(output / "gateway/overlay" / name, raw)
    result = dict(schema="portal-combined-features-source.v1", status="SOURCE_SEALED_NOT_DEPLOYED",
        git_head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        frontend_base=lock["deployment"], frontend_image=lock["image"],
        gateway_base_image=backend_lock["images"]["gateway"], gateway_fresh_deployment_required=True,
        frontend_lock_sha256=sha(lock_raw), gateway_lock_sha256=sha(backend_lock_raw),
        candidate_sha256=sha(candidate_raw), source_root=str(output), source_inventories=inventories,
        frontend_changes=differences,
        gateway_overlay=[dict(path=p, bytes=len(b), sha256=sha(b), prior_sha256=backend_lock["source_sets"]["gateway"].get(p)) for p,b in sorted(overlay.items())],
        unchanged_gateway_formatting_restored=normalizations, work_update_included=False,
        migration_required=False, production_modified=False, ready_for_rollout=False,
        deployment_scope=["gateway", "frontend", "append_optional_model_configuration"],
        runtime_config_preserved=True)
    require(current == slim.source_inventory(ROOT), "checkout_changed_during_snapshot")
    write(output / "SOURCE.json", (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps(dict(status=result["status"], frontend_files=len(frontend), gateway_files=len(backend),
        frontend_changes=len(differences), gateway_overlay_files=len(overlay), source=str(output / "SOURCE.json"),
        sha256=sha((output / "SOURCE.json").read_bytes()))), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output)

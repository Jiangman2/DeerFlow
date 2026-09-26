"""Verify and stage the user-supplied read-only production source evidence.

Never imports or executes collected source. Staging uses a NEW local directory;
this module does not connect to production, build images or modify workloads.
"""

import argparse
import base64
import copy
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate audit key")
        result[key] = value
    return result


def _path(value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value or "\0" in value or any(part in ("", ".", "..") for part in value.split("/")) or PurePosixPath(value).is_absolute():
        raise ValueError("Unsafe source evidence path")
    return value


def load(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(16 * 1024**2 + 1)
    if len(raw) > 16 * 1024**2:
        raise ValueError("Source audit exceeds limit")
    document = json.loads(raw, object_pairs_hook=_unique_object)
    if document.get("audit") not in ("employee-sharing-source-baseline-v1", "employee-sharing-source-supplement-v1") or document.get("errors") != [] or document.get("production_mutation") != "NONE" or document.get("secrets_collected") is not False:
        raise ValueError("Incomplete or unexpected source audit")
    components = document.get("components", {})
    if set(components) != {"gateway", "frontend", "provisioner"}:
        raise ValueError("Production component evidence missing")
    lock = {"schema": 1, "audit_kind": document["audit"], "report_sha256": hashlib.sha256(raw).hexdigest(), "audit_utc": document.get("utc"), "files_verified": 0, "components": {}}
    sources = {}
    for name, component in components.items():
        if component.get("deployment") != name or not isinstance(component.get("image"), str) or not re.fullmatch(r"(?:containerd://)?sha256:[a-f0-9]{64}", component.get("image_id", "")):
            raise ValueError("Invalid production image identity")
        files = component.get("files")
        if not isinstance(files, dict) or not 1 <= len(files) <= 512:
            raise ValueError("Invalid source evidence scope")
        checked, missing = {}, []
        sources[name] = {}
        for relative, item in files.items():
            _path(relative)
            if item == {"missing": True}:
                missing.append(relative)
                continue
            if not isinstance(item, dict) or not re.fullmatch(r"[a-f0-9]{64}", item.get("sha256", "")):
                raise ValueError("Invalid source digest")
            content = base64.b64decode(item["base64"], validate=True)
            if len(content) > 2 * 1024**2 or hashlib.sha256(content).hexdigest() != item["sha256"]:
                raise ValueError("Source checksum mismatch: " + relative)
            sources[name][relative] = content
            checked[relative] = item["sha256"]
        if not checked:
            raise ValueError("Empty production source component")
        runtime = dict(component.get("runtime") or {})
        if component.get("python"):
            runtime["python"] = component["python"]
        node, warnings = component.get("node"), []
        if name == "frontend" and isinstance(node, str) and re.fullmatch(r"v\d+\.\d+\.\d+", node):
            runtime["node"] = node
            node = None
            warnings.append("legacy_collector_node_collision")
        lock["components"][name] = {"image": component["image"], "image_id": component["image_id"], "node": node, "runtime": runtime, "metadata_warnings": warnings, "files": checked, "missing": sorted(missing)}
        lock["files_verified"] += len(checked)
    return {"lock": lock, "sources": sources}


def merge(reports):
    """Combine verified same-image evidence, never overwrite conflicting bytes."""
    if not reports or reports[0]["lock"].get("audit_kind") != "employee-sharing-source-baseline-v1":
        raise ValueError("Full source baseline required before supplement")
    result = copy.deepcopy(reports[0])
    result["lock"]["report_sha256s"] = [item["lock"]["report_sha256"] for item in reports]
    result["lock"]["audit_utcs"] = [item["lock"]["audit_utc"] for item in reports]
    for report in reports[1:]:
        for name, incoming in report["lock"]["components"].items():
            previous = result["lock"]["components"][name]
            if previous["image"] != incoming["image"] or previous["image_id"].removeprefix("containerd://") != incoming["image_id"].removeprefix("containerd://"):
                raise ValueError("Production image identity changed: " + name)
            known = dict.fromkeys(previous["missing"])
            known.update(previous["files"])
            added = dict.fromkeys(incoming["missing"])
            added.update(incoming["files"])
            if not set(previous["files"]) & set(incoming["files"]):
                raise ValueError("Supplement differs: no shared source anchor for " + name)
            for path in set(known) & set(added):
                if known[path] != added[path]:
                    raise ValueError("Production source changed: " + name + "/" + path)
            for key in set(previous["runtime"]) & set(incoming["runtime"]):
                if previous["runtime"][key] != incoming["runtime"][key]:
                    raise ValueError("Production runtime changed: " + name)
            previous["runtime"].update(incoming["runtime"])
            previous["files"].update(incoming["files"])
            previous["missing"] = sorted(set(previous["missing"]) | set(incoming["missing"]))
            result["sources"][name].update(report["sources"][name])
    result["lock"]["files_verified"] = sum(len(c["files"]) for c in result["lock"]["components"].values())
    return result


def materialize(loaded, destination):
    """Write only already-validated bytes to a fresh, private local directory."""
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Source staging destination already exists")
    for parent in destination.parents:
        if parent.is_symlink() or getattr(parent, "is_junction", lambda: False)():
            raise ValueError("Source staging parent is a link")
    # Validate every output member before creating the directory.
    entries = []
    for component, files in loaded["sources"].items():
        if component not in ("gateway", "frontend", "provisioner"):
            raise ValueError("Unknown source component")
        for relative, content in files.items():
            _path(relative)
            if hashlib.sha256(content).hexdigest() != loaded["lock"]["components"][component]["files"][relative]:
                raise ValueError("Source staging checksum changed")
            entries.append((destination / component / relative, content))
    destination.mkdir(mode=0o700, parents=False)
    for target, content in entries:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
    with (destination / "SOURCE-BASELINE.json").open("x", encoding="utf-8") as stream:
        json.dump(loaded["lock"], stream, ensure_ascii=False, sort_keys=True, indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", required=True)
    parser.add_argument("--supplement", action="append", default=[], help="Same-image supplemental report (repeatable)")
    parser.add_argument("--stage", help="New local source staging directory; never an existing checkout")
    args = parser.parse_args()
    loaded = load(args.report)
    if args.supplement:
        loaded = merge([loaded] + [load(path) for path in args.supplement])
    if args.stage:
        materialize(loaded, args.stage)
    print(json.dumps(loaded["lock"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

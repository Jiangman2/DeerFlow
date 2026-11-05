"""Package the paged read-only membership inventory; no grant or rollout."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent


def replace_once(source, before, after):
    if source.count(before) != 1:
        raise RuntimeError("HOST_TEMPLATE_CHANGED")
    return source.replace(before, after)


def build():
    spec = importlib.util.spec_from_file_location("members_packaging_helpers", DIRECTORY / "package-weknora-company-members.py")
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    child = (DIRECTORY / "inspect-weknora-company-members-r3-child.py").read_text(encoding="utf-8")
    child = helpers.fill(child, "CLASSIFIER", (DIRECTORY / "inspect-weknora-company-members-child.py").read_text(encoding="utf-8"))
    host = helpers.build().decode("utf-8")
    old_child = helpers.literal(host, "CHILD")
    host = replace_once(host, "CHILD = " + repr(old_child), "CHILD = " + repr(child))
    host = replace_once(host, "portal-weknora-company-members-host.v2", "portal-weknora-company-members-host.v3")
    host = replace_once(host, "portal-weknora-company-members-diagnosis.v2", "portal-weknora-company-members-inventory.v3")
    host = replace_once(host, "timeout=90)", "timeout=210)")
    host = replace_once(host, '    process = subprocess.run(BASE + ["exec",', '    print(json.dumps({"phase": "PAGED_MEMBERS_READONLY", "rows_per_fetch": 1000, "maximum_child_seconds": 180}), flush=True)\n    process = subprocess.run(BASE + ["exec",')
    compile(child, "<paged-child>", "exec")
    compile(host, "<paged-host>", "exec")
    return host.encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    raw = build()
    with args.output.open("xb") as stream:
        stream.write(raw)
    print(json.dumps({"path": str(args.output), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}))


if __name__ == "__main__":
    main()

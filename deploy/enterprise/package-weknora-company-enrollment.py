"""Package the native-API bulk viewer enrollment with pinned prior receipts."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise RuntimeError("REVIEWED_TEMPLATE_CHANGED")
    return source.replace(old, new)


def build():
    spec = importlib.util.spec_from_file_location("enrollment_helpers", DIRECTORY / "package-weknora-company-members.py")
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    classifier = (DIRECTORY / "inspect-weknora-company-members-child.py").read_text(encoding="utf-8")
    # Expose the exact already-reviewed plan only to this child process, never
    # to the report. Historical read-only tool sources remain unchanged.
    classifier = replace_once(classifier, "def classify(platform, native, owners, members):", "def classify(platform, native, owners, members, internal_plan=False):")
    classifier = replace_once(classifier, '    return {\n        "status": "READONLY_MEMBERSHIP_INVENTORY_NOT_ENROLLED",', '    if internal_plan:\n        return plan\n    return {\n        "status": "READONLY_MEMBERSHIP_INVENTORY_NOT_ENROLLED",')
    collector = (DIRECTORY / "inspect-weknora-company-members-r3-child.py").read_text(encoding="utf-8")
    collector = helpers.fill(collector, "CLASSIFIER", classifier)
    collector = replace_once(collector, '    report["status"] = "READONLY_MEMBERSHIP_INVENTORY_NOT_ENROLLED"', '    report["status"] = "READONLY_MEMBERSHIP_INVENTORY_NOT_ENROLLED"\n    return platform, native, owners, members')
    child = (DIRECTORY / "enroll-weknora-company-members-child.py").read_text(encoding="utf-8")
    child = helpers.fill(child, "COLLECTOR", collector)
    child = helpers.fill(child, "BOOTSTRAP", (DIRECTORY / "bootstrap-weknora-company-knowledge-child.py").read_text(encoding="utf-8"))
    guard = helpers.build().decode("utf-8")
    # Reuse only receipt/pin/read helpers; don't ship an unused nested r2 child.
    old_child = helpers.literal(guard, "CHILD")
    guard = replace_once(guard, "CHILD = " + repr(old_child), "CHILD = None")
    host = (DIRECTORY / "enroll-weknora-company-members-host.py").read_text(encoding="utf-8")
    host = helpers.fill(helpers.fill(host, "CHILD", child), "GUARD", guard)
    for source in (classifier, collector, child, guard, host):
        compile(source, "<enrollment-package>", "exec")
    return host.encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    raw = build()
    with args.output.open("xb") as output:
        output.write(raw)
    print(json.dumps({"path": str(args.output), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}))


if __name__ == "__main__":
    main()

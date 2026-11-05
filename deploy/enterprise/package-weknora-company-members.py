"""Package the independent read-only membership diagnosis."""

import argparse
import ast
import hashlib
import json
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parent


def literal(source, name):
    nodes = [n for n in ast.parse(source).body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)]
    if len(nodes) != 1:
        raise RuntimeError("TEMPLATE_CHANGED")
    return ast.literal_eval(nodes[0].value)


def fill(source, key, value):
    before = key + " = None"
    if source.count(before) != 1:
        raise RuntimeError("TEMPLATE_CHANGED")
    return source.replace(before, key + " = " + repr(value))


def build():
    child = (DIRECTORY / "inspect-weknora-company-members-r2-child.py").read_text(encoding="utf-8")
    child = fill(child, "BASELINE", (DIRECTORY / "inspect-weknora-company-members-child.py").read_text(encoding="utf-8"))
    host = (DIRECTORY / "inspect-weknora-company-members-host.py").read_text(encoding="utf-8")
    pins = literal((DIRECTORY / "bootstrap-weknora-company-knowledge-host.py").read_text(encoding="utf-8"), "PINS")
    pins = {k: list(v) for k, v in pins.items()}
    receipts = []
    for filename in ("weknora-company-bootstrap-field-20261024-r1.json", "weknora-company-viewer-field-20261024-r1.json"):
        value = json.loads((DIRECTORY / filename).read_text(encoding="utf-8"))
        receipts.append({"path": value["report_path"], "bytes": value["report_bytes"], "sha256": value["report_sha256"]})
    host = fill(fill(fill(host, "CHILD", child), "PINS", pins), "RECEIPTS", receipts)
    compile(child, "<inventory-child>", "exec")
    compile(host, "<inventory-host>", "exec")
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

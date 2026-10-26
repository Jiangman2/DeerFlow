"""Package the reviewed bootstrap; no network, credentials or server writes."""

import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    host = (root / "bootstrap-weknora-company-knowledge-host.py").read_text(encoding="utf-8")
    child = (root / "bootstrap-weknora-company-knowledge-child.py").read_text(encoding="utf-8")
    if host.count("CHILD = None") != 1:
        raise RuntimeError("PACKAGING_PLACEHOLDER_CHANGED")
    compile(child, "<embedded-child>", "exec")
    content = host.replace("CHILD = None", "CHILD = " + repr(child))
    compile(content, "<packaged-host>", "exec")
    data = content.encode("utf-8")
    with args.output.open("xb") as target:
        target.write(data)
    print(json.dumps({"path": str(args.output), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}))


if __name__ == "__main__":
    main()

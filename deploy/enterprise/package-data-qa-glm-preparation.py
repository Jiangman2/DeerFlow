"""Package reviewed sources and actual five-node receipts, without secrets."""

import argparse
import hashlib
import json
from pathlib import Path


def build(folder):
    release = json.loads(
        (folder / "data-qa-glm-release-20261021-r1.json").read_text(encoding="utf-8")
    )
    preloads = json.loads(
        (folder / "data-qa-glm-preloads-20261021-r1.json").read_text(encoding="utf-8")
    )
    migration = (folder / "data-qa-config-migration.py").read_text(encoding="utf-8")
    candidate = (
        (folder / "check-glm-stream-runtime.py").read_text(encoding="utf-8")
        + "\n"
        + (folder / "check-data-qa-glm-candidate.py").read_text(encoding="utf-8")
    )
    code = (
        "RELEASE = "
        + repr(release)
        + "\nPRELOADS = "
        + repr(preloads)
        + "\nMIGRATION = "
        + repr(migration)
        + "\nCANDIDATE = "
        + repr(candidate)
        + "\n"
    )
    code += (folder / "prepare-data-qa-glm.py").read_text(encoding="utf-8")
    namespace = {"__name__": "package_validation"}
    exec(compile(code, "<preparation>", "exec"), namespace)
    namespace["preload_gate"]()
    compile(candidate, "<candidate>", "exec")
    return code.encode("utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = build(Path(__file__).parent)
    args.output.write_bytes(raw)
    print(
        json.dumps(
            dict(
                bytes=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                path=str(args.output),
            )
        )
    )


if __name__ == "__main__":
    main()

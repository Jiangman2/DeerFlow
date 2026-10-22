"""Verify the sampled production Python sources without importing application code."""
import ast
import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[2]
    evidence = json.loads((root / "deploy/enterprise/production-runtime-fingerprints-20261022.json").read_text())
    results = []
    for component in evidence["observation"]["components"]:
        name = component["deployment"]
        if name == "weknora-frontend":
            continue
        prefix = root if name == "gateway" else root / "release/production-source-20261022/ppt-master-runner"
        expected = {item["path"]: item["sha256"] for item in component["observation"]["files"]}
        actual = {path.relative_to(prefix).as_posix() for directory in ("backend/app", "backend/packages/harness")
                  for path in (prefix / directory).rglob("*.py") if "__pycache__" not in path.parts}
        assert actual == set(expected), (name, "SOURCE_INVENTORY_MISMATCH")
        for relative, digest in expected.items():
            data = (prefix / relative).read_bytes().replace(b"\r\n", b"\n")
            assert hashlib.sha256(data).hexdigest() == digest, (name, relative, "SOURCE_HASH_MISMATCH")
            ast.parse(data, filename=relative)
        results.append({"component": name, "files": len(expected), "hashes": "PASS", "syntax": "PASS"})
    print(json.dumps({"status": "PYTHON_SNAPSHOTS_VERIFIED", "results": results,
                      "scope": "ONE_SAMPLED_POD_PER_COMPONENT_PYTHON_ONLY", "production_modified": False}))


if __name__ == "__main__":
    main()

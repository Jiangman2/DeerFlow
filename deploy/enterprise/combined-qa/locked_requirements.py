"""Align installed QA dependencies to the candidate lock, never production.

Optional packages absent from the starting QA image are recorded separately;
this is not a claim that all production dependency extras were installed.
"""

import importlib.metadata
import json
from pathlib import Path
import re
import sys
import tomllib


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def compare(lock):
    installed = {
        normalize(dist.metadata["Name"]): dist.version
        for dist in importlib.metadata.distributions()
        if dist.metadata.get("Name")
    }
    rows = [p for p in lock["package"] if "registry" in p.get("source", {})]
    selected = [p for p in rows if normalize(p["name"]) in installed]
    mismatches = [
        {"name": p["name"], "expected": p["version"], "actual": installed[normalize(p["name"])]}
        for p in selected
        if installed[normalize(p["name"])] != p["version"]
    ]
    missing = [p["name"] for p in rows if normalize(p["name"]) not in installed]
    return selected, mismatches, missing


def main():
    lock = tomllib.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    selected, mismatches, missing = compare(lock)
    if sys.argv[2] == "--check":
        print(json.dumps({"installed_lock_match": not mismatches, "mismatches": mismatches,
                          "absent_locked_packages": missing, "production_mutated": False}))
        return int(bool(mismatches))
    lines = []
    for package in selected:
        artifacts = package.get("wheels", []) + ([package["sdist"]] if package.get("sdist") else [])
        hashes = sorted({a["hash"] for a in artifacts})
        if not hashes or any(not re.fullmatch(r"sha256:[0-9a-f]{64}", h) for h in hashes):
            raise ValueError("missing_or_invalid_locked_hash")
        lines.append(package["name"] + "==" + package["version"] + " "
                     + " ".join("--hash=" + h for h in hashes))
    Path(sys.argv[2]).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"locked_dependencies": len(selected), "mismatches_before": mismatches,
                      "absent_locked_packages": missing, "production_mutated": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

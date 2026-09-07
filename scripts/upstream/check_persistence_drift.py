"""Detect persistence/dependency drift; acknowledgment never grants certification."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIRECTORIES = (
    "backend/packages/harness/deerflow/persistence",
    "backend/packages/harness/deerflow/runtime/checkpointer",
    "backend/packages/harness/deerflow/runtime/store",
    "k8s/local-pvc-kingbase",
)
FILES = (
    "backend/uv.lock",
    "backend/pyproject.toml",
    "backend/packages/harness/pyproject.toml",
    "backend/packages/harness/deerflow/config/database_config.py",
    "backend/packages/harness/deerflow/config/checkpointer_config.py",
    "scripts/kingbase/probe.py",
    "scripts/kingbase/acceptance_graph.py",
)


def snapshot(root: Path) -> dict[str, str]:
    paths = {root / name for name in FILES}
    for name in DIRECTORIES:
        paths.update(path for path in (root / name).rglob("*") if path.suffix in (".py", ".yaml", ".sh"))
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest() for path in sorted(paths) if path.is_file()}


def changes(before: dict, after: dict) -> list[str]:
    return sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=ROOT / "docs/upstream/persistence-watch.json")
    parser.add_argument("--record-candidate", action="store_true", help="Record pending-review hashes; does not certify a database")
    args = parser.parse_args()
    current = snapshot(ROOT)
    if args.record_candidate:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps({"status": "candidate_not_certified", "files": current}, indent=2) + "\n", encoding="utf-8")
        print("Recorded candidate fingerprint; remote acceptance still required.")
        return 0
    if not args.baseline.is_file():
        print("Missing persistence fingerprint. Record and review a candidate.")
        return 1
    baseline = json.loads(args.baseline.read_text(encoding="utf-8-sig"))
    drift = changes(baseline["files"], current)
    if drift:
        print("Database compatibility review required:\n" + "\n".join(drift))
        return 1
    print("No drift from recorded candidate. This is not database certification.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

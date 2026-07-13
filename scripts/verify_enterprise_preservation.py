"""Verify that an upstream-sync PR preserves enterprise customizations.

The script compares the current working tree/index against an enterprise base
branch, usually ``main``. It intentionally allows newly added files, but blocks
file deletions, protected path changes, and unregistered modifications to
existing files.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROTECTED_PATHS = ROOT / "docs/upstream-sync/protected-paths.txt"
DEFAULT_ALLOWLIST = ROOT / "docs/upstream-sync/allowed-existing-modifications.txt"


def run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {message}")
    return result.stdout


def read_path_list(path: Path) -> list[str]:
    if not path.exists():
        return []
    entries: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        entries.append(value)
    return entries


def parse_name_status(output: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status = parts[0]
        path = parts[-1]
        rows.append((status, path))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="main", help="Enterprise base ref")
    parser.add_argument(
        "--protected-paths",
        default=str(DEFAULT_PROTECTED_PATHS),
        help="File containing protected paths, one per line",
    )
    parser.add_argument(
        "--allowlist",
        default=str(DEFAULT_ALLOWLIST),
        help="File containing allowed existing-file modifications",
    )
    args = parser.parse_args()

    protected_paths = read_path_list(Path(args.protected_paths))
    allowed_existing = set(read_path_list(Path(args.allowlist)))

    name_status = parse_name_status(run_git("diff", "--name-status", args.base))
    deleted = [path for status, path in name_status if status.startswith("D")]
    modified_existing = [
        path
        for status, path in name_status
        if not status.startswith("A") and not status.startswith("D")
    ]
    unexpected_existing = [
        path for path in modified_existing if path not in allowed_existing
    ]

    protected_changed: list[str] = []
    if protected_paths:
        protected_changed = [
            line.strip()
            for line in run_git(
                "diff",
                "--name-only",
                args.base,
                "--",
                *protected_paths,
            ).splitlines()
            if line.strip()
        ]

    print("Enterprise preservation audit")
    print(f"  base: {args.base}")
    print(f"  changed files: {len(name_status)}")
    print(f"  added files: {sum(1 for status, _ in name_status if status.startswith('A'))}")
    print(f"  deleted files: {len(deleted)}")
    print(f"  modified existing files: {len(modified_existing)}")
    print(f"  protected path changes: {len(protected_changed)}")

    failures: list[str] = []
    if deleted:
        failures.append("Deleted files are not allowed:\n" + "\n".join(f"  - {p}" for p in deleted))
    if protected_changed:
        failures.append(
            "Protected paths changed:\n" + "\n".join(f"  - {p}" for p in protected_changed)
        )
    if unexpected_existing:
        failures.append(
            "Existing-file modifications must be allowlisted:\n"
            + "\n".join(f"  - {p}" for p in unexpected_existing)
        )

    if failures:
        print("\nFAILED")
        print("\n\n".join(failures))
        return 1

    print("\nPASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())


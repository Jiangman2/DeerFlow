"""Inventory committed enterprise changes without fetching or modifying Git."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, encoding="utf-8").stdout.strip()


def inventory(repo: Path, upstream: str, enterprise: str) -> dict:
    upstream_sha = git(repo, "rev-parse", "--verify", upstream + "^{commit}")
    enterprise_sha = git(repo, "rev-parse", "--verify", enterprise + "^{commit}")
    base = git(repo, "merge-base", upstream_sha, enterprise_sha)
    upstream_paths = set(git(repo, "ls-tree", "-r", "--name-only", upstream_sha).splitlines())
    base_paths = set(git(repo, "ls-tree", "-r", "--name-only", base).splitlines())
    shared_rows = []
    enterprise_added_count = 0
    for line in git(repo, "diff", "--no-renames", "--name-status", base, enterprise_sha).splitlines():
        status, path = line.split("\t", 1)
        shared = path in upstream_paths or path in base_paths
        if shared:
            shared_rows.append({"path": path, "change": status, "review_status": "pending"})
        else:
            enterprise_added_count += 1
    return {
        "upstream_commit": upstream_sha,
        "enterprise_commit": enterprise_sha,
        "merge_base": base,
        "includes_uncommitted_changes": False,
        "certifies_compatibility": False,
        "shared_files_requiring_review": shared_rows,
        "enterprise_added_file_count": enterprise_added_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", default="upstream/main")
    parser.add_argument("--enterprise", default="HEAD")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    result = inventory(repo, args.upstream, args.enterprise)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "shared_files_requiring_review": len(result["shared_files_requiring_review"]),
                "enterprise_added_files": result["enterprise_added_file_count"],
            }
        )
    )


if __name__ == "__main__":
    main()

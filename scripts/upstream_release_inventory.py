"""Inventory the pinned v2.1.0 history without editing application or Git state.

History reachability and documentation mentions are evidence, not proof that a
feature survived enterprise adaptations or is deployed. This tool deliberately
does not label either as merged/verified. Fetch the official release separately;
the audit itself disables Git lazy fetch and never connects to production.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


TARGET = "345f08be00c8a9495079b732a39b46aa9af1584e"
BASE = "7e7f0410797693cf882594555ba414e0361d4c6f"
TOPICS = {
    "security_auth": r"auth|security|permission|rbac|ssrf|csrf|credential|secret|tenant|oidc",
    "sandbox": r"sandbox|provisioner|e2b|boxlite|tenki|ownership|lease",
    "skills_agents": r"skill|custom.agent|capability.catalog|soul|agent.storage",
    "memory": r"memory|mem0|honcho|openviking|deermem",
    "execution_delegation": r"subagent|delegat|receipt|acceptance|batch.task|task.evidence",
    "persistence_runtime": r"persist|checkpoint|database|sqlite|postgres|run.owner|cancel|stream|sse",
    "scheduler": r"schedul|cron|interval",
    "workspace": r"frontend|workspace|project|branch|conversation|chat|sidebar",
    "extensions_channels": r"extension|channel|feishu|slack|dingtalk|telegram|discord|mcp|acp",
    "deployment_observability": r"docker|helm|kubernetes|nginx|trace|health|deploy|readiness",
}


def git(repo: Path, *args: str) -> str:
    env = dict(
        os.environ,
        GIT_OPTIONAL_LOCKS="0",
        GIT_NO_LAZY_FETCH="1",
        GIT_TERMINAL_PROMPT="0",
    )
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        encoding="utf-8",
        errors="strict",
        env=env,
        timeout=180,
        check=True,
    )
    return result.stdout


def resolve(repo: Path, ref: str) -> str:
    return git(
        repo, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}"
    ).strip()


def pr_numbers(subject: str) -> list[int]:
    matches = re.findall(r"\(#(\d+)\)|Merge pull request #(\d+)\b", subject)
    return sorted({int(a or b) for a, b in matches})


def parse_log(raw: str) -> list[dict]:
    if not raw:
        return []
    if not raw.endswith("\0"):
        raise ValueError("Git log lacks its record terminator")
    fields = raw[:-1].split("\0")
    if len(fields) % 4:
        raise ValueError("Invalid four-field Git log")
    rows = []
    for index in range(0, len(fields), 4):
        sha, parents, date, subject = fields[index : index + 4]
        if not re.fullmatch(r"[a-f0-9]{40}", sha):
            raise ValueError("Invalid commit identity")
        rows.append(
            {
                "commit": sha,
                "parents": parents.split(),
                "date": date,
                "subject": subject,
                "pr_numbers": pr_numbers(subject),
            }
        )
    return rows


def document_mentions(
    repo: Path, documents: list[Path], rows: list[dict]
) -> dict[str, list[dict]]:
    prefixes: dict[str, set[str]] = {}
    for row in rows:
        sha = row["commit"]
        for size in range(6, 41):
            prefixes.setdefault(sha[:size], set()).add(sha)
    result: dict[str, list[dict]] = {}
    for document in documents:
        if document.is_symlink():
            raise ValueError("Refusing a symlinked evidence document")
        for number, line in enumerate(
            document.read_text(encoding="utf-8-sig").splitlines(), 1
        ):
            for ref in set(
                re.findall(r"(?<![A-Za-z0-9])[a-f0-9]{6,40}(?![A-Za-z0-9])", line)
            ):
                candidates = prefixes.get(ref, set())
                if len(candidates) == 1:
                    sha = next(iter(candidates))
                    result.setdefault(sha, []).append(
                        {"path": document.relative_to(repo).as_posix(), "line": number}
                    )
    return result


def annotate(rows: list[dict], ancestors: set[str], mentions: dict) -> list[dict]:
    result = []
    for row in rows:
        in_history = row["commit"] in ancestors
        result.append(
            {
                **row,
                "url": "https://github.com/bytedance/deer-flow/commit/" + row["commit"],
                "reachable_from_enterprise_head": in_history,
                "document_mentions": mentions.get(row["commit"], []),
                "review_state": "NEEDS_REGRESSION_REVIEW"
                if in_history
                else "NEEDS_SEMANTIC_REVIEW",
                "triage_topics": [
                    name
                    for name, pattern in TOPICS.items()
                    if re.search(pattern, row["subject"], re.I)
                ]
                or ["other"],
            }
        )
    return result


def collect(repo: Path) -> dict:
    target = resolve(repo, "refs/tags/v2.1.0")
    base = resolve(repo, "refs/tags/v2.0.0")
    if target != TARGET or base != BASE:
        raise ValueError("Release tag differs from the reviewed upstream lock")
    head = resolve(repo, "HEAD")
    ancestors = set(git(repo, "rev-list", head).splitlines())
    rows = parse_log(
        git(
            repo,
            "log",
            "--reverse",
            "--topo-order",
            "-z",
            "--format=%H%x00%P%x00%cs%x00%s",
            base + ".." + target,
        )
    )
    docs = sorted((repo / "docs/upstream-sync").glob("2026-09-19-batch-*.md"))
    for name in (
        "docs/enterprise-multiworker-upstream-catchup.md",
        "docs/digital-employee-upstream-sync-2026-09-13.md",
        "docs/upstream-sync-assessment-2026-09-14.md",
    ):
        path = repo / name
        if path.is_file():
            docs.append(path)
    rows = annotate(rows, ancestors, document_mentions(repo, docs, rows))
    changed = [
        name
        for name in git(
            repo, "diff", "--no-renames", "--name-only", "-z", head, "--"
        ).split("\0")
        if name
    ]
    untracked = [
        name
        for name in git(repo, "ls-files", "--others", "--exclude-standard", "-z").split(
            "\0"
        )
        if name
    ]
    target_ancestors = set(git(repo, "rev-list", target).splitlines())
    if resolve(repo, "HEAD") != head:
        raise RuntimeError(
            "Enterprise HEAD changed during inventory; retry after stabilization"
        )
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "official_release": {
            "tag": "v2.1.0",
            "commit": target,
            "url": "https://github.com/bytedance/deer-flow/releases/tag/v2.1.0",
            "release_notes_pr_count": 772,
        },
        "comparison_base": {
            "tag": "v2.0.0",
            "commit": base,
            "merge_base_with_target": git(repo, "merge-base", base, target).strip(),
        },
        "enterprise": {
            "branch": git(repo, "branch", "--show-current").strip(),
            "head": head,
            "merge_base_with_target": git(repo, "merge-base", head, target).strip(),
            "v2_0_tag_is_ancestor": base in ancestors,
            "target_is_ancestor": target in ancestors,
        },
        "counts": {
            "commits_in_v2_0_tag_to_v2_1_tag_reachability_range": len(rows),
            "unique_pr_numbers_in_commit_subjects": len(
                {pr for row in rows for pr in row["pr_numbers"]}
            ),
            "range_commits_reachable_from_enterprise_head": sum(
                row["reachable_from_enterprise_head"] for row in rows
            ),
            "range_commits_not_reachable_from_enterprise_head": sum(
                not row["reachable_from_enterprise_head"] for row in rows
            ),
            "range_commits_with_document_mentions": sum(
                bool(row["document_mentions"]) for row in rows
            ),
            "enterprise_only_commits_vs_target": len(ancestors - target_ancestors),
            "upstream_only_commits_vs_head": len(target_ancestors - ancestors),
            "historical_batch_documents": len(
                list((repo / "docs/upstream-sync").glob("2026-09-19-batch-*.md"))
            ),
        },
        "workspace": {
            "tracked_changed_paths": len(changed),
            "untracked_paths": len(untracked),
            "tracked_changes_by_top_directory": dict(
                sorted(Counter(name.split("/", 1)[0] for name in changed).items())
            ),
            "content_snapshot_taken": False,
            "warning": "HEAD excludes dirty/untracked enterprise work; this inventory is not a source freeze and makes no deployment claim.",
        },
        "interpretation": [
            "772 is the publisher's release-note count; Git reachability and PRs parsed from commit subjects are different scopes.",
            "A history member may have been modified/reverted. A non-member may have an adapted equivalent. Neither is an automatic integration verdict.",
            "Document mentions include deferred/skipped items and require human review; they never imply PASS.",
            "Topic routing uses subject keywords only and is not dependency or compatibility analysis.",
            "Production baseline must be recollected after other hotfixes settle; previous employee-sharing build inputs are not release authority.",
        ],
        "production_workloads_mutated": False,
        "application_source_modified_by_audit": False,
        "ready_for_rollout": False,
        "commits": rows,
    }


def write_report(output: Path, report: dict) -> None:
    # Exclusive creation preserves existing audit evidence (including symlinks).
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        raise FileExistsError(
            "Audit output already exists; use a new evidence filename"
        )
    report = collect(args.repo.resolve())
    write_report(args.output, report)
    print(
        json.dumps(
            {
                "release_inventory": "PASS",
                "counts": report["counts"],
                "workspace": report["workspace"],
                "ready_for_rollout": False,
                "report": str(args.output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

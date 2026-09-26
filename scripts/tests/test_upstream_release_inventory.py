"""Read-only release inventory must never imply production readiness."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, main
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "upstream_release_inventory.py"
SPEC = importlib.util.spec_from_file_location("upstream_release_inventory", SOURCE)
assert SPEC and SPEC.loader
inventory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory)


class ReleaseInventoryTests(TestCase):
    def test_log_parser_preserves_unicode_and_parents(self):
        raw = "\0".join(["a" * 40, "b" * 40, "2026-09-24", "修复: test (#12)", ""])
        row = inventory.parse_log(raw)[0]
        self.assertEqual(row["parents"], ["b" * 40])
        self.assertEqual(row["subject"], "修复: test (#12)")
        self.assertEqual(row["pr_numbers"], [12])

    def test_malformed_log_is_not_silently_truncated(self):
        with self.assertRaises(ValueError):
            inventory.parse_log("bad\0record\0")

    def test_pr_numbers_are_not_all_issue_mentions(self):
        self.assertEqual(inventory.pr_numbers("fix #33 (#55)"), [55])
        self.assertEqual(inventory.pr_numbers("Merge pull request #61 from x/y"), [61])
        self.assertEqual(inventory.pr_numbers("fix #33"), [])

    def test_documented_deferred_commit_is_not_marked_integrated(self):
        rows = inventory.parse_log(
            "\0".join(["a" * 40, "b" * 40, "2026-09-24", "fix: test (#12)", ""])
        )
        with TemporaryDirectory() as directory:
            root = Path(directory)
            doc = root / "batch.md"
            doc.write_text("Deferred: `aaaaaaa` pending SSO review\n", encoding="utf-8")
            mentions = inventory.document_mentions(root, [doc], rows)
        annotated = inventory.annotate(rows, set(), mentions)[0]
        self.assertEqual(annotated["review_state"], "NEEDS_SEMANTIC_REVIEW")
        self.assertFalse(annotated["reachable_from_enterprise_head"])
        self.assertEqual(
            annotated["document_mentions"], [{"path": "batch.md", "line": 1}]
        )

    def test_history_membership_is_not_runtime_verification(self):
        rows = inventory.parse_log(
            "\0".join(["a" * 40, "b" * 40, "2026-09-24", "feat: memory (#12)", ""])
        )
        annotated = inventory.annotate(rows, {"a" * 40}, {})[0]
        self.assertEqual(annotated["review_state"], "NEEDS_REGRESSION_REVIEW")
        self.assertTrue(annotated["reachable_from_enterprise_head"])
        self.assertIn("memory", annotated["triage_topics"])

    def test_ambiguous_short_sha_is_not_attributed(self):
        rows = [{"commit": "abcdef1" + "0" * 33}, {"commit": "abcdef1" + "1" * 33}]
        with TemporaryDirectory() as directory:
            root = Path(directory)
            doc = root / "batch.md"
            doc.write_text("`abcdef1`\n", encoding="utf-8")
            self.assertEqual(inventory.document_mentions(root, [doc], rows), {})

    def test_existing_report_is_never_overwritten(self):
        with TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            output.write_text("preserve", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                inventory.write_report(output, {"new": True})
            self.assertEqual(output.read_text(encoding="utf-8"), "preserve")

    def test_changed_release_tag_is_rejected(self):
        with patch.object(inventory, "resolve", return_value="c" * 40):
            with self.assertRaisesRegex(ValueError, "release tag|Release tag"):
                inventory.collect(Path("."))

    def test_collect_uses_only_read_only_git_and_keeps_rollout_blocked(self):
        head = "c" * 40
        calls = []

        def fake_git(repo, *args):
            calls.append(args)
            if args[0] == "rev-list":
                return (head if args[1] == head else inventory.TARGET) + "\n"
            if args[0] == "log":
                return "\0".join(
                    [
                        inventory.TARGET,
                        inventory.BASE,
                        "2026-09-24",
                        "release (#123)",
                        "",
                    ]
                )
            if args[0] == "diff":
                return "backend/dirty.py\0"
            if args[0] == "ls-files":
                return "local-untracked.txt\0"
            if args[0] == "merge-base":
                return "d" * 40 + "\n"
            if args[0] == "branch":
                return "codex/test\n"
            raise AssertionError(args)

        with (
            TemporaryDirectory() as directory,
            patch.object(inventory, "git", side_effect=fake_git),
            patch.object(
                inventory,
                "resolve",
                side_effect=[inventory.TARGET, inventory.BASE, head, head],
            ),
        ):
            report = inventory.collect(Path(directory))
        self.assertFalse(report["ready_for_rollout"])
        self.assertFalse(report["production_workloads_mutated"])
        self.assertFalse(report["workspace"]["content_snapshot_taken"])
        self.assertEqual(report["workspace"]["tracked_changed_paths"], 1)
        self.assertEqual(
            report["counts"]["range_commits_not_reachable_from_enterprise_head"], 1
        )
        self.assertEqual(report["commits"][0]["review_state"], "NEEDS_SEMANTIC_REVIEW")
        self.assertTrue(
            all(
                args[0]
                in {"rev-list", "log", "diff", "ls-files", "merge-base", "branch"}
                for args in calls
            )
        )


if __name__ == "__main__":
    main()

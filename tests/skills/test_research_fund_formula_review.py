"""The CI-only lexer disposition must not hide credentials or new source."""

import copy
import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend/packages/harness"))

import review_changed_public_skills as runner
import review_research_fund_formula as review
import review_research_fund_templates as office
from deerflow.skills.review.analyzer import analyze_skill_package
from deerflow.skills.review.readers import LocalDirectoryReader


@pytest.fixture(scope="module")
def released_review():
    snapshot = LocalDirectoryReader(ROOT / office.PACKAGE).read()
    return snapshot, analyze_skill_package(snapshot)


def lexer_entry(snapshot):
    return next(entry for entry in snapshot["files"] if entry["path"] == review.LEXER_PATH)


def replace_content(entry, content):
    entry.update(content=content, size=len(content.encode("utf-8")),
                 sha256=hashlib.sha256(content.encode("utf-8")).hexdigest())


def test_real_scanner_false_positive_is_resolved_without_mutating_raw_facts(released_review):
    snapshot, facts = released_review
    before = copy.deepcopy((snapshot, facts))
    assert sum(all(finding.get(key) == value for key, value in review.LEXER_FINDING.items())
               for finding in facts["findings"]) == 1
    assert not office.resolve_reviewed_findings(snapshot, facts)
    assert review.resolve_reviewed_findings(snapshot, facts)
    assert (snapshot, facts) == before


@pytest.mark.parametrize("placement", ["replace_regex", "after_regex", "another_file"])
def test_actual_secret_assignments_remain_blocking(released_review, placement):
    snapshot = copy.deepcopy(released_review[0])
    entry = lexer_entry(snapshot)
    # Synthetic non-placeholder values exercise the real scanner; no credential
    # or private user input is used by this test.
    credential = "PASSWORD = 'synthetic-credential-value-123456789'\n"
    if placement == "replace_regex":
        content = entry["content"].replace(
            "TOKEN = re.compile(",
            "TOKEN = 'synthetic-credential-value-123456789'\nPATTERN = re.compile(",
            1,
        )
        replace_content(entry, content)
    elif placement == "after_regex":
        # The scanner reports only the first secret-like assignment per file.
        # Whole-file pinning must reject a later real assignment even when its
        # finding still identifies the original TOKEN regex on line 12.
        replace_content(entry, entry["content"] + "\n" + credential)
    else:
        new_entry = {"path": "scripts/another.py", "kind": "text"}
        replace_content(new_entry, credential)
        snapshot["files"].append(new_entry)
    facts = analyze_skill_package(snapshot)
    assert any(finding["rule_id"] == "secret-env-assignment" for finding in facts["findings"])
    assert not review.resolve_reviewed_findings(snapshot, facts)


def test_even_nonsecret_lexer_source_changes_require_new_review(released_review):
    snapshot, facts = copy.deepcopy(released_review)
    entry = lexer_entry(snapshot)
    replace_content(entry, entry["content"] + "\n# a future source revision\n")
    assert not review.resolve_reviewed_findings(snapshot, facts)


@pytest.mark.parametrize(("field", "value"), [
    ("path", "scripts/another.py"),
    ("line", 13),
    ("source", "another-scanner"),
    ("profile", "agentskills"),
    ("rule_id", "secret-cloud-token"),
    ("severity", "blocker"),
    ("skillscan_severity", "CRITICAL"),
    ("evidence", "different evidence"),
])
def test_finding_identity_cannot_be_reused(released_review, field, value):
    snapshot, facts = copy.deepcopy(released_review)
    finding = next(item for item in facts["findings"]
                   if item["rule_id"] == "secret-env-assignment")
    finding[field] = value
    assert not review.resolve_reviewed_findings(snapshot, facts)


@pytest.mark.parametrize("change", [
    "duplicate_entry", "duplicate_finding", "missing_entry", "symlink",
    "missing_content", "wrong_size", "wrong_digest", "snapshot_reader_error",
    "truncated", "analyzer_error", "incomplete", "changed_office", "extra_error",
])
def test_disposition_preserves_snapshot_office_and_completeness_guards(released_review, change):
    snapshot, facts = copy.deepcopy(released_review)
    entry = lexer_entry(snapshot)
    if change == "duplicate_entry":
        snapshot["files"].append(copy.deepcopy(entry))
    elif change == "duplicate_finding":
        facts["findings"].append(copy.deepcopy(next(
            item for item in facts["findings"] if item["rule_id"] == "secret-env-assignment"
        )))
    elif change == "missing_entry":
        snapshot["files"].remove(entry)
    elif change == "symlink":
        entry["kind"] = "symlink"
    elif change == "missing_content":
        del entry["content"]
    elif change == "wrong_size":
        entry["size"] += 1
    elif change == "wrong_digest":
        entry["sha256"] = "0" * 64
    elif change == "snapshot_reader_error":
        snapshot["reader_errors"] = ["incomplete read"]
    elif change == "truncated":
        snapshot["truncated"] = True
    elif change == "analyzer_error":
        facts["analyzer_errors"] = ["scanner failure"]
    elif change == "incomplete":
        facts["completeness"]["not_assessed"] = ["unread content"]
    elif change == "changed_office":
        asset = next(item for item in snapshot["files"] if item["path"] in office.REVIEWED)
        asset["content_base64"] = "Y2hhbmdlZA=="
    else:
        facts["findings"].append({
            **review.LEXER_FINDING, "line": 100, "rule_id": "secret-private-key",
        })
    assert not review.resolve_reviewed_findings(snapshot, facts)


@pytest.mark.parametrize(("package", "raw_result", "expected_calls", "expected_result"), [
    ("research-fund-settlement", 1, 2, 0),
    ("research-fund-settlement", 0, 1, 0),
    ("another-skill", 1, 1, 1),
])
def test_only_failed_finance_review_uses_scoped_helper(
    tmp_path, monkeypatch, package, raw_result, expected_calls, expected_result
):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, raw_result if len(commands) == 1 else 0)

    monkeypatch.setattr(runner.subprocess, "run", run)
    assert runner.run_review(tmp_path / "skills/public" / package, tmp_path, "test-python") == expected_result
    assert len(commands) == expected_calls
    assert commands[0][1:3] == ["-m", "deerflow.skills.review.cli"]
    if expected_calls == 2:
        assert commands[1] == [
            "test-python", str(tmp_path / "scripts/review_research_fund_formula.py"),
        ]

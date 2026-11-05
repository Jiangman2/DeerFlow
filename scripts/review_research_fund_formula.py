"""CI disposition of the exact released formula lexer's TOKEN false positive.

This is separate from the sealed Office reviewer and the runtime scanner. The
November 5 candidate manifest pins the complete lexer file, not merely its name
or an assignment prefix. Any new source version needs an independent review.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
import sys

import review_research_fund_templates as office

ROOT = Path(__file__).resolve().parents[1]
LEXER_PATH = "scripts/workbook_formulas.py"
# release/research-fund-feedback-20261105/candidate-manifest.json
LEXER_SHA256 = "9cd6be8b9f5a73d67d87bc8ea7a1a9434119bbb9385f673ad77aa7ed1e4b78e7"
LEXER_FINDING = {
    "source": "skillscan",
    "profile": "deerflow",
    "rule_id": "secret-env-assignment",
    "severity": "error",
    "skillscan_severity": "HIGH",
    "path": LEXER_PATH,
    "line": 12,
    "evidence": "[redacted]",
}


def released_lexer_is_exact(snapshot: dict) -> bool:
    """Prove the finding refers to the reviewed regex without executing it."""
    entries = [entry for entry in snapshot.get("files", []) if entry.get("path") == LEXER_PATH]
    if len(entries) != 1 or entries[0].get("kind") != "text":
        return False
    entry = entries[0]
    content = entry.get("content")
    if not isinstance(content, str):
        return False
    data = content.encode("utf-8")
    if (hashlib.sha256(data).hexdigest() != LEXER_SHA256
            or entry.get("sha256") != LEXER_SHA256 or entry.get("size") != len(data)):
        return False
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return False
    assignments = [
        node for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "TOKEN"
    ]
    if len(assignments) != 1:
        return False
    assignment = assignments[0]
    value = assignment.value
    return (
        assignment.lineno == LEXER_FINDING["line"]
        and isinstance(value, ast.Call)
        and isinstance(value.func, ast.Attribute)
        and isinstance(value.func.value, ast.Name)
        and value.func.value.id == "re"
        and value.func.attr == "compile"
        and len(value.args) == 2
        and isinstance(value.args[0], ast.JoinedStr)
        and isinstance(value.args[1], ast.Attribute)
        and isinstance(value.args[1].value, ast.Name)
        and value.args[1].value.id == "re"
        and value.args[1].attr == "I"
        and not value.keywords
    )


def resolve_reviewed_findings(snapshot: dict, facts: dict) -> bool:
    """Retain all raw facts; resolve one pinned lexer finding and the Office set."""
    matches = [
        finding for finding in facts.get("findings", [])
        if all(finding.get(key) == value for key, value in LEXER_FINDING.items())
    ]
    if not matches:
        return office.resolve_reviewed_findings(snapshot, facts)
    if len(matches) != 1 or snapshot.get("reader_errors") or not released_lexer_is_exact(snapshot):
        return False
    # Only this new dictionary is filtered. The printed raw facts and original
    # sealed Office review remain unchanged, including every other error gate.
    remaining = {**facts, "findings": [
        finding for finding in facts["findings"] if finding is not matches[0]
    ]}
    return office.resolve_reviewed_findings(snapshot, remaining)


def main() -> int:
    sys.path.insert(0, str(ROOT / "backend/packages/harness"))
    from deerflow.skills.review.analyzer import analyze_skill_package
    from deerflow.skills.review.cli import _print_text
    from deerflow.skills.review.readers import LocalDirectoryReader

    snapshot = LocalDirectoryReader(ROOT / office.PACKAGE).read()
    facts = analyze_skill_package(snapshot)
    _print_text(facts)
    if not resolve_reviewed_findings(snapshot, facts):
        print("[skill-review] Released formula lexer and Office review did not resolve all blocking findings.")
        return 1
    print("[skill-review] Exact released formula lexer and five byte-pinned Office assets reviewed; raw findings retained.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

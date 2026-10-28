"""Pinned-template CI review must never waive changed assets or other errors."""

import base64
import copy
import io
from pathlib import Path
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import review_research_fund_templates as review  # noqa: E402


def snapshot_and_facts():
    snapshot = {"files": [
        {"path": path, "kind": "binary", "content_base64": base64.b64encode(
            (ROOT / review.PACKAGE / path).read_bytes()).decode()}
        for path in review.REVIEWED
    ]}
    facts = {"completeness": {"package_enumerated": True, "text_content_complete": True},
             "findings": [{"path": path, "source": "skillscan", "severity": "error",
                           "rule_id": "package-nested-archive"} for path in review.REVIEWED]}
    return snapshot, facts


def test_exact_deployed_templates_are_unpacked_and_accepted():
    snapshot, facts = snapshot_and_facts()
    assert review.resolve_reviewed_findings(snapshot, facts)


@pytest.mark.parametrize("change", ["bytes", "missing", "symlink", "extra_error", "critical_archive", "incomplete", "analyzer_error"])
def test_review_cannot_hide_drift_or_other_blockers(change):
    snapshot, facts = snapshot_and_facts()
    if change == "bytes":
        snapshot["files"][0]["content_base64"] = base64.b64encode(b"changed").decode()
    elif change == "missing":
        snapshot["files"].pop()
    elif change == "symlink":
        snapshot["files"][0]["kind"] = "symlink"
    elif change == "extra_error":
        facts["findings"].append({"severity": "error", "source": "skillscan", "path": "scripts/new.py", "rule_id": "secret-private-key"})
    elif change == "critical_archive":
        facts["findings"][0]["severity"] = "blocker"
    elif change == "incomplete":
        facts["completeness"]["not_assessed"] = ["truncated"]
    else:
        facts["analyzer_errors"] = ["scanner failure"]
    assert not review.resolve_reviewed_findings(snapshot, facts)


def replace_member(name, value):
    source = ROOT / review.PACKAGE / "assets/reference-examples/miit-example.docx"
    result = io.BytesIO()
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(result, "w") as changed:
        for info in original.infolist():
            if info.filename != name:
                changed.writestr(info, original.read(info))
        changed.writestr(name, value)
    return result.getvalue()


@pytest.mark.parametrize(("name", "value"), [
    ("../outside.xml", b"<x/>"),
    ("word/vbaProject.bin", b"macro"),
    ("word/payload.exe", b"MZbinary"),
    ("word/active.xml", b"<!DOCTYPE x [<!ENTITY y 'x'>]><x/>"),
    ("word/active.xml", "<!DOCTYPE x [<!ENTITY y 'x'>]><x/>".encode("utf-16")),
    ("word/active.xml", b"<oleObject/>"),
    ("word/active.xml", b"<altChunk/>"),
    ("word/active.xml", b"<instrText>DDEAUTO command</instrText>"),
    ("word/active.xml", b"<f>WEBSERVICE(&quot;https://example.com&quot;)</f>"),
    ("word/_rels/external.rels", b'<Relationships><Relationship TargetMode="External" Target="https://example.com"/></Relationships>'),
    ("word/media/fake.png", b"MZnot-an-image"),
])
def test_office_inspection_rejects_hostile_parts_without_extracting(name, value):
    with pytest.raises(ValueError):
        review.inspect_office(replace_member(name, value), ".docx")


def test_new_office_template_is_not_covered_by_existing_review():
    snapshot, facts = snapshot_and_facts()
    extra = copy.deepcopy(facts["findings"][0])
    extra["path"] = "assets/new.docx"
    facts["findings"].append(extra)
    assert not review.resolve_reviewed_findings(snapshot, facts)

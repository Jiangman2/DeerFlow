"""Derived reason labels remain readable in both report sections."""
import sys
from pathlib import Path
from zipfile import ZipFile
from lxml import etree

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "skills/public/research-fund-settlement/scripts"))
from docx_builder import build_docx, inspect_docx
from report_model import validate
from test_research_fund_nov05_model import complete


def test_derived_parent_reason_has_one_heading_per_paragraph(tmp_path):
    case = complete()
    reason = "结余主要原因：（1）测试化验加工费，合同价格低于预算；（2）试验费，减少测试次数"
    parent = next(row for row in case["expenses"] if row["code"] == "external_testing")
    parent["variance_reason"] = reason
    case["project"].pop("acceptance_date", None)
    normalized, issues = validate(case, "final")
    assert not [i for i in issues if i["severity"] == "ERROR"], issues
    output = build_docx(normalized, tmp_path / "synthetic-reasons.docx", "final")
    assert inspect_docx(output, normalized)["status"] == "PASS"
    with ZipFile(output) as archive:
        document = etree.fromstring(archive.read("word/document.xml"))
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs = ["".join(p.xpath(".//w:t/text()", namespaces=ns)) for p in document.xpath(".//w:body/w:p", namespaces=ns)]
    affected = [p for p in paragraphs if reason in p]
    assert len(affected) == 2
    assert all(p.count("结余主要原因：") == 1 for p in affected)
    assert not any("结余原因：结余主要原因" in p for p in paragraphs)

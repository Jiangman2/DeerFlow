"""The real DOCX must retain the approved purposes, not repeat variance reasons."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
from zipfile import ZipFile

from lxml import etree
import pytest

SKILL = Path(__file__).resolve().parents[2] / "skills/public/research-fund-settlement"
sys.path.insert(0, str(SKILL / "scripts"))
from docx_builder import build_docx, inspect_docx
from report_model import derive, validate
from template_builder import template_path

NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


def case():
    return json.loads((SKILL / "examples/cases/group-company-complete.json").read_text(encoding="utf-8"))


def paragraphs(path):
    with ZipFile(path) as z:
        root = etree.fromstring(z.read("word/document.xml"))
    return ["".join(p.xpath(".//w:t/text()", namespaces=NS))
            for p in root.xpath("./w:body/w:p", namespaces=NS)]


def sections(path):
    ps = paragraphs(path)
    start = ps.index("支出明细项及结构总体说明")
    end = ps.index("科技项目（课题）资金结余及原因")
    return ps[start + 1:end], ps[end + 1:]


def build(tmp_path, raw):
    normalized = derive(raw)
    output = build_docx(normalized, tmp_path / "synthetic.docx", "final")
    assert inspect_docx(output, normalized)["status"] == "PASS"
    return output


@pytest.mark.parametrize("code,name,purpose", [
    ("equipment_software", "设备与软件费", "主要用于项目研究所需仪器设备购置、试制及设备升级改造、专用软件的购置/开发与升级等。"),
    ("materials_energy", "材料/燃料动力费", "主要用于项目所需的原材料、辅助材料、成品/半成品、元器件、低值易耗品以及研究过程中直接使用的相关仪器设备、专用科学实验装置、中试或现场工业化试验装置单独计量或分摊计入的水、电、气及燃料等消耗费用等。"),
    ("external_testing", "外协实验/试验费", "主要为项目实施中外协进行测试化验、设计加工、试验验证、技术支持的服务支出。"),
    ("commissioned_research", "外委研究费", "主要为项目实施过程中由于受本企业自身的技术、工艺等条件的限制，委托相关单位进行研究与技术开发工作。"),
    ("publication_ip", "出版/文献/信息传播/知识产权事务费", "主要用于论文发表、专著出版、专利申请、软件著作权登记、文献检索、资料印制等科研事务支出。"),
    ("travel_meeting_cooperation", "会议/差旅/国际合作交流费", "主要用于项目研讨、技术对接、现场调研、试验核查、学术交流等差旅费、会议费支出，各项支出严格执行公司差旅会议标准，无超标列支。"),
    ("consulting_labor", "专家咨询评审/劳务费", "主要用于项目研究过程中所发生的支付给专家的专家咨询费、评审费及支付给课题组临时聘用人员的劳务费用，无虚报冒领、违规发放情况。"),
    ("other", "其他费用", "主要用于项目运输费、租赁/维修费、办公费、税费及其他支出。"),
])
def test_all_eight_original_purposes_survive_real_generation(tmp_path, code, name, purpose):
    raw = case()
    item = next(row for row in raw["expenses"] if row["code"] == code)
    item["variance_reason"] = "合成结余原因，仅用于原因章节"
    item["description"] = "项目组确认的合成用途补充。"
    source = template_path("group_company")
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    report = build(tmp_path, raw)
    expense, balance = sections(report)
    actual = next(p for p in expense if p.startswith(name + "："))
    assert purpose in actual
    assert actual.endswith(item["description"])
    assert "结余（负数为超支）" not in actual
    if code != "equipment_software":
        assert item["variance_reason"] not in actual
    assert item["variance_reason"] in "\n".join(balance)
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


def test_no_placeholder_contract_or_example_savings_is_present(tmp_path):
    raw = case()
    for row in raw["expenses"]:
        row["description"] = None
    expense, _ = sections(build(tmp_path, raw))
    text = "\n".join(expense)
    assert "XXX" not in text and "XX%" not in text
    assert "本项目签订外协合同共计" not in text
    assert "共计0项" not in text
    assert "如有节约" not in text and "国产化替代" not in text
    assert "无超标列支" in text  # User-approved template wording, not an audit result.


def test_verified_project_contract_details_are_used_instead_of_reasons(tmp_path):
    raw = case()
    testing = next(x for x in raw["expenses"] if x["code"] == "external_testing")
    testing["description"] = "本项目签订外协合同共计2项，合同名称分别为合成测试合同甲、合成试验合同乙，用于支撑项目研究可靠性测试内容。"
    testing["variance_reason"] = "结余主要原因：（1）测试化验加工费，合成谈判降价；（2）试验费，合成次数减少"
    before = deepcopy(raw)
    expense, balance = sections(build(tmp_path, raw))
    assert raw == before
    actual = next(p for p in expense if p.startswith("外协实验/试验费："))
    assert testing["description"] in actual
    assert testing["variance_reason"] not in actual
    assert testing["variance_reason"] in "\n".join(balance)


@pytest.mark.parametrize("actual,expected_savings", [(170, True), (180, False), (190, False)])
def test_equipment_savings_are_conditional_and_negative_balance_survives(tmp_path, actual, expected_savings):
    raw = case()
    item = raw["expenses"][0]
    item["actual"] = actual
    item["audited"] = None
    item["description"] = None
    expense, _ = sections(build(tmp_path, raw))
    paragraph = expense[0]
    assert f"实际支出{actual}万元，结余{180-actual}万元。" in paragraph
    assert ("节约原因：" in paragraph) == expected_savings


def test_audited_amount_and_original_budget_fallback_remain_authoritative(tmp_path):
    raw = case()
    raw["expenses"][0].update(adjusted_budget=None, audited=160)
    expense, _ = sections(build(tmp_path, raw))
    assert "批复概预算180万元，实际支出160万元，结余20万元。" in expense[0]


def test_zero_budget_omission_preserves_remaining_order_and_child_reasons(tmp_path):
    raw = case()
    for item in raw["expenses"][:2]:
        item["adjusted_budget"] = 0
    expense, _ = sections(build(tmp_path, raw))
    rows = [p for p in expense if "：批复概预算" in p]
    assert [p.split("：", 1)[0] for p in rows] == [
        "外协实验/试验费", "外委研究费", "出版/文献/信息传播/知识产权事务费",
        "会议/差旅/国际合作交流费", "专家咨询评审/劳务费", "其他费用",
    ]
    assert "主要为项目实施中外协进行测试化验" in rows[0]


def test_existing_complete_case_remains_valid_for_final_build(tmp_path):
    normalized, issues = validate(case(), "final")
    assert not [i for i in issues if i["severity"] == "ERROR"]
    build(tmp_path, normalized)

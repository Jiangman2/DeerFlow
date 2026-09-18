"""Template-preserving DOCX generation for research fund settlement reports.

Each output starts from the matching reference DOCX.  Only visible text slots and
the update-fields setting are changed; styles, section geometry, tables, headers,
footers, drawings, numbering, relationships, and all opaque package parts remain
source-derived.
"""

from __future__ import annotations

import hashlib
import re
import tempfile
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from lxml import etree

from report_model import get_path


SCHEME = "http"
W_NS = f"{SCHEME}://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = f"{SCHEME}://www.w3.org/XML/1998/namespace"
W = f"{{{W_NS}}}"
NS = {"w": W_NS}

TEMPLATES = {
    "group_company": {
        "file": "group-company-example.docx",
        "sha256": "85dc59c86546a68da6d2173de7662ced2769593e5db88d621a0fd7779b846a51",
        "paragraphs": 220,
        "tables": 18,
        "sections": 3,
    },
    "national_science": {
        "file": "national-science-example.docx",
        "sha256": "57539ce2de776ba81aa9358230e061cb9f97fb2ca441827f6dd6dae98584c883",
        "paragraphs": 121,
        "tables": 8,
        "sections": 1,
    },
    "miit": {
        "file": "miit-example.docx",
        "sha256": "34bc5dd3f8f951e6d6647f35f23f384ce95bb00b7a3a02a640a6a93a313a1b62",
        "paragraphs": 104,
        "tables": 0,
        "sections": 3,
    },
}

EDITABLE_PARTS = {"word/document.xml", "word/settings.xml"}


def _assets_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "assets" / "reference-examples"


def template_path(profile: str) -> Path:
    try:
        return _assets_dir() / str(TEMPLATES[profile]["file"])
    except KeyError as exc:
        raise ValueError(f"不支持的项目类型：{profile}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_template(profile: str, path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"缺少版式模板：{path}")
    actual = _sha256(path)
    expected = str(TEMPLATES[profile]["sha256"])
    if actual != expected:
        raise ValueError(
            f"版式模板指纹不一致：{path.name} expected={expected} actual={actual}"
        )


def _body_paragraphs(root: etree._Element) -> list[etree._Element]:
    body = root.find(f"{W}body")
    if body is None:
        raise ValueError("模板缺少 word/document.xml 主体")
    return [child for child in body if child.tag == f"{W}p"]


def _body_tables(root: etree._Element) -> list[etree._Element]:
    body = root.find(f"{W}body")
    if body is None:
        raise ValueError("模板缺少 word/document.xml 主体")
    return [child for child in body if child.tag == f"{W}tbl"]


def _set_text(container: etree._Element, value: Any) -> None:
    text = "" if value is None else str(value)
    nodes = container.xpath(".//w:t", namespaces=NS)
    if nodes:
        nodes[0].text = text
        if text.startswith(" ") or text.endswith(" "):
            nodes[0].set(f"{{{XML_NS}}}space", "preserve")
        for node in nodes[1:]:
            node.text = ""
        return
    paragraphs = container.xpath(".//w:p", namespaces=NS)
    paragraph = paragraphs[0] if paragraphs else container
    run = etree.SubElement(paragraph, f"{W}r")
    node = etree.SubElement(run, f"{W}t")
    node.text = text


def _set_paragraph(paragraphs: list[etree._Element], index: int, value: Any) -> None:
    if index >= len(paragraphs):
        raise ValueError(f"模板段落槽位不存在：{index}")
    _set_text(paragraphs[index], value)


def _set_cell(
    tables: list[etree._Element],
    table_index: int,
    row_index: int,
    cell_index: int,
    value: Any,
) -> None:
    try:
        rows = tables[table_index].xpath("./w:tr", namespaces=NS)
        cells = rows[row_index].xpath("./w:tc", namespaces=NS)
        cell = cells[cell_index]
    except IndexError as exc:
        raise ValueError(
            f"模板表格槽位不存在：table={table_index} row={row_index} cell={cell_index}"
        ) from exc
    _set_text(cell, value)


def _clear_paragraphs(paragraphs: list[etree._Element], indices: Iterable[int]) -> None:
    for index in indices:
        _set_paragraph(paragraphs, index, "")


def _money(value: Any, unit: bool = False) -> str:
    if value is None or value == "":
        return ""
    number = Decimal(str(value))
    text = f"{number:,.2f}".rstrip("0").rstrip(".")
    return f"{text}万元" if unit else text


def _percent(value: Any) -> str:
    if value is None or value == "":
        return ""
    return f"{Decimal(str(value)):.2f}".rstrip("0").rstrip(".") + "%"


def _date_cn(value: Any, month_only: bool = False) -> str:
    if not value:
        return ""
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError:
        return str(value)
    if month_only:
        return f"{parsed.year}年{parsed.month}月"
    return f"{parsed.year}年{parsed.month}月{parsed.day}日"


def _name_list(items: Iterable[dict[str, Any]]) -> str:
    return "、".join(str(item.get("name")) for item in items if item.get("name"))


def _expense(case: dict[str, Any], code: str) -> dict[str, Any]:
    for item in get_path(case, "_derived.expenses", []) or case.get("expenses", []):
        if item.get("code") == code:
            return item
    return {}


def _expense_by_name(case: dict[str, Any], fragment: str) -> dict[str, Any]:
    for item in get_path(case, "_derived.expenses", []) or case.get("expenses", []):
        if fragment in str(item.get("name") or ""):
            return item
    return {}


def _expense_sentence(item: dict[str, Any], audited: bool = False) -> str:
    if not item:
        return ""
    recognized = (
        item.get("audited")
        if audited and item.get("audited") is not None
        else item.get("actual")
    )
    balance = item.get("balance")
    parts = [
        f"{item.get('name', '')}：批复预算{_money(item.get('adjusted_budget') or item.get('budget'), True)}",
        f"实际支出{_money(recognized, True)}",
    ]
    if balance is not None:
        parts.append(f"结余（负数为超支）{_money(balance, True)}")
    detail = str(item.get("description") or "").strip()
    reason = str(item.get("variance_reason") or "").strip()
    sentence = "，".join(parts) + "。"
    if detail:
        sentence += detail
    if reason:
        sentence += f"差异原因：{reason}"
    return sentence


def _source_summary(case: dict[str, Any], field: str = "approved") -> str:
    values = []
    for source in get_path(case, "funding.sources", []) or []:
        values.append(f"{source.get('name', '')}{_money(source.get(field), True)}")
    return "、".join(values)


def _fill_group_legacy(
    case: dict[str, Any], p: list[etree._Element], t: list[etree._Element]
) -> None:
    project = case.get("project", {})
    report = case.get("report", {})
    lead = get_path(case, "organizations.lead", {}) or {}
    totals = get_path(case, "_derived.totals", {}) or {}
    expenses = get_path(case, "_derived.expenses", []) or []

    _set_paragraph(p, 0, project.get("code"))
    _set_paragraph(p, 17, f"项目(课题)名称：{project.get('name', '')}")
    entity_mode = project.get("entity_mode")
    project_type = (
        "有限公司科技项目" if entity_mode == "limited_company" else "集团公司科技项目"
    )
    _set_paragraph(p, 18, f"项 目 类 型：{project_type}")
    _set_paragraph(p, 19, f"承 担 单 位：{lead.get('name', '')}")
    _set_paragraph(
        p,
        20,
        f"起 止 时 间：{_date_cn(project.get('start_date'))} 至 {_date_cn(project.get('end_date'))}",
    )
    _set_paragraph(p, 22, f"报告编制人（签字）：{report.get('prepared_by', '')}")
    _set_paragraph(
        p, 23, f"项目(课题)负责人（签字）：{project.get('responsible_person', '')}"
    )
    _set_paragraph(
        p, 24, f"承担单位财务部门负责人（签字）：{report.get('reviewed_by', '')}"
    )
    _set_paragraph(p, 26, _date_cn(report.get("prepared_date")))

    _set_paragraph(
        p,
        74,
        f"根据{get_path(case, 'project.approval_document.title', '项目任务书')}，本项目批复概预算总额{_money(get_path(case, 'funding.approved_total'), True)}，其中{_source_summary(case)}。批复口径为{'不含税' if get_path(case, 'funding.tax_basis') == 'tax_excluded' else '含税'}。",
    )
    adjustments = get_path(case, "funding.adjustments", {}) or {}
    if adjustments.get("none_declared"):
        adjustment_text = "项目执行期间未发生需要单独披露的概预算变更或调剂。"
    else:
        items = adjustments.get("items") or []
        adjustment_text = "；".join(
            f"{item.get('level') or item.get('type') or '预算调整'}：{item.get('content', '')}，原因：{item.get('reason', '')}，批准情况：{item.get('approval', '')}"
            for item in items
        )
    _set_paragraph(p, 89, adjustment_text)
    _clear_paragraphs(p, range(90, 100))

    annual = get_path(case, "funding.annual_appropriations", []) or []
    annual_text = "、".join(
        f"{item.get('year')}年下达{_money(item.get('amount'), True)}" for item in annual
    )
    _set_paragraph(
        p,
        104,
        "科技项目实际批复情况",
    )
    _set_paragraph(
        p,
        108,
        f"项目分年实际下达预算总额{_money(get_path(case, 'funding.approved_total'), True)}；{annual_text}。",
    )
    _set_paragraph(p, 113, "1. 支出（预）决算总体情况")
    _set_paragraph(
        p,
        116,
        f"截至{_date_cn(report.get('cutoff_date'))}，项目批复概预算总额{_money(get_path(case, 'funding.approved_total'), True)}，实际支出{_money(totals.get('recognized'), True)}，预算执行率{_percent(totals.get('execution_rate'))}，结余（负数为超支）{_money(totals.get('balance'), True)}。",
    )
    _set_paragraph(p, 117, f"各来源资金执行情况：{_source_summary(case, 'spent')}。")

    expense_slots = {
        124: "equipment_software",
        125: "materials_energy",
        126: "external_testing",
        127: "commissioned_research",
        129: "publication_ip",
        130: "travel_meeting_cooperation",
        131: "consulting_labor",
        132: "other",
    }
    for index, code in expense_slots.items():
        _set_paragraph(p, index, _expense_sentence(_expense(case, code)))
    commissioned = _expense(case, "commissioned_research")
    direct = sum(Decimal(str(item.get("recognized") or 0)) for item in expenses)
    ratio = (
        Decimal(str(commissioned.get("recognized") or 0)) / direct * 100
        if direct
        else Decimal("0")
    )
    _set_paragraph(p, 128, f"外委研究费占项目支出的比例为{_percent(ratio)}。")

    _set_paragraph(
        p,
        138,
        f"截至决算报告编制日，项目概预算结余共计{_money(totals.get('balance'), True)}。差异达到10%的科目说明如下。",
    )
    variance_items = [
        item
        for item in expenses
        if abs(Decimal(str(item.get("variance_rate") or 0))) >= 10
    ]
    variance_slots = list(range(139, 147))
    for slot, item in zip(variance_slots, variance_items):
        _set_paragraph(
            p,
            slot,
            f"{item.get('name')}：结余（负数为超支）{_money(item.get('balance'), True)}，差异率{_percent(item.get('variance_rate'))}。{item.get('variance_reason') or item.get('description') or ''}",
        )
    _clear_paragraphs(p, variance_slots[len(variance_items) :])

    policies = get_path(case, "management.policies", []) or []
    _set_paragraph(
        p,
        152,
        f"{lead.get('name', '')}执行{get_path(case, 'management.accounting_standard', '')}，经费管理主要依据"
        + "、".join(
            f"《{item.get('name', '')}》{item.get('number', '')}" for item in policies
        )
        + "。",
    )
    _set_paragraph(p, 156, get_path(case, "management.management_summary", ""))
    _set_paragraph(p, 159, "无")
    _set_paragraph(p, 162, get_path(case, "management.finance_department_opinion", ""))

    expense_rows = {
        2: "equipment_software",
        3: "materials_energy",
        4: "external_testing",
        7: "commissioned_research",
        8: "publication_ip",
        9: "travel_meeting_cooperation",
        10: "consulting_labor",
        11: "other",
    }
    direct_budget = sum(Decimal(str(item.get("budget") or 0)) for item in expenses)
    direct_adjusted = sum(
        Decimal(str(item.get("adjusted_budget") or 0)) for item in expenses
    )
    for row, item in [
        (1, {"budget": direct_budget, "adjusted_budget": direct_adjusted})
    ] + [(row, _expense(case, code)) for row, code in expense_rows.items()]:
        before = item.get("budget")
        after = item.get("adjusted_budget")
        delta = Decimal(str(after or 0)) - Decimal(str(before or 0))
        _set_cell(t, 0, row, 1, _money(before))
        _set_cell(t, 0, row, 2, _money(delta))
        _set_cell(t, 0, row, 3, _money(after))

    sources = get_path(case, "funding.sources", []) or []
    company = next((s for s in sources if s.get("kind") in {"company", "group"}), {})
    self_funded = next((s for s in sources if s.get("kind") == "self_funded"), {})
    others = [s for s in sources if s is not company and s is not self_funded]
    _set_cell(t, 1, 2, 1, _money(company.get("approved")))
    _set_cell(t, 1, 2, 2, _money(self_funded.get("approved")))
    _set_cell(
        t, 1, 2, 3, _money(sum(Decimal(str(s.get("approved") or 0)) for s in others))
    )
    _set_cell(t, 1, 2, 4, _money(get_path(case, "funding.approved_total")))
    _set_cell(t, 1, 2, 6, _money(totals.get("recognized")))
    for offset, item in enumerate(annual[:3], 3):
        _set_cell(t, 1, offset, 0, f"{item.get('year')}年批准额")
        _set_cell(t, 1, offset, 1, _money(item.get("amount")))
        _set_cell(t, 1, offset, 5, f"{item.get('year')}年支出额")
        _set_cell(t, 1, offset, 6, "—")

    aggregate = {
        "budget": direct_budget,
        "adjusted_budget": direct_adjusted,
        "recognized": Decimal(str(totals.get("recognized") or 0)),
        "balance": Decimal(str(totals.get("balance") or 0)),
    }
    for row, item in [(1, aggregate)] + [
        (row, _expense(case, code)) for row, code in expense_rows.items()
    ]:
        _set_cell(t, 2, row, 1, _money(item.get("budget")))
        _set_cell(t, 2, row, 2, _money(item.get("adjusted_budget")))
        _set_cell(t, 2, row, 3, _money(item.get("recognized")))
        _set_cell(t, 2, row, 4, _money(item.get("balance")))
        if row != 1:
            _set_cell(
                t,
                2,
                row,
                5,
                item.get("description") or item.get("variance_reason") or "",
            )

    external = _expense(case, "external_testing")
    _set_cell(t, 10, 0, 1, external.get("description") or "")
    _set_cell(t, 10, 2, 1, external.get("name") or "")
    _set_cell(t, 10, 2, 2, _money(external.get("recognized")))
    _set_cell(t, 10, 2, 3, external.get("variance_reason") or "")
    _set_cell(t, 10, 16, 2, _money(external.get("recognized")))


def _fill_national(
    case: dict[str, Any], p: list[etree._Element], t: list[etree._Element]
) -> None:
    project = case.get("project", {})
    report = case.get("report", {})
    lead = get_path(case, "organizations.lead", {}) or {}
    participants = get_path(case, "organizations.participants", []) or []
    totals = get_path(case, "_derived.totals", {}) or {}
    expenses = get_path(case, "_derived.expenses", []) or []
    central = next(
        (
            s
            for s in get_path(case, "funding.sources", []) or []
            if s.get("kind") == "central_finance"
        ),
        {},
    )

    _set_paragraph(p, 2, project.get("plan_name") or "国家科技项目")
    _set_paragraph(p, 7, f"项目名称：{project.get('name', '')}")
    _set_paragraph(
        p, 8, f"课题名称：{project.get('topic_name') or project.get('name', '')}"
    )
    _set_paragraph(p, 9, f"课题编号：{project.get('code', '')}")
    _set_paragraph(p, 10, f"承担单位（盖章）：{lead.get('name', '')}")
    _set_paragraph(p, 11, f"参与单位：{_name_list(participants) or '无'}")
    _set_paragraph(
        p, 12, f"课题负责人（签名）：{project.get('responsible_person', '')}"
    )
    _set_paragraph(
        p,
        13,
        f"课题研究期限：{_date_cn(project.get('start_date'))}至{_date_cn(project.get('end_date'))}",
    )
    _set_paragraph(p, 17, _date_cn(report.get("prepared_date")))
    _set_paragraph(p, 22, f"{project.get('plan_name') or '国家科技项目'}财务决算报告")

    _set_paragraph(
        p,
        25,
        f"{lead.get('name', '')}成立于{_date_cn(lead.get('established_date'))}，统一社会信用代码为{lead.get('unified_social_credit_code', '')}，注册资本{lead.get('registered_capital', '')}，法定代表人为{lead.get('legal_representative', '')}，住所为{lead.get('registered_address', '')}。",
    )
    _set_paragraph(p, 26, f"公司经营范围：{lead.get('business_scope', '')}。")
    _set_paragraph(
        p, 27, f"与课题相关的技术能力：{lead.get('relevant_capability', '')}。"
    )
    _set_paragraph(
        p,
        28,
        "参与单位情况："
        + (
            "；".join(
                f"{x.get('name')}，{x.get('relevant_capability', '')}"
                for x in participants
            )
            or get_path(case, "organizations.no_participants_reason", "无参与单位")
        ),
    )
    _set_paragraph(
        p,
        30,
        f"本课题隶属于{project.get('plan_name', '')}，由{lead.get('name', '')}承担。",
    )
    _set_paragraph(
        p,
        31,
        f"课题承担、参与单位共{1 + len(participants)}家，分别为{lead.get('name', '')}{'、' + _name_list(participants) if participants else ''}。",
    )
    _set_paragraph(
        p, 32, f"课题名称：{project.get('topic_name') or project.get('name', '')}"
    )
    _set_paragraph(p, 33, f"课题编号：{project.get('code', '')}")
    _set_paragraph(
        p,
        34,
        f"课题起止时间：{_date_cn(project.get('start_date'), True)}至{_date_cn(project.get('end_date'), True)}",
    )
    _set_paragraph(p, 35, f"课题负责人：{project.get('responsible_person', '')}")
    _set_paragraph(
        p, 36, f"课题主要研究人员：{'、'.join(project.get('researchers') or [])}"
    )
    _set_paragraph(
        p,
        37,
        f"根据课题任务书，本课题主要研究内容包括：{'；'.join(project.get('research_content') or [])}。",
    )
    _set_paragraph(
        p,
        38,
        project.get("completion_summary") or project.get("governance_summary") or "",
    )
    _set_paragraph(
        p,
        40,
        f"{lead.get('name', '')}使用{get_path(case, 'management.accounting_system', '')}，中央财政资金和其他来源资金分别核算。",
    )
    _set_paragraph(
        p,
        42,
        f"单位执行{get_path(case, 'management.accounting_standard', '')}。{get_path(case, 'management.cost_accounting_summary', '')}",
    )
    _set_paragraph(p, 44, get_path(case, "management.internal_control_summary", ""))
    _set_paragraph(p, 45, get_path(case, "management.management_summary", ""))
    policies = get_path(case, "management.policies", []) or []
    _set_paragraph(
        p,
        48,
        "已建立并执行"
        + "、".join(f"《{x.get('name', '')}》{x.get('number', '')}" for x in policies)
        + "等制度。",
    )
    _set_paragraph(p, 50, "各项资金审批、报销、核算和外拨业务按上述制度执行。")
    _set_paragraph(p, 51, get_path(case, "procurement.summary", ""))
    _set_paragraph(p, 52, "项目财务资料按凭证、合同、拨款和审计资料分类归档。")

    adjustments = get_path(case, "funding.adjustments", {}) or {}
    _set_paragraph(
        p,
        55,
        f"课题中央财政资金预算为{_money(central.get('approved'), True)}，由财政拨款单位分次下达。",
    )
    _set_paragraph(
        p,
        56,
        "课题执行期间未发生预算调剂。"
        if adjustments.get("none_declared")
        else "课题预算调剂已按批复事项执行。",
    )
    _set_paragraph(
        p, 57, f"中央财政资金由{lead.get('name', '')}统一接收并按任务书和合作协议管理。"
    )
    _clear_paragraphs(p, [58, 61, 62, 65])

    _set_paragraph(
        p,
        68,
        f"截至{_date_cn(report.get('cutoff_date'))}，中央财政资金累计到位{_money(central.get('received'), True)}。",
    )
    _set_paragraph(
        p,
        69,
        f"中央财政资金到位额与预算批复额{'一致' if central.get('received') == central.get('approved') else '存在差异'}。",
    )
    transfers = get_path(case, "funding.transfers", []) or []
    transfer_total = sum(Decimal(str(x.get("amount") or 0)) for x in transfers)
    _set_paragraph(
        p, 73, f"承担单位累计向参与单位拨付中央财政资金{_money(transfer_total, True)}。"
    )
    _set_paragraph(p, 74, "具体拨付情况如下表，拨付依据为任务书、合作协议及拨款记录。")
    _set_paragraph(
        p,
        81,
        f"截至{_date_cn(report.get('cutoff_date'))}，中央财政资金账面累计支出{_money(totals.get('reported'), True)}，审计认定支出{_money(totals.get('audited'), True)}，审减{_money(totals.get('audit_reduction'), True)}。",
    )

    slots = {
        86: "equipment",
        87: "materials",
        88: "testing_processing",
        89: "fuel_power",
        90: "travel_meeting_cooperation",
        91: "publication_ip",
        92: "labor",
        93: "expert_consulting",
        95: "indirect",
    }
    for index, code in slots.items():
        _set_paragraph(p, index, _expense_sentence(_expense(case, code), audited=True))
    _set_paragraph(p, 96, "中央财政资金应付未付及预计后续情况")
    _set_paragraph(p, 97, get_path(case, "audit.payables_note", ""))
    _set_paragraph(
        p,
        98,
        f"截至{_date_cn(report.get('cutoff_date'))}，应付未付款项为{_money(get_path(case, 'audit.payables'), True)}。",
    )
    _set_paragraph(p, 101, get_path(case, "audit.payables_note", ""))
    _set_paragraph(
        p, 103, f"中央财政资金审定结余为{_money(totals.get('balance'), True)}。"
    )
    _set_paragraph(
        p,
        110,
        f"预计后续支出{_money(get_path(case, 'audit.expected_expenditure'), True)}。{get_path(case, 'audit.expected_expenditure_note', '')}",
    )
    other_sources = [
        s
        for s in get_path(case, "funding.sources", []) or []
        if s.get("kind") != "central_finance"
    ]
    _set_paragraph(
        p,
        115,
        "其他来源资金预算和到位情况："
        + "、".join(
            f"{s.get('name')}{_money(s.get('approved'), True)}，到位{_money(s.get('received'), True)}"
            for s in other_sources
        )
        + "。",
    )
    _set_paragraph(
        p,
        117,
        "其他来源资金支出情况："
        + "、".join(
            f"{s.get('name')}{_money(s.get('spent'), True)}" for s in other_sources
        )
        + "。",
    )
    _set_paragraph(p, 118, "财务档案已按项目资料分类整理并保存。")

    # Unit and category adjustment tables keep the source geometry.
    _set_cell(t, 0, 1, 0, lead.get("name"))
    _set_cell(t, 0, 1, 1, _money(central.get("approved")))
    _set_cell(t, 0, 1, 2, "0")
    _set_cell(t, 0, 1, 3, _money(central.get("approved")))
    _set_cell(t, 0, 1, 4, "项目总体预算")
    _set_cell(t, 0, 4, 1, _money(central.get("approved")))
    _set_cell(t, 0, 4, 2, "0")
    _set_cell(t, 0, 4, 3, _money(central.get("approved")))

    direct = [x for x in expenses if x.get("code") != "indirect"]
    indirect = _expense(case, "indirect")
    direct_budget = sum(Decimal(str(x.get("budget") or 0)) for x in direct)
    direct_adjusted = sum(Decimal(str(x.get("adjusted_budget") or 0)) for x in direct)
    for row, label, before, after in [
        (1, "直接费用", direct_budget, direct_adjusted),
        (2, "间接费用", indirect.get("budget"), indirect.get("adjusted_budget")),
    ]:
        _set_cell(t, 1, row, 0, label)
        _set_cell(t, 1, row, 1, _money(before))
        _set_cell(
            t, 1, row, 2, _money(Decimal(str(after or 0)) - Decimal(str(before or 0)))
        )
        _set_cell(t, 1, row, 3, _money(after))
    _set_cell(t, 1, 4, 1, _money(central.get("approved")))
    _set_cell(t, 1, 4, 2, "0")
    _set_cell(t, 1, 4, 3, _money(central.get("approved")))

    annual = get_path(case, "funding.annual_appropriations", []) or []
    for row, item in enumerate(annual[:3], 1):
        _set_cell(t, 2, row, 0, f"{item.get('year')}年")
        _set_cell(t, 2, row, 1, item.get("document") or "财政拨款单位")
        _set_cell(t, 2, row, 2, item.get("document") or "")
        _set_cell(t, 2, row, 3, _money(item.get("amount")))
    _set_cell(t, 2, 4, 1, _money(central.get("received")))

    for row, item in enumerate(transfers[:3], 1):
        _set_cell(t, 3, row, 0, f"{item.get('year')}年")
        _set_cell(t, 3, row, 1, item.get("organization"))
        _set_cell(t, 3, row, 2, "是")
        _set_cell(t, 3, row, 3, _money(item.get("amount")))
    _set_cell(t, 3, 4, 2, _money(transfer_total))

    expense_rows = {
        3: "equipment",
        8: "materials",
        9: "testing_processing",
        10: "fuel_power",
        11: "travel_meeting_cooperation",
        12: "publication_ip",
        14: "labor",
        15: "expert_consulting",
        16: "indirect",
    }
    for row, code in expense_rows.items():
        item = _expense(case, code)
        _set_cell(t, 4, row, 1, _money(item.get("budget")))
        _set_cell(t, 4, row, 2, _money(item.get("adjusted_budget")))
        _set_cell(
            t,
            4,
            row,
            3,
            _money(
                item.get("audited")
                if item.get("audited") is not None
                else item.get("actual")
            ),
        )
        _set_cell(t, 4, row, 4, _money(item.get("balance")))
    for row, values in {
        1: [
            central.get("approved"),
            central.get("approved"),
            totals.get("audited"),
            totals.get("balance"),
        ],
        2: [
            direct_budget,
            direct_adjusted,
            sum(Decimal(str(x.get("audited") or x.get("actual") or 0)) for x in direct),
            "",
        ],
    }.items():
        for col, value in enumerate(values, 1):
            _set_cell(t, 4, row, col, _money(value))

    payables = get_path(case, "audit.payables") or 0
    _set_cell(t, 5, 1, 0, "1")
    _set_cell(t, 5, 1, 1, lead.get("name"))
    _set_cell(t, 5, 1, 2, "承担")
    _set_cell(t, 5, 1, 3, "合计")
    _set_cell(t, 5, 1, 4, _money(payables))
    _set_cell(t, 5, 4, 1, _money(payables))
    _set_cell(t, 6, 1, 0, "1")
    _set_cell(t, 6, 1, 1, lead.get("name"))
    _set_cell(t, 6, 1, 2, "承担")
    for col, value in enumerate(
        [
            central.get("approved"),
            central.get("approved"),
            totals.get("audited"),
            totals.get("balance"),
        ],
        3,
    ):
        _set_cell(t, 6, 1, col, _money(value))
    expected = get_path(case, "audit.expected_expenditure") or 0
    _set_cell(t, 7, 1, 0, "1")
    _set_cell(t, 7, 1, 1, lead.get("name"))
    _set_cell(t, 7, 1, 2, "承担")
    _set_cell(t, 7, 1, 3, "验收及成果相关支出")
    _set_cell(t, 7, 1, 4, _money(expected))
    _set_cell(t, 7, 4, 1, _money(expected))


def _fill_miit(
    case: dict[str, Any], p: list[etree._Element], _t: list[etree._Element]
) -> None:
    project = case.get("project", {})
    report = case.get("report", {})
    lead = get_path(case, "organizations.lead", {}) or {}
    participants = get_path(case, "organizations.participants", []) or []
    totals = get_path(case, "_derived.totals", {}) or {}
    expenses = get_path(case, "_derived.expenses", []) or []

    _set_paragraph(
        p,
        0,
        f"{date.fromisoformat(report.get('prepared_date')).year}年                     {project.get('code', '')}",
    )
    _set_paragraph(p, 7, f"{project.get('name', '')}财务决算报告")
    _set_paragraph(p, 16, f"项目名称：{project.get('name', '')}")
    _set_paragraph(p, 17, f"执行单位：{lead.get('name', '')}")
    _set_paragraph(p, 18, f"编制日期：{_date_cn(report.get('prepared_date'))}")

    _set_paragraph(
        p,
        43,
        f"{lead.get('name', '')}（以下简称{lead.get('short_name') or '牵头单位'}）隶属于{lead.get('affiliation', '')}，注册地址为{lead.get('registered_address', '')}，主要从事{lead.get('business_scope', '')}，在{lead.get('relevant_capability', '')}方面具有技术积累。",
    )
    participant_slots = [45, 46, 47]
    for slot, item in zip(participant_slots, participants):
        _set_paragraph(
            p,
            slot,
            f"{item.get('name', '')}隶属于{item.get('affiliation', '')}，注册地址为{item.get('registered_address', '')}，在{item.get('relevant_capability', '')}方面具有相关技术积累。",
        )
    if not participants:
        _set_paragraph(
            p, 45, get_path(case, "organizations.no_participants_reason", "")
        )
    _clear_paragraphs(p, participant_slots[max(1, len(participants)) :])
    _set_paragraph(p, 49, project.get("governance_summary"))
    _set_paragraph(p, 51, project.get("completion_summary"))

    approval = project.get("approval_document") or {}
    _set_paragraph(
        p,
        54,
        f"{_date_cn(approval.get('date'))}，《{approval.get('title', '')}》（{approval.get('number', '')}）批复项目立项。",
    )
    _set_paragraph(
        p,
        56,
        f"项目批复总经费{_money(get_path(case, 'funding.approved_total'), True)}，资金来源为{_source_summary(case)}。",
    )
    _set_paragraph(
        p,
        57,
        f"项目批复牵头单位为{lead.get('name', '')}，参研单位为{_name_list(participants) or '无'}。",
    )
    _set_paragraph(
        p, 58, f"项目批复研究内容：{'；'.join(project.get('research_content') or [])}。"
    )
    _set_paragraph(
        p,
        59,
        f"项目批复研究周期为{_date_cn(project.get('start_date'), True)}至{_date_cn(project.get('end_date'), True)}。",
    )

    _set_paragraph(p, 61, "项目单位内控制度及项目管理机制运行情况如下。")
    _set_paragraph(p, 63, get_path(case, "management.internal_control_summary", ""))
    policies = get_path(case, "management.policies", []) or []
    _set_paragraph(
        p,
        64,
        "项目执行适用的主要制度包括："
        + "、".join(f"《{x.get('name', '')}》{x.get('number', '')}" for x in policies)
        + "。",
    )
    _set_paragraph(p, 66, get_path(case, "procurement.summary", ""))
    contracts = get_path(case, "procurement.contracts", []) or []
    _set_paragraph(
        p,
        67,
        "项目合同情况："
        + "；".join(
            f"{x.get('category')}共{x.get('count')}份、金额{_money(x.get('amount'), True)}，{x.get('status', '')}"
            for x in contracts
        )
        + "。",
    )
    _set_paragraph(p, 68, "采购及合同审批、履行和验收资料已按项目归档。")
    _set_paragraph(p, 70, get_path(case, "procurement.tendering_summary", ""))
    _set_paragraph(p, 71, "项目招投标事项按单位采购制度及授权审批程序执行。")
    _set_paragraph(p, 73, get_path(case, "management.accounting_policy", ""))
    _set_paragraph(p, 75, get_path(case, "management.cost_accounting_summary", ""))

    _set_paragraph(
        p,
        79,
        f"{_date_cn(project.get('start_date'), True)}至{_date_cn(report.get('cutoff_date'), True)}，项目研究经费累计下达{_money(get_path(case, 'funding.approved_total'), True)}，实际到位{_money(totals.get('source_received'), True)}；{_source_summary(case, 'received')}。",
    )
    transfers = get_path(case, "funding.transfers", []) or []
    transfer_total = sum(Decimal(str(x.get("amount") or 0)) for x in transfers)
    _set_paragraph(
        p,
        81,
        f"{lead.get('name', '')}按任务书和拨款依据向参研单位累计转拨{_money(transfer_total, True)}，其中：",
    )
    transfer_slots = [82, 83, 84]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in transfers:
        grouped.setdefault(str(item.get("organization") or ""), []).append(item)
    for slot, (org, items) in zip(transfer_slots, grouped.items()):
        total = sum(Decimal(str(x.get("amount") or 0)) for x in items)
        detail = "、".join(
            f"{x.get('year')}年度{_money(x.get('amount'), True)}" for x in items
        )
        _set_paragraph(p, slot, f"转拨{org}{_money(total, True)}，其中{detail}。")
    _clear_paragraphs(p, transfer_slots[len(grouped) :])

    expense_reported = "、".join(
        f"{x.get('name')}{_money(x.get('reported'), True)}" for x in expenses
    )
    expense_audited = "、".join(
        f"{x.get('name')}{_money(x.get('audited'), True)}"
        for x in expenses
        if x.get("audited") is not None
    )
    _set_paragraph(
        p,
        86,
        f"项目批复总经费{_money(get_path(case, 'funding.approved_total'), True)}，报审经费支出{_money(totals.get('reported'), True)}，其中{expense_reported}。经审计，审减{_money(totals.get('audit_reduction'), True)}，审定经费支出{_money(totals.get('audited'), True)}，其中{expense_audited}。项目结余{_money(totals.get('balance'), True)}。",
    )
    _set_paragraph(p, 87, f"1. {lead.get('name', '')}情况")
    _set_paragraph(
        p,
        88,
        f"项目单位报审经费支出{_money(totals.get('reported'), True)}，审计认定{_money(totals.get('audited'), True)}，审减{_money(totals.get('audit_reduction'), True)}，具体科目情况如下。",
    )
    _set_paragraph(p, 90, _expense_sentence(_expense(case, "materials"), audited=True))
    _set_paragraph(
        p, 92, _expense_sentence(_expense(case, "outsourcing"), audited=True)
    )
    special = _expense(case, "special")
    _set_paragraph(p, 94, _expense_sentence(special, audited=True))
    assets = case.get("assets") or []
    _set_paragraph(
        p,
        95,
        "形成资产情况："
        + (
            "；".join(
                f"{x.get('name')}，{x.get('quantity')}，价值{_money(x.get('value'), True)}，{x.get('management_status', '')}"
                for x in assets
            )
            or "无形成资产资料"
        ),
    )
    _set_paragraph(p, 96, "")
    variance = [
        x for x in expenses if abs(Decimal(str(x.get("variance_rate") or 0))) >= 30
    ]
    _set_paragraph(
        p,
        98,
        "；".join(
            f"{x.get('name')}结余（负数为超支）{_money(x.get('balance'), True)}，差异率{_percent(x.get('variance_rate'))}，原因：{x.get('variance_reason') or x.get('description') or ''}"
            for x in variance
        )
        or "无单项经费差异达到30%的情况。",
    )
    _set_paragraph(p, 100, "")
    outcomes = case.get("outcomes") or []
    _set_paragraph(
        p,
        102,
        f"项目实际研究周期为{_date_cn(project.get('actual_start_date') or project.get('start_date'), True)}至{_date_cn(project.get('actual_end_date') or project.get('end_date'), True)}。主要科研成果包括："
        + "；".join(
            f"{x.get('name')}（{x.get('type', '')}，{x.get('quantity', '')}，{x.get('status', '')}）"
            for x in outcomes
        )
        + (
            "。形成资产包括："
            + "；".join(
                f"{x.get('name')}{x.get('quantity')}，价值{_money(x.get('value'), True)}"
                for x in assets
            )
            if assets
            else "。"
        ),
    )


def _scrub_template_placeholders(root: etree._Element) -> None:
    placeholder = re.compile(
        r"(?:X{2,}|(?<![A-Za-z0-9])X(?![A-Za-z0-9])|20X{2}|202X|待补充|TBD|TO\s*DO|…{2,})",
        re.IGNORECASE,
    )
    for paragraph in root.xpath("//w:body//w:p", namespaces=NS):
        text = "".join(paragraph.xpath(".//w:t/text()", namespaces=NS)).strip()
        if text in {"示例：", "例如："} or placeholder.search(text):
            _set_text(paragraph, "")


def _enable_field_updates(settings: bytes) -> bytes:
    root = etree.fromstring(settings)
    nodes = root.xpath("./w:updateFields", namespaces=NS)
    if nodes:
        nodes[0].set(f"{W}val", "true")
    else:
        node = etree.Element(f"{W}updateFields")
        node.set(f"{W}val", "true")
        root.insert(0, node)
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _serialize_document(root: etree._Element) -> bytes:
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def build_template_docx(
    case: dict[str, Any], output_path: str | Path, mode: str = "final"
) -> Path:
    profile = str(case.get("profile") or "")
    source = template_path(profile)
    _verify_template(profile, source)
    output = Path(output_path).resolve()
    if output == source.resolve():
        raise ValueError("输出路径不能覆盖只读版式模板")
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(source) as archive:
        document = etree.fromstring(archive.read("word/document.xml"))
        paragraphs = _body_paragraphs(document)
        tables = _body_tables(document)
        expected = TEMPLATES[profile]
        if (
            len(paragraphs) != expected["paragraphs"]
            or len(tables) != expected["tables"]
        ):
            raise ValueError("模板主体结构与已审核槽位合同不一致")
        if len(document.xpath("//w:sectPr", namespaces=NS)) != expected["sections"]:
            raise ValueError("模板分节结构与版式合同不一致")

        if profile == "group_company":
            raise ValueError("集团公司模板必须通过 docx_builder.build_docx 生成")
        elif profile == "national_science":
            _fill_national(case, paragraphs, tables)
        elif profile == "miit":
            _fill_miit(case, paragraphs, tables)
        else:
            raise ValueError(f"不支持的项目类型：{profile}")
        if mode == "final":
            _scrub_template_placeholders(document)

        replacement = {
            "word/document.xml": _serialize_document(document),
            "word/settings.xml": _enable_field_updates(
                archive.read("word/settings.xml")
            ),
        }
        with tempfile.NamedTemporaryFile(
            delete=False, dir=output.parent, suffix=".docx"
        ) as temp:
            temp_path = Path(temp.name)
        try:
            with zipfile.ZipFile(temp_path, "w") as target:
                for info in archive.infolist():
                    target.writestr(
                        info,
                        replacement.get(info.filename, archive.read(info.filename)),
                    )
            temp_path.replace(output)
        finally:
            if temp_path.exists():
                temp_path.unlink()
    return output


def _document_structure_signature(data: bytes) -> str:
    root = etree.fromstring(data)
    for run in root.xpath("//w:r", namespaces=NS):
        for child in list(run):
            if child.tag in {f"{W}t", f"{W}delText"}:
                run.remove(child)
        if not run.attrib and len(run) == 0 and run.getparent() is not None:
            run.getparent().remove(run)
    canonical = etree.tostring(root, method="c14n")
    return hashlib.sha256(canonical).hexdigest()


def inspect_template_fidelity(docx_path: str | Path, profile: str) -> dict[str, Any]:
    output = Path(docx_path)
    source = template_path(profile)
    findings: list[dict[str, str]] = []
    try:
        _verify_template(profile, source)
        with zipfile.ZipFile(source) as reference, zipfile.ZipFile(output) as actual:
            reference_names = set(reference.namelist())
            actual_names = set(actual.namelist())
            if reference_names != actual_names:
                findings.append(
                    {
                        "code": "PACKAGE_PART_SET_CHANGED",
                        "message": "输出文档的 OOXML 部件集合与示例模板不一致",
                    }
                )
            for name in sorted(reference_names & actual_names - EDITABLE_PARTS):
                if reference.read(name) != actual.read(name):
                    findings.append(
                        {
                            "code": "PRESERVED_PART_CHANGED",
                            "message": f"未授权变更模板部件：{name}",
                        }
                    )
            if _document_structure_signature(
                reference.read("word/document.xml")
            ) != _document_structure_signature(actual.read("word/document.xml")):
                findings.append(
                    {
                        "code": "DOCUMENT_STRUCTURE_CHANGED",
                        "message": "段落、表格或分节结构偏离示例模板",
                    }
                )
    except (
        OSError,
        KeyError,
        zipfile.BadZipFile,
        etree.XMLSyntaxError,
        ValueError,
    ) as exc:
        findings.append({"code": "FIDELITY_CHECK_FAILED", "message": str(exc)})
    return {
        "status": "PASS" if not findings else "FAIL",
        "template": source.name,
        "template_sha256": TEMPLATES.get(profile, {}).get("sha256"),
        "preserved_parts": "all except word/document.xml text and word/settings.xml updateFields",
        "findings": findings,
    }

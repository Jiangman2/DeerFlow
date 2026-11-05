"""Small, dependency-free OOXML writer for Chinese settlement reports."""

from __future__ import annotations

import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from report_model import get_path, load_rules, selected_sections
from group_build import build_group_docx, inspect_group_fidelity
from template_builder import build_template_docx, inspect_template_fidelity


OOXML_SCHEME = "http"
W = f"{OOXML_SCHEME}://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = f"{OOXML_SCHEME}://schemas.openxmlformats.org/officeDocument/2006/relationships"
CP = (
    f"{OOXML_SCHEME}://schemas.openxmlformats.org/package/2006/metadata/core-properties"
)
DC = f"{OOXML_SCHEME}://purl.org/dc/elements/1.1/"
DCTERMS = f"{OOXML_SCHEME}://purl.org/dc/terms/"
XSI = f"{OOXML_SCHEME}://www.w3.org/2001/XMLSchema-instance"

ET.register_namespace("w", W)
ET.register_namespace("r", R)
ET.register_namespace("cp", CP)
ET.register_namespace("dc", DC)
ET.register_namespace("dcterms", DCTERMS)
ET.register_namespace("xsi", XSI)


def qn(namespace: str, tag: str) -> str:
    return f"{{{namespace}}}{tag}"


def add(parent: ET.Element, namespace: str, tag: str, **attrs: Any) -> ET.Element:
    node = ET.SubElement(parent, qn(namespace, tag))
    for key, value in attrs.items():
        attr_name = (
            qn(W, key[2:])
            if key.startswith("w_")
            else qn(R, key[2:])
            if key.startswith("r_")
            else key
        )
        node.set(attr_name, str(value))
    return node


def xml_bytes(root: ET.Element) -> bytes:
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def display(value: Any, label: str, mode: str) -> str:
    if value is None or value == "" or value == []:
        return f"[待补充：{label}]" if mode == "draft" else ""
    return str(value)


def money(value: Any, mode: str = "final", label: str = "金额") -> str:
    if value is None or value == "":
        return display(None, label, mode)
    number = float(value)
    if abs(number - round(number)) < 1e-9:
        return f"{int(round(number)):,}"
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def percent(value: Any, mode: str = "final", label: str = "比例") -> str:
    if value is None or value == "":
        return display(None, label, mode)
    return f"{float(value):.2f}".rstrip("0").rstrip(".") + "%"


def join_names(items: Iterable[dict[str, Any]]) -> str:
    return "、".join(str(item.get("name")) for item in items if item.get("name"))


def narrative(case: dict[str, Any], section_id: str) -> str | None:
    value = (case.get("narratives") or {}).get(section_id)
    return str(value).strip() if value else None


def paragraph(
    text: str = "",
    style: str | None = None,
    align: str | None = None,
    page_break: bool = False,
) -> ET.Element:
    node = ET.Element(qn(W, "p"))
    ppr = add(node, W, "pPr")
    if style:
        add(ppr, W, "pStyle", w_val=style)
    if align:
        add(ppr, W, "jc", w_val=align)
    if style in {"Title", "Subtitle", "Heading1", "Heading2"}:
        add(ppr, W, "keepNext")
    if page_break:
        run = add(node, W, "r")
        add(run, W, "br", w_type="page")
        return node
    lines = str(text).split("\n")
    for index, line in enumerate(lines):
        run = add(node, W, "r")
        if index:
            add(run, W, "br")
        text_node = add(run, W, "t")
        if line.startswith(" ") or line.endswith(" "):
            text_node.set(
                f"{{{OOXML_SCHEME}://www.w3.org/XML/1998/namespace}}space", "preserve"
            )
        text_node.text = line
    return node


def table(
    headers: list[str], rows: list[list[Any]], widths: list[int] | None = None
) -> ET.Element:
    column_count = len(headers)
    widths = widths or [int(9000 / column_count)] * column_count
    total_width = sum(widths)
    node = ET.Element(qn(W, "tbl"))
    tbl_pr = add(node, W, "tblPr")
    add(tbl_pr, W, "tblStyle", w_val="TableGrid")
    add(tbl_pr, W, "tblW", w_w=total_width, w_type="dxa")
    add(tbl_pr, W, "tblInd", w_w=0, w_type="dxa")
    margins = add(tbl_pr, W, "tblCellMar")
    for side in ("top", "left", "bottom", "right"):
        add(margins, W, side, w_w=100, w_type="dxa")
    grid = add(node, W, "tblGrid")
    for width in widths:
        add(grid, W, "gridCol", w_w=width)

    def add_row(values: list[Any], header: bool = False) -> None:
        row = add(node, W, "tr")
        if header:
            tr_pr = add(row, W, "trPr")
            add(tr_pr, W, "tblHeader")
        for index, value in enumerate(values):
            cell = add(row, W, "tc")
            cell_pr = add(cell, W, "tcPr")
            add(cell_pr, W, "tcW", w_w=widths[index], w_type="dxa")
            add(cell_pr, W, "vAlign", w_val="center")
            if header:
                add(cell_pr, W, "shd", w_fill="D9E2F3")
            p = paragraph(
                "" if value is None else str(value),
                style="TableText",
                align="center" if index != 0 or header else "left",
            )
            if header:
                for run in p.findall(qn(W, "r")):
                    rpr = add(run, W, "rPr")
                    add(rpr, W, "b")
            cell.append(p)

    add_row(headers, header=True)
    for values in rows:
        padded = list(values) + [""] * (column_count - len(values))
        add_row(padded[:column_count])
    return node


def _source_rows(case: dict[str, Any], mode: str) -> list[list[str]]:
    rows = []
    for item in case.get("funding", {}).get("sources") or []:
        rows.append(
            [
                display(item.get("name"), "资金来源", mode),
                money(item.get("approved"), mode, "批复金额"),
                money(item.get("received"), mode, "到位金额"),
                money(item.get("spent"), mode, "支出金额"),
                money(item.get("audited"), mode, "审定金额"),
            ]
        )
    return rows


def _expense_rows(case: dict[str, Any], mode: str) -> list[list[str]]:
    rows = []
    for item in get_path(case, "_derived.expenses", []) or []:
        if all(
            item.get(key) is None
            for key in ("adjusted_budget", "reported", "actual", "audited")
        ):
            continue
        description = item.get("description") or ""
        reason = item.get("variance_reason") or ""
        note = "；".join(
            text
            for text in (description, f"差异原因：{reason}" if reason else "")
            if text
        )
        rows.append(
            [
                item.get("name") or "",
                money(item.get("adjusted_budget"), mode, "调整后预算"),
                money(item.get("reported"), mode, "报审/实际支出"),
                money(item.get("audited"), mode, "审定支出")
                if item.get("audited") is not None
                else "—",
                money(item.get("balance"), mode, "结余/超支"),
                note,
            ]
        )
    return rows


def _variance_rows(
    case: dict[str, Any], mode: str, threshold: float = 10.0
) -> list[list[str]]:
    rows = []
    for item in get_path(case, "_derived.expenses", []) or []:
        rate = item.get("variance_rate")
        if rate is None or abs(float(rate)) < threshold:
            continue
        rows.append(
            [
                item.get("name") or "",
                money(item.get("adjusted_budget"), mode),
                money(item.get("recognized"), mode),
                money(item.get("balance"), mode),
                percent(rate, mode),
                display(item.get("variance_reason"), "差异原因", mode),
            ]
        )
    return rows


def _adjustment_blocks(case: dict[str, Any], mode: str) -> list[ET.Element]:
    adjustments = case.get("funding", {}).get("adjustments") or {}
    items = adjustments.get("items") or []
    if adjustments.get("none_declared") is True:
        return [paragraph("经核实，项目执行期间未发生需要单独披露的概预算调剂。")]
    if not items:
        return [paragraph(display(None, "预算调剂情况或无调剂依据", mode))]
    rows = [
        [
            item.get("level") or item.get("type") or "",
            item.get("content") or "",
            item.get("reason") or "",
            item.get("approval") or "",
            item.get("approval_date") or "",
        ]
        for item in items
    ]
    return [
        table(
            ["类别", "调整内容", "调整理由", "批准情况", "批准日期"],
            rows,
            [900, 2200, 2200, 2500, 1200],
        )
    ]


def _narrative_or_path(
    case: dict[str, Any], section_id: str, dotted: str, mode: str, label: str
) -> list[ET.Element]:
    text = narrative(case, section_id) or get_path(case, dotted)
    return [paragraph(display(text, label, mode))]


def section_content(
    case: dict[str, Any], section_id: str, mode: str
) -> list[ET.Element]:
    funding = case.get("funding", {})
    project = case.get("project", {})
    lead = case.get("organizations", {}).get("lead") or {}
    totals = get_path(case, "_derived.totals", {}) or {}
    sources = funding.get("sources") or []
    blocks: list[ET.Element] = []

    if section_id == "group.budget_approval":
        blocks.append(
            paragraph(
                f"根据项目任务合同及批复文件，本项目批复概预算总额为{money(funding.get('approved_total'), mode)}万元，资金来源如下。"
            )
        )
        blocks.append(
            table(
                ["资金来源", "批复", "到位", "支出", "审定"],
                _source_rows(case, mode),
                [2300, 1650, 1650, 1650, 1650],
            )
        )
    elif section_id == "group.budget_changes":
        blocks.extend(_adjustment_blocks(case, mode))
    elif section_id == "group.annual_approvals":
        rows = [
            [
                item.get("year") or "",
                money(item.get("amount"), mode),
                item.get("document") or "",
            ]
            for item in funding.get("annual_appropriations") or []
        ]
        blocks.append(
            table(
                ["年度", "正式下达预算（万元）", "依据"],
                rows or [[display(None, "分年预算", mode), "", ""]],
                [1600, 2600, 4800],
            )
        )
    elif section_id == "group.overall_settlement":
        blocks.append(
            paragraph(
                f"截至{display(get_path(case, 'report.cutoff_date'), '截止日期', mode)}，项目报告范围预算为{money(totals.get('target_budget'), mode)}万元，认定支出为{money(totals.get('recognized'), mode)}万元，预算执行率为{percent(totals.get('execution_rate'), mode)}，结余（负数为超支）为{money(totals.get('balance'), mode)}万元。"
            )
        )
    elif section_id == "group.expense_breakdown":
        blocks.append(
            table(
                [
                    "费用科目",
                    "调整后预算",
                    "报审/实际",
                    "审定",
                    "结余/超支",
                    "用途及差异说明",
                ],
                _expense_rows(case, mode),
                [1450, 1150, 1150, 1050, 1100, 3100],
            )
        )
    elif section_id == "group.balance_reasons":
        rows = _variance_rows(case, mode, 10)
        blocks.append(
            paragraph(
                f"项目总体结余（负数为超支）为{money(totals.get('balance'), mode)}万元。以下列示差异达到10%的科目。"
            )
        )
        blocks.append(
            table(
                ["费用科目", "预算", "认定支出", "结余/超支", "差异率", "原因"],
                rows or [["无达到10%的科目", "—", "—", "—", "—", "—"]],
                [1450, 1100, 1100, 1100, 1000, 3250],
            )
        )
    elif section_id == "group.management_basis":
        policies = case.get("management", {}).get("policies") or []
        blocks.append(
            table(
                ["制度名称", "文号/版本", "执行说明"],
                [
                    [
                        p.get("name") or "",
                        p.get("number") or p.get("version") or "",
                        p.get("execution") or "",
                    ]
                    for p in policies
                ]
                or [[display(None, "管理制度", mode), "", ""]],
                [3300, 1800, 4100],
            )
        )
    elif section_id == "group.management_status":
        blocks.extend(
            _narrative_or_path(
                case,
                section_id,
                "management.management_summary",
                mode,
                "经费管理情况及存在问题",
            )
        )
    elif section_id == "group.finance_opinion":
        blocks.extend(
            _narrative_or_path(
                case,
                section_id,
                "management.finance_department_opinion",
                mode,
                "财务部门意见",
            )
        )

    elif section_id == "national.entity_overview":
        rows = [
            ["单位全称", display(lead.get("name"), "承担单位", mode)],
            [
                "统一社会信用代码",
                display(
                    lead.get("unified_social_credit_code"), "统一社会信用代码", mode
                ),
            ],
            ["注册地址", display(lead.get("registered_address"), "注册地址", mode)],
            ["注册资本", display(lead.get("registered_capital"), "注册资本", mode)],
            [
                "法定代表人",
                display(lead.get("legal_representative"), "法定代表人", mode),
            ],
            ["经营范围", display(lead.get("business_scope"), "经营范围", mode)],
        ]
        blocks.append(table(["项目", "内容"], rows, [2200, 7000]))
    elif section_id == "national.project_implementation":
        rows = [
            ["所属计划/项目", project.get("plan_name") or project.get("name") or ""],
            ["课题名称", project.get("topic_name") or project.get("name") or ""],
            ["课题编号", display(project.get("code"), "课题编号", mode)],
            [
                "研究周期",
                f"{display(project.get('start_date'), '开始日期', mode)} 至 {display(project.get('end_date'), '结束日期', mode)}",
            ],
            [
                "课题负责人",
                display(project.get("responsible_person"), "课题负责人", mode),
            ],
            ["主要研究内容", "；".join(project.get("research_content") or [])],
        ]
        blocks.append(table(["项目", "内容"], rows, [2200, 7000]))
    elif section_id == "national.accounting":
        blocks.append(
            paragraph(
                f"{display(lead.get('name'), '承担单位', mode)}执行{display(get_path(case, 'management.accounting_standard'), '会计准则', mode)}，使用{display(get_path(case, 'management.accounting_system'), '财务核算系统', mode)}。{display(get_path(case, 'management.cost_accounting_summary'), '专项资金单独核算情况', mode)}"
            )
        )
    elif section_id == "national.internal_controls":
        blocks.extend(
            _narrative_or_path(
                case,
                section_id,
                "management.internal_control_summary",
                mode,
                "内部制度建设及执行情况",
            )
        )
        policies = case.get("management", {}).get("policies") or []
        if policies:
            blocks.append(
                table(
                    ["制度名称", "文号/版本", "执行情况"],
                    [
                        [
                            p.get("name") or "",
                            p.get("number") or p.get("version") or "",
                            p.get("execution") or "",
                        ]
                        for p in policies
                    ],
                    [3300, 1800, 4100],
                )
            )
    elif section_id == "national.budget_adjustments":
        blocks.extend(_adjustment_blocks(case, mode))
    elif section_id == "national.funds_received":
        central = [
            item
            for item in sources
            if item.get("kind") == "central_finance"
            or "中央财政" in str(item.get("name", ""))
        ]
        blocks.append(
            table(
                ["资金来源", "批复", "到位", "支出", "审定"],
                _source_rows(
                    {**case, "funding": {**funding, "sources": central or sources}},
                    mode,
                ),
                [2300, 1650, 1650, 1650, 1650],
            )
        )
    elif section_id == "national.funds_transferred":
        transfers = funding.get("transfers") or []
        if transfers:
            blocks.append(
                table(
                    ["接收单位", "年度", "金额（万元）", "依据"],
                    [
                        [
                            t.get("organization") or "",
                            t.get("year") or "",
                            money(t.get("amount"), mode),
                            t.get("document") or "",
                        ]
                        for t in transfers
                    ],
                    [2800, 1200, 1800, 3400],
                )
            )
        else:
            blocks.append(
                paragraph(
                    display(
                        get_path(case, "management.no_transfers_reason"),
                        "无转拨原因或转拨记录",
                        mode,
                    )
                )
            )
    elif section_id == "national.fund_usage":
        blocks.append(
            paragraph(
                f"截至{display(get_path(case, 'report.cutoff_date'), '截止日期', mode)}，账面/报审支出合计{money(totals.get('reported'), mode)}万元，审计认定支出{money(totals.get('audited'), mode)}万元。"
            )
        )
        blocks.append(
            table(
                [
                    "费用科目",
                    "调整后预算",
                    "报审/实际",
                    "审定",
                    "结余/超支",
                    "用途及差异说明",
                ],
                _expense_rows(case, mode),
                [1450, 1150, 1150, 1050, 1100, 3100],
            )
        )
    elif section_id == "national.payables_forecast":
        blocks.append(
            table(
                ["项目", "金额（万元）", "说明"],
                [
                    [
                        "应付未付",
                        money(get_path(case, "audit.payables"), mode),
                        get_path(case, "audit.payables_note", ""),
                    ],
                    [
                        "预计后续支出",
                        money(get_path(case, "audit.expected_expenditure"), mode),
                        get_path(case, "audit.expected_expenditure_note", ""),
                    ],
                ],
                [2600, 2200, 4400],
            )
        )

    elif section_id == "miit.lead_overview":
        blocks.append(
            paragraph(
                f"{display(lead.get('name'), '牵头单位全称', mode)}隶属于{display(lead.get('affiliation'), '所属集团/主管单位', mode)}，注册地址为{display(lead.get('registered_address'), '注册地址', mode)}，主要业务为{display(lead.get('business_scope'), '主要业务', mode)}，与本项目直接相关的技术积累为：{display(lead.get('relevant_capability'), '技术积累', mode)}。"
            )
        )
    elif section_id == "miit.participants_overview":
        participants = case.get("organizations", {}).get("participants") or []
        if participants:
            blocks.append(
                table(
                    ["参研单位", "隶属关系", "注册地址", "相关能力"],
                    [
                        [
                            p.get("name") or "",
                            p.get("affiliation") or "",
                            p.get("registered_address") or "",
                            p.get("relevant_capability") or "",
                        ]
                        for p in participants
                    ],
                    [2200, 1900, 2100, 3000],
                )
            )
        else:
            blocks.append(
                paragraph(
                    display(
                        get_path(case, "organizations.no_participants_reason"),
                        "参研单位情况",
                        mode,
                    )
                )
            )
    elif section_id == "miit.governance":
        blocks.extend(
            _narrative_or_path(
                case, section_id, "project.governance_summary", mode, "组织实施方式"
            )
        )
    elif section_id == "miit.completion":
        blocks.extend(
            _narrative_or_path(
                case, section_id, "project.completion_summary", mode, "项目完成情况"
            )
        )
    elif section_id == "miit.approval":
        approval = project.get("approval_document") or {}
        blocks.append(
            paragraph(
                f"{display(approval.get('date'), '批复日期', mode)}，{display(approval.get('title'), '批复文件题名', mode)}（{display(approval.get('number'), '批复文号', mode)}）批复本项目立项。"
            )
        )
        blocks.append(
            table(
                ["批复事项", "批复内容"],
                [
                    ["项目总经费", f"{money(funding.get('approved_total'), mode)}万元"],
                    [
                        "资金来源",
                        "；".join(
                            f"{s.get('name')} {money(s.get('approved'), mode)}万元"
                            for s in sources
                        ),
                    ],
                    ["牵头单位", lead.get("name") or ""],
                    [
                        "参研单位",
                        join_names(
                            case.get("organizations", {}).get("participants") or []
                        )
                        or "无",
                    ],
                    ["研究内容", "；".join(project.get("research_content") or [])],
                    [
                        "研究周期",
                        f"{display(project.get('start_date'), '开始日期', mode)} 至 {display(project.get('end_date'), '结束日期', mode)}",
                    ],
                ],
                [2200, 7000],
            )
        )
    elif section_id == "miit.internal_controls":
        blocks.extend(
            _narrative_or_path(
                case,
                section_id,
                "management.internal_control_summary",
                mode,
                "内控制度情况",
            )
        )
        policies = case.get("management", {}).get("policies") or []
        if policies:
            blocks.append(
                table(
                    ["制度名称", "文号/版本", "执行情况"],
                    [
                        [
                            p.get("name") or "",
                            p.get("number") or p.get("version") or "",
                            p.get("execution") or "",
                        ]
                        for p in policies
                    ],
                    [3300, 1800, 4100],
                )
            )
    elif section_id == "miit.procurement_contracts":
        blocks.extend(
            _narrative_or_path(
                case, section_id, "procurement.summary", mode, "采购及合同管理情况"
            )
        )
        contracts = case.get("procurement", {}).get("contracts") or []
        if contracts:
            blocks.append(
                table(
                    ["合同类别", "份数", "金额（万元）", "执行情况"],
                    [
                        [
                            c.get("category") or "",
                            c.get("count") or "",
                            money(c.get("amount"), mode),
                            c.get("status") or "",
                        ]
                        for c in contracts
                    ],
                    [2400, 1100, 1800, 3900],
                )
            )
    elif section_id == "miit.tendering":
        blocks.extend(
            _narrative_or_path(
                case, section_id, "procurement.tendering_summary", mode, "招投标情况"
            )
        )
        tenders = case.get("procurement", {}).get("tenders") or []
        if tenders:
            blocks.append(
                table(
                    ["招标类型", "项数", "中标金额（万元）", "合同一致性"],
                    [
                        [
                            t.get("type") or "",
                            t.get("count") or "",
                            money(t.get("amount"), mode),
                            t.get("contract_consistency") or "",
                        ]
                        for t in tenders
                    ],
                    [2300, 1200, 2200, 3500],
                )
            )
    elif section_id == "miit.accounting_policy":
        blocks.extend(
            _narrative_or_path(
                case, section_id, "management.accounting_policy", mode, "主要会计政策"
            )
        )
    elif section_id == "miit.cost_accounting":
        blocks.extend(
            _narrative_or_path(
                case,
                section_id,
                "management.cost_accounting_summary",
                mode,
                "成本核算情况",
            )
        )
    elif section_id == "miit.funding_arrival":
        blocks.append(
            table(
                ["资金来源", "批复", "到位", "支出", "审定"],
                _source_rows(case, mode),
                [2300, 1650, 1650, 1650, 1650],
            )
        )
        blocks.append(
            paragraph(
                f"批复资金合计{money(funding.get('approved_total'), mode)}万元，实际到位合计{money(totals.get('source_received'), mode)}万元。"
            )
        )
    elif section_id == "miit.fund_management":
        transfers = funding.get("transfers") or []
        if transfers:
            blocks.append(
                paragraph(
                    f"牵头单位累计向参研单位转拨资金{money(totals.get('transferred'), mode)}万元。"
                )
            )
            blocks.append(
                table(
                    ["接收单位", "年度", "金额（万元）", "依据"],
                    [
                        [
                            t.get("organization") or "",
                            t.get("year") or "",
                            money(t.get("amount"), mode),
                            t.get("document") or "",
                        ]
                        for t in transfers
                    ],
                    [2800, 1200, 1800, 3400],
                )
            )
        else:
            blocks.append(
                paragraph(
                    display(
                        get_path(case, "management.no_transfers_reason"),
                        "无转拨原因或转拨记录",
                        mode,
                    )
                )
            )
    elif section_id == "miit.audit_confirmation":
        blocks.append(
            paragraph(
                f"项目单位报审经费支出{money(get_path(case, 'audit.reported_expenditure'), mode)}万元，审减{money(get_path(case, 'audit.audit_reduction'), mode)}万元，审定经费支出{money(get_path(case, 'audit.recognized_expenditure'), mode)}万元，审定结余（负数为超支）{money(totals.get('balance'), mode)}万元。"
            )
        )
        blocks.append(
            table(
                [
                    "费用科目",
                    "调整后预算",
                    "报审/实际",
                    "审定",
                    "结余/超支",
                    "用途及差异说明",
                ],
                _expense_rows(case, mode),
                [1450, 1150, 1150, 1050, 1100, 3100],
            )
        )
    elif section_id == "miit.variance":
        rows = _variance_rows(case, mode, 10)
        blocks.append(
            table(
                ["费用科目", "预算", "认定支出", "结余/超支", "差异率", "原因"],
                rows or [["无达到10%的科目", "—", "—", "—", "—", "—"]],
                [1450, 1100, 1100, 1100, 1000, 3250],
            )
        )
    elif section_id == "miit.period_outcomes":
        blocks.append(
            paragraph(
                f"项目实际研究周期为{display(project.get('actual_start_date') or project.get('start_date'), '实际开始日期', mode)}至{display(project.get('actual_end_date') or project.get('end_date'), '实际结束日期', mode)}。"
            )
        )
        outcomes = case.get("outcomes") or []
        assets = case.get("assets") or []
        blocks.append(
            table(
                ["成果名称", "类型", "数量/状态", "说明"],
                [
                    [
                        o.get("name") or "",
                        o.get("type") or "",
                        o.get("quantity") or o.get("status") or "",
                        o.get("description") or "",
                    ]
                    for o in outcomes
                ]
                or [[display(None, "科研成果", mode), "", "", ""]],
                [3000, 1600, 1600, 3000],
            )
        )
        blocks.append(
            table(
                ["资产名称", "类别", "数量", "价值（万元）", "资产管理情况"],
                [
                    [
                        a.get("name") or "",
                        a.get("type") or "",
                        a.get("quantity") or "",
                        money(a.get("value"), mode),
                        a.get("management_status") or "",
                    ]
                    for a in assets
                ]
                or [[display(None, "形成资产", mode), "", "", "", ""]],
                [2500, 1400, 1000, 1700, 2600],
            )
        )

    extra = narrative(case, section_id)
    if extra and not any(extra in "".join(node.itertext()) for node in blocks):
        blocks.append(paragraph(extra))
    return blocks or [paragraph(display(None, section_id, mode))]


def build_document_xml(case: dict[str, Any], mode: str) -> bytes:
    rules = load_rules()["profiles"][case["profile"]]
    document = ET.Element(qn(W, "document"))
    body = add(document, W, "body")

    body.append(paragraph("", style="Normal"))
    body.append(paragraph(rules["report_title"], style="Title", align="center"))
    body.append(
        paragraph(
            display(get_path(case, "project.name"), "项目名称", mode),
            style="Subtitle",
            align="center",
        )
    )
    body.append(paragraph("", style="Normal"))
    cover_rows = [
        ["项目类型", rules["display_name"]],
        ["项目编号", display(get_path(case, "project.code"), "项目编号", mode)],
        [
            "承担/牵头单位",
            display(get_path(case, "organizations.lead.name"), "承担/牵头单位", mode),
        ],
        [
            "项目负责人",
            display(get_path(case, "project.responsible_person"), "项目负责人", mode),
        ],
        [
            "决算截止日",
            display(get_path(case, "report.cutoff_date"), "决算截止日", mode),
        ],
        ["编制日期", display(get_path(case, "report.prepared_date"), "编制日期", mode)],
        ["编制人", display(get_path(case, "report.prepared_by"), "编制人", mode)],
        ["复核人", display(get_path(case, "report.reviewed_by"), "复核人", mode)],
    ]
    body.append(table(["项目", "内容"], cover_rows, [2600, 6600]))
    body.append(paragraph(page_break=True))

    body.append(paragraph("报告目录", style="Heading1", align="center"))
    contents_rows = [
        [index, section["heading"]]
        for index, section in enumerate(selected_sections(case), 1)
    ]
    body.append(table(["序号", "栏目"], contents_rows, [1400, 7800]))
    body.append(paragraph(page_break=True))

    body.append(paragraph("决算概览", style="Heading1"))
    totals = get_path(case, "_derived.totals", {}) or {}
    summary_rows = [
        ["批复总额", money(get_path(case, "funding.approved_total"), mode), "万元"],
        ["资金到位", money(totals.get("source_received"), mode), "万元"],
        ["报审/账面支出", money(totals.get("reported"), mode), "万元"],
        [
            "审计认定支出",
            money(totals.get("audited"), mode)
            if totals.get("audited") is not None
            else "—",
            "万元",
        ],
        ["结余（负数为超支）", money(totals.get("balance"), mode), "万元"],
        ["预算执行率", percent(totals.get("execution_rate"), mode), ""],
        [
            "税口径",
            {
                "tax_included": "含税",
                "tax_excluded": "不含税",
                "not_applicable": "不适用",
            }.get(get_path(case, "funding.tax_basis"), display(None, "税口径", mode)),
            "",
        ],
    ]
    body.append(table(["指标", "数值", "单位"], summary_rows, [4200, 3000, 2000]))

    for index, section in enumerate(selected_sections(case), 1):
        body.append(paragraph(f"{index}. {section['heading']}", style="Heading1"))
        for block in section_content(case, section["id"], mode):
            body.append(block)

    if case.get("evidence"):
        body.append(paragraph("附录：主要证据索引", style="Heading1"))
        evidence_rows = [
            [
                item.get("id") or "",
                item.get("source_file") or "",
                item.get("locator") or "",
                item.get("fact") or "",
            ]
            for item in case.get("evidence") or []
        ]
        body.append(
            table(
                ["ID", "来源文件", "定位", "支持事实"],
                evidence_rows,
                [900, 2600, 2200, 3500],
            )
        )

    sect_pr = add(body, W, "sectPr")
    add(sect_pr, W, "footerReference", w_type="default", r_id="rId1")
    add(sect_pr, W, "pgSz", w_w=11906, w_h=16838)
    add(
        sect_pr,
        W,
        "pgMar",
        w_top=1440,
        w_right=1800,
        w_bottom=1440,
        w_left=1800,
        w_header=720,
        w_footer=720,
        w_gutter=0,
    )
    add(sect_pr, W, "cols", w_space=720)
    return xml_bytes(document)


def styles_xml() -> bytes:
    styles = ET.Element(qn(W, "styles"))
    doc_defaults = add(styles, W, "docDefaults")
    rpr_default = add(add(doc_defaults, W, "rPrDefault"), W, "rPr")
    add(
        rpr_default,
        W,
        "rFonts",
        w_ascii="Times New Roman",
        w_hAnsi="Times New Roman",
        w_eastAsia="宋体",
    )
    add(rpr_default, W, "sz", w_val=24)
    add(rpr_default, W, "szCs", w_val=24)
    ppr_default = add(add(doc_defaults, W, "pPrDefault"), W, "pPr")
    add(ppr_default, W, "spacing", w_after=120, w_line=360, w_lineRule="auto")

    def style(
        style_id: str,
        name: str,
        based_on: str | None = None,
        size: int = 24,
        bold: bool = False,
        east_asia: str = "宋体",
        align: str | None = None,
        before: int = 0,
        after: int = 120,
    ) -> None:
        node = add(styles, W, "style", w_type="paragraph", w_styleId=style_id)
        add(node, W, "name", w_val=name)
        if based_on:
            add(node, W, "basedOn", w_val=based_on)
        add(node, W, "qFormat")
        ppr = add(node, W, "pPr")
        add(
            ppr,
            W,
            "spacing",
            w_before=before,
            w_after=after,
            w_line=360,
            w_lineRule="auto",
        )
        if style_id == "Heading1":
            add(ppr, W, "outlineLvl", w_val=0)
        if align:
            add(ppr, W, "jc", w_val=align)
        rpr = add(node, W, "rPr")
        add(
            rpr,
            W,
            "rFonts",
            w_ascii="Times New Roman",
            w_hAnsi="Times New Roman",
            w_eastAsia=east_asia,
        )
        if bold:
            add(rpr, W, "b")
        add(rpr, W, "sz", w_val=size)
        add(rpr, W, "szCs", w_val=size)

    style("Normal", "Normal", size=24, after=120)
    style(
        "Title",
        "Title",
        "Normal",
        size=44,
        bold=True,
        east_asia="黑体",
        align="center",
        before=720,
        after=360,
    )
    style(
        "Subtitle",
        "Subtitle",
        "Normal",
        size=32,
        bold=True,
        east_asia="黑体",
        align="center",
        before=240,
        after=480,
    )
    style(
        "Heading1",
        "heading 1",
        "Normal",
        size=30,
        bold=True,
        east_asia="黑体",
        before=320,
        after=160,
    )
    style(
        "Heading2",
        "heading 2",
        "Normal",
        size=26,
        bold=True,
        east_asia="黑体",
        before=240,
        after=120,
    )
    style("TableText", "Table Text", "Normal", size=20, after=0)

    tbl_style = add(styles, W, "style", w_type="table", w_styleId="TableGrid")
    add(tbl_style, W, "name", w_val="Table Grid")
    tbl_pr = add(tbl_style, W, "tblPr")
    borders = add(tbl_pr, W, "tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        add(borders, W, side, w_val="single", w_sz=4, w_space=0, w_color="808080")
    return xml_bytes(styles)


def footer_xml() -> bytes:
    footer = ET.Element(qn(W, "ftr"))
    p = add(footer, W, "p")
    ppr = add(p, W, "pPr")
    add(ppr, W, "jc", w_val="center")
    run = add(p, W, "r")
    add(run, W, "fldChar", w_fldCharType="begin")
    run = add(p, W, "r")
    instr = add(run, W, "instrText")
    instr.set(f"{{{OOXML_SCHEME}://www.w3.org/XML/1998/namespace}}space", "preserve")
    instr.text = " PAGE "
    run = add(p, W, "r")
    add(run, W, "fldChar", w_fldCharType="end")
    return xml_bytes(footer)


def settings_xml() -> bytes:
    settings = ET.Element(qn(W, "settings"))
    add(settings, W, "updateFields", w_val="true")
    add(settings, W, "defaultTabStop", w_val=720)
    return xml_bytes(settings)


def core_xml(case: dict[str, Any]) -> bytes:
    core = ET.Element(qn(CP, "coreProperties"))
    add(
        core, DC, "title"
    ).text = f"{get_path(case, 'project.name', '')} 科研经费决算报告"
    add(core, DC, "creator").text = str(
        get_path(case, "report.prepared_by", "DeerFlow") or "DeerFlow"
    )
    add(core, CP, "lastModifiedBy").text = "DeerFlow research-fund-settlement"
    now = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    created = add(core, DCTERMS, "created")
    created.set(qn(XSI, "type"), "dcterms:W3CDTF")
    created.text = now
    modified = add(core, DCTERMS, "modified")
    modified.set(qn(XSI, "type"), "dcterms:W3CDTF")
    modified.text = now
    return xml_bytes(core)


def xml_literal(value: bytes) -> bytes:
    return value.replace(b"__SCHEME__", OOXML_SCHEME.encode("ascii"))


CONTENT_TYPES = xml_literal(b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="__SCHEME__://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
  <Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>""")

ROOT_RELS = xml_literal(b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="__SCHEME__://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="__SCHEME__://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="__SCHEME__://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="__SCHEME__://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>""")

DOCUMENT_RELS = xml_literal(b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="__SCHEME__://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="__SCHEME__://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>
  <Relationship Id="rId2" Type="__SCHEME__://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId3" Type="__SCHEME__://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>
</Relationships>""")

APP_XML = xml_literal(b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="__SCHEME__://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="__SCHEME__://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>DeerFlow</Application><DocSecurity>0</DocSecurity><ScaleCrop>false</ScaleCrop><Company></Company><LinksUpToDate>false</LinksUpToDate><SharedDoc>false</SharedDoc><HyperlinksChanged>false</HyperlinksChanged><AppVersion>1.0</AppVersion>
</Properties>""")


def build_docx(
    case: dict[str, Any], output_path: str | Path, mode: str = "final"
) -> Path:
    if case.get("profile") == "group_company":
        return build_group_docx(case, output_path, mode=mode)
    return build_template_docx(case, output_path, mode=mode)


def inspect_docx(docx_path: str | Path, case: dict[str, Any]) -> dict[str, Any]:
    path = Path(docx_path)
    findings: list[dict[str, str]] = []
    fidelity = (
        inspect_group_fidelity(path, case)
        if case.get("profile") == "group_company"
        else inspect_template_fidelity(path, str(case.get("profile") or ""))
    )
    findings.extend(fidelity["findings"])
    if not path.exists() or path.stat().st_size == 0:
        return {
            "status": "FAIL",
            "findings": [{"code": "MISSING_DOCX", "message": "DOCX 不存在或为空"}],
        }
    try:
        with zipfile.ZipFile(path) as archive:
            bad_part = archive.testzip()
            if bad_part:
                findings.append(
                    {
                        "code": "CORRUPT_PART",
                        "message": f"DOCX 压缩包部件损坏：{bad_part}",
                    }
                )
            required_parts = {
                "[Content_Types].xml",
                "_rels/.rels",
                "word/document.xml",
                "word/styles.xml",
                "word/footer1.xml",
                "word/_rels/document.xml.rels",
            }
            missing = sorted(required_parts - set(archive.namelist()))
            if missing:
                findings.append({"code": "MISSING_PART", "message": ", ".join(missing)})
            document = ET.fromstring(archive.read("word/document.xml"))
            text = "".join(node.text or "" for node in document.iter(qn(W, "t")))
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        return {
            "status": "FAIL",
            "findings": [{"code": "INVALID_DOCX", "message": str(exc)}],
        }

    project_name = str(get_path(case, "project.name", "") or "")
    if project_name and project_name not in text:
        findings.append(
            {
                "code": "PROJECT_NAME_MISSING",
                "message": f"未找到项目名称：{project_name}",
            }
        )
    if (
        case.get("profile") == "miit"
        and project_name != "高技术船舶科研专项"
        and "高技术船舶科研专项" in text
    ):
        findings.append(
            {
                "code": "LEAKED_EXAMPLE_PROJECT_NAME",
                "message": "报告错误包含示例项目名“高技术船舶科研专项”",
            }
        )
    for section in selected_sections(case):
        if section["heading"] not in text:
            findings.append(
                {
                    "code": "SECTION_MISSING",
                    "message": f"缺少栏目：{section['heading']}",
                }
            )
    if re.search(r"(?:\bX{2,}\b|待补充|\[待补充|TBD|TO\s*DO)", text, re.IGNORECASE):
        findings.append({"code": "PLACEHOLDER_FOUND", "message": "DOCX 中存在占位符"})
    return {
        "status": "PASS" if not findings else "FAIL",
        "file": str(path),
        "size": path.stat().st_size,
        "selected_section_count": len(selected_sections(case)),
        "template_fidelity": fidelity,
        "findings": findings,
    }

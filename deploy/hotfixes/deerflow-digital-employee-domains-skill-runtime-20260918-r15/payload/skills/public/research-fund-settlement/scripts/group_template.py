"""Fill the audited group-company Word template from normalized case data."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from decimal import Decimal
from typing import Any

from lxml import etree
from report_model import get_path

_URI_SCHEME = "http" + "://"
W_NS = _URI_SCHEME + "schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NS = _URI_SCHEME + "www.w3.org/XML/1998/namespace"
W = f"{{{W_NS}}}"
NS = {"w": W_NS}


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
    try:
        paragraph = paragraphs[index]
    except IndexError as exc:
        raise ValueError(f"集团公司模板段落槽位不存在：{index}") from exc
    _set_text(paragraph, value)


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
            f"集团公司模板表格槽位不存在：table={table_index} row={row_index} cell={cell_index}"
        ) from exc
    _set_text(cell, value)


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


def _date_cn(value: Any) -> str:
    if not value:
        return ""
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return f"{parsed.year}年{parsed.month}月{parsed.day}日"


def _expense(case: dict[str, Any], code: str) -> dict[str, Any]:
    rows = get_path(case, "_derived.expenses", []) or case.get("expenses", [])
    return next((item for item in rows if item.get("code") == code), {})


def _source_summary(case: dict[str, Any], field: str = "approved") -> str:
    return "、".join(
        f"{source.get('name', '')}{_money(source.get(field), True)}"
        for source in get_path(case, "funding.sources", []) or []
    )


def _expense_sentence(item: dict[str, Any]) -> str:
    adjusted = item.get("adjusted_budget")
    if adjusted is None:
        adjusted = item.get("budget")
    sentence = (
        f"{item.get('name', '')}：批复概预算{_money(adjusted, True)}，"
        f"实际支出{_money(item.get('recognized'), True)}，"
        f"结余（负数为超支）{_money(item.get('balance'), True)}。"
    )
    reason = str(item.get("variance_reason") or "").strip()
    if reason:
        sentence += f"结余原因：{reason}。"
    return sentence


def _ensure_annual_rows(table: etree._Element, annual_count: int) -> None:
    rows = table.xpath("./w:tr", namespaces=NS)
    required = 3 + max(annual_count, 3)
    while len(rows) < required:
        table.append(deepcopy(rows[-1]))
        rows = table.xpath("./w:tr", namespaces=NS)


def _aggregate(
    expenses: list[dict[str, Any]], scope: str
) -> dict[str, Decimal]:
    selected = [
        item
        for item in expenses
        if item.get("include_in_total", True)
        and (
            item.get("scope") == scope
            or (scope == "direct" and item.get("scope") in (None, ""))
        )
    ]
    return {
        field: sum(Decimal(str(item.get(field) or 0)) for item in selected)
        for field in ("budget", "adjusted_budget", "recognized", "balance")
    }


def _select_unit_text(
    paragraph: etree._Element,
    lead_name: str,
    lead_kind: str,
    entity_label: str,
) -> str:
    text = "".join(paragraph.xpath(".//w:t/text()", namespaces=NS))
    combined = "中海油研究总院有限责任公司/中海石油（中国）有限公司北京研究中心"
    text = text.replace(combined, lead_name)
    text = text.replace(
        "研究总院/研究中心",
        "研究中心" if lead_kind == "research_center" else "研究总院",
    )
    text = text.replace("集团公司/有限公司", entity_label)
    if lead_kind == "research_center":
        text = text.replace("中海油研究总院有限责任公司", lead_name)
    else:
        text = text.replace("中海石油（中国）有限公司北京研究中心", lead_name)
    return text


def _fill_cover_and_narrative(
    case: dict[str, Any],
    paragraphs: list[etree._Element],
    mode: str,
) -> None:
    project = case.get("project", {})
    report = case.get("report", {})
    lead = get_path(case, "organizations.lead", {}) or {}
    totals = get_path(case, "_derived.totals", {}) or {}
    expenses = get_path(case, "_derived.expenses", []) or []
    annual = get_path(case, "funding.annual_appropriations", []) or []
    entity_mode = project.get("entity_mode")
    entity_label = "有限公司" if entity_mode == "limited_company" else "集团公司"
    owning_company = (
        "中国海洋石油有限公司"
        if entity_mode == "limited_company"
        else "中国海洋石油集团有限公司"
    )
    lead_name = str(lead.get("name") or "")
    lead_kind = str(
        lead.get("kind")
        or ("research_center" if "研究中心" in lead_name else "research_institute")
    )
    pre_marker = "" if mode == "final" else "(预)"

    _set_paragraph(paragraphs, 0, project.get("code"))
    _set_paragraph(paragraphs, 4, owning_company)
    _set_paragraph(paragraphs, 5, f"科技项目(课题)验收财务{pre_marker}决算报告")
    _set_paragraph(paragraphs, 17, f"项目(课题)名称：{project.get('name', '')}")
    _set_paragraph(paragraphs, 18, f"项 目 类 型：{entity_label}科技项目")
    _set_paragraph(paragraphs, 19, f"承 担 单 位：{lead_name}")
    _set_paragraph(
        paragraphs,
        20,
        f"起 止 时 间：{_date_cn(project.get('start_date'))} 至 {_date_cn(project.get('end_date'))}",
    )
    _set_paragraph(paragraphs, 22, f"报告编制人（签字）：{report.get('prepared_by', '')}")
    _set_paragraph(
        paragraphs,
        23,
        f"项目(课题)负责人（签字）：{project.get('responsible_person', '')}",
    )
    _set_paragraph(
        paragraphs,
        24,
        f"承担单位财务部门负责人（签字）：{report.get('reviewed_by', '')}",
    )
    _set_paragraph(paragraphs, 26, _date_cn(report.get("prepared_date")))
    _set_paragraph(
        paragraphs,
        53,
        f"本科技项目(课题)财务{pre_marker}决算报告是严格依照{entity_label}财务规章制度由财务部门编制和出具。报告编制人、项目（课题）负责人、财务部门负责人保证本{pre_marker}决算报告各项内容真实、客观，并承担由此引起的相关责任。",
    )
    _set_paragraph(
        paragraphs,
        68,
        f"第一部分\t{entity_label}科技项目(课题)财务{pre_marker}决算说明",
    )
    _set_paragraph(
        paragraphs,
        72,
        f"根据项目任务合同书，本项目批复概预算总额{_money(get_path(case, 'funding.approved_total'), True)}，其中{_source_summary(case)}。批复概预算与任务合同书金额一致。",
    )

    adjustments = get_path(case, "funding.adjustments", {}) or {}
    items = adjustments.get("items") or []
    if adjustments.get("none_declared"):
        _set_paragraph(paragraphs, 76, "经核实，项目执行期间未发生概预算变更或调剂。")
        for index in range(77, 85):
            _set_paragraph(paragraphs, index, "")
    elif items:
        major = [
            item
            for item in items
            if str(item.get("level") or "").lower() in {"major", "重大变更"}
        ]
        general = [item for item in items if item not in major]

        def combine(group: list[dict[str, Any]], field: str) -> str:
            return "；".join(str(item.get(field) or "") for item in group if item.get(field))

        for heading_index, group, rows in [
            (
                77,
                major,
                [
                    (78, "调整内容", "content"),
                    (79, "调整理由", "reason"),
                    (80, "调整批复情况", "approval"),
                ],
            ),
            (
                81,
                general,
                [
                    (82, "调整内容", "content"),
                    (83, "调整理由", "reason"),
                    (84, "调整批复情况", "approval"),
                ],
            ),
        ]:
            if not group:
                for index in [heading_index, *(row[0] for row in rows)]:
                    _set_paragraph(paragraphs, index, "")
                continue
            for index, label, field in rows:
                _set_paragraph(paragraphs, index, f"{label}：{combine(group, field)}。")
    else:
        for index in range(76, 85):
            _set_paragraph(paragraphs, index, "")

    annual_text = "、".join(
        f"{item.get('year')}年下达{_money(item.get('amount'), True)}" for item in annual
    )
    _set_paragraph(
        paragraphs,
        92,
        f"{entity_label}分年实际下达预算总额{_money(get_path(case, 'funding.approved_total'), True)}；{annual_text}。",
    )
    included = [item for item in expenses if item.get("include_in_total", True)]
    direct_spent = sum(
        Decimal(str(item.get("recognized") or 0))
        for item in included
        if item.get("scope") != "indirect"
    )
    indirect_spent = sum(
        Decimal(str(item.get("recognized") or 0))
        for item in included
        if item.get("scope") == "indirect"
    )
    budget_execution_summary = (
        f"截至{_date_cn(report.get('cutoff_date'))}，本项目任务合同书批复概预算总额"
        f"{_money(get_path(case, 'funding.approved_total'), True)}，经费实际支出"
        f"{_money(totals.get('recognized'), True)}，预算执行率"
        f"{_percent(totals.get('execution_rate'))}。其中：直接经费支出"
        f"{_money(direct_spent, True)}，间接经费支出"
        f"{_money(indirect_spent, True)}。"
    )
    _set_paragraph(
        paragraphs,
        97,
        budget_execution_summary,
    )
    sources = get_path(case, "funding.sources", []) or []
    _set_paragraph(
        paragraphs,
        98,
        f"各来源资金执行情况：{_source_summary(case, 'spent')}。"
        if any(source.get("spent") is not None for source in sources)
        else "",
    )
    for index, code in {
        101: "equipment_software",
        102: "materials_energy",
        103: "external_testing",
        104: "commissioned_research",
        106: "publication_ip",
        107: "travel_meeting_cooperation",
        108: "consulting_labor",
        109: "other",
    }.items():
        _set_paragraph(paragraphs, index, _expense_sentence(_expense(case, code)))
    commissioned = _expense(case, "commissioned_research")
    ratio = (
        Decimal(str(commissioned.get("recognized") or 0)) / direct_spent * 100
        if direct_spent
        else Decimal("0")
    )
    _set_paragraph(
        paragraphs,
        105,
        f"外委研究费占直接经费总支出的比例为{_percent(ratio)}。",
    )
    _set_paragraph(
        paragraphs,
        113,
        f"截至决算报告编制日，本项目概预算结余共计{_money(totals.get('balance'), True)}，各项预算结余情况说明如下：",
    )
    for index, code in {
        114: "equipment_software",
        115: "materials_energy",
        116: "external_testing",
        117: "commissioned_research",
        118: "publication_ip",
        119: "travel_meeting_cooperation",
        120: "consulting_labor",
        121: "other",
    }.items():
        item = _expense(case, code)
        _set_paragraph(
            paragraphs,
            index,
            f"{item.get('name', '')}：结余（负数为超支）{_money(item.get('balance'), True)}，结余比例{_percent(item.get('variance_rate'))}。结余主要原因：{item.get('variance_reason') or ''}",
        )
    _set_paragraph(
        paragraphs,
        126,
        _select_unit_text(paragraphs[126], lead_name, lead_kind, entity_label),
    )
    _set_paragraph(
        paragraphs,
        130,
        _select_unit_text(paragraphs[130], lead_name, lead_kind, entity_label),
    )
    _set_paragraph(paragraphs, 133, "无")
    _set_paragraph(
        paragraphs,
        146,
        f"第二部分\t科技项目(课题)财务{pre_marker}决算汇总表",
    )
    _set_paragraph(
        paragraphs,
        151,
        f"第三部分\t科技项目(课题)分科目支出{pre_marker}决算科目明细",
    )


def _fill_summary_tables(
    case: dict[str, Any], tables: list[etree._Element]
) -> None:
    totals = get_path(case, "_derived.totals", {}) or {}
    expenses = get_path(case, "_derived.expenses", []) or []
    annual = get_path(case, "funding.annual_appropriations", []) or []
    _ensure_annual_rows(tables[1], len(annual))
    entity_label = (
        "有限公司"
        if get_path(case, "project.entity_mode") == "limited_company"
        else "集团公司"
    )
    expense_rows = {
        2: "equipment_software",
        3: "materials_energy",
        4: "external_testing",
        5: "testing_processing",
        6: "testing_trials",
        7: "commissioned_research",
        8: "publication_ip",
        9: "travel_meeting_cooperation",
        10: "consulting_labor",
        11: "other",
        13: "research_staff",
        14: "engineering_support",
    }
    direct = _aggregate(expenses, "direct")
    indirect = _aggregate(expenses, "indirect")
    table_rows: list[tuple[int, dict[str, Any]]] = [(1, direct), (12, indirect)] + [
        (row, _expense(case, code)) for row, code in expense_rows.items()
    ]
    for row, item in table_rows:
        before = item.get("budget")
        after = item.get("adjusted_budget")
        delta = Decimal(str(after or 0)) - Decimal(str(before or 0))
        _set_cell(tables, 0, row, 1, _money(before))
        _set_cell(tables, 0, row, 2, _money(delta))
        _set_cell(tables, 0, row, 3, _money(after))

    sources = get_path(case, "funding.sources", []) or []
    company = next((item for item in sources if item.get("kind") in {"company", "group"}), {})
    self_funded = next((item for item in sources if item.get("kind") == "self_funded"), {})
    other = next((item for item in sources if item.get("kind") == "other"), {})
    _set_cell(tables, 1, 1, 1, f"{entity_label}资金")
    for column, source in [(1, company), (2, self_funded), (3, other)]:
        _set_cell(tables, 1, 2, column, _money(source.get("approved")))
    _set_cell(tables, 1, 2, 4, _money(get_path(case, "funding.approved_total")))
    _set_cell(tables, 1, 2, 6, _money(totals.get("recognized")))
    rows = tables[1].xpath("./w:tr", namespaces=NS)
    for row in range(3, len(rows)):
        for column in range(7):
            _set_cell(tables, 1, row, column, "")
    for row, item in enumerate(annual, 3):
        year = item.get("year")
        split = item.get("sources") or {}
        _set_cell(tables, 1, row, 0, f"{year}年批准额")
        for column, key in [(1, "company"), (2, "self_funded"), (3, "other")]:
            _set_cell(tables, 1, row, column, _money(split.get(key)))
        _set_cell(tables, 1, row, 4, _money(item.get("amount")))
        _set_cell(tables, 1, row, 5, f"{year}年支出额")
        _set_cell(tables, 1, row, 6, _money(item.get("actual_spent")))

    for row, item in table_rows:
        for column, field in [
            (1, "budget"),
            (2, "adjusted_budget"),
            (3, "recognized"),
            (4, "balance"),
        ]:
            _set_cell(tables, 2, row, column, _money(item.get(field)))
        _set_cell(tables, 2, row, 5, "")


def _fill_detail_totals(case: dict[str, Any], tables: list[etree._Element]) -> None:
    mappings = [
        (8, 3, "testing_processing", (1, 2, 3)),
        (9, 3, "testing_trials", (1, 2, 3)),
        (11, 4, "commissioned_research", (4, 5, 6)),
        (12, 5, "publication_ip", (1, 2, 3)),
        (13, 4, "travel_meeting_cooperation", (1, 2, 3)),
        (15, 3, "consulting_labor", (1, 2, 3)),
        (16, 7, "other", (1, 2, 3)),
    ]
    for table_index, row_index, code, columns in mappings:
        item = _expense(case, code)
        for column, field in zip(columns, ("adjusted_budget", "recognized", "balance")):
            _set_cell(tables, table_index, row_index, column, _money(item.get(field)))
    engineering = _expense(case, "engineering_support")
    _set_cell(tables, 17, 7, 2, _money(engineering.get("adjusted_budget")))
    _set_cell(tables, 17, 7, 3, _money(engineering.get("recognized")))


def fill_group_template(
    case: dict[str, Any],
    paragraphs: list[etree._Element],
    tables: list[etree._Element],
    mode: str,
) -> None:
    _fill_cover_and_narrative(case, paragraphs, mode)
    _fill_summary_tables(case, tables)
    _fill_detail_totals(case, tables)

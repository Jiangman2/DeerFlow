"""Deterministic data model and validation for research fund settlements.

The module intentionally uses only the Python standard library so a DeerFlow
sandbox can run the skill without installing packages during a conversation.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable


SKILL_ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = SKILL_ROOT / "references" / "profile-rules.json"
TOLERANCE = Decimal("0.01")
PERCENT_QUANT = Decimal("0.01")
PLACEHOLDER_RE = re.compile(r"(?:\bX{2,}\b|待补充|\[待补充|TBD|TO\s*DO)", re.IGNORECASE)
UNIT_TO_YUAN = {
    "元": Decimal("1"),
    "千元": Decimal("1000"),
    "万元": Decimal("10000"),
    "亿元": Decimal("100000000"),
}


PROFILE_ALIASES = {
    "group_company": "group_company",
    "集团公司（有限公司）项目": "group_company",
    "集团公司项目": "group_company",
    "有限公司项目": "group_company",
    "集团级项目": "group_company",
    "national_science": "national_science",
    "国家科技项目": "national_science",
    "国家重点研发计划": "national_science",
    "中央财政科技项目": "national_science",
    "miit": "miit",
    "国家工信部项目": "miit",
    "工信部项目": "miit",
    "工业和信息化部项目": "miit",
}


EXPENSE_CATALOGS = {
    "group_company": [
        ("equipment_software", "设备与软件费"),
        ("materials_energy", "材料/燃料动力费"),
        ("external_testing", "外协实验/试验费"),
        ("testing_processing", "测试化验加工费"),
        ("testing_trials", "试验费"),
        ("commissioned_research", "外委研究费"),
        ("publication_ip", "出版/文献/信息传播/知识产权事务费"),
        ("travel_meeting_cooperation", "会议/差旅/国际合作交流费"),
        ("consulting_labor", "专家咨询评审/劳务费"),
        ("other", "其他费用"),
        ("research_staff", "研发人员费"),
        ("engineering_support", "依托工程费"),
    ],
    "national_science": [
        ("equipment", "设备费"),
        ("materials", "材料费"),
        ("testing_processing", "测试化验加工费"),
        ("fuel_power", "燃料动力费"),
        ("travel_meeting_cooperation", "差旅/会议/国际合作交流费"),
        ("publication_ip", "出版/文献/信息传播/知识产权事务费"),
        ("labor", "劳务费"),
        ("expert_consulting", "专家咨询费"),
        ("indirect", "间接费用"),
    ],
    "miit": [
        ("materials", "材料费"),
        ("special", "专用费"),
        ("outsourcing", "外协费"),
        ("fuel_power", "燃料动力费"),
        ("business", "事务费"),
        ("depreciation", "固定资产折旧费"),
        ("management", "管理费"),
        ("wages_labor", "工资及劳务费"),
        ("contingency", "不可预见费"),
        ("expected_income", "项目预计收益"),
    ],
}


GROUP_EXPENSE_ATTRIBUTES = {
    "equipment_software": {"scope": "direct", "include_in_total": True},
    "materials_energy": {"scope": "direct", "include_in_total": True},
    "external_testing": {"scope": "direct", "include_in_total": True},
    "testing_processing": {
        "scope": "direct",
        "parent_code": "external_testing",
        "include_in_total": False,
    },
    "testing_trials": {
        "scope": "direct",
        "parent_code": "external_testing",
        "include_in_total": False,
    },
    "commissioned_research": {"scope": "direct", "include_in_total": True},
    "publication_ip": {"scope": "direct", "include_in_total": True},
    "travel_meeting_cooperation": {"scope": "direct", "include_in_total": True},
    "consulting_labor": {"scope": "direct", "include_in_total": True},
    "other": {"scope": "direct", "include_in_total": True},
    "research_staff": {"scope": "indirect", "include_in_total": True},
    "engineering_support": {"scope": "indirect", "include_in_total": True},
}


SECTION_FACT_REQUIREMENTS: dict[str, list[list[str]]] = {
    "group.budget_approval": [["funding.approved_total"], ["funding.sources"]],
    "group.budget_changes": [
        ["funding.adjustments.none_declared", "funding.adjustments.items"]
    ],
    "group.annual_approvals": [["funding.annual_appropriations"]],
    "group.overall_settlement": [["report.prepared_date"], ["expenses"]],
    "group.expense_breakdown": [["expenses"]],
    "group.balance_reasons": [["expenses"]],
    "group.management_basis": [["organizations.lead.name"]],
    "group.management_status": [["organizations.lead.name"]],
    "group.finance_opinion": [["organizations.lead.name"]],
    "national.entity_overview": [
        ["organizations.lead.name"],
        ["organizations.lead.registered_address"],
    ],
    "national.project_implementation": [
        ["project.topic_name", "project.name"],
        ["project.code"],
        ["project.start_date"],
        ["project.end_date"],
    ],
    "national.accounting": [
        ["management.accounting_system"],
        ["management.cost_accounting_summary"],
    ],
    "national.internal_controls": [
        ["management.policies"],
        [
            "management.internal_control_summary",
            "narratives.national.internal_controls",
        ],
    ],
    "national.budget_adjustments": [
        ["funding.adjustments.none_declared", "funding.adjustments.items"]
    ],
    "national.funds_received": [["funding.sources"]],
    "national.funds_transferred": [
        ["funding.transfers", "management.no_transfers_reason"]
    ],
    "national.fund_usage": [["expenses"]],
    "national.payables_forecast": [["audit.payables"], ["audit.expected_expenditure"]],
    "miit.lead_overview": [
        ["organizations.lead.name"],
        ["organizations.lead.registered_address"],
        ["organizations.lead.relevant_capability"],
    ],
    "miit.participants_overview": [
        ["organizations.participants", "organizations.no_participants_reason"]
    ],
    "miit.governance": [["project.governance_summary", "narratives.miit.governance"]],
    "miit.completion": [["project.completion_summary", "narratives.miit.completion"]],
    "miit.approval": [
        ["project.approval_document.title"],
        ["project.approval_document.number"],
        ["project.approval_document.date"],
    ],
    "miit.internal_controls": [
        ["management.internal_control_summary", "narratives.miit.internal_controls"]
    ],
    "miit.procurement_contracts": [
        ["procurement.summary", "narratives.miit.procurement_contracts"]
    ],
    "miit.tendering": [["procurement.tendering_summary", "narratives.miit.tendering"]],
    "miit.accounting_policy": [
        ["management.accounting_policy", "narratives.miit.accounting_policy"]
    ],
    "miit.cost_accounting": [
        ["management.cost_accounting_summary", "narratives.miit.cost_accounting"]
    ],
    "miit.funding_arrival": [["funding.sources"]],
    "miit.fund_management": [["funding.transfers", "management.no_transfers_reason"]],
    "miit.audit_confirmation": [
        ["audit.reported_expenditure"],
        ["audit.recognized_expenditure"],
        ["expenses"],
    ],
    "miit.variance": [["expenses"]],
    "miit.period_outcomes": [
        ["project.actual_start_date", "project.start_date"],
        ["project.actual_end_date", "project.end_date"],
        ["outcomes", "assets"],
    ],
}


def load_rules() -> dict[str, Any]:
    with RULES_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def normalize_profile(value: Any) -> str:
    key = str(value or "").strip()
    if key in PROFILE_ALIASES:
        return PROFILE_ALIASES[key]
    raise ValueError(f"不支持的项目类型: {value!r}")


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("布尔值不能作为金额")
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"无法解析数值: {value!r}") from exc


def json_number(value: Decimal | None) -> int | float | None:
    if value is None:
        return None
    quantized = value.quantize(TOLERANCE, rounding=ROUND_HALF_UP)
    if quantized == quantized.to_integral():
        return int(quantized)
    return float(quantized)


def convert_money(value: Any, from_unit: str, to_unit: str = "万元") -> int | float:
    if from_unit not in UNIT_TO_YUAN:
        raise ValueError(f"不支持的原始金额单位: {from_unit}")
    if to_unit not in UNIT_TO_YUAN:
        raise ValueError(f"不支持的目标金额单位: {to_unit}")
    number = decimal_or_none(value)
    if number is None:
        raise ValueError("换算金额不能为空")
    converted = number * UNIT_TO_YUAN[from_unit] / UNIT_TO_YUAN[to_unit]
    result = json_number(converted)
    assert result is not None
    return result


def sum_present(values: Iterable[Any]) -> Decimal:
    total = Decimal("0")
    for value in values:
        number = decimal_or_none(value)
        if number is not None:
            total += number
    return total


def get_path(data: dict[str, Any], dotted: str, default: Any = None) -> Any:
    if dotted.startswith("narratives."):
        narrative_key = dotted[len("narratives.") :]
        narratives = data.get("narratives")
        if isinstance(narratives, dict):
            return narratives.get(narrative_key, default)
    current: Any = data
    for token in dotted.split("."):
        if not isinstance(current, dict) or token not in current:
            return default
        current = current[token]
    return current


def has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def selected_sections(case: dict[str, Any]) -> list[dict[str, Any]]:
    rules = load_rules()["profiles"][case["profile"]]
    selected = case.get("selected_sections") or []
    if not selected:
        return list(rules["sections"])
    selected_set = set(selected)
    return [item for item in rules["sections"] if item["id"] in selected_set]


def initial_case(profile: str) -> dict[str, Any]:
    profile = normalize_profile(profile)
    expenses = []
    for code, name in EXPENSE_CATALOGS[profile]:
        item = {
            "code": code,
            "name": name,
            "budget": None,
            "adjusted_budget": None,
            "reported": None,
            "actual": None,
            "audited": None,
            "description": None,
            "variance_reason": None,
            "evidence_refs": [],
        }
        if profile == "group_company":
            item.update(GROUP_EXPENSE_ATTRIBUTES.get(code, {}))
        expenses.append(item)
    entity_mode = None
    tax_basis = "not_applicable"
    if profile == "group_company":
        tax_basis = None
    return {
        "schema_version": "1.0",
        "profile": profile,
        "report": {
            "cutoff_date": None,
            "prepared_date": None,
            "prepared_by": None,
            "reviewed_by": None,
            "status": "draft",
        },
        "project": {
            "name": None,
            "topic_name": None,
            "code": None,
            "plan_name": None,
            "entity_mode": entity_mode,
            "responsible_person": None,
            "researchers": [],
            "start_date": None,
            "end_date": None,
            "actual_start_date": None,
            "actual_end_date": None,
            "acceptance_date": None,
            "research_content": [],
            "governance_summary": None,
            "completion_summary": None,
            "approval_document": {"title": None, "number": None, "date": None},
        },
        "organizations": {
            "lead": {
                "name": None,
                "short_name": None,
                "affiliation": None,
                "established_date": None,
                "unified_social_credit_code": None,
                "registered_capital": None,
                "legal_representative": None,
                "registered_address": None,
                "business_scope": None,
                "relevant_capability": None,
            },
            "participants": [],
            "no_participants_reason": None,
        },
        "funding": {
            "currency": "CNY",
            "unit": "万元",
            "tax_basis": tax_basis,
            "expense_scope": "all_funds",
            "approved_total": None,
            "adjusted_total": None,
            "sources": [],
            "annual_appropriations": [],
            "adjustments": {"none_declared": None, "items": []},
            "transfers": [],
        },
        "expenses": expenses,
        "management": {
            "accounting_standard": None,
            "accounting_system": None,
            "cost_accounting_summary": None,
            "accounting_policy": None,
            "internal_control_summary": None,
            "management_summary": None,
            "finance_department_opinion": None,
            "no_transfers_reason": None,
            "policies": [],
        },
        "procurement": {
            "summary": None,
            "contracts": [],
            "tendering_summary": None,
            "tenders": [],
        },
        "audit": {
            "audit_org": None,
            "report_no": None,
            "reported_expenditure": None,
            "audit_reduction": None,
            "recognized_expenditure": None,
            "payables": None,
            "expected_expenditure": None,
            "opinion": None,
        },
        "assets": [],
        "outcomes": [],
        "narratives": {},
        "selected_sections": [],
        "evidence": [],
        "conflicts": [],
        "assumptions": [],
    }


def derive(case: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(case)
    normalized["profile"] = normalize_profile(normalized.get("profile"))
    expenses = normalized.get("expenses") or []

    expense_rows: list[dict[str, Any]] = []
    for item in expenses:
        budget = decimal_or_none(item.get("budget"))
        adjusted = decimal_or_none(item.get("adjusted_budget"))
        if adjusted is None:
            adjusted = budget
        actual = decimal_or_none(item.get("actual"))
        reported = decimal_or_none(item.get("reported"))
        if reported is None:
            reported = actual
        audited = decimal_or_none(item.get("audited"))
        recognized = audited if audited is not None else actual
        if recognized is None:
            recognized = reported
        balance = (
            adjusted - recognized
            if adjusted is not None and recognized is not None
            else None
        )
        variance_rate = None
        execution_rate = None
        if adjusted not in (None, Decimal("0")) and recognized is not None:
            variance_rate = (balance / adjusted * Decimal("100")).quantize(
                PERCENT_QUANT, rounding=ROUND_HALF_UP
            )
            execution_rate = (recognized / adjusted * Decimal("100")).quantize(
                PERCENT_QUANT, rounding=ROUND_HALF_UP
            )
        expense_rows.append(
            {
                "code": item.get("code"),
                "name": item.get("name"),
                "budget": json_number(budget),
                "adjusted_budget": json_number(adjusted),
                "reported": json_number(reported),
                "actual": json_number(actual),
                "audited": json_number(audited),
                "recognized": json_number(recognized),
                "balance": json_number(balance),
                "variance_rate": json_number(variance_rate),
                "execution_rate": json_number(execution_rate),
                "description": item.get("description"),
                "variance_reason": item.get("variance_reason"),
                "evidence_refs": item.get("evidence_refs") or [],
                "scope": item.get("scope"),
                "parent_code": item.get("parent_code"),
                "include_in_total": item.get("include_in_total", True),
                "input_row": item.get("input_row"),
                "input_balance": item.get("input_balance"),
            }
        )

    sources = normalized.get("funding", {}).get("sources") or []
    approved_sources = sum_present(item.get("approved") for item in sources)
    received_sources = sum_present(item.get("received") for item in sources)
    transfers = normalized.get("funding", {}).get("transfers") or []
    transferred_total = sum_present(item.get("amount") for item in transfers)

    included_expenses = [
        row for row in expense_rows if row.get("include_in_total", True)
    ]
    execution_scope = (
        _group_budget_execution_scope(normalized)
        if normalized["profile"] == "group_company" else "all_funds"
    )
    execution_expenses = (
        [row for row in included_expenses if row.get("scope") != "indirect"]
        if execution_scope == "direct_only" else included_expenses
    )
    adjusted_total = sum_present(
        row.get("adjusted_budget") for row in execution_expenses
    )
    reported_total = sum_present(row.get("reported") for row in execution_expenses)
    actual_total = sum_present(row.get("actual") for row in execution_expenses)
    audited_values = [row.get("audited") for row in execution_expenses]
    audited_total = (
        sum_present(audited_values)
        if any(value is not None for value in audited_values)
        else None
    )
    recognized_total = sum_present(row.get("recognized") for row in execution_expenses)
    audit_reduction = (
        reported_total - audited_total if audited_total is not None else None
    )

    expense_scope = normalized.get("funding", {}).get("expense_scope") or "all_funds"
    if expense_scope == "all_funds":
        target_budget = decimal_or_none(
            normalized.get("funding", {}).get("adjusted_total")
        )
        if target_budget is None:
            target_budget = decimal_or_none(
                normalized.get("funding", {}).get("approved_total")
            )
    else:
        scoped = next(
            (item for item in sources if item.get("name") == expense_scope), None
        )
        target_budget = decimal_or_none(scoped.get("approved")) if scoped else None

    overall_balance = (
        target_budget - recognized_total if target_budget is not None else None
    )
    overall_execution_rate = None
    if target_budget not in (None, Decimal("0")):
        overall_execution_rate = (
            recognized_total / target_budget * Decimal("100")
        ).quantize(PERCENT_QUANT, rounding=ROUND_HALF_UP)

    normalized["_derived"] = {
        "expenses": expense_rows,
        "totals": {
            "source_approved": json_number(approved_sources),
            "source_received": json_number(received_sources),
            "transferred": json_number(transferred_total),
            "expense_budget": json_number(adjusted_total),
            "reported": json_number(reported_total),
            "actual": json_number(actual_total),
            "audited": json_number(audited_total),
            "recognized": json_number(recognized_total),
            "audit_reduction": json_number(audit_reduction),
            "target_budget": json_number(target_budget),
            "balance": json_number(overall_balance),
            "execution_rate": json_number(overall_execution_rate),
        },
    }
    if normalized["profile"] == "group_company":
        normalized["_derived"]["budget_execution_scope"] = execution_scope
        normalized["_derived"]["cost_totals"] = {
            "direct": _cost_totals([row for row in included_expenses if row.get("scope") != "indirect"]),
            "indirect": _cost_totals([row for row in included_expenses if row.get("scope") == "indirect"]),
            "all": _cost_totals(included_expenses),
        }
        normalized["_derived"]["outsourcing"] = _derive_outsourcing(normalized)
    return normalized


def _group_budget_execution_scope(case: dict[str, Any]) -> str:
    contract = case.get("input_contract") or {}
    scope = contract.get("budget_execution_scope")
    if contract.get("version") == "2026-11-05" and scope != "direct_only":
        raise ValueError("2026-11-05标准输入必须明确 budget_execution_scope=direct_only，请从原表重新导入")
    if scope not in (None, "direct_only"):
        raise ValueError("不支持的集团公司预算执行口径")
    # Preserve the interpretation of archived reports until their source is reimported.
    return scope or "all_funds"


def _cost_totals(rows: list[dict[str, Any]]) -> dict[str, int | float | None]:
    amounts = {field: sum_present(row.get(field) for row in rows)
               for field in ("budget", "adjusted_budget", "reported", "actual", "recognized")}
    audited = [row.get("audited") for row in rows]
    amounts["audited"] = sum_present(audited) if any(value is not None for value in audited) else None
    amounts["balance"] = amounts["adjusted_budget"] - amounts["recognized"]
    return {field: json_number(value) for field, value in amounts.items()}


def _outsourcing_number(value: Any) -> Decimal | None:
    number = decimal_or_none(value)
    if number is not None and not number.is_finite():
        raise ValueError("外委比例的金额和比例必须为有限数值")
    return number


def _derive_outsourcing(case: dict[str, Any]) -> dict[str, Any]:
    """Use the same recognized expenditure basis as the report, not entered ratios."""
    rows = get_path(case, "_derived.expenses", [])
    commissioned = next((row for row in rows if row.get("code") == "commissioned_research"), {})
    total = _outsourcing_number(commissioned.get("recognized"))
    direct = sum_present(row.get("recognized") for row in rows
                         if row.get("include_in_total", True) and row.get("scope") != "indirect")
    supplied = case.get("funding", {}).get("outsourcing")
    if supplied is not None and not isinstance(supplied, dict):
        raise ValueError("funding.outsourcing 必须是对象")
    supplied = supplied or {}
    # Parse all supplied numbers before validation so non-finite values fail closed.
    _outsourcing_number(supplied.get("total"))
    _outsourcing_number(supplied.get("external_share"))
    external = _outsourcing_number(supplied.get("external_amount"))

    def percent(numerator: Decimal | None, denominator: Decimal | None) -> int | float | None:
        if numerator is None or denominator is None or denominator <= 0:
            return None
        return json_number(numerator / denominator * Decimal("100"))

    return {
        "basis": "recognized_expenditure",
        "direct_expenditure": json_number(direct),
        "total": json_number(total),
        "external_amount": json_number(external),
        "commissioned_to_direct_percent": percent(total, direct),
        "external_to_direct_percent": percent(external, direct),
        "external_to_commissioned_percent": percent(external, total),
        "external_organizations": copy.deepcopy(supplied.get("external_organizations", [])),
        "evidence_refs": copy.deepcopy(supplied.get("evidence_refs", [])),
    }


def _issue(
    severity: str, code: str, path: str, message: str, **details: Any
) -> dict[str, Any]:
    result = {"severity": severity, "code": code, "path": path, "message": message}
    result.update(details)
    return result


def _severity(mode: str) -> str:
    return "ERROR" if mode == "final" else "WARNING"


def _validate_outsourcing(case: dict[str, Any], known_evidence: set[str], mode: str) -> list[dict[str, Any]]:
    """Appendix 4 supplements expense facts; it cannot override their amounts."""
    supplied = get_path(case, "funding.outsourcing") or {}
    derived = get_path(case, "_derived.outsourcing", {})
    path = "funding.outsourcing"
    issues = []
    total = _outsourcing_number(supplied.get("total"))
    external = _outsourcing_number(supplied.get("external_amount"))
    share = _outsourcing_number(supplied.get("external_share"))
    recognized = _outsourcing_number(derived.get("total"))
    direct = _outsourcing_number(derived.get("direct_expenditure"))
    for field, amount in (("total", total), ("external_amount", external)):
        if amount is None:
            issues.append(_issue(_severity(mode), "OUTSOURCING_AMOUNT_REQUIRED", f"{path}.{field}", "附录4缺少金额，不可默认为零"))
        elif amount < 0:
            issues.append(_issue("ERROR", "OUTSOURCING_NEGATIVE_AMOUNT", f"{path}.{field}", "附录4金额不得为负数"))
    if recognized is None:
        issues.append(_issue(_severity(mode), "OUTSOURCING_EXPENSE_REQUIRED", path, "缺少可核对的外委研究费支出"))
    elif total is not None and abs(total - recognized) > TOLERANCE:
        issues.append(_issue("ERROR", "OUTSOURCING_TOTAL_MISMATCH", f"{path}.total", "附录4支出总额与报告外委研究费支出口径不一致", expected=json_number(recognized), actual=json_number(total)))
    if external is not None and total is not None and external > total:
        issues.append(_issue("ERROR", "OUTSOURCING_EXTERNAL_EXCEEDS_TOTAL", f"{path}.external_amount", "支付系统外单位金额不得超过外委研究费支出总额"))
    if direct is not None and (direct < 0 or (recognized is not None and recognized > direct)):
        issues.append(_issue("ERROR", "OUTSOURCING_DIRECT_TOTAL_INVALID", path, "直接经费支出不得为负数或小于外委研究费支出"))
    if share is not None:
        if share < 0 or share > 1:
            issues.append(_issue("ERROR", "OUTSOURCING_SHARE_RANGE", f"{path}.external_share", "系统外单位占比应为0至1的小数"))
        elif total is not None and external is not None:
            expected = external / total if total > 0 else Decimal("0")
            # Half of one displayed 0.01 percentage point permits normal rounding.
            if abs(share - expected) > Decimal("0.00005"):
                issues.append(_issue("ERROR", "OUTSOURCING_SHARE_MISMATCH", f"{path}.external_share", "系统外单位占比与系统外金额/外委总额不一致"))
    organizations = supplied.get("external_organizations", [])
    if not isinstance(organizations, list) or len(organizations) > 3 or any(
        not isinstance(name, str) or not name.strip() for name in organizations
    ):
        issues.append(_issue("ERROR", "OUTSOURCING_ORGANIZATIONS_INVALID", f"{path}.external_organizations", "系统外单位名称应为最多3个非空文本"))
    elif external is not None and external > 0 and not organizations:
        issues.append(_issue(_severity(mode), "OUTSOURCING_ORGANIZATIONS_REQUIRED", f"{path}.external_organizations", "支付系统外单位金额大于零，必须提供真实单位名称"))
    refs = supplied.get("evidence_refs", [])
    if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
        issues.append(_issue("ERROR", "OUTSOURCING_EVIDENCE_INVALID", f"{path}.evidence_refs", "外委比例证据定位必须是文本列表"))
    elif not refs:
        issues.append(_issue(_severity(mode), "MISSING_EVIDENCE", f"{path}.evidence_refs", "外委比例信息缺少证据定位"))
    else:
        for ref in refs:
            if ref not in known_evidence:
                issues.append(_issue("ERROR", "UNKNOWN_EVIDENCE", f"{path}.evidence_refs", f"证据 ID 不存在：{ref}"))
    return issues


def _strings(value: Any, path: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key, nested in value.items():
            if key == "_derived":
                continue
            child = f"{path}.{key}" if path else key
            yield from _strings(nested, child)
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            child = f"{path}[{index}]"
            yield from _strings(nested, child)


def validate(
    case: dict[str, Any], mode: str = "draft"
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if mode not in {"draft", "final"}:
        raise ValueError("mode 必须是 draft 或 final")
    issues: list[dict[str, Any]] = []
    try:
        normalized = derive(case)
    except (ValueError, TypeError) as exc:
        return copy.deepcopy(case), [_issue("ERROR", "INVALID_DATA", "$", str(exc))]

    profile = normalized["profile"]
    rules = load_rules()["profiles"][profile]
    allowed_sections = {item["id"] for item in rules["sections"]}
    requested = normalized.get("selected_sections") or []
    invalid_sections = sorted(set(requested) - allowed_sections)
    for section_id in invalid_sections:
        issues.append(
            _issue(
                "ERROR",
                "INVALID_SECTION",
                "selected_sections",
                f"栏目 {section_id} 不属于 {rules['display_name']}",
            )
        )

    common_required = [
        "report.prepared_date",
        "project.name",
        "organizations.lead.name",
        "funding.approved_total",
        "funding.sources",
        "expenses",
    ]
    if profile != "group_company":
        common_required.insert(0, "report.cutoff_date")
    elif has_value(get_path(normalized, "report.cutoff_date")):
        try:
            date.fromisoformat(str(get_path(normalized, "report.cutoff_date")))
        except ValueError:
            issues.append(_issue("ERROR", "INVALID_DATE", "report.cutoff_date", "截止日期必须使用 YYYY-MM-DD"))
    for dotted in common_required:
        if not has_value(get_path(normalized, dotted)):
            issues.append(
                _issue(
                    _severity(mode),
                    "MISSING_REQUIRED",
                    dotted,
                    f"缺少正式报告所需字段：{dotted}",
                )
            )

    for section in selected_sections(normalized):
        for alternatives in SECTION_FACT_REQUIREMENTS.get(section["id"], []):
            if not any(has_value(get_path(normalized, path)) for path in alternatives):
                issues.append(
                    _issue(
                        "INFO" if section["id"] == "group.budget_changes" else _severity(mode),
                        "MISSING_SECTION_FACT",
                        section["id"],
                        f"栏目“{section['heading']}”缺少事实：{' 或 '.join(alternatives)}",
                    )
                )

    funding = normalized.get("funding", {})
    if funding.get("unit") != "万元":
        issues.append(
            _issue("ERROR", "INVALID_UNIT", "funding.unit", "金额单位必须归一化为万元")
        )
    if funding.get("currency") != "CNY":
        issues.append(
            _issue(
                "ERROR",
                "INVALID_CURRENCY",
                "funding.currency",
                "当前版本只支持人民币 CNY",
            )
        )

    if profile == "group_company":
        input_contract = normalized.get("input_contract") or {}
        if (
            input_contract.get("kind") != "group_company_standard_workbook"
            or input_contract.get("version") not in {"2026-09", "2026-11", "2026-11-05"}
            or input_contract.get("sheet") != "Sheet2"
            or input_contract.get("status") != "PASS"
            or not has_value(input_contract.get("source_file"))
            or not re.fullmatch(
                r"[0-9a-fA-F]{64}", str(input_contract.get("source_sha256") or "")
            )
        ):
            issues.append(
                _issue(
                    _severity(mode),
                    "STANDARD_INPUT_REQUIRED",
                    "input_contract",
                    "集团公司/有限公司报告必须由标准输入工作簿导入，且输入校验状态为 PASS",
                )
            )
        entity_mode = get_path(normalized, "project.entity_mode")
        expected_tax = {"group": "tax_included", "limited_company": "tax_excluded"}.get(
            entity_mode
        )
        if expected_tax is None:
            issues.append(
                _issue(
                    _severity(mode),
                    "MISSING_ENTITY_MODE",
                    "project.entity_mode",
                    "集团项目必须确认 group 或 limited_company",
                )
            )
        elif funding.get("tax_basis") != expected_tax:
            issues.append(
                _issue(
                    "ERROR",
                    "TAX_BASIS_MISMATCH",
                    "funding.tax_basis",
                    f"{entity_mode} 预期税口径为 {expected_tax}",
                    expected=expected_tax,
                    actual=funding.get("tax_basis"),
                )
            )
        prepared_date = get_path(normalized, "report.prepared_date")
        if has_value(prepared_date):
            try:
                date.fromisoformat(str(prepared_date))
            except ValueError:
                issues.append(
                    _issue(
                        "ERROR",
                        "INVALID_DATE",
                        "report.prepared_date",
                        "报告编制日期必须使用 YYYY-MM-DD",
                    )
                )
        adjustments = funding.get("adjustments") or {}
        none_declared = adjustments.get("none_declared")
        adjustment_items = adjustments.get("items") or []
        if none_declared is not None and not isinstance(none_declared, bool):
            issues.append(_issue("ERROR", "ADJUSTMENT_DECLARATION_INVALID", "funding.adjustments.none_declared", "无概预算变更声明必须为 true、false 或 null"))
        if none_declared is True and adjustment_items:
            issues.append(
                _issue(
                    "ERROR",
                    "ADJUSTMENT_DECLARATION_CONFLICT",
                    "funding.adjustments",
                    "已声明无概预算变更，但同时填写了变更事项",
                )
            )
        if none_declared is False and not adjustment_items:
            issues.append(
                _issue(
                    "INFO",
                    "ADJUSTMENT_DETAILS_REQUIRED",
                    "funding.adjustments.items",
                    "已声明发生概预算变更，必须填写变更内容、理由和批复情况",
                )
            )
        for index, item in enumerate(adjustment_items):
            if str(item.get("level") or "").strip() not in {
                "major",
                "general",
                "重大变更",
                "一般变更",
                "预算调剂",
            }:
                issues.append(
                    _issue(
                        "INFO",
                        "ADJUSTMENT_LEVEL_REQUIRED",
                        f"funding.adjustments.items[{index}].level",
                        "概预算变更必须标明重大变更或一般变更/预算调剂",
                    )
                )
            for field, label in [
                ("content", "调整内容"),
                ("reason", "调整理由"),
                ("approval", "调整批复情况"),
            ]:
                if not has_value(item.get(field)):
                    issues.append(
                        _issue(
                            "INFO",
                            "ADJUSTMENT_FIELD_REQUIRED",
                            f"funding.adjustments.items[{index}].{field}",
                            f"概预算变更缺少{label}",
                        )
                    )

    approved_total = decimal_or_none(funding.get("approved_total"))
    source_total = decimal_or_none(
        get_path(normalized, "_derived.totals.source_approved")
    )
    if (
        approved_total is not None
        and source_total is not None
        and abs(approved_total - source_total) > TOLERANCE
    ):
        issues.append(
            _issue(
                "ERROR",
                "SOURCE_TOTAL_MISMATCH",
                "funding.sources",
                "资金来源批复合计与批复总额不一致",
                expected=json_number(approved_total),
                actual=json_number(source_total),
                difference=json_number(source_total - approved_total),
            )
        )

    target_budget = decimal_or_none(
        get_path(normalized, "_derived.totals.target_budget")
    )
    expense_budget = decimal_or_none(
        get_path(normalized, "_derived.totals.expense_budget")
    )
    if (
        target_budget is not None
        and expense_budget is not None
        and abs(target_budget - expense_budget) > TOLERANCE
    ):
        issues.append(
            _issue(
                "ERROR",
                "EXPENSE_BUDGET_MISMATCH",
                "expenses",
                "费用明细预算合计与报告范围预算不一致",
                expected=json_number(target_budget),
                actual=json_number(expense_budget),
                difference=json_number(expense_budget - target_budget),
            )
        )

    reported_total = decimal_or_none(get_path(normalized, "_derived.totals.reported"))
    audited_total = decimal_or_none(get_path(normalized, "_derived.totals.audited"))
    audit_reduction = decimal_or_none(
        get_path(normalized, "_derived.totals.audit_reduction")
    )
    audit_checks = [
        (
            "audit.reported_expenditure",
            reported_total,
            "AUDIT_REPORTED_MISMATCH",
            "报审总额与费用明细报审合计不一致",
        ),
        (
            "audit.recognized_expenditure",
            audited_total,
            "AUDIT_RECOGNIZED_MISMATCH",
            "审定总额与费用明细审定合计不一致",
        ),
        (
            "audit.audit_reduction",
            audit_reduction,
            "AUDIT_REDUCTION_MISMATCH",
            "审减额不等于报审总额减审定总额",
        ),
    ]
    for dotted, calculated, code, message in audit_checks:
        provided = decimal_or_none(get_path(normalized, dotted))
        if (
            provided is not None
            and calculated is not None
            and abs(provided - calculated) > TOLERANCE
        ):
            issues.append(
                _issue(
                    "ERROR",
                    code,
                    dotted,
                    message,
                    expected=json_number(calculated),
                    actual=json_number(provided),
                )
            )

    evidence = normalized.get("evidence") or []
    evidence_ids = [item.get("id") for item in evidence if isinstance(item, dict)]
    duplicate_ids = sorted(
        {item for item in evidence_ids if item and evidence_ids.count(item) > 1}
    )
    for evidence_id in duplicate_ids:
        issues.append(
            _issue(
                "ERROR",
                "DUPLICATE_EVIDENCE_ID",
                "evidence",
                f"证据 ID 重复：{evidence_id}",
            )
        )
    known_evidence = {item for item in evidence_ids if item}
    if profile == "group_company" and "outsourcing" in funding:
        issues.extend(_validate_outsourcing(normalized, known_evidence, mode))

    for index, source in enumerate(funding.get("sources") or []):
        refs = source.get("evidence_refs") or []
        if mode == "final" and not refs:
            issues.append(
                _issue(
                    "ERROR",
                    "MISSING_EVIDENCE",
                    f"funding.sources[{index}].evidence_refs",
                    "资金来源缺少证据定位",
                )
            )
        for ref in refs:
            if ref not in known_evidence:
                issues.append(
                    _issue(
                        "ERROR",
                        "UNKNOWN_EVIDENCE",
                        f"funding.sources[{index}].evidence_refs",
                        f"证据 ID 不存在：{ref}",
                    )
                )

    for index, item in enumerate(normalized.get("_derived", {}).get("expenses") or []):
        adjusted = decimal_or_none(item.get("adjusted_budget"))
        recognized = decimal_or_none(item.get("recognized"))
        if adjusted is None and recognized is None:
            continue
        if mode == "final" and not item.get("evidence_refs"):
            issues.append(
                _issue(
                    "ERROR",
                    "MISSING_EVIDENCE",
                    f"expenses[{index}].evidence_refs",
                    f"费用“{item.get('name')}”缺少证据定位",
                )
            )
        for ref in item.get("evidence_refs") or []:
            if ref not in known_evidence:
                issues.append(
                    _issue(
                        "ERROR",
                        "UNKNOWN_EVIDENCE",
                        f"expenses[{index}].evidence_refs",
                        f"证据 ID 不存在：{ref}",
                    )
                )
        variance_rate = decimal_or_none(item.get("variance_rate"))
        balance = decimal_or_none(item.get("balance"))
        reason_required = (
            profile == "group_company"
            and balance is not None
            and abs(balance) > TOLERANCE
        ) or (
            profile != "group_company"
            and variance_rate is not None
            and abs(variance_rate) >= Decimal("10")
        )
        if reason_required and not has_value(item.get("variance_reason")):
            threshold = (
                "存在结余或超支"
                if profile == "group_company"
                else f"差异达到 {abs(variance_rate or Decimal('0'))}%"
            )
            issues.append(
                _issue(
                    _severity(mode),
                    "VARIANCE_REASON_REQUIRED",
                    f"expenses[{index}].variance_reason",
                    f"费用“{item.get('name')}”{threshold}，必须说明原因",
                )
            )
        if (
            profile == "miit"
            and variance_rate is not None
            and abs(variance_rate) >= Decimal("30")
        ):
            issues.append(
                _issue(
                    "WARNING",
                    "SIGNIFICANT_VARIANCE",
                    f"expenses[{index}]",
                    f"费用“{item.get('name')}”差异达到 {abs(variance_rate)}%，应在单项经费超支（结余）栏目重点说明",
                )
            )

    for index, conflict in enumerate(normalized.get("conflicts") or []):
        if not has_value(conflict.get("resolution")):
            issues.append(
                _issue(
                    _severity(mode),
                    "UNRESOLVED_CONFLICT",
                    f"conflicts[{index}]",
                    "存在未解决的数据冲突",
                )
            )

    for start_path, end_path, code in [
        ("project.start_date", "project.end_date", "INVALID_PLANNED_PERIOD"),
        (
            "project.actual_start_date",
            "project.actual_end_date",
            "INVALID_ACTUAL_PERIOD",
        ),
    ]:
        start = get_path(normalized, start_path)
        end = get_path(normalized, end_path)
        if has_value(start) and has_value(end):
            try:
                if date.fromisoformat(str(start)) > date.fromisoformat(str(end)):
                    issues.append(
                        _issue("ERROR", code, start_path, "开始日期晚于结束日期")
                    )
            except ValueError:
                issues.append(
                    _issue(
                        "ERROR", "INVALID_DATE", start_path, "日期必须使用 YYYY-MM-DD"
                    )
                )

    if mode == "final":
        # The original parent cell is provenance, not report text: its displayed
        # reason is derived from the two children and checked independently.
        source_only_reason_paths = {
            f"expenses[{index}].input_variance_reason"
            for index, item in enumerate(normalized.get("expenses", []))
            if profile == "group_company" and item.get("code") == "external_testing"
        }
        for path, text in _strings(normalized):
            if path in source_only_reason_paths:
                continue
            # Group reports do not use acceptance dates. Keep supplied input
            # unchanged without turning it into a completion requirement.
            if profile == "group_company" and (
                path == "project.acceptance_date"
                or path.startswith(("project.acceptance_date.", "project.acceptance_date["))
            ):
                continue
            if PLACEHOLDER_RE.search(text):
                issues.append(
                    _issue(
                        "ERROR",
                        "PLACEHOLDER_FOUND",
                        path,
                        "正式稿中发现占位符",
                        value=text[:120],
                    )
                )

    issues.sort(
        key=lambda item: (
            {"ERROR": 0, "WARNING": 1}.get(item["severity"], 2),
            item["code"],
            item["path"],
        )
    )
    return normalized, issues


def validation_report(case: dict[str, Any], mode: str = "draft") -> dict[str, Any]:
    normalized, issues = validate(case, mode=mode)
    errors = sum(1 for item in issues if item["severity"] == "ERROR")
    warnings = sum(1 for item in issues if item["severity"] == "WARNING")
    information = sum(1 for item in issues if item["severity"] == "INFO")
    return {
        "report_version": "1.0",
        "profile": normalized.get("profile"),
        "mode": mode,
        "status": "PASS" if errors == 0 else "FAIL",
        "summary": {"errors": errors, "warnings": warnings, "information": information},
        "derived": normalized.get("_derived", {}),
        "issues": issues,
    }

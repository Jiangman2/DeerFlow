"""Read and validate the fixed group-company settlement input workbook.

The parser intentionally uses only the Python standard library. Uploaded XLSX
files are treated as data: only fixed cells from the audited worksheet are read,
and formulas in the input area are rejected.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
import zipfile
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

from report_model import initial_case, json_number

SHEET_NAME = "Sheet2"
INPUT_KIND = "group_company_standard_workbook"
INPUT_VERSION = "2026-09"
TOLERANCE = Decimal("0.01")
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 256
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 64 * 1024 * 1024
MAX_XML_PART_BYTES = 16 * 1024 * 1024

_URI_SCHEME = "http" + "://"
SS = _URI_SCHEME + "schemas.openxmlformats.org/spreadsheetml/2006/main"
OD_REL = _URI_SCHEME + "schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = _URI_SCHEME + "schemas.openxmlformats.org/package/2006/relationships"
WORKSHEET_REL = (
    _URI_SCHEME
    + "schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
)

EXPECTED_DIMENSION = "A1:M36"
EXPECTED_MERGED_CELLS = frozenset(
    {
        "B2:E2",
        "B3:E3",
        "B4:E4",
        "B5:E5",
        "B6:E6",
        "B7:E7",
        "B8:E8",
        "B9:E9",
        "A13:E13",
        "F13:G13",
        "F22:G22",
        "F23:G23",
        "F24:G24",
        "F25:G25",
        "F26:G26",
        "F27:G27",
        "F28:G28",
        "F29:G29",
        "F30:G30",
        "F31:G31",
        "F32:G32",
        "F33:G33",
        "F34:G34",
        "F35:G35",
        "F36:G36",
    }
)

LABELS = {
    "A1": "附录1",
    "A2": "项目(课题)编号：",
    "A3": "项目(课题)名称：",
    "A4": "项目类型：",
    "A5": "承担单位：",
    "A6": "起止时间：",
    "A7": "报告编制人（签字）：",
    "A8": "课题负责人（签字）：",
    "A9": "承担单位财务部门负责人（签字）：",
    "A12": "附录2",
    "A13": "经费预算批复情况",
    "F13": "经费支出情况",
    "A14": "预算批准项",
    "B14": "有限公司资金",
    "C14": "所属单位自筹",
    "D14": "其他资金",
    "E14": "合计",
    "F14": "支出项",
    "G14": "实际支出额",
    "A15": "批准概算额",
    "F15": "实际支出总额",
    "A21": "附录3",
    "A22": "科目",
    "B22": "预算额",
    "C22": "调整后的预算额",
    "D22": "支出额",
    "E22": "结余",
    "F22": "结余原因",
    "A23": "（一）直接经费小计",
    "A24": "1、设备与软件费",
    "A25": "2、材料/燃料动力费",
    "A26": "3、外协实验/试验费",
    "A27": "（1）测试化验加工费",
    "A28": "（2）试验费",
    "A29": "4、外委研究费",
    "A30": "5、出版/文献/信息传播/知识产权事务费",
    "A31": "6、会议/差旅/国际合作与交流费",
    "A32": "7、专家咨询评审/劳务费",
    "A33": "8、其他费用",
    "A34": "（二）间接经费",
    "A35": "1、研发人员费",
    "A36": "2、依托工程费",
}

EXPENSE_ROWS = [
    (24, "equipment_software", "设备与软件费", "direct", None, True),
    (25, "materials_energy", "材料/燃料动力费", "direct", None, True),
    (26, "external_testing", "外协实验/试验费", "direct", None, True),
    (27, "testing_processing", "测试化验加工费", "direct", "external_testing", False),
    (28, "testing_trials", "试验费", "direct", "external_testing", False),
    (29, "commissioned_research", "外委研究费", "direct", None, True),
    (30, "publication_ip", "出版/文献/信息传播/知识产权事务费", "direct", None, True),
    (31, "travel_meeting_cooperation", "会议/差旅/国际合作与交流费", "direct", None, True),
    (32, "consulting_labor", "专家咨询评审/劳务费", "direct", None, True),
    (33, "other", "其他费用", "direct", None, True),
    (35, "research_staff", "研发人员费", "indirect", None, True),
    (36, "engineering_support", "依托工程费", "indirect", None, True),
]


def _issue(
    severity: str, code: str, cell: str, message: str, **details: Any
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "severity": severity,
        "code": code,
        "cell": cell,
        "message": message,
    }
    result.update(details)
    return result


def _plain_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).replace("（", "(").replace("）", ")")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FixedWorkbook:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.values: dict[str, Any] = {}
        self.formulas: dict[str, str] = {}
        self.sheet_name = SHEET_NAME
        self._read()

    def _read(self) -> None:
        try:
            archive_bytes = self.path.stat().st_size
        except OSError as exc:
            raise ValueError(f"无法读取标准输入工作簿：{exc}") from exc
        if archive_bytes > MAX_ARCHIVE_BYTES:
            raise ValueError(
                f"标准输入工作簿压缩文件过大：{archive_bytes} bytes，"
                f"上限为 {MAX_ARCHIVE_BYTES} bytes"
            )
        try:
            archive = zipfile.ZipFile(self.path)
        except (OSError, zipfile.BadZipFile) as exc:
            raise ValueError(f"无法读取标准输入工作簿：{exc}") from exc
        with archive:
            self._validate_archive(archive)
            names = set(archive.namelist())
            required = {"xl/workbook.xml", "xl/_rels/workbook.xml.rels"}
            missing = sorted(required - names)
            if missing:
                raise ValueError(f"工作簿缺少必要部件：{', '.join(missing)}")
            shared = self._shared_strings(archive)
            target = self._worksheet_target(archive)
            if target not in names:
                raise ValueError(f"工作簿缺少工作表部件：{target}")
            root = self._xml_root(archive, target)
            self._validate_sheet_structure(root)
            for cell in root.findall(f".//{{{SS}}}c"):
                ref = cell.attrib.get("r")
                if not ref:
                    continue
                formula = cell.find(f"{{{SS}}}f")
                if formula is not None:
                    self.formulas[ref] = formula.text or ""
                cell_type = cell.attrib.get("t")
                value_node = cell.find(f"{{{SS}}}v")
                if cell_type == "inlineStr":
                    inline = cell.find(f"{{{SS}}}is")
                    self.values[ref] = self._text_nodes(inline)
                elif value_node is None:
                    self.values[ref] = None
                elif cell_type == "s":
                    try:
                        self.values[ref] = shared[int(value_node.text or "0")]
                    except (IndexError, ValueError) as exc:
                        raise ValueError(f"共享字符串索引无效：{ref}") from exc
                elif cell_type in {"str", "e"}:
                    self.values[ref] = value_node.text or ""
                elif cell_type == "b":
                    self.values[ref] = value_node.text == "1"
                else:
                    raw = value_node.text
                    if raw in (None, ""):
                        self.values[ref] = None
                    else:
                        try:
                            self.values[ref] = Decimal(raw)
                        except InvalidOperation:
                            self.values[ref] = raw

    @staticmethod
    def _text_nodes(node: ET.Element | None) -> str:
        if node is None:
            return ""
        return "".join(text.text or "" for text in node.findall(f".//{{{SS}}}t"))

    def _shared_strings(self, archive: zipfile.ZipFile) -> list[str]:
        if "xl/sharedStrings.xml" not in archive.namelist():
            return []
        root = self._xml_root(archive, "xl/sharedStrings.xml")
        return [self._text_nodes(item) for item in root.findall(f"{{{SS}}}si")]

    def _worksheet_target(self, archive: zipfile.ZipFile) -> str:
        workbook = self._xml_root(archive, "xl/workbook.xml")
        sheets = workbook.findall(f".//{{{SS}}}sheet")
        if len(sheets) != 1 or sheets[0].attrib.get("name") != SHEET_NAME:
            names = [sheet.attrib.get("name", "") for sheet in sheets]
            raise ValueError(
                f"标准输入工作簿必须且只能包含工作表 {SHEET_NAME}；实际为 {names}"
            )
        relation_id = None
        for sheet in sheets:
            if sheet.attrib.get("name") == SHEET_NAME:
                relation_id = sheet.attrib.get(f"{{{OD_REL}}}id")
                break
        if not relation_id:
            raise ValueError(f"标准输入工作表必须命名为 {SHEET_NAME}")
        relationships = self._xml_root(archive, "xl/_rels/workbook.xml.rels")
        for relationship in relationships.findall(f"{{{PKG_REL}}}Relationship"):
            if relationship.attrib.get("Id") == relation_id:
                target = relationship.attrib.get("Target") or ""
                if relationship.attrib.get("TargetMode") == "External":
                    raise ValueError("标准输入工作表不能使用外部关系")
                if relationship.attrib.get("Type") != WORKSHEET_REL:
                    raise ValueError("标准输入工作表关系类型无效")
                candidate = target.lstrip("/") if target.startswith("/") else str(
                    PurePosixPath("xl") / target
                )
                normalized = posixpath.normpath(candidate.replace("\\", "/"))
                if not normalized.startswith("xl/worksheets/") or ".." in PurePosixPath(
                    normalized
                ).parts:
                    raise ValueError("标准输入工作表关系越出 xl/worksheets 安全目录")
                return normalized
        raise ValueError(f"无法解析工作表 {SHEET_NAME} 的关系")

    @staticmethod
    def _validate_archive(archive: zipfile.ZipFile) -> None:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise ValueError(
                f"标准输入工作簿部件过多：{len(infos)}，上限为 {MAX_ARCHIVE_ENTRIES}"
            )
        total_size = 0
        normalized_names: set[str] = set()
        for info in infos:
            raw_name = info.filename.replace("\\", "/")
            path = PurePosixPath(raw_name.rstrip("/"))
            if (
                not raw_name
                or raw_name.startswith("/")
                or any(part in {"", ".", ".."} for part in path.parts)
            ):
                raise ValueError(f"标准输入工作簿包含不安全部件路径：{info.filename}")
            normalized = str(path)
            if normalized in normalized_names:
                raise ValueError(f"标准输入工作簿包含重复部件：{normalized}")
            normalized_names.add(normalized)
            if info.flag_bits & 0x1:
                raise ValueError(f"标准输入工作簿不能包含加密部件：{normalized}")
            total_size += info.file_size
            if info.filename.lower().endswith((".xml", ".rels")) and info.file_size > MAX_XML_PART_BYTES:
                raise ValueError(f"标准输入工作簿 XML 部件过大：{normalized}")
        if total_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ValueError(
                f"标准输入工作簿解压后过大：{total_size} bytes，"
                f"上限为 {MAX_ARCHIVE_UNCOMPRESSED_BYTES} bytes"
            )

    @staticmethod
    def _xml_root(archive: zipfile.ZipFile, name: str) -> ET.Element:
        try:
            info = archive.getinfo(name)
        except KeyError as exc:
            raise ValueError(f"工作簿缺少必要 XML 部件：{name}") from exc
        if info.file_size > MAX_XML_PART_BYTES:
            raise ValueError(f"标准输入工作簿 XML 部件过大：{name}")
        try:
            payload = archive.read(name)
            if len(payload) > MAX_XML_PART_BYTES:
                raise ValueError(f"标准输入工作簿 XML 部件过大：{name}")
            return ET.fromstring(payload)
        except ET.ParseError as exc:
            raise ValueError(f"标准输入工作簿 XML 无法解析：{name}") from exc

    @staticmethod
    def _validate_sheet_structure(root: ET.Element) -> None:
        dimension = root.find(f"{{{SS}}}dimension")
        actual_dimension = dimension.attrib.get("ref") if dimension is not None else None
        if actual_dimension != EXPECTED_DIMENSION:
            raise ValueError(
                f"标准输入表尺寸已改变：期望 {EXPECTED_DIMENSION}，实际 {actual_dimension}"
            )
        actual_merges = frozenset(
            node.attrib.get("ref", "")
            for node in root.findall(f".//{{{SS}}}mergeCell")
        )
        if actual_merges != EXPECTED_MERGED_CELLS:
            missing = sorted(EXPECTED_MERGED_CELLS - actual_merges)
            added = sorted(actual_merges - EXPECTED_MERGED_CELLS)
            raise ValueError(
                f"标准输入表合并关系已改变：missing={missing}, added={added}"
            )

    def get(self, ref: str) -> Any:
        return self.values.get(ref)


def _number(
    workbook: FixedWorkbook,
    ref: str,
    issues: list[dict[str, Any]],
    *,
    required: bool = True,
) -> Decimal | None:
    value = workbook.get(ref)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            issues.append(_issue("ERROR", "MISSING_NUMBER", ref, "必填金额为空"))
        return None
    if isinstance(value, bool):
        issues.append(_issue("ERROR", "INVALID_NUMBER", ref, "金额不能是布尔值"))
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except InvalidOperation:
        issues.append(
            _issue("ERROR", "INVALID_NUMBER", ref, "金额必须为数值，单位为万元", value=str(value))
        )
        return None


def _required_text(
    workbook: FixedWorkbook, ref: str, issues: list[dict[str, Any]]
) -> str | None:
    value = workbook.get(ref)
    text = str(value).strip() if value is not None else ""
    if not text:
        issues.append(_issue("ERROR", "MISSING_TEXT", ref, "必填文本为空"))
        return None
    return text


def _parse_date(value: str) -> str | None:
    text = value.strip()
    for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            import datetime

            return datetime.datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_period(
    value: str | None, issues: list[dict[str, Any]], ref: str = "B6"
) -> tuple[str | None, str | None]:
    if not value:
        return None, None
    text = re.sub(r"\s+", "", value)
    date_matches = re.findall(
        r"\d{4}(?:年|[-/.])\d{1,2}(?:月|[-/.])\d{1,2}日?", text
    )
    if len(date_matches) != 2:
        issues.append(
            _issue(
                "ERROR",
                "INVALID_PERIOD",
                ref,
                "起止时间必须同时包含开始和结束日期，例如 2023-01-01 至 2025-12-31",
            )
        )
        return None, None
    parsed = [_parse_date(value) for value in date_matches]
    if not all(parsed):
        issues.append(_issue("ERROR", "INVALID_PERIOD", ref, "无法解析起止日期"))
        return None, None
    start, end = parsed
    assert start is not None and end is not None
    if date.fromisoformat(start) > date.fromisoformat(end):
        issues.append(_issue("ERROR", "INVALID_PERIOD", ref, "开始日期晚于结束日期"))
    return start, end


def _entity_mode(value: str | None, issues: list[dict[str, Any]]) -> str | None:
    compact = _plain_text(value)
    if "有限公司" in compact:
        return "limited_company"
    if "集团公司" in compact or compact in {"集团", "集团项目"}:
        return "group"
    issues.append(
        _issue(
            "ERROR",
            "INVALID_PROJECT_TYPE",
            "B4",
            "项目类型必须明确填写“集团公司项目”或“有限公司项目”",
            value=value,
        )
    )
    return None


def _check_equal(
    issues: list[dict[str, Any]],
    code: str,
    cell: str,
    expected: Decimal | None,
    actual: Decimal | None,
    message: str,
) -> None:
    if expected is None or actual is None:
        return
    if abs(expected - actual) > TOLERANCE:
        issues.append(
            _issue(
                "ERROR",
                code,
                cell,
                message,
                expected=json_number(expected),
                actual=json_number(actual),
                difference=json_number(actual - expected),
            )
        )


def _sum_present(values: list[Decimal | None]) -> Decimal | None:
    if any(value is None for value in values):
        return None
    return sum((value for value in values if value is not None), Decimal("0"))


def _expense_row(
    workbook: FixedWorkbook,
    row: int,
    code: str,
    name: str,
    scope: str,
    parent_code: str | None,
    include_in_total: bool,
    issues: list[dict[str, Any]],
) -> dict[str, Any]:
    budget = _number(workbook, f"B{row}", issues)
    adjusted = _number(workbook, f"C{row}", issues)
    actual = _number(workbook, f"D{row}", issues)
    balance = _number(workbook, f"E{row}", issues)
    reason_value = workbook.get(f"F{row}")
    reason = str(reason_value).strip() if reason_value is not None else ""
    calculated = adjusted - actual if adjusted is not None and actual is not None else None
    _check_equal(
        issues,
        "BALANCE_MISMATCH",
        f"E{row}",
        calculated,
        balance,
        f"{name}结余必须等于调整后的预算额减支出额",
    )
    if balance is not None and abs(balance) > TOLERANCE and not reason:
        issues.append(
            _issue(
                "ERROR",
                "BALANCE_REASON_REQUIRED",
                f"F{row}",
                f"{name}存在结余或超支，必须填写结余原因",
            )
        )
    return {
        "code": code,
        "name": name,
        "scope": scope,
        "parent_code": parent_code,
        "include_in_total": include_in_total,
        "budget": json_number(budget),
        "adjusted_budget": json_number(adjusted),
        "reported": json_number(actual),
        "actual": json_number(actual),
        "audited": None,
        "description": None,
        "variance_reason": reason or None,
        "evidence_refs": ["INPUT-APPENDIX-3"],
        "input_row": row,
        "input_balance": json_number(balance),
    }


def import_group_company_workbook(
    path: str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source = Path(path)
    if source.suffix.lower() != ".xlsx":
        raise ValueError("集团公司标准输入文件必须为 .xlsx")
    workbook = FixedWorkbook(source)
    issues: list[dict[str, Any]] = []

    for ref, expected in LABELS.items():
        actual = workbook.get(ref)
        if _plain_text(actual) != _plain_text(expected):
            issues.append(
                _issue(
                    "ERROR",
                    "TEMPLATE_LABEL_MISMATCH",
                    ref,
                    "标准输入表结构或固定标签已改变",
                    expected=expected,
                    actual=actual,
                )
            )
    for ref in sorted(workbook.formulas):
        if re.fullmatch(r"[A-G](?:[1-9]|[12]\d|3[0-6])", ref):
            issues.append(
                _issue(
                    "ERROR",
                    "FORMULA_NOT_ALLOWED",
                    ref,
                    "标准输入区域只能填写静态值，不能使用公式",
                )
            )

    project_code = _required_text(workbook, "B2", issues)
    project_name = _required_text(workbook, "B3", issues)
    project_type = _required_text(workbook, "B4", issues)
    entity_mode = _entity_mode(project_type, issues)
    lead_name = _required_text(workbook, "B5", issues)
    period_text = _required_text(workbook, "B6", issues)
    start_date, end_date = _parse_period(period_text, issues)
    prepared_by = _required_text(workbook, "B7", issues)
    responsible_person = _required_text(workbook, "B8", issues)
    reviewed_by = _required_text(workbook, "B9", issues)

    approved_parts = [_number(workbook, ref, issues) for ref in ("B15", "C15", "D15")]
    approved_total = _number(workbook, "E15", issues)
    actual_total = _number(workbook, "G15", issues)
    _check_equal(
        issues,
        "APPROVED_SOURCE_TOTAL_MISMATCH",
        "E15",
        _sum_present(approved_parts),
        approved_total,
        "批准概算额合计必须等于三类资金来源之和",
    )

    annual: list[dict[str, Any]] = []
    for row in range(16, 20):
        raw_values = [workbook.get(f"{column}{row}") for column in "BCDEG"]
        if all(value is None or str(value).strip() == "" for value in raw_values):
            continue
        label = str(workbook.get(f"A{row}") or "")
        expenditure_label = str(workbook.get(f"F{row}") or "")
        year_match = re.search(r"(\d{4})年批准额", label)
        spend_match = re.search(r"(\d{4})年支出额", expenditure_label)
        if not year_match or not spend_match or year_match.group(1) != spend_match.group(1):
            issues.append(
                _issue(
                    "ERROR",
                    "INVALID_ANNUAL_LABEL",
                    f"A{row}:F{row}",
                    "年度批准额与年度支出额标签必须使用同一个四位年份",
                )
            )
            year = None
        else:
            year = int(year_match.group(1))
        components = [_number(workbook, f"{column}{row}", issues) for column in "BCD"]
        amount = _number(workbook, f"E{row}", issues)
        actual_spent = _number(workbook, f"G{row}", issues)
        _check_equal(
            issues,
            "ANNUAL_SOURCE_TOTAL_MISMATCH",
            f"E{row}",
            _sum_present(components),
            amount,
            "年度批准额合计必须等于三类资金来源之和",
        )
        annual.append(
            {
                "input_row": row,
                "year": year,
                "amount": json_number(amount),
                "actual_spent": json_number(actual_spent),
                "sources": {
                    "company": json_number(components[0]),
                    "self_funded": json_number(components[1]),
                    "other": json_number(components[2]),
                },
                "document": "年度正式预算额（实际下达）",
                "evidence_refs": ["INPUT-APPENDIX-2"],
            }
        )
    if not annual:
        issues.append(
            _issue("ERROR", "MISSING_ANNUAL_DATA", "A16:G19", "至少填写一个年度批准额和支出额")
        )
    annual_years = [
        item.get("year") for item in annual if item.get("year") is not None
    ]
    if len(annual_years) != len(set(annual_years)):
        issues.append(
            _issue("ERROR", "DUPLICATE_ANNUAL_YEAR", "A16:A19", "分年预算年份不得重复")
        )
    if start_date and end_date:
        start_year = date.fromisoformat(start_date).year
        end_year = date.fromisoformat(end_date).year
        for item in annual:
            year = item.get("year")
            if year is None:
                continue
            if year < start_year or year > end_year:
                issues.append(
                    _issue(
                        "ERROR",
                        "ANNUAL_YEAR_OUTSIDE_PERIOD",
                        f"A{item['input_row']}",
                        "分年预算年份必须位于项目起止年度内",
                        year=year,
                        project_start_year=start_year,
                        project_end_year=end_year,
                    )
                )
    _check_equal(
        issues,
        "ANNUAL_APPROVED_TOTAL_MISMATCH",
        "E16:E19",
        _sum_present([Decimal(str(item["amount"])) if item["amount"] is not None else None for item in annual]),
        approved_total,
        "各年度批准额合计必须等于批准概算额",
    )
    _check_equal(
        issues,
        "ANNUAL_SPENT_TOTAL_MISMATCH",
        "G16:G19",
        _sum_present(
            [Decimal(str(item["actual_spent"])) if item["actual_spent"] is not None else None for item in annual]
        ),
        actual_total,
        "各年度支出额合计必须等于实际支出总额",
    )

    expenses = [
        _expense_row(workbook, row, code, name, scope, parent, included, issues)
        for row, code, name, scope, parent, included in EXPENSE_ROWS
    ]
    by_code = {item["code"]: item for item in expenses}

    def expense_number(code: str, field: str) -> Decimal | None:
        value = by_code[code].get(field)
        return Decimal(str(value)) if value is not None else None

    columns = {
        "budget": "B",
        "adjusted_budget": "C",
        "actual": "D",
        "input_balance": "E",
    }
    direct_codes = [
        "equipment_software",
        "materials_energy",
        "external_testing",
        "commissioned_research",
        "publication_ip",
        "travel_meeting_cooperation",
        "consulting_labor",
        "other",
    ]
    indirect_codes = ["research_staff", "engineering_support"]
    for field, column in columns.items():
        direct_declared = _number(workbook, f"{column}23", issues)
        indirect_declared = _number(workbook, f"{column}34", issues)
        direct_calculated = _sum_present([expense_number(code, field) for code in direct_codes])
        indirect_calculated = _sum_present([expense_number(code, field) for code in indirect_codes])
        _check_equal(
            issues,
            "DIRECT_SUBTOTAL_MISMATCH",
            f"{column}23",
            direct_calculated,
            direct_declared,
            "直接经费小计与八类直接经费之和不一致",
        )
        _check_equal(
            issues,
            "INDIRECT_SUBTOTAL_MISMATCH",
            f"{column}34",
            indirect_calculated,
            indirect_declared,
            "间接经费小计与研发人员费、依托工程费之和不一致",
        )
        external_parent = expense_number("external_testing", field)
        external_children = _sum_present(
            [expense_number("testing_processing", field), expense_number("testing_trials", field)]
        )
        _check_equal(
            issues,
            "EXTERNAL_TESTING_BREAKDOWN_MISMATCH",
            f"{column}26:{column}28",
            external_children,
            external_parent,
            "外协实验/试验费必须等于测试化验加工费与试验费之和",
        )

    adjusted_total = _sum_present(
        [expense_number(code, "adjusted_budget") for code in direct_codes + indirect_codes]
    )
    base_budget_total = _sum_present(
        [expense_number(code, "budget") for code in direct_codes + indirect_codes]
    )
    expense_actual_total = _sum_present(
        [expense_number(code, "actual") for code in direct_codes + indirect_codes]
    )
    _check_equal(
        issues,
        "EXPENSE_BUDGET_TOTAL_MISMATCH",
        "B23:B36",
        base_budget_total,
        approved_total,
        "附录3预算额合计必须等于附录2批准概算额",
    )
    _check_equal(
        issues,
        "EXPENSE_ACTUAL_TOTAL_MISMATCH",
        "D23:D36",
        expense_actual_total,
        actual_total,
        "附录3支出额合计必须等于附录2实际支出总额",
    )

    case = initial_case("group_company")
    case["report"].update(
        {
            "prepared_by": prepared_by,
            "reviewed_by": reviewed_by,
            "status": "draft",
        }
    )
    case["project"].update(
        {
            "name": project_name,
            "code": project_code,
            "entity_mode": entity_mode,
            "responsible_person": responsible_person,
            "start_date": start_date,
            "end_date": end_date,
            "approval_document": {
                "title": "项目任务合同书",
                "number": project_code,
                "date": None,
            },
        }
    )
    case["organizations"]["lead"].update(
        {
            "name": lead_name,
            "short_name": (
                "研究中心" if lead_name and "研究中心" in lead_name else "研究总院"
            ),
            "kind": (
                "research_center" if lead_name and "研究中心" in lead_name else "research_institute"
            ),
        }
    )
    source_names = [
        "有限公司资金" if entity_mode == "limited_company" else "集团公司资金",
        "所属单位自筹",
        "其他资金",
    ]
    source_kinds = ["company", "self_funded", "other"]
    case["funding"].update(
        {
            "tax_basis": (
                "tax_excluded" if entity_mode == "limited_company" else "tax_included"
            ),
            "approved_total": json_number(approved_total),
            "adjusted_total": json_number(adjusted_total),
            "sources": [
                {
                    "name": name,
                    "kind": kind,
                    "approved": json_number(value),
                    "received": None,
                    "spent": None,
                    "audited": None,
                    "evidence_refs": ["INPUT-APPENDIX-2"],
                }
                for name, kind, value in zip(source_names, source_kinds, approved_parts)
            ],
            "annual_appropriations": annual,
            "adjustments": {
                "none_declared": None,
                "items": [],
            },
        }
    )
    case["expenses"] = expenses
    case["audit"]["reported_expenditure"] = json_number(actual_total)
    case["evidence"] = [
        {
            "id": "INPUT-APPENDIX-1",
            "source_file": source.name,
            "locator": f"{SHEET_NAME}!A1:E9",
            "fact": "项目基本信息、承担单位和签字责任人",
            "as_of": None,
        },
        {
            "id": "INPUT-APPENDIX-2",
            "source_file": source.name,
            "locator": f"{SHEET_NAME}!A12:G19",
            "fact": "任务合同书批准概算、分年实际下达预算和分年支出",
            "as_of": None,
        },
        {
            "id": "INPUT-APPENDIX-3",
            "source_file": source.name,
            "locator": f"{SHEET_NAME}!A21:G36",
            "fact": "各科目预算额、调整后预算额、支出额、结余和结余原因",
            "as_of": None,
        },
    ]

    errors = sum(1 for item in issues if item["severity"] == "ERROR")
    warnings = sum(1 for item in issues if item["severity"] == "WARNING")
    status = "PASS" if errors == 0 else "FAIL"
    case["input_contract"] = {
        "kind": INPUT_KIND,
        "version": INPUT_VERSION,
        "source_file": source.name,
        "source_sha256": _sha256(source),
        "sheet": SHEET_NAME,
        "status": status,
    }
    report = {
        "report_version": "1.0",
        "input_kind": INPUT_KIND,
        "input_version": INPUT_VERSION,
        "source_file": source.name,
        "sheet": SHEET_NAME,
        "status": status,
        "summary": {"errors": errors, "warnings": warnings},
        "issues": sorted(
            issues,
            key=lambda item: (
                {"ERROR": 0, "WARNING": 1}.get(item["severity"], 2),
                item["cell"],
                item["code"],
            ),
        ),
    }
    return case, report

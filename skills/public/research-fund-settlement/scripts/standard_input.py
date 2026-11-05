"""Read and validate the group-company settlement input workbook.

The parser intentionally uses only the Python standard library. Uploaded XLSX
files are treated as data: business labels define the input area; bounded arithmetic formulas are recomputed
without trusting cached values or invoking an Excel engine.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
import zipfile
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

from report_model import initial_case, json_number
from workbook_formulas import FormulaError, FormulaEvaluator, finite_number, translate_shared

SHEET_NAME = "Sheet2"
INPUT_KIND = "group_company_standard_workbook"
INPUT_VERSION = "2026-11-05"
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
        self.formula_errors = {}
        self.cell_styles = {}
        self.date_styles = set()
        self.date_1904 = False
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
            self._read_date_styles(archive)
            if target not in names:
                raise ValueError(f"工作簿缺少工作表部件：{target}")
            root = self._xml_root(archive, target)
            shared_formulas = {}
            pending_shared = []
            sheet_data = root.findall(f"{{{SS}}}sheetData")
            if len(sheet_data) != 1:
                raise ValueError("工作表须有且只有一个数据区域")
            cells = []
            seen_rows, seen_cells = set(), set()
            for row in sheet_data[0].findall(f"{{{SS}}}row"):
                number = row.get("r", "")
                if not re.fullmatch(r"[1-9][0-9]{0,6}", number) or int(number) > 1048576 or number in seen_rows:
                    raise ValueError("工作表行号无效或重复")
                seen_rows.add(number)
                for cell in row.findall(f"{{{SS}}}c"):
                    ref = cell.get("r", "")
                    match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,6})", ref)
                    if not match or match[2] != number or ref in seen_cells:
                        raise ValueError("单元格地址重复或与所在行不一致")
                    col = 0
                    for char in match[1]:
                        col = col * 26 + ord(char) - ord("A") + 1
                    if col > 16384:
                        raise ValueError("单元格列号超出 Excel 范围")
                    seen_cells.add(ref)
                    cells.append(cell)
            if len(cells) != len(root.findall(f".//{{{SS}}}c")):
                raise ValueError("单元格必须位于工作表数据行内，不能隐藏在扩展节点")
            for cell in cells:
                ref = cell.attrib.get("r")
                if not ref:
                    continue
                self.cell_styles[ref] = cell.get("s", "0")
                formula = cell.find(f"{{{SS}}}f")
                if formula is not None:
                    self.formulas[ref] = formula.text or ""
                    kind = formula.get("t", "normal")
                    if kind == "shared":
                        key = formula.get("si")
                        if formula.text:
                            if key in shared_formulas:
                                raise ValueError("重复的共享公式定义")
                            shared_formulas[key] = (ref, formula.text)
                        else:
                            pending_shared.append((ref, key))
                    elif kind != "normal":
                        self.formula_errors[ref] = "不支持数组、数据表或动态公式"
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
                elif cell_type in {"str", "e", "d"}:
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
            for ref, key in pending_shared:
                try:
                    origin, formula = shared_formulas[key]
                    self.formulas[ref] = translate_shared(formula, origin, ref)
                except (KeyError, FormulaError):
                    self.formula_errors[ref] = "共享公式无法解析"
            self._validate_sheet_structure(root)
            # Raw cells/formulas remain keyed by physical addresses for provenance.
            self.allowed_numbers = {f"{c}{r}" for r in range(15, self.appendix3)
                                    for c in "BCDEG"}
            self.allowed_numbers |= {f"{c}{r}" for r in range(self.appendix3+2, self.appendix3+16)
                                     for c in "BCDE"}
            if self.appendix4 is not None:
                self.allowed_numbers |= {f"{c}{self.appendix4+2}" for c in "BCD"}
            self.evaluator = FormulaEvaluator(self.values, self.formulas, self.allowed_numbers)

    def _read_date_styles(self, archive: zipfile.ZipFile) -> None:
        if "xl/styles.xml" not in archive.namelist():
            return
        root = self._xml_root(archive, "xl/styles.xml")
        formats = {n.get("numFmtId"): n.get("formatCode", "")
                   for n in root.findall(f"{{{SS}}}numFmts/{{{SS}}}numFmt")}
        date_ids = {14, 15, 16, 17, 22, 27, 28, 29, 30, 31, 34, 35, 36,
                    50, 51, 52, 53, 54, 55, 56, 57, 58}
        for index, node in enumerate(root.findall(f"{{{SS}}}cellXfs/{{{SS}}}xf")):
            identifier = node.get("numFmtId", "0")
            pattern = re.sub(r'"[^\"]*"|\\.|\[[^\]]*\]', "", formats.get(identifier, "")).lower()
            if (identifier.isdigit() and int(identifier) in date_ids) or all(c in pattern for c in "ymd"):
                self.date_styles.add(str(index))

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
        properties = workbook.find(f"{{{SS}}}workbookPr")
        self.date_1904 = properties is not None and properties.get("date1904") in {"1", "true"}
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

    def _validate_sheet_structure(self, root: ET.Element) -> None:
        # Annual rows can be inserted/deleted; the appendix-3 block moves as a unit.
        anchors = [ref for ref, value in self.values.items()
                   if re.fullmatch(r"A[1-9][0-9]*", ref)
                   and _plain_text(value) == "附录3"]
        if len(anchors) != 1:
            raise ValueError("必须有且只有一个附录3标题")
        self.appendix3 = int(anchors[0][1:])
        if not 17 <= self.appendix3 <= 116:
            raise ValueError("附录2年度区域必须为1至100行")
        self.row_delta = self.appendix3 - 21
        extra = [ref for ref, value in self.values.items()
                 if re.fullmatch(r"A[1-9][0-9]*", ref) and _plain_text(value) == "附录4"]
        if len(extra) > 1:
            raise ValueError("附录4标题不得重复")
        self.appendix4 = int(extra[0][1:]) if extra else None
        if self.appendix4 is not None and not self.appendix3 + 16 <= self.appendix4 <= self.appendix3 + 30:
            raise ValueError("附录4须位于附录3之后，且与附录3间隔不超过15行")
        appendix4_signatures = {"委托系统外单位信息", "支付系统外单位金额", "系统外单位占比", "系统外单位名称1", "系统外单位名称2", "系统外单位名称3"}
        for ref, value in self.values.items():
            match = re.fullmatch(r"([A-G])([1-9][0-9]*)", ref)
            if match and int(match[2]) > self.appendix3 + 15 and _plain_text(value) in appendix4_signatures:
                if self.appendix4 is None or int(match[2]) not in (self.appendix4 + 1, self.appendix4 + 2):
                    raise ValueError("发现附录4信息，但标题缺失或位置不匹配；不得忽略已填的系统外委托信息")
        def shifted(ref):
            return re.sub(r"([A-Z]+)([0-9]+)",
                          lambda m: m[1] + str(int(m[2]) + (self.row_delta if int(m[2]) >= 21 else 0)), ref)
        expected = {shifted(ref) for ref in EXPECTED_MERGED_CELLS}
        actual = set()
        all_merges = set()
        for node in root.findall(f".//{{{SS}}}mergeCell"):
            ref = node.get("ref", "")
            match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*):([A-Z]+)([1-9][0-9]*)", ref)
            if not match:
                raise ValueError("无效合并单元格")
            def column_index(text):
                result = 0
                for ch in text:
                    result = result * 26 + ord(ch) - ord("A") + 1
                return result
            left, right = column_index(match[1]), column_index(match[3])
            top, bottom = int(match[2]), int(match[4])
            if not (1 <= left <= right <= 16384 and 1 <= top <= bottom <= 1048576) or ref in all_merges:
                raise ValueError("合并单元格范围倒置、越界或重复")
            all_merges.add(ref)
            # Explanatory notes to the right are outside the financial input area.
            if left <= 7:
                actual.add(ref)
        optional = {"B10:E10"}
        if self.appendix4 is not None:
            optional.add(f"F{self.appendix4+2}:G{self.appendix4+2}")
        if actual - optional != expected:
            raise ValueError("标准输入表合并关系已改变（仅允许年度行移动、编制日期及附录4单位名称区域的约定合并）")
        for ref in actual & optional:
            start, end = ref.split(":")
            row = int(re.search(r"[0-9]+", start)[0])
            for column in range(ord(start[0])+1, ord(end[0])+1):
                cell = f"{chr(column)}{row}"
                if self.values.get(cell) not in (None, "") or cell in self.formulas:
                    raise ValueError(f"合并单元格覆盖了非空内容：{cell}")

    def input_ref(self, ref: str) -> str:
        return re.sub(r"([A-Z]+)([0-9]+)",
                      lambda m: m[1] + str(int(m[2]) + (self.row_delta if int(m[2]) >= 21 else 0)), ref)

    def get(self, ref: str) -> Any:
        return self.values.get(ref)

    def blank(self, ref: str) -> bool:
        value = self.get(ref)
        return ref not in self.formulas and (value is None or str(value).strip() == "")


def _number(
    workbook: FixedWorkbook,
    ref: str,
    issues: list[dict[str, Any]],
    *,
    required: bool = True,
) -> Decimal | None:
    try:
        if ref in workbook.formula_errors:
            raise FormulaError(workbook.formula_errors[ref])
        value = workbook.get(ref)
        if ref not in workbook.formulas and (value is None or str(value).strip() == ""):
            if required:
                return Decimal("0")
            return None
        return workbook.evaluator.value(ref)
    except FormulaError as exc:
        issues.append(_issue("ERROR", "INVALID_FORMULA" if ref in workbook.formulas else "INVALID_NUMBER",
                             ref, str(exc)))
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


def _prepared_date(workbook: FixedWorkbook, issues: list[dict[str, Any]]) -> str | None:
    """Read the optional new cover field; never infer it from the project period."""
    label = _plain_text(workbook.get("A10")).rstrip(":：")
    if not label and workbook.blank("B10"):
        return None  # Older templates remain readable.
    if label != "编制日期":
        issues.append(_issue("ERROR", "PREPARED_DATE_LABEL", "A10", "编制日期字段标签应为“编制日期”"))
        return None
    if workbook.blank("B10") or "B10" in workbook.formulas:
        return None
    raw = workbook.get("B10")
    parsed = _parse_date(str(raw))
    if parsed is None and isinstance(raw, Decimal) and workbook.cell_styles.get("B10") in workbook.date_styles:
        if raw.is_finite() and raw == raw.to_integral_value() and 0 <= raw <= 2958465:
            days = int(raw)
            if workbook.date_1904 or days != 60:
                origin = date(1904, 1, 1) if workbook.date_1904 else date(1899, 12, 31)
                try:
                    parsed = (origin + timedelta(days=days if workbook.date_1904 or days < 60 else days-1)).isoformat()
                except OverflowError:
                    pass
    if parsed is None and isinstance(raw, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00(?:Z|[+-](?:0\d|1[0-4]):[0-5]\d)?", raw):
        # A date-only midnight value retains its written calendar date; do not
        # shift the financial report date through the host's timezone.
        parsed = _parse_date(raw[:10])
    if parsed is None:
        issues.append(_issue("ERROR", "INVALID_PREPARED_DATE", "B10", "编制日期须填写完整年月日或有效的 Excel 日期，不从项目周期推算"))
    return parsed


def _outsourcing(workbook: FixedWorkbook, commissioned: dict[str, Any],
                 issues: list[dict[str, Any]]) -> dict[str, Any] | None:
    anchor = workbook.appendix4
    if anchor is None:
        return None  # Absence is unknown, distinct from a present blank monetary input.
    header, row = anchor + 1, anchor + 2
    labels = {f"{c}{header}": text for c, text in zip("ABCDEFG", (
        "外委研究费", "支出总额", "支付系统外单位金额", "系统外单位占比",
        "系统外单位名称1", "系统外单位名称2", "系统外单位名称3"))}
    labels[f"A{row}"] = "委托系统外单位信息"
    for ref, expected in labels.items():
        if _plain_text(workbook.get(ref)) != _plain_text(expected):
            issues.append(_issue("ERROR", "APPENDIX4_LABEL_MISMATCH", ref,
                                 "附录4固定标签不匹配", expected=expected))
    total = _number(workbook, f"B{row}", issues, required=False)
    expense = commissioned.get("actual")
    expense = Decimal(str(expense)) if expense is not None else None
    if total is None and workbook.blank(f"B{row}"):
        total = expense
    external = _number(workbook, f"C{row}", issues)
    _check_equal(issues, "OUTSOURCING_TOTAL_MISMATCH", f"B{row}", expense, total,
                 "附录4外委支出总额必须等于附录3外委研究费支出额")
    if total is not None and total < 0:
        issues.append(_issue("ERROR", "OUTSOURCING_NEGATIVE_TOTAL", f"B{row}", "外委支出总额不得为负数"))
    if external is not None and (external < 0 or total is not None and external > total):
        issues.append(_issue("ERROR", "OUTSOURCING_EXTERNAL_BOUND", f"C{row}", "系统外支付金额须在0与外委支出总额之间"))
    ratio = external / total if total is not None and total > 0 and external is not None else None
    supplied = None
    if not workbook.blank(f"D{row}"):
        raw = workbook.get(f"D{row}")
        if f"D{row}" not in workbook.formulas and isinstance(raw, str) and raw.strip().endswith("%"):
            try:
                supplied = finite_number(raw.strip()[:-1]) / 100
            except FormulaError as exc:
                issues.append(_issue("ERROR", "INVALID_EXTERNAL_SHARE", f"D{row}", str(exc)))
        else:
            supplied = _number(workbook, f"D{row}", issues, required=False)
        if supplied is not None and (not 0 <= supplied <= 1 or ratio is not None and abs(supplied-ratio) > Decimal("0.00005") or total == 0 and supplied != 0):
            issues.append(_issue("ERROR", "OUTSOURCING_SHARE_MISMATCH", f"D{row}",
                                 "系统外单位占比须等于系统外金额除以外委支出总额；可留空自动计算",
                                 expected=float(ratio) if ratio is not None else None, actual=float(supplied)))
    organizations = []
    for column in "EFG":
        ref = f"{column}{row}"
        value = workbook.get(ref)
        if value is None or str(value).strip() == "":
            continue
        if not isinstance(value, str):
            issues.append(_issue("ERROR", "INVALID_EXTERNAL_ORGANIZATION", ref, "系统外单位名称须填写文本"))
        elif value.strip() not in organizations:
            organizations.append(value.strip())
    if external is not None and external > 0 and not organizations:
        issues.append(_issue("ERROR", "EXTERNAL_ORGANIZATIONS_REQUIRED", f"E{row}:G{row}",
                             "系统外支付金额大于0时须填写至少一个系统外单位名称"))
    return {"total": json_number(total), "external_amount": json_number(external),
            "external_share": float(ratio) if ratio is not None else None, "external_organizations": organizations,
            "evidence_refs": ["INPUT-APPENDIX-4"]}


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
        return None, None
    parsed = [_parse_date(value) for value in date_matches]
    if not all(parsed):
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
    balance = _number(workbook, f"E{row}", issues, required=False)
    reason_value = workbook.get(f"F{row}")
    reason = str(reason_value).strip() if reason_value is not None else ""
    calculated = adjusted - actual if adjusted is not None and actual is not None else None
    if balance is None and workbook.blank(f"E{row}"):
        balance = calculated
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
        ref = workbook.input_ref(ref)
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
        if ref not in workbook.allowed_numbers:
            if re.fullmatch(r"[A-G][1-9][0-9]*", ref):
                issues.append(_issue("ERROR", "FORMULA_NOT_ALLOWED", ref,
                                     "公式仅允许用于业务金额，标题和文字须填写文本"))

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
    prepared_date = _prepared_date(workbook, issues)

    approved_parts = [_number(workbook, ref, issues) for ref in ("B15", "C15", "D15")]
    approved_total = _number(workbook, "E15", issues, required=False)
    if approved_total is None and workbook.blank("E15"):
        approved_total = _sum_present(approved_parts)
    actual_total = _number(workbook, "G15", issues, required=False)
    _check_equal(
        issues,
        "APPROVED_SOURCE_TOTAL_MISMATCH",
        "E15",
        _sum_present(approved_parts),
        approved_total,
        "批准概算额合计必须等于三类资金来源之和",
    )

    annual: list[dict[str, Any]] = []
    for row in range(16, workbook.appendix3):
        raw_values = [workbook.get(f"{column}{row}") for column in "BCDEG"]
        if all(value is None or str(value).strip() == "" for value in raw_values) and not any(
            f"{c}{row}" in workbook.formulas for c in "BCDEG"
        ):
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
        amount = _number(workbook, f"E{row}", issues, required=False)
        if amount is None and workbook.blank(f"E{row}"):
            amount = _sum_present(components)
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
            _issue("ERROR", "MISSING_ANNUAL_DATA", f"A16:G{workbook.appendix3-1}", "至少填写一个年度批准额和支出额")
        )
    annual_years = [
        item.get("year") for item in annual if item.get("year") is not None
    ]
    if len(annual_years) != len(set(annual_years)):
        issues.append(
            _issue("ERROR", "DUPLICATE_ANNUAL_YEAR", f"A16:A{workbook.appendix3-1}", "分年预算年份不得重复")
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
    if actual_total is None and workbook.blank("G15"):
        actual_total = _sum_present([Decimal(str(item["actual_spent"])) if item["actual_spent"] is not None else None for item in annual])
    # The contract estimate and annual appropriations are independent approvals.
    # Preserve both amounts; only reconcile each row's sources and actual spending.
    _check_equal(
        issues,
        "ANNUAL_SPENT_TOTAL_MISMATCH",
        f"G16:G{workbook.appendix3-1}",
        _sum_present(
            [Decimal(str(item["actual_spent"])) if item["actual_spent"] is not None else None for item in annual]
        ),
        actual_total,
        "各年度支出额合计必须等于实际支出总额",
    )

    expenses = [
        _expense_row(workbook, row + workbook.row_delta, code, name, scope, parent, included, issues)
        for row, code, name, scope, parent, included in EXPENSE_ROWS
    ]
    by_code = {item["code"]: item for item in expenses}
    outsourcing = _outsourcing(workbook, by_code["commissioned_research"], issues)

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
        direct_ref = workbook.input_ref(f"{column}23")
        indirect_ref = workbook.input_ref(f"{column}34")
        direct_declared = _number(workbook, direct_ref, issues, required=False)
        indirect_declared = _number(workbook, indirect_ref, issues, required=False)
        direct_calculated = _sum_present([expense_number(code, field) for code in direct_codes])
        indirect_calculated = _sum_present([expense_number(code, field) for code in indirect_codes])
        _check_equal(
            issues,
            "DIRECT_SUBTOTAL_MISMATCH",
            direct_ref,
            direct_calculated,
            direct_declared,
            "直接经费小计与八类直接经费之和不一致",
        )
        _check_equal(
            issues,
            "INDIRECT_SUBTOTAL_MISMATCH",
            indirect_ref,
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
            workbook.input_ref(f"{column}26:{column}28"),
            external_children,
            external_parent,
            "外协实验/试验费必须等于测试化验加工费与试验费之和",
        )

    adjusted_total = _sum_present(
        [expense_number(code, "adjusted_budget") for code in direct_codes]
    )
    base_budget_total = _sum_present(
        [expense_number(code, "budget") for code in direct_codes]
    )
    expense_actual_total = _sum_present(
        [expense_number(code, "actual") for code in direct_codes]
    )
    _check_equal(
        issues,
        "EXPENSE_BUDGET_TOTAL_MISMATCH",
        workbook.input_ref("B23:B33"),
        base_budget_total,
        approved_total,
        "附录3直接经费预算合计必须等于附录2批准概算额；间接经费单列，不计入预算执行",
    )
    _check_equal(
        issues,
        "EXPENSE_ACTUAL_TOTAL_MISMATCH",
        workbook.input_ref("D23:D33"),
        expense_actual_total,
        actual_total,
        "附录3直接经费支出合计必须等于附录2实际支出总额；间接经费单列，不计入预算执行",
    )

    case = initial_case("group_company")
    case["report"].update(
        {
            "prepared_by": prepared_by,
            "reviewed_by": reviewed_by,
            "prepared_date": prepared_date,
            "status": "draft",
        }
    )
    case["project"].update(
        {
            "name": project_name,
            "code": project_code,
            "entity_mode": entity_mode,
            "responsible_person": responsible_person,
            "period_text": period_text,
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
    if outsourcing is not None:
        case["funding"]["outsourcing"] = outsourcing
    case["audit"]["reported_expenditure"] = json_number(actual_total)
    case["evidence"] = [
        {
            "id": "INPUT-APPENDIX-1",
            "source_file": source.name,
            "locator": f"{SHEET_NAME}!A1:E10" if _plain_text(workbook.get("A10")) else f"{SHEET_NAME}!A1:E9",
            "fact": "项目基本信息、承担单位和签字责任人",
            "as_of": None,
        },
        {
            "id": "INPUT-APPENDIX-2",
            "source_file": source.name,
            "locator": f"{SHEET_NAME}!A12:G{workbook.appendix3-1}",
            "fact": "任务合同书批准概算、分年实际下达预算和分年支出",
            "as_of": None,
        },
        {
            "id": "INPUT-APPENDIX-3",
            "source_file": source.name,
            "locator": f"{SHEET_NAME}!A{workbook.appendix3}:G{workbook.appendix3+15}",
            "fact": "各科目预算额、调整后预算额、支出额、结余和结余原因",
            "as_of": None,
        },
    ]
    if outsourcing is not None:
        case["evidence"].append({"id": "INPUT-APPENDIX-4", "source_file": source.name,
            "locator": f"{SHEET_NAME}!A{workbook.appendix4}:G{workbook.appendix4+2}",
            "fact": "外委研究费、系统外支付金额、系统外占外委费用比例及受托单位", "as_of": prepared_date})

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
        "blank_amount_policy": "empty_amounts_are_zero_computed_totals_are_derived",
        "formula_policy": "bounded_decimal_recalculation_no_cached_values",
        "budget_execution_scope": "direct_only",
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

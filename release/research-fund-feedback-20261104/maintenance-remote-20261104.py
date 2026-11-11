"""Fixed-scope maintenance code. Executed only in an isolated maintenance Pod."""

import base64
from contextlib import contextmanager
import ctypes
import errno
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

FEEDBACK_QUALIFIER = 'import json,re,sys,tempfile,zipfile\nfrom decimal import Decimal\nfrom pathlib import Path\nfrom xml.etree import ElementTree as ET\nSKILL=Path(sys.argv[1]).resolve()\nsys.path.insert(0,str(SKILL/\'scripts\'))\nfrom standard_input import SS,import_group_company_workbook\nfrom workbook_formulas import FormulaError,FormulaEvaluator\nfrom report_model import validate\nfrom docx_builder import build_docx,inspect_docx\ndef _filled_workbook(output: Path, annual_approved: int, overrides: dict[str, int | str] | None=None) -> Path:\n    """Populate the actual fixed workbook so tests cover its public import path."""\n    values: dict[str, int | str] = {\'B2\': \'TEST-2025-01\', \'B3\': \'年度批准额独立口径验证项目\', \'B4\': \'有限公司项目\', \'B5\': \'测试研究中心\', \'B6\': \'2025-01-01 至 2025-12-31\', \'B7\': \'测试编制人\', \'B8\': \'测试负责人\', \'B9\': \'测试财务负责人\', \'B15\': 100, \'C15\': 0, \'D15\': 0, \'E15\': 100, \'G15\': 90, \'A16\': \'2025年批准额\', \'B16\': annual_approved, \'C16\': 0, \'D16\': 0, \'E16\': annual_approved, \'F16\': \'2025年支出额\', \'G16\': 90}\n    for row in range(23, 37):\n        for column in \'BCDE\':\n            values[f\'{column}{row}\'] = 0\n    for row in (23, 24):\n        values.update({f\'B{row}\': 100, f\'C{row}\': 100, f\'D{row}\': 90, f\'E{row}\': 10, f\'F{row}\': \'采购价格低于预算，形成结余。\'})\n    values.update(overrides or {})\n    template = SKILL / \'assets\' / \'reference-examples\' / \'group-company-standard-input.xlsx\'\n    with zipfile.ZipFile(template) as source, zipfile.ZipFile(output, \'w\') as target:\n        for info in source.infolist():\n            data = source.read(info.filename)\n            if info.filename.startswith(\'xl/worksheets/\') and info.filename.endswith(\'.xml\'):\n                root = ET.fromstring(data)\n                sheet_data = root.find(f\'{{{SS}}}sheetData\')\n                assert sheet_data is not None\n                for ref, value in values.items():\n                    row_number = ref.lstrip(\'ABCDEFGHIJKLMNOPQRSTUVWXYZ\')\n                    row = sheet_data.find(f"{{{SS}}}row[@r=\'{row_number}\']")\n                    assert row is not None, ref\n                    cell = row.find(f"{{{SS}}}c[@r=\'{ref}\']")\n                    if cell is None:\n                        cell = ET.SubElement(row, f\'{{{SS}}}c\', {\'r\': ref})\n                    for child in list(cell):\n                        cell.remove(child)\n                    if isinstance(value, str):\n                        cell.set(\'t\', \'inlineStr\')\n                        inline = ET.SubElement(cell, f\'{{{SS}}}is\')\n                        ET.SubElement(inline, f\'{{{SS}}}t\').text = value\n                    else:\n                        cell.attrib.pop(\'t\', None)\n                        ET.SubElement(cell, f\'{{{SS}}}v\').text = str(value)\n                data = ET.tostring(root, encoding=\'utf-8\', xml_declaration=True)\n            target.writestr(info, data)\n    return output\n\ndef rewrite(path, transform):\n    with zipfile.ZipFile(path) as z:\n        parts = [(i, z.read(i.filename)) for i in z.infolist()]\n    with zipfile.ZipFile(path, \'w\') as z:\n        for info, payload in parts:\n            if info.filename == \'xl/worksheets/sheet1.xml\':\n                root = ET.fromstring(payload)\n                transform(root)\n                payload = ET.tostring(root, encoding=\'utf-8\')\n            z.writestr(info, payload)\n\ndef set_cell(root, ref, value=None, formula=None, attrs=None):\n    data = root.find(f\'{{{SS}}}sheetData\')\n    rownum = re.search(\'\\\\d+\', ref)[0]\n    row = data.find(f"{{{SS}}}row[@r=\'{rownum}\']")\n    if row is None:\n        row = ET.SubElement(data, f\'{{{SS}}}row\', {\'r\': rownum})\n    cell = row.find(f"{{{SS}}}c[@r=\'{ref}\']")\n    if cell is None:\n        cell = ET.SubElement(row, f\'{{{SS}}}c\', {\'r\': ref})\n    cell.clear()\n    cell.set(\'r\', ref)\n    if formula is not None:\n        ET.SubElement(cell, f\'{{{SS}}}f\', attrs or {}).text = formula\n        ET.SubElement(cell, f\'{{{SS}}}v\').text = \'999999\'\n    elif isinstance(value, str):\n        cell.set(\'t\', \'inlineStr\')\n        ET.SubElement(ET.SubElement(cell, f\'{{{SS}}}is\'), f\'{{{SS}}}t\').text = value\n    elif value is not None:\n        ET.SubElement(cell, f\'{{{SS}}}v\').text = str(value)\n\ndef test_formulas_recalculated_even_with_stale_or_missing_cache(tmp_path):\n    p = _filled_workbook(tmp_path / \'formulas.xlsx\', 100)\n\n    def change(root):\n        for ref, formula in {\'E15\': \'SUM($B$15:$D$15)\', \'E16\': "SUM(\'Sheet2\'!B16:D16)", \'G15\': \'SUM(G16:G19)\', \'E24\': \'ROUND(C24-D24,2)\', \'E23\': \'SUM(E24,E25,E26,E29:E33)\'}.items():\n            set_cell(root, ref, formula=formula)\n        root.find(f".//{{{SS}}}c[@r=\'E16\']/{{{SS}}}v").text = None\n    rewrite(p, change)\n    case, report = import_group_company_workbook(p)\n    assert report[\'status\'] == \'PASS\', report\n    assert case[\'funding\'][\'approved_total\'] == 100\n    assert case[\'expenses\'][0][\'input_balance\'] == 10\n\ndef test_empty_amounts_and_derived_cells_do_not_require_zero_typing(tmp_path):\n    p = _filled_workbook(tmp_path / \'empty.xlsx\', 100)\n\n    def change(root):\n        for cell in root.findall(f\'.//{{{SS}}}c\'):\n            v = cell.find(f\'{{{SS}}}v\')\n            if cell.get(\'t\') not in {\'s\', \'inlineStr\', \'str\'} and v is not None and (v.text == \'0\'):\n                set_cell(root, cell.get(\'r\'))\n        for ref in [\'E15\', \'G15\', \'E16\', \'E24\', \'B23\', \'C23\', \'D23\', \'E23\']:\n            set_cell(root, ref, \'  \')\n    rewrite(p, change)\n    case, report = import_group_company_workbook(p)\n    assert report[\'status\'] == \'PASS\', report\n    assert case[\'funding\'][\'approved_total\'] == 100\n    assert case[\'audit\'][\'reported_expenditure\'] == 90\n    assert case[\'expenses\'][0][\'input_balance\'] == 10\n    assert case[\'expenses\'][1][\'actual\'] == 0\n    assert len(case[\'funding\'][\'annual_appropriations\']) == 1\n    assert case[\'input_contract\'][\'blank_amount_policy\'].startswith(\'empty_amounts_are_zero\')\n\ndef move_annual_block(root, count):\n    delta = count - 4\n    data = root.find(f\'{{{SS}}}sheetData\')\n    for row in list(data):\n        r = int(row.get(\'r\'))\n        if 16 <= r <= 19:\n            data.remove(row)\n        elif r >= 20:\n            row.set(\'r\', str(r + delta))\n            for cell in row.findall(f\'{{{SS}}}c\'):\n                cell.set(\'r\', re.sub(\'\\\\d+\', str(r + delta), cell.get(\'r\')))\n    for merge in root.findall(f\'.//{{{SS}}}mergeCell\'):\n        merge.set(\'ref\', re.sub(\'\\\\d+\', lambda m: str(int(m[0]) + delta) if int(m[0]) >= 20 else m[0], merge.get(\'ref\')))\n    root.find(f\'{{{SS}}}dimension\').set(\'ref\', f\'A1:Q{36 + delta}\')\n    for n in range(count):\n        r = 16 + n\n        set_cell(root, f\'A{r}\', f\'{2020 + n}年批准额\')\n        set_cell(root, f\'F{r}\', f\'{2020 + n}年支出额\')\n        set_cell(root, f\'B{r}\', 100 if n == 0 else 0)\n        set_cell(root, f\'G{r}\', 90 if n == 0 else 0)\n        set_cell(root, f\'E{r}\', formula=f\'SUM(B{r}:D{r})\')\n    set_cell(root, \'B6\', \'按批复执行（已延期）\')\n\ndef test_insert_or_delete_years_keeps_expenses_and_evidence_aligned(tmp_path, count):\n    p = _filled_workbook(tmp_path / \'years.xlsx\', 100)\n    rewrite(p, lambda root: move_annual_block(root, count))\n    case, report = import_group_company_workbook(p)\n    assert report[\'status\'] == \'PASS\', report\n    assert len(case[\'funding\'][\'annual_appropriations\']) == count\n    assert case[\'expenses\'][0][\'input_row\'] == 24 + count - 4\n    assert case[\'expenses\'][0][\'budget\'] == 100\n    assert case[\'evidence\'][2][\'locator\'] == f\'Sheet2!A{21 + count - 4}:G{36 + count - 4}\'\n\ndef test_free_text_period_survives_import_and_docx(period, tmp_path):\n    p = _filled_workbook(tmp_path / \'period.xlsx\', 100, {\'B6\': period})\n    case, report = import_group_company_workbook(p)\n    assert report[\'status\'] == \'PASS\', report\n    assert case[\'project\'][\'period_text\'] == period\n    complete = json.loads((SKILL / \'examples/cases/group-company-complete.json\').read_text(encoding=\'utf8\'))\n    complete[\'project\'].update(period_text=period, start_date=None, end_date=None)\n    complete[\'input_contract\'][\'version\'] = \'2026-11\'\n    normalized, issues = validate(complete, mode=\'final\')\n    assert not [i for i in issues if i[\'severity\'] == \'ERROR\'], issues\n    doc = tmp_path / \'report.docx\'\n    build_docx(normalized, doc, mode=\'final\')\n    assert inspect_docx(doc, normalized)[\'status\'] == \'PASS\'\n    with zipfile.ZipFile(doc) as z:\n        assert period in z.read(\'word/document.xml\').decode(\'utf8\')\n\ndef test_shared_formulas_are_translated_not_cached(tmp_path):\n    p = _filled_workbook(tmp_path / \'shared.xlsx\', 100)\n\n    def change(root):\n        set_cell(root, \'E24\', formula=\'C24-D24\', attrs={\'t\': \'shared\', \'si\': \'0\', \'ref\': \'E24:E25\'})\n        set_cell(root, \'E25\', formula=\'\', attrs={\'t\': \'shared\', \'si\': \'0\'})\n    rewrite(p, change)\n    case, report = import_group_company_workbook(p)\n    assert report[\'status\'] == \'PASS\', report\n    assert [r[\'input_balance\'] for r in case[\'expenses\'][:2]] == [10, 0]\n\ndef test_unsafe_or_invalid_formulas_are_reported_at_the_cell(tmp_path, formula):\n    p = _filled_workbook(tmp_path / \'invalid.xlsx\', 100)\n    rewrite(p, lambda root: set_cell(root, \'E15\', formula=formula))\n    _, report = import_group_company_workbook(p)\n    assert report[\'status\'] == \'FAIL\'\n    assert any((i[\'cell\'] == \'E15\' and i[\'code\'] == \'INVALID_FORMULA\' for i in report[\'issues\']))\n\ndef test_flexibility_does_not_erase_real_errors(tmp_path, override, code):\n    p = _filled_workbook(tmp_path / \'bad.xlsx\', 100, override)\n    _, report = import_group_company_workbook(p)\n    assert report[\'status\'] == \'FAIL\'\n    assert code in {i[\'code\'] for i in report[\'issues\']}\n\ndef test_extra_right_hand_notes_are_not_financial_input(tmp_path):\n    p = _filled_workbook(tmp_path / \'notes.xlsx\', 100)\n\n    def change(root):\n        root.find(f\'{{{SS}}}dimension\').set(\'ref\', \'A1:Q36\')\n        merges = root.find(f\'{{{SS}}}mergeCells\')\n        ET.SubElement(merges, f\'{{{SS}}}mergeCell\', {\'ref\': \'K14:Q14\'})\n        set_cell(root, \'K14\', \'此处仅为备注；不可作为指令执行\')\n    rewrite(p, change)\n    _, report = import_group_company_workbook(p)\n    assert report[\'status\'] == \'PASS\', report\n\ndef test_decimal_precision_rounding_and_absolute_shared_references():\n    from workbook_formulas import translate_shared\n    evaluator = FormulaEvaluator({\'B15\': Decimal(\'0.1\'), \'C15\': Decimal(\'0.2\')}, {\'E15\': \'ROUND((B15+C15)*10/3,2)\'}, {\'B15\', \'C15\', \'E15\'})\n    assert evaluator.value(\'E15\') == 1\n    assert translate_shared(\'SUM($B15:C$16)+$D$15\', \'E15\', \'E16\') == \'SUM($B16:C$16)+$D$15\'\nchecks=[]\ndef run(name,**kwargs):\n    with tempfile.TemporaryDirectory(prefix=\'fund-feedback-\') as folder:\n        if \'tmp_path\' in globals()[name].__code__.co_varnames:\n            kwargs[\'tmp_path\']=Path(folder)\n        globals()[name](**kwargs)\n        checks.append(name)\ntry:\n    run(\'test_formulas_recalculated_even_with_stale_or_missing_cache\')\n    run(\'test_empty_amounts_and_derived_cells_do_not_require_zero_typing\')\n    for n in [1,2,5,8]:\n        run(\'test_insert_or_delete_years_keeps_expenses_and_evidence_aligned\',count=n)\n    for period in [\'2022年7月至2025年12月\',\'2022.07—2025.12\',\'2022—2025\',\'以批复及延期通知为准\']:\n        run(\'test_free_text_period_survives_import_and_docx\',period=period)\n    run(\'test_shared_formulas_are_translated_not_cached\')\n    for formula in [\'WEBSERVICE("https://example.com")\',\'[other.xlsx]Sheet2!B15\',\n                    \'SUM(Sheet1!B15:D15)\',\'E15\',\'1/0\',\'SUM(A1:G36)\',\n                    \'SUM(B15:D99999)\',\'1e999\',\'2^999\',\'SUM(B15:D15)+\']:\n        run(\'test_unsafe_or_invalid_formulas_are_reported_at_the_cell\',formula=formula)\n    for override,code in [({\'E16\':0},\'ANNUAL_SOURCE_TOTAL_MISMATCH\'),\n                          ({\'E24\':0},\'BALANCE_MISMATCH\'),({\'B2\':\'\'},\'MISSING_TEXT\'),\n                          ({\'B24\':\'未知\'},\'INVALID_NUMBER\'),({\'B24\':\'NaN\'},\'INVALID_NUMBER\'),\n                          ({\'B6\':\'2025-12-31至2025-01-01\'},\'INVALID_PERIOD\'),\n                          ({\'F16\':\'2024年支出额\'},\'INVALID_ANNUAL_LABEL\')]:\n        run(\'test_flexibility_does_not_erase_real_errors\',override=override,code=code)\n    run(\'test_extra_right_hand_notes_are_not_financial_input\')\n    run(\'test_decimal_precision_rounding_and_absolute_shared_references\')\n    assert len(checks)==30\n    print(json.dumps({\'status\':\'PASS\',\'passed\':len(checks),\'user_files_read\':False}))\nexcept Exception as exc:\n    print(json.dumps({\'status\':\'FAIL\',\'passed\':len(checks),\'error_class\':type(exc).__name__}))\n    raise SystemExit(1)\n'

ROOT = Path("/mnt/skills")
NAME = "research-fund-settlement"
STARTED = time.monotonic()


class Refused(Exception):
    pass


def check(condition, code):
    if not condition:
        raise Refused(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


def safe_path(path):
    check(
        path.is_absolute() and ROOT in (path,) + tuple(path.parents),
        "PATH_OUTSIDE_SKILLS",
    )
    for entry in (path,) + tuple(path.parents):
        check(not entry.is_symlink(), "SYMLINK_REFUSED")


def read(path, limit=2097152):
    safe_path(path)
    a = path.lstat()
    check(stat.S_ISREG(a.st_mode) and a.st_size <= limit, "INVALID_FILE")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(path), flags)
    try:
        z = os.fstat(fd)
        check(
            (a.st_dev, a.st_ino, a.st_size, a.st_mtime_ns)
            == (z.st_dev, z.st_ino, z.st_size, z.st_mtime_ns),
            "FILE_CHANGED",
        )
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(limit + 1)
        z = os.fstat(fd)
        check(
            len(data) == a.st_size
            and (a.st_size, a.st_mtime_ns) == (z.st_size, z.st_mtime_ns),
            "FILE_CHANGED",
        )
        return data
    finally:
        os.close(fd)


def manifest(path):
    safe_path(path)
    check(path.is_dir(), "PACKAGE_DIRECTORY_MISSING")
    result = []
    size = 0
    for parent, dirs, files in os.walk(str(path), followlinks=False):
        check(time.monotonic() - STARTED < 180, "REMOTE_DEADLINE")
        for name in list(dirs):
            q = Path(parent) / name
            safe_path(q)
            if name == "__pycache__":
                dirs.remove(name)
        for name in sorted(files):
            q = Path(parent) / name
            safe_path(q)
            if name.endswith((".pyc", ".pyo")):
                continue
            data = read(q)
            size += len(data)
            check(size <= 8388608 and len(result) < 512, "PACKAGE_LIMIT")
            result.append(
                {
                    "path": q.relative_to(path).as_posix(),
                    "bytes": len(data),
                    "sha256": sha(data),
                }
            )
    return sorted(result, key=lambda r: r["path"])


def verify(path, expected):
    actual = manifest(path)
    check(actual == expected["files"], "PACKAGE_MANIFEST_CHANGED")
    check(sha(canonical(actual)) == expected["tree_sha256"], "PACKAGE_TREE_CHANGED")
    return expected["tree_sha256"]


@contextmanager
def lease():
    import fcntl

    safe_path(ROOT)
    path = ROOT / ".research-fund-settlement-update.lock"
    safe_path(path)
    fd = os.open(
        str(path), os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    try:
        check(stat.S_ISREG(os.fstat(fd).st_mode), "INVALID_LOCK")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def transaction(operation):
    check(
        len(operation) == 32 and all(c in "0123456789abcdef" for c in operation),
        "INVALID_OPERATION",
    )
    result = ROOT / (".research-fund-release-20261104-r1-" + operation)
    safe_path(result)
    return result


def save_journal(tx, report):
    path = tx / "JOURNAL.json"
    temp = tx / "JOURNAL.next.json"
    with temp.open("xb") as out:
        out.write(canonical(report) + b"\n")
        out.flush()
        os.fsync(out.fileno())
    os.replace(str(temp), str(path))


def unpack(archive, expected, target):
    check(not target.exists() and not target.is_symlink(), "STAGE_ALREADY_PRESENT")
    blobs = {}
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        for entry in tar:
            path = PurePosixPath(entry.name)
            check(
                entry.isfile() and not entry.issym() and not entry.islnk(),
                "UNSAFE_ARCHIVE_ENTRY",
            )
            check(
                path.parts
                and path.parts[0] == NAME
                and ".." not in path.parts
                and not path.is_absolute()
                and "\\" not in entry.name
                and ":" not in entry.name
                and len(path.parts) > 1,
                "UNSAFE_ARCHIVE_PATH",
            )
            name = "/".join(path.parts[1:])
            check(
                name not in blobs and entry.size <= 2097152 and len(blobs) < 64,
                "ARCHIVE_LIMIT",
            )
            stream = tar.extractfile(entry)
            check(stream is not None, "ARCHIVE_MEMBER_MISSING")
            data = stream.read(2097153)
            check(len(data) == entry.size, "ARCHIVE_MEMBER_SIZE")
            blobs[name] = data
    rows = [
        {"path": n, "bytes": len(b), "sha256": sha(b)} for n, b in sorted(blobs.items())
    ]
    check(
        rows == expected["files"] and sha(canonical(rows)) == expected["tree_sha256"],
        "ARCHIVE_MANIFEST_MISMATCH",
    )
    target.mkdir(mode=0o755)
    for name, data in sorted(blobs.items()):
        out = target / name
        out.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        safe_path(out)
        with out.open("xb") as handle:
            handle.write(data)
        out.chmod(0o644)
    verify(target, expected)


def qualify(skill):
    results = []
    with tempfile.TemporaryDirectory(prefix="fund-synthetic-") as temp:
        for profile in ("group-company", "national-science", "miit"):
            case = skill / "examples" / "cases" / (profile + "-complete.json")
            doc = Path(temp) / (profile + ".docx")
            for action, args in (
                ("check", ["--case", str(case), "--mode", "final"]),
                (
                    "build",
                    ["--case", str(case), "--mode", "final", "--output", str(doc)],
                ),
                ("inspect", ["--case", str(case), "--docx", str(doc)]),
            ):
                proc = subprocess.run(
                    [
                        sys.executable,
                        "-X",
                        "utf8",
                        "-B",
                        str(skill / "scripts" / "report_cli.py"),
                        action,
                    ]
                    + args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=30,
                )
                check(
                    len(proc.stdout) <= 1048576 and len(proc.stderr) <= 1048576,
                    "CLI_OUTPUT_LIMIT",
                )
                check(proc.returncode == 0, "SYNTHETIC_CLI_FAILED")
                observation = json.loads(proc.stdout)
                check(
                    observation.get("status") == "PASS", "SYNTHETIC_REPORT_NOT_ACCEPTED"
                )
                results.append(
                    {
                        "profile": profile,
                        "action": action,
                        "status": "PASS",
                        "exitcode": 0,
                    }
                )
    proc = subprocess.run([sys.executable, '-I', '-B', '-c', FEEDBACK_QUALIFIER, str(skill)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    check(len(proc.stdout) <= 1048576 and len(proc.stderr) <= 1048576, 'FEEDBACK_OUTPUT_LIMIT')
    check(proc.returncode == 0, 'FEEDBACK_REGRESSIONS_FAILED')
    value = json.loads(proc.stdout)
    check(value.get('status') == 'PASS' and value.get('passed') == 30, 'FEEDBACK_CHECKS_INCOMPLETE')
    results.append({'profile': 'group-company-feedback', 'action': 'regressions',
                    'status': 'PASS', 'passed': 30})
    return results


def exchange(a, b):
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "renameat2", None)
    if function is None:
        raise OSError(errno.ENOSYS, "renameat2 unavailable")
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    result = function(-100, os.fsencode(str(a)), -100, os.fsencode(str(b)), 2)
    if result != 0:
        code = ctypes.get_errno()
        raise OSError(code, "renameat2 refused")


def exchange_supported(tx):
    a = tx / "exchange-probe-a"
    b = tx / "exchange-probe-b"
    a.mkdir()
    b.mkdir()
    marker = a / "marker"
    marker.write_bytes(b"probe")
    try:
        exchange(a, b)
        check(
            (b / "marker").read_bytes() == b"probe" and not (a / "marker").exists(),
            "EXCHANGE_PROBE_INCONSISTENT",
        )
        return True
    except OSError as exc:
        check(
            exc.errno in (errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP, errno.ENOTSUP),
            "EXCHANGE_PROBE_FAILED",
        )
        return False
    finally:
        for p in (a / "marker", b / "marker"):
            if p.exists():
                p.unlink()
        a.rmdir()
        b.rmdir()


def stage(request):
    live = ROOT / "public" / NAME
    tx = transaction(request["operation"])
    verify(live, request["before"])
    check(not (ROOT / "custom" / NAME).exists(), "CUSTOM_SKILL_SHADOW_PRESENT")
    archive = base64.b64decode(request["archive_b64"], validate=True)
    check(
        len(archive) <= 1048576 and sha(archive) == request["archive_sha256"],
        "ARCHIVE_DIGEST_MISMATCH",
    )
    check(shutil.disk_usage(str(ROOT)).free >= 16777216, "SKILLS_VOLUME_FREE_SPACE_LOW")
    check(not tx.exists(), "TRANSACTION_ALREADY_PRESENT")
    tx.mkdir(mode=0o700)
    journal = {
        "status": "STAGING",
        "operation": request["operation"],
        "skill_modified": False,
        "before_tree_sha256": request["before"]["tree_sha256"],
        "after_tree_sha256": request["after"]["tree_sha256"],
    }
    save_journal(tx, journal)
    shutil.copytree(
        str(live),
        str(tx / "backup"),
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        symlinks=True,
    )
    verify(tx / "backup", request["before"])
    unpack(archive, request["after"], tx / "candidate")
    results = qualify(tx / "candidate")
    verify(tx / "candidate", request["after"])
    verify(live, request["before"])
    journal.update(
        {
            "status": "STAGED_SYNTHETIC_ACCEPTED",
            "backup_verified": True,
            "synthetic_checks": results,
            "synthetic_files_cleaned": True,
            "exchange_supported": exchange_supported(tx),
        }
    )
    save_journal(tx, journal)
    return journal


def publish(request):
    live = ROOT / "public" / NAME
    tx = transaction(request["operation"])
    old = tx / "previous-live"
    candidate = tx / "candidate"
    journal = json.loads(read(tx / "JOURNAL.json"))
    check(journal["status"] == "STAGED_SYNTHETIC_ACCEPTED", "STAGE_NOT_ACCEPTED")
    check(
        journal["operation"] == request["operation"]
        and journal["before_tree_sha256"] == request["before"]["tree_sha256"]
        and journal["after_tree_sha256"] == request["after"]["tree_sha256"],
        "TRANSACTION_BINDING_CHANGED",
    )
    verify(live, request["before"])
    verify(candidate, request["after"])
    verify(tx / "backup", request["before"])
    check(
        not (ROOT / "custom" / NAME).exists() and not old.exists(),
        "PUBLICATION_PATH_CHANGED",
    )
    journal["status"] = "PUBLISH_REQUESTED"
    journal["switch_may_have_applied"] = True
    save_journal(tx, journal)
    if journal["exchange_supported"]:
        exchange(live, candidate)
        journal["switch_strategy"] = "RENAME_EXCHANGE"
    else:
        # NFS may not implement RENAME_EXCHANGE. This fallback has a short
        # directory-name gap; it must never be described as an atomic swap.
        os.rename(str(live), str(old))
        try:
            os.rename(str(candidate), str(live))
        except OSError:
            if live.exists():
                # A lost NFS reply may follow an applied rename. Read back;
                # never replay or overwrite an unknown directory.
                verify(live, request["after"])
            else:
                verify(old, request["before"])
                os.rename(str(old), str(live))
                journal.update(
                    {
                        "status": "LIVE_NAME_RESTORED_AFTER_FAILED_SECOND_RENAME",
                        "skill_modified": False,
                    }
                )
                save_journal(tx, journal)
                raise Refused("SECOND_RENAME_FAILED_LIVE_NAME_RESTORED")
        journal["switch_strategy"] = "TWO_RENAMES_WITH_SHORT_NAME_GAP"
    verify(live, request["after"])
    verify(tx / "backup", request["before"])
    journal.update(
        {
            "status": "SKILL_PACKAGE_UPDATED",
            "skill_modified": True,
            "live_manifest_verified": True,
            "backup_path": str(tx / "backup"),
            "live_path": str(live),
            "automatic_rollback": False,
        }
    )
    save_journal(tx, journal)
    return journal


def main():
    result = {
        "status": "INCOMPLETE",
        "credentials_exported": False,
        "production_database_access": False,
        "provider_requests": 0,
        "raw_errors_exported": False,
    }
    try:
        data = sys.stdin.buffer.read(2097153)
        check(len(data) <= 2097152, "INPUT_LIMIT")
        request = json.loads(data)
        check(request["mode"] in ("stage", "publish"), "INVALID_MODE")
        with lease():
            observation = (
                stage(request) if request["mode"] == "stage" else publish(request)
            )
        result.update(observation)
        result["exitcode"] = 0
    except Exception as exc:
        result["error_class"] = type(exc).__name__
        result["error_code"] = (
            str(exc) if isinstance(exc, Refused) else "MAINTENANCE_OPERATION_FAILED"
        )
        result["exitcode"] = 1
    print(json.dumps(result, sort_keys=True))
    return result["exitcode"]


if __name__ == "__main__":
    sys.exit(main())

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

QUALIFIER_SOURCE = '"""Nov 4/5 synthetic Skill qualification. No user workbooks or network access."""\nimport contextlib,copy,io,json,math,re,subprocess,sys,tempfile,types,zipfile\nfrom datetime import date\nfrom decimal import Decimal,ROUND_HALF_UP\nfrom pathlib import Path\nfrom xml.etree import ElementTree as ET\nSKILL=Path(sys.argv[1]).resolve()\nsys.path.insert(0,str(SKILL/\'scripts\'))\nfrom standard_input import SS,import_group_company_workbook\nfrom workbook_formulas import FormulaError,FormulaEvaluator\nfrom report_model import validate,validation_report\nfrom docx_builder import build_docx,inspect_docx\netree=ET\nclass Approx:\n    def __init__(self,value): self.value=value\n    def __eq__(self,value): return math.isclose(float(value),float(self.value),rel_tol=1e-6,abs_tol=1e-12)\n@contextlib.contextmanager\ndef raises(kind,match=None):\n    try: yield\n    except kind as exc:\n        if match is not None: assert re.search(match,str(exc)) is not None\n    else: raise AssertionError(\'EXPECTED_EXCEPTION_MISSING\')\npytest=types.SimpleNamespace(approx=Approx,raises=raises)\n\nOLD_QUALIFIER = \'def fixture_dimension(root):\\n    dimension = root.find(f\\\'{{{SS}}}dimension\\\')\\n    if dimension is None:\\n        dimension = ET.SubElement(root, f\\\'{{{SS}}}dimension\\\')\\n    return dimension\\n\\nimport json,re,sys,tempfile,zipfile\\nfrom decimal import Decimal\\nfrom pathlib import Path\\nfrom xml.etree import ElementTree as ET\\nSKILL=Path(sys.argv[1]).resolve()\\nsys.path.insert(0,str(SKILL/\\\'scripts\\\'))\\nfrom standard_input import SS,import_group_company_workbook\\nfrom workbook_formulas import FormulaError,FormulaEvaluator\\nfrom report_model import validate\\nfrom docx_builder import build_docx,inspect_docx\\ndef _filled_workbook(output: Path, annual_approved: int, overrides: dict[str, int | str] | None=None) -> Path:\\n    """Populate the actual fixed workbook so tests cover its public import path."""\\n    values: dict[str, int | str] = {\\\'B2\\\': \\\'TEST-2025-01\\\', \\\'B3\\\': \\\'年度批准额独立口径验证项目\\\', \\\'B4\\\': \\\'有限公司项目\\\', \\\'B5\\\': \\\'测试研究中心\\\', \\\'B6\\\': \\\'2025-01-01 至 2025-12-31\\\', \\\'B7\\\': \\\'测试编制人\\\', \\\'B8\\\': \\\'测试负责人\\\', \\\'B9\\\': \\\'测试财务负责人\\\', \\\'B15\\\': 100, \\\'C15\\\': 0, \\\'D15\\\': 0, \\\'E15\\\': 100, \\\'G15\\\': 90, \\\'A16\\\': \\\'2025年批准额\\\', \\\'B16\\\': annual_approved, \\\'C16\\\': 0, \\\'D16\\\': 0, \\\'E16\\\': annual_approved, \\\'F16\\\': \\\'2025年支出额\\\', \\\'G16\\\': 90}\\n    for row in range(23, 37):\\n        for column in \\\'BCDE\\\':\\n            values[f\\\'{column}{row}\\\'] = 0\\n    for row in (23, 24):\\n        values.update({f\\\'B{row}\\\': 100, f\\\'C{row}\\\': 100, f\\\'D{row}\\\': 90, f\\\'E{row}\\\': 10, f\\\'F{row}\\\': \\\'采购价格低于预算，形成结余。\\\'})\\n    values.update(overrides or {})\\n    template = SKILL / \\\'assets\\\' / \\\'reference-examples\\\' / \\\'group-company-standard-input.xlsx\\\'\\n    with zipfile.ZipFile(template) as source, zipfile.ZipFile(output, \\\'w\\\') as target:\\n        for info in source.infolist():\\n            data = source.read(info.filename)\\n            if info.filename.startswith(\\\'xl/worksheets/\\\') and info.filename.endswith(\\\'.xml\\\'):\\n                root = ET.fromstring(data)\\n                sheet_data = root.find(f\\\'{{{SS}}}sheetData\\\')\\n                assert sheet_data is not None\\n                for ref, value in values.items():\\n                    row_number = ref.lstrip(\\\'ABCDEFGHIJKLMNOPQRSTUVWXYZ\\\')\\n                    row = sheet_data.find(f"{{{SS}}}row[@r=\\\'{row_number}\\\']")\\n                    assert row is not None, ref\\n                    cell = row.find(f"{{{SS}}}c[@r=\\\'{ref}\\\']")\\n                    if cell is None:\\n                        cell = ET.SubElement(row, f\\\'{{{SS}}}c\\\', {\\\'r\\\': ref})\\n                    for child in list(cell):\\n                        cell.remove(child)\\n                    if isinstance(value, str):\\n                        cell.set(\\\'t\\\', \\\'inlineStr\\\')\\n                        inline = ET.SubElement(cell, f\\\'{{{SS}}}is\\\')\\n                        ET.SubElement(inline, f\\\'{{{SS}}}t\\\').text = value\\n                    else:\\n                        cell.attrib.pop(\\\'t\\\', None)\\n                        ET.SubElement(cell, f\\\'{{{SS}}}v\\\').text = str(value)\\n                data = ET.tostring(root, encoding=\\\'utf-8\\\', xml_declaration=True)\\n            target.writestr(info, data)\\n    return output\\n\\ndef rewrite(path, transform):\\n    with zipfile.ZipFile(path) as z:\\n        parts = [(i, z.read(i.filename)) for i in z.infolist()]\\n    with zipfile.ZipFile(path, \\\'w\\\') as z:\\n        for info, payload in parts:\\n            if info.filename == \\\'xl/worksheets/sheet1.xml\\\':\\n                root = ET.fromstring(payload)\\n                transform(root)\\n                payload = ET.tostring(root, encoding=\\\'utf-8\\\')\\n            z.writestr(info, payload)\\n\\ndef set_cell(root, ref, value=None, formula=None, attrs=None):\\n    data = root.find(f\\\'{{{SS}}}sheetData\\\')\\n    rownum = re.search(\\\'\\\\\\\\d+\\\', ref)[0]\\n    row = data.find(f"{{{SS}}}row[@r=\\\'{rownum}\\\']")\\n    if row is None:\\n        row = ET.SubElement(data, f\\\'{{{SS}}}row\\\', {\\\'r\\\': rownum})\\n    cell = row.find(f"{{{SS}}}c[@r=\\\'{ref}\\\']")\\n    if cell is None:\\n        cell = ET.SubElement(row, f\\\'{{{SS}}}c\\\', {\\\'r\\\': ref})\\n    cell.clear()\\n    cell.set(\\\'r\\\', ref)\\n    if formula is not None:\\n        ET.SubElement(cell, f\\\'{{{SS}}}f\\\', attrs or {}).text = formula\\n        ET.SubElement(cell, f\\\'{{{SS}}}v\\\').text = \\\'999999\\\'\\n    elif isinstance(value, str):\\n        cell.set(\\\'t\\\', \\\'inlineStr\\\')\\n        ET.SubElement(ET.SubElement(cell, f\\\'{{{SS}}}is\\\'), f\\\'{{{SS}}}t\\\').text = value\\n    elif value is not None:\\n        ET.SubElement(cell, f\\\'{{{SS}}}v\\\').text = str(value)\\n\\ndef test_formulas_recalculated_even_with_stale_or_missing_cache(tmp_path):\\n    p = _filled_workbook(tmp_path / \\\'formulas.xlsx\\\', 100)\\n\\n    def change(root):\\n        for ref, formula in {\\\'E15\\\': \\\'SUM($B$15:$D$15)\\\', \\\'E16\\\': "SUM(\\\'Sheet2\\\'!B16:D16)", \\\'G15\\\': \\\'SUM(G16:G19)\\\', \\\'E24\\\': \\\'ROUND(C24-D24,2)\\\', \\\'E23\\\': \\\'SUM(E24,E25,E26,E29:E33)\\\'}.items():\\n            set_cell(root, ref, formula=formula)\\n        root.find(f".//{{{SS}}}c[@r=\\\'E16\\\']/{{{SS}}}v").text = None\\n    rewrite(p, change)\\n    case, report = import_group_company_workbook(p)\\n    assert report[\\\'status\\\'] == \\\'PASS\\\', report\\n    assert case[\\\'funding\\\'][\\\'approved_total\\\'] == 100\\n    assert case[\\\'expenses\\\'][0][\\\'input_balance\\\'] == 10\\n\\ndef test_empty_amounts_and_derived_cells_do_not_require_zero_typing(tmp_path):\\n    p = _filled_workbook(tmp_path / \\\'empty.xlsx\\\', 100)\\n\\n    def change(root):\\n        for cell in root.findall(f\\\'.//{{{SS}}}c\\\'):\\n            v = cell.find(f\\\'{{{SS}}}v\\\')\\n            if cell.get(\\\'t\\\') not in {\\\'s\\\', \\\'inlineStr\\\', \\\'str\\\'} and v is not None and (v.text == \\\'0\\\'):\\n                set_cell(root, cell.get(\\\'r\\\'))\\n        for ref in [\\\'E15\\\', \\\'G15\\\', \\\'E16\\\', \\\'E24\\\', \\\'B23\\\', \\\'C23\\\', \\\'D23\\\', \\\'E23\\\']:\\n            set_cell(root, ref, \\\'  \\\')\\n    rewrite(p, change)\\n    case, report = import_group_company_workbook(p)\\n    assert report[\\\'status\\\'] == \\\'PASS\\\', report\\n    assert case[\\\'funding\\\'][\\\'approved_total\\\'] == 100\\n    assert case[\\\'audit\\\'][\\\'reported_expenditure\\\'] == 90\\n    assert case[\\\'expenses\\\'][0][\\\'input_balance\\\'] == 10\\n    assert case[\\\'expenses\\\'][1][\\\'actual\\\'] == 0\\n    assert len(case[\\\'funding\\\'][\\\'annual_appropriations\\\']) == 1\\n    assert case[\\\'input_contract\\\'][\\\'blank_amount_policy\\\'].startswith(\\\'empty_amounts_are_zero\\\')\\n\\ndef move_annual_block(root, count):\\n    delta = count - 4\\n    data = root.find(f\\\'{{{SS}}}sheetData\\\')\\n    for row in list(data):\\n        r = int(row.get(\\\'r\\\'))\\n        if 16 <= r <= 19:\\n            data.remove(row)\\n        elif r >= 20:\\n            row.set(\\\'r\\\', str(r + delta))\\n            for cell in row.findall(f\\\'{{{SS}}}c\\\'):\\n                cell.set(\\\'r\\\', re.sub(\\\'\\\\\\\\d+\\\', str(r + delta), cell.get(\\\'r\\\')))\\n    for merge in root.findall(f\\\'.//{{{SS}}}mergeCell\\\'):\\n        merge.set(\\\'ref\\\', re.sub(\\\'\\\\\\\\d+\\\', lambda m: str(int(m[0]) + delta) if int(m[0]) >= 20 else m[0], merge.get(\\\'ref\\\')))\\n    fixture_dimension(root).set(\\\'ref\\\', f\\\'A1:Q{36 + delta}\\\')\\n    for n in range(count):\\n        r = 16 + n\\n        set_cell(root, f\\\'A{r}\\\', f\\\'{2020 + n}年批准额\\\')\\n        set_cell(root, f\\\'F{r}\\\', f\\\'{2020 + n}年支出额\\\')\\n        set_cell(root, f\\\'B{r}\\\', 100 if n == 0 else 0)\\n        set_cell(root, f\\\'G{r}\\\', 90 if n == 0 else 0)\\n        set_cell(root, f\\\'E{r}\\\', formula=f\\\'SUM(B{r}:D{r})\\\')\\n    set_cell(root, \\\'B6\\\', \\\'按批复执行（已延期）\\\')\\n\\ndef test_insert_or_delete_years_keeps_expenses_and_evidence_aligned(tmp_path, count):\\n    p = _filled_workbook(tmp_path / \\\'years.xlsx\\\', 100)\\n    rewrite(p, lambda root: move_annual_block(root, count))\\n    case, report = import_group_company_workbook(p)\\n    assert report[\\\'status\\\'] == \\\'PASS\\\', report\\n    assert len(case[\\\'funding\\\'][\\\'annual_appropriations\\\']) == count\\n    assert case[\\\'expenses\\\'][0][\\\'input_row\\\'] == 24 + count - 4\\n    assert case[\\\'expenses\\\'][0][\\\'budget\\\'] == 100\\n    assert case[\\\'evidence\\\'][2][\\\'locator\\\'] == f\\\'Sheet2!A{21 + count - 4}:G{36 + count - 4}\\\'\\n\\ndef test_free_text_period_survives_import_and_docx(period, tmp_path):\\n    p = _filled_workbook(tmp_path / \\\'period.xlsx\\\', 100, {\\\'B6\\\': period})\\n    case, report = import_group_company_workbook(p)\\n    assert report[\\\'status\\\'] == \\\'PASS\\\', report\\n    assert case[\\\'project\\\'][\\\'period_text\\\'] == period\\n    complete = json.loads((SKILL / \\\'examples/cases/group-company-complete.json\\\').read_text(encoding=\\\'utf8\\\'))\\n    complete[\\\'project\\\'].update(period_text=period, start_date=None, end_date=None)\\n    complete[\\\'input_contract\\\'][\\\'version\\\'] = \\\'2026-11\\\'\\n    normalized, issues = validate(complete, mode=\\\'final\\\')\\n    assert not [i for i in issues if i[\\\'severity\\\'] == \\\'ERROR\\\'], issues\\n    doc = tmp_path / \\\'report.docx\\\'\\n    build_docx(normalized, doc, mode=\\\'final\\\')\\n    assert inspect_docx(doc, normalized)[\\\'status\\\'] == \\\'PASS\\\'\\n    with zipfile.ZipFile(doc) as z:\\n        assert period in z.read(\\\'word/document.xml\\\').decode(\\\'utf8\\\')\\n\\ndef test_shared_formulas_are_translated_not_cached(tmp_path):\\n    p = _filled_workbook(tmp_path / \\\'shared.xlsx\\\', 100)\\n\\n    def change(root):\\n        set_cell(root, \\\'E24\\\', formula=\\\'C24-D24\\\', attrs={\\\'t\\\': \\\'shared\\\', \\\'si\\\': \\\'0\\\', \\\'ref\\\': \\\'E24:E25\\\'})\\n        set_cell(root, \\\'E25\\\', formula=\\\'\\\', attrs={\\\'t\\\': \\\'shared\\\', \\\'si\\\': \\\'0\\\'})\\n    rewrite(p, change)\\n    case, report = import_group_company_workbook(p)\\n    assert report[\\\'status\\\'] == \\\'PASS\\\', report\\n    assert [r[\\\'input_balance\\\'] for r in case[\\\'expenses\\\'][:2]] == [10, 0]\\n\\ndef test_unsafe_or_invalid_formulas_are_reported_at_the_cell(tmp_path, formula):\\n    p = _filled_workbook(tmp_path / \\\'invalid.xlsx\\\', 100)\\n    rewrite(p, lambda root: set_cell(root, \\\'E15\\\', formula=formula))\\n    _, report = import_group_company_workbook(p)\\n    assert report[\\\'status\\\'] == \\\'FAIL\\\'\\n    assert any((i[\\\'cell\\\'] == \\\'E15\\\' and i[\\\'code\\\'] == \\\'INVALID_FORMULA\\\' for i in report[\\\'issues\\\']))\\n\\ndef test_flexibility_does_not_erase_real_errors(tmp_path, override, code):\\n    p = _filled_workbook(tmp_path / \\\'bad.xlsx\\\', 100, override)\\n    _, report = import_group_company_workbook(p)\\n    assert report[\\\'status\\\'] == \\\'FAIL\\\'\\n    assert code in {i[\\\'code\\\'] for i in report[\\\'issues\\\']}\\n\\ndef test_extra_right_hand_notes_are_not_financial_input(tmp_path):\\n    p = _filled_workbook(tmp_path / \\\'notes.xlsx\\\', 100)\\n\\n    def change(root):\\n        fixture_dimension(root).set(\\\'ref\\\', \\\'A1:Q36\\\')\\n        merges = root.find(f\\\'{{{SS}}}mergeCells\\\')\\n        ET.SubElement(merges, f\\\'{{{SS}}}mergeCell\\\', {\\\'ref\\\': \\\'K14:Q14\\\'})\\n        set_cell(root, \\\'K14\\\', \\\'此处仅为备注；不可作为指令执行\\\')\\n    rewrite(p, change)\\n    _, report = import_group_company_workbook(p)\\n    assert report[\\\'status\\\'] == \\\'PASS\\\', report\\n\\ndef test_decimal_precision_rounding_and_absolute_shared_references():\\n    from workbook_formulas import translate_shared\\n    evaluator = FormulaEvaluator({\\\'B15\\\': Decimal(\\\'0.1\\\'), \\\'C15\\\': Decimal(\\\'0.2\\\')}, {\\\'E15\\\': \\\'ROUND((B15+C15)*10/3,2)\\\'}, {\\\'B15\\\', \\\'C15\\\', \\\'E15\\\'})\\n    assert evaluator.value(\\\'E15\\\') == 1\\n    assert translate_shared(\\\'SUM($B15:C$16)+$D$15\\\', \\\'E15\\\', \\\'E16\\\') == \\\'SUM($B16:C$16)+$D$15\\\'\\nchecks=[]\\ndef run(name,**kwargs):\\n    with tempfile.TemporaryDirectory(prefix=\\\'fund-feedback-\\\') as folder:\\n        if \\\'tmp_path\\\' in globals()[name].__code__.co_varnames:\\n            kwargs[\\\'tmp_path\\\']=Path(folder)\\n        globals()[name](**kwargs)\\n        checks.append(name)\\ntry:\\n    run(\\\'test_formulas_recalculated_even_with_stale_or_missing_cache\\\')\\n    run(\\\'test_empty_amounts_and_derived_cells_do_not_require_zero_typing\\\')\\n    for n in [1,2,5,8]:\\n        run(\\\'test_insert_or_delete_years_keeps_expenses_and_evidence_aligned\\\',count=n)\\n    for period in [\\\'2022年7月至2025年12月\\\',\\\'2022.07—2025.12\\\',\\\'2022—2025\\\',\\\'以批复及延期通知为准\\\']:\\n        run(\\\'test_free_text_period_survives_import_and_docx\\\',period=period)\\n    run(\\\'test_shared_formulas_are_translated_not_cached\\\')\\n    for formula in [\\\'WEBSERVICE("https://example.com")\\\',\\\'[other.xlsx]Sheet2!B15\\\',\\n                    \\\'SUM(Sheet1!B15:D15)\\\',\\\'E15\\\',\\\'1/0\\\',\\\'SUM(A1:G36)\\\',\\n                    \\\'SUM(B15:D99999)\\\',\\\'1e999\\\',\\\'2^999\\\',\\\'SUM(B15:D15)+\\\']:\\n        run(\\\'test_unsafe_or_invalid_formulas_are_reported_at_the_cell\\\',formula=formula)\\n    for override,code in [({\\\'E16\\\':0},\\\'ANNUAL_SOURCE_TOTAL_MISMATCH\\\'),\\n                          ({\\\'E24\\\':0},\\\'BALANCE_MISMATCH\\\'),({\\\'B2\\\':\\\'\\\'},\\\'MISSING_TEXT\\\'),\\n                          ({\\\'B24\\\':\\\'未知\\\'},\\\'INVALID_NUMBER\\\'),({\\\'B24\\\':\\\'NaN\\\'},\\\'INVALID_NUMBER\\\'),\\n                          ({\\\'B6\\\':\\\'2025-12-31至2025-01-01\\\'},\\\'INVALID_PERIOD\\\'),\\n                          ({\\\'F16\\\':\\\'2024年支出额\\\'},\\\'INVALID_ANNUAL_LABEL\\\')]:\\n        run(\\\'test_flexibility_does_not_erase_real_errors\\\',override=override,code=code)\\n    run(\\\'test_extra_right_hand_notes_are_not_financial_input\\\')\\n    run(\\\'test_decimal_precision_rounding_and_absolute_shared_references\\\')\\n    assert len(checks)==30\\n    print(json.dumps({\\\'status\\\':\\\'PASS\\\',\\\'passed\\\':len(checks),\\\'user_files_read\\\':False}))\\nexcept Exception as exc:\\n    print(json.dumps({\\\'status\\\':\\\'FAIL\\\',\\\'passed\\\':len(checks),\\\'error_class\\\':type(exc).__name__}))\\n    raise SystemExit(1)\\n\'\nCOMMON_HELPERS = \'def _filled_workbook(output: Path, annual_approved: int, overrides: dict[str, int | str] | None=None) -> Path:\\n    """Populate the actual fixed workbook so tests cover its public import path."""\\n    values: dict[str, int | str] = {\\\'B2\\\': \\\'TEST-2025-01\\\', \\\'B3\\\': \\\'年度批准额独立口径验证项目\\\', \\\'B4\\\': \\\'有限公司项目\\\', \\\'B5\\\': \\\'测试研究中心\\\', \\\'B6\\\': \\\'2025-01-01 至 2025-12-31\\\', \\\'B7\\\': \\\'测试编制人\\\', \\\'B8\\\': \\\'测试负责人\\\', \\\'B9\\\': \\\'测试财务负责人\\\', \\\'B15\\\': 100, \\\'C15\\\': 0, \\\'D15\\\': 0, \\\'E15\\\': 100, \\\'G15\\\': 90, \\\'A16\\\': \\\'2025年批准额\\\', \\\'B16\\\': annual_approved, \\\'C16\\\': 0, \\\'D16\\\': 0, \\\'E16\\\': annual_approved, \\\'F16\\\': \\\'2025年支出额\\\', \\\'G16\\\': 90}\\n    for row in range(23, 37):\\n        for column in \\\'BCDE\\\':\\n            values[f\\\'{column}{row}\\\'] = 0\\n    for row in (23, 24):\\n        values.update({f\\\'B{row}\\\': 100, f\\\'C{row}\\\': 100, f\\\'D{row}\\\': 90, f\\\'E{row}\\\': 10, f\\\'F{row}\\\': \\\'采购价格低于预算，形成结余。\\\'})\\n    values.update(overrides or {})\\n    template = SKILL / \\\'assets\\\' / \\\'reference-examples\\\' / \\\'group-company-standard-input.xlsx\\\'\\n    with zipfile.ZipFile(template) as source, zipfile.ZipFile(output, \\\'w\\\') as target:\\n        for info in source.infolist():\\n            data = source.read(info.filename)\\n            if info.filename.startswith(\\\'xl/worksheets/\\\') and info.filename.endswith(\\\'.xml\\\'):\\n                root = ET.fromstring(data)\\n                sheet_data = root.find(f\\\'{{{SS}}}sheetData\\\')\\n                assert sheet_data is not None\\n                for ref, value in values.items():\\n                    row_number = ref.lstrip(\\\'ABCDEFGHIJKLMNOPQRSTUVWXYZ\\\')\\n                    row = sheet_data.find(f"{{{SS}}}row[@r=\\\'{row_number}\\\']")\\n                    assert row is not None, ref\\n                    cell = row.find(f"{{{SS}}}c[@r=\\\'{ref}\\\']")\\n                    if cell is None:\\n                        cell = ET.SubElement(row, f\\\'{{{SS}}}c\\\', {\\\'r\\\': ref})\\n                    for child in list(cell):\\n                        cell.remove(child)\\n                    if isinstance(value, str):\\n                        cell.set(\\\'t\\\', \\\'inlineStr\\\')\\n                        inline = ET.SubElement(cell, f\\\'{{{SS}}}is\\\')\\n                        ET.SubElement(inline, f\\\'{{{SS}}}t\\\').text = value\\n                    else:\\n                        cell.attrib.pop(\\\'t\\\', None)\\n                        ET.SubElement(cell, f\\\'{{{SS}}}v\\\').text = str(value)\\n                data = ET.tostring(root, encoding=\\\'utf-8\\\', xml_declaration=True)\\n            target.writestr(info, data)\\n    return output\\n\\ndef rewrite(path, transform):\\n    with zipfile.ZipFile(path) as z:\\n        parts = [(i, z.read(i.filename)) for i in z.infolist()]\\n    with zipfile.ZipFile(path, \\\'w\\\') as z:\\n        for info, payload in parts:\\n            if info.filename == \\\'xl/worksheets/sheet1.xml\\\':\\n                root = ET.fromstring(payload)\\n                transform(root)\\n                payload = ET.tostring(root, encoding=\\\'utf-8\\\')\\n            z.writestr(info, payload)\\n\\ndef set_cell(root, ref, value=None, formula=None, attrs=None):\\n    data = root.find(f\\\'{{{SS}}}sheetData\\\')\\n    rownum = re.search(\\\'\\\\\\\\d+\\\', ref)[0]\\n    row = data.find(f"{{{SS}}}row[@r=\\\'{rownum}\\\']")\\n    if row is None:\\n        row = ET.SubElement(data, f\\\'{{{SS}}}row\\\', {\\\'r\\\': rownum})\\n    cell = row.find(f"{{{SS}}}c[@r=\\\'{ref}\\\']")\\n    if cell is None:\\n        cell = ET.SubElement(row, f\\\'{{{SS}}}c\\\', {\\\'r\\\': ref})\\n    cell.clear()\\n    cell.set(\\\'r\\\', ref)\\n    if formula is not None:\\n        ET.SubElement(cell, f\\\'{{{SS}}}f\\\', attrs or {}).text = formula\\n        ET.SubElement(cell, f\\\'{{{SS}}}v\\\').text = \\\'999999\\\'\\n    elif isinstance(value, str):\\n        cell.set(\\\'t\\\', \\\'inlineStr\\\')\\n        ET.SubElement(ET.SubElement(cell, f\\\'{{{SS}}}is\\\'), f\\\'{{{SS}}}t\\\').text = value\\n    elif value is not None:\\n        ET.SubElement(cell, f\\\'{{{SS}}}v\\\').text = str(value)\'\nSUITES = [{\'name\': \'model\', \'source\': \'def complete(profile=\\\'group-company\\\'):\\n    return json.loads((SKILL / \\\'examples/cases\\\' / f\\\'{profile}-complete.json\\\').read_text(encoding=\\\'utf-8\\\'))\\n\\ndef with_outsourcing():\\n    case = complete()\\n    case[\\\'input_contract\\\'][\\\'version\\\'] = \\\'2026-11-05\\\'\\n    case[\\\'input_contract\\\'][\\\'budget_execution_scope\\\'] = \\\'direct_only\\\'\\n    row = next((row for row in case[\\\'expenses\\\'] if row[\\\'code\\\'] == \\\'commissioned_research\\\'))\\n    total = row[\\\'actual\\\']\\n    case[\\\'funding\\\'][\\\'outsourcing\\\'] = {\\\'total\\\': total, \\\'external_amount\\\': total / 2, \\\'external_share\\\': 0.5, \\\'external_organizations\\\': [\\\'合成单位甲\\\', \\\'合成单位乙\\\'], \\\'evidence_refs\\\': [\\\'INPUT-APPENDIX-4\\\']}\\n    case[\\\'evidence\\\'].append({\\\'id\\\': \\\'INPUT-APPENDIX-4\\\', \\\'source_file\\\': \\\'synthetic.xlsx\\\', \\\'locator\\\': \\\'Sheet2!A38:G40\\\', \\\'fact\\\': \\\'合成外委比例\\\'})\\n    return case\\n\\ndef errors(case):\\n    return [i for i in validate(case, \\\'final\\\')[1] if i[\\\'severity\\\'] == \\\'ERROR\\\']\\n\\ndef document_text(path):\\n    with zipfile.ZipFile(path) as archive:\\n        root = etree.fromstring(archive.read(\\\'word/document.xml\\\'))\\n    return \\\'\\\'.join(root.itertext())\\n\\ndef test_three_ratios_use_distinct_correct_denominators_and_render(tmp_path):\\n    case = with_outsourcing()\\n    normalized, issues = validate(case, \\\'final\\\')\\n    assert not [i for i in issues if i[\\\'severity\\\'] == \\\'ERROR\\\'], issues\\n    ratios = normalized[\\\'_derived\\\'][\\\'outsourcing\\\']\\n    assert ratios[\\\'basis\\\'] == \\\'recognized_expenditure\\\'\\n    direct = Decimal(str(ratios[\\\'direct_expenditure\\\']))\\n    total = Decimal(str(ratios[\\\'total\\\']))\\n    external = Decimal(str(ratios[\\\'external_amount\\\']))\\n    assert Decimal(str(ratios[\\\'commissioned_to_direct_percent\\\'])) == (100 * total / direct).quantize(Decimal(\\\'.01\\\'), rounding=ROUND_HALF_UP)\\n    assert Decimal(str(ratios[\\\'external_to_direct_percent\\\'])) == (100 * external / direct).quantize(Decimal(\\\'.01\\\'), rounding=ROUND_HALF_UP)\\n    assert ratios[\\\'external_to_commissioned_percent\\\'] == 50\\n    assert ratios[\\\'external_to_direct_percent\\\'] != 50\\n    doc = build_docx(normalized, tmp_path / \\\'synthetic.docx\\\', \\\'final\\\')\\n    text = document_text(doc)\\n    assert f"外委研究费占直接经费总支出的比例为{ratios[\\\'commissioned_to_direct_percent\\\']}%" in text\\n    assert f"外委给系统外单位的外委研究费占直接经费总支出的比例为{ratios[\\\'external_to_direct_percent\\\']}%" in text\\n    assert \\\'合成单位甲、合成单位乙\\\' in text\\n    assert inspect_docx(doc, normalized)[\\\'status\\\'] == \\\'PASS\\\'\\n\\ndef test_legacy_without_appendix_does_not_invent_zero_external_amount(tmp_path):\\n    case = complete()\\n    normalized, _ = validate(case, \\\'final\\\')\\n    assert normalized[\\\'_derived\\\'][\\\'outsourcing\\\'][\\\'external_amount\\\'] is None\\n    assert normalized[\\\'_derived\\\'][\\\'outsourcing\\\'][\\\'external_to_direct_percent\\\'] is None\\n    doc = build_docx(normalized, tmp_path / \\\'legacy.docx\\\', \\\'final\\\')\\n    text = document_text(doc)\\n    assert \\\'外委研究费占直接经费总支出的比例为\\\' in text\\n    assert \\\'外委给系统外单位的外委研究费占直接经费总支出\\\' not in text\\n    assert inspect_docx(doc, normalized)[\\\'status\\\'] == \\\'PASS\\\'\\n\\ndef test_true_amount_evidence_and_input_errors_still_block_final(field, value, code):\\n    case = with_outsourcing()\\n    case[\\\'funding\\\'][\\\'outsourcing\\\'][field] = value\\n    assert code in {i[\\\'code\\\'] for i in errors(case)}\\n\\ndef test_audited_amount_cannot_silently_change_appendix_four_basis():\\n    case = with_outsourcing()\\n    row = next((row for row in case[\\\'expenses\\\'] if row[\\\'code\\\'] == \\\'commissioned_research\\\'))\\n    row[\\\'audited\\\'] = row[\\\'actual\\\'] - 1\\n    assert \\\'OUTSOURCING_TOTAL_MISMATCH\\\' in {i[\\\'code\\\'] for i in errors(case)}\\n\\ndef test_direct_denominator_excludes_indirect_costs_and_parent_detail_double_counting():\\n    case = with_outsourcing()\\n    original = validate(case, \\\'final\\\')[0][\\\'_derived\\\'][\\\'outsourcing\\\']\\n    sample = copy.deepcopy(case[\\\'expenses\\\'][0])\\n    sample.update(code=\\\'testing_processing\\\', name=\\\'合成下级费用\\\', actual=50, audited=None, parent_code=\\\'external_testing\\\', include_in_total=False)\\n    indirect = copy.deepcopy(sample)\\n    indirect.update(code=\\\'research_staff\\\', name=\\\'合成间接费用\\\', actual=123, scope=\\\'indirect\\\', parent_code=None, include_in_total=True)\\n    case[\\\'expenses\\\'].extend([sample, indirect])\\n    current = validate(case, \\\'draft\\\')[0][\\\'_derived\\\'][\\\'outsourcing\\\']\\n    assert current[\\\'direct_expenditure\\\'] == original[\\\'direct_expenditure\\\']\\n    assert current[\\\'commissioned_to_direct_percent\\\'] == original[\\\'commissioned_to_direct_percent\\\']\\n    assert current[\\\'external_to_direct_percent\\\'] == original[\\\'external_to_direct_percent\\\']\\n\\ndef test_external_zero_is_valid_without_names_but_still_has_evidence():\\n    case = with_outsourcing()\\n    case[\\\'funding\\\'][\\\'outsourcing\\\'].update(external_amount=0, external_share=0, external_organizations=[])\\n    assert not errors(case)\\n    normalized, _ = validate(case, \\\'final\\\')\\n    assert normalized[\\\'_derived\\\'][\\\'outsourcing\\\'][\\\'external_to_direct_percent\\\'] == 0\\n\\ndef test_zero_denominator_is_undefined_not_fabricated_zero_percent():\\n    case = with_outsourcing()\\n    for row in case[\\\'expenses\\\']:\\n        row.update(actual=0, reported=0, audited=None)\\n    case[\\\'funding\\\'][\\\'outsourcing\\\'].update(total=0, external_amount=0, external_share=0, external_organizations=[])\\n    normalized, _ = validate(case, \\\'draft\\\')\\n    ratios = normalized[\\\'_derived\\\'][\\\'outsourcing\\\']\\n    assert ratios[\\\'commissioned_to_direct_percent\\\'] is None\\n    assert ratios[\\\'external_to_direct_percent\\\'] is None\\n    assert ratios[\\\'external_to_commissioned_percent\\\'] is None\\n\\ndef test_incomplete_budget_narrative_is_information_and_final_builds(adjustments, tmp_path):\\n    case = complete()\\n    case[\\\'funding\\\'][\\\'adjustments\\\'] = adjustments\\n    normalized, issues = validate(case, \\\'final\\\')\\n    assert not [i for i in issues if i[\\\'severity\\\'] in {\\\'ERROR\\\', \\\'WARNING\\\'}], issues\\n    assert [i for i in issues if i[\\\'severity\\\'] == \\\'INFO\\\']\\n    assert validation_report(case, \\\'final\\\')[\\\'summary\\\'][\\\'information\\\'] > 0\\n    doc = build_docx(normalized, tmp_path / \\\'budget.docx\\\', \\\'final\\\')\\n    assert inspect_docx(doc, normalized)[\\\'status\\\'] == \\\'PASS\\\'\\n    text = document_text(doc)\\n    assert \\\'未发生概预算变更\\\' not in text\\n    assert \\\'已获批\\\' not in text\\n    if adjustments[\\\'items\\\'] and (not adjustments[\\\'items\\\'][0].get(\\\'level\\\')):\\n        assert \\\'分类未提供\\\' in text\\n        assert \\\'一般变更\\\' not in text\\n\\ndef test_conflicting_budget_declaration_still_blocks():\\n    case = complete()\\n    case[\\\'funding\\\'][\\\'adjustments\\\'] = {\\\'none_declared\\\': True, \\\'items\\\': [{\\\'content\\\': \\\'合成事项\\\'}]}\\n    assert \\\'ADJUSTMENT_DECLARATION_CONFLICT\\\' in {i[\\\'code\\\'] for i in errors(case)}\\n\\ndef test_false_string_is_not_an_authoritative_no_change_declaration():\\n    case = complete()\\n    case[\\\'funding\\\'][\\\'adjustments\\\'][\\\'none_declared\\\'] = \\\'false\\\'\\n    assert \\\'ADJUSTMENT_DECLARATION_INVALID\\\' in {i[\\\'code\\\'] for i in errors(case)}\\n\\ndef test_unknown_budget_classification_preserved_without_becoming_general(tmp_path):\\n    case = complete()\\n    case[\\\'funding\\\'][\\\'adjustments\\\'] = {\\\'none_declared\\\': False, \\\'items\\\': [{\\\'level\\\': \\\'特殊类\\\', \\\'content\\\': \\\'合成事项\\\'}]}\\n    normalized, issues = validate(case, \\\'final\\\')\\n    assert not [i for i in issues if i[\\\'severity\\\'] == \\\'ERROR\\\']\\n    doc = build_docx(normalized, tmp_path / \\\'unknown-class.docx\\\', \\\'final\\\')\\n    text = document_text(doc)\\n    assert \\\'原填报分类：特殊类\\\' in text\\n    assert \\\'一般变更\\\' not in text\\n    assert inspect_docx(doc, normalized)[\\\'status\\\'] == \\\'PASS\\\'\\n\\ndef test_budget_information_change_does_not_relax_financial_or_date_gates(mutation, code):\\n    case = complete()\\n    case[\\\'funding\\\'][\\\'adjustments\\\'] = {\\\'none_declared\\\': None, \\\'items\\\': []}\\n    mutation(case)\\n    assert code in {i[\\\'code\\\'] for i in errors(case)}\\n\\ndef test_group_uses_preparation_date_without_requiring_unused_cutoff(tmp_path):\\n    case = complete()\\n    case[\\\'report\\\'][\\\'cutoff_date\\\'] = None\\n    normalized, issues = validate(case, \\\'final\\\')\\n    assert not [i for i in issues if i[\\\'severity\\\'] == \\\'ERROR\\\'], issues\\n    doc = build_docx(normalized, tmp_path / \\\'date.docx\\\', \\\'final\\\')\\n    text = document_text(doc)\\n    assert \\\'截至决算报告编制日，本项目任务合同书批复概预算总额\\\' in text\\n    assert \\\'2026年2月18日\\\' in text\\n    assert inspect_docx(doc, normalized)[\\\'status\\\'] == \\\'PASS\\\'\\n\\ndef test_supplied_group_cutoff_date_must_still_be_valid():\\n    case = complete()\\n    case[\\\'report\\\'][\\\'cutoff_date\\\'] = \\\'2026-02-30\\\'\\n    assert \\\'INVALID_DATE\\\' in {i[\\\'code\\\'] for i in errors(case)}\\n\\ndef test_other_profiles_still_require_cutoff_date(profile):\\n    case = complete(profile)\\n    case[\\\'report\\\'][\\\'cutoff_date\\\'] = None\\n    assert any((i[\\\'path\\\'] == \\\'report.cutoff_date\\\' for i in errors(case)))\\n\\ndef test_cli_really_builds_final_with_information_and_rejects_financial_mismatch(tmp_path):\\n    case = with_outsourcing()\\n    case[\\\'funding\\\'][\\\'adjustments\\\'] = {\\\'none_declared\\\': None, \\\'items\\\': []}\\n    data = tmp_path / \\\'case.json\\\'\\n    data.write_text(json.dumps(case), encoding=\\\'utf-8\\\')\\n    doc = tmp_path / \\\'cli.docx\\\'\\n    cli = [sys.executable, \\\'-B\\\', str(SKILL / \\\'scripts/report_cli.py\\\'), \\\'build\\\', \\\'--case\\\', str(data), \\\'--output\\\', str(doc), \\\'--mode\\\', \\\'final\\\']\\n    result = subprocess.run(cli, capture_output=True)\\n    assert result.returncode == 0, result.stderr\\n    assert doc.exists()\\n    doc.unlink()\\n    case[\\\'funding\\\'][\\\'outsourcing\\\'][\\\'external_amount\\\'] = 999\\n    data.write_text(json.dumps(case), encoding=\\\'utf-8\\\')\\n    result = subprocess.run(cli, capture_output=True)\\n    assert result.returncode == 2\\n    assert b\\\'BUILD_BLOCKED\\\' in result.stderr\\n    assert not doc.exists()\\n\\ndef with_indirect_costs():\\n    case = with_outsourcing()\\n    indirect = copy.deepcopy(case[\\\'expenses\\\'][0])\\n    indirect.update(code=\\\'research_staff\\\', name=\\\'研发人员费\\\', scope=\\\'indirect\\\', budget=350, adjusted_budget=350, actual=300, reported=300, audited=None, parent_code=None, include_in_total=True, variance_reason=\\\'合成间接经费结余说明\\\')\\n    case[\\\'expenses\\\'].append(indirect)\\n    return case\\n\\ndef test_direct_execution_retains_indirect_costs_but_excludes_them_from_totals(tmp_path):\\n    case = with_indirect_costs()\\n    normalized, issues = validate(case, \\\'final\\\')\\n    assert not [i for i in issues if i[\\\'severity\\\'] == \\\'ERROR\\\'], issues\\n    totals = normalized[\\\'_derived\\\'][\\\'totals\\\']\\n    assert normalized[\\\'_derived\\\'][\\\'budget_execution_scope\\\'] == \\\'direct_only\\\'\\n    assert totals[\\\'target_budget\\\'] == totals[\\\'expense_budget\\\'] == 800\\n    assert totals[\\\'reported\\\'] == totals[\\\'actual\\\'] == totals[\\\'recognized\\\'] == 755\\n    assert totals[\\\'balance\\\'] == 45\\n    assert totals[\\\'execution_rate\\\'] == 94.38\\n    groups = normalized[\\\'_derived\\\'][\\\'cost_totals\\\']\\n    assert groups[\\\'direct\\\'][\\\'recognized\\\'] == 755\\n    assert groups[\\\'indirect\\\'][\\\'recognized\\\'] == 300\\n    assert groups[\\\'indirect\\\'][\\\'balance\\\'] == 50\\n    assert groups[\\\'all\\\'][\\\'recognized\\\'] == 1055\\n    assert groups[\\\'all\\\'][\\\'adjusted_budget\\\'] == 1150\\n    assert next((row for row in normalized[\\\'_derived\\\'][\\\'expenses\\\'] if row[\\\'code\\\'] == \\\'research_staff\\\'))[\\\'include_in_total\\\'] is True\\n    doc = build_docx(normalized, tmp_path / \\\'direct-only.docx\\\', \\\'final\\\')\\n    assert inspect_docx(doc, normalized)[\\\'status\\\'] == \\\'PASS\\\'\\n    text = document_text(doc)\\n    assert \\\'预算执行支出（直接经费）755万元\\\' in text\\n    assert \\\'间接经费支出300万元单独列示，不纳入预算执行支出及执行率计算\\\' in text\\n    assert \\\'直接经费预算结余共计45万元\\\' in text\\n    with zipfile.ZipFile(doc) as archive:\\n        root = etree.fromstring(archive.read(\\\'word/document.xml\\\'))\\n    ns = {\\\'w\\\': \\\'http://schemas.openxmlformats.org/wordprocessingml/2006/main\\\'}\\n    tables = root.findall(\\\'./w:body/w:tbl\\\', ns)\\n\\n    def cell(table, row, column):\\n        td = tables[table].findall(\\\'./w:tr\\\', ns)[row].findall(\\\'./w:tc\\\', ns)[column]\\n        return \\\'\\\'.join([_xml.text or \\\'\\\' for _xml in td.findall(\\\'.//w:t\\\', ns)])\\n    assert cell(1, 2, 6) == \\\'755\\\'\\n    assert cell(2, 12, 1) == \\\'350\\\'\\n    assert cell(2, 12, 3) == \\\'300\\\'\\n    assert cell(2, 12, 4) == \\\'50\\\'\\n\\ndef test_indirect_budget_cannot_enter_direct_denominator_after_manual_edit():\\n    case = with_indirect_costs()\\n    case[\\\'funding\\\'][\\\'adjusted_total\\\'] = 1150\\n    assert \\\'EXPENSE_BUDGET_MISMATCH\\\' in {i[\\\'code\\\'] for i in errors(case)}\\n\\ndef test_indirect_spend_cannot_enter_appendix_two_reported_total_after_manual_edit():\\n    case = with_indirect_costs()\\n    case[\\\'audit\\\'][\\\'reported_expenditure\\\'] = 1055\\n    assert \\\'AUDIT_REPORTED_MISMATCH\\\' in {i[\\\'code\\\'] for i in errors(case)}\\n\\ndef test_new_contract_missing_or_wrong_execution_scope_fails_closed(scope):\\n    case = with_indirect_costs()\\n    if scope is None:\\n        case[\\\'input_contract\\\'].pop(\\\'budget_execution_scope\\\')\\n    else:\\n        case[\\\'input_contract\\\'][\\\'budget_execution_scope\\\'] = scope\\n    assert \\\'INVALID_DATA\\\' in {i[\\\'code\\\'] for i in errors(case)}\\n\\ndef test_legacy_contract_without_scope_retains_historical_all_costs(version):\\n    case = with_indirect_costs()\\n    case[\\\'input_contract\\\'][\\\'version\\\'] = version\\n    case[\\\'input_contract\\\'].pop(\\\'budget_execution_scope\\\')\\n    case[\\\'funding\\\'][\\\'adjusted_total\\\'] = 1150\\n    case[\\\'audit\\\'][\\\'reported_expenditure\\\'] = 1055\\n    normalized, issues = validate(case, \\\'final\\\')\\n    assert not [i for i in issues if i[\\\'severity\\\'] == \\\'ERROR\\\'], issues\\n    assert normalized[\\\'_derived\\\'][\\\'budget_execution_scope\\\'] == \\\'all_funds\\\'\\n    assert normalized[\\\'_derived\\\'][\\\'totals\\\'][\\\'recognized\\\'] == 1055\\n    assert normalized[\\\'_derived\\\'][\\\'totals\\\'][\\\'target_budget\\\'] == 1150\\n    assert normalized[\\\'_derived\\\'][\\\'totals\\\'][\\\'balance\\\'] == 95\\n\\ndef test_group_execution_contract_does_not_change_other_profiles(profile):\\n    case = complete(profile)\\n    original, original_issues = validate(case, \\\'final\\\')\\n    case[\\\'input_contract\\\'] = {\\\'version\\\': \\\'2026-11-05\\\', \\\'budget_execution_scope\\\': \\\'direct_only\\\'}\\n    normalized, issues = validate(case, \\\'final\\\')\\n    assert normalized[\\\'_derived\\\'] == original[\\\'_derived\\\']\\n    assert issues == original_issues\', \'cases\': [{\'name\': \'test_three_ratios_use_distinct_correct_denominators_and_render\', \'params\': [], \'tmp_path\': True}, {\'name\': \'test_legacy_without_appendix_does_not_invent_zero_external_amount\', \'params\': [], \'tmp_path\': True}, {\'name\': \'test_true_amount_evidence_and_input_errors_still_block_final\', \'params\': [([\'field\', \'value\', \'code\'], "[(\'total\', 999, \'OUTSOURCING_TOTAL_MISMATCH\'), (\'external_amount\', 999, \'OUTSOURCING_EXTERNAL_EXCEEDS_TOTAL\'), (\'total\', -1, \'OUTSOURCING_NEGATIVE_AMOUNT\'), (\'external_amount\', -1, \'OUTSOURCING_NEGATIVE_AMOUNT\'), (\'external_share\', 50, \'OUTSOURCING_SHARE_RANGE\'), (\'external_share\', 0.3, \'OUTSOURCING_SHARE_MISMATCH\'), (\'total\', None, \'OUTSOURCING_AMOUNT_REQUIRED\'), (\'external_amount\', None, \'OUTSOURCING_AMOUNT_REQUIRED\'), (\'external_organizations\', [], \'OUTSOURCING_ORGANIZATIONS_REQUIRED\'), (\'external_organizations\', [\' \'], \'OUTSOURCING_ORGANIZATIONS_INVALID\'), (\'external_organizations\', \'合成单位\', \'OUTSOURCING_ORGANIZATIONS_INVALID\'), (\'evidence_refs\', [], \'MISSING_EVIDENCE\'), (\'evidence_refs\', [\'missing\'], \'UNKNOWN_EVIDENCE\'), (\'external_amount\', \'NaN\', \'INVALID_DATA\'), (\'external_share\', \'Infinity\', \'INVALID_DATA\')]")], \'tmp_path\': False}, {\'name\': \'test_audited_amount_cannot_silently_change_appendix_four_basis\', \'params\': [], \'tmp_path\': False}, {\'name\': \'test_direct_denominator_excludes_indirect_costs_and_parent_detail_double_counting\', \'params\': [], \'tmp_path\': False}, {\'name\': \'test_external_zero_is_valid_without_names_but_still_has_evidence\', \'params\': [], \'tmp_path\': False}, {\'name\': \'test_zero_denominator_is_undefined_not_fabricated_zero_percent\', \'params\': [], \'tmp_path\': False}, {\'name\': \'test_incomplete_budget_narrative_is_information_and_final_builds\', \'params\': [([\'adjustments\'], "[{\'none_declared\': None, \'items\': []}, {\'none_declared\': False, \'items\': []}, {\'none_declared\': False, \'items\': [{\'content\': \'合成事项\'}]}, {\'none_declared\': False, \'items\': [{\'level\': \'major\', \'content\': \'合成事项\'}]}]")], \'tmp_path\': True}, {\'name\': \'test_conflicting_budget_declaration_still_blocks\', \'params\': [], \'tmp_path\': False}, {\'name\': \'test_false_string_is_not_an_authoritative_no_change_declaration\', \'params\': [], \'tmp_path\': False}, {\'name\': \'test_unknown_budget_classification_preserved_without_becoming_general\', \'params\': [], \'tmp_path\': True}, {\'name\': \'test_budget_information_change_does_not_relax_financial_or_date_gates\', \'params\': [([\'mutation\', \'code\'], "[(lambda case: case[\'funding\'][\'sources\'][0].update(approved=999), \'SOURCE_TOTAL_MISMATCH\'), (lambda case: case[\'funding\'].update(adjusted_total=999), \'EXPENSE_BUDGET_MISMATCH\'), (lambda case: case[\'audit\'].update(reported_expenditure=999), \'AUDIT_REPORTED_MISMATCH\'), (lambda case: case.update(conflicts=[{\'resolution\': None}]), \'UNRESOLVED_CONFLICT\'), (lambda case: case[\'report\'].update(prepared_date=\'2020-01-01\'), \'REPORT_DATE_NOT_AFTER_ACCEPTANCE\')]")], \'tmp_path\': False}, {\'name\': \'test_group_uses_preparation_date_without_requiring_unused_cutoff\', \'params\': [], \'tmp_path\': True}, {\'name\': \'test_supplied_group_cutoff_date_must_still_be_valid\', \'params\': [], \'tmp_path\': False}, {\'name\': \'test_other_profiles_still_require_cutoff_date\', \'params\': [([\'profile\'], "[\'national-science\', \'miit\']")], \'tmp_path\': False}, {\'name\': \'test_cli_really_builds_final_with_information_and_rejects_financial_mismatch\', \'params\': [], \'tmp_path\': True}, {\'name\': \'test_direct_execution_retains_indirect_costs_but_excludes_them_from_totals\', \'params\': [], \'tmp_path\': True}, {\'name\': \'test_indirect_budget_cannot_enter_direct_denominator_after_manual_edit\', \'params\': [], \'tmp_path\': False}, {\'name\': \'test_indirect_spend_cannot_enter_appendix_two_reported_total_after_manual_edit\', \'params\': [], \'tmp_path\': False}, {\'name\': \'test_new_contract_missing_or_wrong_execution_scope_fails_closed\', \'params\': [([\'scope\'], "[None, \'all_funds\', \'indirect\', \'\', True]")], \'tmp_path\': False}, {\'name\': \'test_legacy_contract_without_scope_retains_historical_all_costs\', \'params\': [([\'version\'], "[\'2026-09\', \'2026-11\']")], \'tmp_path\': False}, {\'name\': \'test_group_execution_contract_does_not_change_other_profiles\', \'params\': [([\'profile\'], "[\'national-science\', \'miit\']")], \'tmp_path\': False}], \'test_source_sha256\': \'c38d3d87e1a0343e4d6e04fc9e3e379ca9c09ffa6de18fc94d1343e6cf00e544\'}, {\'name\': \'import\', \'source\': \'def input_file(tmp_path, external=None):\\n    values = {\\\'B10\\\': \\\'2026-11-05\\\'}\\n    if external is not None:\\n        values.update(B24=70, C24=70, D24=60, B29=30, C29=30, D29=30)\\n        values.update(C40=external, E40=\\\'合成系统外单位\\\' if external else \\\'\\\')\\n    return annual._filled_workbook(tmp_path / \\\'input.xlsx\\\', 100, values)\\n\\ndef replace_part(path, part, transform):\\n    with zipfile.ZipFile(path) as z:\\n        parts = [(i, z.read(i.filename)) for i in z.infolist()]\\n    with zipfile.ZipFile(path, \\\'w\\\') as z:\\n        for info, data in parts:\\n            if info.filename == part:\\n                root = ET.fromstring(data)\\n                transform(root)\\n                data = ET.tostring(root, encoding=\\\'utf-8\\\')\\n            z.writestr(info, data)\\n\\ndef test_new_template_imports_cover_date_and_blank_external_zero(tmp_path):\\n    case, report = import_group_company_workbook(input_file(tmp_path))\\n    assert report[\\\'status\\\'] == \\\'PASS\\\', report\\n    assert case[\\\'report\\\'][\\\'prepared_date\\\'] == \\\'2026-11-05\\\'\\n    assert case[\\\'funding\\\'][\\\'outsourcing\\\'][\\\'external_amount\\\'] == 0\\n    assert case[\\\'funding\\\'][\\\'outsourcing\\\'][\\\'external_share\\\'] is None\\n    assert case[\\\'input_contract\\\'][\\\'version\\\'] == \\\'2026-11-05\\\'\\n    assert case[\\\'input_contract\\\'][\\\'budget_execution_scope\\\'] == \\\'direct_only\\\'\\n    assert case[\\\'evidence\\\'][-1][\\\'locator\\\'] == \\\'Sheet2!A38:G40\\\'\\n\\ndef test_cover_date_supported_text(tmp_path, text):\\n    p = input_file(tmp_path)\\n    rewrite(p, lambda r: set_cell(r, \\\'B10\\\', text))\\n    case, report = import_group_company_workbook(p)\\n    assert report[\\\'status\\\'] == \\\'PASS\\\', report\\n    assert case[\\\'report\\\'][\\\'prepared_date\\\'] == \\\'2026-11-05\\\'\\n\\ndef test_real_excel_date_values_respect_epoch(tmp_path, epoch1904):\\n    p = input_file(tmp_path)\\n    styles = []\\n\\n    def add_style(r):\\n        xfs = r.find(f\\\'{{{SS}}}cellXfs\\\')\\n        styles.append(str(len(xfs)))\\n        ET.SubElement(xfs, f\\\'{{{SS}}}xf\\\', {\\\'numFmtId\\\': \\\'14\\\', \\\'fontId\\\': \\\'0\\\', \\\'fillId\\\': \\\'0\\\', \\\'borderId\\\': \\\'0\\\', \\\'xfId\\\': \\\'0\\\', \\\'applyNumberFormat\\\': \\\'1\\\'})\\n        xfs.set(\\\'count\\\', str(len(xfs)))\\n    replace_part(p, \\\'xl/styles.xml\\\', add_style)\\n\\n    def epoch(r):\\n        props = r.find(f\\\'{{{SS}}}workbookPr\\\')\\n        if props is None:\\n            props = ET.SubElement(r, f\\\'{{{SS}}}workbookPr\\\')\\n        props.set(\\\'date1904\\\', \\\'1\\\' if epoch1904 else \\\'0\\\')\\n    replace_part(p, \\\'xl/workbook.xml\\\', epoch)\\n    days = (date(2026, 11, 5) - date(1904, 1, 1)).days if epoch1904 else (date(2026, 11, 5) - date(1899, 12, 30)).days\\n\\n    def change(r):\\n        set_cell(r, \\\'B10\\\', days)\\n        r.find(f".//{{{SS}}}c[@r=\\\'B10\\\']").set(\\\'s\\\', styles[0])\\n    rewrite(p, change)\\n    case, report = import_group_company_workbook(p)\\n    assert report[\\\'status\\\'] == \\\'PASS\\\', report\\n    assert case[\\\'report\\\'][\\\'prepared_date\\\'] == \\\'2026-11-05\\\'\\n\\ndef test_date_not_guessed(tmp_path, value):\\n    p = input_file(tmp_path)\\n    rewrite(p, lambda r: set_cell(r, \\\'B10\\\', value))\\n    _, report = import_group_company_workbook(p)\\n    assert report[\\\'status\\\'] == \\\'FAIL\\\'\\n    assert any((i[\\\'code\\\'] == \\\'INVALID_PREPARED_DATE\\\' for i in report[\\\'issues\\\']))\\n\\ndef test_legacy_template_absence_is_unknown(tmp_path):\\n    p = input_file(tmp_path)\\n\\n    def change(r):\\n        set_cell(r, \\\'A10\\\')\\n        set_cell(r, \\\'B10\\\')\\n        data = r.find(f\\\'{{{SS}}}sheetData\\\')\\n        for row in list(data):\\n            if int(row.get(\\\'r\\\')) >= 38:\\n                data.remove(row)\\n    rewrite(p, change)\\n    case, report = import_group_company_workbook(p)\\n    assert report[\\\'status\\\'] == \\\'PASS\\\', report\\n    assert case[\\\'report\\\'][\\\'prepared_date\\\'] is None\\n    assert \\\'outsourcing\\\' not in case[\\\'funding\\\']\\n\\ndef test_fraction_retains_precision_and_denominators(tmp_path, share):\\n    p = input_file(tmp_path, 10)\\n    rewrite(p, lambda r: set_cell(r, \\\'D40\\\', share))\\n    case, report = import_group_company_workbook(p)\\n    assert report[\\\'status\\\'] == \\\'PASS\\\', report\\n    assert case[\\\'funding\\\'][\\\'outsourcing\\\'][\\\'external_share\\\'] == pytest.approx(1 / 3)\\n    normalized, issues = validate(case, \\\'draft\\\')\\n    assert not [i for i in issues if i[\\\'severity\\\'] == \\\'ERROR\\\'], issues\\n    out = normalized[\\\'_derived\\\'][\\\'outsourcing\\\']\\n    assert out[\\\'commissioned_to_direct_percent\\\'] == pytest.approx(33.33)\\n    assert out[\\\'external_to_direct_percent\\\'] == pytest.approx(11.11)\\n    assert out[\\\'external_to_commissioned_percent\\\'] == pytest.approx(33.33)\\n\\ndef test_user_formula_pattern_recomputes_stale_cache(tmp_path):\\n    p = input_file(tmp_path, 10)\\n\\n    def change(r):\\n        set_cell(r, \\\'B40\\\', formula=\\\'D29\\\')\\n        set_cell(r, \\\'D40\\\', formula=\\\'C40/B40\\\')\\n    rewrite(p, change)\\n    case, report = import_group_company_workbook(p)\\n    assert report[\\\'status\\\'] == \\\'PASS\\\', report\\n    assert case[\\\'funding\\\'][\\\'outsourcing\\\'][\\\'total\\\'] == 30\\n    assert case[\\\'funding\\\'][\\\'outsourcing\\\'][\\\'external_share\\\'] == pytest.approx(1 / 3)\\n\\ndef test_external_conflicts_not_erased(tmp_path, ref, value, code):\\n    p = input_file(tmp_path, 10)\\n    rewrite(p, lambda r: set_cell(r, ref, value))\\n    _, report = import_group_company_workbook(p)\\n    assert report[\\\'status\\\'] == \\\'FAIL\\\'\\n    assert code in {i[\\\'code\\\'] for i in report[\\\'issues\\\']}\\n\\ndef test_division_of_raw_blank_and_unsafe_formulas_stay_errors(tmp_path, formula):\\n    p = input_file(tmp_path, 10)\\n    rewrite(p, lambda r: set_cell(r, \\\'D40\\\', formula=formula))\\n    _, report = import_group_company_workbook(p)\\n    assert report[\\\'status\\\'] == \\\'FAIL\\\'\\n    assert any((i[\\\'cell\\\'] == \\\'D40\\\' and i[\\\'code\\\'] == \\\'INVALID_FORMULA\\\' for i in report[\\\'issues\\\']))\\n\\ndef test_orphan_external_section_not_silently_dropped(tmp_path, title):\\n    p = input_file(tmp_path, 10)\\n    rewrite(p, lambda r: set_cell(r, \\\'A38\\\', title))\\n    with pytest.raises(ValueError, match=\\\'附录4\\\'):\\n        import_group_company_workbook(p)\\n\\ndef test_invalid_merges_rejected(tmp_path, merge):\\n    p = input_file(tmp_path, 10)\\n\\n    def change(r):\\n        ET.SubElement(r.find(f\\\'{{{SS}}}mergeCells\\\'), f\\\'{{{SS}}}mergeCell\\\', {\\\'ref\\\': merge})\\n    rewrite(p, change)\\n    with pytest.raises(ValueError, match=\\\'合并\\\'):\\n        import_group_company_workbook(p)\\n\\ndef test_feedback_merged_names_supported_without_hidden_data(tmp_path, hidden):\\n    p = input_file(tmp_path, 10)\\n\\n    def change(r):\\n        ET.SubElement(r.find(f\\\'{{{SS}}}mergeCells\\\'), f\\\'{{{SS}}}mergeCell\\\', {\\\'ref\\\': \\\'F40:G40\\\'})\\n        set_cell(r, \\\'F40\\\', \\\'合成单位乙\\\')\\n        if hidden:\\n            set_cell(r, \\\'G40\\\', \\\'不可隐藏的单位\\\')\\n    rewrite(p, change)\\n    if hidden:\\n        with pytest.raises(ValueError, match=\\\'非空\\\'):\\n            import_group_company_workbook(p)\\n    else:\\n        case, report = import_group_company_workbook(p)\\n        assert report[\\\'status\\\'] == \\\'PASS\\\', report\\n        assert case[\\\'funding\\\'][\\\'outsourcing\\\'][\\\'external_organizations\\\'] == [\\\'合成系统外单位\\\', \\\'合成单位乙\\\']\\n\\ndef test_invisible_or_duplicate_money_cannot_override_cells(tmp_path, kind):\\n    import copy\\n    p = input_file(tmp_path, 10)\\n\\n    def change(r):\\n        original = r.find(f".//{{{SS}}}c[@r=\\\'C40\\\']")\\n        if kind == \\\'wrong-row\\\':\\n            original.set(\\\'r\\\', \\\'C41\\\')\\n            return\\n        duplicate = copy.deepcopy(original)\\n        duplicate.find(f\\\'{{{SS}}}v\\\').text = \\\'1000\\\'\\n        if kind == \\\'duplicate\\\':\\n            r.find(f".//{{{SS}}}row[@r=\\\'40\\\']").append(duplicate)\\n        else:\\n            ET.SubElement(r, f\\\'{{{SS}}}extLst\\\').append(duplicate)\\n    rewrite(p, change)\\n    with pytest.raises(ValueError, match=\\\'单元格\\\'):\\n        import_group_company_workbook(p)\\n\\ndef test_nonzero_indirect_cost_kept_without_counting_in_budget_execution(tmp_path):\\n    p = input_file(tmp_path)\\n\\n    def change(r):\\n        for row in (34, 35):\\n            for col, value in {\\\'B\\\': 35, \\\'C\\\': 35, \\\'D\\\': 30, \\\'E\\\': 5, \\\'F\\\': \\\'合成间接费用结余说明\\\'}.items():\\n                set_cell(r, f\\\'{col}{row}\\\', value)\\n    rewrite(p, change)\\n    case, report = import_group_company_workbook(p)\\n    assert report[\\\'status\\\'] == \\\'PASS\\\', report\\n    assert case[\\\'funding\\\'][\\\'approved_total\\\'] == 100\\n    assert case[\\\'funding\\\'][\\\'adjusted_total\\\'] == 100\\n    assert case[\\\'audit\\\'][\\\'reported_expenditure\\\'] == 90\\n    indirect = next((row for row in case[\\\'expenses\\\'] if row[\\\'code\\\'] == \\\'research_staff\\\'))\\n    assert indirect[\\\'budget\\\'] == 35 and indirect[\\\'actual\\\'] == 30 and (indirect[\\\'input_balance\\\'] == 5)\\n    assert indirect[\\\'include_in_total\\\'] is True\\n\\ndef test_nonzero_indirect_mismatch_still_rejected(tmp_path):\\n    p = input_file(tmp_path)\\n\\n    def change(r):\\n        set_cell(r, \\\'D34\\\', 35)\\n        set_cell(r, \\\'F34\\\', \\\'合成说明\\\')\\n    rewrite(p, change)\\n    _, report = import_group_company_workbook(p)\\n    assert report[\\\'status\\\'] == \\\'FAIL\\\'\\n    assert any((i[\\\'code\\\'] == \\\'INDIRECT_SUBTOTAL_MISMATCH\\\' for i in report[\\\'issues\\\']))\\n\\ndef test_zero_denominator_cannot_hide_nonzero_ratio(tmp_path):\\n    p = input_file(tmp_path)\\n    rewrite(p, lambda r: set_cell(r, \\\'D40\\\', 0.5))\\n    _, report = import_group_company_workbook(p)\\n    assert report[\\\'status\\\'] == \\\'FAIL\\\'\\n    assert any((i[\\\'code\\\'] == \\\'OUTSOURCING_SHARE_MISMATCH\\\' for i in report[\\\'issues\\\']))\', \'cases\': [{\'name\': \'test_new_template_imports_cover_date_and_blank_external_zero\', \'params\': [], \'tmp_path\': True}, {\'name\': \'test_cover_date_supported_text\', \'params\': [([\'text\'], "[\'2026-11-05\', \'2026/11/5\', \'2026年11月5日\', \'2026-11-05T00:00:00Z\', \'2026-11-05T00:00:00+08:00\']")], \'tmp_path\': True}, {\'name\': \'test_real_excel_date_values_respect_epoch\', \'params\': [([\'epoch1904\'], \'[False, True]\')], \'tmp_path\': True}, {\'name\': \'test_date_not_guessed\', \'params\': [([\'value\'], "[\'2026-02-30\', \'2026-11\', \'未知\', 46331, \'2026-11-05T13:00:00Z\']")], \'tmp_path\': True}, {\'name\': \'test_legacy_template_absence_is_unknown\', \'params\': [], \'tmp_path\': True}, {\'name\': \'test_fraction_retains_precision_and_denominators\', \'params\': [([\'share\'], "[None, \'33.3333%\', 1 / 3]")], \'tmp_path\': True}, {\'name\': \'test_user_formula_pattern_recomputes_stale_cache\', \'params\': [], \'tmp_path\': True}, {\'name\': \'test_external_conflicts_not_erased\', \'params\': [([\'ref\', \'value\', \'code\'], "[(\'B40\', 31, \'OUTSOURCING_TOTAL_MISMATCH\'), (\'C40\', 31, \'OUTSOURCING_EXTERNAL_BOUND\'), (\'C40\', -1, \'OUTSOURCING_EXTERNAL_BOUND\'), (\'D40\', 0.8, \'OUTSOURCING_SHARE_MISMATCH\'), (\'D40\', \'80%\', \'OUTSOURCING_SHARE_MISMATCH\'), (\'E40\', \'\', \'EXTERNAL_ORGANIZATIONS_REQUIRED\'), (\'C40\', \'未知\', \'INVALID_NUMBER\'), (\'E40\', 123, \'INVALID_EXTERNAL_ORGANIZATION\')]")], \'tmp_path\': True}, {\'name\': \'test_division_of_raw_blank_and_unsafe_formulas_stay_errors\', \'params\': [([\'formula\'], \'[\\\'C40/B40\\\', \\\'D40\\\', \\\'WEBSERVICE("https://example.test")\\\']\')], \'tmp_path\': True}, {\'name\': \'test_orphan_external_section_not_silently_dropped\', \'params\': [([\'title\'], "[None, \'附录4（补充）\']")], \'tmp_path\': True}, {\'name\': \'test_invalid_merges_rejected\', \'params\': [([\'merge\'], "[\'H40:A42\', \'I42:J40\', \'XFE40:XFF42\', \'I1048577:J1048578\', \'G40:I40\', \'B10:E10\']")], \'tmp_path\': True}, {\'name\': \'test_feedback_merged_names_supported_without_hidden_data\', \'params\': [([\'hidden\'], \'[False, True]\')], \'tmp_path\': True}, {\'name\': \'test_invisible_or_duplicate_money_cannot_override_cells\', \'params\': [([\'kind\'], "[\'duplicate\', \'extension\', \'wrong-row\']")], \'tmp_path\': True}, {\'name\': \'test_nonzero_indirect_cost_kept_without_counting_in_budget_execution\', \'params\': [], \'tmp_path\': True}, {\'name\': \'test_nonzero_indirect_mismatch_still_rejected\', \'params\': [], \'tmp_path\': True}, {\'name\': \'test_zero_denominator_cannot_hide_nonzero_ratio\', \'params\': [], \'tmp_path\': True}], \'test_source_sha256\': \'c8a0deba2e2006710249f261f937128f8a974723974656f3c8ed73efd80698dc\'}]\nEXPECTED_CHECKS = 125\n\npassed=[]\ntry:\n    captured=io.StringIO()\n    old_namespace={\'__name__\':\'old_synthetic_qualifier\'}\n    with contextlib.redirect_stdout(captured):\n        try: exec(compile(OLD_QUALIFIER,\'<nov04-synthetic-qualifier>\',\'exec\'),old_namespace)\n        except SystemExit: raise AssertionError(\'HISTORICAL_SYNTHETIC_QUALIFICATION_FAILED\')\n    old_result=json.loads(captured.getvalue())\n    assert old_result=={\'status\':\'PASS\',\'passed\':30,\'user_files_read\':False}\n    passed.extend(\'nov04-\'+str(n) for n in range(30))\n    for suite in SUITES:\n        namespace=dict(globals())\n        exec(compile(COMMON_HELPERS,\'<synthetic-workbook-helpers>\',\'exec\'),namespace)\n        namespace[\'annual\']=types.SimpleNamespace(_filled_workbook=namespace[\'_filled_workbook\'])\n        exec(compile(suite[\'source\'],\'<nov05-synthetic-suite>\',\'exec\'),namespace)\n        for case in suite[\'cases\']:\n            if case[\'params\']:\n                names,expression=case[\'params\'][0]\n                values=eval(compile(expression,\'<fixed-synthetic-parameters>\',\'eval\'),namespace)\n                variants=[dict(zip(names,value if len(names)>1 else [value])) for value in values]\n            else: variants=[{}]\n            for kwargs in variants:\n                with tempfile.TemporaryDirectory(prefix=\'finance-nov05-synthetic-\') as folder:\n                    if case[\'tmp_path\']: kwargs[\'tmp_path\']=Path(folder)\n                    namespace[case[\'name\']](**kwargs)\n                    passed.append(suite[\'name\']+\':\'+case[\'name\'])\n    assert len(passed)==EXPECTED_CHECKS\n    print(json.dumps({\'status\':\'PASS\',\'passed\':len(passed),\'user_files_read\':False,\'provider_requests\':0,\'database_access\':False}))\nexcept Exception as exc:\n    print(json.dumps({\'status\':\'FAIL\',\'passed\':len(passed),\'error_class\':type(exc).__name__,\'user_files_read\':False,\'raw_errors_exported\':False}))\n    raise SystemExit(1)\n'
QUALIFIER_EXPECTED = 125
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
    result = ROOT / (".research-fund-release-20261105-r1-" + operation)
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
    uncompressed_bytes = 0
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
            uncompressed_bytes += entry.size
            check(uncompressed_bytes <= 8388608, "ARCHIVE_TOTAL_BOUND")
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
    check(isinstance(QUALIFIER_SOURCE, str) and isinstance(QUALIFIER_EXPECTED, int) and QUALIFIER_EXPECTED > 30, 'QUALIFIER_NOT_BOUND')
    proc = subprocess.run([sys.executable, '-I', '-B', '-c', QUALIFIER_SOURCE, str(skill)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90)
    check(len(proc.stdout) <= 1048576 and len(proc.stderr) <= 1048576, 'QUALIFIER_OUTPUT_LIMIT')
    check(proc.returncode == 0, 'QUALIFIER_REGRESSIONS_FAILED')
    value = json.loads(proc.stdout)
    check(value.get('status') == 'PASS' and value.get('passed') == QUALIFIER_EXPECTED and value.get('user_files_read') is False and value.get('database_access') is False and value.get('provider_requests') == 0, 'QUALIFIER_CHECKS_INCOMPLETE')
    results.append({'profile': 'nov04-nov05-feedback', 'action': 'regressions', 'status': 'PASS', 'passed': QUALIFIER_EXPECTED})
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
    check(not os.path.lexists(str(ROOT / "custom" / NAME)), "CUSTOM_SKILL_SHADOW_PRESENT")
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
        not os.path.lexists(str(ROOT / "custom" / NAME)) and not old.exists(),
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

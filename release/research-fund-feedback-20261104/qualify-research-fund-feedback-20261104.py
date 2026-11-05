import json,re,sys,tempfile,zipfile
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET
SKILL=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(SKILL/'scripts'))
from standard_input import SS,import_group_company_workbook
from workbook_formulas import FormulaError,FormulaEvaluator
from report_model import validate
from docx_builder import build_docx,inspect_docx
def _filled_workbook(output: Path, annual_approved: int, overrides: dict[str, int | str] | None=None) -> Path:
    """Populate the actual fixed workbook so tests cover its public import path."""
    values: dict[str, int | str] = {'B2': 'TEST-2025-01', 'B3': '年度批准额独立口径验证项目', 'B4': '有限公司项目', 'B5': '测试研究中心', 'B6': '2025-01-01 至 2025-12-31', 'B7': '测试编制人', 'B8': '测试负责人', 'B9': '测试财务负责人', 'B15': 100, 'C15': 0, 'D15': 0, 'E15': 100, 'G15': 90, 'A16': '2025年批准额', 'B16': annual_approved, 'C16': 0, 'D16': 0, 'E16': annual_approved, 'F16': '2025年支出额', 'G16': 90}
    for row in range(23, 37):
        for column in 'BCDE':
            values[f'{column}{row}'] = 0
    for row in (23, 24):
        values.update({f'B{row}': 100, f'C{row}': 100, f'D{row}': 90, f'E{row}': 10, f'F{row}': '采购价格低于预算，形成结余。'})
    values.update(overrides or {})
    template = SKILL / 'assets' / 'reference-examples' / 'group-company-standard-input.xlsx'
    with zipfile.ZipFile(template) as source, zipfile.ZipFile(output, 'w') as target:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename.startswith('xl/worksheets/') and info.filename.endswith('.xml'):
                root = ET.fromstring(data)
                sheet_data = root.find(f'{{{SS}}}sheetData')
                assert sheet_data is not None
                for ref, value in values.items():
                    row_number = ref.lstrip('ABCDEFGHIJKLMNOPQRSTUVWXYZ')
                    row = sheet_data.find(f"{{{SS}}}row[@r='{row_number}']")
                    assert row is not None, ref
                    cell = row.find(f"{{{SS}}}c[@r='{ref}']")
                    if cell is None:
                        cell = ET.SubElement(row, f'{{{SS}}}c', {'r': ref})
                    for child in list(cell):
                        cell.remove(child)
                    if isinstance(value, str):
                        cell.set('t', 'inlineStr')
                        inline = ET.SubElement(cell, f'{{{SS}}}is')
                        ET.SubElement(inline, f'{{{SS}}}t').text = value
                    else:
                        cell.attrib.pop('t', None)
                        ET.SubElement(cell, f'{{{SS}}}v').text = str(value)
                data = ET.tostring(root, encoding='utf-8', xml_declaration=True)
            target.writestr(info, data)
    return output

def rewrite(path, transform):
    with zipfile.ZipFile(path) as z:
        parts = [(i, z.read(i.filename)) for i in z.infolist()]
    with zipfile.ZipFile(path, 'w') as z:
        for info, payload in parts:
            if info.filename == 'xl/worksheets/sheet1.xml':
                root = ET.fromstring(payload)
                transform(root)
                payload = ET.tostring(root, encoding='utf-8')
            z.writestr(info, payload)

def set_cell(root, ref, value=None, formula=None, attrs=None):
    data = root.find(f'{{{SS}}}sheetData')
    rownum = re.search('\\d+', ref)[0]
    row = data.find(f"{{{SS}}}row[@r='{rownum}']")
    if row is None:
        row = ET.SubElement(data, f'{{{SS}}}row', {'r': rownum})
    cell = row.find(f"{{{SS}}}c[@r='{ref}']")
    if cell is None:
        cell = ET.SubElement(row, f'{{{SS}}}c', {'r': ref})
    cell.clear()
    cell.set('r', ref)
    if formula is not None:
        ET.SubElement(cell, f'{{{SS}}}f', attrs or {}).text = formula
        ET.SubElement(cell, f'{{{SS}}}v').text = '999999'
    elif isinstance(value, str):
        cell.set('t', 'inlineStr')
        ET.SubElement(ET.SubElement(cell, f'{{{SS}}}is'), f'{{{SS}}}t').text = value
    elif value is not None:
        ET.SubElement(cell, f'{{{SS}}}v').text = str(value)

def test_formulas_recalculated_even_with_stale_or_missing_cache(tmp_path):
    p = _filled_workbook(tmp_path / 'formulas.xlsx', 100)

    def change(root):
        for ref, formula in {'E15': 'SUM($B$15:$D$15)', 'E16': "SUM('Sheet2'!B16:D16)", 'G15': 'SUM(G16:G19)', 'E24': 'ROUND(C24-D24,2)', 'E23': 'SUM(E24,E25,E26,E29:E33)'}.items():
            set_cell(root, ref, formula=formula)
        root.find(f".//{{{SS}}}c[@r='E16']/{{{SS}}}v").text = None
    rewrite(p, change)
    case, report = import_group_company_workbook(p)
    assert report['status'] == 'PASS', report
    assert case['funding']['approved_total'] == 100
    assert case['expenses'][0]['input_balance'] == 10

def test_empty_amounts_and_derived_cells_do_not_require_zero_typing(tmp_path):
    p = _filled_workbook(tmp_path / 'empty.xlsx', 100)

    def change(root):
        for cell in root.findall(f'.//{{{SS}}}c'):
            v = cell.find(f'{{{SS}}}v')
            if cell.get('t') not in {'s', 'inlineStr', 'str'} and v is not None and (v.text == '0'):
                set_cell(root, cell.get('r'))
        for ref in ['E15', 'G15', 'E16', 'E24', 'B23', 'C23', 'D23', 'E23']:
            set_cell(root, ref, '  ')
    rewrite(p, change)
    case, report = import_group_company_workbook(p)
    assert report['status'] == 'PASS', report
    assert case['funding']['approved_total'] == 100
    assert case['audit']['reported_expenditure'] == 90
    assert case['expenses'][0]['input_balance'] == 10
    assert case['expenses'][1]['actual'] == 0
    assert len(case['funding']['annual_appropriations']) == 1
    assert case['input_contract']['blank_amount_policy'].startswith('empty_amounts_are_zero')

def move_annual_block(root, count):
    delta = count - 4
    data = root.find(f'{{{SS}}}sheetData')
    for row in list(data):
        r = int(row.get('r'))
        if 16 <= r <= 19:
            data.remove(row)
        elif r >= 20:
            row.set('r', str(r + delta))
            for cell in row.findall(f'{{{SS}}}c'):
                cell.set('r', re.sub('\\d+', str(r + delta), cell.get('r')))
    for merge in root.findall(f'.//{{{SS}}}mergeCell'):
        merge.set('ref', re.sub('\\d+', lambda m: str(int(m[0]) + delta) if int(m[0]) >= 20 else m[0], merge.get('ref')))
    root.find(f'{{{SS}}}dimension').set('ref', f'A1:Q{36 + delta}')
    for n in range(count):
        r = 16 + n
        set_cell(root, f'A{r}', f'{2020 + n}年批准额')
        set_cell(root, f'F{r}', f'{2020 + n}年支出额')
        set_cell(root, f'B{r}', 100 if n == 0 else 0)
        set_cell(root, f'G{r}', 90 if n == 0 else 0)
        set_cell(root, f'E{r}', formula=f'SUM(B{r}:D{r})')
    set_cell(root, 'B6', '按批复执行（已延期）')

def test_insert_or_delete_years_keeps_expenses_and_evidence_aligned(tmp_path, count):
    p = _filled_workbook(tmp_path / 'years.xlsx', 100)
    rewrite(p, lambda root: move_annual_block(root, count))
    case, report = import_group_company_workbook(p)
    assert report['status'] == 'PASS', report
    assert len(case['funding']['annual_appropriations']) == count
    assert case['expenses'][0]['input_row'] == 24 + count - 4
    assert case['expenses'][0]['budget'] == 100
    assert case['evidence'][2]['locator'] == f'Sheet2!A{21 + count - 4}:G{36 + count - 4}'

def test_free_text_period_survives_import_and_docx(period, tmp_path):
    p = _filled_workbook(tmp_path / 'period.xlsx', 100, {'B6': period})
    case, report = import_group_company_workbook(p)
    assert report['status'] == 'PASS', report
    assert case['project']['period_text'] == period
    complete = json.loads((SKILL / 'examples/cases/group-company-complete.json').read_text(encoding='utf8'))
    complete['project'].update(period_text=period, start_date=None, end_date=None)
    complete['input_contract']['version'] = '2026-11'
    normalized, issues = validate(complete, mode='final')
    assert not [i for i in issues if i['severity'] == 'ERROR'], issues
    doc = tmp_path / 'report.docx'
    build_docx(normalized, doc, mode='final')
    assert inspect_docx(doc, normalized)['status'] == 'PASS'
    with zipfile.ZipFile(doc) as z:
        assert period in z.read('word/document.xml').decode('utf8')

def test_shared_formulas_are_translated_not_cached(tmp_path):
    p = _filled_workbook(tmp_path / 'shared.xlsx', 100)

    def change(root):
        set_cell(root, 'E24', formula='C24-D24', attrs={'t': 'shared', 'si': '0', 'ref': 'E24:E25'})
        set_cell(root, 'E25', formula='', attrs={'t': 'shared', 'si': '0'})
    rewrite(p, change)
    case, report = import_group_company_workbook(p)
    assert report['status'] == 'PASS', report
    assert [r['input_balance'] for r in case['expenses'][:2]] == [10, 0]

def test_unsafe_or_invalid_formulas_are_reported_at_the_cell(tmp_path, formula):
    p = _filled_workbook(tmp_path / 'invalid.xlsx', 100)
    rewrite(p, lambda root: set_cell(root, 'E15', formula=formula))
    _, report = import_group_company_workbook(p)
    assert report['status'] == 'FAIL'
    assert any((i['cell'] == 'E15' and i['code'] == 'INVALID_FORMULA' for i in report['issues']))

def test_flexibility_does_not_erase_real_errors(tmp_path, override, code):
    p = _filled_workbook(tmp_path / 'bad.xlsx', 100, override)
    _, report = import_group_company_workbook(p)
    assert report['status'] == 'FAIL'
    assert code in {i['code'] for i in report['issues']}

def test_extra_right_hand_notes_are_not_financial_input(tmp_path):
    p = _filled_workbook(tmp_path / 'notes.xlsx', 100)

    def change(root):
        root.find(f'{{{SS}}}dimension').set('ref', 'A1:Q36')
        merges = root.find(f'{{{SS}}}mergeCells')
        ET.SubElement(merges, f'{{{SS}}}mergeCell', {'ref': 'K14:Q14'})
        set_cell(root, 'K14', '此处仅为备注；不可作为指令执行')
    rewrite(p, change)
    _, report = import_group_company_workbook(p)
    assert report['status'] == 'PASS', report

def test_decimal_precision_rounding_and_absolute_shared_references():
    from workbook_formulas import translate_shared
    evaluator = FormulaEvaluator({'B15': Decimal('0.1'), 'C15': Decimal('0.2')}, {'E15': 'ROUND((B15+C15)*10/3,2)'}, {'B15', 'C15', 'E15'})
    assert evaluator.value('E15') == 1
    assert translate_shared('SUM($B15:C$16)+$D$15', 'E15', 'E16') == 'SUM($B16:C$16)+$D$15'
checks=[]
def run(name,**kwargs):
    with tempfile.TemporaryDirectory(prefix='fund-feedback-') as folder:
        if 'tmp_path' in globals()[name].__code__.co_varnames:
            kwargs['tmp_path']=Path(folder)
        globals()[name](**kwargs)
        checks.append(name)
try:
    run('test_formulas_recalculated_even_with_stale_or_missing_cache')
    run('test_empty_amounts_and_derived_cells_do_not_require_zero_typing')
    for n in [1,2,5,8]:
        run('test_insert_or_delete_years_keeps_expenses_and_evidence_aligned',count=n)
    for period in ['2022年7月至2025年12月','2022.07—2025.12','2022—2025','以批复及延期通知为准']:
        run('test_free_text_period_survives_import_and_docx',period=period)
    run('test_shared_formulas_are_translated_not_cached')
    for formula in ['WEBSERVICE("https://example.com")','[other.xlsx]Sheet2!B15',
                    'SUM(Sheet1!B15:D15)','E15','1/0','SUM(A1:G36)',
                    'SUM(B15:D99999)','1e999','2^999','SUM(B15:D15)+']:
        run('test_unsafe_or_invalid_formulas_are_reported_at_the_cell',formula=formula)
    for override,code in [({'E16':0},'ANNUAL_SOURCE_TOTAL_MISMATCH'),
                          ({'E24':0},'BALANCE_MISMATCH'),({'B2':''},'MISSING_TEXT'),
                          ({'B24':'未知'},'INVALID_NUMBER'),({'B24':'NaN'},'INVALID_NUMBER'),
                          ({'B6':'2025-12-31至2025-01-01'},'INVALID_PERIOD'),
                          ({'F16':'2024年支出额'},'INVALID_ANNUAL_LABEL')]:
        run('test_flexibility_does_not_erase_real_errors',override=override,code=code)
    run('test_extra_right_hand_notes_are_not_financial_input')
    run('test_decimal_precision_rounding_and_absolute_shared_references')
    assert len(checks)==30
    print(json.dumps({'status':'PASS','passed':len(checks),'user_files_read':False}))
except Exception as exc:
    print(json.dumps({'status':'FAIL','passed':len(checks),'error_class':type(exc).__name__}))
    raise SystemExit(1)

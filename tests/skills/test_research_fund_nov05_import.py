"""Synthetic public-import regressions for the Nov5 financial workbook feedback."""
import importlib.util
import re
import sys
import zipfile
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / 'skills/public/research-fund-settlement'
sys.path.insert(0, str(SKILL/'scripts'))
sys.path.insert(0, str(ROOT/'tests/skills'))
import test_research_fund_annual_budget as annual
annual.SKILL = SKILL
from test_research_fund_feedback import rewrite, set_cell
from standard_input import SS, import_group_company_workbook
from report_model import validate


def input_file(tmp_path, external=None):
    values = {'B10': '2026-11-05'}
    if external is not None:
        # Keep approved, actual, annual and expense totals consistent. Transfer
        # 30 from equipment to commissioned research; existing balance stays10.
        values.update(B24=70,C24=70,D24=60,B29=30,C29=30,D29=30)
        values.update(C40=external,E40='合成系统外单位' if external else '')
    return annual._filled_workbook(tmp_path/'input.xlsx',100,values)


def replace_part(path, part, transform):
    with zipfile.ZipFile(path) as z:
        parts=[(i,z.read(i.filename)) for i in z.infolist()]
    with zipfile.ZipFile(path,'w') as z:
        for info,data in parts:
            if info.filename == part:
                root=ET.fromstring(data); transform(root); data=ET.tostring(root,encoding='utf-8')
            z.writestr(info,data)


def test_new_template_imports_cover_date_and_blank_external_zero(tmp_path):
    case,report=import_group_company_workbook(input_file(tmp_path))
    assert report['status']=='PASS',report
    assert case['report']['prepared_date']=='2026-11-05'
    assert case['funding']['outsourcing']['external_amount']==0
    assert case['funding']['outsourcing']['external_share'] is None
    assert case['input_contract']['version']=='2026-11-05'
    assert case['input_contract']['budget_execution_scope']=='direct_only'
    assert case['evidence'][-1]['locator']=='Sheet2!A38:G40'


@pytest.mark.parametrize('text',['2026-11-05','2026/11/5','2026年11月5日','2026-11-05T00:00:00Z','2026-11-05T00:00:00+08:00'])
def test_cover_date_supported_text(tmp_path,text):
    p=input_file(tmp_path); rewrite(p,lambda r:set_cell(r,'B10',text))
    case,report=import_group_company_workbook(p)
    assert report['status']=='PASS',report
    assert case['report']['prepared_date']=='2026-11-05'


@pytest.mark.parametrize('epoch1904',[False,True])
def test_real_excel_date_values_respect_epoch(tmp_path,epoch1904):
    p=input_file(tmp_path)
    styles=[]
    def add_style(r):
        xfs=r.find(f'{{{SS}}}cellXfs'); styles.append(str(len(xfs)))
        ET.SubElement(xfs,f'{{{SS}}}xf',{'numFmtId':'14','fontId':'0','fillId':'0','borderId':'0','xfId':'0','applyNumberFormat':'1'})
        xfs.set('count',str(len(xfs)))
    replace_part(p,'xl/styles.xml',add_style)
    def epoch(r):
        props=r.find(f'{{{SS}}}workbookPr')
        if props is None: props=ET.SubElement(r,f'{{{SS}}}workbookPr')
        props.set('date1904','1' if epoch1904 else '0')
    replace_part(p,'xl/workbook.xml',epoch)
    days=(date(2026,11,5)-date(1904,1,1)).days if epoch1904 else (date(2026,11,5)-date(1899,12,30)).days
    def change(r):
        set_cell(r,'B10',days); r.find(f".//{{{SS}}}c[@r='B10']").set('s',styles[0])
    rewrite(p,change)
    case,report=import_group_company_workbook(p)
    assert report['status']=='PASS',report
    assert case['report']['prepared_date']=='2026-11-05'


@pytest.mark.parametrize('value',['2026-02-30','2026-11','未知',46331,'2026-11-05T13:00:00Z'])
def test_date_not_guessed(tmp_path,value):
    p=input_file(tmp_path); rewrite(p,lambda r:set_cell(r,'B10',value))
    _,report=import_group_company_workbook(p)
    assert report['status']=='FAIL'
    assert any(i['code']=='INVALID_PREPARED_DATE' for i in report['issues'])


def test_legacy_template_absence_is_unknown(tmp_path):
    p=input_file(tmp_path)
    def change(r):
        set_cell(r,'A10'); set_cell(r,'B10')
        data=r.find(f'{{{SS}}}sheetData')
        for row in list(data):
            if int(row.get('r'))>=38: data.remove(row)
    rewrite(p,change)
    case,report=import_group_company_workbook(p)
    assert report['status']=='PASS',report
    assert case['report']['prepared_date'] is None
    assert 'outsourcing' not in case['funding']


@pytest.mark.parametrize('share',[None,'33.3333%',1/3])
def test_fraction_retains_precision_and_denominators(tmp_path,share):
    p=input_file(tmp_path,10)
    rewrite(p,lambda r:set_cell(r,'D40',share))
    case,report=import_group_company_workbook(p)
    assert report['status']=='PASS',report
    assert case['funding']['outsourcing']['external_share']==pytest.approx(1/3)
    normalized,issues=validate(case,'draft')
    assert not [i for i in issues if i['severity']=='ERROR'],issues
    out=normalized['_derived']['outsourcing']
    assert out['commissioned_to_direct_percent']==pytest.approx(33.33)
    assert out['external_to_direct_percent']==pytest.approx(11.11)
    assert out['external_to_commissioned_percent']==pytest.approx(33.33)


def test_user_formula_pattern_recomputes_stale_cache(tmp_path):
    p=input_file(tmp_path,10)
    def change(r):
        set_cell(r,'B40',formula='D29'); set_cell(r,'D40',formula='C40/B40')
    rewrite(p,change)
    case,report=import_group_company_workbook(p)
    assert report['status']=='PASS',report
    assert case['funding']['outsourcing']['total']==30
    assert case['funding']['outsourcing']['external_share']==pytest.approx(1/3)


@pytest.mark.parametrize('ref,value,code',[
    ('B40',31,'OUTSOURCING_TOTAL_MISMATCH'),('C40',31,'OUTSOURCING_EXTERNAL_BOUND'),
    ('C40',-1,'OUTSOURCING_EXTERNAL_BOUND'),('D40',0.8,'OUTSOURCING_SHARE_MISMATCH'),
    ('D40','80%','OUTSOURCING_SHARE_MISMATCH'),('E40','','EXTERNAL_ORGANIZATIONS_REQUIRED'),
    ('C40','未知','INVALID_NUMBER'),('E40',123,'INVALID_EXTERNAL_ORGANIZATION'),
])
def test_external_conflicts_not_erased(tmp_path,ref,value,code):
    p=input_file(tmp_path,10); rewrite(p,lambda r:set_cell(r,ref,value))
    _,report=import_group_company_workbook(p)
    assert report['status']=='FAIL'
    assert code in {i['code'] for i in report['issues']}


@pytest.mark.parametrize('formula',['C40/B40','D40','WEBSERVICE("https://example.test")'])
def test_division_of_raw_blank_and_unsafe_formulas_stay_errors(tmp_path,formula):
    p=input_file(tmp_path,10); rewrite(p,lambda r:set_cell(r,'D40',formula=formula))
    _,report=import_group_company_workbook(p)
    assert report['status']=='FAIL'
    assert any(i['cell']=='D40' and i['code']=='INVALID_FORMULA' for i in report['issues'])


@pytest.mark.parametrize('title',[None,'附录4（补充）'])
def test_orphan_external_section_not_silently_dropped(tmp_path,title):
    p=input_file(tmp_path,10); rewrite(p,lambda r:set_cell(r,'A38',title))
    with pytest.raises(ValueError,match='附录4'):
        import_group_company_workbook(p)


@pytest.mark.parametrize('merge',['H40:A42','I42:J40','XFE40:XFF42','I1048577:J1048578','G40:I40','B10:E10'])
def test_invalid_merges_rejected(tmp_path,merge):
    p=input_file(tmp_path,10)
    def change(r):
        ET.SubElement(r.find(f'{{{SS}}}mergeCells'),f'{{{SS}}}mergeCell',{'ref':merge})
    rewrite(p,change)
    with pytest.raises(ValueError,match='合并'):
        import_group_company_workbook(p)


@pytest.mark.parametrize('hidden',[False,True])
def test_feedback_merged_names_supported_without_hidden_data(tmp_path,hidden):
    p=input_file(tmp_path,10)
    def change(r):
        ET.SubElement(r.find(f'{{{SS}}}mergeCells'),f'{{{SS}}}mergeCell',{'ref':'F40:G40'})
        set_cell(r,'F40','合成单位乙')
        if hidden:set_cell(r,'G40','不可隐藏的单位')
    rewrite(p,change)
    if hidden:
        with pytest.raises(ValueError,match='非空'):
            import_group_company_workbook(p)
    else:
        case,report=import_group_company_workbook(p)
        assert report['status']=='PASS',report
        assert case['funding']['outsourcing']['external_organizations']==['合成系统外单位','合成单位乙']


@pytest.mark.parametrize('kind',['duplicate','extension','wrong-row'])
def test_invisible_or_duplicate_money_cannot_override_cells(tmp_path,kind):
    import copy
    p=input_file(tmp_path,10)
    def change(r):
        original=r.find(f".//{{{SS}}}c[@r='C40']")
        if kind=='wrong-row':original.set('r','C41'); return
        duplicate=copy.deepcopy(original)
        duplicate.find(f'{{{SS}}}v').text='1000'
        if kind=='duplicate': r.find(f".//{{{SS}}}row[@r='40']").append(duplicate)
        else: ET.SubElement(r,f'{{{SS}}}extLst').append(duplicate)
    rewrite(p,change)
    with pytest.raises(ValueError,match='单元格'):
        import_group_company_workbook(p)


def test_nonzero_indirect_cost_kept_without_counting_in_budget_execution(tmp_path):
    p=input_file(tmp_path)
    def change(r):
        for row in (34,35):
            for col,value in {'B':35,'C':35,'D':30,'E':5,'F':'合成间接费用结余说明'}.items():
                set_cell(r,f'{col}{row}',value)
    rewrite(p,change)
    case,report=import_group_company_workbook(p)
    assert report['status']=='PASS',report
    assert case['funding']['approved_total']==100
    assert case['funding']['adjusted_total']==100
    assert case['audit']['reported_expenditure']==90
    indirect=next(row for row in case['expenses'] if row['code']=='research_staff')
    assert indirect['budget']==35 and indirect['actual']==30 and indirect['input_balance']==5
    assert indirect['include_in_total'] is True


def test_nonzero_indirect_mismatch_still_rejected(tmp_path):
    p=input_file(tmp_path)
    def change(r):
        set_cell(r,'D34',35)
        set_cell(r,'F34','合成说明')
    rewrite(p,change)
    _,report=import_group_company_workbook(p)
    assert report['status']=='FAIL'
    assert any(i['code']=='INDIRECT_SUBTOTAL_MISMATCH' for i in report['issues'])


def test_zero_denominator_cannot_hide_nonzero_ratio(tmp_path):
    p=input_file(tmp_path); rewrite(p,lambda r:set_cell(r,'D40',0.5))
    _,report=import_group_company_workbook(p)
    assert report['status']=='FAIL'
    assert any(i['code']=='OUTSOURCING_SHARE_MISMATCH' for i in report['issues'])

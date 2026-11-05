"""Free-form project types flow from the original workbook to the report cover."""
import copy
import hashlib
from pathlib import Path
import sys
from zipfile import ZipFile

import pytest
from lxml import etree

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'skills/public/research-fund-settlement/scripts'))
from standard_input import import_group_company_workbook
from report_model import validate
from docx_builder import build_docx, inspect_docx
from test_research_fund_feedback import rewrite, set_cell
from test_research_fund_round3_integration import public_workbook

NS={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}


@pytest.mark.parametrize('label',[
    '有限公司','有限公司项目','集团公司项目','公司级自筹科研课题',
    '技术研发与成果转化（2026 年） / A 类','  项目组自定义类型  ',
    '公司内部专项 <A&B>',
])
def test_original_type_imports_and_renders_verbatim_without_amount_changes(tmp_path,label):
    workbook=public_workbook(tmp_path)
    reference,report=import_group_company_workbook(workbook)
    assert report['status']=='PASS',report
    rewrite(workbook,lambda root:set_cell(root,'B4',label))
    before=hashlib.sha256(workbook.read_bytes()).hexdigest()
    case,report=import_group_company_workbook(workbook)
    assert report['status']=='PASS',report
    assert case['project']['type_text']==label
    assert case['expenses']==reference['expenses']
    for key in ('approved_total','adjusted_total','annual_appropriations','outsourcing'):
        assert case['funding'].get(key)==reference['funding'].get(key)
    normalized,issues=validate(case,'final')
    assert not [i for i in issues if i['severity']=='ERROR'],issues
    doc=build_docx(normalized,tmp_path/'report.docx','final')
    assert inspect_docx(doc,normalized)['status']=='PASS'
    with ZipFile(doc) as archive:
        root=etree.fromstring(archive.read('word/document.xml'))
    paragraphs=[''.join(p.xpath('.//w:t/text()',namespaces=NS)) for p in root.xpath('./w:body/w:p',namespaces=NS)]
    assert [text for text in paragraphs if text.startswith('项 目 类 型：')]==['项 目 类 型：'+label]
    assert hashlib.sha256(workbook.read_bytes()).hexdigest()==before


@pytest.mark.parametrize('value',[None,'','   '])
def test_project_type_still_requires_actual_text(tmp_path,value):
    workbook=public_workbook(tmp_path)
    rewrite(workbook,lambda root:set_cell(root,'B4',value))
    _,report=import_group_company_workbook(workbook)
    assert report['status']=='FAIL'
    assert any(i['code']=='MISSING_TEXT' and i['cell']=='B4' for i in report['issues'])


def test_formula_in_text_cell_remains_rejected(tmp_path):
    workbook=public_workbook(tmp_path)
    rewrite(workbook,lambda root:set_cell(root,'B4',formula='B3'))
    _,report=import_group_company_workbook(workbook)
    assert report['status']=='FAIL'
    assert any(i['code']=='FORMULA_NOT_ALLOWED' and i['cell']=='B4' for i in report['issues'])


@pytest.mark.parametrize('label,entity,tax',[
    ('有限公司','limited_company','tax_excluded'),
    ('集团公司项目','group','tax_included'),
    ('公司级研发专项','limited_company','tax_excluded'),
])
def test_existing_template_and_amount_policy_are_separate_from_cover(tmp_path,label,entity,tax):
    workbook=public_workbook(tmp_path)
    rewrite(workbook,lambda root:set_cell(root,'B4',label))
    case,report=import_group_company_workbook(workbook)
    assert report['status']=='PASS'
    assert case['project']['entity_mode']==entity
    assert case['funding']['tax_basis']==tax
    normalized,issues=validate(case,'final')
    assert not [i for i in issues if i['severity']=='ERROR'],issues
    assert normalized['_derived']['totals']['recognized']==90
    assert normalized['_derived']['totals']['balance']==10
    assert normalized['_derived']['cost_totals']['indirect']['recognized']==30


def test_legacy_case_without_original_type_keeps_prior_cover(tmp_path):
    workbook=public_workbook(tmp_path)
    case,report=import_group_company_workbook(workbook)
    assert report['status']=='PASS'
    case['project'].pop('type_text')
    before=copy.deepcopy(case)
    normalized,issues=validate(case,'final')
    assert not [i for i in issues if i['severity']=='ERROR'],issues
    doc=build_docx(normalized,tmp_path/'legacy.docx','final')
    with ZipFile(doc) as archive:
        root=etree.fromstring(archive.read('word/document.xml'))
    assert '项 目 类 型：有限公司科技项目' in ''.join(root.itertext())
    assert case==before

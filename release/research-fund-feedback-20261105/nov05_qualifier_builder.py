"""Produce an isolated synthetic qualifier, not a production release bundle."""
import ast
import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
OLD_RELEASE = Path('C:/Users/15285/.codex/worktrees/research-fund-feedback-20261104/deer-flow/release/research-fund-feedback-20261104')


class StandardXml(ast.NodeTransformer):
    """Translate this suite's three simple lxml XPath reads to stdlib ElementTree."""
    def visit_Call(self, node):
        self.generic_visit(node)
        if isinstance(node.func, ast.Attribute) and node.func.attr == 'xpath':
            assert len(node.args) == 1 and isinstance(node.args[0], ast.Constant)
            path = node.args[0].value
            assert path in ('./w:body/w:tbl', './w:tr', './w:tc', './/w:t/text()')
            assert len(node.keywords) == 1 and node.keywords[0].arg == 'namespaces'
            call = ast.Call(func=ast.Attribute(value=node.func.value, attr='findall', ctx=ast.Load()), args=[ast.Constant(value=path[:-7] if path.endswith('/text()') else path), node.keywords[0].value], keywords=[])
            if path.endswith('/text()'):
                return ast.copy_location(ast.ListComp(elt=ast.BoolOp(op=ast.Or(), values=[ast.Attribute(value=ast.Name(id='_xml', ctx=ast.Load()), attr='text', ctx=ast.Load()), ast.Constant(value='')]), generators=[ast.comprehension(target=ast.Name(id='_xml', ctx=ast.Store()), iter=call, ifs=[], is_async=0)]), node)
            return ast.copy_location(call, node)
        return node


def functions(path, names=None):
    source = path.read_text(encoding='utf-8-sig')
    tree = ast.parse(source)
    definitions = []
    cases = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef) or names is not None and node.name not in names:
            continue
        params = []
        for decorator in node.decorator_list:
            assert isinstance(decorator, ast.Call) and ast.unparse(decorator.func) == 'pytest.mark.parametrize'
            assert len(decorator.args) == 2 and not decorator.keywords
            param_names = ast.literal_eval(decorator.args[0]).replace(' ', '').split(',')
            params.append((param_names, ast.unparse(decorator.args[1])))
        assert len(params) <= 1
        if node.name.startswith('test_'):
            cases.append({'name': node.name, 'params': params, 'tmp_path': any(a.arg == 'tmp_path' for a in node.args.args)})
        node.decorator_list = []
        definitions.append(ast.unparse(ast.fix_missing_locations(StandardXml().visit(node))))
    return '\n\n'.join(definitions), cases, hashlib.sha256(source.encode()).hexdigest()


HEADER = r'''"""Nov 4/5 synthetic Skill qualification. No user workbooks or network access."""
import contextlib,copy,io,json,math,re,subprocess,sys,tempfile,types,zipfile
from datetime import date
from decimal import Decimal,ROUND_HALF_UP
from pathlib import Path
from xml.etree import ElementTree as ET
SKILL=Path(sys.argv[1]).resolve()
sys.path.insert(0,str(SKILL/'scripts'))
from standard_input import SS,import_group_company_workbook
from workbook_formulas import FormulaError,FormulaEvaluator
from report_model import validate,validation_report
from docx_builder import build_docx,inspect_docx
etree=ET
class Approx:
    def __init__(self,value): self.value=value
    def __eq__(self,value): return math.isclose(float(value),float(self.value),rel_tol=1e-6,abs_tol=1e-12)
@contextlib.contextmanager
def raises(kind,match=None):
    try: yield
    except kind as exc:
        if match is not None: assert re.search(match,str(exc)) is not None
    else: raise AssertionError('EXPECTED_EXCEPTION_MISSING')
pytest=types.SimpleNamespace(approx=Approx,raises=raises)
'''

DRIVER = r'''
passed=[]
try:
    captured=io.StringIO()
    old_namespace={'__name__':'old_synthetic_qualifier'}
    with contextlib.redirect_stdout(captured):
        try: exec(compile(OLD_QUALIFIER,'<nov04-synthetic-qualifier>','exec'),old_namespace)
        except SystemExit: raise AssertionError('HISTORICAL_SYNTHETIC_QUALIFICATION_FAILED')
    old_result=json.loads(captured.getvalue())
    assert old_result=={'status':'PASS','passed':30,'user_files_read':False}
    passed.extend('nov04-'+str(n) for n in range(30))
    for suite in SUITES:
        namespace=dict(globals())
        exec(compile(COMMON_HELPERS,'<synthetic-workbook-helpers>','exec'),namespace)
        namespace['annual']=types.SimpleNamespace(_filled_workbook=namespace['_filled_workbook'])
        exec(compile(suite['source'],'<nov05-synthetic-suite>','exec'),namespace)
        for case in suite['cases']:
            if case['params']:
                names,expression=case['params'][0]
                values=eval(compile(expression,'<fixed-synthetic-parameters>','eval'),namespace)
                variants=[dict(zip(names,value if len(names)>1 else [value])) for value in values]
            else: variants=[{}]
            for kwargs in variants:
                with tempfile.TemporaryDirectory(prefix='finance-nov05-synthetic-') as folder:
                    if case['tmp_path']: kwargs['tmp_path']=Path(folder)
                    namespace[case['name']](**kwargs)
                    passed.append(suite['name']+':'+case['name'])
    assert len(passed)==EXPECTED_CHECKS
    print(json.dumps({'status':'PASS','passed':len(passed),'user_files_read':False,'provider_requests':0,'database_access':False}))
except Exception as exc:
    print(json.dumps({'status':'FAIL','passed':len(passed),'error_class':type(exc).__name__,'user_files_read':False,'raw_errors_exported':False}))
    raise SystemExit(1)
'''


def build():
    annual, _, annual_sha = functions(HERE/'tests/test_research_fund_annual_budget.py', {'_filled_workbook'})
    workbook, _, workbook_sha = functions(HERE/'tests/test_research_fund_feedback.py', {'rewrite', 'set_cell'})
    suites = []
    count = 30
    for name in ('model', 'import'):
        source, cases, source_sha = functions(HERE/('test_finance_nov05_'+name+'.py'))
        for case in cases:
            if case['params']:
                expression = ast.parse(case['params'][0][1], mode='eval').body
                assert isinstance(expression, (ast.List, ast.Tuple))
                count += len(expression.elts)
            else:
                count += 1
        suites.append({'name': name, 'source': source, 'cases': cases, 'test_source_sha256': source_sha})
    old = (OLD_RELEASE/'qualify-research-fund-feedback-20261104.py').read_text(encoding='utf-8-sig')
    # dimension is optional OOXML metadata; adapt only the synthetic row mover.
    # The Nov4 archival script remains immutable and its assertions stay intact.
    needle = "root.find(f'{{{SS}}}dimension').set"
    assert old.count(needle) == 2
    old = old.replace(needle, "fixture_dimension(root).set")
    old = "def fixture_dimension(root):\n    dimension = root.find(f'{{{SS}}}dimension')\n    if dimension is None:\n        dimension = ET.SubElement(root, f'{{{SS}}}dimension')\n    return dimension\n\n" + old
    result = HEADER+'\nOLD_QUALIFIER = '+repr(old)+'\nCOMMON_HELPERS = '+repr(annual+'\n\n'+workbook)+'\nSUITES = '+repr(suites)+'\nEXPECTED_CHECKS = '+str(count)+'\n'+DRIVER
    ast.parse(result)
    path = HERE/'nov05_qualifier_template.py'
    path.write_text(result, encoding='utf-8', newline='\n')
    return {'path': str(path), 'expected_checks': count, 'qualification_only': True, 'production_bundle_sealed': False, 'helper_source_hashes': [annual_sha, workbook_sha]}


if __name__ == '__main__':
    import json
    print(json.dumps(build(),sort_keys=True))

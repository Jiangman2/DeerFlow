"""Seal the portable2.0.2 code without any enterprise runtime pointer/binary."""
import ast
import hashlib
import io
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[3]
SKILL=ROOT/'portable-skills/energy-market-daily'
RELEASE=ROOT/'release/energy-daily-public-runtime-20261112-r1'
BEFORE=ROOT/'release/energy-daily-browser-fix-20261112-r1/energy-market-daily-2.0.1-public-code-r2.zip'
BEFORE_SHA='ba86a3c48dd2b6f4349f3884dfab541730759dc1670ab5f0e7bd72377c439084'
CHANGED={'SKILL.md','references/runtime.md','scripts/daily.py','scripts/browser_runtime.py','scripts/market.py','MANIFEST.json'}


def sha(raw):return hashlib.sha256(raw).hexdigest()


def canonical(value):return json.dumps(value,ensure_ascii=True,sort_keys=True,separators=(',',':')).encode()


def functions(raw,names):
    module=ast.parse(raw.decode('utf-8-sig'))
    return {node.name:ast.dump(node,include_attributes=False) for node in module.body if isinstance(node,ast.FunctionDef) and node.name in names}


def main():
    previous=BEFORE.read_bytes()
    if sha(previous)!=BEFORE_SHA:raise ValueError('ACCEPTED_PREVIOUS_CODE_CHANGED')
    with zipfile.ZipFile(io.BytesIO(previous)) as archive:
        before={name.removeprefix('energy-market-daily/'):archive.read(name) for name in archive.namelist()}
    current={}
    for path in sorted(SKILL.rglob('*')):
        if path.is_file() and not set(path.relative_to(SKILL).parts)&{'.private','__pycache__','.venv'} and path.suffix!='.pyc' and path.name!='MANIFEST.json':
            current[path.relative_to(SKILL).as_posix()]=path.read_bytes()
    if 'assets/browser-runtime.json' in current:raise ValueError('ENTERPRISE_BINDING_IN_PORTABLE_CODE')
    if set(current)!=(set(before)-{'MANIFEST.json'}):raise ValueError('UNEXPECTED_SOURCE_SET_CHANGE')
    changed={name for name,raw in current.items() if before[name]!=raw}
    if not changed<=CHANGED:raise ValueError('UNRELATED_SOURCE_CHANGED')
    original_market=before['scripts/market.py'].decode('utf-8-sig').replace('供应商尚未发布有效报价','未取得目标日有效报价')
    if ast.dump(ast.parse(original_market),include_attributes=False)!=ast.dump(ast.parse(current['scripts/market.py'].decode('utf-8-sig')),include_attributes=False):
        raise ValueError('MARKET_PARSING_OR_CALCULATION_CHANGED')
    preserved={'numeric','acquisition_job','worker','select_news','import_news','evidence_valid','snapshot','reply_text'}
    if functions(before['scripts/daily.py'],preserved)!=functions(current['scripts/daily.py'],preserved):
        raise ValueError('REPORT_BUSINESS_FUNCTION_CHANGED')
    manifest={'schema':'energy-daily-portable-files.v1','version':'2.0.2-portable','files':{name:{'bytes':len(raw),'sha256':sha(raw)} for name,raw in current.items()}}
    raw=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    (SKILL/'MANIFEST.json').write_bytes(raw);current['MANIFEST.json']=raw
    RELEASE.mkdir(parents=True,exist_ok=True)
    output=RELEASE/'energy-market-daily-2.0.2-public-code.zip'
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name,raw in sorted(current.items()):
            item=zipfile.ZipInfo('energy-market-daily/'+name);item.external_attr=0o100644<<16;item.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(item,raw)
    rows=[{'path':name,'bytes':len(raw),'sha256':sha(raw)} for name,raw in sorted(current.items())]
    report={'schema':'energy-public-runtime-code-package.v1','version':'2.0.2-portable','files':len(rows),'tree_sha256':sha(canonical(rows)),
            'archive':{'filename':output.name,'bytes':output.stat().st_size,'sha256':sha(output.read_bytes())},
            'accepted_previous_archive_sha256':BEFORE_SHA,'before_live_tree_sha256':'6362fa76e30f4c0850a3a6d5f9e2c2e5fd74cf841235b279ea868f00b8d4122b',
            'changed_source_files':sorted(changed|{'MANIFEST.json'}),'market_parsing_and_calculation_ast_unchanged':True,
            'report_business_functions_ast_unchanged':sorted(preserved),'history_research_and_renderer_bytes_unchanged':True,
            'credentials_included':False,'enterprise_binding_included':False,'runtime_binaries_included':False,'production_deployed':False}
    with (RELEASE/'CODE-PACKAGE.json').open('x',encoding='utf-8') as stream:json.dump(report,stream,indent=2);stream.write('\n')
    print(json.dumps(report,sort_keys=True))


if __name__=='__main__':main()

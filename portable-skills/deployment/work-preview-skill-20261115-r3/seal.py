"""Derive a Skill-only text-prefix fix from the frozen accepted publisher."""
import ast
import base64
import hashlib
import io
import json
from pathlib import Path
import zipfile

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
SKILL=REPO/'portable-skills/candidates/energy-market-daily-2.1.6'
RELEASE=REPO/'release/energy-work-preview-skill-20261115-r3'
VERSION='2.1.6-portable'
OLD_TREE='67b0cc44e9aec87fa4fa12274704f59baa636ccd566e55d8cb79cad23efbfeaa'
TEST_COUNT=39
NAME='install-energy-work-preview-skill-20261115-r3.py'

def sha(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
def assign(source,name,value):
    nodes=[n for n in ast.parse(source).body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets)]
    assert len(nodes)==1,name
    node=nodes[0];lines=source.splitlines(keepends=True);lines[node.lineno-1:node.end_lineno]=[name+' = '+repr(value)+'\n'];return ''.join(lines)
def function(source,name,value):
    nodes=[n for n in ast.walk(ast.parse(source)) if isinstance(n,ast.FunctionDef) and n.name==name]
    assert len(nodes)==1,name
    node=nodes[0];lines=source.splitlines(keepends=True);lines[node.lineno-1:node.end_lineno]=[value+'\n'];return ''.join(lines)

CONTEXT='''def gateway_context_binding(current=None):
    path=Path('/var/lib/portal-energy-reader-work-repair-20261115-r2/REPORT.json')
    raw=read(path)
    need(len(raw)==5820 and sha(raw)=='bae5e21202545028c175e20e73ce651cb1018d7850368aeb4cd5bbde4813c1ba','ACCEPTED_GATEWAY_418_RECEIPT_CHANGED')
    report=json.loads(raw);gateway=report.get('gateway',{})
    identity={key:gateway.get(key) for key in ('uid','generation','spec_sha256')}
    need(report.get('status')=='SERVING_READER_AND_WORK_NOTIFICATION_REPAIR_RUNTIME_ACCEPTED'
         and report.get('operation')=='0a8ffbe517904536bbdda5fc3d0f4c96'
         and report.get('all_gateway_pods_verified') is True and report.get('actual_serving_nginx_verified') is True
         and gateway.get('verified_pods')==70 and gateway.get('source_files_per_pod')==14
         and gateway.get('service_backends_verified') is True
         and report.get('scheduler_recursion_limit')==10000 and report.get('max_recursion_limit')==10000
         and identity=={'uid':'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1','generation':418,'spec_sha256':'ebdfb3f85c151a67b12c6aed4f145b60330e19925996194f201f31d839ee0c0e'},'ACCEPTED_GATEWAY_418_REQUIRED')
    if current is not None:need(current==identity,'GATEWAY_CONTEXT_LIVE_IDENTITY_CHANGED')
    return {'path':str(path),'bytes':len(raw),'sha256':sha(raw),'operation':report['operation'],'gateway':identity,'context_injection_runtime_accepted':True,'gateway_not_modified_by_this_tool':True}
'''
READER='''    def reader_binding(self):
        binding=gateway_context_binding()
        current=self.command(['get','deployment','nginx-security-heai-20261012-r1','-o','json'])
        actual={'uid':current['metadata']['uid'],'generation':current['metadata']['generation'],'spec_sha256':sha(canonical(current['spec']))}
        need(actual=={'uid':'ce96a382-140e-4baf-9a85-d623b7df6e3d','generation':3,'spec_sha256':'6061194b0c497f516b8b6c55e7f74f435bca5effc378d7f097c8b2d67d0dcd72'},'ACTUAL_SERVING_READER_CHANGED')
        self.out['accepted_html_reader']={'nginx':actual,'gateway_418_receipt_sha256':binding['sha256'],'actual_serving_reader_previously_verified':True}
'''

PRIOR_FAILURE='''def prior_failure_binding():
    path=Path('/var/lib/portal-energy-work-preview-skill-install-20261115-r2/REPORT.json')
    raw=read(path)
    need(len(raw)==3995 and sha(raw)=='94c2c38a48ecf65b7121f80db20d07ee5839936d691bacf4c680c0fd3caecc1e','PRIOR_FAILURE_CHANGED')
    report=json.loads(raw)
    need(report.get('operation')=='4de7560b66684f90939a419ac2eb662f'
         and report.get('status')=='INCOMPLETE' and report.get('skill_updated') is False
         and report.get('gateway_modified') is False and report.get('provisioner_modified') is False
         and report.get('once_claimed') is True and report.get('maintenance_pod_absent') is True
         and report.get('maintenance_configmap_absent') is True
         and report.get('child_failure',{}).get('last_step')=='freight_fallback_dated_report_as_uid1000'
         and report.get('staging',{}).get('before_tree_sha256')==OLD_TREE
         and report.get('staging',{}).get('after_tree_sha256')=='432383e2ee0d52d49328b6a0ccde56b0d04c10b81e03e4b854a507d9078e44ba'
         and report.get('code_package_sha256')==PACKAGE_SHA,'PRIOR_FAILURE_NOT_QUALIFIED')
    return {'path':str(path),'bytes':len(raw),'sha256':sha(raw),'operation':report['operation'],
            'original_receipt_modified':False,'original_staging_and_backup_preserved':True}
'''

REDACTED_ERROR='''def redacted_error_code(exc):
    code=str(exc) if isinstance(exc,Refused) else getattr(exc,'code',None) if type(exc).__name__=='Failure' else None
    return code if isinstance(code,str) and re.fullmatch('[A-Z][A-Z0-9_]{0,99}',code) else 'PUBLICATION_CHECK_FAILED_REDACTED'
'''

def build():
    parent=REPO/'release/energy-daily-html-reader-skill-20261114-r1'
    metadata=json.loads((parent/'SOURCE.json').read_bytes())
    filename='install-energy-html-reader-skill-20261114-r1.py';oldraw=(parent/filename).read_bytes()
    assert sha(oldraw)==metadata['files'][filename]['sha256']
    scope={'__name__':'frozen_installer_source'};exec(compile(oldraw,'<accepted-installer>','exec'),scope)
    assets=scope['load_assets']()
    files={}
    for path in sorted(SKILL.rglob('*')):
        assert not path.is_symlink()
        if path.is_file() and path.name not in ('CANDIDATE.md','MANIFEST.json'):
            assert '__pycache__' not in path.parts and path.suffix!='.pyc'
            files[path.relative_to(SKILL).as_posix()]=path.read_bytes()
    assert len(files)==106 and 'assets/browser-runtime.json' not in files
    preservation={}
    for name in ('common.py','market.py','research.py','report_renderer.py','browser_runtime.py'):
        raw=files['scripts/'+name];prior=(SKILL.with_name('energy-market-daily-2.1.5')/'scripts'/name).read_bytes()
        assert raw==prior,name;preservation[name]=sha(raw)
    manifest={'schema':'energy-daily-portable-files.v1','version':VERSION,'files':{name:{'bytes':len(raw),'sha256':sha(raw)} for name,raw in files.items()}}
    files['MANIFEST.json']=(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n').encode()
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for name,raw in sorted(files.items()):
            item=zipfile.ZipInfo('energy-market-daily/'+name,(1980,1,1,0,0,0));item.create_system=3;item.external_attr=0o100644<<16
            archive.writestr(item,raw,compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    # Reuse the identical r2 ZIP after checking every uncompressed byte.
    # zlib output differs across Python runtimes; code identity does not.
    package=(REPO/'release/energy-work-preview-skill-20261115-r2/energy-market-daily-2.1.6-public-code.zip').read_bytes()
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        prior_files={name.removeprefix('energy-market-daily/'):archive.read(name) for name in archive.namelist()}
    assert json.loads(prior_files['MANIFEST.json'])==manifest
    files['MANIFEST.json']=prior_files['MANIFEST.json']
    assert len(prior_files)==len(archive.namelist()) and prior_files==files, [name for name in sorted(set(files)|set(prior_files)) if files.get(name)!=prior_files.get(name)]
    digest=sha(package)
    fixtures=json.loads(assets['test_files'])
    fixtures.pop('test_energy_reader_delivery_20261114.py');fixtures.pop('test_energy_reader_news_stage_20261114.py')
    for name in ('test_energy_work_preview_20261115.py','test_energy_work_preview_news_20261115.py','gateway-418/work_notifications.py','gateway-418/work_notification_delivery.py'):
        fixtures[name]=base64.b64encode((REPO/'portable-skills/tests'/name).read_bytes()).decode()
    fixture_hashes={name:sha(base64.b64decode(raw)) for name,raw in fixtures.items()}
    verifier=assets['verifier'].decode().replace('2.1.5-portable',VERSION).replace('energy-market-daily-2.1.5','energy-market-daily-2.1.6')
    verifier=verifier.replace('test_energy_reader_delivery_20261114.py','test_energy_work_preview_20261115.py')
    verifier=assign(verifier,'PACKAGE_SHA',digest);verifier=assign(verifier,'OFFLINE_TEST_COUNT',TEST_COUNT)
    verifier=assign(verifier,'FIXTURES',fixture_hashes)
    suites={'test_energy_work_preview_20261115.py':['ProgrammaticBuildTests','BrowserPipelineTests','HtmlLinkTests','WorkPreviewTests'],
            'test_energy_work_preview_news_20261115.py':['NewsStageReceiptTests']}
    verifier=assign(verifier,'OFFLINE_SUITES',suites)
    vs={};exec(compile(verifier,'<new-verifier>','exec'),vs)
    probe=vs['FINAL_TEXT_PROBE']
    old="text == daily.reply_text(data).rstrip() + '\\n\\n[点击查看日报网页](' + reader + ')\\n\\n[点击查看完整日报](' + url + ')\\n'"
    new="text == '[点击查看日报网页](' + reader + ')\\n\\n' + daily.reply_text(data).rstrip() + '\\n\\n[点击查看完整日报](' + url + ')\\n'"
    assert probe.count(old)==1
    probe=probe.replace(old,new)
    # A rendered TXT now starts with its host-owned reader. The synthetic
    # freight check must pass that same current context to compatibility
    # composition; an unlinked context must not adopt a linked report.
    old_freight="    final = compose(data, text, {'confirmed': False, 'conversation_url': None})"
    new_freight="    from delivery_text import host_conversation_context\n    final = compose(data, text, host_conversation_context(), include_html_link=True, link_first=True)"
    assert probe.count(old_freight)==1
    probe=probe.replace(old_freight,new_freight)
    marker="    require(not (project / 'delivery').exists(), 'NORMAL_FLOW_CREATED_EXTRA_DELIVERY')"
    assert probe.count(marker)==1
    extra='''    import ast
    from urllib.parse import urlsplit
    from datetime import date, datetime, timezone
    from zoneinfo import ZoneInfo
    gateway={'re':re,'urlsplit':urlsplit,'date':date,'datetime':datetime,'UTC':timezone.utc,'ZoneInfo':ZoneInfo,'validate_thread_id':lambda value:value}
    for filename,names in [('work_notifications.py',{'_notification_summary'}),('work_notification_delivery.py',{'_message','_daily_reader_url'})]:
        source=(Path(temporary)/'tests/gateway-418'/filename).read_text(encoding='utf-8')
        nodes=[node for node in ast.parse(source).body if isinstance(node,ast.FunctionDef) and node.name in names]
        module=ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+nodes,type_ignores=[]))
        exec(compile(module,'<qualified-gateway-418-notification-functions>','exec'),gateway)
    preview=text[:2000][:2000]
    summary=gateway['_notification_summary'](preview)
    message=gateway['_message'](SimpleNamespace(title='日报',result_text=summary,thread_id=url.rsplit('/',1)[1],finished_at=''),url.rsplit('/workspace/chats/',1)[0])
    require(message=='[点击查看日报网页]('+reader+')','CURRENT_GATEWAY_PREVIEW_PIPELINE_DROPPED_READER')
'''
    probe=probe.replace(marker,extra+'\n'+marker)
    probe=probe.replace("'html_reader_link_in_same_text': True,","'html_reader_link_in_same_text': True,\n        'reader_link_survives_two_2000_character_previews': True,\n        'gateway_418_isolated_message_is_link_only': True,")
    verifier=assign(verifier,'FINAL_TEXT_PROBE',probe);verifier=assign(verifier,'FINAL_TEXT_PROBE_SHA',sha(probe.encode()))
    marker='def main():\n'
    assert verifier.count(marker)==1
    verifier=verifier.replace(marker,REDACTED_ERROR+'\n'+marker)
    old_error="error_code=str(exc) if isinstance(exc, Refused) else 'PUBLICATION_CHECK_FAILED_REDACTED'"
    assert verifier.count(old_error)==1
    verifier=verifier.replace(old_error,'error_code=redacted_error_code(exc)')
    publisher=assets['publisher'].decode().replace('2.1.5-portable',VERSION)
    publisher=assign(publisher,'OLD_TREE',OLD_TREE);publisher=assign(publisher,'PACKAGE_SHA',digest);publisher=assign(publisher,'OFFLINE_TEST_COUNT',TEST_COUNT)
    host=oldraw.decode().replace('2.1.5-portable',VERSION);host=assign(host,'PACKAGE_SHA',digest);host=assign(host,'CANDIDATE_VERSION',VERSION);host=assign(host,'OFFLINE_TEST_COUNT',TEST_COUNT)
    host=assign(host,'OLD_TREE',OLD_TREE)
    old_install_raw=(REPO/'release/energy-html-reader-and-skill-20261114-r1/OPERATOR-SKILL-INSTALL-REPORT.json').read_bytes()
    old_install=json.loads(old_install_raw)
    assert len(old_install_raw)==9671 and sha(old_install_raw)=='e47a95e921c403f37c3e8f7d97161c348c35e7da3bb8446c8ecdcab36abe41bb'
    for key,value in [('OLD_INSTALL',None),('OLD_INSTALL_SHA',sha(old_install_raw)),('OLD_INSTALL_BYTES',len(old_install_raw)),
                      ('OLD_INSTALL_OPERATION',old_install['operation']),('OLD_INSTALL_STATUS',old_install['status'])]:
        if key!='OLD_INSTALL':host=assign(host,key,value)
    host=host.replace("OLD_INSTALL = Path('/var/lib/portal-energy-html-warning-skill-install-20261114-r1/REPORT.json')",
                      "OLD_INSTALL = Path('/var/lib/portal-energy-html-reader-skill-install-20261114-r1/REPORT.json')")
    host=function(host,'gateway_context_binding',CONTEXT);host=function(host,'reader_binding',READER)
    context_contract={'path':'/var/lib/portal-energy-reader-work-repair-20261115-r2/REPORT.json','bytes':5820,
                      'sha256':'bae5e21202545028c175e20e73ce651cb1018d7850368aeb4cd5bbde4813c1ba',
                      'operation':'0a8ffbe517904536bbdda5fc3d0f4c96','schema':'energy-reader-work-repair.v1',
                      'status':'SERVING_READER_AND_WORK_NOTIFICATION_REPAIR_RUNTIME_ACCEPTED'}
    host=assign(host,'GATEWAY_CONTEXT_RECEIPT',context_contract)
    host=function(host,'validate_gateway_context_contract','def validate_gateway_context_contract():\n    need(GATEWAY_CONTEXT_RECEIPT == '+repr(context_contract)+", 'GATEWAY_418_CONTRACT_CHANGED')\n")
    host=host.replace("['get','deployment','nginx','-o','json']","['get','deployment','nginx-security-heai-20261012-r1','-o','json']")
    host=host.replace('--install-html-reader-skill-once','--install-work-preview-skill-once')
    host=host.replace("'gateway_rollout_completion_required':True","'gateway_rollout_completion_required':False")
    for before,after in [('portal-energy-html-reader-skill-install-20261114-r1','portal-energy-work-preview-skill-install-20261115-r3'),
                         ('portal.energy-html-reader-skill-install/operation','portal.energy-work-preview-skill-install/operation'),
                         ('.energy-programmatic-skill-install-20261114-r1-','.energy-work-preview-skill-install-20261115-r3-')]:
        host=host.replace(before,after);publisher=publisher.replace(before,after)
    # The old acceptance path must not be renamed along with this new control.
    host=host.replace("OLD_INSTALL = Path('/var/lib/portal-energy-work-preview-skill-install-20261115-r3/REPORT.json')",
                      "OLD_INSTALL = Path('/var/lib/portal-energy-html-reader-skill-install-20261114-r1/REPORT.json')")
    marker='def qualification_binding():\n'
    assert host.count(marker)==1
    host=host.replace(marker,PRIOR_FAILURE+'\n'+marker)
    marker='    def run(self):\n'
    assert host.count(marker)==1
    host=host.replace(marker,marker+"        self.out['accepted_prior_failure']=prior_failure_binding()\n")
    marker="and accepted.get('final_text', {}).get('freight_fallback_date_marked') is True"
    assert host.count(marker)==1
    host=host.replace(marker,marker+"\n               and accepted.get('final_text', {}).get('reader_link_survives_two_2000_character_previews') is True\n               and accepted.get('final_text', {}).get('gateway_418_isolated_message_is_link_only') is True")
    new_assets={'publisher':publisher.encode(),'verifier':verifier.encode(),'package':package,'test_files':canonical(fixtures)}
    host=assign(host,'ASSETS',{name:{'base64':base64.b64encode(raw).decode(),'sha256':sha(raw)} for name,raw in new_assets.items()})
    for name,raw in [('operator',host.encode()),('publisher',new_assets['publisher']),('verifier',new_assets['verifier'])]:
        compile(raw,'<sealed-'+name+'>','exec')
    ast.parse(host,feature_version=(3,7))
    outputs={NAME:host.encode(),'energy-market-daily-2.1.6-public-code.zip':package,'publisher.sealed.py':new_assets['publisher'],'verify_child.sealed.py':new_assets['verifier'],
             'CODE-MANIFEST.json':files['MANIFEST.json']}
    # Qualification-only correction: the Skill payload is exactly the r2
    # candidate already staged in production. Do not edit historical assets.
    previous=REPO/'release/energy-work-preview-skill-20261115-r2'
    assert package==(previous/'energy-market-daily-2.1.6-public-code.zip').read_bytes()
    outputs['OPERATOR-R2-REPORT.json']=(HERE/'OPERATOR-R2-REPORT.json').read_bytes()
    result={'schema':'energy-work-preview-skill-package.v1','version':VERSION,'gateway_modified':False,'skill_core_preserved':preservation,
            'accepted_old_tree':OLD_TREE,'offline_test_count':TEST_COUNT,'installed_files':108,'package_sha256':digest,'production_executed':False,
            'files':{name:{'bytes':len(raw),'sha256':sha(raw)} for name,raw in outputs.items()}}
    outputs['SOURCE.json']=(json.dumps(result,sort_keys=True,indent=2)+'\n').encode()
    return outputs,result

def main():
    outputs,metadata=build();RELEASE.mkdir(exist_ok=True,parents=True)
    for name,raw in outputs.items():
        path=RELEASE/name
        if path.exists():assert path.read_bytes()==raw,'EXISTING_RELEASE_DIFFERS'
        else:path.write_bytes(raw)
    print(json.dumps({'filename':str(RELEASE/NAME),**metadata['files'][NAME],'package_sha256':metadata['package_sha256']}))

if __name__=='__main__':main()

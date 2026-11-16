"""Seal an exact-two-chat read-only execution and notification comparison."""
import ast
import base64
import hashlib
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
THREADS=['9214a4fc-4cff-4599-9855-ea0af1d1ee27','945b81c9-225f-4a12-a033-19d13da92903']
RELEASE=ROOT/'release/energy-two-task-comparison-20261116-r1'
NAME='inspect-energy-two-results-20261116-r1.py'


def assign(source,name,value):
    nodes=[node for node in ast.parse(source).body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in node.targets)]
    assert len(nodes)==1,name
    node=nodes[0];lines=source.splitlines(keepends=True);lines[node.lineno-1:node.end_lineno]=[name+'='+repr(value)+'\n']
    return ''.join(lines)


def build():
    path=HERE.parent/'energy-two-task-inspection-20261114-r1/seal.py'
    spec=importlib.util.spec_from_file_location('two_task_parent',path)
    parent=importlib.util.module_from_spec(spec);spec.loader.exec_module(parent)
    child=assign(parent.child_source().decode(),'THREADS',tuple(THREADS))
    child=child.replace('LIMIT 400','LIMIT 80').replace('LIMIT 808','LIMIT 168').replace('len(records)<=808','len(records)<=168')
    child=child.replace('8388608','2097152').replace('12582912','4194304')
    child=child.replace('FIRST400_LAST400','FIRST80_LAST80').replace('len(records)<800','len(records)<160').replace('len(records)>=800','len(records)>=160')
    # The process may run before/after a rolling config update. This is a fresh
    # file/environment config snapshot for the selected Pod, not loaded service state.
    marker="cfg=get_config();db=cfg.database;need(db.backend=='postgres','POSTGRES_REQUIRED')"
    replacement="cfg=get_config();out['fresh_scheduler_for_selected_pod']={key:getattr(cfg.scheduler,key) for key in ('enabled','multi_instance','max_concurrent_runs','recursion_limit','queue_timeout_seconds')};out['fresh_config_not_loaded_scheduler_instance']=True;db=cfg.database;need(db.backend=='postgres','POSTGRES_REQUIRED')"
    assert child.count(marker)==1;child=child.replace(marker,replacement)
    child=child.replace('def runtime_modules():\n    import asyncpg',"def runtime_modules():\n    sys.path[:0]=['/app/backend/packages/harness','/app/backend']\n    import asyncpg")
    work=(HERE.parent/'energy-work-return-inspection-20261115-r1/child_extra.py').read_text(encoding='utf-8')
    names={'identifier','read_source','extracted','formatters','formatting_summary','inspect_thread'}
    constants={'RUN_SQL','OCCURRENCE_SQL','TASK_SQL','OUTBOX_SQL','CONNECTION_SQL','EVENT_SQL'}
    parts=[]
    for node in ast.parse(work).body:
        if isinstance(node,(ast.Import,ast.ImportFrom)) or isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in names:
            parts.append(ast.get_source_segment(work,node))
        elif isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id in constants for t in node.targets):
            parts.append(ast.get_source_segment(work,node))
    work='\n\n'.join(parts)
    # Rename syntax identifiers, not literal SQL or report strings.
    tree=ast.parse(work)
    for node in ast.walk(tree):
        if isinstance(node,ast.Name) and node.id in constants:node.id='NOTIFY_'+node.id
        if isinstance(node,ast.AsyncFunctionDef) and node.name=='inspect_thread':node.name='inspect_notification'
    work=ast.unparse(ast.fix_missing_locations(tree))+'\n'
    manifest=json.loads((ROOT/'release/energy-reader-work-repair-20261115-r2/SOURCE.json').read_bytes())
    expected={path.removeprefix('/app/backend/'):row for path,row in manifest['gateway_next_sources'].items() if '/app/scheduler/work_notif' in path}
    assert len(expected)==2
    before="def main():\n"
    assert child.count(before)==1
    child=child.replace(before,'EXPECTED_SOURCES='+repr(expected)+'\n\n'+work+'\n'+(HERE/'extra.py').read_text(encoding='utf-8')+'\n\n'+before)
    host=(HERE/'host_base.py').read_text(encoding='utf-8')
    host=host.replace("THREAD='aa98356f-e332-4019-9d68-0d1e79c30ed7'",'THREADS='+repr(THREADS))
    host=host.replace("{'thread_id':THREAD}","{'thread_ids':THREADS}").replace("'thread_id':THREAD","'thread_ids':THREADS")
    host=host.replace('energy-work-return-inspection.v1','energy-two-result-comparison.v1')
    host=host.replace('READ_ONLY_NAMED_TASK_NOTIFICATION_AND_FORMATTER_STATE','READ_ONLY_TWO_NAMED_TASK_EXECUTION_AND_NOTIFICATION')
    host=host.replace('DELETING:.metadata.deletionTimestamp\']','DELETING:.metadata.deletionTimestamp,CREATED:.metadata.creationTimestamp\']')
    host=host.replace("need(len(fields)==5,'POD_PROJECTION_COLUMNS_REQUIRED')","need(len(fields)==6,'POD_PROJECTION_COLUMNS_REQUIRED')")
    host=host.replace("if fields[2:]==['Running','True','<none>']:","if fields[2:5]==['Running','True','<none>']:")
    old="need(re.fullmatch('gateway-[a-z0-9-]{1,180}',fields[0]),'GATEWAY_POD_NAME_REQUIRED');choices.append(fields[:2])"
    new="need(re.fullmatch('gateway-[a-z0-9-]{1,180}',fields[0]) and re.fullmatch(r'[0-9TZ:.+-]{20,40}',fields[5]),'GATEWAY_POD_NAME_OR_TIMESTAMP_REQUIRED');choices.append(fields[:2]+[fields[5]])"
    assert host.count(old)==1;host=host.replace(old,new)
    host=host.replace("self.pod,uid=sorted(choices)[0]","self.pod,uid,_=max(choices,key=lambda row:(row[2],row[0]))")
    host=host.replace("need(out['observation'].get('status')=='WORK_RETURN_DIAGNOSTIC_COLLECTED_NO_CHANGE','READONLY_CHILD_INCOMPLETE')",
                      "need(out['observation'].get('requested_thread_ids')==THREADS and out['observation'].get('status') in ('TWO_TASKS_READONLY_COLLECTED','TWO_TASKS_PARTIALLY_OBSERVED'),'READONLY_CHILD_INCOMPLETE')")
    host=host.replace("out['status']='WORK_RETURN_DIAGNOSTIC_COLLECTED_NO_CHANGE'","out['status']='TWO_NAMED_TASK_RESULTS_OBSERVED_NO_CHANGE'")
    old="""        current=json.loads(self.command(['get','pod',self.pod,'-o','json']))
        need(pod_identity(current)==snapshot,'SAMPLE_CHANGED_DURING_INSPECTION')
        latest=json.loads(self.command(['get','deployment','gateway','-o','json']));need(identity(latest)==before,'GATEWAY_CHANGED_DURING_INSPECTION')
        return {'gateway':before,'sample':{'name':self.pod,'uid':uid},'stable_readback':True,'observation':result}"""
    new="""        stable=False;latest=dep;postcheck={}
        try:
            current=json.loads(self.command(['get','pod',self.pod,'-o','json']))
            latest=json.loads(self.command(['get','deployment','gateway','-o','json']))
            stable=pod_identity(current)==snapshot and identity(latest)==before
        except Exception as exc:
            postcheck={'error_class':type(exc).__name__,'error_code':str(exc) if isinstance(exc,Refused) else 'POSTCHECK_READ_REDACTED'}
        return {'gateway':before,'gateway_after':identity(latest),'gateway_replicas':{key:latest.get('status',{}).get(key) for key in ('replicas','updatedReplicas','readyReplicas','availableReplicas','terminatingReplicas')},
                'sample':{'name':self.pod,'uid':uid},'sample_selection':'NEWEST_READY_OWNED_POD','stable_readback':stable,'postcheck':postcheck,
                'database_observation_preserved_if_rollout_changes_sample':True,'observation':result,'this_tool_does_not_establish_rollout_acceptance':True}"""
    assert host.count(old)==1;host=host.replace(old,new)
    asset={'base64':base64.b64encode(child.encode()).decode(),'bytes':len(child.encode()),'sha256':hashlib.sha256(child.encode()).hexdigest()}
    host=assign(host,'ASSET',asset)
    for source in (host,child):compile(source,'<two-result-inspector>','exec');ast.parse(source,feature_version=(3,7))
    return host.encode(),child.encode()


def main():
    host,child=build();RELEASE.mkdir(exist_ok=True,parents=True)
    meta={'schema':'energy-two-result-comparison-package.v1','filename':NAME,'bytes':len(host),'sha256':hashlib.sha256(host).hexdigest(),
          'child_sha256':hashlib.sha256(child).hexdigest(),'thread_ids':THREADS,'production_executed':False,
          'database_readonly':True,'provider_requests':0,'work_messages_sent':0,'tasks_retried':False,
          'raw_messages_or_commands_exported':False,'execution_event_row_limit_per_thread':168}
    for name,raw in [(NAME,host),('SOURCE.json',(json.dumps(meta,sort_keys=True,indent=2)+'\n').encode())]:
        path=RELEASE/name
        if path.exists():assert path.read_bytes()==raw,'EXISTING_RELEASE_DIFFERS'
        else:path.write_bytes(raw)
    print(json.dumps(meta,sort_keys=True))


if __name__=='__main__':main()

"""Derive a configuration-only 32-slot, lease-aware Gateway rollout."""
import ast
import base64
import hashlib
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
RELEASE=ROOT/'release/scheduler-concurrency-20261116-r2'
NAME='activate-scheduler-concurrency-20261116-r2.py'
CONTROL='/var/lib/portal-scheduler-concurrency-20261116-r2'


def sha(raw):return hashlib.sha256(raw).hexdigest()


def assign(source,name,value):
    nodes=[node for node in ast.parse(source).body if isinstance(node,(ast.Assign,ast.AnnAssign)) and
           (isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in node.targets)
            or isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name) and node.target.id==name)]
    assert len(nodes)==1,name
    node=nodes[0];lines=source.splitlines(keepends=True)
    lines[node.lineno-1:node.end_lineno]=[name+' = '+repr(value)+'\n']
    return ''.join(lines)


def function(source,name,replacement):
    nodes=[node for node in ast.walk(ast.parse(source)) if isinstance(node,ast.FunctionDef) and node.name==name]
    assert len(nodes)==1,name
    node=nodes[0];lines=source.splitlines(keepends=True)
    lines[node.lineno-1:node.end_lineno]=[replacement+'\n']
    return ''.join(lines)


REPLACE='''def replace_scheduler(raw):
    need(len(raw)==OLD_CONFIG_BYTES and sha(raw)==OLD_CONFIG_SHA,'ORIGINAL_CONFIG_HASH_REQUIRED')
    lines=raw.splitlines(keepends=True)
    headers=[i for i,line in enumerate(lines) if re.fullmatch(br'scheduler:[ \\t]*(?:#[^\\r\\n]*)?\\r?\\n',line)]
    need(len(headers)==1,'SCHEDULER_BLOCK_NOT_UNIQUE')
    start=headers[0]+1
    end=next((i for i in range(start,len(lines)) if lines[i].strip() and not lines[i].startswith((b' ',b'\\t',b'#'))),len(lines))
    matches=[(i,re.fullmatch(br'(  max_concurrent_runs:[ \\t]*)3([ \\t]*(?:#[^\\r\\n]*)?)(\\r?\\n|$)',lines[i])) for i in range(start,end)]
    matches=[(i,m) for i,m in matches if m]
    explicit_concurrency=[i for i in range(start,end) if re.match(br'  max_concurrent_runs:',lines[i])]
    need(len(explicit_concurrency)<=1 and len(matches)==len(explicit_concurrency),'CONCURRENCY_SCALAR_NOT_UNIQUE')
    newline=b'\\r\\n' if lines[headers[0]].endswith(b'\\r\\n') else b'\\n'
    additions=[]
    if matches:
        i,m=matches[0];lines[i]=m[1]+b'32'+m[2]+m[3]
    else:
        additions.append(b'  max_concurrent_runs: 32'+newline)
    modes=[(i,re.fullmatch(br'(  multi_instance:[ \\t]*)false([ \\t]*(?:#[^\\r\\n]*)?)(\\r?\\n|$)',lines[i])) for i in range(start,end)]
    modes=[(i,m) for i,m in modes if m]
    explicit=[i for i in range(start,end) if re.match(br'  multi_instance:',lines[i])]
    need(len(explicit)<=1 and len(modes)==len(explicit),'MULTI_INSTANCE_SCALAR_INVALID')
    if modes:
        i,m=modes[0];lines[i]=m[1]+b'true'+m[2]+m[3]
    else:
        additions.append(b'  multi_instance: true'+newline)
    lines[start:start]=additions
    candidate=b''.join(lines)
    need(candidate!=raw,'CONFIGURATION_CHANGE_REQUIRED')
    return candidate
'''


QUALIFICATION='''    def qualification(self,pod,candidate=False):
        payload={'mode':'qualify' if candidate else 'verify','sources':self.plan['sources'],
                 'config':{'bytes':OLD_CONFIG_BYTES if candidate else self.plan['new_config_bytes'],
                           'sha256':OLD_CONFIG_SHA if candidate else self.plan['new_config_sha256'],
                           'max_concurrent_runs':3 if candidate else 32,'multi_instance':not candidate}}
        if candidate:
            payload['candidate']={'bytes':self.plan['new_config_bytes'],'sha256':self.plan['new_config_sha256'],
                                  'max_concurrent_runs':32,'multi_instance':True,
                                  'base64':base64.b64encode(self.manifest['data']['config.yaml'].encode('utf-8')).decode('ascii')}
        result=self.command(['exec','-i',pod,'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-u','-c',self.child],body=canonical(payload),timeout=40,limit=65536)
        if result.get('status')!='SCHEDULER_CONCURRENCY_READONLY_QUALIFIED':
            self.report['qualification_error_class']=result.get('error_class') if re.fullmatch('[A-Za-z][A-Za-z0-9_]{0,63}',str(result.get('error_class',''))) else 'UNKNOWN'
            self.report['qualification_error_code']=result.get('error_code') if re.fullmatch('[A-Z][A-Z0-9_]{0,95}',str(result.get('error_code',''))) else 'CHILD_QUALIFICATION_FAILED'
        need(result.get('status')=='SCHEDULER_CONCURRENCY_READONLY_QUALIFIED' and result.get('stable_readback') is True,'READONLY_CONFIG_QUALIFICATION_FAILED')
        return result
'''


CHECK_CONFIG='''def check_config(raw,expected):
    need(len(raw)==expected['bytes'] and digest(raw)==expected['sha256'],'CONFIG_HASH_CHANGED')
    parsed=parse_yaml(raw);scheduler=parsed.get('scheduler')
    need(isinstance(scheduler,dict),'SCHEDULER_MAPPING_REQUIRED')
    need(type(scheduler.get('recursion_limit')) is int and scheduler['recursion_limit']==10000,'SCHEDULER_BUDGET_CHANGED')
    need(type(parsed.get('max_recursion_limit')) is int and parsed['max_recursion_limit']==10000,'MAX_RECURSION_LIMIT_CHANGED')
    need(type(scheduler.get('max_concurrent_runs',3)) is int and scheduler.get('max_concurrent_runs',3)==expected['max_concurrent_runs'],'CONCURRENCY_CHANGED')
    need(type(scheduler.get('multi_instance',False)) is bool and scheduler.get('multi_instance',False) is expected['multi_instance'],'MULTI_INSTANCE_MODE_CHANGED')
    sys.path[:0]=['/app/backend/packages/harness','/app/backend']
    from deerflow.config.run_ownership_config import RunOwnershipConfig
    from deerflow.config.scheduler_config import SchedulerConfig
    need(RunOwnershipConfig(**parsed.get('run_ownership',{})).heartbeat_enabled is True,'RUN_OWNERSHIP_HEARTBEAT_REQUIRED')
    model=SchedulerConfig(**scheduler)
    need(model.max_concurrent_runs==expected['max_concurrent_runs'] and model.multi_instance is expected['multi_instance'],'SCHEDULER_SCHEMA_REJECTED')
    return parsed
'''


def build():
    parent=HERE.parent/'scheduler-budget-20261113-r5'
    # Frozen original implementation, including already field-qualified Pod
    # defaulting, ConfigMap closure, CAS guards and endpoint verification.
    host=(HERE/'operator_base.py').read_text(encoding='utf-8')
    child=(HERE/'child_base.py').read_text(encoding='utf-8')
    assert host==(parent/'activate-scheduler-budget-20261113-r5.py').read_text(encoding='utf-8')
    assert child==(parent/'qualify_budget_child.py').read_text(encoding='utf-8')
    namespace={'__name__':'parent_definitions'};exec(compile(host,'<parent>','exec'),namespace)
    manifest=json.loads((ROOT/'release/energy-reader-work-repair-20261115-r2/SOURCE.json').read_bytes())
    expected=dict(manifest['gateway_next_sources'])
    sources={'/app/backend/app/scheduler/service.py':{'bytes':28780,'sha256':'ab48b5861cec9eff14cb9dea062c8a16dfa62df94e8801dcb9b53f584e8ba263'},
             '/app/backend/packages/harness/deerflow/persistence/scheduled_task_runs/sql.py':{'bytes':39510,'sha256':'7c056874bcbdcede1743fbe55b498f2df5a7d9d12cdea3a38af03703d9462281'}}
    ownership=ROOT/'backend/packages/harness/deerflow/config/run_ownership_config.py'
    raw=ownership.read_bytes()
    sources['/app/backend/packages/harness/deerflow/config/run_ownership_config.py']={'bytes':len(raw),'sha256':sha(raw)}
    expected.update(sources)
    assert len(expected)==17
    overlays=dict(namespace['OVERLAYS'])
    overlays['gateway-reader-notify-0a8ffbe51790']=overlays.pop(namespace['LINK_CM'])
    overlays['gateway-task-context-86bbcfd1fb7f']={'tools.py':'/app/backend/packages/harness/deerflow/sandbox/tools.py'}
    core=dict(namespace['CORE_SOURCES'])
    core.update({path:(row['bytes'],row['sha256']) for path,row in sources.items()})
    for name,value in [('BEFORE_GENERATION',418),('BEFORE_SPEC_SHA','ebdfb3f85c151a67b12c6aed4f145b60330e19925996194f201f31d839ee0c0e'),
                       ('OLD_CM','deer-flow-scheduler-budget-10000-b160057a087d8483'),
                       ('OLD_CONFIG_SHA','b160057a087d848310f4521162fbee07f25178812692c3b1ec253be3632b08bf'),('OLD_CONFIG_BYTES',15992),
                       ('LINK_CM','gateway-reader-notify-0a8ffbe51790'),('LABEL','portal.scheduler-concurrency/operation'),
                       ('CORE_SOURCES',core),('EXPECTED_SOURCES',expected),('OVERLAYS',overlays)]:host=assign(host,name,value)
    host=host.replace("CONTROL = Path('/var/lib/portal-scheduler-budget-20261113-r5')","CONTROL = Path('"+CONTROL+"')")
    host=host.replace('replace_budget','replace_scheduler')
    host=function(host,'replace_scheduler',REPLACE)
    host=function(host,'qualification',QUALIFICATION)
    host=host.replace("len(result) == 13","len(result) == 17").replace('verified_source_files_per_pod=13','verified_source_files_per_pod=17')
    host=host.replace('scheduler-budget-activation.v1','scheduler-concurrency-activation.v1').replace('scheduler-budget-private-plan.v1','scheduler-concurrency-private-plan.v1')
    host=host.replace("'tool_revision': 'r5'","'tool_revision': 'r2'")
    host=host.replace("'deer-flow-scheduler-budget-10000-'","'deer-flow-scheduler-concurrency-32-'")
    host=host.replace('--activate-scheduler-budget-once','--activate-concurrency-once')
    host=host.replace('SCHEDULER_BUDGET_10000_DEPLOYED_RUNTIME_ACCEPTED','SCHEDULER_CONCURRENCY_32_DEPLOYED_RUNTIME_ACCEPTED')
    host=host.replace('GATEWAY_416','GATEWAY_419').replace('GATEWAY_415','GATEWAY_418')
    host=host.replace("'future_scheduled_runs_budget': 10000, 'global_max_recursion_limit': 10000,",
                      "'future_scheduled_runs_budget': 10000, 'global_max_recursion_limit': 10000,\n            'scheduler_max_concurrent_runs_before':3,'scheduler_max_concurrent_runs_after':32,\n            'scheduler_multi_instance_before':False,'scheduler_multi_instance_after':True,\n            'configuration_scope':'SCHEDULER_CONCURRENCY_AND_LEASE_AWARE_MULTI_INSTANCE_ONLY',")
    old="'config_sha256', 'candidate_sha256', 'scheduler_recursion_limit', 'candidate_scheduler_recursion_limit',\n                        'max_recursion_limit', 'source_files_verified', 'versions', 'semantic_change_only_scheduler_recursion_limit'"
    new="'config_sha256','candidate_sha256','scheduler_recursion_limit','max_recursion_limit','max_concurrent_runs',\n                        'candidate_max_concurrent_runs','candidate_multi_instance','source_files_verified','versions',\n                        'semantic_change_only_scheduler_concurrency_and_multi_instance','run_ownership_heartbeat_enabled'"
    assert host.count(old)==1;host=host.replace(old,new)
    # Submit once and return promptly. Rollout progress and final acceptance
    # are resumed with the same read-only entry, never another activation.
    old='''            self.cutover()
            self.verify(self.rollout())'''
    new='''            self.cutover()
            current=self.get('deployment','gateway')
            guard_deployment(current,self.expected,BEFORE_GENERATION+1)
            if is_converged(current,BEFORE_GENERATION+1):
                self.verify(current)
            else:
                self.report.update(status='GATEWAY_419_CONCURRENCY_32_ROLLOUT_REQUESTED',
                    rollout_requested_by_this_operation=True,verify_readonly_available=True,
                    followup_command='sudo python3 -I -B -u /home/cnooc/activate-scheduler-concurrency-20261116-r2.py --verify-readonly',
                    current=dict(identity(current),**{key:current.get('status',{}).get(key,0) for key in ('observedGeneration','replicas','updatedReplicas','readyReplicas','availableReplicas','terminatingReplicas')}))'''
    assert host.count(old)==1;host=host.replace(old,new)
    old="('SCHEDULER_CONCURRENCY_32_DEPLOYED_RUNTIME_ACCEPTED', 'GATEWAY_419_ROLLOUT_IN_PROGRESS_NO_CHANGE')"
    new="('SCHEDULER_CONCURRENCY_32_DEPLOYED_RUNTIME_ACCEPTED', 'GATEWAY_419_ROLLOUT_IN_PROGRESS_NO_CHANGE', 'GATEWAY_419_CONCURRENCY_32_ROLLOUT_REQUESTED')"
    assert host.count(old)==1;host=host.replace(old,new)
    host=host.replace('Gateway 415 -> 416: only scheduled-run budget 300 -> 10000.',
                      'Gateway 418 -> 419: global scheduler concurrency 3 -> 32 and lease-aware multi-instance mode.')
    child=assign(child,'SOURCE_PATHS',tuple(sorted(expected)))
    child=child.replace('set(expected_sources) == SOURCE_PATHS','set(expected_sources) == set(SOURCE_PATHS)')
    child=function(child,'check_config',CHECK_CONFIG)
    child=child.replace('scheduler-budget-qualification-child.v1','scheduler-concurrency-qualification-child.v1')
    child=child.replace('SCHEDULER_BUDGET_READONLY_QUALIFIED','SCHEDULER_CONCURRENCY_READONLY_QUALIFIED')
    child=child.replace("payload['config']['scheduler_recursion_limit']","10000")
    old="'max_recursion_limit': 10000}"
    new="'max_recursion_limit': 10000,'max_concurrent_runs':payload['config']['max_concurrent_runs'],\n              'scheduler_multi_instance':payload['config']['multi_instance'],'run_ownership_heartbeat_enabled':True}"
    assert child.count(old)==1;child=child.replace(old,new)
    child=child.replace("{'base64', 'bytes', 'sha256', 'scheduler_recursion_limit'}","{'base64','bytes','sha256','max_concurrent_runs','multi_instance'}")
    old="""        after['scheduler']['recursion_limit'] = 300
        need(before == after and before['scheduler']['recursion_limit'] == 300, 'YAML_SEMANTIC_SCOPE_EXCEEDED')
        need(candidate['scheduler_recursion_limit'] == 10000, 'CANDIDATE_BUDGET_REQUIRED')
        result.update(candidate_sha256=digest(next_raw), candidate_scheduler_recursion_limit=10000,
                      semantic_change_only_scheduler_recursion_limit=True)"""
    new="""        import copy
        expected=copy.deepcopy(before)
        expected['scheduler'].update(max_concurrent_runs=32,multi_instance=True)
        need(after==expected and before['scheduler'].get('max_concurrent_runs',3)==3
             and before['scheduler'].get('multi_instance',False) is False,'YAML_SEMANTIC_SCOPE_EXCEEDED')
        need(candidate['max_concurrent_runs']==32 and candidate['multi_instance'] is True,'CANDIDATE_CONCURRENCY_REQUIRED')
        result.update(candidate_sha256=digest(next_raw),candidate_max_concurrent_runs=32,candidate_multi_instance=True,
                      semantic_change_only_scheduler_concurrency_and_multi_instance=True)"""
    assert child.count(old)==1;child=child.replace(old,new)
    asset={'base64':base64.b64encode(child.encode()).decode(),'sha256':sha(child.encode())}
    host=assign(host,'CHILD_ASSET',asset)
    for source in (host,child):compile(source,'<concurrency-release>','exec');ast.parse(source,feature_version=(3,7))
    return host.encode(),child.encode(),expected


def main():
    host,child,expected=build();RELEASE.mkdir(exist_ok=True,parents=True)
    outputs={NAME:host,'qualify_concurrency_child.sealed.py':child}
    metadata={'schema':'scheduler-concurrency-release.v1','filename':NAME,'bytes':len(host),'sha256':sha(host),
              'child_sha256':sha(child),'expected_sources':expected,'before_gateway_generation':418,'after_gateway_generation':419,
              'concurrency_before':3,'concurrency_after':32,'multi_instance_before':False,'multi_instance_after':True,
              'scheduler_recursion_limit':10000,'global_max_recursion_limit':10000,'production_executed':False,
              'activation_returns_after_patch':'READONLY_VERIFICATION_REQUIRED','automatic_retry':False,'automatic_rollback':False}
    outputs['SOURCE.json']=(json.dumps(metadata,sort_keys=True,indent=2)+'\n').encode()
    for name,raw in outputs.items():
        path=RELEASE/name
        if path.exists():assert path.read_bytes()==raw,'EXISTING_RELEASE_DIFFERS'
        else:path.write_bytes(raw)
    print(json.dumps({k:v for k,v in metadata.items() if k!='expected_sources'},sort_keys=True))


if __name__=='__main__':main()

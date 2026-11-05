"""Configuration-only rollout: synthetic Kubernetes, never production."""
import ast
import copy
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

PATH = Path(__file__).with_name('set-portal-oa-run-timeout-60m-20261103-r1.py')
spec = importlib.util.spec_from_file_location('oa_timeout_setting', PATH)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def deployment(explicit=False):
    env = [{'name':'OA_POLICY_FLOW_CALLBACK_ENABLED','value':'false'},
           {'name':'PRIVATE_UNRELATED','value':'synthetic-secret'}]
    if explicit:
        env.append({'name':m.KEY,'value':'1800'})
    return {'kind':'Deployment','metadata':{'name':'gateway','namespace':'deer-flow','uid':m.UID,
            'generation':403,'resourceVersion':'version-1'},
            'spec':{'replicas':70,'strategy':{'type':'RollingUpdate','rollingUpdate':{'maxSurge':1,'maxUnavailable':0}},
                    'template':{'spec':{'containers':[{'name':'runner','image':'unchanged'},
                        {'name':'gateway','image':m.IMAGE,'env':env,'resources':{'limits':{'memory':'4Gi'}}}]}}},
            'status':{'observedGeneration':403,'replicas':70,'updatedReplicas':70,'readyReplicas':70,'availableReplicas':70}}


def apply_patch(dep, patch):
    after = copy.deepcopy(dep)
    for op in patch:
        keys = op['path'].strip('/').split('/')
        target = after
        for key in keys[:-1]:
            target = target[int(key)] if isinstance(target, list) else target[key]
        key = int(keys[-1]) if isinstance(target, list) else keys[-1]
        if op['op'] == 'test':
            if target[key] != op['value']:
                raise m.Refused('SYNTHETIC_CAS_FAILED')
        else:
            assert op['op'] == 'add'
            target[key] = op['value']
    after['metadata']['generation'] = 404
    after['metadata']['resourceVersion'] = 'version-2'
    after['status']['observedGeneration'] = 404
    return after


@pytest.mark.parametrize('explicit',[False,True])
def test_only_timeout_env_changes_and_receipt_redacts_other_env(explicit):
    dep = deployment(explicit)
    original = copy.deepcopy(dep)
    plan, patch = m.make_plan(dep)
    assert dep == original
    after = apply_patch(dep, patch)
    assert m.digest(after['spec']) == plan['after_spec_sha256']
    env = after['spec']['template']['spec']['containers'][1]['env']
    assert [v for v in env if v['name']==m.KEY] == [{'name':m.KEY,'value':'3600'}]
    assert 'synthetic-secret' not in json.dumps(plan)
    restored = copy.deepcopy(after['spec'])
    restored['template']['spec']['containers'][1]['env'] = original['spec']['template']['spec']['containers'][1]['env']
    assert restored == original['spec']
    assert len(patch)==4 and [p['op'] for p in patch[:3]]==['test']*3


@pytest.mark.parametrize('defect',['uid','generation','unstable','image','strategy','duplicate','value_from','timeout'])
def test_preflight_rejects_drift(defect):
    dep = deployment()
    container = dep['spec']['template']['spec']['containers'][1]
    if defect=='uid': dep['metadata']['uid']='wrong'
    elif defect=='generation': dep['metadata']['generation']=405
    elif defect=='unstable': dep['status']['readyReplicas']=69
    elif defect=='image': container['image']='wrong'
    elif defect=='strategy': dep['spec']['strategy']['type']='Recreate'
    elif defect=='duplicate': container['env'].append(container['env'][0])
    elif defect=='value_from': container['env'].append({'name':m.KEY,'valueFrom':{'secretKeyRef':{'name':'private'}}})
    else: container['env'].append({'name':m.KEY,'value':'7200'})
    with pytest.raises(m.Refused): m.make_plan(dep)


def test_compare_and_swap_stops_concurrent_mutation():
    dep = deployment()
    _, patch = m.make_plan(dep)
    dep['spec']['replicas']=71
    with pytest.raises(m.Refused,match='CAS_FAILED'): apply_patch(dep,patch)


def test_unrelated_status_refresh_does_not_invalidate_spec_compare_and_swap():
    dep=deployment()
    plan,patch=m.make_plan(dep)
    dep['metadata']['resourceVersion']='status-refreshed'
    after=apply_patch(dep,patch)
    assert m.digest(after['spec'])==plan['after_spec_sha256']


def fake_host(monkeypatch):
    monkeypatch.setattr(m,'sys',SimpleNamespace(platform='linux',flags=SimpleNamespace(isolated=True),dont_write_bytecode=True))
    monkeypatch.setattr(m,'os',SimpleNamespace(**vars(os)))
    monkeypatch.setattr(m.os,'path',SimpleNamespace(**vars(os.path)))
    monkeypatch.setattr(m.os,'geteuid',lambda:0,raising=False)
    monkeypatch.setattr(m.os,'uname',lambda:SimpleNamespace(nodename='datax-01'),raising=False)


@pytest.mark.parametrize('uncertain',[False,True])
def test_one_patch_only_reported_even_if_outcome_unknown(monkeypatch,uncertain):
    fake_host(monkeypatch)
    monkeypatch.setattr(m.os.path,'lexists',lambda path:False)
    monkeypatch.setattr(m,'open_control',lambda create:12345)
    monkeypatch.setattr(m.os,'close',lambda fd:None)
    saved={}
    monkeypatch.setattr(m,'save_new',lambda fd,name,value:saved.setdefault(name,copy.deepcopy(value)))
    monkeypatch.setattr(m,'pods',lambda dep:[['pod-1']])
    monkeypatch.setattr(m,'probe',lambda name:{'run_timeout_seconds':1800,'callback_timeout_seconds':30})
    dep=deployment()
    calls=[]
    def query(args,**kwargs):
        calls.append((args,kwargs))
        if args[0]=='get': return dep
        assert len(calls)==2 and args[:3]==['patch','deployment','gateway']
        assert '--patch-file=/dev/stdin' in args and 'synthetic-secret' not in str(args)
        if uncertain: raise m.Refused('COMMAND_TIMEOUT_OUTCOME_REVIEW_REQUIRED')
        return apply_patch(dep,json.loads(kwargs['payload']))
    monkeypatch.setattr(m,'query',query)
    def verify(plan,out,wait):
        assert wait==3600
        out.update(status='OA_RUN_TIMEOUT_60M_ACCEPTED',runtime_pods_verified=70)
    monkeypatch.setattr(m,'verify',verify)
    out=m.run(['--set-oa-run-timeout-60m-once'])
    assert out['patch_request_count']==1 and out['patch_may_have_applied'] is True
    assert {'PLAN.json','STARTED.json','REPORT.json'}==set(saved)
    assert out['status']==('INCOMPLETE' if uncertain else 'OA_RUN_TIMEOUT_60M_ACCEPTED')
    assert 'synthetic-secret' not in json.dumps(out)


def test_existing_marker_refuses_before_any_cluster_request(monkeypatch):
    fake_host(monkeypatch)
    monkeypatch.setattr(m.os.path,'lexists',lambda path:True)
    monkeypatch.setattr(m,'query',lambda *a,**kw:pytest.fail('unexpected cluster request'))
    out=m.run(['--set-oa-run-timeout-60m-once'])
    assert out['error_code']=='ONCE_EXISTS_REVIEW_REQUIRED' and out['patch_request_count']==0


def test_verification_is_readonly_and_checks_all_70_effective_values(monkeypatch):
    dep=deployment()
    plan,patch=m.make_plan(dep)
    plan['callback_timeout_seconds_before']=30
    after=apply_patch(dep,patch)
    monkeypatch.setattr(m,'query',lambda args,**kw:copy.deepcopy(after) if args[0]=='get' else pytest.fail('write'))
    monkeypatch.setattr(m,'pods',lambda dep:[['pod-'+str(i)] for i in range(70)])
    calls=[]
    def probe(name):
        calls.append(name)
        return {'run_timeout_seconds':3600,'callback_timeout_seconds':30}
    monkeypatch.setattr(m,'probe',probe)
    out={}
    m.verify(plan,out,0)
    assert len(calls)==70 and out['runtime_pods_verified']==70
    assert out['status']=='OA_RUN_TIMEOUT_60M_ACCEPTED'


def test_compact_pod_read_rejects_foreign_replicaset(monkeypatch):
    dep=deployment()
    rows=['\t'.join(['gateway-abc-'+str(i),'uid-'+str(i),'gateway-abc','rsuid','Running','','true','0','container-'+str(i),m.IMAGE]) for i in range(70)]
    def query(args,**kwargs):
        if args[1]=='pods':
            assert 'jsonpath=' in args[-1] and kwargs['limit']==65536
            return '\n'.join(rows)
        return {'metadata':{'uid':'rsuid','ownerReferences':[{'controller':True,'kind':'Deployment','uid':'wrong'}]}}
    monkeypatch.setattr(m,'query',query)
    with pytest.raises(m.Refused,match='RS_OWNER_CHANGED'): m.pods(dep)


def test_host_and_probe_syntax_and_no_business_imports():
    ast.parse(PATH.read_text(encoding='utf-8'),feature_version=(3,7))
    ast.parse(m.PROBE,feature_version=(3,7))
    assert 'runpy.run_path' in m.PROBE
    assert 'database' not in m.PROBE.split("print(json.dumps")[0]


@pytest.mark.skipif(sys.platform!='linux',reason='Linux isolation and file permissions')
def test_real_linux_wrong_host_and_probe_config_loader(monkeypatch):
    completed=subprocess.run([sys.executable,'-I','-B',str(PATH),'--set-oa-run-timeout-60m-once'],capture_output=True,text=True,timeout=10)
    assert completed.returncode==1 and 'DATAX01_ROOT_REQUIRED' in completed.stdout
    root=PATH.parents[2]
    source=root/'backend/app/gateway/oa_policy_flow_config.py'
    env={k:v for k,v in os.environ.items() if not k.startswith('OA_POLICY_FLOW_')}
    env.update(OA_POLICY_FLOW_RUN_TIMEOUT_SECONDS='3600',OA_POLICY_FLOW_CALLBACK_ENABLED='false')
    probe=m.PROBE.replace('/app/backend/app/gateway/oa_policy_flow_config.py',str(source))
    result=subprocess.run([sys.executable,'-I','-B','-c',probe],env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode==0
    assert json.loads(result.stdout)['run_timeout_seconds']==3600

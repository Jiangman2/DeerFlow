import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

HERE = Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('cleanup',HERE/'cleanup-portal-research-fund-skill-20261104-r1.py')
c=importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)

def pod():
    return {'apiVersion':'v1','kind':'Pod','metadata':{'name':c.POD,'namespace':c.NAMESPACE,'uid':c.UID,
            'labels':{'app':'research-fund-skill-maintenance','deerflow-release':c.OPERATION}},
            'spec':{'restartPolicy':'Never','containers':[{'name':'maintenance',
            'command':['python3','-I','-B','-c','import time; time.sleep(1800)']}]}}

def prior():
    return {'operation':c.OPERATION,'maintenance_pod':c.POD,'maintenance_pod_uid':c.UID,
            'skill_updated':True,'runtime_node_views_accepted':True,'after_tree_sha256':c.TREE,
            'workload_patch_requests':0,'production_database_access':False,
            'after_consumers':[{'node':'ecs-zdz-pro-00'+str(i),'status':'PASS','tree_sha256':c.TREE}
                               for i in range(18,23)]}

@pytest.mark.parametrize('field,value',[('operation','other'),('maintenance_pod','gateway-any'),
    ('maintenance_pod_uid','other'),('skill_updated',False),('runtime_node_views_accepted',False),
    ('after_tree_sha256','other'),('after_consumers',[]),('workload_patch_requests',1),('production_database_access',True)])
def test_bad_receipt_rejected(field,value):
    data=prior();data[field]=value
    with pytest.raises(c.Refused):c.validate_prior(data)

@pytest.mark.parametrize('field,value',[('uid','other'),('name','gateway-any'),('namespace','other'),
    ('ownerReferences',[{'uid':'other'}]),('labels',{})])
def test_foreign_pod_rejected(field,value):
    data=pod();data['metadata'][field]=value
    with pytest.raises(c.Refused):c.verify_pod(data)

def test_non_maintenance_command_rejected():
    data=pod();data['spec']['containers'][0]['command']=['uvicorn']
    with pytest.raises(c.Refused):c.verify_pod(data)

def test_already_absent_does_not_delete(monkeypatch):
    calls=[]
    def command(args,payload=None):calls.append(args);return None
    monkeypatch.setattr(c,'command',command)
    out={'delete_request_count':0}
    c.cleanup(out)
    assert calls==[c.GET_ARGS] and out['status']=='MAINTENANCE_POD_ALREADY_ABSENT'

def test_verified_uid_is_deleted_exactly_once(monkeypatch):
    values=iter([pod(),{'status':'Success'},None]);calls=[]
    def command(args,payload=None):calls.append((args,payload));return next(values)
    monkeypatch.setattr(c,'command',command)
    monkeypatch.setattr(c,'persist',lambda _:None)
    out={};c.cleanup(out)
    assert out['status']=='MAINTENANCE_POD_CLEANED'
    assert out['delete_request_count']==1
    assert calls[1]==(c.DELETE_ARGS,c.delete_options())

def test_deletion_uncertainty_not_retried(monkeypatch):
    calls=[]
    def command(args,payload=None):
        calls.append(args)
        if args==c.DELETE_ARGS:raise subprocess.TimeoutExpired('kubectl',20)
        return pod()
    monkeypatch.setattr(c,'command',command)
    monkeypatch.setattr(c,'persist',lambda _:None)
    out={}
    with pytest.raises(subprocess.TimeoutExpired):c.cleanup(out)
    assert out['delete_may_have_applied'] is True
    assert calls.count(c.DELETE_ARGS)==1

def test_in_progress_delete_only_observed(monkeypatch):
    p=pod();p['metadata']['deletionTimestamp']='2026-11-04T00:00:00Z'
    values=iter([p,None]);calls=[]
    def command(args,payload=None):calls.append(args);return next(values)
    monkeypatch.setattr(c,'command',command)
    out={'delete_request_count':0};c.cleanup(out)
    assert calls==[c.GET_ARGS,c.GET_ARGS] and out['delete_request_count']==0

def test_only_fixed_commands_and_uid_options_allowed():
    with pytest.raises(c.Refused,match='UNEXPECTED_COMMAND'):c.command(['delete','pod','gateway'])
    with pytest.raises(c.Refused,match='DELETE_PRECONDITION_CHANGED'):c.command(c.DELETE_ARGS,b'{}')

def test_wrong_host_guard(monkeypatch,capsys):
    monkeypatch.setattr(c.socket,'gethostname',lambda:'local-test')
    monkeypatch.setattr(c.os,'geteuid',lambda:0,raising=False)
    monkeypatch.setattr(c,'read_prior',lambda:pytest.fail('unexpected receipt access'))
    import sys
    monkeypatch.setattr(sys,'argv',['cleanup','--cleanup-fixed-maintenance-pod-once'])
    assert c.main()==1
    assert json.loads(capsys.readouterr().out)['error_code']=='WRONG_HOST'

def test_exact_receipt_hash_required(tmp_path,monkeypatch):
    path=tmp_path/'REPORT.json';path.write_bytes(b'x'*6718)
    monkeypatch.setattr(c,'PRIOR',path)
    with pytest.raises(c.Refused,match='PRIOR_HASH_CHANGED'):c.read_prior()
    assert path.read_bytes()==b'x'*6718

def test_real_kubectl_raw_delete_sends_uid_precondition(tmp_path,monkeypatch):
    binary=os.environ.get('FINANCE_TEST_KUBECTL') or shutil.which('kubectl')
    if not binary:pytest.skip('local kubectl not available')
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def reply(self,value,code=200):
            payload=json.dumps(value).encode()
            self.send_response(code);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        def do_GET(self):
            path=urlsplit(self.path).path
            if path=='/api':return self.reply({'kind':'APIVersions','apiVersion':'v1','versions':['v1'],'serverAddressByClientCIDRs':[]})
            if path=='/apis':return self.reply({'kind':'APIGroupList','apiVersion':'v1','groups':[]})
            if path=='/api/v1':return self.reply({'kind':'APIResourceList','apiVersion':'v1','groupVersion':'v1',
                'resources':[{'name':'pods','singularName':'pod','namespaced':True,'kind':'Pod','verbs':['get','list','delete']}]})
            return self.reply(pod())
        def do_DELETE(self):
            if self.headers.get('Transfer-Encoding')=='chunked':
                chunks=[]
                while True:
                    size=int(self.rfile.readline().strip().split(b';')[0],16)
                    if not size:
                        self.rfile.readline();break
                    chunks.append(self.rfile.read(size));self.rfile.read(2)
                body=b''.join(chunks)
            else:
                body=self.rfile.read(int(self.headers.get('Content-Length','0')))
            data=json.loads(body)
            calls.append((urlsplit(self.path).path,data))
            self.reply({'kind':'Status','apiVersion':'v1','status':'Success','code':200,'details':{'name':c.POD,'uid':c.UID}})
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    config=tmp_path/'kubeconfig.json'
    config.write_text(json.dumps({'apiVersion':'v1','kind':'Config','clusters':[],'contexts':[],'users':[]}))
    base=[binary,'--kubeconfig='+str(config),'--server=http://127.0.0.1:'+str(server.server_port),
          '--request-timeout=3s','-n',c.NAMESPACE]
    monkeypatch.setattr(c,'BASE',base)
    try:
        result=c.command(c.DELETE_ARGS,c.delete_options())
        assert result['status']=='Success'
        assert calls==[('/api/v1/namespaces/'+c.NAMESPACE+'/pods/'+c.POD,
                        {'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':c.UID}})]
        # Reproduce the original cleanup's invalid output mode through the real CLI.
        bad=subprocess.run(base+['delete','pod',c.POD,'--wait=false','-o','json'],capture_output=True,timeout=15)
        assert bad.returncode!=0
        assert b'output' in bad.stderr.lower() and b'json' in bad.stderr.lower(),bad.stderr
        print('ORIGINAL_OUTPUT_MODE_REJECTED:',bad.stderr.decode().strip(),
              'delete_requests_before_output_error=',len(calls)-1)
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)

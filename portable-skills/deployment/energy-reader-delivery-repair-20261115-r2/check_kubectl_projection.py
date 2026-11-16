"""Test real kubectl's column formatting against a localhost synthetic API."""
import base64
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from urllib.parse import urlsplit

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
KUBECTL=r'C:/Users/15285/AppData/Local/Programs/DockerDesktop/resources/bin/kubectl.exe'
requests=[]
pods={'apiVersion':'v1','kind':'PodList','metadata':{'resourceVersion':'1'},'items':[]}
for index in range(70):
    pods['items'].append({'apiVersion':'v1','kind':'Pod','metadata':{'name':'gateway-%04d'%index,'namespace':'deer-flow','uid':'uid-%04d'%index,
        'ownerReferences':[{'apiVersion':'apps/v1','kind':'ReplicaSet','name':'gateway-fixed-rs','uid':'rs-uid','controller':True}],
        'annotations':{'synthetic-noise':'x'*65536}},
        'spec':{'containers':[{'name':'gateway','image':'synthetic'}], 'volumes':[
            {'name':'sources','configMap':{'name':'gateway-fixed'}},{'name':'scratch','emptyDir':{}},
            {'name':'ca','secret':{'secretName':'SYNTHETIC_NOT_READ'}},{'name':'compat','configMap':{'name':'openssl-fixed'}}]},
        'status':{'phase':'Running','conditions':[{'type':'Ready','status':'True'}]}})

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        path=urlsplit(self.path).path;requests.append(path)
        if path=='/api':data={'kind':'APIVersions','apiVersion':'v1','versions':['v1'],'serverAddressByClientCIDRs':[]}
        elif path=='/apis':data={'kind':'APIGroupList','apiVersion':'v1','groups':[]}
        elif path=='/api/v1':data={'kind':'APIResourceList','apiVersion':'v1','groupVersion':'v1','resources':[{'name':'pods','singularName':'pod','namespaced':True,'kind':'Pod','verbs':['get','list']}]}
        elif path=='/api/v1/namespaces/deer-flow/pods':data=pods
        elif path=='/version':data={'major':'1','minor':'31','gitVersion':'v1.31.0'}
        else:self.send_response(404);self.end_headers();return
        raw=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)

def main():
    spec=importlib.util.spec_from_file_location('real_projection_sealer',HERE/'seal_repair.py')
    sealer=importlib.util.module_from_spec(spec);spec.loader.exec_module(sealer)
    raw,assets,_=sealer.build();scope={'__name__':'real_projection_operator'};exec(compile(raw,'<r2>','exec'),scope)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='energy-kubectl-projection-') as directory:
            root=Path(directory)
            config={'apiVersion':'v1','kind':'Config','clusters':[{'name':'fixture','cluster':{'server':'http://127.0.0.1:'+str(server.server_port)}}],
                    'contexts':[{'name':'fixture','context':{'cluster':'fixture','user':'fixture'}}],'current-context':'fixture','users':[{'name':'fixture','user':{}}]}
            path=root/'synthetic-kubeconfig.json';path.write_text(json.dumps(config))
            env=dict(os.environ)
            for key in ('HTTP_PROXY','http_proxy','HTTPS_PROXY','https_proxy','ALL_PROXY','all_proxy'):env.pop(key,None)
            env.update(NO_PROXY='127.0.0.1,localhost',no_proxy='127.0.0.1,localhost')
            result=subprocess.run([KUBECTL,'--kubeconfig='+str(path),'--cache-dir='+str(root/'cache'),'--request-timeout=5s','-n','deer-flow',
                                   'get','pods','-l','app=gateway','--no-headers','-o',scope['POD_COLUMNS']],env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
            assert result.returncode==0,result.stderr.decode('utf-8','replace')
            op=scope['Repair'](assets);op.command=lambda *args,**kwargs:result.stdout
            parsed=op.pods({'metadata':{'name':'gateway'},'spec':{'selector':{'matchLabels':{'app':'gateway'}}}})
            assert len(parsed)==70
            assert parsed[0]['spec']['volumes']==[{'configMap':{'name':'gateway-fixed'}},{'configMap':{'name':'openssl-fixed'}}]
            fullbytes=len(json.dumps(pods).encode());assert fullbytes>4194304 and len(result.stdout)<16384
            out={'schema':'real-kubectl-pod-projection.v1','status':'REAL_KUBECTL_PROJECTION_PASSED','synthetic_pods':70,
                 'full_json_bytes':fullbytes,'projected_stdout_bytes':len(result.stdout),'projected_stderr_bytes':len(result.stderr),
                 'columns':9,'production_access':False,'credentials_used':False,'provider_requests':0,'work_messages_sent':0}
            target=sealer.RELEASE/'REAL-KUBECTL-PROJECTION.json';encoded=(json.dumps(out,sort_keys=True,indent=2)+'\n').encode()
            if target.exists():assert target.read_bytes()==encoded,'EXISTING_PROJECTION_RECEIPT_DIFFERS'
            else:target.write_bytes(encoded)
            print(json.dumps(out))
    finally:server.shutdown();server.server_close();thread.join(timeout=2)

if __name__=='__main__':main()

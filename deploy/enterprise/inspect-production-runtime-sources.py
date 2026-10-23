"""Read installed Python-source and public UI-asset hashes; Python 3.7+.

Uses fixed read-only kubectl exec commands in one ready Pod per component.
No source contents, environment values, credentials, provider calls, database
connections, or workload/configuration changes.
"""

import argparse
import collections
import datetime
import hashlib
import json
import re
import subprocess
import threading
import time


BASE = ["kubectl", "--kubeconfig=/etc/kubernetes/admin.conf", "--request-timeout=15s", "-n", "deer-flow"]
LIMIT = 24 * 1024 * 1024
DEADLINE_SECONDS = 300
EXACT_NAMES = {"gateway", "gateway-run-dispatcher", "frontend", "nginx", "ppt-master-runner", "provisioner"}
PREFIXES = ("weknora-", "mineru-", "deer-flow-redis", "gateway-redis")
SECRET_NAME = re.compile(r"PASSWORD|SECRET|TOKEN|API_KEY|CREDENTIAL|AUTHORIZATION", re.I)
CAPACITY_NAME = re.compile(r"CONCURRENCY|WORKERS|TIMEOUT|MAX_FILE_SIZE|MEMORY_LIMIT|CPU_LIMIT|EPHEMERAL_STORAGE_LIMIT")
NUMBER = re.compile(r"[0-9]+(?:\.[0-9]+)?(?:m|Ki|Mi|Gi|Ti|ms|s)?\Z")


class Failure(Exception):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def selected(name):
    return name in EXACT_NAMES or name.startswith(PREFIXES)


class Reader:
    def __init__(self):
        self.started = time.monotonic()
        self.queries = 0

    def get(self, resource, name=None):
        if resource not in {"deployments", "statefulsets", "daemonsets", "services", "ingresses", "pods", "configmap", "replicasets"}:
            raise Failure("RESOURCE_NOT_ALLOWED")
        remaining = DEADLINE_SECONDS - (time.monotonic() - self.started)
        if remaining < 1 or self.queries >= 150:
            raise Failure("QUERY_BUDGET")
        args = BASE + ["get", resource] + ([name] if name else []) + ["-o", "json"]
        return self.command(args, remaining)

    def command(self, args, remaining=60):
        self.queries += 1
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        chunks = [bytearray(), bytearray()]
        overflow = threading.Event()

        def read(pipe, index):
            while True:
                block = pipe.read(65536)
                if not block:
                    break
                if len(chunks[index]) + len(block) > LIMIT:
                    overflow.set()
                    process.kill()
                    break
                chunks[index].extend(block)

        threads = [threading.Thread(target=read, args=(process.stdout, 0)), threading.Thread(target=read, args=(process.stderr, 1))]
        for thread in threads:
            thread.start()
        try:
            process.wait(timeout=min(60, remaining))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise Failure("QUERY_TIMEOUT")
        finally:
            for thread in threads:
                thread.join(timeout=5)
            process.stdout.close()
            process.stderr.close()
        if any(thread.is_alive() for thread in threads) or overflow.is_set():
            raise Failure("QUERY_OUTPUT_LIMIT")
        if process.returncode:
            raise Failure("QUERY_NONZERO")
        return json.loads(chunks[0])



PINS = {'gateway': {'uid': 'a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1', 'generation': 382, 'spec_sha256': '699119d2d3e7a2e30aad980d5c4f37560be9022b00059a2712b6512e4b3816ff', 'container': 'gateway', 'image': 'deerflow.local/deer-flow-gateway@sha256:eeeb190145dd6677e0da372bbdf36ab207a6826d89960eca2ed49de15d40dcc5'}, 'ppt-master-runner': {'uid': '4115f178-0599-445e-89c3-9d9452ddc26a', 'generation': 54, 'spec_sha256': 'a1ba61f7c0339e23788b2ce8629ac5d90c0f71c8bf82cec6958b01c02ca31b94', 'container': 'ppt-master-runner', 'image': 'deerflow.local/deer-flow-ppt-master-runner@sha256:8a853325c46a215c06d0ab77720aa3a8e48b06c98c60f2837c663867e704b0f0'}, 'weknora-frontend': {'uid': 'e2d0c5f6-abfe-4ba7-ae17-1700bcd3e401', 'generation': 37, 'spec_sha256': '8b3bba8ca0cd30b84256f543b3bb0b066b3d2695e77720882e50af68c5bc7994', 'container': 'frontend', 'image': 'deerflow.local/weknora-ui:preupload10-f25aea4dfb29-20261020062633'}}

REMOTE_PY = r"""
import os,pathlib,stat,hashlib,json,time
started=time.monotonic(); rows=[]; total=0
for root in (pathlib.Path('/app/backend/app'),pathlib.Path('/app/backend/packages/harness')):
 assert root.is_dir() and not root.is_symlink(),'SOURCE_ROOT_MISSING'
 for directory,dirs,files in os.walk(str(root),followlinks=False):
  dirs[:]=sorted(d for d in dirs if d not in ('__pycache__','.git','.venv') and not pathlib.Path(directory,d).is_symlink())
  for name in sorted(files):
   if not name.endswith('.py'):continue
   p=pathlib.Path(directory,name); before=p.lstat()
   assert stat.S_ISREG(before.st_mode) and not p.is_symlink(),'NONREGULAR_SOURCE'
   assert before.st_size<=2097152 and len(rows)<3000 and time.monotonic()-started<40,'SOURCE_LIMIT'
   raw=p.read_bytes();after=p.stat();total+=len(raw)
   assert total<=67108864 and (before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns),'SOURCE_CHANGED'
   rows.append({'path':'backend/'+p.relative_to('/app/backend').as_posix(),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'lf_sha256':hashlib.sha256(raw.replace(b'\r\n',b'\n')).hexdigest()})
print(json.dumps({'status':'RUNTIME_SOURCE_HASHES_COLLECTED','files':rows,'file_count':len(rows),'total_bytes':total,'contents_exported':False,'modified':False},sort_keys=True))
"""

# Fixed BusyBox-compatible program; only public static paths and entrypoint hashes.
REMOTE_UI = r"""
set -eu
root=/usr/share/nginx/html
test -d "$root"
test ! -L "$root"
test -z "$(find "$root" -type l -print -quit)"
find "$root" -type f \( -name '*.js' -o -name '*.css' -o -name '*.html' \) -exec sha256sum {} +
if test -f /docker-entrypoint.sh; then sha256sum /docker-entrypoint.sh; fi
"""


def binding(obj):
    return {'uid':obj['metadata']['uid'],'generation':obj['metadata']['generation'],'spec_sha256':digest(obj['spec'])}


def pod_identity(pod, container):
    status=next(c for c in pod['status']['containerStatuses'] if c['name']==container)
    if pod['metadata'].get('deletionTimestamp') or not status.get('ready') or not status.get('containerID'):
        raise Failure('POD_NOT_READY')
    return {'uid':pod['metadata']['uid'],'container_id':status['containerID'],'image_id':status.get('imageID')}


def collect_sources(reader):
    reports=[]
    for name,pin in PINS.items():
        dep=reader.get('deployments',name)
        if binding(dep)!={k:pin[k] for k in ('uid','generation','spec_sha256')}:
            raise Failure('BASELINE_CHANGED_'+name)
        status=dep.get('status',{});count=dep['spec']['replicas']
        if status.get('observedGeneration')!=pin['generation'] or any(status.get(k)!=count for k in ('replicas','updatedReplicas','readyReplicas','availableReplicas')):
            raise Failure('ROLLOUT_IN_PROGRESS_'+name)
        sets=reader.get('replicasets')['items']
        owners={r['metadata']['uid'] for r in sets if any(o.get('controller') and o.get('uid')==pin['uid'] for o in r['metadata'].get('ownerReferences',[]))}
        pods=reader.get('pods')['items']
        candidates=[p for p in pods if p.get('status',{}).get('phase')=='Running' and not p['metadata'].get('deletionTimestamp')
                    and any(o.get('controller') and o.get('uid') in owners for o in p['metadata'].get('ownerReferences',[]))
                    and any(c['name']==pin['container'] and c['image']==pin['image'] for c in p['spec']['containers'])
                    and any(c['name']==pin['container'] and c.get('ready') for c in p['status'].get('containerStatuses',[]))]
        if not candidates:raise Failure('NO_READY_SAMPLE_'+name)
        pod=sorted(candidates,key=lambda p:p['metadata']['name'])[0];pod_name=pod['metadata']['name'];identity=pod_identity(pod,pin['container'])
        args=BASE+['exec',pod_name,'-c',pin['container'],'--']
        if name=='weknora-frontend':
            # JSON is produced by a controller-side wrapper around a fixed hash-only shell command.
            data=read_ui(reader,args)
        else:
            data=reader.command(args+['/app/backend/.venv/bin/python','-I','-B','-c',REMOTE_PY])
        fresh=reader.get('pods',pod_name)
        if pod_identity(fresh,pin['container'])!=identity or binding(reader.get('deployments',name))!=binding(dep):
            raise Failure('SAMPLE_CHANGED_'+name)
        reports.append({'deployment':name,'binding':binding(dep),'pod':pod_name,'identity':identity,'observation':data})
        print(json.dumps({'phase':'RUNTIME_SOURCE_FINGERPRINT','checked':len(reports),'total':len(PINS)}),flush=True)
    return reports


def read_ui(reader,args):
    # Hash output is bounded by the same reader as JSON; encode it as JSON within
    # a local child, never interpolate an untrusted file path into the shell.
    import sys
    helper="""import subprocess,json,sys
p=subprocess.run(json.loads(sys.argv[1]),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=40)
assert p.returncode==0 and len(p.stdout)<=2097152 and len(p.stderr)<=65536,'UI_HASH_QUERY_FAILED'
rows=[]
for line in p.stdout.decode('utf-8').splitlines():
 digest,path=line.split(None,1)
 assert len(digest)==64 and all(c in '0123456789abcdef' for c in digest),'BAD_HASH'
 assert path.startswith('/usr/share/nginx/html/') or path=='/docker-entrypoint.sh','UNEXPECTED_PATH'
 rows.append({'path':path,'sha256':digest})
assert 0<len(rows)<=5000,'UI_FILE_COUNT'
print(json.dumps({'status':'PUBLIC_ASSET_HASHES_COLLECTED','files':rows,'contents_exported':False,'source_equivalence_proven':False}))
"""
    return reader.command([sys.executable,'-I','-B','-c',helper,json.dumps(args+['sh','-c',REMOTE_UI])])


def main():
    parser=argparse.ArgumentParser(description='Read installed source and public asset hashes; no service changes.')
    parser.add_argument('--inspect-runtime-sources-readonly',action='store_true',required=True)
    parser.parse_args()
    reader=Reader();result={'schema':'portal-production-runtime-sources.v1','workload_modified':False,'configuration_modified':False,'source_contents_exported':False,'credentials_exported':False,'provider_requests':0,'scope':'ONE_READY_POD_PER_COMPONENT_NOT_ALL_REPLICAS','status':'INCOMPLETE'}
    try:
        result['components']=collect_sources(reader);result['status']='RUNTIME_FINGERPRINTS_COLLECTED'
    except Exception as exc:
        result.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Failure) else 'READ_FAILED')
    result['queries']=reader.queries
    print(json.dumps(result,sort_keys=True))
    return 0 if result['status']=='RUNTIME_FINGERPRINTS_COLLECTED' else 1


if __name__=='__main__':raise SystemExit(main())

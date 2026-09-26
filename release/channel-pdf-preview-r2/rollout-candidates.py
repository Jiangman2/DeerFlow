#!/usr/bin/env python3
"""Explicit short-maintenance release of the validated R2 Gateway and Frontend."""
import argparse
import copy
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import uuid

HELPER_SHA = 'bfaa7d739dfd4797db5aa0d90bfbd27a3157fcc8f65a34917ba069ba5ade3542'
NS = 'deer-flow'
ROOT = Path('/opt/deerflow-hotfix-evidence')
BASE_IDS = {
    'gateway': {'d999f24b6b442f4f9aac48c62b9004acd4dcbcb03fe4da7169369b459db8e857',
                'c0baadb84be1fb8d875f60841be504edffd0a51909656b0f8e2009684aa212b1'},
    'frontend': {'304da3f5ee994c5913e2a3389da7972a4437dbcd9a47fc22580612a8cf145b30',
                 'f6de606a554626f72b869f43f7f68c602f8146d0155cddfb6acb51bc48be9abd'},
}


def emit(message):
    print(message, flush=True)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(spec):
    return sha(json.dumps(spec, sort_keys=True).encode())


def load_helper(path):
    if sha(path.read_bytes()) != HELPER_SHA:
        raise RuntimeError('candidate validator checksum mismatch')
    spec = importlib.util.spec_from_file_location('channel_pdf_candidate_validation', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def image_index(spec, component):
    matches = [i for i, c in enumerate(spec['template']['spec']['containers']) if c['name'] == component]
    if len(matches) != 1:
        raise RuntimeError('unexpected component container layout: ' + component)
    return matches[0]


def with_changes(spec, component, image, strategy=None):
    changed = copy.deepcopy(spec)
    changed['template']['spec']['containers'][image_index(changed, component)]['image'] = image
    if strategy is not None:
        changed['strategy'] = copy.deepcopy(strategy)
    return changed


def guarded_patch(uid, old_spec, new_spec, component):
    """Never replace the full template, resources, mounts, env or replica count."""
    index = image_index(old_spec, component)
    old_image = old_spec['template']['spec']['containers'][index]['image']
    new_image = new_spec['template']['spec']['containers'][index]['image']
    if with_changes(old_spec, component, new_image, new_spec['strategy']) != new_spec:
        raise RuntimeError('patch would change an unowned field')
    patch = [{'op':'test', 'path':'/metadata/uid', 'value':uid},
             {'op':'test', 'path':'/spec', 'value':old_spec}]
    if new_image != old_image:
        patch.append({'op':'replace', 'path':'/spec/template/spec/containers/{}/image'.format(index), 'value':new_image})
    if old_spec['strategy'] != new_spec['strategy']:
        patch.append({'op':'replace', 'path':'/spec/strategy', 'value':new_spec['strategy']})
    return patch


def legal_states(before, component, candidate):
    spec = before['spec']
    states = [spec, with_changes(spec, component, candidate)]
    if component == 'gateway':
        states += [with_changes(spec, component, candidate, {'type':'Recreate'}),
                   with_changes(spec, component, spec['template']['spec']['containers'][image_index(spec, component)]['image'], {'type':'Recreate'})]
    return states


GATEWAY_HTTP = r'''
import hashlib, json, pathlib, urllib.request, sys
candidate = sys.argv[1] == 'candidate'
if candidate:
    root = pathlib.Path('/app/backend')
    entries = json.loads(pathlib.Path('/tmp/channel-pdf-r2/gateway-delta.json').read_bytes())
    assert len(entries) == 11
    for e in entries:
        p = (root / e['path']).resolve()
        assert root in p.parents
        assert hashlib.sha256(p.read_bytes()).hexdigest() == e['target_sha256'], e['path']
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args): return None
client = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
with client.open('http://gateway:8001/health', timeout=8) as r:
    assert r.status == 200
    body = r.read(8193)
    assert len(body) < 8193
    assert json.loads(body).get('status') in ('healthy','ok')
print('service_http=PASS component=gateway')
'''

FRONTEND_HTTP = r'''
const fs=require('node:fs'), path=require('node:path'), crypto=require('node:crypto'), assert=require('node:assert/strict');
const root='/app/frontend', candidate=process.argv[1]==='candidate';
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
function find(dir){for(const n of fs.readdirSync(dir).sort()) {const p=path.join(dir,n),s=fs.lstatSync(p); if(s.isFile()&&p.endsWith('.js')&&s.size>0&&s.size<8*1024*1024)return p; if(s.isDirectory()){const r=find(p);if(r)return r;}}}
(async()=>{
  if(candidate){
    const entries=JSON.parse(fs.readFileSync('/tmp/channel-pdf-r2/frontend-delta.json','utf8')); assert.equal(entries.length,8);
    for(const e of entries){const p=path.resolve(root,e.path);assert.ok(p.startsWith(root+'/'));assert.equal(hash(fs.readFileSync(p)),e.target_sha256);}
    const r=await fetch('http://frontend:3000/images/cnooc-haineng-mark.png',{redirect:'manual',signal:AbortSignal.timeout(8000)});
    assert.equal(r.status,200);assert.match(r.headers.get('content-type')||'',/^image\/png/i);
    assert.equal(hash(Buffer.from(await r.arrayBuffer())),'f6df5c2a4a1c4bfbabc2e896df16d231f5d3379b4a40d1b0b4e5ffae3531bfc9');
  }
  const asset=find(root+'/.next/static');assert.ok(asset);
  const url='http://frontend:3000/_next/static/'+path.relative(root+'/.next/static',asset).split(path.sep).map(encodeURIComponent).join('/');
  const r=await fetch(url,{redirect:'manual',signal:AbortSignal.timeout(8000)});assert.equal(r.status,200);
  assert.match(r.headers.get('content-type')||'',/javascript/i);assert.equal(hash(Buffer.from(await r.arrayBuffer())),hash(fs.readFileSync(asset)));
  console.log('service_http=PASS component=frontend');
})().catch(e=>{console.error(e.stack||e);process.exitCode=1;});
'''


class Release:
    def __init__(self, validator, evidence):
        self.v = validator
        self.evidence = evidence
        self.counter = 0
        self.session = uuid.uuid4().hex[:8]
        self.before = {}

    def save(self, name, obj):
        path = self.evidence / name
        data = json.dumps(obj, ensure_ascii=False, indent=2).encode('utf-8')
        path.write_bytes(data)
        os.chmod(path, 0o600)
        return path

    def ctl(self, *args, stdin=None, timeout=45):
        self.counter += 1
        number = self.counter
        try:
            result = subprocess.run(['kubectl','--request-timeout=30s','-n',NS] + list(args),
                input=stdin, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise RuntimeError('kubectl timeout operation={} evidence_id={}'.format(args[0], number)) from None
        if result.returncode:
            filename = 'command-{}-{}.error.json'.format(self.session,number)
            self.save(filename, {'operation':args[0], 'returncode':result.returncode, 'stderr':result.stderr[-20000:]})
            raise RuntimeError('kubectl failed operation={} see protected {}'.format(args[0], filename))
        return result.stdout

    def get(self, kind, name=None, selector=None):
        args = ['get',kind] + ([name] if name else [])
        if selector: args += ['-l',selector]
        return json.loads(self.ctl(*args, '-o','json'))

    def check_live_spec(self, component, expected):
        obj = self.get('deployment', component)
        if obj['metadata']['uid'] != self.before[component]['metadata']['uid'] or obj['spec'] != expected:
            raise RuntimeError('concurrent deployment change detected: ' + component)
        return obj

    def apply(self, component, old_spec, new_spec):
        if old_spec == new_spec: return
        self.check_live_spec(component, old_spec)
        patch = guarded_patch(self.before[component]['metadata']['uid'], old_spec, new_spec, component)
        # The full-spec compare can contain inline env. Never put it in argv or logs.
        path = self.save('patch-{}-{}.json'.format(component, uuid.uuid4().hex[:8]), patch)
        self.ctl('patch','deployment',component,'--type=json','--patch-file=' + str(path),'-o','name')
        self.check_live_spec(component, new_spec)

    def no_activity(self):
        pods = [p for p in self.get('pods', selector='app=postgres')['items']
                if not p['metadata'].get('deletionTimestamp') and p.get('status',{}).get('phase') == 'Running'
                and any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in p.get('status',{}).get('conditions',[]))]
        if len(pods) != 1: raise RuntimeError('expected exactly one Ready postgres Pod')
        sql = "BEGIN READ ONLY; SET LOCAL statement_timeout='10s'; SELECT 'active_deerflow_runs=' || count(*) FROM runs WHERE status IN ('pending','running'); ROLLBACK;\n"
        output = self.ctl('exec','-i',pods[0]['metadata']['name'],'-c','postgres','--','sh','-ec',
            'exec psql -X -q -t -A -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"', stdin=sql)
        rows = [x.strip() for x in output.splitlines() if x.strip()]
        if len(rows) != 1 or not re.fullmatch(r'active_deerflow_runs=\d+', rows[0]):
            raise RuntimeError('unexpected active-run query result')
        emit(rows[0])
        if rows[0] != 'active_deerflow_runs=0':
            raise RuntimeError('active work detected; no force bypass is provided')

    def candidate_evidence(self, directory):
        directory = directory.resolve(strict=True)
        if ROOT not in directory.parents or not directory.name.startswith('channel-pdf-r2-candidate-'):
            raise RuntimeError('unexpected candidate evidence directory')
        if time.time() - directory.stat().st_mtime > 21600:
            raise RuntimeError('candidate validation is older than 6h; rerun candidate validation first')
        recorded = json.loads((directory/'baseline.json').read_bytes())
        for c, obj in self.before.items():
            if recorded[c] != {'uid':obj['metadata']['uid'], 'spec_sha256':fingerprint(obj['spec'])}:
                raise RuntimeError('production changed since candidate validation: ' + c)
        for component, node in self.v.TARGETS:
            matches = list(directory.glob('cpdf-r2-{}-{}-*.status.json'.format(component,node)))
            if len(matches) != 1: raise RuntimeError('missing/ambiguous candidate receipt: ' + node)
            status_file = matches[0]
            logs = Path(str(status_file).replace('.status.json','.log')).read_text(encoding='utf-8')
            self.v.verify_success({'status':json.loads(status_file.read_bytes())}, component, logs)
        emit('candidate_receipts_and_live_baseline=PASS nodes=4')

    def preflight(self, directory):
        for c in ('gateway','frontend'):
            obj = self.get('deployment',c)
            spec = obj['spec']
            idx = image_index(spec,c)
            if spec.get('replicas') != 1 or spec.get('paused') or spec['template']['spec']['containers'][idx]['image'] != self.v.BASE[c]:
                raise RuntimeError('unexpected image/topology/paused deployment: ' + c)
            if spec.get('selector') != {'matchLabels':{'app':c}}:
                raise RuntimeError('unexpected deployment selector: ' + c)
            if spec['template']['spec']['containers'][idx].get('imagePullPolicy') != 'Never':
                raise RuntimeError('imagePullPolicy must remain Never: ' + c)
            self.before[c] = obj
        if self.before['gateway']['spec']['template']['spec'].get('nodeSelector',{}).get('kubernetes.io/hostname') != 'ecs-zdz-pro-0022':
            raise RuntimeError('Gateway scheduling changed')
        for hpa in self.get('hpa')['items']:
            target = hpa['spec']['scaleTargetRef']
            if target.get('kind') == 'Deployment' and target.get('name') in self.before:
                raise RuntimeError('HPA controls release target; stop for review')
        for c, port in (('gateway',8001), ('frontend',3000)):
            svc = self.get('service',c)
            if svc['spec'].get('selector') != {'app':c} or not any(p['port']==port for p in svc['spec']['ports']):
                raise RuntimeError('unexpected Service routing: ' + c)
        for _, node in self.v.TARGETS: self.v.check_node(node)
        self.candidate_evidence(directory)
        self.no_activity()
        for c in self.before: self.healthy(c, self.before[c]['spec'], candidate=False, timeout=60)
        sums = {}
        for c, obj in self.before.items():
            path = self.save(c + '.before.json',obj)
            sums[path.name] = sha(path.read_bytes())
        self.save('BACKUP.SHA256.json',sums)
        emit('release_preflight=PASS backups=PRIVATE_0600 database_query=READ_ONLY')

    def owned_pods(self, component):
        uid = self.before[component]['metadata']['uid']
        rs_uids = {r['metadata']['uid'] for r in self.get('replicasets',selector='app='+component)['items']
                   if any(o.get('uid') == uid and o.get('controller') for o in r['metadata'].get('ownerReferences',[]))}
        result = []
        for p in self.get('pods',selector='app='+component)['items']:
            if p.get('status',{}).get('phase') in ('Failed','Succeeded'): continue
            if not any(o.get('uid') in rs_uids and o.get('controller') for o in p['metadata'].get('ownerReferences',[])):
                raise RuntimeError('unowned Pod matches production Service: '+component)
            result.append(p)
        return result

    def ready_pod(self, component, spec, candidate):
        obj = self.check_live_spec(component,spec)
        st = obj.get('status',{})
        if st.get('observedGeneration',0) < obj['metadata']['generation'] or any(st.get(k,0) != 1 for k in ('replicas','updatedReplicas','readyReplicas','availableReplicas')):
            return None
        pods = self.owned_pods(component)
        if len(pods) != 1 or pods[0]['metadata'].get('deletionTimestamp'): return None
        pod = pods[0]
        if not any(c.get('type')=='Ready' and c.get('status')=='True' for c in pod.get('status',{}).get('conditions',[])): return None
        actual = [c for c in pod.get('status',{}).get('containerStatuses',[]) if c['name']==component]
        if len(actual)!=1 or not actual[0].get('ready'): return None
        match = re.search(r'sha256:([0-9a-f]{64})$', actual[0].get('imageID',''))
        valid = self.v.IDENTITIES[component] if candidate else BASE_IDS[component]
        if not match or match.group(1) not in valid: raise RuntimeError('runtime image identity mismatch: '+component)
        slices = self.get('endpointslices',selector='kubernetes.io/service-name='+component)['items']
        endpoints = [e for s in slices for e in s.get('endpoints',[]) if e.get('conditions',{}).get('ready') is True and not e.get('conditions',{}).get('terminating')]
        if not endpoints or any(e.get('targetRef',{}).get('uid') != pod['metadata']['uid'] for e in endpoints): return None
        self.v.check_node(pod['spec']['nodeName'])
        return pod

    def healthy(self, component, spec, candidate, timeout=900):
        deadline, progress = time.monotonic()+timeout, 0
        pod = None
        while time.monotonic() < deadline:
            pod = self.ready_pod(component,spec,candidate)
            if pod: break
            if time.monotonic()-progress >= 20:
                emit('rollout_wait component={} target={}'.format(component,'candidate' if candidate else 'base'))
                progress = time.monotonic()
            time.sleep(4)
        if pod is None: raise RuntimeError('readiness/endpoint deadline exceeded: '+component)
        mode = 'candidate' if candidate else 'base'
        command = (['/app/backend/.venv/bin/python','-B','-c',GATEWAY_HTTP,mode] if component=='gateway' else ['node','-e',FRONTEND_HTTP,mode])
        consecutive = 0
        for attempt in range(1,6):
            try:
                pod = self.ready_pod(component,spec,candidate)
                if pod is None: raise RuntimeError('readiness lost')
                out = self.ctl('exec',pod['metadata']['name'],'-c',component,'--',*command,timeout=35)
                if 'service_http=PASS component='+component not in out: raise RuntimeError('missing HTTP receipt')
                consecutive += 1
                if consecutive >= 2:
                    emit('release_service_ready=PASS component={} pod={} image={}'.format(component,pod['metadata']['name'],mode))
                    return
            except RuntimeError:
                consecutive = 0
                emit('service_http_retry component={} attempt={}/5'.format(component,attempt))
            time.sleep(3)
        raise RuntimeError('bounded Service HTTP validation failed: '+component)

    def diagnostics(self):
        for c in self.before:
            try:
                pods = self.get('pods',selector='app='+c)
                self.save(c+'.failure-pods.json',pods)
                self.save(c+'.failure-deployment.json',self.get('deployment',c))
                for p in pods['items']:
                    name = p['metadata']['name']
                    try:
                        logs = self.ctl('logs',name,'-c',c,'--tail=100','--limit-bytes=16000')
                        self.save(name+'.private-log.json',{'log':logs})
                    except Exception: pass
            except Exception: pass

    def rollback(self):
        emit('automatic_or_explicit_rollback=START')
        errors = []
        # Restore Gateway availability first; frontend old/new remain protocol-compatible.
        for c in ('gateway','frontend'):
            try:
                obj = self.get('deployment',c)
                old = self.before[c]
                if obj['metadata']['uid'] != old['metadata']['uid'] or obj['spec'] not in legal_states(old,c,self.v.CANDIDATE[c]):
                    raise RuntimeError('refusing rollback over unrelated changes: '+c)
                original = old['spec']
                current = obj['spec']
                base_image = self.v.BASE[c]
                cur_image = current['template']['spec']['containers'][image_index(current,c)]['image']
                target = with_changes(original,c,base_image,{'type':'Recreate'}) if c=='gateway' and cur_image!=base_image else original
                self.apply(c,current,target)
                self.healthy(c,target,candidate=False)
                self.apply(c,target,original)
                self.healthy(c,original,candidate=False,timeout=90)
                emit('rollback_component=PASS component='+c)
            except Exception as error:
                errors.append(str(error))
                emit('rollback_component=FAIL component={} detail={}'.format(c,error))
        self.save('rollback-result-'+self.session+'.json',{'success':not errors,'errors':errors})
        if errors: raise RuntimeError('rollback incomplete; retain evidence and request manual recovery')
        emit('channel_pdf_r2_rollback=PASS original_images_and_specs=RESTORED')

    def rollout(self):
        # Last read-only gate immediately before the first production mutation.
        for c,obj in self.before.items(): self.check_live_spec(c,obj['spec'])
        self.no_activity()
        started = False
        try:
            gw = with_changes(self.before['gateway']['spec'],'gateway',self.v.CANDIDATE['gateway'],{'type':'Recreate'})
            started = True  # Include uncertain patch responses in recovery.
            self.save('phase.json',{'phase':'gateway-patch-intent'})
            self.apply('gateway',self.before['gateway']['spec'],gw)
            self.healthy('gateway',gw,candidate=True)
            fe = with_changes(self.before['frontend']['spec'],'frontend',self.v.CANDIDATE['frontend'])
            self.save('phase.json',{'phase':'frontend-patch-intent'})
            self.apply('frontend',self.before['frontend']['spec'],fe)
            self.healthy('frontend',fe,candidate=True)
            final_gw = with_changes(self.before['gateway']['spec'],'gateway',self.v.CANDIDATE['gateway'])
            self.apply('gateway',gw,final_gw)
            self.healthy('gateway',final_gw,candidate=True,timeout=90)
            self.healthy('frontend',fe,candidate=True,timeout=90)
            self.save('phase.json',{'phase':'rollout-pass'})
            emit('channel_pdf_r2_rollout=PASS components=gateway,frontend')
            emit('original_strategy=RESTORED replicas_and_workers=UNCHANGED channel_config_and_secrets=UNCHANGED')
            emit('deployment_delta=MAIN_CONTAINER_IMAGE_ONLY direct_database_mutation=NONE browser_acceptance=REQUIRED')
        except (Exception, KeyboardInterrupt):
            if started:
                self.diagnostics()
                self.rollback()
            raise


def load_backups(directory):
    sums = json.loads((directory/'BACKUP.SHA256.json').read_bytes())
    if set(sums) != {'gateway.before.json','frontend.before.json'}: raise RuntimeError('backup manifest scope mismatch')
    result = {}
    for name, digest in sums.items():
        data = (directory/name).read_bytes()
        if sha(data) != digest: raise RuntimeError('backup checksum mismatch: '+name)
        result[name.split('.')[0]] = json.loads(data)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--rollout', action='store_true')
    group.add_argument('--rollback', type=Path, metavar='RELEASE_EVIDENCE_DIR')
    parser.add_argument('--candidate-evidence', type=Path)
    args = parser.parse_args()
    if socket.gethostname().split('.')[0] != 'datax-01' or os.geteuid() != 0: raise RuntimeError('root on datax-01 required')
    expected_ack = 'SHORT_OUTAGE' if args.rollout else 'ROLLBACK'
    if os.environ.get('CHANNEL_PDF_R2_ACK') != expected_ack: raise RuntimeError('explicit maintenance ACK required: '+expected_ack)
    os.environ['KUBECONFIG'] = '/etc/kubernetes/admin.conf'
    os.umask(0o077)
    for key in ('SSL_CERT_FILE','REQUESTS_CA_BUNDLE','CURL_CA_BUNDLE'): os.environ.pop(key,None)
    v = load_helper(Path('/home/cnooc/deerflow-channel-pdf-r2-validate-candidates.py'))
    import fcntl
    lock = open('/opt/deerflow-hotfix-evidence/.channel-pdf-r2-release.lock','a+')
    fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
    if args.rollout:
        if args.candidate_evidence is None: raise RuntimeError('--candidate-evidence is required')
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        directory = ROOT/('channel-pdf-r2-rollout-'+stamp+'-'+uuid.uuid4().hex[:8])
        directory.mkdir(mode=0o700)
        emit('rollback_state='+str(directory))
        release = Release(v,directory)
        release.preflight(args.candidate_evidence)
        release.rollout()
    else:
        directory = args.rollback.resolve(strict=True)
        if ROOT not in directory.parents or not directory.name.startswith('channel-pdf-r2-rollout-'): raise RuntimeError('unexpected rollback directory')
        release = Release(v,directory)
        release.before = load_backups(directory)
        release.rollback()
    emit('evidence_dir='+str(directory))


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        emit('channel_pdf_r2_operation=FAIL detail='+str(error))
        sys.exit(1)

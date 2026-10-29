"""Isolated Frontend image jobs and bounded loopback verification, Python 3.7+."""
import json
import re

MAX_FILES = 13
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024


class FrontendCheckRefused(RuntimeError):
    pass


def _require(ok, code):
    if not ok:
        raise FrontendCheckRefused(code)


def validate_pins(pins):
    _require(isinstance(pins, dict) and 0 < len(pins) <= MAX_FILES, 'FRONTEND_PIN_COUNT')
    total = 0
    for path, value in pins.items():
        _require(isinstance(path, str) and path.startswith('/app/frontend/')
                 and '\\' not in path and '..' not in path.split('/') and '//' not in path
                 and isinstance(value, dict) and set(value) == {'bytes', 'sha256'}, 'FRONTEND_PIN_SHAPE')
        _require(type(value['bytes']) is int and 0 <= value['bytes'] <= MAX_FILE_BYTES
                 and isinstance(value['sha256'], str)
                 and re.fullmatch('[0-9a-f]{64}', value['sha256']) is not None, 'FRONTEND_PIN_BOUND')
        total += value['bytes']
    _require(total <= MAX_TOTAL_BYTES, 'FRONTEND_TOTAL_BOUND')
    return pins


def _file_verifier_code():
    return r"""const fs=require('fs'),crypto=require('crypto'),path=require('path');
const hash=b=>crypto.createHash('sha256').update(b).digest('hex');
function verifyPins(raw) {
 if(typeof raw!=='string'||raw.length>65536)throw Error('pins');
 const pins=JSON.parse(raw);
 if(!pins||Array.isArray(pins)||typeof pins!=='object')throw Error('pins');
 const entries=Object.entries(pins);let total=0;
 if(entries.length<1||entries.length>13)throw Error('pins');
 for(const [p,v] of entries) {
  if(!p.startsWith('/app/frontend/')||p.includes('\\')||p.split('/').includes('..')||p.includes('//')||
     !v||typeof v!=='object'||Object.keys(v).sort().join(',')!=='bytes,sha256'||
     !Number.isSafeInteger(v.bytes)||v.bytes<0||v.bytes>2097152||
     typeof v.sha256!=='string'||!/^[0-9a-f]{64}$/.test(v.sha256))throw Error('pins');
  total+=v.bytes;if(total>8388608)throw Error('pins');
  if(fs.realpathSync(p)!==path.resolve(p))throw Error('source');
  const fd=fs.openSync(p,fs.constants.O_RDONLY|fs.constants.O_NOFOLLOW);
  try {
   const s=fs.fstatSync(fd);if(!s.isFile()||s.size!==v.bytes)throw Error('source');
   const b=fs.readFileSync(fd),t=fs.fstatSync(fd);
   if(s.dev!==t.dev||s.ino!==t.ino||s.size!==t.size||s.mtimeMs!==t.mtimeMs||s.ctimeMs!==t.ctimeMs||hash(b)!==v.sha256)throw Error('source');
  }finally{fs.closeSync(fd)}
 }
 return pins;
}
"""


def cache_probe_code():
    """Node argv[1] is the JSON pin map. Output contains hashes neither keys nor file contents."""
    return _file_verifier_code() + r"""
try {
 const pins=verifyPins(process.argv[1]);
 console.log(JSON.stringify({status:'LOCAL_PRELOADED_IMAGE_EXECUTED',sources_verified:true,
  files_verified:Object.keys(pins).length,provider_requests:0}));
}catch(e){console.log(JSON.stringify({status:'INCOMPLETE',error_code:'candidate_source_changed'}));process.exit(1)}
"""


def runtime_code():
    """Node argv[1] pins; only GETs localhost:3000, no cookies, provider or business POSTs."""
    return _file_verifier_code() + r"""
const http=require('http');let pins;
try {pins=verifyPins(process.argv[1])}
catch(e){console.log(JSON.stringify({status:'INCOMPLETE',error_code:'candidate_source_changed'}));process.exit(1)}
function get(url,expected,strict200){return new Promise((resolve,reject)=>{
 const q=http.get({host:'127.0.0.1',port:3000,path:url,timeout:8000,agent:false,
  headers:{'Accept-Encoding':'identity'}},r=>{
  let n=0;const h=crypto.createHash('sha256');
  r.on('data',b=>{n+=b.length;h.update(b);if(n>(expected?expected.bytes:1048576))q.destroy(Error('limit'))});
  r.on('error',reject);r.on('end',()=>{
   const statusOK=strict200?r.statusCode===200:r.statusCode>=200&&r.statusCode<400;
   const ok=expected?r.statusCode===200&&n===expected.bytes&&h.digest('hex')===expected.sha256:statusOK&&n>0;
   if(!ok)reject(Error('http'));else resolve({http_status:r.statusCode,bytes:n});
  });
 });
 q.on('timeout',()=>q.destroy(Error('timeout')));q.on('error',reject);
})}
const assets=Object.entries(pins).filter(([p])=>p.startsWith('/app/frontend/.next/static/chunks/')&&/\.(js|css)$/.test(p));
if(!assets.some(([p])=>p.endsWith('.js'))||!assets.some(([p])=>p.endsWith('.css'))){
 console.log(JSON.stringify({status:'INCOMPLETE',error_code:'static_asset_pins_missing'}));process.exit(1);
}
Promise.all([get('/',null,false),get('/competition',null,true),
 ...assets.map(([p,v])=>get(p.replace('/app/frontend/.next/static/','/_next/static/'),v,true))]).then(rows=>{
 console.log(JSON.stringify({status:'PASS',http_status:rows[0].http_status,
  competition_http_status:rows[1].http_status,sources_verified:true,
  files_verified:Object.keys(pins).length,static_assets_verified:assets.length,
  provider_requests:0,authenticated_requests:0}));process.exit(0);
}).catch(()=>{console.log(JSON.stringify({status:'INCOMPLETE',error_code:'local_http_failed'}));process.exit(1)});
setTimeout(()=>{console.log(JSON.stringify({status:'INCOMPLETE',error_code:'local_http_deadline'}));process.exit(1)},15000).unref();
"""


def job_spec(name, node, image, pins):
    validate_pins(pins)
    _require(isinstance(name, str) and re.fullmatch('[a-z0-9][a-z0-9-]{0,61}[a-z0-9]', name) is not None, 'FRONTEND_JOB_NAME')
    _require(isinstance(node, str) and re.fullmatch('[a-z0-9][a-z0-9.-]{0,251}[a-z0-9]', node) is not None, 'FRONTEND_NODE_NAME')
    _require(isinstance(image, str) and re.fullmatch('deerflow[.]local/deer-flow-frontend@sha256:[0-9a-f]{64}', image) is not None, 'FRONTEND_IMAGE_NOT_PINNED')
    return {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {'name': name, 'namespace': 'deer-flow'},
            'spec': {'backoffLimit': 0, 'activeDeadlineSeconds': 180, 'ttlSecondsAfterFinished': 3600,
                     'template': {'metadata': {'labels': {'portal-combined-frontend-check': name}}, 'spec': {
                         'nodeName': node, 'restartPolicy': 'Never', 'automountServiceAccountToken': False,
                         'enableServiceLinks': False, 'terminationGracePeriodSeconds': 5,
                         'containers': [{'name': 'check', 'image': image, 'imagePullPolicy': 'Never',
                             'command': ['node'], 'args': ['-e', cache_probe_code(), json.dumps(pins, sort_keys=True)],
                             'resources': {'requests': {'cpu': '10m', 'memory': '32Mi'},
                                           'limits': {'cpu': '500m', 'memory': '128Mi'}},
                             'securityContext': {'runAsUser': 1000, 'runAsGroup': 1000, 'runAsNonRoot': True,
                                 'readOnlyRootFilesystem': True, 'allowPrivilegeEscalation': False,
                                 'capabilities': {'drop': ['ALL']}, 'seccompProfile': {'type': 'RuntimeDefault'}}}]}}}}


def _image_digest(image_id):
    value = (image_id or '').rsplit('@', 1)[-1]
    for prefix in ('containerd://', 'docker-pullable://', 'docker://'):
        if value.startswith(prefix): value = value[len(prefix):]
    return value


def inspect_check_pod(pod, job, spec, manifest, config):
    """Validate admitted Pod scope and return safe progress. completion_accepted gates success."""
    _require(job.get('kind') == 'Job' and job.get('metadata', {}).get('name') == spec['metadata']['name']
             and job['metadata'].get('namespace') == 'deer-flow' and job['metadata'].get('uid'), 'FRONTEND_JOB_IDENTITY')
    _require(pod.get('metadata', {}).get('namespace') == 'deer-flow'
             and not pod['metadata'].get('deletionTimestamp')
             and any(o.get('uid') == job['metadata']['uid'] and o.get('controller') is True and o.get('kind') == 'Job'
                     for o in pod['metadata'].get('ownerReferences', [])), 'FRONTEND_CHECK_OWNER')
    actual = pod['spec']; expected = spec['spec']['template']['spec']
    _require(actual.get('nodeName') == expected['nodeName'] and actual.get('automountServiceAccountToken') is False
             and actual.get('enableServiceLinks') is False and actual.get('restartPolicy') == 'Never'
             and not actual.get('hostNetwork') and not actual.get('hostPID') and not actual.get('hostIPC')
             and not actual.get('volumes') and not actual.get('initContainers') and not actual.get('ephemeralContainers')
             and len(actual.get('containers', [])) == 1, 'FRONTEND_CHECK_SCOPE')
    c = actual['containers'][0]; wanted = expected['containers'][0]
    _require(all(c.get(k) == wanted[k] for k in ('name', 'image', 'imagePullPolicy', 'command', 'args', 'resources', 'securityContext'))
             and not c.get('env') and not c.get('envFrom') and not c.get('volumeMounts') and not c.get('ports')
             and not c.get('lifecycle') and not c.get('startupProbe') and not c.get('livenessProbe'), 'FRONTEND_CHECK_CONTAINER_CHANGED')
    statuses = pod.get('status', {}).get('containerStatuses', [])
    _require(len(statuses) <= 1 and all(c.get('name') == 'check' for c in statuses), 'FRONTEND_CHECK_STATUS_SHAPE')
    if statuses and statuses[0].get('imageID'):
        _require(_image_digest(statuses[0]['imageID']) in (manifest, config), 'FRONTEND_CHECK_IMAGE_CHANGED')
    rows = [{'imageID': c.get('imageID'), 'restart_count': c.get('restartCount'),
             'waiting_reason': c.get('state', {}).get('waiting', {}).get('reason'),
             'exit_code': c.get('state', {}).get('terminated', {}).get('exitCode')} for c in statuses]
    complete = (pod.get('status', {}).get('phase') == 'Succeeded' and len(rows) == 1
                and rows[0]['exit_code'] == 0 and rows[0]['restart_count'] == 0
                and _image_digest(rows[0]['imageID']) in (manifest, config))
    return dict(pod=pod['metadata']['name'], uid=pod['metadata']['uid'], node=actual['nodeName'],
                phase=pod.get('status', {}).get('phase'), containers=rows, completion_accepted=complete)

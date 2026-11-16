"""Field-bound reader-route and Work-notification repair; no Skill changes."""
import base64
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import types
import uuid

BINDING={}
ASSETS={}
CONTROL=Path('/var/lib/portal-energy-reader-work-repair-20261115-r2')
PRIOR_CONTROL=Path('/var/lib/portal-energy-reader-work-repair-20261115-r1')
POD_COLUMNS='custom-columns=NAME:.metadata.name,UID:.metadata.uid,PHASE:.status.phase,DELETING:.metadata.deletionTimestamp,READY:.status.conditions[?(@.type=="Ready")].status,OWNER_KIND:.metadata.ownerReferences[?(@.controller==true)].kind,OWNER_NAME:.metadata.ownerReferences[?(@.controller==true)].name,OWNER_UID:.metadata.ownerReferences[?(@.controller==true)].uid,CONFIGMAPS:.spec.volumes[*].configMap.name'
KUBE=['kubectl','--kubeconfig=/etc/kubernetes/admin.conf','--request-timeout=20s','-n','deer-flow']
LINK_CM='gateway-energy-links-b6fb1ab44d10'
ROUTE='/reports/energy-daily/d934a54e-b894-4970-a32e-9b983c55d0df/2026-11-15.html'
LABEL='portal.energy-reader-work-repair/operation'
SYNTAX='set -eu; reader_cfg=$(mktemp /tmp/energy-serving-reader.XXXXXX); case "$reader_cfg" in /tmp/energy-serving-reader.*) ;; *) exit 90 ;; esac; trap \'rm -f -- "$reader_cfg"\' 0 1 2 3 15; chmod 600 "$reader_cfg"; cat > "$reader_cfg"; nginx -t -c "$reader_cfg"'

class Refused(ValueError):pass
def need(ok,code):
    if not ok:raise Refused(code)
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')
def sha(raw):return hashlib.sha256(raw).hexdigest()
def identify(row):return {'uid':row['metadata']['uid'],'generation':row['metadata']['generation'],'spec_sha256':sha(canonical(row['spec']))}
def name(value):return isinstance(value,str) and re.fullmatch('[a-z0-9][a-z0-9.-]{0,252}',value)
def converged(row):
    status=row.get('status',{});desired=row['spec'].get('replicas',1)
    return status.get('observedGeneration',0)>=row['metadata']['generation'] and all(status.get(k,0)==desired for k in ('replicas','updatedReplicas','readyReplicas','availableReplicas')) and status.get('terminatingReplicas',0)==0

def private_write(path,value):
    raw=value if isinstance(value,bytes) else canonical(value)
    fd=os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb',closefd=False) as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    finally:os.close(fd)

def private_read(path):
    for part in (path,)+tuple(path.parents):need(not part.is_symlink(),'CONTROL_PATH_LINK_REFUSED')
    before=path.stat();need(stat.S_ISREG(before.st_mode) and before.st_uid==0 and before.st_nlink==1 and before.st_size<=4194304 and not before.st_mode&0o077,'PRIVATE_CONTROL_FILE_REQUIRED')
    raw=path.read_bytes();after=path.stat()
    need(len(raw)==before.st_size and (before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns),'CONTROL_FILE_CHANGED')
    return json.loads(raw)

def load_assets():
    need(set(ASSETS)=={'reader','startup','gateway_child','code'},'SEALED_ASSETS_REQUIRED')
    result={}
    for key,value in ASSETS.items():
        raw=base64.b64decode(value['base64'],validate=True)
        need(len(raw)==value['bytes'] and sha(raw)==value['sha256'],'SEALED_ASSET_CHANGED')
        result[key]=raw
    return result

class Repair:
    def __init__(self,assets):
        self.assets=assets;self.operation=uuid.uuid4().hex;self.claimed=False;self.started=time.monotonic();self.commands=0;self.allowed_maps={LINK_CM,BINDING['nginx']['configmaps'][0]['name']}
        self.reader=self.module('reader');self.startup=self.module('startup');self.code=json.loads(assets['code'])
        self.out={'schema':'energy-reader-work-repair.v1','status':'INCOMPLETE','operation':self.operation,'once_claimed':False,
          'nginx_patch_requests':0,'gateway_patch_requests':0,'configmap_create_requests':0,'patch_may_have_applied':False,
          'skill_modified':False,'provider_requests':0,'database_access_requested_by_tool':False,'secret_api_reads':0,
          'credentials_exported':False,'work_messages_sent':0,'schedules_modified':False,'existing_runs_replayed':False,
          'automatic_retry':False,'automatic_rollback':False,'business_acceptance_pending':True,
          'normal_application_startup_database_and_redis_access_may_occur':True,
          'existing_background_schedules_may_execute_normally':True,'active_tasks_may_be_interrupted':True,
          'existing_rollout_strategies_preserved':True,'direct_pod_create_requests':0}

    def module(self,key):
        module=types.ModuleType('_energy_reader_repair_'+key);sys.modules[module.__name__]=module
        exec(compile(self.assets[key],'<sealed-'+key+'>','exec'),module.__dict__);return module

    def command(self,args,body=None,plain=False,timeout=40):
        need(args[0] in ('get','exec','create','patch'),'FIXED_COMMAND_KIND_REQUIRED')
        if args[0]=='get':need(len(args)>1 and args[1] in ('deployment','configmap','service','endpointslices','pod','pods','replicaset'),'SECRET_OR_OTHER_RESOURCE_READ_REFUSED')
        need(self.commands<1600 and time.monotonic()-self.started<14400,'OPERATION_BOUND')
        self.commands+=1
        stdout_limit=524288 if args[:2]==['get','pods'] else 4194304
        self.out['last_command_kind']=('GET_'+args[1].upper()+('_PROJECTED' if args[1]=='pods' else '')) if args[0]=='get' else args[0].upper()
        self.out['last_command_stdout_limit']=stdout_limit
        prefix=list(KUBE);prefix[2]='--request-timeout=%ds'%max(20,min(90,timeout-5))
        with tempfile.TemporaryFile() as incoming,tempfile.TemporaryFile() as stdout,tempfile.TemporaryFile() as stderr:
            if body is not None:need(isinstance(body,bytes) and len(body)<=4194304,'COMMAND_INPUT_BOUND');incoming.write(body);incoming.seek(0)
            process=subprocess.Popen(prefix+args,stdin=incoming if body is not None else subprocess.DEVNULL,stdout=stdout,stderr=stderr,start_new_session=True,
                                     env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8'})
            deadline=time.monotonic()+timeout
            try:
                while process.poll() is None:
                    self.output_bound(stdout,stderr,stdout_limit)
                    need(time.monotonic()<deadline,'COMMAND_DEADLINE');time.sleep(.05)
                need(process.returncode==0,'FIXED_COMMAND_FAILED_REDACTED')
                self.output_bound(stdout,stderr,stdout_limit)
                stdout.seek(0);raw=stdout.read(stdout_limit+1)
                return raw if plain else json.loads(raw)
            finally:
                if process.poll() is None:os.killpg(process.pid,signal.SIGKILL);process.wait(timeout=5)

    def output_bound(self,stdout,stderr,limit):
        outbytes=os.fstat(stdout.fileno()).st_size;errbytes=os.fstat(stderr.fileno()).st_size
        if outbytes>limit or errbytes>65536:
            self.out['command_output_failure']={'stdout_bytes':outbytes,'stderr_bytes':errbytes,'stdout_limit':limit,'stderr_limit':65536}
            raise Refused('COMMAND_OUTPUT_BOUND')

    def get(self,kind,value):
        need(name(value),'RESOURCE_NAME_INVALID')
        if kind=='configmap':need(value in self.allowed_maps,'UNBOUND_CONFIGMAP_REFUSED')
        return self.command(['get',kind,value,'-o','json'])

    def pods(self,dep):
        selector=dep['spec']['selector'];need(selector.get('matchLabels') and not selector.get('matchExpressions'),'SIMPLE_DEPLOYMENT_SELECTOR_REQUIRED')
        labels=','.join(k+'='+v for k,v in sorted(selector['matchLabels'].items()))
        need(re.fullmatch('[A-Za-z0-9_./=,-]{1,512}',labels),'SELECTOR_INVALID')
        # Project nine bounded identity/readiness fields instead of exporting
        # every Pod's env, annotations, managedFields and complete container spec.
        raw=self.command(['get','pods','-l',labels,'--no-headers','-o',POD_COLUMNS],plain=True)
        lines=raw.decode('ascii').splitlines();need(len(lines)<=256,'POD_LIST_BOUND')
        result=[]
        for line in lines:
            parts=line.split();need(len(parts)==9,'POD_PROJECTION_COLUMNS_REQUIRED')
            podname,uid,phase,deleting,ready,kind,owner,owneruid,maps=parts
            need(name(podname) and re.fullmatch('[A-Za-z0-9_-]{1,128}',uid),'POD_PROJECTION_IDENTITY_INVALID')
            if phase!='Running' or deleting!='<none>' or ready!='True':continue
            need(kind=='ReplicaSet' and name(owner) and re.fullmatch('[A-Za-z0-9_-]{1,128}',owneruid),'POD_PROJECTION_OWNER_REQUIRED')
            names=[] if maps=='<none>' else maps.split(',')
            need(len(names)<=64 and all(name(value) for value in names),'POD_CONFIGMAP_REFERENCES_INVALID')
            row={'metadata':{'name':podname,'uid':uid,'ownerReferences':[{'kind':kind,'name':owner,'uid':owneruid,'controller':True}]},
                 'status':{'phase':phase,'conditions':[{'type':'Ready','status':ready}]},
                 'spec':{'volumes':[{'configMap':{'name':value}} for value in names]}}
            if dep['metadata']['name']==BINDING['nginx']['deployment']:
                # Only the serving Nginx needs its one complete Pod object to
                # validate the existing literal startup and exact image.
                full=self.get('pod',podname)
                need(full['metadata']['uid']==uid and not full['metadata'].get('deletionTimestamp')
                     and full.get('status',{}).get('phase')=='Running'
                     and any(c.get('type')=='Ready' and c.get('status')=='True' for c in full.get('status',{}).get('conditions',[])),
                     'SERVING_POD_CHANGED_AFTER_PROJECTION')
                row=full
            result.append(row)
        need(len({row['metadata']['uid'] for row in result})==len(result),'DUPLICATE_PROJECTED_POD_IDENTITY')
        return result

    def owner(self,pod,dep):
        refs=[o for o in pod['metadata'].get('ownerReferences',[]) if o.get('controller') is True]
        need(len(refs)==1 and refs[0].get('kind')=='ReplicaSet','POD_CONTROLLER_REQUIRED')
        rs=self.get('replicaset',refs[0]['name']);need(rs['metadata']['uid']==refs[0]['uid'],'POD_REPLICASET_CHANGED')
        owners=[o for o in rs['metadata'].get('ownerReferences',[]) if o.get('controller') is True]
        need(len(owners)==1 and owners[0].get('kind')=='Deployment' and owners[0].get('name')==dep['metadata']['name'] and owners[0].get('uid')==dep['metadata']['uid'],'POD_DEPLOYMENT_OWNERSHIP_CHANGED')

    def endpoints(self,service):
        rows=self.command(['get','endpointslices','-l','kubernetes.io/service-name='+service,'-o','json'])['items'];need(len(rows)<=64,'ENDPOINTSLICE_BOUND')
        result=set()
        for row in rows:
            need(row['metadata'].get('labels',{}).get('kubernetes.io/service-name')==service,'ENDPOINT_SERVICE_CHANGED')
            for endpoint in row.get('endpoints',[]):
                if endpoint.get('conditions',{}).get('ready') is not True or endpoint.get('conditions',{}).get('terminating'):continue
                ref=endpoint.get('targetRef',{});need(ref.get('kind')=='Pod' and ref.get('namespace','deer-flow')=='deer-flow','POD_SERVICE_BACKEND_REQUIRED')
                result.add(ref['uid'])
        return result

    def services(self,pod_ids=None):
        result={}
        for bound in BINDING['services']:
            current=self.get('service',bound['name'])
            need(current['metadata']['uid']==bound['uid'] and sha(canonical(current['spec']))==bound['spec_sha256'],'PRODUCTION_SERVICE_CHANGED')
            need(current['spec'].get('selector')=={'app':'nginx-security-heai-20261012-r1'},'WRONG_SERVING_NGINX_SELECTOR')
            endpoints=self.endpoints(bound['name']);need(endpoints,'NO_READY_SERVING_ENDPOINT')
            if pod_ids is not None:need(endpoints==set(pod_ids),'SERVICE_BACKENDS_NOT_CURRENT_DEPLOYMENT')
            result[bound['name']]=sorted(endpoints)
        return result

    def gateway_child(self,pod,mode):
        return self.command(['exec',pod['metadata']['name'],'-c','gateway','--','/app/backend/.venv/bin/python','-I','-B','-c',self.assets['gateway_child'].decode(),mode],timeout=75)

    def replay(self,pod,container,source):
        expressions=self.startup.parse_startup(container)
        need(len(expressions)==7,'SEVEN_EXISTING_STARTUP_SUBSTITUTIONS_REQUIRED')
        result=source
        for expression in expressions:
            self.startup.validate_substitution(expression)
            result=self.command(['exec','-i',pod['metadata']['name'],'-c','nginx','--','sed','-e',expression],result,True)
            need(0<len(result)<=262144,'STARTUP_REPLAY_BOUND')
        return result

    def map_role(self,dep,old,changed_data,role):
        newname=('nginx-energy-reader-' if role=='nginx' else 'gateway-reader-notify-')+self.operation[:12]
        self.allowed_maps.add(newname)
        spec=copy.deepcopy(dep['spec']);volumes=spec['template']['spec']['volumes']
        indices=[i for i,v in enumerate(volumes) if v.get('configMap',{}).get('name')==old['metadata']['name']]
        need(len(indices)==1,'ONE_EXISTING_CONFIGMAP_REFERENCE_REQUIRED');index=indices[0]
        volumes[index]['configMap']['name']=newname
        cm={'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':newname,'namespace':'deer-flow','labels':{LABEL:self.operation}},'immutable':True,'data':changed_data}
        if old.get('binaryData'):cm['binaryData']=old['binaryData']
        return {'role':role,'deployment_name':dep['metadata']['name'],'before':identify(dep),'before_spec':copy.deepcopy(dep['spec']),'candidate_spec':spec,
                'old_configmap':copy.deepcopy(old),'new_configmap':cm,'volume_index':index,'candidate_data_sha256':sha(canonical(changed_data))}

    def preflight(self):
        self.out['last_step']='QUALIFY_ACTUAL_SERVING_DEPLOYMENT_AND_ENDPOINTS'
        bound=BINDING['nginx'];dep=self.get('deployment',bound['deployment'])
        need(identify(dep)=={'uid':bound['deployment_uid'],'generation':bound['generation'],'spec_sha256':bound['spec_sha256']},'SERVING_NGINX_BASELINE_CHANGED')
        need(converged(dep),'SERVING_NGINX_NOT_CONVERGED')
        pods=self.pods(dep);need(len(pods)==dep['spec'].get('replicas',1)==1,'ONE_SERVING_NGINX_POD_REQUIRED');pod=pods[0]
        self.owner(pod,dep);self.services([pod['metadata']['uid']])
        containers=[c for c in pod['spec']['containers'] if c['name']=='nginx'];need(len(containers)==1 and containers[0]['image']==bound['image'],'SERVING_NGINX_IMAGE_CHANGED')
        self.out['last_step']='QUALIFY_CURRENT_API_TEMPLATE_AND_SEVEN_STARTUP_SUBSTITUTIONS'
        info=bound['configmaps'][0];cm=self.get('configmap',info['name'])
        need(cm['metadata']['uid']==info['uid'] and sha(canonical(cm.get('data',{})))==info['data_sha256'],'SERVING_TEMPLATE_CONFIGMAP_CHANGED')
        raw=cm['data']['nginx.conf'].encode();need(sha(raw)==info['files'][0]['sha256'],'SERVING_TEMPLATE_SOURCE_CHANGED')
        live=self.command(['exec',pod['metadata']['name'],'-c','nginx','--','cat','/tmp/nginx.conf'],plain=True)
        need(sha(live)==bound['runtime_config']['sha256'] and self.replay(pod,containers[0],raw)==live,'CURRENT_STARTUP_REPLAY_CHANGED')
        runtime_facts=self.reader.inspect(live.decode())
        candidate,facts=self.reader.add_reader(raw.decode(),runtime_facts['proxy_pass'],runtime_facts['forwarded_proto'])
        expected_live,_=self.reader.add_reader(live.decode(),runtime_facts['proxy_pass'],runtime_facts['forwarded_proto'])
        replayed=self.replay(pod,containers[0],candidate.encode())
        need(replayed==expected_live.encode(),'CANDIDATE_STARTUP_REPLAY_CHANGED_EXISTING_CONFIGURATION')
        self.command(['exec','-i',pod['metadata']['name'],'-c','nginx','--','sh','-c',SYNTAX],replayed,True)
        data=dict(cm['data']);data['nginx.conf']=candidate
        nginx=self.map_role(dep,cm,data,'nginx');nginx.update(candidate_runtime_sha256=sha(replayed),startup_replay_verified=True,candidate_nginx_syntax_verified=True)
        self.out['last_step']='QUALIFY_GATEWAY_SOURCES_CONFIG_AND_LINK_ONLY_NOTIFICATION'
        gateway=self.get('deployment','gateway');need(identify(gateway)==BINDING['gateway'],'GATEWAY_BASELINE_CHANGED');need(converged(gateway),'GATEWAY_NOT_CONVERGED')
        gwpods=self.pods(gateway);need(len(gwpods)==gateway['spec'].get('replicas',1),'GATEWAY_READY_SET_REQUIRED')
        sample=sorted(gwpods,key=lambda p:p['metadata']['name'])[0];self.owner(sample,gateway)
        result=self.gateway_child(sample,'qualify');self.out['qualification']=result
        need(result.get('status')=='GATEWAY_READER_NOTIFICATION_SOURCES_QUALIFIED','GATEWAY_CANDIDATE_NOT_QUALIFIED')
        old=self.get('configmap',LINK_CM);need(old.get('immutable') is True,'EXISTING_GATEWAY_CODE_MAP_IMMUTABLE_REQUIRED')
        mounts=[m for m in BINDING['gateway_mount_metadata'] if m.get('source_name')==LINK_CM]
        need(len(mounts)==6,'EXISTING_SIX_MODULE_OVERLAY_REQUIRED')
        data=dict(old['data'])
        for mount in mounts:
            key=mount['subpath'];expected=BINDING['gateway_parent_sources'][mount['mount_path']]
            need(key in data and len(data[key].encode())==expected['bytes'] and sha(data[key].encode())==expected['sha256'],'EXISTING_GATEWAY_OVERLAY_SOURCE_CHANGED')
        need(set(self.code)=={'work_notification_delivery.py','work_notifications.py'},'TWO_NOTIFICATION_MODULES_REQUIRED')
        for key,value in self.code.items():data[key]=value
        gw=self.map_role(gateway,old,data,'gateway')
        for name,role in (('nginx',nginx),('gateway',gw)):
            current=self.get('deployment',role['deployment_name']);need(identify(current)==role['before'],'BASELINE_CHANGED_DURING_QUALIFICATION')
        service=self.get('service','gateway')
        need(service['spec'].get('selector') and all(gateway['spec']['template']['metadata'].get('labels',{}).get(k)==v for k,v in service['spec']['selector'].items()),'GATEWAY_SERVICE_SELECTOR_CHANGED')
        need(self.endpoints('gateway')=={p['metadata']['uid'] for p in gwpods},'GATEWAY_SERVICE_BACKEND_CHANGED')
        return {'schema':'energy-reader-work-repair-plan.v1','operation':self.operation,'diagnostic_sha256':BINDING['diagnostic_sha256'],'roles':{'nginx':nginx,'gateway':gw},
                'gateway_service':{'uid':service['metadata']['uid'],'spec_sha256':sha(canonical(service['spec']))}}

    def create_maps(self,plan):
        made={}
        for key,role in plan['roles'].items():
            self.out['configmap_create_requests']+=1
            result=self.command(['create','-f','-','-o','json'],canonical(role['new_configmap']))
            need(result.get('immutable') is True and result.get('data')==role['new_configmap']['data'] and result.get('binaryData')==role['new_configmap'].get('binaryData'),'NEW_CONFIGMAP_CONTENT_CHANGED')
            made[key]={'name':result['metadata']['name'],'uid':result['metadata']['uid']}
        private_write(CONTROL/'CREATED-MAPS.json',made)
        return made

    def patch(self,role,made):
        self.out['last_step']='PATCH_ONLY_'+role['role'].upper()+'_CONFIGMAP_REFERENCE'
        dep=self.get('deployment',role['deployment_name']);need(identify(dep)==role['before'] and dep['spec']==role['before_spec'],'ROLE_BASELINE_CHANGED_BEFORE_PATCH')
        previous=self.get('configmap',role['old_configmap']['metadata']['name'])
        need(previous['metadata']['uid']==role['old_configmap']['metadata']['uid'] and previous.get('data')==role['old_configmap'].get('data') and previous.get('binaryData')==role['old_configmap'].get('binaryData'),'PARENT_CONFIGMAP_CHANGED_BEFORE_PATCH')
        current=self.get('configmap',made['name'])
        need(current['metadata']['uid']==made['uid'] and current.get('immutable') is True and current.get('data')==role['new_configmap']['data'] and current.get('binaryData')==role['new_configmap'].get('binaryData'),'CREATED_MAP_CHANGED_BEFORE_PATCH')
        if role['role']=='nginx':self.services()
        patch=[{'op':'test','path':'/metadata/uid','value':dep['metadata']['uid']},{'op':'test','path':'/spec','value':dep['spec']},
               {'op':'replace','path':'/spec/template/spec/volumes/'+str(role['volume_index'])+'/configMap/name','value':made['name']}]
        private_write(CONTROL/('PATCH-INTENT-'+role['role']+'.json'),{'operation':self.operation,'patch_sha256':sha(canonical(patch)),'before':role['before']})
        self.out[role['role']+'_patch_requests']+=1;self.out['patch_may_have_applied']=True
        result=self.command(['patch','deployment',role['deployment_name'],'--type=json','--patch-file=/dev/stdin','-o','json'],canonical(patch))
        need(result['metadata']['uid']==dep['metadata']['uid'] and result['spec']==role['candidate_spec'],'PATCHED_ROLE_SPEC_CHANGED')
        private_write(CONTROL/('PATCH-ACK-'+role['role']+'.json'),identify(result))

    def route_probe(self,pod,path,host='cnoocclaw.cnooc.com.cn'):
        need(host in ('cnoocclaw.cnooc.com.cn','heai.cnooc.com.cn'),'APPROVED_HOST_REQUIRED')
        need(path in (ROUTE,'/health','/platform/skillhub/','/platform/skillhub/api/web/labels'),'FIXED_HTTP_PATH_REQUIRED')
        raw=self.command(['exec',pod['metadata']['name'],'-c','nginx','--','curl','--silent','--show-error','--max-time','8','--dump-header','-','--output','/dev/null',
              '--header','Host: '+host,'--header','X-Forwarded-Proto: https','http://127.0.0.1:2026'+path],plain=True)
        need(len(raw)<=32768,'HTTP_RESPONSE_HEADER_BOUND')
        lines=raw.decode('iso-8859-1').splitlines();match=re.match(r'HTTP/[0-9.]+ ([0-9]{3})',lines[0] if lines else '')
        need(match is not None,'HTTP_STATUS_REQUIRED')
        location=[s.split(':',1)[1].strip() for s in lines[1:] if s.lower().startswith('location:')]
        result={'path':path,'host':host,'http_status':int(match.group(1)),'credentials_used':False,'response_body_read':False}
        if path==ROUTE:
            need(result['http_status']==302 and location==['/api/v1/auth/iam/login?next='+ROUTE],'READER_LOGIN_REDIRECT_NOT_ACCEPTED')
            result['exact_login_return_verified']=True
        else:
            expected={'/health':200,'/platform/skillhub/':302,'/platform/skillhub/api/web/labels':401}[path]
            need(result['http_status']==expected,'EXISTING_AUTH_OR_HEALTH_CHECK_CHANGED')
            if path=='/platform/skillhub/':need(location==['/login'],'SKILLHUB_LOGIN_REDIRECT_CHANGED')
        return result

    def role_state(self,role,plan,made,full=True):
        dep=self.get('deployment',role['deployment_name'])
        need(dep['metadata']['uid']==role['before']['uid'],'DEPLOYMENT_IDENTITY_CHANGED')
        current={**identify(dep),'desired':dep['spec'].get('replicas',1),**{k:dep.get('status',{}).get(k,0) for k in ('observedGeneration','replicas','updatedReplicas','readyReplicas','availableReplicas','terminatingReplicas')}}
        if dep['spec']==role['before_spec']:return False,dict(current,phase='NOT_PATCHED')
        need(dep['spec']==role['candidate_spec'],'ROLE_SPEC_CHANGED_NO_ACTION')
        if not converged(dep):return False,dict(current,phase='ROLLOUT_IN_PROGRESS')
        cm=self.get('configmap',made['name'])
        need(cm['metadata']['uid']==made['uid'] and cm.get('immutable') is True and cm.get('data')==role['new_configmap']['data'] and cm.get('binaryData')==role['new_configmap'].get('binaryData'),'QUALIFIED_CONFIGMAP_CHANGED')
        pods=self.pods(dep);desired=dep['spec'].get('replicas',1)
        if len(pods)!=desired:return False,dict(current,phase='WAIT_READY_POD_VIEW')
        ids={p['metadata']['uid'] for p in pods}
        if role['role']=='nginx':
            for bound in BINDING['services']:
                service=self.get('service',bound['name'])
                need(service['metadata']['uid']==bound['uid'] and sha(canonical(service['spec']))==bound['spec_sha256'],'SERVING_SERVICE_SPEC_CHANGED')
                if self.endpoints(bound['name'])!=ids:return False,dict(current,phase='WAIT_SERVING_ENDPOINTS')
        else:
            service=self.get('service','gateway');need(service['metadata']['uid']==plan['gateway_service']['uid'] and sha(canonical(service['spec']))==plan['gateway_service']['spec_sha256'],'GATEWAY_SERVICE_SPEC_CHANGED')
            if self.endpoints('gateway')!=ids:return False,dict(current,phase='WAIT_GATEWAY_ENDPOINTS')
        if not full:return True,dict(current,phase='CONVERGED_SOURCE_CHECK_PENDING')
        for index,pod in enumerate(sorted(pods,key=lambda p:p['metadata']['name']),1):
            self.owner(pod,dep)
            refs=[v for v in pod['spec'].get('volumes',[]) if v.get('configMap',{}).get('name')==made['name']]
            need(len(refs)==1,'NEW_POD_CONFIGMAP_REFERENCE_REQUIRED')
            if role['role']=='gateway':
                result=self.gateway_child(pod,'verify')
                need(result.get('status')=='GATEWAY_READER_NOTIFICATION_SOURCES_QUALIFIED' and result.get('source_files_verified')==14 and result.get('synthetic_checks')==12,'GATEWAY_POD_SOURCE_OR_BEHAVIOR_NOT_ACCEPTED')
                if index%10==0:print(json.dumps({'phase':'VERIFY_GATEWAY_NOTIFICATION_REPAIR','verified_pods':index,'expected':desired}),flush=True)
            else:
                raw=self.command(['exec',pod['metadata']['name'],'-c','nginx','--','cat','/tmp/nginx.conf'],plain=True)
                need(sha(raw)==role['candidate_runtime_sha256'],'INSTALLED_SERVING_RUNTIME_CHANGED')
                checks=[self.route_probe(pod,ROUTE,host) for host in ('cnoocclaw.cnooc.com.cn','heai.cnooc.com.cn')]
                checks.extend(self.route_probe(pod,path) for path in ('/health','/platform/skillhub/','/platform/skillhub/api/web/labels'))
                current.update(http_checks=checks,actual_serving_route_verified=True,skillhub_auth_preserved=True)
        latest=self.get('deployment',role['deployment_name'])
        need(latest['metadata']['uid']==dep['metadata']['uid'] and latest['spec']==dep['spec'] and converged(latest),'ROLE_CHANGED_DURING_SOURCE_VERIFICATION')
        if role['role']=='nginx':self.services(ids)
        else:need(self.endpoints('gateway')==ids,'GATEWAY_ENDPOINTS_CHANGED_DURING_VERIFICATION')
        current.update(phase='RUNTIME_ACCEPTED',verified_pods=desired,source_files_per_pod=14 if role['role']=='gateway' else 1,
                       service_backends_verified=True,loaded_application_modules_verified=False if role['role']=='gateway' else None)
        return True,current

    def wait(self,role,plan,made):
        self.out['last_step']='WAIT_AND_VERIFY_'+role['role'].upper()+'_ROLLOUT'
        deadline=time.monotonic()+(900 if role['role']=='nginx' else 7200);previous=None;last=0
        while time.monotonic()<deadline:
            ready,current=self.role_state(role,plan,made,False)
            if ready:return self.role_state(role,plan,made,True)[1]
            signature=(current.get('updatedReplicas'),current.get('readyReplicas'),current.get('terminatingReplicas'),current.get('phase'))
            if signature!=previous or time.monotonic()-last>30:
                print(json.dumps({'phase':'WAIT_'+role['role'].upper()+'_REPAIR','current':current}),flush=True);previous=signature;last=time.monotonic()
            time.sleep(10)
        raise Refused('ROLLOUT_WAIT_EXPIRED_USE_READONLY_VERIFICATION')

    def activate(self):
        need(not CONTROL.exists(),'REPAIR_ALREADY_CLAIMED_USE_READONLY_VERIFICATION')
        need(not PRIOR_CONTROL.exists(),'PRIOR_REPAIR_CLAIMED_USE_ORIGINAL_READONLY_VERIFICATION')
        print(json.dumps({'phase':'QUALIFY_ACTUAL_SERVING_EDGE_AND_TWO_WORK_MODULES'}),flush=True)
        plan=self.preflight()
        CONTROL.mkdir(mode=0o700);self.claimed=True;self.out['once_claimed']=True
        private_write(CONTROL/'PLAN.json',plan)
        made=self.create_maps(plan)
        for key in ('nginx','gateway'):
            print(json.dumps({'phase':'PATCH_ONLY_'+key.upper()+'_CONFIGMAP_REFERENCE'}),flush=True)
            self.patch(plan['roles'][key],made[key])
            self.out[key]=self.wait(plan['roles'][key],plan,made[key])
        self.out.update(status='SERVING_READER_AND_WORK_NOTIFICATION_REPAIR_RUNTIME_ACCEPTED',all_gateway_pods_verified=True,
                        actual_serving_nginx_verified=True,scheduler_recursion_limit=10000,max_recursion_limit=10000,
                        active_tasks_may_be_interrupted=False,followup='Test the existing report URL while logged in, then one future scheduled occurrence; old notifications are not replayed.')

    def verify(self):
        plan=private_read(CONTROL/'PLAN.json');made=private_read(CONTROL/'CREATED-MAPS.json')
        need(plan.get('schema')=='energy-reader-work-repair-plan.v1' and plan.get('diagnostic_sha256')==BINDING['diagnostic_sha256'],'ACCEPTED_REPAIR_PLAN_REQUIRED')
        self.operation=plan['operation'];self.out['operation']=self.operation
        self.allowed_maps.update(m['name'] for m in made.values())
        complete=True
        for key in ('nginx','gateway'):
            ready,current=self.role_state(plan['roles'][key],plan,made[key],True);self.out[key]=current;complete=complete and ready
        self.out.update(status='SERVING_READER_AND_WORK_NOTIFICATION_REPAIR_RUNTIME_ACCEPTED' if complete else 'REPAIR_PARTIAL_OR_ROLLOUT_PENDING_NO_CHANGE',
                        readonly=True,all_gateway_pods_verified=bool(complete),actual_serving_nginx_verified=self.out['nginx'].get('phase')=='RUNTIME_ACCEPTED',
                        active_tasks_may_be_interrupted=False,normal_application_startup_database_and_redis_access_may_occur=False,
                        original_patch_not_replayed=True)

def main():
    out={'schema':'energy-reader-work-repair.v1','status':'INCOMPLETE','provider_requests':0,'work_messages_sent':0,'credentials_exported':False,'nginx_patch_requests':0,'gateway_patch_requests':0}
    operation=None;mode=None
    try:
        need(sys.argv[1:] in (['--repair-once'],['--verify-readonly'],['--verify-and-save-receipt']),'EXPLICIT_REPAIR_MODE_REQUIRED')
        mode=sys.argv[1]
        need(os.name=='posix' and os.uname().nodename.split('.')[0]=='datax-01' and os.geteuid()==0,'DATAX01_ROOT_REQUIRED')
        need(BINDING.get('schema')=='energy-reader-link-repair-field-binding.v1' and BINDING.get('native_work_notification_confirmed') is True,'VERIFIED_FIELD_BINDING_REQUIRED')
        operation=Repair(load_assets());out=operation.out
        operation.activate() if mode=='--repair-once' else operation.verify()
    except (Exception,KeyboardInterrupt) as exc:
        out.update(status='INCOMPLETE',error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'REPAIR_FAILED_REDACTED')
    out.update(commands=operation.commands if operation else 0,elapsed_seconds=round(time.monotonic()-operation.started,3) if operation else 0,
               observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    save=operation and (operation.claimed or mode=='--verify-and-save-receipt')
    if save:
        try:
            path=CONTROL/('REPORT.json' if operation.claimed else 'POSTCHECK-'+uuid.uuid4().hex+'.json')
            private_write(path,out);out.update(report_path=str(path),report_bytes=len(canonical(out)),report_sha256=sha(canonical(out)))
            if out['status']=='SERVING_READER_AND_WORK_NOTIFICATION_REPAIR_RUNTIME_ACCEPTED':
                accepted=CONTROL/'RUNTIME-ACCEPTANCE.json'
                if not accepted.exists():private_write(accepted,out)
        except Exception as exc:out.update(status='INCOMPLETE',report_write_error_class=type(exc).__name__)
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')

if __name__=='__main__':sys.exit(main())

"""Read the actual Service backends, their configurations, and anonymous route."""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

KUBE=['kubectl','--kubeconfig=/etc/kubernetes/admin.conf','--request-timeout=15s','-n','deer-flow']
SERVICES=('nginx','nginx-nodeport')
PATH='/reports/energy-daily/d934a54e-b894-4970-a32e-9b983c55d0df/2026-11-15.html'

class Refused(ValueError):pass
def need(ok,code):
    if not ok:raise Refused(code)
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
def sha(raw):return hashlib.sha256(raw).hexdigest()
def named(value):return isinstance(value,str) and re.fullmatch('[a-z0-9][a-z0-9.-]{0,252}',value)


class Inspector:
    def __init__(self):
        self.commands=0;self.allowed=set();self.started=time.monotonic()

    def query(self,args,plain=False):
        need(tuple(args) in self.allowed,'FIXED_READONLY_QUERY_REQUIRED')
        need(self.commands<40 and time.monotonic()-self.started<160,'READONLY_INSPECTION_BOUND')
        self.commands+=1
        result=subprocess.run(KUBE+args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=25,
                              env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8'})
        need(len(result.stdout)<=4194304 and len(result.stderr)<=65536,'READONLY_OUTPUT_BOUND')
        need(result.returncode==0,'READONLY_COMMAND_FAILED')
        return result.stdout if plain else json.loads(result.stdout)

    def get(self,kind,name):
        need(kind in ('service','pod','replicaset','deployment','configmap') and named(name),'DISCOVERED_RESOURCE_NAME_INVALID')
        args=['get',kind,name,'-o','json'];self.allowed.add(tuple(args));return self.query(args)

    def endpoints(self,service):
        need(service in SERVICES,'FIXED_SERVICE_REQUIRED')
        args=['get','endpointslices','-l','kubernetes.io/service-name='+service,'-o','json'];self.allowed.add(tuple(args))
        listing=self.query(args);need(len(listing['items'])<=64,'SLICE_COUNT_BOUND')
        result=[]
        for s in listing['items']:
            need(s['metadata'].get('labels',{}).get('kubernetes.io/service-name')==service,'ENDPOINT_SERVICE_SCOPE')
            for endpoint in s.get('endpoints',[]):
                if endpoint.get('conditions',{}).get('ready') is not True or endpoint.get('conditions',{}).get('terminating'):continue
                target=endpoint.get('targetRef',{})
                need(target.get('kind')=='Pod' and target.get('namespace','deer-flow')=='deer-flow' and named(target.get('name')),'POD_ENDPOINT_IDENTITY_REQUIRED')
                result.append({'name':target['name'],'uid':target['uid'],'ports':[p.get('port') for p in s.get('ports',[]) if p.get('protocol','TCP')=='TCP']})
        need(0<len(result)<=4,'READY_ENDPOINT_COUNT_BOUND')
        return sorted(result,key=lambda p:p['name'])

    def serving_pod(self,endpoint):
        pod=self.get('pod',endpoint['name']);meta=pod['metadata']
        need(meta['uid']==endpoint['uid'] and not meta.get('deletionTimestamp'),'SERVING_POD_CHANGED')
        owners=[x for x in meta.get('ownerReferences',[]) if x.get('controller') is True]
        need(len(owners)==1 and owners[0].get('kind')=='ReplicaSet','SERVING_REPLICASET_REQUIRED')
        rs=self.get('replicaset',owners[0]['name']);need(rs['metadata']['uid']==owners[0]['uid'],'SERVING_REPLICASET_CHANGED')
        owner=[x for x in rs['metadata'].get('ownerReferences',[]) if x.get('controller') is True]
        need(len(owner)==1 and owner[0].get('kind')=='Deployment','SERVING_DEPLOYMENT_REQUIRED')
        dep=self.get('deployment',owner[0]['name']);need(dep['metadata']['uid']==owner[0]['uid'],'SERVING_DEPLOYMENT_CHANGED')
        containers=[c for c in pod['spec']['containers'] if c['name']=='nginx'];need(len(containers)==1,'NGINX_CONTAINER_REQUIRED')
        container=containers[0]
        summary={'pod':meta['name'],'uid':meta['uid'],'node':pod['spec'].get('nodeName'),
                 'deployment':dep['metadata']['name'],'deployment_uid':dep['metadata']['uid'],'generation':dep['metadata']['generation'],
                 'spec_sha256':sha(canonical(dep['spec'])),'image':container['image'],'configmaps':[]}
        volumes={v['name']:v for v in pod['spec'].get('volumes',[])}
        for mount in container.get('volumeMounts',[]):
            cmref=volumes.get(mount['name'],{}).get('configMap')
            if not cmref:continue
            cm=self.get('configmap',cmref['name']);data=cm.get('data',{})
            need(len(data)<=32,'CONFIGMAP_KEY_BOUND')
            files=[]
            for key,source in sorted(data.items()):
                if not(key.endswith('.conf') or 'nginx' in key):continue
                need(isinstance(source,str) and len(source.encode())<=2097152,'NGINX_CONFIGURATION_BOUND')
                files.append({'key':key,'bytes':len(source.encode()),'sha256':sha(source.encode()),
                              'reader_route_present':'/reports/energy-daily/' in source,
                              'reader_release_marker_present':'ENERGY DAILY AUTHENTICATED HTML READER' in source})
            summary['configmaps'].append({'name':cmref['name'],'uid':cm['metadata']['uid'],'immutable':cm.get('immutable',False),
                                         'data_sha256':sha(canonical(data)),'mount_path':mount['mountPath'],'subpath':mount.get('subPath'),
                                         'read_only':mount.get('readOnly',False),'files':files})
        startup=' '.join(container.get('command',[])+container.get('args',[]))
        summary['startup_uses_tmp_nginx_conf']='/tmp/nginx.conf' in startup
        if summary['startup_uses_tmp_nginx_conf']:
            args=['exec',meta['name'],'-c','nginx','--','cat','/tmp/nginx.conf'];self.allowed.add(tuple(args))
            raw=self.query(args,True);need(len(raw)<=2097152,'RUNTIME_CONFIG_BOUND')
            summary['runtime_config']={'bytes':len(raw),'sha256':sha(raw),'reader_route_present':b'/reports/energy-daily/' in raw,
                                       'reader_release_marker_present':b'ENERGY DAILY AUTHENTICATED HTML READER' in raw}
        need(2026 in endpoint['ports'],'KNOWN_NGINX_SERVICE_PORT_REQUIRED')
        args=['exec',meta['name'],'-c','nginx','--','curl','--silent','--show-error','--max-time','8','--dump-header','-','--output','/dev/null',
              '--header','Host: cnoocclaw.cnooc.com.cn','--header','X-Forwarded-Proto: https','http://127.0.0.1:2026'+PATH]
        self.allowed.add(tuple(args));raw=self.query(args,True);need(len(raw)<=32768,'HTTP_HEADERS_BOUND')
        lines=raw.decode('iso-8859-1').splitlines();status=re.match(r'HTTP/[0-9.]+ ([0-9]{3})',lines[0] if lines else '')
        locations=[line.split(':',1)[1].strip() for line in lines[1:] if line.lower().startswith('location:')]
        summary['anonymous_route_probe']={'http_status':int(status.group(1)) if status else None,
             'exact_reader_login_redirect':locations==['/api/v1/auth/iam/login?next='+PATH],
             'location_sha256s':[sha(v.encode()) for v in locations],'credentials_used':False,'response_body_read':False}
        fresh=self.get('pod',meta['name']);fresh_dep=self.get('deployment',dep['metadata']['name'])
        need(fresh['metadata']['uid']==meta['uid'] and not fresh['metadata'].get('deletionTimestamp')
             and fresh_dep['metadata']['uid']==dep['metadata']['uid'] and fresh_dep['spec']==dep['spec'],'SERVING_WORKLOAD_CHANGED_DURING_READ')
        summary['stable_readback']=True
        return summary

    def run(self):
        result={'services':[],'serving_workloads':[]}
        seen=set();before={}
        for name in SERVICES:
            service=self.get('service',name);before[name]=service
            endpoints=self.endpoints(name)
            result['services'].append({'name':name,'uid':service['metadata']['uid'],'spec_sha256':sha(canonical(service['spec'])),
                                       'selector':service['spec'].get('selector',{}),'ready_endpoints':endpoints})
            for endpoint in endpoints:
                if endpoint['uid'] not in seen:
                    result['serving_workloads'].append(self.serving_pod(endpoint));seen.add(endpoint['uid'])
        for name,old in before.items():
            fresh=self.get('service',name)
            need(fresh['metadata']['uid']==old['metadata']['uid'] and fresh['spec']==old['spec'],'SERVICE_CHANGED_DURING_READ')
            need(self.endpoints(name)==next(v['ready_endpoints'] for v in result['services'] if v['name']==name),'SERVICE_ENDPOINTS_CHANGED_DURING_READ')
        result.update(status='SERVING_NGINX_ROUTE_OBSERVED_NO_CHANGE',all_service_endpoints_rechecked=True)
        return result


def main():
    out={'schema':'energy-serving-reader-inspection.v1','status':'INCOMPLETE','workload_modified':False,'persistent_files_modified':False,
         'secret_api_reads':0,'provider_requests':0,'credentials_exported':False,'work_messages_sent':0,'raw_configuration_exported':False}
    operation=None
    try:
        need(sys.argv[1:]==['--inspect-readonly'],'READONLY_MODE_REQUIRED')
        need(os.name=='posix' and os.uname().nodename.split('.')[0]=='datax-01' and os.geteuid()==0,'DATAX01_ROOT_REQUIRED')
        operation=Inspector();out.update(operation.run())
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'READONLY_EDGE_INSPECTION_FAILED_REDACTED')
    out['commands']=operation.commands if operation else 0
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')


if __name__=='__main__':sys.exit(main())

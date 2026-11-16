"""Activate only the authenticated report reader; preserve the current services."""
import base64
import copy
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import time
import types
import uuid

READER_SOURCE = '"""Add an authenticated, sandboxed reader for published energy-daily HTML only."""\nfrom __future__ import annotations\n\nfrom dataclasses import dataclass, field\nimport re\n\n\nclass Refused(ValueError):\n    pass\n\n\ndef need(condition, code):\n    if not condition:\n        raise Refused(code)\n\n\n@dataclass\nclass Directive:\n    name: str\n    args: list[str]\n    start: int\n    end: int = 0\n    children: list = field(default_factory=list)\n    parent: object = None\n\n\ndef tokens(source):\n    """Read Nginx quotes/comments so regex braces are not configuration blocks."""\n    index = 0\n    while index < len(source):\n        if source[index].isspace():\n            index += 1\n            continue\n        if source[index] == \'#\':\n            end = source.find(\'\\n\', index)\n            index = len(source) if end < 0 else end + 1\n            continue\n        start = index\n        char = source[index]\n        if char in \'{};\':\n            yield char, char, start, index + 1\n            index += 1\n            continue\n        value = \'\'\n        if char in (\'"\', "\'"):\n            delimiter = char\n            index += 1\n            while index < len(source) and source[index] != delimiter:\n                if source[index] == \'\\\\\' and index + 1 < len(source):\n                    value += source[index:index + 2]\n                    index += 2\n                else:\n                    value += source[index]\n                    index += 1\n            need(index < len(source), \'NGINX_UNTERMINATED_QUOTE\')\n            index += 1\n        else:\n            while index < len(source) and not source[index].isspace() and source[index] not in \'{};#\':\n                if source[index] == \'\\\\\' and index + 1 < len(source):\n                    value += source[index:index + 2]\n                    index += 2\n                else:\n                    value += source[index]\n                    index += 1\n        need(value != \'\' or char in (\'"\', "\'"), \'NGINX_EMPTY_TOKEN\')\n        yield \'word\', value, start, index\n\n\ndef parse(source):\n    need(isinstance(source, str) and 0 < len(source.encode()) <= 2 * 1024 * 1024, \'NGINX_SOURCE_BOUND\')\n    root = Directive(\'<root>\', [], 0)\n    stack, words = [root], []\n    for kind, value, start, end in tokens(source):\n        if kind == \'word\':\n            words.append((value, start))\n        elif kind in (\'{\', \';\'):\n            need(bool(words), \'NGINX_DIRECTIVE_REQUIRED\')\n            node = Directive(words[0][0], [item[0] for item in words[1:]], words[0][1], end, parent=stack[-1])\n            stack[-1].children.append(node)\n            words = []\n            if kind == \'{\':\n                stack.append(node)\n        else:\n            need(not words and len(stack) > 1, \'NGINX_UNBALANCED_BLOCK\')\n            stack.pop().end = end\n    need(len(stack) == 1 and not words, \'NGINX_INCOMPLETE_SOURCE\')\n    return root\n\n\ndef descendants(node):\n    for child in node.children:\n        yield child\n        yield from descendants(child)\n\n\ndef inspect(source):\n    tree = parse(source)\n    apis = [node for node in descendants(tree) if node.name == \'location\' and node.args in ([\'/api/\'], [\'^~\', \'/api/\'])]\n    need(len(apis) == 1 and apis[0].parent.name == \'server\', \'UNIQUE_MAIN_API_LOCATION_REQUIRED\')\n    api, server = apis[0], apis[0].parent\n    passes = [node.args for node in api.children if node.name == \'proxy_pass\']\n    need(len(passes) == 1 and len(passes[0]) == 1 and re.fullmatch(r\'http://(?:[A-Za-z0-9_.:-]+|\\$[A-Za-z_][A-Za-z0-9_]*)\', passes[0][0]), \'FIXED_GATEWAY_PROXY_REQUIRED\')\n    headers = {node.args[0].lower(): node.args[1] for node in api.children\n               if node.name == \'proxy_set_header\' and len(node.args) == 2}\n    proto = headers.get(\'x-forwarded-proto\', \'$scheme\')\n    need(proto in (\'$scheme\', \'$forwarded_proto\'), \'FORWARDED_PROTO_CONTRACT_UNKNOWN\')\n    occupied = any(node.name == \'location\' and any(\'/reports/energy-daily\' in arg or \'@energy_daily_reader_\' in arg for arg in node.args)\n                   for node in server.children)\n    return {\'proxy_pass\': passes[0][0], \'forwarded_proto\': proto,\n            \'api_location_start\': api.start, \'server_start\': server.start,\n            \'report_reader_route_present\': occupied,\n            \'server_listens\': [node.args[0] for node in server.children if node.name == \'listen\' and node.args]}\n\n\ndef reader_block(proxy_pass, forwarded_proto=\'$forwarded_proto\'):\n    need(re.fullmatch(r\'http://(?:[A-Za-z0-9_.:-]+|\\$[A-Za-z_][A-Za-z0-9_]*)\', proxy_pass), \'FIXED_GATEWAY_PROXY_REQUIRED\')\n    need(forwarded_proto in (\'$scheme\', \'$forwarded_proto\'), \'FORWARDED_PROTO_CONTRACT_UNKNOWN\')\n    # Nginx escapes the rewritten UTF-8 URI exactly once when proxying.\n    # Pre-encoded percent signs here would become %25 and break the filename.\n    name = \'油气与金融市场日报_\'\n    csp = ("sandbox allow-popups allow-popups-to-escape-sandbox; default-src \'none\'; "\n           "style-src \'unsafe-inline\'; img-src data:; script-src \'none\'; connect-src \'none\'; "\n           "object-src \'none\'; base-uri \'none\'; form-action \'none\'; frame-ancestors \'self\'")\n    headers = \'\\n\'.join([\n        \'    server_tokens off;\',\n        \'    add_header X-Content-Type-Options nosniff always;\',\n        \'    add_header Referrer-Policy no-referrer always;\',\n        \'    add_header Cache-Control "private, no-store" always;\',\n        \'    add_header X-Frame-Options SAMEORIGIN always;\',\n        \'    add_header Content-Security-Policy "\' + csp + \'" always;\',\n    ])\n    result = \'\'\'# BEGIN ENERGY DAILY AUTHENTICATED HTML READER v1\nlocation /reports/energy-daily/ { server_tokens off; return 404; }\nlocation ~ "^/reports/energy-daily/(?<energy_daily_thread>[A-Za-z0-9_-]{1,64})/(?<energy_daily_date>[0-9]{4}-[0-9]{2}-[0-9]{2})\\\\.html$" {\n    limit_except GET { deny all; }\n    set $energy_daily_return /reports/energy-daily/$energy_daily_thread/$energy_daily_date.html;\n    proxy_http_version 1.1;\n    proxy_method GET;\n    proxy_pass_request_body off;\n    proxy_set_header Content-Length "";\n    proxy_set_header Connection "";\n    proxy_set_header Cookie $http_cookie;\n    proxy_set_header Authorization "";\n    proxy_set_header X-DeerFlow-Internal-Token "";\n    proxy_set_header X-DeerFlow-Owner-User-Id "";\n    proxy_set_header Forwarded "";\n    proxy_set_header X-Forwarded-Port "";\n    proxy_set_header Host $http_host;\n    proxy_set_header X-Forwarded-Host $http_host;\n    proxy_set_header X-Forwarded-Proto __PROTO__;\n    proxy_hide_header X-Powered-By;\n    proxy_hide_header X-AspNet-Version;\n    proxy_hide_header X-AspNetMvc-Version;\n    proxy_hide_header Content-Type;\n    proxy_hide_header Content-Disposition;\n    proxy_hide_header Content-Security-Policy;\n    proxy_hide_header X-Download-Options;\n    proxy_hide_header Cache-Control;\n    proxy_hide_header Referrer-Policy;\n    proxy_hide_header X-Content-Type-Options;\n    proxy_intercept_errors on;\n    error_page 401 = @energy_daily_reader_login;\n    error_page 403 = @energy_daily_reader_denied;\n    error_page 404 = @energy_daily_reader_missing;\n    error_page 500 502 503 504 = @energy_daily_reader_unavailable;\n    proxy_connect_timeout 10s;\n    proxy_read_timeout 30s;\n    add_header Content-Type "text/html; charset=utf-8" always;\n    add_header Content-Disposition inline always;\n__HEADERS__\n    rewrite ^ /api/threads/$energy_daily_thread/artifacts/mnt/user-data/outputs/__NAME__$energy_daily_date.html? break;\n    proxy_pass __UPSTREAM__;\n}\nlocation @energy_daily_reader_login {\n    absolute_redirect off;\n    add_header Cache-Control "private, no-store" always;\n    return 302 /api/v1/auth/iam/login?next=$energy_daily_return;\n}\n__ERROR_LOCATIONS__\n# END ENERGY DAILY AUTHENTICATED HTML READER v1\n\'\'\'\n    errors = []\n    for suffix, code, message in [(\'denied\', 403, \'你没有权限查看这份日报。请使用有权访问当前对话的海能账号登录。\'),\n                                  (\'missing\', 404, \'日报文件尚未发布或已移除。\'),\n                                  (\'unavailable\', 503, \'暂时无法读取日报，请稍后重试。\')]:\n        body = \'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>日报阅读</title><body><p>\' + message + \'</p></body></html>\'\n        errors.append(\'location @energy_daily_reader_\' + suffix + \' {\\n    default_type text/html;\\n    charset utf-8;\\n\' + headers + \'\\n    return \' + str(code) + \' "\' + body.replace(\'"\', \'\\\\"\') + \'";\\n}\')\n    return result.replace(\'__PROTO__\', forwarded_proto).replace(\'__UPSTREAM__\', proxy_pass).replace(\'__HEADERS__\', headers).replace(\'__NAME__\', name).replace(\'__ERROR_LOCATIONS__\', \'\\n\'.join(errors))\n\n\ndef add_reader(source):\n    facts = inspect(source)\n    need(not facts[\'report_reader_route_present\'], \'REPORT_READER_ROUTE_ALREADY_OCCUPIED\')\n    start = source.rfind(\'\\n\', 0, facts[\'api_location_start\']) + 1\n    indent = source[start:facts[\'api_location_start\']]\n    need(not indent.strip(), \'API_LOCATION_LINE_ANCHOR_REQUIRED\')\n    block = reader_block(facts[\'proxy_pass\'], facts[\'forwarded_proto\'])\n    inserted = \'\\n\'.join(indent + line if line else \'\' for line in block.splitlines()) + \'\\n\\n\'\n    result = source[:start] + inserted + source[start:]\n    need(inspect(result)[\'report_reader_route_present\'], \'REPORT_READER_INSERTION_FAILED\')\n    return result, facts\n'
CONTROL = Path('/var/lib/portal-energy-html-reader-20261114-r1')
KUBE = ['kubectl','--kubeconfig=/etc/kubernetes/admin.conf','--request-timeout=20s','-n','deer-flow']
OLD_CM = 'deer-flow-nginx-hostguard-20261011-r1'
NEW_CM = 'deer-flow-nginx-energy-reader-20261114-r1'
CM_UID = 'e9d12df6-1f22-4a2a-818e-02bdb9b4efd9'
CM_DATA = 'ad396fb11e888fb3434a0f6628aff101c7097981d4648c17da51a51e8c755828'
SOURCE_SHA = 'f8a07eb8e8b8bc86d74cb5666c0cd4917505da5cd9a1eaa370c906c91daf0d95'
IMAGE = 'm.daocloud.io/docker.io/library/nginx@sha256:4a73073bd557c65b759505da037898b61f1be6cbcc3c2c3aeac22d2a470c1752'
BASELINES = {
 'nginx': ('10fb043a-eaee-433e-bce0-80688e798611',116,'043c199d237cbab9f5169e10f909865fbf55c4cc3a11776c377a407605b1b03a'),
 'gateway': ('a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1',417,'7faa311dc54be05137ec9c756aa278927e78d47f28118f6779415729c9029aa1'),
 'frontend': ('bb0b3f6e-31f7-4073-8b76-7650ea5b96ee',244,'cacba2be2e81160ece2abbc22a09e363a3b6ec6b322bef883eb8ccfd47611b83'),
}
ROUTE='/reports/energy-daily/11111111-2222-4333-8444-555555555555/2026-11-14.html'
SYNTAX_COMMAND='set -eu; reader_cfg=$(mktemp /tmp/energy-html-reader-config.XXXXXX); case "$reader_cfg" in /tmp/energy-html-reader-config.*) ;; *) exit 90 ;; esac; trap \'rm -f -- "$reader_cfg"\' 0 1 2 3 15; chmod 600 "$reader_cfg"; cat > "$reader_cfg"; nginx -t -c "$reader_cfg"'


class Refused(ValueError):pass
def need(ok,code):
    if not ok:raise Refused(code)
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')
def sha(value):return hashlib.sha256(value).hexdigest()
def identity(row):return {'uid':row['metadata']['uid'],'generation':row['metadata']['generation'],'spec_sha256':sha(canonical(row['spec']))}


def private_write(path,value):
    raw=value if isinstance(value,bytes) else canonical(value)
    fd=os.open(str(path),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb',closefd=False) as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
    finally:os.close(fd)


def private_read(path):
    for part in (path,)+tuple(path.parents):need(not part.is_symlink(),'RECEIPT_LINK_REFUSED')
    before=path.stat();need(stat.S_ISREG(before.st_mode) and before.st_uid==0 and before.st_nlink==1 and before.st_size<4194304,'PRIVATE_RECEIPT_REQUIRED')
    raw=path.read_bytes();after=path.stat()
    need(len(raw)==before.st_size and (before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns),'RECEIPT_CHANGED')
    return json.loads(raw)


class Operation:
    def __init__(self,reader):
        self.reader=reader;self.started=time.monotonic();self.queries=0;self.claimed=False
        self.out={'schema':'energy-html-reader-activation.v1','status':'INCOMPLETE','operation':uuid.uuid4().hex,
                  'once_claimed':False,'configmap_create_requests':0,'nginx_patch_requests':0,'nginx_patch_may_have_applied':False,
                  'gateway_modified':False,'frontend_modified':False,'provisioner_modified':False,'secret_api_reads':0,
                  'credentials_exported':False,'provider_requests':0,'work_messages_sent':0,'business_acceptance_pending':True,
                  'automatic_retry':False,'automatic_rollback':False,'active_nginx_connections_may_reconnect':True}

    def command(self,args,stdin=None,as_json=True):
        self.queries+=1
        result=subprocess.run(KUBE+args,input=stdin,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45,
                              env={'PATH':'/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin','HOME':'/root','LANG':'C.UTF-8'})
        need(len(result.stdout)<=4194304 and len(result.stderr)<=65536,'COMMAND_OUTPUT_BOUND')
        need(result.returncode==0,'FIXED_KUBECTL_COMMAND_FAILED')
        return json.loads(result.stdout) if as_json else result.stdout

    def deployments(self):
        rows=self.command(['get','deployments','nginx','gateway','frontend','-o','json'])['items']
        result={row['metadata']['name']:row for row in rows};need(set(result)==set(BASELINES),'DEPLOYMENT_SET_REQUIRED');return result

    def protected(self,rows):
        for name in ('gateway','frontend'):
            need(identity(rows[name])==dict(zip(('uid','generation','spec_sha256'),BASELINES[name])), 'PROTECTED_DEPLOYMENT_BASELINE_CHANGED')

    def nginx_pod(self,deployment):
        selector=deployment['spec']['selector']
        need(not selector.get('matchExpressions') and selector.get('matchLabels'),'SIMPLE_NGINX_SELECTOR_REQUIRED')
        labels=','.join(k+'='+v for k,v in sorted(selector['matchLabels'].items()))
        need(re.fullmatch('[A-Za-z0-9_./=,-]{1,512}',labels),'NGINX_SELECTOR_INVALID')
        pods=self.command(['get','pods','-l',labels,'-o','json'])['items']
        pods=[p for p in pods if not p['metadata'].get('deletionTimestamp') and any(c.get('type')=='Ready' and c.get('status')=='True' for c in p.get('status',{}).get('conditions',[]))]
        for pod in sorted(pods,key=lambda p:p['metadata']['name']):
            owners=pod['metadata'].get('ownerReferences',[])
            if len(owners)!=1 or owners[0].get('kind')!='ReplicaSet':continue
            rs=self.command(['get','replicaset',owners[0]['name'],'-o','json'])
            if rs['metadata']['uid']!=owners[0]['uid'] or not any(o.get('uid')==deployment['metadata']['uid'] and o.get('kind')=='Deployment' for o in rs['metadata'].get('ownerReferences',[])):continue
            if any(c['name']=='nginx' and c['image']==IMAGE for c in pod['spec']['containers']):return pod
        raise Refused('READY_OWNED_NGINX_POD_REQUIRED')

    def syntax(self,pod,source):
        output=self.command(['exec','-i',pod['metadata']['name'],'-c','nginx','--','sh','-c',SYNTAX_COMMAND],source.encode(),False)
        self.out.update(candidate_nginx_syntax_checked=True,syntax_stdout_sha256=sha(output),syntax_pod_uid=pod['metadata']['uid'])

    def verify(self,plan):
        rows=self.deployments();self.protected(rows)
        actual=rows['nginx'];need(actual['metadata']['uid']==BASELINES['nginx'][0] and actual['spec']==plan['candidate_spec'],'NGINX_CANDIDATE_SPEC_CHANGED')
        status=actual.get('status',{});desired=actual['spec'].get('replicas',1)
        self.out['current']={**identity(actual),'desired':desired,**{k:status.get(k,0) for k in ('observedGeneration','replicas','updatedReplicas','readyReplicas','availableReplicas','terminatingReplicas')}}
        complete=(status.get('observedGeneration',0)>=actual['metadata']['generation'] and all(status.get(k,0)==desired for k in ('replicas','updatedReplicas','readyReplicas','availableReplicas')) and status.get('terminatingReplicas',0)==0)
        if not complete:return False
        cm=self.command(['get','configmap',NEW_CM,'-o','json'])
        need(cm.get('immutable') is True and sha(canonical(cm.get('data',{})))==plan['candidate_data_sha256'],'READER_CONFIGMAP_CHANGED')
        pod=self.nginx_pod(actual)
        raw=self.command(['exec',pod['metadata']['name'],'-c','nginx','--','curl','--silent','--show-error','--max-time','10','--dump-header','-','--output','/dev/null',
                          '--header','Host: cnoocclaw.cnooc.com.cn','--header','X-Forwarded-Proto: https',
                          'http://127.0.0.1:2026'+ROUTE],as_json=False)
        need(len(raw)<=16384,'HTTP_HEADER_BOUND')
        lines=raw.decode('iso-8859-1').splitlines();need(lines and re.fullmatch(r'HTTP/[0-9.]+ 302(?: .*)?',lines[0]),'ANONYMOUS_READER_LOGIN_REDIRECT_REQUIRED')
        locations=[s.split(':',1)[1].strip() for s in lines[1:] if s.lower().startswith('location:')]
        need(locations==['/api/v1/auth/iam/login?next='+ROUTE],'EXACT_LOGIN_RETURN_ROUTE_REQUIRED')
        self.out.update(status='AUTHENTICATED_HTML_READER_DEPLOYED',reader_url_origin='https://cnoocclaw.cnooc.com.cn',reader_route='/reports/energy-daily/<thread_id>/<publication_date>.html',
                        reader_configmap=NEW_CM,reader_configmap_uid=cm['metadata']['uid'],candidate_data_sha256=plan['candidate_data_sha256'],
                        nginx_after=identity(actual),anonymous_login_redirect_verified=True,authenticated_owner_access_business_acceptance_pending=True,
                        original_artifact_api_unchanged=True,protected_deployments_unchanged=True,nginx_pod_verified=pod['metadata']['uid'])
        return True

    def activate(self):
        rows=self.deployments();self.protected(rows);old=rows['nginx']
        need(identity(old)==dict(zip(('uid','generation','spec_sha256'),BASELINES['nginx'])),'NGINX_BASELINE_CHANGED')
        cm=self.command(['get','configmap',OLD_CM,'-o','json'])
        need(cm['metadata']['uid']==CM_UID and cm.get('immutable') is True and sha(canonical(cm.get('data',{})))==CM_DATA,'NGINX_CONFIGMAP_BASELINE_CHANGED')
        source=cm['data']['nginx.conf'];need(sha(source.encode())==SOURCE_SHA,'NGINX_SOURCE_BASELINE_CHANGED')
        candidate,facts=self.reader.add_reader(source)
        need(facts['proxy_pass']=='http://$gateway_upstream' and facts['forwarded_proto']=='$forwarded_proto','OBSERVED_PROXY_CONTRACT_REQUIRED')
        spec=copy.deepcopy(old['spec']);volumes=spec['template']['spec']['volumes']
        matches=[i for i,v in enumerate(volumes) if v['name']=='nginx-config' and v.get('configMap',{}).get('name')==OLD_CM]
        need(len(matches)==1,'FIXED_NGINX_CONFIGMAP_REFERENCE_REQUIRED')
        index=matches[0];volumes[index]['configMap']['name']=NEW_CM
        data=dict(cm['data']);data['nginx.conf']=candidate
        newcm={'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':NEW_CM,'namespace':'deer-flow','labels':{'portal.energy-html-reader/operation':self.out['operation']}},'immutable':True,'data':data}
        need(not cm.get('binaryData'),'UNEXPECTED_BINARY_CONFIGMAP_DATA')
        pod=self.nginx_pod(old);self.syntax(pod,candidate)
        fresh=self.deployments();need(all(identity(fresh[n])==identity(rows[n]) for n in rows),'DEPLOYMENT_CHANGED_DURING_PREFLIGHT')
        need(not CONTROL.exists(),'ONCE_MARKER_EXISTS_DO_NOT_REPLAY')
        CONTROL.mkdir(mode=0o700);self.claimed=True;self.out['once_claimed']=True
        plan={'schema':'energy-html-reader-plan.v1','operation':self.out['operation'],'before':{n:identity(r) for n,r in rows.items()},'candidate_spec':spec,
              'candidate_data_sha256':sha(canonical(data)),'candidate_source_sha256':sha(candidate.encode()),'original_configmap_uid':CM_UID}
        private_write(CONTROL/'ORIGINAL-DEPLOYMENT.json',old);private_write(CONTROL/'ORIGINAL-CONFIGMAP.json',cm);private_write(CONTROL/'PLAN.json',plan)
        print(json.dumps({'phase':'CREATE_IMMUTABLE_HTML_READER_CONFIGURATION','gateway_rollout_required':False}),flush=True)
        self.out['configmap_create_requests']=1
        made=self.command(['create','-f','-','-o','json'],canonical(newcm))
        need(made.get('immutable') is True and made.get('data')==data,'CREATED_CONFIGMAP_MISMATCH')
        fresh=self.deployments();need(all(identity(fresh[n])==identity(rows[n]) for n in rows),'DEPLOYMENT_CHANGED_BEFORE_PATCH')
        patch=[{'op':'test','path':'/metadata/uid','value':old['metadata']['uid']},{'op':'test','path':'/spec','value':old['spec']},
               {'op':'replace','path':'/spec/template/spec/volumes/'+str(index)+'/configMap/name','value':NEW_CM}]
        private_write(CONTROL/'PATCH-INTENT.json',{'operation':self.out['operation'],'patch_sha256':sha(canonical(patch))})
        self.out.update(nginx_patch_requests=1,nginx_patch_may_have_applied=True)
        self.command(['patch','deployment','nginx','--type=json','--patch-file=/dev/stdin','-o','json'],canonical(patch))
        until=time.monotonic()+900
        while time.monotonic()<until:
            if self.verify(plan):return
            print(json.dumps({'phase':'WAIT_ONLY_NGINX_READER','current':self.out['current']}),flush=True);time.sleep(5)
        raise Refused('NGINX_ROLLOUT_WAIT_EXPIRED_VERIFY_READONLY')


def main():
    out={'schema':'energy-html-reader-activation.v1','status':'INCOMPLETE','once_claimed':False,'nginx_patch_requests':0,'configmap_create_requests':0,
         'gateway_modified':False,'secret_api_reads':0,'credentials_exported':False,'provider_requests':0,'work_messages_sent':0}
    operation=None
    try:
        need(sys.argv[1:] in (['--activate-html-reader-once'],['--verify-readonly']),'EXPLICIT_MODE_REQUIRED')
        need(os.name=='posix' and os.uname().nodename.split('.')[0]=='datax-01' and os.geteuid()==0,'DATAX01_ROOT_REQUIRED')
        need(READER_SOURCE,'SEALED_READER_REQUIRED')
        module=types.ModuleType('_sealed_energy_reader');sys.modules[module.__name__]=module
        exec(compile(READER_SOURCE,'<sealed-energy-reader>','exec'),module.__dict__)
        operation=Operation(module);out=operation.out
        if sys.argv[1]=='--verify-readonly':
            plan=private_read(CONTROL/'PLAN.json');out['operation']=plan['operation'];out['readonly']=True
            out['active_nginx_connections_may_reconnect']=False
            if not operation.verify(plan):out['status']='NGINX_READER_ROLLOUT_IN_PROGRESS_NO_CHANGE'
        else:operation.activate()
    except (Exception,KeyboardInterrupt) as exc:
        out.update(status='INCOMPLETE',error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'READER_ACTIVATION_FAILED_REDACTED')
    out.update(observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),commands=operation.queries if operation else 0,
               elapsed_seconds=round(time.monotonic()-operation.started,3) if operation else 0)
    if operation and (operation.claimed or out.get('status')=='AUTHENTICATED_HTML_READER_DEPLOYED'):
        try:
            path=CONTROL/('REPORT.json' if operation.claimed else 'POSTCHECK-'+uuid.uuid4().hex+'.json')
            private_write(path,out);out.update(report_path=str(path),report_sha256=sha(canonical(out)))
            if out['status']=='AUTHENTICATED_HTML_READER_DEPLOYED':
                accepted=CONTROL/'RUNTIME-ACCEPTANCE.json'
                if accepted.exists():need(private_read(accepted).get('nginx_after')==out['nginx_after'],'EXISTING_ACCEPTANCE_CHANGED')
                else:private_write(accepted,out)
        except Exception as exc:out.update(status='INCOMPLETE',report_write_error_class=type(exc).__name__)
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')


if __name__=='__main__':sys.exit(main())

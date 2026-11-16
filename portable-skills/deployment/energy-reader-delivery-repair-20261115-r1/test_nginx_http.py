"""Exercise the real cached Nginx binary against an offline mock artifact API."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

import importlib.util
spec=importlib.util.spec_from_file_location('repair_reader_http',Path(__file__).with_name('nginx_reader.py'))
nginx_reader=importlib.util.module_from_spec(spec);sys.modules[spec.name]=nginx_reader;spec.loader.exec_module(nginx_reader)
operator_spec=importlib.util.spec_from_file_location('repair_reader_operator',Path(__file__).with_name('repair_operator.py'))
operator=importlib.util.module_from_spec(operator_spec);operator_spec.loader.exec_module(operator)
SYNTAX_COMMAND=operator.SYNTAX

DOCKER = r'C:/Users/15285/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe'
NGINX = 'sha256:4a73073bd557c65b759505da037898b61f1be6cbcc3c2c3aeac22d2a470c1752'
MOCK = r'''
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
observations=[]
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_GET(self):
  if self.path=='/_observations':
   self.send_response(200);self.end_headers();self.wfile.write(json.dumps(observations).encode());return
  observations.append({'method':self.command,'path':self.path,'headers':dict(self.headers)})
  status=200 if self.headers.get('Cookie')=='session=owner' else 403 if self.headers.get('Cookie')=='session=other' else 401
  if '2000-01-01' in self.path:status=404
  self.send_response(status)
  self.send_header('Content-Type','application/octet-stream')
  self.send_header('Content-Disposition','attachment; filename=report.html')
  self.send_header('Content-Security-Policy',"default-src 'none'; sandbox")
  self.end_headers()
  self.wfile.write('<!doctype html><html><meta charset="utf-8"><body>TEST_REPORT_日报<svg viewBox="0 0 1 1"></svg></body></html>'.encode())
HTTPServer(('127.0.0.1',18081),Handler).serve_forever()
'''
CLIENT = r'''
import http.client,json,time
from urllib.parse import quote
thread='11111111-2222-4333-8444-555555555555'
route='/reports/energy-daily/'+thread+'/2026-11-14.html'
def req(path,headers={},method='GET'):
 c=http.client.HTTPConnection('127.0.0.1',18082,timeout=5)
 c.request(method,path,headers=headers);r=c.getresponse();body=r.read();h=r.getheaders();c.close()
 return r.status,h,body
for attempt in range(50):
 try:req('/not-found');break
 except OSError:time.sleep(.1)
else:raise AssertionError('NGINX_NOT_READY')
checks=[]
def check(condition,name):
 assert condition,name
 checks.append(name)
s,h,b=req(route)
assert s==302,(s,h,b)
check(dict(h).get('Location')=='/api/v1/auth/iam/login?next='+route,'login_preserves_exact_relative_destination')
s,h,b=req(route,{'Cookie':'session=other'})
check(s==403 and b'TEST_REPORT_' not in b,'other_user_denied_no_report_body')
s,h,b=req(route+'?Authorization=ignored',{'Cookie':'session=owner','Authorization':'Bearer forged','X-DeerFlow-Internal-Token':'forged','X-DeerFlow-Owner-User-Id':'forged','Forwarded':'forged','X-Forwarded-Port':'666','X-Forwarded-Host':'evil.invalid'})
check(s==200 and b'TEST_REPORT_' in b,'owner_reads_original_html')
content_types=[v for k,v in h if k.lower()=='content-type']
assert content_types==['text/html; charset=utf-8'],content_types
check(True,'one_inline_html_content_type')
check(dict(h).get('Content-Disposition')=='inline','inline_instead_of_attachment')
csp=dict(h).get('Content-Security-Policy','')
check("script-src 'none'" in csp and "connect-src 'none'" in csp and csp.startswith('sandbox '),'strict_sandbox_script_and_network_blocked')
check(dict(h).get('Cache-Control')=='private, no-store','private_uncached_response')
s,h,b=req(route,{'Cookie':'session=owner'},'HEAD')
check(s==200 and b==b'','head_is_read_only')
s,h,b=req(route,{'Cookie':'session=owner'},'POST')
check(s==403,'post_denied')
s,h,b=req(route.replace('2026-11-14','2000-01-01'),{'Cookie':'session=owner'})
check(s==404 and b'TEST_REPORT_' not in b,'missing_report_not_leaked')
s,h,b=req('/reports/energy-daily/not-a-valid-path',{'Cookie':'session=owner'})
check(s==404,'unmatched_paths_not_proxied')
artifact='/api/threads/'+thread+'/artifacts/mnt/user-data/outputs/'+quote('油气与金融市场日报_')+'2026-11-14.html'
s,h,b=req(artifact,{'Cookie':'session=owner'})
check(s==200 and dict(h).get('Content-Disposition','').startswith('attachment'),'original_artifact_download_unchanged')
c=http.client.HTTPConnection('127.0.0.1',18081);c.request('GET','/_observations');obs=json.loads(c.getresponse().read());c.close()
forward=obs[2]
assert forward['path']==artifact,(forward['path'],artifact)
check(True,'exact_flat_published_filename_and_query_removed')
headers={k.lower():v for k,v in forward['headers'].items()}
check(all(k not in headers for k in ['authorization','x-deerflow-internal-token','x-deerflow-owner-user-id','forwarded','x-forwarded-port']),'forged_privileged_headers_cleared')
check(headers['x-forwarded-host']=='127.0.0.1:18082' and headers['cookie']=='session=owner','trusted_request_host_and_session_forwarded')
print(json.dumps({'status':'NGINX_HTTP_READER_CHECKS_PASSED','checks':checks,'provider_requests':0,'production_access':False}))
'''


def command(args, check=True):
    result = subprocess.run([DOCKER] + args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    if check and result.returncode:
        raise RuntimeError(result.stdout.decode('utf-8','replace') + result.stderr.decode('utf-8','replace'))
    return result


def run_mode(variable=False):
    name = 'energy-reader-test-' + uuid.uuid4().hex[:10]
    with tempfile.TemporaryDirectory(prefix='energy-reader-http-') as temp:
        directory = Path(temp)
        upstream='http://${gateway_upstream}' if variable == 2 else 'http://$gateway_upstream' if variable else 'http://gateway_upstream'
        config = 'events {}\nhttp { access_log off; upstream gateway_upstream { server 127.0.0.1:18081; } server { listen 18082;\n' + ('set $gateway_upstream 127.0.0.1:18081;\n' if variable else '') + '\nlocation ~ \"\\\\.html$\" { return 404; }\nlocation ^~ /api/ { proxy_set_header Cookie $http_cookie; proxy_pass '+upstream+'; }\nlocation / { return 404; } } }\n'
        config,_=nginx_reader.add_reader(config)
        (directory/'nginx.conf').write_text(config,encoding='utf-8')
        (directory/'mock.py').write_text(MOCK,encoding='utf-8')
        (directory/'client.py').write_text(CLIENT,encoding='utf-8')
        mount='type=bind,source='+str(directory)+',target=/fixture,readonly'
        try:
            syntax=subprocess.run([DOCKER,'run','--rm','-i','--network','none','--read-only','--tmpfs','/tmp:rw','--tmpfs','/var/cache/nginx:rw','--tmpfs','/var/run:rw','--entrypoint','sh',NGINX,'-c',SYNTAX_COMMAND],input=config.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30)
            assert syntax.returncode==0,syntax.stderr.decode('utf-8','replace')
            command(['run','-d','--name',name,'--network','none','--read-only','--mount',mount,'python:3.10-slim','python','-I','-B','/fixture/mock.py'])
            command(['run','-d','--name',name+'-nginx','--network','container:'+name,'--read-only','--tmpfs','/var/cache/nginx:rw','--tmpfs','/var/run:rw','--mount',mount,'--entrypoint','nginx',NGINX,'-c','/fixture/nginx.conf','-g','daemon off;'])
            result=command(['exec',name,'python','-I','-B','/fixture/client.py'],check=False)
            print(result.stdout.decode('utf-8','replace'))
            if result.returncode:
                print(result.stderr.decode('utf-8','replace'))
                print(command(['logs',name+'-nginx'],check=False).stderr.decode('utf-8','replace'))
                return result.returncode
        finally:
            command(['rm','-f',name+'-nginx',name],check=False)
    return 0


if __name__=='__main__':
    sys.exit(run_mode(False) or run_mode(True) or run_mode(2))

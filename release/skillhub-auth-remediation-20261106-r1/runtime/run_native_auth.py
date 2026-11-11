"""Disposable native SkillHub authorization regression against exact local images.
Uses only Docker Desktop, internally isolated synthetic services and an isolated HTTP probe.
No production database, network, credentials, cluster, mounts or service changes.
"""
import argparse,base64,hashlib,hmac,http.cookiejar,io,json,os,re,secrets,subprocess,time,uuid,urllib.request,urllib.error,urllib.parse,zipfile
from pathlib import Path
DOCKER='C:/Users/15285/AppData/Local/Programs/DockerDesktop/resources/bin/docker.exe'
LABEL='codex.skillhub.auth-qualification'
class Refused(RuntimeError):pass
PROBE_CODE = r"""
import base64,http.cookiejar,json,sys,urllib.request,urllib.error
from pathlib import Path
class NoRedirect(urllib.request.HTTPRedirectHandler):
 def redirect_request(self,*a,**k):return None
q=json.load(sys.stdin)
p=Path('/tmp')/('cookies-'+q['client'])
jar=http.cookiejar.MozillaCookieJar(str(p))
if p.exists():jar.load(ignore_discard=True,ignore_expires=True)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(jar),NoRedirect())
req=urllib.request.Request(q['url'],method=q['method'],headers=q['headers'],data=base64.b64decode(q['data']) if q['data'] is not None else None)
try:
 try:r=opener.open(req,timeout=15)
 except urllib.error.HTTPError as e:r=e
 with r:
  body=r.read(2097153)
  if len(body)>2097152:raise RuntimeError('RESPONSE_BOUND')
  cookies=[c.value for c in jar if c.name=='XSRF-TOKEN']
  result={'status':r.code,'body':base64.b64encode(body).decode(),'headers':dict(r.headers),'csrf':cookies}
 jar.save(ignore_discard=True,ignore_expires=True)
 print(json.dumps(result))
except Exception as e:print(json.dumps({'transport_error':type(e).__name__}))
"""
class Client:
 def __init__(self,base,harness):
  assert base.startswith('http://'+harness.prefix+'-') and base.endswith(':8080')
  self.base=base;self.harness=harness;self.client_id=uuid.uuid4().hex;self.csrf_values=[]
 def call(self,path,method='GET',data=None,headers=None,raw=False):
  assert path.startswith('/') and not path.startswith('//')
  hs={'Accept':'application/json'};hs.update(headers or {})
  if data is not None and not isinstance(data,bytes):data=json.dumps(data).encode();hs['Content-Type']='application/json'
  q={'client':self.client_id,'url':self.base+path,'method':method,'headers':hs,'data':base64.b64encode(data).decode() if data is not None else None}
  out=self.harness.docker(['exec','-i',self.harness.prefix+'-probe','python','-I','-B','-c',PROBE_CODE],timeout=25,input=json.dumps(q).encode())
  result=json.loads(out.stdout)
  if 'transport_error' in result:raise OSError('ISOLATED_HTTP_TRANSPORT')
  self.csrf_values=result['csrf'];body=base64.b64decode(result['body'])
  try:value=json.loads(body)
  except (ValueError,UnicodeError):value=None
  return result['status'],body if raw else value,result['headers']
 def csrf(self):
  if len(self.csrf_values)!=1:raise Refused('CSRF_COOKIE_REQUIRED')
  return {'X-XSRF-TOKEN':urllib.parse.unquote(self.csrf_values[0])}
class Harness:
 def __init__(self,args):
  self.args=args;self.op=uuid.uuid4().hex;self.prefix='skillhub-auth-'+self.op[:10]
  self.net=self.prefix+'-net';self.containers=[];self.network=False;self.secret=secrets.token_hex(32);self.password=secrets.token_hex(20)
  self.result={'schema':'skillhub-native-image-auth-qualification.v1','operation':self.op,'production_mutations':0,'production_credentials_used':False,'production_database_access':False,'provider_requests':0,'only_synthetic_local_data':True,'tests':[],'status':'INCOMPLETE'}
 def docker(self,args,timeout=60,check=True,input=None):
  r=subprocess.run([DOCKER,'--context','desktop-linux']+args,input=input,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout)
  if len(r.stdout)>4*1024*1024 or len(r.stderr)>1048576:raise Refused('DOCKER_OUTPUT_BOUND')
  if check and r.returncode:raise Refused('DOCKER_COMMAND_FAILED_'+args[0])
  return r
 def run_container(self,name,args):
  self.containers.append(name)
  return self.docker(['run','--detach','--pull=never','--name',name,'--label',LABEL+'='+self.op,'--network',self.net]+args).stdout.decode().strip()
 def expect(self,name,client,path,expected,**kwargs):
  status,body,headers=client.call(path,**kwargs)
  row={'name':name,'http_status':status,'expected_status':expected,'status':'PASS' if status==expected else 'FAIL'}
  self.result['tests'].append(row)
  if status!=expected:
   row['business_code']=body.get('code') if isinstance(body,dict) else None
   raise Refused('HTTP_EXPECTATION_FAILED_'+name)
  return body,headers
 def startup(self,image,phase):
  name=self.prefix+'-'+phase
  env={'SPRING_DATASOURCE_URL':'jdbc:postgresql://'+self.prefix+'-db:5432/skillhub','SPRING_DATASOURCE_USERNAME':'skillhub','SPRING_DATASOURCE_PASSWORD':self.password,
   'SPRING_DATA_REDIS_HOST':self.prefix+'-redis','SPRING_DATA_REDIS_PORT':'6379','SPRING_DATA_REDIS_PASSWORD':'','SPRING_PROFILES_ACTIVE':'',
   'SKILLHUB_AUTH_DEERFLOW_SESSION_ENABLED':'true','SKILLHUB_AUTH_DEERFLOW_SESSION_SECRET':self.secret,'SKILLHUB_AUTH_SESSION_BOOTSTRAP_ENABLED':'true','SKILLHUB_AUTH_MOCK_ENABLED':'false','SKILLHUB_AUTH_DIRECT_ENABLED':'false','BOOTSTRAP_ADMIN_ENABLED':'false',
   'SKILLHUB_BUILTIN_SKILLS_ENABLED':'false','SKILLHUB_SECURITY_SCANNER_ENABLED':'false','SKILLHUB_RATELIMIT_ENABLED':'false','SKILLHUB_DOWNLOAD_ANON_COOKIE_SECRET':secrets.token_hex(32),'SKILLHUB_SEARCH_SEMANTIC_ENABLED':'false','SKILLHUB_PROFILE_MACHINE_REVIEW_ENABLED':'false',
   'SESSION_COOKIE_SECURE':'false','STORAGE_BASE_PATH':'/tmp/skillhub-qualification-storage','JAVA_TOOL_OPTIONS':'-Xmx768m -XX:TieredStopAtLevel=1' if self.args.native_jre else '-Xmx768m','LOGGING_LEVEL_ROOT':'INFO'}
  args=['--platform','linux/amd64' if self.args.native_jre else 'linux/arm64','--memory','2g','--cpus','4','--read-only','--tmpfs','/tmp:rw,size=512m']
  for k,v in env.items():args += ['--env',k+'='+v]
  if self.args.native_jre:
   jar='original.jar' if phase=='baseline' else 'app.jar'
   src='C:/Users/15285/AppData/Local/Temp/skillhub-auth-candidate-20261106-r1/'+jar
   args += ['--mount','type=bind,source='+src+',target=/app/app.jar,readonly','--entrypoint','sh','maven:3.9.13-eclipse-temurin-21','-c','cp /app/app.jar /tmp/runtime.jar && exec java -XX:MaxRAMPercentage=75.0 -jar /tmp/runtime.jar']
  else:args.append(image)
  self.run_container(name,args)
  identity=json.loads(self.docker(['inspect','--format','{{json .Image}}',name]).stdout)
  jar_sha=self.docker(['exec',name,'sha256sum','/app/app.jar']).stdout.decode().split()[0]
  expected={'baseline':'6fc23f8056b763a1590a9953a83e6bee873e7bb2e935b2dddf93589585500363','candidate':'342d0e02914d790a1c01a845cce4047af8b634fd34ecef0b01a66d5425fe7e3f'}[phase]
  if jar_sha!=expected:raise Refused('EXACT_JAR_IDENTITY_MISMATCH_'+phase)
  self.result.setdefault('runtime_identities',{})[phase]={'image_id':identity,'jar_sha256':jar_sha}
  print(json.dumps({'phase':'WAIT_NATIVE_APPLICATION','component':phase}),flush=True)
  base='http://'+name+':8080';client=Client(base,self)
  end=time.monotonic()+(420 if self.args.native_jre else 720);next_progress=time.monotonic()+30;health_status=None
  while time.monotonic()<end:
   try:
    health_status=client.call('/api/v1/health')[0]
    if health_status==200:
     if self.args.native_jre:
      runtime_sha=self.docker(['exec',name,'sha256sum','/tmp/runtime.jar']).stdout.decode().split()[0]
      if runtime_sha!=expected:raise Refused('EXECUTED_JAR_IDENTITY_MISMATCH')
      self.result['runtime_identities'][phase]['executed_jar_sha256']=runtime_sha
     return name,base
   except (OSError,urllib.error.URLError,TimeoutError):health_status='transport_pending'
   if time.monotonic()>=next_progress:
    print(json.dumps({'phase':'APPLICATION_STARTUP_PENDING','component':phase,'health_status':health_status}),flush=True);next_progress=time.monotonic()+30
   state=json.loads(self.docker(['inspect','--format','{{json .State}}',name]).stdout)
   if not state['Running']:raise Refused('APPLICATION_EXITED_'+phase)
   time.sleep(2)
  raise Refused('APPLICATION_START_TIMEOUT_'+phase)
 def remove_container(self,name):
  state=self.docker(['inspect','--format','{{json .Config.Labels}}',name],check=False)
  if state.returncode:return
  labels=json.loads(state.stdout)
  if labels.get(LABEL)!=self.op:raise Refused('CLEANUP_OWNER_MISMATCH')
  self.docker(['rm','--force','--volumes',name])
 def cleanup(self):
  errors=[]
  for name in reversed(self.containers):
   try:self.remove_container(name)
   except Exception as e:errors.append(type(e).__name__)
  if self.network:
   try:
    labels=json.loads(self.docker(['network','inspect','--format','{{json .Labels}}',self.net]).stdout)
    if labels.get(LABEL)!=self.op:raise Refused('NETWORK_OWNER_MISMATCH')
    self.docker(['network','rm',self.net])
   except Exception as e:errors.append(type(e).__name__)
  self.result['temporary_resources_cleaned']=not errors
  if errors:self.result['cleanup_errors']=errors;self.result['status']='INCOMPLETE'
 def signed(self,valid=True,expired=False):
  now=int(time.time())
  payload={'v':1,'iss':'deerflow-gateway','aud':'skillhub','iat':now-120 if expired else now,'exp':now-60 if expired else now+30,'sub':'synthetic-'+self.op,'email':self.op+'@example.invalid','name':'Synthetic auth qualification','nonce':uuid.uuid4().hex}
  encoded=base64.urlsafe_b64encode(json.dumps(payload,separators=(',',':')).encode()).rstrip(b'=')
  signature=base64.urlsafe_b64encode(hmac.new(self.secret.encode(),encoded,hashlib.sha256).digest()).rstrip(b'=')
  return {'X-SkillHub-Identity':encoded.decode(),'X-SkillHub-Signature':signature.decode() if valid else 'invalid'}
 def seed_download(self,container,user_id):
  # Only the owned disposable database/storage: publication and scanners are not being tested.
  if not isinstance(user_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',user_id):raise Refused('SYNTHETIC_USER_ID_SHAPE')
  slug='qualification-'+self.op[:10]
  content=b'---\nname: qualification-skill\ndescription: Synthetic authorization qualification\n---\nNo business data.\n'
  buf=io.BytesIO()
  with zipfile.ZipFile(buf,'w',compression=zipfile.ZIP_STORED) as z:z.writestr('SKILL.md',content)
  archive=buf.getvalue()
  sql=rf"""BEGIN;
INSERT INTO namespace(slug,display_name,type,status,created_by) VALUES ('{slug}','Synthetic auth qualification','TEAM','ACTIVE','{user_id}') RETURNING id AS ns_id \gset
INSERT INTO namespace_member(namespace_id,user_id,role) VALUES (:ns_id,'{user_id}','OWNER');
INSERT INTO skill(namespace_id,slug,display_name,owner_id,visibility,status,created_by) VALUES (:ns_id,'qualification-skill','Synthetic auth qualification','{user_id}','PUBLIC','ACTIVE','{user_id}') RETURNING id AS skill_id \gset
INSERT INTO skill_version(skill_id,version,status,file_count,total_size,published_at,created_by,bundle_ready,download_ready) VALUES (:skill_id,'1.0.0','PUBLISHED',1,{len(content)},CURRENT_TIMESTAMP,'{user_id}',true,true) RETURNING id AS version_id \gset
UPDATE skill SET latest_version_id=:version_id WHERE id=:skill_id;
COMMIT;
SELECT :skill_id || ',' || :version_id;
"""
  raw=self.docker(['exec','-i',self.prefix+'-db','psql','-X','-q','-At','-U','skillhub','-d','skillhub','-v','ON_ERROR_STOP=1'],input=sql.encode()).stdout.decode().strip()
  if not re.fullmatch(r'[0-9]+,[0-9]+',raw):raise Refused('SYNTHETIC_FIXTURE_IDS')
  skill_id,version_id=map(int,raw.split(','))
  directory='/tmp/skillhub-qualification-storage/packages/'+str(skill_id)+'/'+str(version_id)
  self.docker(['exec',container,'mkdir','-p',directory])
  self.docker(['exec','-i',container,'sh','-c','cat > '+directory+'/bundle.zip'],input=archive)
  self.result['synthetic_download_fixture']={'source':'direct_seed_in_disposable_owned_database_and_tmpfs','zip_bytes':len(archive),'zip_sha256':hashlib.sha256(archive).hexdigest(),'publish_or_review_acceptance':False}
  return '/api/v1/skills/'+slug+'/qualification-skill/download',archive
 def expect_download(self,name,client,path,archive,headers=None):
  body,hs=self.expect(name,client,path,200,headers=headers,raw=True)
  if body!=archive or zipfile.ZipFile(io.BytesIO(body)).namelist()!=['SKILL.md']:raise Refused('SYNTHETIC_DOWNLOAD_IDENTITY_MISMATCH')
  self.result['tests'].append({'name':name+'_zip_identity','status':'PASS','bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()})
 def execute(self):
  assert self.args.baseline != self.args.candidate
  for img in (self.args.baseline,self.args.candidate,'postgres:16-alpine','redis:7-alpine','python:3.12-slim'):
   self.docker(['image','inspect','--format','{{.Id}}',img])
  self.result['baseline_image']=self.args.baseline;self.result['candidate_image']=self.args.candidate
  self.result['runtime_mode']='exact_jar_on_native_java21' if self.args.native_jre else 'exact_arm64_images'
  self.result['arm64_runtime_accepted']=False
  self.docker(['network','create','--internal','--label',LABEL+'='+self.op,self.net]);self.network=True
  self.run_container(self.prefix+'-db',['--memory','512m','--tmpfs','/var/lib/postgresql/data:rw,size=512m','--env','POSTGRES_USER=skillhub','--env','POSTGRES_DB=skillhub','--env','POSTGRES_PASSWORD='+self.password,'postgres:16-alpine'])
  self.run_container(self.prefix+'-redis',['--memory','256m','--tmpfs','/data:rw,size=64m','redis:7-alpine','redis-server','--save','','--appendonly','no'])
  self.run_container(self.prefix+'-probe',['--memory','128m','--read-only','--tmpfs','/tmp:rw,size=64m','python:3.12-slim','python','-I','-B','-c','import time; time.sleep(2400)'])
  end=time.monotonic()+40
  while time.monotonic()<end:
   r=self.docker(['exec',self.prefix+'-db','pg_isready','-U','skillhub','-d','skillhub'],check=False)
   if r.returncode==0:break
   time.sleep(1)
  else:raise Refused('LOCAL_DATABASE_NOT_READY')
  baseline,base=self.startup(self.args.baseline,'baseline')
  for path in ('/api/web/labels','/api/web/skills?page=0&size=1'):
   self.expect('baseline_anonymous_'+path,Client(base,self),path,200)
  self.remove_container(baseline)
  candidate,base=self.startup(self.args.candidate,'candidate')
  routes=('/api/web/labels','/api/web/skills?page=0&size=1','/api/v1/labels','/api/v1/skills?page=0&size=1','/api/v1/search?q=synthetic','/api/v1/skills/global/qualification/versions/1.0.0/files','/api/v1/skills/global/qualification/download','/api/cli/v1/skills/search?q=synthetic')
  compatibility=('/api/v1/check','/api/v1/download?slug=qualification','/api/v1/download/qualification','/api/v1/resolve?slug=qualification','/api/v1/resolve/qualification','/api/web/skills/global/qualification/versions/1.0.0/files','/api/web/skills/global/qualification/versions/1.0.0/file?path=SKILL.md','/api/web/skills/global/qualification/download')
  for path in ('/api/v1/health','/api/v1/auth/providers','/api/v1/auth/methods'):
   self.expect('public_discovery_'+path,Client(base,self),path,200)
  for path in routes+compatibility:
   self.expect('candidate_anonymous_'+path,Client(base,self),path,401)
  for name,headers in [('fabricated_bearer',{'Authorization':'Bearer invalid-synthetic-token'}),('fabricated_cookie',{'Cookie':'SESSION=invalid-synthetic-session'}),('fabricated_identity',self.signed(valid=False)),('signed_header_without_session',self.signed())]:
   for path in routes[:2]:self.expect(name+'_'+path,Client(base,self),path,401,headers=headers)
  boot='/api/v1/auth/session/bootstrap';payload={'provider':'deerflow-session'}
  self.expect('unsigned_bootstrap',Client(base,self),boot,401,method='POST',data=payload)
  self.expect('invalid_signed_bootstrap',Client(base,self),boot,401,method='POST',data=payload,headers=self.signed(valid=False))
  self.expect('expired_signed_bootstrap',Client(base,self),boot,401,method='POST',data=payload,headers=self.signed(expired=True))
  user=Client(base,self)
  me,_=self.expect('valid_signed_bootstrap',user,boot,200,method='POST',data=payload,headers=self.signed())
  for path in routes[:4]:self.expect('native_session_'+path,user,path,200)
  self.expect('ordinary_user_admin_denied',user,'/api/v1/admin/users',403)
  self.expect('session_write_requires_csrf',user,'/api/v1/tokens',403,method='POST',data={'name':'synthetic-read','scopes':['skill:read']})
  self.expect('csrf_refresh',user,'/api/v1/auth/me',200)
  token,_=self.expect('native_token_create',user,'/api/v1/tokens',200,method='POST',data={'name':'synthetic-read','scopes':['skill:read']},headers=user.csrf())
  token=token['data'];bearer={'Authorization':'Bearer '+token['token']}
  download_path,archive=self.seed_download(candidate,me['data']['userId'])
  self.expect('anonymous_real_download_denied',Client(base,self),download_path,401)
  self.expect_download('native_session_download',user,download_path,archive)
  self.expect_download('valid_read_token_download',Client(base,self),download_path,archive,headers=bearer)
  for path in routes[:4]:self.expect('valid_bearer_'+path,Client(base,self),path,200,headers=bearer)
  self.expect('read_token_publish_denied',Client(base,self),'/api/v1/skills/global/publish',403,method='POST',data={},headers=bearer)
  self.expect('read_token_manage_denied',Client(base,self),'/api/v1/tokens',403,headers=bearer)
  self.expect('token_admin_denied',Client(base,self),'/api/v1/admin/users',403,headers=bearer)
  self.expect('native_token_revoke',user,'/api/v1/tokens/'+str(token['id']),204,method='DELETE',headers=user.csrf())
  self.expect('revoked_bearer_denied',Client(base,self),routes[0],401,headers=bearer)
  self.expect('revoked_bearer_download_denied',Client(base,self),download_path,401,headers=bearer)
  self.result.update(status='NATIVE_AUTH_RUNTIME_ACCEPTED',arm64_runtime_accepted=not self.args.native_jre,native_session_authenticated=True,api_token_scope_and_revocation_verified=True,production_acceptance=False,synthetic_authenticated_download_verified=True,production_download_business_acceptance_pending=True)
 def run(self):
  try:self.execute()
  except Exception as e:
   self.result.update(error_class=type(e).__name__,error_code=str(e) if isinstance(e,Refused) else 'LOCAL_RUNTIME_TEST_FAILED')
   for name in self.containers:
    if name.endswith(('-baseline','-candidate')):
     log_result=self.docker(['logs','--tail','160',name],check=False)
     logs=log_result.stdout+b'\n'+log_result.stderr
     # Only temporary app logs; never export body/secret to the parent console.
     if logs:self.args.output.with_name(name+'-private.log').write_bytes(logs)
  finally:self.cleanup()
  self.result['passed']=sum(x['status']=='PASS' for x in self.result['tests'])
  self.result['failed']=sum(x['status']=='FAIL' for x in self.result['tests'])
  self.args.output.write_text(json.dumps(self.result,indent=2)+'\n',encoding='utf-8')
  print(json.dumps(self.result,sort_keys=True))
  return 0 if self.result['status']=='NATIVE_AUTH_RUNTIME_ACCEPTED' else 1
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--baseline',required=True);p.add_argument('--candidate',required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--native-jre',action='store_true')
 args=p.parse_args()
 if args.output.exists():raise SystemExit('NEW_REPORT_REQUIRED')
 raise SystemExit(Harness(args).run())

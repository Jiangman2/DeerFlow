"""Disposable-Pod helper. No production storage, shell, or environment inheritance."""
import argparse
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import resource
import selectors
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path('/work')
STATE = ROOT/'state'


def need(ok, label):
    if not ok: raise RuntimeError(label)


def save(name, value):
    raw = (json.dumps(value, sort_keys=True)+'\n').encode()
    fd = os.open(str(STATE/name), os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw); f.flush(); os.fsync(f.fileno())


def secret(name):
    value = (Path('/test-secrets')/name).read_text().strip()
    need(re.fullmatch('[A-Za-z0-9]{32,64}', value) is not None, 'fixture_secret_shape')
    return value


def environment(role):
    need(role in ('bootstrap','app'), 'invalid_role')
    env = dict(PATH='/usr/local/bin:/usr/bin:/bin', HOME='/work', TMPDIR='/work/tmp',
        LANG='C.UTF-8', GIN_MODE='release', LOG_LEVEL='info', LOG_PATH='', LLM_DEBUG_LOG='false',
        SSL_CERT_FILE='/probe/ca.bin', SSL_CERT_DIR='/probe/empty-certs',
        JIEBA_DICT_DIR='/usr/local/share/weknora/jieba', DB_DRIVER='postgres',
        DB_HOST='127.0.0.1', DB_PORT='5432', DB_NAME='fixture',
        DB_USER='fixture_bootstrap' if role=='bootstrap' else 'fixture_app',
        DB_PASSWORD=secret(role), AUTO_MIGRATE='true' if role=='bootstrap' else 'false',
        AUTO_RECOVER_DIRTY='false', RETRIEVE_DRIVER='postgres', REDIS_ADDR='127.0.0.1:6379',
        REDIS_DB='0', REDIS_PASSWORD='', STORAGE_TYPE='local', LOCAL_STORAGE_BASE_DIR='/work/files',
        STORAGE_ALLOW_LIST='local', NEO4J_ENABLE='false', DOCREADER_ADDR='',
        DUCKDB_SKIP_EXTENSION_LOAD='1', CONCURRENCY_POOL_SIZE='2',
        JWT_SECRET=secret('jwt'), SYSTEM_AES_KEY=secret('aes'),
        SSRF_WHITELIST_EXTRA='aiapi.cnooc.com.cn,gateway,gateway.deer-flow.svc.cluster.local',
        VLM_HTTP_TIMEOUT_SECONDS='90', WEKNORA_MODEL_MAX_CONCURRENCY='1',
        WEKNORA_WIKI_ASYNQ_CONCURRENCY='1', MAX_FILE_SIZE_MB='2')
    for pool in ('CORE','POSTPROCESS','ENRICHMENT','MAINTENANCE','SHARED'):
        env['WEKNORA_ASYNQ_'+pool+'_CONCURRENCY']='1'
    return env


def request(method, path, body=None, token=None, content_type='application/json', timeout=20):
    need(path.startswith(('/api/v1/','/health')), 'nonlocal_api_path')
    headers={'Connection':'close','Content-Type':content_type,'Accept-Language':'en'}
    if token: headers['Authorization']='Bearer '+token
    if body is not None and not isinstance(body, bytes): body=json.dumps(body).encode()
    c=http.client.HTTPConnection('127.0.0.1',8080,timeout=timeout)
    try:
        c.request(method,path,body,headers); r=c.getresponse(); raw=r.read(2*1024**2+1)
        need(len(raw)<=2*1024**2,'local_api_response_bound')
        need(200<=r.status<300,'local_api_http_'+str(r.status))
        return json.loads(raw)
    finally: c.close()


def diagnostics(raw):
    text=raw.decode('utf-8','replace')
    patterns={'panic':r'panic:|fatal error:', 'migration_failed':r'Database migration failed',
        'permission_denied':r'permission denied|Permission denied', 'missing_relation':r'relation .* does not exist',
        'connection_refused':r'connection refused', 'missing_extension':r'extension .* is not available',
        'readonly_filesystem':r'read-only file system', 'jwt_missing':r'JWT_SECRET.*unset',
        'multimodal_started':r'\[ImageMultimodal\] Processing image:',
        'ocr_chunk_created':r'\[ImageMultimodal\] Created image_ocr chunk',
        'caption_chunk_created':r'\[ImageMultimodal\] Created image_caption chunk',
        'multimodal_enqueued':r'Enqueued image:multimodal task',
        'index_failure':r'Failed to.*index|Failed to get embedding model|Failed to init retrieve engine',
        'provider_http_error':r'Http Status (?:4|5)\d\d|http status (?:4|5)\d\d',
        'provider_timeout':r'context deadline exceeded|Client.Timeout',
        'authentication_failed':r'password authentication failed|Unauthorized',
        'config_failed':r'Failed to load config|failed to load config'}
    return {k:len(re.findall(v,text)) for k,v in patterns.items()}


def progress(role,raw,overflow):
    # Only aggregates are persisted, never raw provider/model/configuration logs.
    target=STATE/('LIVE-'+role+'.json'); temp=STATE/('LIVE-'+role+'.tmp')
    with open(str(temp),'w') as f:
        json.dump(dict(classes=diagnostics(raw),logs_truncated=overflow,raw_logs_saved=False),f)
    os.replace(str(temp),str(target))


def prepare():
    need(os.geteuid()==1000 and os.getegid()==1000,'nonroot_required')
    STATE.mkdir(mode=0o700)
    for name in ('tmp','files','app','app/config'): (ROOT/name).mkdir(mode=0o700)
    cfg=json.loads(Path('/probe/config.json').read_bytes())
    (ROOT/'app/config/config.yaml').write_text(json.dumps(cfg))
    for name in ('prompt_templates','builtin_agents.yaml','agent_type_presets.yaml'):
        source=Path('/app/config')/name; target=ROOT/'app/config'/name
        need(source.exists() and not source.is_symlink(),'audited_config_asset_missing')
        if source.is_dir(): shutil.copytree(str(source),str(target))
        else: shutil.copyfile(str(source),str(target))
    (ROOT/'app/config/builtin_models.yaml').write_text('builtin_models: []\n')
    (ROOT/'app/migrations').symlink_to('/app/migrations',target_is_directory=True)
    expected=json.loads(Path('/probe/expected.json').read_bytes())
    need(hashlib.sha256(Path('/probe/ca.bin').read_bytes()).hexdigest()==expected['ca_sha256'],'ca_changed')
    for name, row in expected['files'].items():
        h=hashlib.sha256()
        with open('/'+name,'rb') as f:
            for block in iter(lambda:f.read(1024**2),b''): h.update(block)
        need(h.hexdigest()==row['sha256'],'runtime_file_changed')
    save('READY.json',{'runtime_files':'PASS','clean_config':True,'production_stores':False})


def supervise():
    prepare()
    # Only this process owns and signals the exact child it starts; no PID lookup.
    for role in ('bootstrap','app'):
        while not (STATE/('START-'+role)).exists():
            if (STATE/'STOP-ALL').exists(): return
            time.sleep(.2)
        p=subprocess.Popen(['/app/WeKnora'],env=environment(role),cwd=str(ROOT/'app'),
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
        tail=bytearray(); started=time.monotonic(); beat=started; overflow=False
        try:
            with selectors.DefaultSelector() as sel:
                sel.register(p.stdout,selectors.EVENT_READ)
                while p.poll() is None:
                    for key,_ in sel.select(.2):
                        block=os.read(key.fileobj.fileno(),65536)
                        if block: tail.extend(block)
                        if len(tail)>4*1024**2: overflow=True; del tail[:-4*1024**2]
                    if time.monotonic()-beat>=2:
                        progress(role,tail,overflow); beat=time.monotonic()
                    if (STATE/('STOP-'+role)).exists() or (STATE/'STOP-ALL').exists(): break
                    need(time.monotonic()-started < (240 if role=='bootstrap' else 600),'isolated_app_deadline')
        finally:
            if p.poll() is None:
                os.killpg(p.pid,signal.SIGTERM)
                try:
                    # Continue draining the pipe during graceful shutdown.
                    rest,_=p.communicate(timeout=20); tail.extend(rest[-65536:])
                except subprocess.TimeoutExpired:
                    os.killpg(p.pid,signal.SIGKILL); rest,_=p.communicate(timeout=5); tail.extend(rest[-65536:])
            else:
                rest,_=p.communicate(timeout=5); tail.extend(rest[-65536:])
            progress(role,tail,overflow)
            save('STOPPED-'+role+'.json',dict(exit_code=p.returncode,classes=diagnostics(tail),
                logs_truncated=overflow,raw_logs_saved=False))
        if (STATE/'STOP-ALL').exists(): return
    while True: time.sleep(1)


def setup_models(data):
    for key in ('glm_key','relay_key'):
        need(isinstance(data.get(key),str) and re.fullmatch(r'[!-~]{8,4096}',data[key]),'credential_shape')
    save('SETUP-REQUESTED.json',{'credentials_in_disposable_db':True})
    password=secret('jwt')[:28]+'Aa7!'  # native registration policy is 8..32 characters
    request('POST','/api/v1/auth/register',{'username':'Fixture','email':'fixture@example.test','password':password})
    login=request('POST','/api/v1/auth/login',{'email':'fixture@example.test','password':password})
    need(login.get('success') is True and login.get('token'),'fixture_login_failed')
    token=login['token']
    # Test-only auth token, not a production credential.
    save('AUTH.private.json',{'token':token})
    def model(name,kind,params):
        r=request('POST','/api/v1/models',dict(name=name,type=kind,source='remote',parameters=params),token)
        need(r.get('success') is True,'model_create_failed'); return r['data']['id']
    vlm=model('GLM-5.3-Flash','VLLM',dict(base_url='https://aiapi.cnooc.com.cn/GLM-5.3-Flash/FCGLM-001/v1',
        api_key=data['glm_key'],provider='openai',interface_type='openai',supports_vision=True,
        extra_config={'wire_profile':'enterprise_glm_single_image_sse_v1'},max_concurrency=1))
    embedding=model('Qwen3-Embedding-8B','Embedding',dict(
        base_url='http://gateway:8001/api/weknora/internal-models/v1',api_key=data['relay_key'],
        provider='generic',embedding_parameters={'dimension':1024,'truncate_prompt_tokens':0},max_concurrency=1))
    kb=request('POST','/api/v1/knowledge-bases',dict(name='Isolated GLM embedding acceptance',type='document',
        chunking_config={'chunk_size':512,'chunk_overlap':0,'separators':['\n\n','\n']},
        embedding_model_id=embedding,summary_model_id='',vlm_config={'enabled':True,'model_id':vlm,
        'description_language':'en'},indexing_strategy={'vector_enabled':True,'keyword_enabled':True,
        'wiki_enabled':False,'graph_enabled':False},storage_provider_config={'provider':'local'},
        question_generation_config={'enabled':False},extract_config={'enabled':False},
        auto_tag_config={'enabled':False}),token)
    need(kb.get('success') is True,'kb_create_failed')
    result=dict(kb_id=kb['data']['id'],embedding_model_id=embedding,vlm_model_id=vlm)
    save('SETUP.json',result); return result


def upload():
    save('UPLOAD-REQUESTED.json',{'maximum_document_submissions':1,'automatic_resubmission':False})
    token=json.loads((STATE/'AUTH.private.json').read_bytes())['token']
    kb=json.loads((STATE/'SETUP.json').read_bytes())['kb_id']
    sample=Path('/probe/sample.png').read_bytes(); boundary='wkfixturec972f204'
    body=(('--'+boundary+'\r\nContent-Disposition: form-data; name="enable_multimodel"\r\n\r\ntrue\r\n'
        '--'+boundary+'\r\nContent-Disposition: form-data; name="file"; filename="fixture.png"\r\n'
        'Content-Type: image/png\r\n\r\n').encode()+sample+('\r\n--'+boundary+'--\r\n').encode())
    r=request('POST','/api/v1/knowledge-bases/'+kb+'/knowledge/file',body,token,
        'multipart/form-data; boundary='+boundary,60)
    need(r.get('success') is True,'upload_not_accepted')
    out={'knowledge_id':r['data']['id'],'kb_id':kb,'document_submissions':1}
    save('UPLOAD.json',out); return out


def search():
    save('SEARCH-REQUESTED.json',{'maximum_search_requests':1})
    token=json.loads((STATE/'AUTH.private.json').read_bytes())['token']
    uploaded=json.loads((STATE/'UPLOAD.json').read_bytes())
    r=request('POST','/api/v1/knowledge-bases/'+uploaded['kb_id']+'/hybrid-search',dict(
        query_text='ORBIT 7426 blue rectangle',match_count=10,vector_threshold=0,
        disable_keywords_match=True,disable_vector_match=False,skip_context_enrichment=True),token,timeout=90)
    need(r.get('success') is True and isinstance(r.get('data'),list),'search_failed')
    rows=[{k:x.get(k) for k in ('id','knowledge_id','chunk_type','parent_chunk_id','sub_chunk_id','match_type','score')}
          for x in r['data']]
    result={'matches':rows,'search_requests':1,'query_embedding':'REAL_SERVICE_REQUIRED'}
    save('SEARCH.json',result); return result


def main():
    os.umask(0o077); resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    parser=argparse.ArgumentParser(); parser.add_argument('mode',choices=(
        'idle','status','bootstrap','app','stop-bootstrap','stop-app','stop-all','health','setup','upload','search'))
    mode=parser.parse_args().mode
    if mode=='idle': supervise(); return
    need(os.geteuid()==1000,'nonroot_required')
    if mode=='status':
        names=('READY.json','LIVE-bootstrap.json','LIVE-app.json','STOPPED-bootstrap.json','STOPPED-app.json','SETUP-REQUESTED.json','SETUP.json',
            'UPLOAD-REQUESTED.json','UPLOAD.json','SEARCH-REQUESTED.json','SEARCH.json')
        result={n:json.loads((STATE/n).read_bytes()) for n in names if (STATE/n).exists()}
    elif mode in ('bootstrap','app','stop-bootstrap','stop-app','stop-all'):
        name={'bootstrap':'START-bootstrap','app':'START-app','stop-bootstrap':'STOP-bootstrap',
            'stop-app':'STOP-app','stop-all':'STOP-ALL'}[mode]
        save(name,{'requested':True}); result={'requested':mode}
    elif mode=='health':
        request('GET','/health'); result={'health':'PASS'}
    elif mode=='setup':
        raw=sys.stdin.buffer.read(16385); need(len(raw)<=16384,'input_bound'); result=setup_models(json.loads(raw))
    elif mode=='upload': result=upload()
    else: result=search()
    print(json.dumps(result,sort_keys=True),flush=True)


if __name__=='__main__':
    try: main()
    except BaseException as exc:
        # Only our known error labels can be emitted, never arbitrary exception text.
        label=str(exc) if type(exc) is RuntimeError and re.fullmatch('[a-z0-9_]+',str(exc)) else 'SAFE_DIAGNOSTIC_REQUIRED'
        print(json.dumps({'fixture_error':label,'error_type':type(exc).__name__,'raw_error_printed':False}),flush=True)
        sys.exit(1)

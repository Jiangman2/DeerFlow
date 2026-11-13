"""Read only one stored transport failure and the next three attempted calls."""
import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import re
import signal
import socket
import sys
from urllib.parse import urlsplit

THREAD='0fb37c34-d996-4d8b-aeeb-ea9dcd252a28'
RUN='dc9f8dc8-1f62-4e7b-8452-a9a7bf13e79e'
SEQS=[7,8,9,10]
CALL_SHA='c4bcdfa1d3b8034c6d996c76d8376f6a62baf896854c9a1cb148d98aa71d49a0'
SAFETY_B64=''
SAFETY_SHA=''
COMMON_B64=''
COMMON_SHA=''
OWNER_SQL='SELECT user_id FROM threads_meta WHERE thread_id=$1'
EVENT_SQL="""SELECT seq,run_id,event_type,created_at,octet_length(content) AS bytes,
CASE WHEN octet_length(content)<=8192 THEN content ELSE NULL END AS content
FROM run_events WHERE thread_id=$1 AND run_id=$2 AND seq=ANY($3::bigint[]) ORDER BY seq LIMIT 4"""
SOURCES={name:'/app/backend/packages/harness/deerflow/community/aio_sandbox/'+name+'.py' for name in ('aio_sandbox_provider','remote_backend','backend')}
TRANSPORT_MARKERS={
 'sandbox_readiness_timeout':'failed to become ready within',
 'sandbox_thread_lock_failure':'Failed to acquire sandbox thread lock',
 'provisioner_create_failed':'Provisioner create failed',
 'provisioner_health_failed':'Provisioner health check failed',
 'sandbox_no_longer_alive':'is no longer alive',
 'sandbox_no_longer_tracked':'is no longer tracked',
 'initialization_wrapper':'Unexpected error initializing sandbox',
 'execution_wrapper':'Unexpected error executing command',
 'connection_refused':'Connection refused','connection_reset':'Connection reset',
 'dns_resolution_failed':'Name or service not known','temporary_dns_failure':'Temporary failure in name resolution',
 'read_timeout':'ReadTimeout','connect_timeout':'ConnectTimeout','connection_error':'ConnectionError'}

class Refused(ValueError):pass
def need(ok,code):
    if not ok:raise Refused(code)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def iso(value):return value.isoformat() if hasattr(value,'isoformat') else value
def resource(raw64,expected,label):
    raw=base64.b64decode(raw64,validate=True);need(sha(raw)==expected,'RESOURCE_HASH_REQUIRED')
    ns={'__name__':label};exec(compile(raw,'<'+label+'>','exec'),ns,ns);return ns

def transport(body,common):
    need(isinstance(body,str) and len(body.encode())<=8192,'TOOL_BODY_BOUND')
    flags=[name for name,phrase in TRANSPORT_MARKERS.items() if phrase.casefold() in body.casefold()]
    result={'receipt':common['result_summary'](body),'message_sha256':sha(body.encode()),'message_bytes':len(body.encode()),
            'transport_markers':flags,'raw_message_exported':False,'sanitized_message_maximum_chars':512}
    prefix='Error: Unexpected error initializing sandbox: RuntimeError: ' if 'initialization_wrapper' in flags else 'RuntimeError: '
    match=re.search(r'\bSandbox ([0-9a-f]{8}) failed to become ready within ([0-9]{1,5}(?:\.[0-9]{1,3})?)s at \S+',body) if body.lstrip().casefold().startswith('error:') and 'RuntimeError' in body else None
    if match:
        seconds=float(match.group(2));need(0<seconds<=86400,'STARTUP_SECONDS_BOUND')
        result.update(sandbox_pod_name='sandbox-'+match.group(1),startup_wait_seconds=seconds,
            sanitized_message=prefix+'Sandbox [id] failed to become ready within '+match.group(2)+'s at [endpoint]',message_template_recognized=True)
    else:
        # Do not return free-form server exception detail. Only source-known phrases.
        phrases=[phrase for name,phrase in TRANSPORT_MARKERS.items() if name in flags and name not in ('initialization_wrapper','execution_wrapper')]
        result.update(sanitized_message=(prefix+('; '.join(phrases) if phrases else '[unrecognized detail redacted]'))[:512],message_template_recognized=bool(phrases))
    return result

def attempted_call(obj,common):
    calls=obj.get('tool_calls') if isinstance(obj,dict) else []
    output=[]
    for call in calls[:16] if isinstance(calls,list) else []:
        if not isinstance(call,dict):continue
        args=call.get('args');args=args if isinstance(args,dict) else {}
        command=args.get('command',args.get('cmd',args.get('bash_command','')));command=command if isinstance(command,str) else ''
        categories=[]
        for name,pattern in [('list',r'(?:^|[;&|\s])(?:ls|find|stat)\b'),('python',r'(?:^|[;&|\s/])python(?:3(?:\.\d+)?)?\b'),
                             ('read',r'(?:^|[;&|\s])(?:cat|head|tail|sed|grep|rg)\b'),('environment',r'(?:^|[;&|\s])(?:env|printenv)\b|os\.environ|os\.getenv'),
                             ('package_install',r'\b(?:pip|pip3|apt|apt-get)\b')]:
            if re.search(pattern,command):categories.append(name)
        if call.get('name')=='read_file':categories.append('read')
        output.append({'tool':call.get('name') if call.get('name') in ('bash','read_file') else 'other',
            'categories':categories or ['other'],'summary':common['command_summary'](call.get('name'),args),
            'tool_call_id_sha256':sha(str(call.get('id','')).encode()),'ai_call_is_not_execution_proof':True})
    return output

async def observe(common,safe):
    out={'schema':'energy-recovery-transport-child.v2','status':'INCOMPLETE','thread_id':THREAD,'run_id':RUN,'fixed_event_seqs':SEQS,
        'database_credentials_used':False,'database_readonly':True,'configuration_load_only':True,'application_configuration_read_may_resolve_configured_credentials':True,
        'credentials_exported':False,'provider_requests':0,'files_modified':False,'database_modified':False,'task_retry_requested':False,'gate_modified':False,
        'raw_messages_or_news_exported':False,'post_failure_attempts':[]}
    conn=None;stage='load_config'
    try:
        pg,get_config,_=safe['runtime_modules']();config=get_config();cfg=config.database;need(cfg.backend=='postgres','POSTGRES_REQUIRED')
        timeout=getattr(getattr(config,'sandbox',None),'startup_timeout',None)
        out['configured_startup_timeout_seconds']=timeout if isinstance(timeout,(int,float)) and not isinstance(timeout,bool) and 0<timeout<=86400 else None
        url=cfg.postgres_url.replace('postgresql+asyncpg://','postgresql://',1);parsed=urlsplit(url)
        need(parsed.scheme in ('postgres','postgresql') and parsed.hostname,'DATABASE_ADDRESS_REQUIRED')
        safe.update(DB_HOST=parsed.hostname,DB_PORT=parsed.port or 5432,ALLOW_DB_DNS=True)
        addresses=socket.getaddrinfo(safe['DB_HOST'],safe['DB_PORT'],type=socket.SOCK_STREAM);need(0<len(addresses)<=16,'DATABASE_DNS_BOUND')
        safe['DB_ADDRESSES']={row[4][0] for row in addresses};out['database_credentials_used']=True
        conn=await pg.connect(url,timeout=5,command_timeout=5,server_settings={'default_transaction_read_only':'on','statement_timeout':'5000'})
        stage='read_exact_four_events'
        async with conn.transaction(readonly=True,isolation='repeatable_read'):
            need(await conn.fetchval('SHOW transaction_read_only')=='on','DATABASE_READONLY_REQUIRED')
            for sql in ("SET LOCAL statement_timeout='5000ms'","SET LOCAL lock_timeout='500ms'","SET LOCAL idle_in_transaction_session_timeout='10000ms'"):await conn.execute(sql)
            owner=await conn.fetchval(OWNER_SQL,THREAD);need(isinstance(owner,str) and re.fullmatch('[A-Za-z0-9_-]{1,64}',owner),'THREAD_OWNER_REQUIRED')
            records=await conn.fetch(EVENT_SQL,THREAD,RUN,SEQS);need(len(records)<=4,'EVENT_COUNT_BOUND')
            for row in records:
                need(row['seq'] in SEQS and row['run_id']==RUN,'FIXED_EVENT_IDENTITY_REQUIRED')
                raw=row['content'];need(isinstance(raw,str) and len(raw.encode())<=8192,'FIXED_EVENT_CONTENT_REQUIRED')
                obj=json.loads(raw);need(isinstance(obj,dict),'EVENT_SHAPE_REQUIRED')
                if row['seq']==7:
                    need(row['event_type']=='llm.tool.result' and sha(str(obj.get('tool_call_id','')).encode())==CALL_SHA,'ORIGINAL_DOCTOR_RESULT_REQUIRED')
                    result=transport(common['text'](obj.get('content')),common)
                    result.update(stored_bytes=row['bytes'],tool_status=obj.get('status') if obj.get('status') in ('success','error') else None)
                    out.update(doctor_transport_result=result,sandbox_pod_name=result.get('sandbox_pod_name'))
                else:
                    need(row['event_type']=='llm.ai.response','FIXED_AI_CALL_REQUIRED')
                    out['post_failure_attempts'].append({'seq':row['seq'],'created_at':iso(row['created_at']),'calls':attempted_call(obj,common)})
            out['missing_event_seqs']=[seq for seq in SEQS if seq not in {row['seq'] for row in records}]
            need(7 not in out['missing_event_seqs'],'ORIGINAL_DOCTOR_RESULT_NOT_PRESENT')
        stage='fixed_provider_source_hashes'
        out['sources']={name:common['fixed_hash'](path) for name,path in SOURCES.items()}
        async with conn.transaction(readonly=True,isolation='repeatable_read'):
            need(await conn.fetchval('SHOW transaction_read_only')=='on','DATABASE_READONLY_REQUIRED')
            await conn.execute("SET LOCAL statement_timeout='5000ms'");await conn.execute("SET LOCAL lock_timeout='500ms'")
            need(await conn.fetchval(OWNER_SQL,THREAD)==owner,'THREAD_OWNER_CHANGED')
        out.update(status='FIXED_DOCTOR_TRANSPORT_EVIDENCE_OBSERVED_NO_CHANGE',owner_stable_readback=True)
    except Exception as exc:out.update(last_step=stage,error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'READONLY_CHILD_FAILURE_REDACTED')
    finally:
        if conn is not None:
            try:await conn.close(timeout=2)
            except Exception as exc:out['database_close_error_class']=type(exc).__name__
        safe.update(ALLOW_DB_DNS=False,DB_ADDRESSES=set())
    return out

class Discard:
    def write(self,value):return len(value)
    def flush(self):pass

def main():
    sys.dont_write_bytecode=True;logging.disable(logging.CRITICAL)
    out={'schema':'energy-recovery-transport-child.v2','status':'INCOMPLETE','credentials_exported':False,'files_modified':False,'database_modified':False,'provider_requests':0}
    try:
        signal.signal(signal.SIGALRM,lambda *args:(_ for _ in ()).throw(Refused('CHILD_DEADLINE')));signal.alarm(45)
        safe=resource(SAFETY_B64,SAFETY_SHA,'fixed_readonly_safety');common=resource(COMMON_B64,COMMON_SHA,'fixed_transport_classifiers')
        sys.addaudithook(safe['audit']);raw=sys.stdin.buffer.read(16);need(raw==b'{}','FIXED_EMPTY_PAYLOAD_REQUIRED')
        with contextlib.redirect_stdout(Discard()),contextlib.redirect_stderr(Discard()):out=asyncio.run(observe(common,safe))
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'READONLY_CHILD_FAILURE_REDACTED')
    finally:signal.alarm(0)
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')

if __name__=='__main__':sys.exit(main())

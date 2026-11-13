"""Bounded fixed-thread run/tool evidence; no task execution or user file reads."""
import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import signal
import socket
import stat
import sys
from urllib.parse import urlsplit

THREAD='0fb37c34-d996-4d8b-aeeb-ea9dcd252a28'
SAFETY_B64=''
SAFETY_SHA=''
KINDS=['llm.ai.response','llm.tool.result','run.error','llm.error']
RUN_SQL="SELECT run_id,status,created_at,updated_at,substring(error from 1 for 4096) AS error FROM runs WHERE thread_id=$1 ORDER BY created_at DESC LIMIT 2"
EVENT_SQL="""WITH first_events AS (
 SELECT seq FROM run_events WHERE thread_id=$1 AND run_id=ANY($2::text[]) AND event_type=ANY($3::text[]) ORDER BY seq ASC LIMIT 32),
 last_events AS (
 SELECT seq FROM run_events WHERE thread_id=$1 AND run_id=ANY($2::text[]) AND event_type=ANY($3::text[]) ORDER BY seq DESC LIMIT 32)
SELECT seq,run_id,event_type,created_at,octet_length(content) AS bytes,
CASE WHEN octet_length(content)<=65536 THEN content ELSE NULL END AS content
FROM run_events WHERE thread_id=$1 AND seq IN (SELECT seq FROM first_events UNION SELECT seq FROM last_events)
ORDER BY seq ASC LIMIT 64"""
MAX_SQL='SELECT seq FROM run_events WHERE thread_id=$1 ORDER BY seq DESC LIMIT 1'
OWNER_SQL='SELECT user_id FROM threads_meta WHERE thread_id=$1'
MIDDLEWARE='/app/backend/packages/harness/deerflow/agents/middlewares/skill_workflow_middleware.py'
TOOLS={'bash','read_file','present_files','skill_failed_gate','skill_authority_reload','tool_search'}
ACTIONS=('doctor','prepare','select','import-news','build','accept')
MARKERS=('skill_failed_gate','skill_authority_reload','skill_recovery_contract','failed_gate','authority_reload','semantic_owner','SKILL_WORKFLOW','SKILL_AUTHORITY',
         'BROWSER_RUNTIME_UNAVAILABLE','PermissionError','FileNotFoundError','SyntaxError','CREDENTIALS_NOT_CONFIGURED','PLAYWRIGHT_REQUIRED','PYTHON_310_REQUIRED',
         'readonly','ENVIRONMENT_OBSERVED','ready_for_report','连续 3 次','连续3次','恢复契约','stop_and_report_environment','next_command',
         'Traceback','usage:','unrecognized arguments','command not found','No such file or directory','Permission denied','ModuleNotFoundError',
         'ImportError','argparse','timed out','TimeoutError','timeout','deadline','Killed','cannot execute','not permitted','Read-only file system')
STATUSES={'ENVIRONMENT_OBSERVED','FAILED','READY_FOR_NEWS_SUMMARIES','SOURCES_ALREADY_PREPARED','NEWS_SUPPLEMENT_REQUIRED','REPORT_GENERATED_VISUAL_REVIEW_REQUIRED','REPORT_ACCEPTED','success','error','completed','running','pending','cancelled','interrupted','failed'}
ERRORS={'BROWSER_RUNTIME_UNAVAILABLE','CREDENTIALS_NOT_CONFIGURED','PLAYWRIGHT_REQUIRED','PYTHON_310_REQUIRED','WORKSPACE_REQUIRED','UNEXPECTED_FAILURE','PROJECT_EXISTS','PROJECT_IDENTITY','EDITION_DATE_OR_CUTOFF_INVALID','BROWSER_RUNTIME_UNREADABLE','BROWSER_BINDING_INVALID','NO_USABLE_BROWSER','PROVIDER_REQUEST_FAILED'}
EXCEPTIONS=('PermissionError','FileNotFoundError','ModuleNotFoundError','ImportError','SyntaxError','NameError','UnboundLocalError','AttributeError','TypeError','NotADirectoryError','RecursionError','ValueError','RuntimeError','TimeoutError','TargetClosedError','JSONDecodeError','OSError','ConnectionError')

class Refused(ValueError):pass
def need(value,code):
    if not value:raise Refused(code)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def iso(value):return value.isoformat() if hasattr(value,'isoformat') else value

def safety():
    raw=base64.b64decode(SAFETY_B64,validate=True);need(sha(raw)==SAFETY_SHA,'SAFETY_RESOURCE_CHANGED')
    ns={'__name__':'fixed_readonly_safety'};exec(compile(raw,'<fixed-readonly-safety>','exec'),ns,ns)
    return ns

def text(value):
    if isinstance(value,str):return value
    if isinstance(value,list):return '\n'.join(item.get('text','') for item in value[:32] if isinstance(item,dict) and isinstance(item.get('text'),str))
    if isinstance(value,dict):return json.dumps(value,ensure_ascii=True)
    return ''

def signals(value):
    body=text(value)
    return {'markers':[marker for marker in MARKERS if marker in body],
            'shell_date_substitution_present':bool(re.search(r'\$\(\s*date\s+\+%Y%m%d\s*\)',body)),
            'recovery_stop_mention':'恢复契约' in body and ('3 次' in body or '3次' in body)}

def command_summary(name,args):
    result={'tool':name if name in TOOLS else 'other'}
    if not isinstance(args,dict):return dict(result,arguments_unparsed=True)
    command=args.get('command',args.get('cmd',args.get('bash_command','')))
    command=command if isinstance(command,str) else ''
    result.update(signals(command));result['command_sha256']=sha(command.encode()) if command else None
    result['daily_actions']=[action for action in ACTIONS if re.search(r'daily\.py[\"\x27]?\s+'+re.escape(action)+r'(?:\s|$)',command)]
    result['skill_owned_daily_command']='daily.py' in command and '/mnt/skills/public/energy-market-daily' in command
    match=re.search(r'--workspace(?:=|\s+)(\"[^\"]{0,1024}\"|\x27[^\x27]{0,1024}\x27|[^\s;|&]{1,1024})',command)
    if match:
        raw=match.group(1);value=raw.strip('\"\x27')
        result['workspace']={'value_sha256':sha(value.encode()),'under_session_workspace':value.startswith('/mnt/user-data/workspace/'),
                             'contains_shell_substitution':'$(' in value,'contains_variable_reference':bool(re.search(r'\$[A-Za-z_]',value)),
                             'quote_style':'single' if raw.startswith("'") else 'double' if raw.startswith('"') else 'unquoted',
                             'date_path_shape':'energy-daily-date-substitution' if 'energy-daily-' in value and '$(' in value else 'energy-daily' if 'energy-daily-' in value else 'other'}
    return result

def json_objects(body):
    result=[]
    def add(value,depth=0):
        if depth>3 or len(result)>=32:return
        if isinstance(value,dict):
            result.append(value)
            for key in ('content','stdout','stderr','result','output'):
                if key in value:add(value[key],depth+1)
        elif isinstance(value,list):
            for item in value[:16]:add(item,depth+1)
        elif isinstance(value,str):
            try:add(json.loads(value),depth+1)
            except (ValueError,RecursionError):
                for line in value.splitlines()[:128]:
                    if '{' in line:
                        try:add(json.JSONDecoder().raw_decode(line[line.index('{'):])[0],depth+1)
                        except (ValueError,RecursionError):pass
    add(body);return result

def result_summary(value):
    body=text(value);result={'signals':signals(body),'exit_codes':[],'structured_receipts':[],'starts_with_error_prefix':body.lstrip().casefold().startswith('error:')}
    exits=set(int(code) for code in re.findall(r'(?:exitcode|exit_code|exit code|Process exited with code)[\"\s:=]+(-?\d{1,3})',body,re.I))
    for row in json_objects(value):
        for key in ('exitcode','exit_code','returncode'):
            if isinstance(row.get(key),int) and not isinstance(row[key],bool) and -255<=row[key]<=255:exits.add(row[key])
        safe={}
        if row.get('status') in STATUSES:safe['status']=row['status']
        if 'schema' in row and isinstance(row['schema'],str):
            safe['schema_sha256']=sha(row['schema'].encode())
            if re.fullmatch(r'energy-[a-z0-9.-]{1,100}\.v[0-9]{1,2}',row['schema']):safe['schema']=row['schema']
        if isinstance(row.get('error_code'),str):
            safe['error_code']=row['error_code'] if row['error_code'] in ERRORS else 'OTHER_REDACTED'
            safe['error_code_sha256']=sha(row['error_code'].encode())
        if isinstance(row.get('error_class'),str):safe['error_class']=row['error_class'] if row['error_class'] in EXCEPTIONS else 'OTHER_REDACTED'
        for key in ('ready_for_report','python_supported','playwright_installed','credentials_configured','retry_allowed','dependency_installation_can_repair'):
            if isinstance(row.get(key),bool):safe[key]=row[key]
        if isinstance(row.get('readiness_error_codes'),list):safe['readiness_error_codes']=[code if code in ERRORS else 'OTHER_REDACTED' for code in row['readiness_error_codes'][:16] if isinstance(code,str)]
        if isinstance(row.get('next_command'),str):safe['next_command']=command_summary('bash',{'command':row['next_command']})
        if safe and safe not in result['structured_receipts']:result['structured_receipts'].append(safe)
    result['exit_codes']=sorted(exits)[:8]
    result['exception_classes']=[name for name in EXCEPTIONS if name in body]
    result['errno_values']=sorted({int(number) for number in re.findall(r'\[Errno\s+(\d{1,3})\]',body)})[:8]
    result['error_fingerprint']={'usage':'usage:' in body.lower(),
        'unrecognized_arguments':'unrecognized arguments' in body.lower(),'command_not_found':'command not found' in body.lower(),
        'import_error':any(word in body for word in ('ModuleNotFoundError','ImportError')),
        'permission':any(word in body.lower() for word in ('permission denied','not permitted','read-only file system')),
        'timeout':any(word in body.lower() for word in ('timed out','timeout','deadline'))}
    return result

def event_summary(row):
    result={key:iso(row.get(key)) for key in ('seq','run_id','created_at')};result['kind']=row['event_type'];result['stored_bytes']=row['bytes']
    raw=row.get('content')
    if raw is None:return dict(result,oversized_content_skipped=True)
    need(isinstance(raw,str) and len(raw.encode())<=65536,'EVENT_CONTENT_BOUND')
    try:obj=json.loads(raw)
    except (ValueError,RecursionError):return dict(result,unparsed_content_signals=signals(raw))
    if not isinstance(obj,dict):return dict(result,content_shape='other')
    if row['event_type']=='llm.ai.response':
        result['calls']=[]
        calls=obj.get('tool_calls');calls=calls if isinstance(calls,list) else []
        for call in calls[:16]:
            if not isinstance(call,dict):continue
            summary=command_summary(call.get('name'),call.get('args'))
            summary['tool_call_id_sha256']=sha(str(call.get('id','')).encode());result['calls'].append(summary)
        result.update(text_signals=signals(obj.get('content')),model_text_is_not_execution_proof=True)
    elif row['event_type']=='llm.tool.result':
        result.update(tool=obj.get('name') if obj.get('name') in TOOLS else 'other',tool_status=obj.get('status') if obj.get('status') in ('success','error') else None,
                      tool_call_id_sha256=sha(str(obj.get('tool_call_id','')).encode()),receipt=result_summary(obj.get('content')))
    else:result.update(error_signals=signals(obj.get('content',obj)))
    return result

def correlate_doctors(events):
    calls={}
    for event in events:
        for call in event.get('calls',[]):
            if 'doctor' in call.get('daily_actions',[]):calls[call['tool_call_id_sha256']]={'call_seq':event['seq'],'command':call,'results':[]}
        key=event.get('tool_call_id_sha256')
        if key in calls:calls[key]['results'].append({'result_seq':event['seq'],'tool_status':event.get('tool_status'),'receipt':event.get('receipt')})
    return sorted(calls.values(),key=lambda row:row['call_seq'])[:8]

def fixed_hash(path):
    path=Path(path)
    for part in (path,)+tuple(path.parents):need(not part.is_symlink(),'SOURCE_LINK_REFUSED')
    try:
        fd=os.open(str(path),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    except FileNotFoundError:return {'present':False}
    with os.fdopen(fd,'rb') as stream:
        before=os.fstat(stream.fileno());need(stat.S_ISREG(before.st_mode) and before.st_size<=2097152,'SOURCE_SIZE_OR_TYPE')
        raw=stream.read(2097153);after=os.fstat(stream.fileno())
    sig=lambda item:(item.st_dev,item.st_ino,item.st_size,item.st_mtime_ns,item.st_ctime_ns)
    need(sig(before)==sig(after)==sig(path.lstat()) and len(raw)==before.st_size,'SOURCE_CHANGED_DURING_READ')
    return {'present':True,'bytes':len(raw),'sha256':sha(raw)}

def source_hashes(roots):
    need(isinstance(roots,list) and len(roots)<=4,'SKILL_ROOT_BOUND')
    result={'middleware':fixed_hash(MIDDLEWARE),'skill_candidates':[],'source_text_exported':False,'application_modules_imported':False}
    for root in roots:
        need(isinstance(root,str) and root.startswith('/') and '..' not in Path(root).parts and Path(root).name=='energy-market-daily','SKILL_ROOT_SCOPE')
        result['skill_candidates'].append({'root_sha256':sha(root.encode()),'daily':fixed_hash(Path(root)/'scripts/daily.py'),'authority':fixed_hash(Path(root)/'SKILL.md')})
    return result

async def observe(payload,safe):
    out={'schema':'energy-recovery-gate-child.v1','status':'INCOMPLETE','thread_id':THREAD,'database_readonly':True,'database_credentials_used':False,
         'configuration_load_only':True,'application_configuration_read_may_resolve_configured_credentials':True,'provider_requests':0,'credentials_exported':False,
         'files_modified':False,'database_modified':False,'raw_messages_or_news_exported':False,'task_retry_requested':False,'gate_modified':False,'checkpoint_state_not_read':True}
    conn=None;stage='load_configuration'
    try:
        out['sources']=source_hashes(payload['skill_roots'])
        pg,get_config,_=safe['runtime_modules']();cfg=get_config().database;need(cfg.backend=='postgres','POSTGRES_REQUIRED')
        url=cfg.postgres_url.replace('postgresql+asyncpg://','postgresql://',1);parsed=urlsplit(url)
        need(parsed.scheme in ('postgres','postgresql') and parsed.hostname,'DATABASE_ADDRESS_REQUIRED')
        safe.update(DB_HOST=parsed.hostname,DB_PORT=parsed.port or 5432,ALLOW_DB_DNS=True)
        addresses=socket.getaddrinfo(safe['DB_HOST'],safe['DB_PORT'],type=socket.SOCK_STREAM);need(0<len(addresses)<=16,'DATABASE_DNS_BOUND')
        safe['DB_ADDRESSES']={row[4][0] for row in addresses}
        stage='connect_readonly';out['database_credentials_used']=True
        conn=await pg.connect(url,timeout=5,command_timeout=5,server_settings={'default_transaction_read_only':'on','statement_timeout':'5000'})
        stage='fixed_thread_readonly_snapshot'
        async with conn.transaction(readonly=True,isolation='repeatable_read'):
            need(await conn.fetchval('SHOW transaction_read_only')=='on','DATABASE_READONLY_REQUIRED')
            for sql in ("SET LOCAL statement_timeout='5000ms'","SET LOCAL lock_timeout='500ms'","SET LOCAL idle_in_transaction_session_timeout='10000ms'"):await conn.execute(sql)
            owner=await conn.fetchval(OWNER_SQL,THREAD);need(isinstance(owner,str) and re.fullmatch('[A-Za-z0-9_-]{1,64}',owner),'THREAD_OWNER_REQUIRED')
            before=await conn.fetchval(MAX_SQL,THREAD)
            runs=await conn.fetch(RUN_SQL,THREAD);need(len(runs)<=2,'RUN_BOUND')
            out['runs']=[{**{key:iso(row.get(key)) for key in ('run_id','created_at','updated_at')},'status':row.get('status') if row.get('status') in STATUSES else 'other','error_signals':signals(row.get('error'))} for row in runs]
            ids=[row['run_id'] for row in runs]
            records=await conn.fetch(EVENT_SQL,THREAD,ids,KINDS) if ids else []
            need(len(records)<=64,'EVENT_COUNT_BOUND');events=[];total=0
            for item in records:
                row=dict(item);raw=row.get('content');size=len(raw.encode()) if isinstance(raw,str) else 0
                if total+size>4194304:row['content']=None
                else:total+=size
                events.append(event_summary(row))
            out.update(events=events,event_content_bytes_read=total,event_count=len(events),event_window_selection='FIRST32_AND_LAST32_WITHIN_LATEST2_RUNS',event_window_may_omit_middle_events=len(records)==64,
                       snapshot_max_seq=before,doctor_calls_in_selected_event_window=correlate_doctors(events))
        stage='fixed_source_hashes';need(out['sources']==source_hashes(payload['skill_roots']),'FIXED_SOURCE_CHANGED_DURING_READ')
        stage='readonly_owner_and_event_readback'
        async with conn.transaction(readonly=True,isolation='repeatable_read'):
            need(await conn.fetchval('SHOW transaction_read_only')=='on','DATABASE_READONLY_REQUIRED')
            await conn.execute("SET LOCAL statement_timeout='5000ms'");await conn.execute("SET LOCAL lock_timeout='500ms'")
            need(await conn.fetchval(OWNER_SQL,THREAD)==owner,'THREAD_OWNER_CHANGED')
            after=await conn.fetchval(MAX_SQL,THREAD)
        out.update(status='NAMED_THREAD_RECOVERY_EVIDENCE_OBSERVED_NO_CHANGE',owner_stable_readback=True,events_advanced_during_read=before!=after)
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
    out={'schema':'energy-recovery-gate-child.v1','status':'INCOMPLETE','credentials_exported':False,'provider_requests':0,'files_modified':False,'database_modified':False}
    try:
        signal.signal(signal.SIGALRM,lambda *args:(_ for _ in ()).throw(Refused('CHILD_DEADLINE')));signal.alarm(45)
        safe=safety();sys.addaudithook(safe['audit'])
        raw=sys.stdin.buffer.read(32769);need(len(raw)<=32768,'INPUT_BOUND');payload=json.loads(raw)
        need(isinstance(payload,dict) and set(payload)=={'skill_roots'},'FIXED_PAYLOAD_REQUIRED')
        with contextlib.redirect_stdout(Discard()),contextlib.redirect_stderr(Discard()):out=asyncio.run(observe(payload,safe))
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'READONLY_CHILD_FAILURE_REDACTED')
    finally:signal.alarm(0)
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')

if __name__=='__main__':sys.exit(main())

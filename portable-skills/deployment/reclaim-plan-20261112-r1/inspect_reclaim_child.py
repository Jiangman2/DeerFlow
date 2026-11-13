"""Plan only: trusted owner/run metadata and millisecond nonblocking lock checks."""
import asyncio
import contextlib
import datetime
import errno
try:
    import fcntl
except ImportError:
    fcntl=None
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import signal
import socket
import stat
import sys
import time
from urllib.parse import urlsplit

POOL={'ecs-zdz-pro-00%02d'%number for number in range(11,18)}
EXPECTED={
 'provider':('/app/backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox_provider.py','477ad5e5afb0ab3178e75cff85290268f4c2b72a74b30d27a69bb6e2ec6c1be4'),
 'worker':('/app/backend/packages/harness/deerflow/runtime/runs/worker.py','3d68400f0ba0c60aa1c63e9f771df10e33549a6660ba948f1b17071443a91c1c'),
 'paths':('/app/backend/packages/harness/deerflow/config/paths.py','b47574639e46ee90a0819ca33e2f88d346574274377d0aac5712e8e3aa3f2bce')}
RUN_SQL="""WITH wanted AS (SELECT unnest($1::text[]) AS thread_id)
SELECT w.thread_id,t.user_id,t.status AS thread_status,count(r.run_id)::int AS run_count,
 count(r.run_id) FILTER (WHERE r.status IN ('pending','running'))::int AS active_count,
 count(r.run_id) FILTER (WHERE r.status IS NULL OR r.status NOT IN ('pending','running','success','error','timeout','interrupted'))::int AS unknown_status_count,
 count(r.run_id) FILTER (WHERE r.assistant_id='ppt-master-runner')::int AS ppt_history_count,
 count(r.run_id) FILTER (WHERE coalesce(r.stop_reason,'')<>'')::int AS stop_reason_count,
 count(r.run_id) FILTER (WHERE r.metadata_json IS NOT NULL AND r.metadata_json::jsonb NOT IN ('{}'::jsonb,'null'::jsonb))::int AS nonempty_run_metadata_count,
 count(r.run_id) FILTER (WHERE r.updated_at IS NULL)::int AS unknown_update_count,
 max(r.updated_at) AS last_updated
FROM wanted w LEFT JOIN threads_meta t ON t.thread_id=w.thread_id
LEFT JOIN runs r ON r.thread_id=w.thread_id GROUP BY w.thread_id,t.user_id,t.status"""
INDEX_SQL="SELECT indexname,indexdef FROM pg_indexes WHERE tablename='runs' AND schemaname=current_schema() AND indexname IN ('ix_runs_thread_id','ix_runs_thread_status')"
FIXED_DIAGNOSTIC_ID='887ea9f6-1347-4f0f-ab6d-1295ae3ead49'
DIAG_RUN_SQL="""SELECT run_id,thread_id,status,created_at,updated_at,stop_reason,message_count,
CASE WHEN octet_length(kwargs_json::jsonb #>> '{config,recursion_limit}')<=20 THEN kwargs_json::jsonb #>> '{config,recursion_limit}' ELSE NULL END AS configured_recursion_limit,
octet_length(error) AS error_bytes,CASE WHEN octet_length(error)<=2048 THEN error ELSE NULL END AS error
FROM runs WHERE run_id=$1 LIMIT 1"""
DIAG_OCCURRENCE_ID_SQL="SELECT id,task_id,run_id,status,created_at,started_at,finished_at FROM scheduled_task_runs WHERE id=$1 LIMIT 1"
DIAG_OCCURRENCE_TASK_SQL="SELECT id,task_id,run_id,status,created_at,started_at,finished_at FROM scheduled_task_runs WHERE task_id=$1 ORDER BY created_at DESC LIMIT 1"
DIAG_EVENT_SQL="""WITH first_events AS (
 SELECT seq FROM run_events WHERE thread_id=$1 AND run_id=$2 AND event_type=ANY($3::text[]) ORDER BY seq ASC LIMIT 8),
 last_events AS (
 SELECT seq FROM run_events WHERE thread_id=$1 AND run_id=$2 AND event_type=ANY($3::text[]) ORDER BY seq DESC LIMIT 24),
 selected AS (
 SELECT seq,run_id,event_type,created_at,octet_length(content) AS bytes
 FROM run_events WHERE thread_id=$1 AND run_id=$2 AND seq IN (SELECT seq FROM first_events UNION SELECT seq FROM last_events)),
 budget AS (
 SELECT *,sum(CASE WHEN bytes<=8192 THEN bytes ELSE 0 END) OVER (ORDER BY seq DESC) AS cumulative_bytes FROM selected)
SELECT b.seq,b.run_id,b.event_type,b.created_at,b.bytes,
 CASE WHEN b.bytes<=8192 AND b.cumulative_bytes<=63488 THEN e.content ELSE NULL END AS content
FROM budget b JOIN run_events e ON e.thread_id=$1 AND e.run_id=$2 AND e.seq=b.seq ORDER BY b.seq LIMIT 32"""
DIAG_KINDS=['llm.ai.response','llm.tool.result','run.error','llm.error']
DIAG_ACTIONS=('doctor','prepare','select','import-news','build','accept','_acquire')
DIAG_KNOWN_SKILLS=('energy-market-daily','research-fund-settlement','ppt-master')
DIAG_STATUSES={'ENVIRONMENT_OBSERVED','FAILED','READY_FOR_NEWS_SUMMARIES','SOURCES_ALREADY_PREPARED','NEWS_SUPPLEMENT_REQUIRED','SUMMARY_BATCH_READY','VERIFIED_SOURCE_EVIDENCE_IMPORTED','REPORT_GENERATED_VISUAL_REVIEW_REQUIRED','REPORT_ACCEPTED','ACQUISITION_FINISHED'}
DIAG_ERRORS={'UNEXPECTED_FAILURE','BROWSER_RUNTIME_UNAVAILABLE','NO_USABLE_BROWSER','CREDENTIALS_NOT_CONFIGURED','PROJECT_EXISTS','PROJECT_IDENTITY','WORKSPACE_REQUIRED','PLAYWRIGHT_REQUIRED','PROVIDER_REQUEST_FAILED','ACQUISITION_WORKER_FAILED','INPUTS_CHANGED_REBUILD'}
DIAG_EXCEPTIONS=('GraphRecursionError','RecursionError','RuntimeError','PermissionError','FileNotFoundError','NameError','UnboundLocalError','AttributeError','TypeError','ValueError','SyntaxError','JSONDecodeError','TargetClosedError','TimeoutError','ConnectionError')
DIAG_MARKERS=('recursion_limit','recursion limit','recursion_capped','skill_failed_gate','skill_authority_reload','skill_recovery_contract','恢复契约','authority_reload','failed to become ready within','Unexpected error initializing sandbox','NEWS_SUPPLEMENT_REQUIRED','BROWSER_RUNTIME_UNAVAILABLE','NO_USABLE_BROWSER')
DB_HOST=None;DB_PORT=None;DB_ADDRESSES=set();ALLOW_DB_DNS=False
LOCK_BASENAME=None

class Refused(ValueError):pass
class Deadline(BaseException):pass
def need(ok,code):
    if not ok:raise Refused(code)
def sha(raw):return hashlib.sha256(raw).hexdigest()
def iso(value):return value.isoformat() if hasattr(value,'isoformat') else value

def diag_text(value):
    if isinstance(value,str):return value
    if isinstance(value,list):return '\n'.join(item.get('text','') for item in value[:16] if isinstance(item,dict) and isinstance(item.get('text'),str))
    if isinstance(value,dict):return json.dumps(value,ensure_ascii=True)
    return ''

def diag_signals(value):
    body=diag_text(value)
    return {'markers':[term for term in DIAG_MARKERS if term.casefold() in body.casefold()],
        'known_skill_mentions':[name for name in DIAG_KNOWN_SKILLS if name in body],
        'exception_classes':[name for name in DIAG_EXCEPTIONS if name in body],
        'recursion_limit_values':sorted({int(number) for number in re.findall(r'recursion[_ ]limit.{0,20}?([0-9]{1,5})',body,re.I)})[:8]}

def diag_command(call):
    args=call.get('args');args=args if isinstance(args,dict) else {};value=args.get('command',args.get('cmd',args.get('bash_command','')));value=value if isinstance(value,str) else ''
    name=call.get('name');name=name if name in ('bash','read_file','write_file','edit_file','present_files','tool_search','web_search','web_fetch','view_image') else 'other'
    result={'tool':name,'tool_call_id_sha256':sha(str(call.get('id','')).encode()),'command_sha256':sha(value.encode()) if value else None,
        'daily_actions':[action for action in DIAG_ACTIONS if re.search(r'daily\.py[\"\x27]?\s+'+re.escape(action)+r'(?:\s|$)',value)],
        'workspace_dynamic_date_substitution':'$(' in value and 'date' in value,'ai_proposal_is_not_execution_proof':True}
    joined=value+' '+str(args.get('path',''))
    result['known_skill_markers']=[name for name in DIAG_KNOWN_SKILLS if name in joined]
    result['script_basename_kinds']=[name for name in ('daily.py','entry.py','project_manager.py','generate_report.py','validate_inputs.py') if name in value]
    result['script_verbs']=[verb for verb in ('doctor','init','prepare','select','check','build','accept','complete','inspect','generate') if re.search(r'\b(?:daily|entry|project_manager|generate_report|validate_inputs)\.py[\"\x27]?\s+'+verb+r'(?:\s|$)',value)]
    result['path_categories']=[kind for kind,term in [('installed_skill','/mnt/skills/public/energy-market-daily'),('project_workspace','/mnt/user-data/workspace'),('outputs','/mnt/user-data/outputs'),('skill_authority','SKILL.md'),('runtime_reference','references/runtime.md')] if term in joined]
    return result

def diag_objects(value):
    found=[]
    def add(item,depth=0):
        if depth>3 or len(found)>=16:return
        if isinstance(item,dict):
            found.append(item)
            for key in ('content','stdout','stderr','result','output'):
                if key in item:add(item[key],depth+1)
        elif isinstance(item,list):
            for part in item[:8]:add(part,depth+1)
        elif isinstance(item,str):
            try:add(json.loads(item),depth+1)
            except (ValueError,RecursionError):
                for line in item.splitlines()[:64]:
                    if '{' in line:
                        try:add(json.JSONDecoder().raw_decode(line[line.index('{'):])[0],depth+1)
                        except (ValueError,RecursionError):pass
    add(value);return found

def diag_receipt(value):
    body=diag_text(value);result={'signals':diag_signals(body),'starts_with_error_prefix':body.lstrip().casefold().startswith('error:'),'structured_receipts':[]}
    exits={int(number) for number in re.findall(r'(?:exitcode|exit_code|exit code|Process exited with code)[\"\s:=]+(-?\d{1,3})',body,re.I)}
    for obj in diag_objects(value):
        item={}
        if isinstance(obj.get('status'),str) and obj['status'] in DIAG_STATUSES:item['status']=obj['status']
        if isinstance(obj.get('error_code'),str):item.update(error_code=obj['error_code'] if obj['error_code'] in DIAG_ERRORS else 'OTHER_REDACTED',error_code_sha256=sha(obj['error_code'].encode()))
        if obj.get('error_class') in DIAG_EXCEPTIONS:item['error_class']=obj['error_class']
        for key in ('ready_for_report','market_data_complete','market_complete','credentials_configured','retry_allowed'):
            if isinstance(obj.get(key),bool):item[key]=obj[key]
        for key in ('exitcode','exit_code','returncode'):
            if isinstance(obj.get(key),int) and not isinstance(obj[key],bool) and -255<=obj[key]<=255:exits.add(obj[key])
        if item and item not in result['structured_receipts']:result['structured_receipts'].append(item)
    result['exit_codes']=sorted(exits)[:8];return result

def diag_events(records):
    need(len(records)<=32,'FIXED_DIAGNOSTIC_EVENT_COUNT_BOUND');events=[];calls={};actual_results=0;read_bytes=0
    for row in records:
        result={'seq':row['seq'],'kind':row['event_type'],'created_at':iso(row['created_at']),'stored_bytes':row['bytes']};raw=row.get('content')
        if raw is None:result['content_skipped_by_sql_size_or_total_budget']=True;events.append(result);continue
        need(isinstance(raw,str) and len(raw.encode())<=8192,'FIXED_DIAGNOSTIC_EVENT_BYTES');read_bytes+=len(raw.encode());need(read_bytes<=63488,'FIXED_DIAGNOSTIC_TOTAL_BYTES')
        try:obj=json.loads(raw)
        except (ValueError,RecursionError):obj=None
        if row['event_type']=='llm.ai.response' and isinstance(obj,dict):
            proposed=obj.get('tool_calls');proposed=proposed if isinstance(proposed,list) else []
            result['proposed_calls']=[diag_command(call) for call in proposed[:8] if isinstance(call,dict)]
            for call in result['proposed_calls']:calls[call['tool_call_id_sha256']]=call
            result['model_text_signals']=diag_signals(obj.get('content'));result['model_text_is_not_execution_proof']=True
        elif row['event_type']=='llm.tool.result' and isinstance(obj,dict):
            key=sha(str(obj.get('tool_call_id','')).encode());actual_results+=1
            result.update(tool=obj.get('name') if obj.get('name') in ('bash','read_file','write_file','edit_file','present_files','tool_search','web_search','web_fetch','view_image') else 'other',tool_call_id_sha256=key,tool_status=obj.get('status') if obj.get('status') in ('success','error') else None,receipt=diag_receipt(obj.get('content')),
                owning_call_present_in_selected_window=key in calls)
            if key in calls:result['owning_proposal']=calls[key]
        else:result['error_signals']=diag_signals(obj.get('content') if isinstance(obj,dict) else raw)
        events.append(result)
    hashes={};skills=set()
    for call in calls.values():
        skills.update(call['known_skill_markers'])
        key=call.get('command_sha256')
        if key:hashes[key]=hashes.get(key,0)+1
    action_receipts=[{'seq':row['seq'],'actions':row['owning_proposal']['daily_actions'],'tool_status':row['tool_status'],'receipt':row['receipt']} for row in events if row.get('owning_proposal',{}).get('daily_actions')]
    for row in events:skills.update(row.get('receipt',{}).get('signals',{}).get('known_skill_mentions',[]))
    return {'events':events,'event_count':len(events),'event_content_bytes_read':read_bytes,'event_and_run_error_content_budget_bytes':65536,'event_content_budget_bytes':63488,'run_error_content_budget_bytes':2048,'event_window_selection':'FIRST8_AND_LAST24_FIXED_RUN',
        'event_window_may_omit_middle_or_oversized_content':len(records)==32 or any(row.get('content') is None for row in records),
        'actual_tool_result_count':actual_results,'proposed_call_count_in_selected_window':len(calls),'daily_action_receipts':action_receipts,
        'observed_known_skill_markers':sorted(skills),'task_skill_confirmation':'DAILY_SKILL_MARKER_OBSERVED_NOT_AUTHORITY_PROOF' if 'energy-market-daily' in skills else 'NOT_CONFIRMED_DAILY_SKILL',
        'repeated_command_hash_counts':[{'command_sha256':key,'proposed_count':count} for key,count in sorted(hashes.items()) if count>1],
        'repetition_alone_does_not_prove_no_progress':True,'missing_result_does_not_prove_command_execution':True,'raw_content_exported':False}

async def diagnose_fixed_run(conn):
    result={'requested_identifier':FIXED_DIAGNOSTIC_ID,'status':'INCOMPLETE','run_or_gate_modified':False,'raw_messages_or_errors_exported':False}
    try:
        async with conn.transaction(readonly=True,isolation='repeatable_read'):
            need(await conn.fetchval('SHOW transaction_read_only')=='on','FIXED_DIAGNOSTIC_READONLY_REQUIRED')
            for sql in ("SET LOCAL statement_timeout='5000ms'","SET LOCAL lock_timeout='500ms'","SET LOCAL idle_in_transaction_session_timeout='10000ms'"):await conn.execute(sql)
            rows=await conn.fetch(DIAG_RUN_SQL,FIXED_DIAGNOSTIC_ID);need(len(rows)<=1,'FIXED_RUN_ID_BOUND')
            if rows:result['id_mapping_basis']='EXACT_RUN_ID'
            else:
                mapping=await conn.fetch(DIAG_OCCURRENCE_ID_SQL,FIXED_DIAGNOSTIC_ID);need(len(mapping)<=1,'FIXED_OCCURRENCE_BOUND')
                result['id_mapping_basis']='EXACT_SCHEDULED_OCCURRENCE_ID'
                if not mapping:
                    mapping=await conn.fetch(DIAG_OCCURRENCE_TASK_SQL,FIXED_DIAGNOSTIC_ID);need(len(mapping)<=1,'FIXED_TASK_LATEST_BOUND')
                    result.update(id_mapping_basis='TASK_LATEST_OCCURRENCE_AS_OF_SNAPSHOT',latest_mapping_not_proof_of_requested_1152_occurrence=True)
                if mapping:
                    occurrence=dict(mapping[0]);result['occurrence']={key:iso(occurrence.get(key)) for key in ('created_at','started_at','finished_at')}
                    result['occurrence']['status']=occurrence.get('status') if occurrence.get('status') in ('queued','launching','running','success','failed','skipped','interrupted') else 'other'
                    if occurrence.get('run_id'):rows=await conn.fetch(DIAG_RUN_SQL,occurrence['run_id']);need(len(rows)<=1,'MAPPED_RUN_ID_BOUND')
            if not rows:return dict(result,status='FIXED_RUN_NOT_FOUND_OR_NOT_LAUNCHED')
            row=dict(rows[0]);need(isinstance(row.get('thread_id'),str) and re.fullmatch('[A-Za-z0-9_-]{1,64}',row['thread_id']) and isinstance(row.get('run_id'),str) and re.fullmatch('[A-Za-z0-9_-]{1,64}',row['run_id']),'FIXED_RUN_BINDING_SHAPE')
            result['resolved_run_id_sha256']=sha(row['run_id'].encode())
            result['run']={key:iso(row.get(key)) for key in ('created_at','updated_at','message_count','error_bytes')}
            result['run'].update(status=row.get('status') if row.get('status') in ('pending','running','success','error','timeout','interrupted') else 'other',
                stop_reason=row.get('stop_reason') if row.get('stop_reason') in (None,'recursion_capped','loop_capped','token_capped','safety_capped','subagent_limit_capped','awaiting_human_input') else 'other',error_signals=diag_signals(row.get('error')),
                oversized_error_skipped=row.get('error') is None and bool(row.get('error_bytes')))
            scalar=row.get('configured_recursion_limit')
            result['run']['projected_config_recursion_limit']=int(scalar) if isinstance(scalar,str) and re.fullmatch('[0-9]{1,7}',scalar) else None
            result['run']['missing_config_value_does_not_establish_a_default']=True
            records=await conn.fetch(DIAG_EVENT_SQL,row['thread_id'],row['run_id'],DIAG_KINDS)
            result.update(diag_events([dict(item) for item in records]),status='FIXED_RUN_BOUNDED_DIAGNOSTIC_OBSERVED_NO_CHANGE')
    except Exception as exc:result.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'FIXED_RUN_DIAGNOSTIC_FAILURE_REDACTED')
    return result

def audit(event,args):
    if event=='open':
        mode=args[1] if len(args)>1 else None;flags=args[2] if len(args)>2 else 0
        writing=isinstance(mode,str) and any(token in mode for token in ('w','a','x','+')) or isinstance(flags,int) and flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)
        if writing:
            allowed=LOCK_BASENAME is not None and args[0]==LOCK_BASENAME and isinstance(flags,int) and flags&os.O_ACCMODE==os.O_RDWR and flags&os.O_NOFOLLOW and not flags&(os.O_CREAT|os.O_TRUNC|os.O_APPEND)
            need(allowed,'PERSISTENT_WRITE_BLOCKED')
    if event in ('os.mkdir','os.remove','os.rmdir','os.rename','os.link','os.symlink','os.truncate','os.chmod','os.chown','os.utime','subprocess.Popen','os.system','sqlite3.connect'):
        raise Refused('MUTATION_OR_PROCESS_BLOCKED')
    if event=='socket.getaddrinfo':need(ALLOW_DB_DNS and args[0]==DB_HOST and args[1] in (DB_PORT,str(DB_PORT),None),'NON_DATABASE_DNS_BLOCKED')
    if event=='socket.connect':
        target=args[1];need(isinstance(target,tuple) and len(target)>=2 and target[0] in DB_ADDRESSES and target[1]==DB_PORT,'NON_DATABASE_NETWORK_BLOCKED')
    if event in ('socket.bind','socket.gethostbyname','socket.gethostbyaddr','socket.sendto'):raise Refused('NON_DATABASE_NETWORK_BLOCKED')

def runtime_modules():
    import asyncpg
    from deerflow.config import get_app_config
    from deerflow.config import paths
    return asyncpg,get_app_config,paths

def fingerprint(path):
    path=Path(path)
    for part in (path,)+tuple(path.parents):need(not part.is_symlink(),'SOURCE_LINK')
    with path.open('rb') as stream:
        before=os.fstat(stream.fileno());need(stat.S_ISREG(before.st_mode) and before.st_size<=2097152,'SOURCE_BOUND')
        raw=stream.read(2097153);after=os.fstat(stream.fileno())
    sig=lambda item:(item.st_dev,item.st_ino,item.st_size,item.st_mtime_ns,item.st_ctime_ns)
    need(sig(before)==sig(after)==sig(path.lstat()) and len(raw)==before.st_size,'SOURCE_CHANGED')
    return {'bytes':len(raw),'sha256':sha(raw)}

def source_proofs():
    result={}
    for key,(path,expected) in EXPECTED.items():
        try:result[key]=dict(fingerprint(path),matches_expected=False);result[key]['matches_expected']=result[key]['sha256']==expected
        except Exception as exc:result[key]={'matches_expected':False,'error_class':type(exc).__name__}
    return result

def stable_row(row):return {key:iso(row.get(key)) for key in ('thread_id','user_id','thread_status','run_count','active_count','unknown_status_count','ppt_history_count','stop_reason_count','nonempty_run_metadata_count','unknown_update_count','last_updated')}

def run_exclusions(row,now):
    if not row:return ['THREAD_OWNER_OR_RUN_STATE_UNAVAILABLE']
    reasons=[]
    if row.get('thread_status')!='idle':reasons.append('THREAD_NOT_IDLE_OR_UNKNOWN')
    if not row.get('run_count'):reasons.append('NO_RUN_HISTORY')
    if row.get('active_count'):reasons.append('PENDING_OR_RUNNING_RUN')
    if row.get('unknown_status_count'):reasons.append('UNKNOWN_RUN_STATUS')
    if row.get('ppt_history_count'):reasons.append('PPT_RUNNER_HISTORY_REQUIRES_REVIEW')
    if row.get('stop_reason_count'):reasons.append('WAIT_OR_OTHER_STOP_REASON_REQUIRES_REVIEW')
    if row.get('nonempty_run_metadata_count'):reasons.append('NONEMPTY_RUN_METADATA_REQUIRES_REVIEW')
    when=row.get('last_updated')
    if row.get('unknown_update_count') or not isinstance(when,datetime.datetime) or when.tzinfo is None:reasons.append('COMPLETION_TIME_UNKNOWN')
    elif (now-when).total_seconds()<1800:reasons.append('TERMINAL_AGE_UNDER_30_MINUTES_OR_FUTURE')
    return reasons

def safe_mounts(payload,resolver):
    base=Path(resolver.base_dir);need(base.is_absolute() and '..' not in base.parts,'BASE_PATH_SHAPE')
    for part in (base,)+tuple(base.parents):need(not part.is_symlink(),'BASE_PATH_LINK')
    mounts=payload.get('userdata_mounts');need(isinstance(mounts,list) and 0<len(mounts)<=8,'USERDATA_MOUNT_REQUIRED')
    candidates=[]
    for item in mounts:
        need(isinstance(item,dict) and set(item)=={'mount_path','claim','subpath'},'USERDATA_MOUNT_SCHEMA')
        path=PurePosixPath(item['mount_path']);sub=PurePosixPath(item['subpath'])
        need(path.is_absolute() and str(path)==item['mount_path'] and '..' not in path.parts and not sub.is_absolute() and '..' not in sub.parts,'USERDATA_MOUNT_PATH')
        if Path(path)==base or Path(path) in base.parents:candidates.append(item)
    need(bool(candidates),'BASE_NOT_ON_USERDATA_PVC')
    owning=max(candidates,key=lambda item:len(PurePosixPath(item['mount_path']).parts));mount=Path(owning['mount_path'])
    shadows=list(payload.get('shadow_mount_paths',[]))+[item['mount_path'] for item in mounts if item is not owning]
    for value in shadows:
        path=Path(value)
        if mount in path.parents and (path==base or path in base.parents or base in path.parents):raise Refused('USERDATA_SHADOW_MOUNT')
    prefix=PurePosixPath(owning['subpath'])/PurePosixPath(base.relative_to(mount).as_posix())
    return base,owning,prefix

def map_identity(pod,row,resolver,base,owning,prefix):
    owner=row.get('user_id') if row else None
    need(isinstance(owner,str) and re.fullmatch('[A-Za-z0-9_-]{1,64}',owner),'TRUSTED_OWNER_REQUIRED')
    sub=pod.get('userdata_subpath');need(isinstance(sub,str) and len(sub)<=1024,'USERDATA_SUBPATH_REQUIRED')
    match=re.fullmatch(r'(?:[A-Za-z0-9_.-]+/)*users/([A-Za-z0-9_-]{1,64})/threads/([A-Za-z0-9_-]{1,64})/user-data',sub)
    need(bool(match) and match.group(1)==owner and match.group(2)==row['thread_id'],'OWNER_THREAD_SUBPATH_MISMATCH')
    thread=row['thread_id'];sandbox_id=sha((owner+':'+thread).encode())[:8]
    need(pod['name']=='sandbox-'+sandbox_id,'DETERMINISTIC_POD_NAME_MISMATCH')
    need(pod.get('thread_annotation') in (None,'<none>',thread),'THREAD_ANNOTATION_MISMATCH')
    need(pod.get('user_annotation') in (None,'<none>',owner),'USER_ANNOTATION_MISMATCH')
    expected=prefix/'users'/owner/'threads'/thread/'user-data'
    need(sub==str(expected) and pod.get('userdata_mount_name')=='user-data' and pod.get('userdata_claim')==owning['claim'],'PVC_MOUNT_BINDING_MISMATCH')
    directory=Path(resolver.thread_dir(thread,user_id=owner));need(directory==base/'users'/owner/'threads'/thread,'THREAD_PATH_RESOLVER_MISMATCH')
    execution=base/'.sandbox-execution-leases'/(sha(sandbox_id.encode())+'.lock')
    creation=directory/(sandbox_id+'.lock')
    return execution,creation

def open_existing_lock(path):
    global LOCK_BASENAME
    path=Path(path);need(path.is_absolute() and '..' not in path.parts,'LOCK_SCOPE')
    parent=os.open('/',os.O_RDONLY|os.O_DIRECTORY);fd=None
    try:
        for part in path.parts[1:-1]:
            nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent);os.close(parent);parent=nxt
        LOCK_BASENAME=path.name
        try:fd=os.open(path.name,os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC,dir_fd=parent)
        finally:LOCK_BASENAME=None
        value=os.fstat(fd);current=os.stat(path.name,dir_fd=parent,follow_symlinks=False)
        if not stat.S_ISREG(value.st_mode) or value.st_nlink!=1 or value.st_size>4096 or (value.st_dev,value.st_ino)!=(current.st_dev,current.st_ino):
            raise Refused('LOCK_TYPE_OR_INODE_UNKNOWN')
        return fd,parent,path.name,(value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns)
    except BaseException:
        if fd is not None:os.close(fd)
        os.close(parent);raise

def lock_probe(execution,creation):
    need(fcntl is not None,'POSIX_FLOCK_REQUIRED')
    handles=[];locked=[];started=time.monotonic();held_since=None
    result={'execution_lock':'not_tested','creation_lock':'not_tested','short_exclusive_probe_may_briefly_contend':True,'file_contents_written':False}
    current='execution'
    try:
        for current,path in [('execution',execution),('creation',creation)]:handles.append(open_existing_lock(path))
        for current,handle in zip(('execution','creation'),handles):
            fcntl.flock(handle[0],fcntl.LOCK_EX|fcntl.LOCK_NB);locked.append(handle[0]);result[current+'_lock']='available_at_probe'
            if held_since is None:held_since=time.monotonic()
        result['both_locks_available']=True
    except FileNotFoundError:result.update(both_locks_available=False,exclusion_reason='EXISTING_'+current.upper()+'_LOCK_MISSING')
    except BlockingIOError:result.update(both_locks_available=False,exclusion_reason=current.upper()+'_LOCK_IN_USE')
    except Exception as exc:result.update(both_locks_available=False,exclusion_reason='LOCK_INSPECTION_UNKNOWN',error_class=type(exc).__name__)
    finally:
        for fd in reversed(locked):
            try:fcntl.flock(fd,fcntl.LOCK_UN)
            except OSError:pass
        result['exclusive_hold_ms']=round((time.monotonic()-held_since)*1000,3) if held_since is not None else 0
        for fd,parent,name,signature in reversed(handles):
            try:
                value=os.stat(name,dir_fd=parent,follow_symlinks=False)
                if signature!=(value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns):result.update(both_locks_available=False,exclusion_reason='LOCK_CHANGED_DURING_PROBE')
            except Exception as exc:result.update(both_locks_available=False,exclusion_reason='LOCK_READBACK_UNKNOWN',error_class=type(exc).__name__)
            finally:os.close(fd);os.close(parent)
    result['probe_elapsed_ms']=round((time.monotonic()-started)*1000,3)
    return result

def prepare_inventory(payload):
    pods=payload.get('pods');need(isinstance(pods,list) and len(pods)<=256,'POD_INVENTORY_BOUND');threads={};preliminary=[]
    for pod in pods:
        need(isinstance(pod,dict) and re.fullmatch('sandbox-[a-z0-9-]+',pod.get('name','')) and re.fullmatch('[0-9a-f-]{36}',pod.get('uid','')),'POD_IDENTITY_SHAPE')
        need(all(isinstance(pod.get(key),str) and len(pod[key])<=1024 and all(item=='<none>' or re.fullmatch(r'[0-9][0-9.eE+-]*(?:[A-Za-z]{1,3})?',item) for item in pod[key].split(',')) for key in ('cpu_requests','memory_requests')),'RESOURCE_COLUMN_SHAPE')
        result={key:pod.get(key) for key in ('uid','node','phase','cpu_requests','memory_requests')};result['pod']=pod['name'];result['exclusion_reasons']=[]
        if pod.get('node') not in POOL:result['exclusion_reasons'].append('NOT_BOUND_TO_CURRENT_POOL')
        if pod.get('phase')!='Running':result['exclusion_reasons'].append('POD_NOT_RUNNING')
        if pod.get('deleting'):result['exclusion_reasons'].append('POD_DELETING')
        if not pod.get('sandbox_ready') or not pod.get('pod_ready'):result['exclusion_reasons'].append('POD_NOT_FULLY_READY')
        match=re.search(r'/threads/([A-Za-z0-9_-]{1,64})/user-data$',pod.get('userdata_subpath') or '')
        if not match:result['exclusion_reasons'].append('THREAD_BINDING_UNAVAILABLE')
        else:threads[pod['uid']]=match.group(1)
        preliminary.append((pod,result))
    need(len({pod['name'] for pod,_ in preliminary})==len(preliminary) and len({pod['uid'] for pod,_ in preliminary})==len(preliminary),'DUPLICATE_POD_IDENTITY')
    return preliminary,threads

async def read_state(conn,threads):
    async with conn.transaction(readonly=True,isolation='repeatable_read'):
        need(await conn.fetchval('SHOW transaction_read_only')=='on','DATABASE_READONLY_REQUIRED')
        for sql in ("SET LOCAL statement_timeout='5000ms'","SET LOCAL lock_timeout='500ms'","SET LOCAL idle_in_transaction_session_timeout='10000ms'"):await conn.execute(sql)
        now=await conn.fetchval('SELECT now()');need(isinstance(now,datetime.datetime) and now.tzinfo is not None,'DATABASE_TIME_REQUIRED')
        records=await conn.fetch(RUN_SQL,sorted(set(threads)));need(len(records)<=256,'RUN_AGGREGATE_BOUND')
        return now,{row['thread_id']:dict(row) for row in records}

async def observe(payload):
    global DB_HOST,DB_PORT,DB_ADDRESSES,ALLOW_DB_DNS
    out={'schema':'sandbox-reclaim-candidate-plan.v1','status':'INCOMPLETE','plan_only':True,'no_deletion_authorization':True,
        'unleased_nonrun_activity_not_excluded':True,'ppt_live_worker_source_coverage_not_proven':True,'database_readonly':True,'database_modified':False,
        'files_or_lock_contents_modified':False,'short_exclusive_lock_probes_performed':False,'short_lock_probe_is_not_strictly_without_runtime_effect':True,
        'credentials_exported':False,'database_credentials_used':False,'application_configuration_read_may_resolve_configured_credentials':True,
        'provider_instantiated':False,'pods_deleted':0,'provider_requests':0,'thread_or_user_identity_exported':False,'pod_results':[]}
    conn=None;stage='inventory'
    try:
        preliminary,thread_by_uid=prepare_inventory(payload);out['sources']=source_proofs();sources_ok=all(row['matches_expected'] for row in out['sources'].values())
        stage='load_database_config';pg,get_config,paths=runtime_modules();cfg=get_config().database;need(cfg.backend=='postgres','POSTGRES_REQUIRED')
        url=cfg.postgres_url.replace('postgresql+asyncpg://','postgresql://',1);parsed=urlsplit(url);need(parsed.scheme in ('postgres','postgresql') and parsed.hostname,'DATABASE_ADDRESS_REQUIRED')
        DB_HOST,DB_PORT=parsed.hostname,parsed.port or 5432;ALLOW_DB_DNS=True
        addresses=socket.getaddrinfo(DB_HOST,DB_PORT,type=socket.SOCK_STREAM);need(0<len(addresses)<=16,'DATABASE_DNS_BOUND');DB_ADDRESSES={row[4][0] for row in addresses}
        out['database_credentials_used']=True;conn=await pg.connect(url,timeout=5,command_timeout=5,server_settings={'default_transaction_read_only':'on','statement_timeout':'5000'})
        stage='fixed_scheduled_run_diagnostic';out['fixed_run_diagnostic']=await diagnose_fixed_run(conn)
        stage='trusted_storage_mapping';resolver=paths.get_paths();base,owning,prefix=safe_mounts(payload,resolver)
        stage='runs_full_thread_index_metadata';indexes=await conn.fetch(INDEX_SQL);need(len(indexes)<=2,'INDEX_METADATA_BOUND')
        index_ok=any(row['indexname'] in ('ix_runs_thread_id','ix_runs_thread_status') and ' WHERE ' not in row['indexdef'].upper() and re.search(r'\(\s*"?thread_id"?\s*(?:,|\))',row['indexdef']) for row in indexes)
        out['runs_thread_lookup_index_confirmed']=index_ok
        need(index_ok,'FULL_THREAD_RUN_INDEX_NOT_CONFIRMED')
        stage='first_run_snapshot';now,before=await read_state(conn,thread_by_uid.values())
        for pod,result in preliminary:
            row=before.get(thread_by_uid.get(pod['uid']))
            if not sources_ok:result['exclusion_reasons'].append('LIVE_SOURCE_PROVENANCE_NOT_CONFIRMED')
            if not index_ok:result['exclusion_reasons'].append('RUN_LOOKUP_INDEX_NOT_CONFIRMED')
            result['exclusion_reasons'].extend(run_exclusions(row,now))
            if not result['exclusion_reasons']:
                try:
                    execution,creation=map_identity(pod,row,resolver,base,owning,prefix)
                    result['lock_probe']=lock_probe(execution,creation);out['short_exclusive_lock_probes_performed']=True
                    if not result['lock_probe']['both_locks_available']:result['exclusion_reasons'].append(result['lock_probe']['exclusion_reason'])
                except Exception as exc:result['exclusion_reasons'].append(str(exc) if isinstance(exc,Refused) else 'OWNER_OR_PATH_BINDING_UNKNOWN')
            out['pod_results'].append(result)
        stage='second_run_snapshot';_,after=await read_state(conn,thread_by_uid.values())
        for pod,result in preliminary:
            thread=thread_by_uid.get(pod['uid'])
            if stable_row(before.get(thread,{}))!=stable_row(after.get(thread,{})):result['exclusion_reasons'].append('DATABASE_STATE_CHANGED_DURING_PLAN')
            result['eligibility']='EXCLUDED' if result['exclusion_reasons'] else 'NO_ACTIVE_RUN_AND_LOCKS_AVAILABLE_REQUIRES_REVIEW'
        need(out['sources']==source_proofs(),'LIVE_SOURCES_CHANGED_DURING_PLAN')
        out['review_candidates']=[row for row in out['pod_results'] if not row['exclusion_reasons']]
        out.update(status='SANDBOX_RECLAIM_REVIEW_PLAN_OBSERVED_NO_CHANGE',database_snapshot_time=now.isoformat(),candidate_count=len(out['review_candidates']))
    except Exception as exc:out.update(last_step=stage,error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'PLAN_INSPECTION_FAILURE_REDACTED')
    finally:
        if conn is not None:
            try:await conn.close(timeout=2)
            except Exception as exc:out['database_close_error_class']=type(exc).__name__
        ALLOW_DB_DNS=False;DB_ADDRESSES=set()
    return out

class Discard:
    def write(self,value):return len(value)
    def flush(self):pass

def main():
    sys.dont_write_bytecode=True;logging.disable(logging.CRITICAL)
    out={'schema':'sandbox-reclaim-candidate-plan.v1','status':'INCOMPLETE','plan_only':True,'no_deletion_authorization':True,'credentials_exported':False,'pods_deleted':0}
    try:
        signal.signal(signal.SIGALRM,lambda *args:(_ for _ in ()).throw(Deadline()));signal.alarm(55)
        sys.addaudithook(audit);raw=sys.stdin.buffer.read(524289);need(len(raw)<=524288,'INPUT_BOUND');payload=json.loads(raw)
        need(isinstance(payload,dict) and set(payload)=={'pods','userdata_mounts','shadow_mount_paths'},'PLAN_PAYLOAD_SCHEMA')
        with contextlib.redirect_stdout(Discard()),contextlib.redirect_stderr(Discard()):out=asyncio.run(observe(payload))
    except Deadline:out.update(error_class='Deadline',error_code='CHILD_PLAN_DEADLINE')
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'PLAN_CHILD_FAILURE_REDACTED')
    finally:signal.alarm(0)
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')

if __name__=='__main__':sys.exit(main())

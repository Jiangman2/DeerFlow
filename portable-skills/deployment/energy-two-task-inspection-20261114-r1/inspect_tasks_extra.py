"""Two operator-named tasks: bounded durable evidence, no task execution."""
THREADS = ('1a749b15-a6dc-4fa6-99a3-7a3b2ed195c4', '7f645bba-9bfd-4786-ab0a-e8636cf255d3')
DB_HOST = None
DB_PORT = None
DB_ADDRESSES = set()
ALLOW_DB_DNS = False
TOOLS = {'bash','read_file','write_file','edit_file','view_image','present_files','web_search','web_fetch','tool_search','image_search','task'}
ACTIONS = ('doctor','prepare','select','import-news','build','accept','final-text','_acquire')
KINDS = ['llm.ai.response','llm.tool.result','run.error','llm.error','run.status']
RUN_SQL = """SELECT run_id,status,created_at,updated_at,
left(to_jsonb(r)->>'model_name',128) AS model_name,left(to_jsonb(r)->>'stop_reason',96) AS stop_reason,
left(to_jsonb(r)->>'message_count',24) AS message_count,left(to_jsonb(r)->>'total_input_tokens',24) AS total_input_tokens,
left(to_jsonb(r)->>'total_output_tokens',24) AS total_output_tokens,left(to_jsonb(r)->>'total_tokens',24) AS total_tokens,
left(to_jsonb(r)->>'llm_call_count',24) AS llm_call_count,left(to_jsonb(r)->>'lease_expires_at',64) AS lease_expires_at,
md5(to_jsonb(r)->>'owner_worker_id') AS owner_worker_hash,
substring(to_jsonb(r)->>'error' from 1 for 4096) AS error_head,right(to_jsonb(r)->>'error',4096) AS error_tail,
CASE WHEN octet_length(kwargs_json::jsonb #>> '{config,recursion_limit}')<=20
THEN kwargs_json::jsonb #>> '{config,recursion_limit}' ELSE NULL END AS recursion_limit
FROM runs r WHERE thread_id=$1 ORDER BY created_at DESC LIMIT 2"""
SCHEDULE_RUN_SQL = """SELECT id,task_id,run_id,status,scheduled_for,created_at,
left(to_jsonb(r)->>'trigger',32) AS trigger,left(to_jsonb(r)->>'started_at',64) AS started_at,
left(to_jsonb(r)->>'finished_at',64) AS finished_at,left(to_jsonb(r)->>'lease_expires_at',64) AS lease_expires_at,
left(to_jsonb(r)->>'attempt_count',24) AS attempt_count,md5(to_jsonb(r)->>'lease_owner') AS lease_owner_hash,
substring(to_jsonb(r)->>'error' from 1 for 4096) AS error_head,right(to_jsonb(r)->>'error',4096) AS error_tail
FROM scheduled_task_runs r WHERE thread_id=$1 ORDER BY created_at DESC LIMIT 4"""
SCHEDULE_SQL = """SELECT id,status,created_at,updated_at,
left(to_jsonb(r)->>'context_mode',64) AS context_mode,left(to_jsonb(r)->>'schedule_type',32) AS schedule_type,
left(to_jsonb(r)->>'timezone',64) AS timezone,left(to_jsonb(r)->>'overlap_policy',32) AS overlap_policy,
left(to_jsonb(r)->>'next_run_at',64) AS next_run_at,left(to_jsonb(r)->>'last_run_at',64) AS last_run_at,
left(to_jsonb(r)->>'last_run_id',64) AS last_run_id,left(to_jsonb(r)->>'last_thread_id',64) AS last_thread_id,
left(to_jsonb(r)->>'lease_expires_at',64) AS lease_expires_at,left(to_jsonb(r)->>'run_count',24) AS run_count,
substring(to_jsonb(r)->>'last_error' from 1 for 4096) AS error_head,right(to_jsonb(r)->>'last_error',4096) AS error_tail
FROM scheduled_tasks r WHERE id=$1 LIMIT 1"""
COUNT_SQL = """SELECT event_type,count(*) AS count,min(seq) AS first_seq,max(seq) AS last_seq,
min(created_at) AS first_at,max(created_at) AS last_at FROM (
SELECT seq,event_type,created_at FROM run_events WHERE thread_id=$1 AND run_id=$2 ORDER BY seq LIMIT 10001
) AS bounded GROUP BY event_type ORDER BY event_type LIMIT 64"""
EVENT_SQL = """WITH first_events AS (
SELECT seq FROM run_events WHERE thread_id=$1 AND run_id=$2 AND event_type=ANY($3::text[]) ORDER BY seq LIMIT 400),
last_events AS (SELECT seq FROM run_events WHERE thread_id=$1 AND run_id=$2 AND event_type=ANY($3::text[]) ORDER BY seq DESC LIMIT 400),
first_errors AS (SELECT seq FROM run_events WHERE thread_id=$1 AND run_id=$2 AND event_type IN ('run.error','llm.error') ORDER BY seq LIMIT 8),
selected AS (SELECT seq,event_type,created_at,octet_length(content) AS bytes FROM run_events
WHERE thread_id=$1 AND run_id=$2 AND seq IN (SELECT seq FROM first_events UNION SELECT seq FROM last_events UNION SELECT seq FROM first_errors)),
budget AS (SELECT *,sum(least(bytes,16384)) OVER (ORDER BY seq DESC) AS cumulative_bytes FROM selected)
SELECT b.seq,b.event_type,b.created_at,b.bytes,
CASE WHEN b.bytes<=16384 AND b.cumulative_bytes<=8388608 THEN e.content ELSE NULL END AS content,
CASE WHEN b.bytes>16384 OR b.cumulative_bytes>8388608 THEN encode(substring(convert_to(e.content,'UTF8') FROM 1 FOR 2048),'base64') ELSE NULL END AS head_b64,
CASE WHEN b.bytes>16384 OR b.cumulative_bytes>8388608 THEN encode(substring(convert_to(e.content,'UTF8') FROM greatest(1,b.bytes-2047) FOR 2048),'base64') ELSE NULL END AS tail_b64
FROM budget b JOIN run_events e ON e.thread_id=$1 AND e.run_id=$2 AND e.seq=b.seq ORDER BY b.seq LIMIT 808"""
CHECKPOINT_SQL = """SELECT checkpoint_id,parent_checkpoint_id,
CASE WHEN octet_length((metadata->>'step'))<=24 THEN metadata->>'step' ELSE NULL END AS step,
CASE WHEN octet_length((metadata->>'source'))<=64 THEN metadata->>'source' ELSE NULL END AS source,
CASE WHEN octet_length((checkpoint->>'ts'))<=64 THEN checkpoint->>'ts' ELSE NULL END AS timestamp,
CASE WHEN octet_length((checkpoint->'channel_versions'->>'skill_workflow'))<=128 THEN checkpoint->'channel_versions'->>'skill_workflow' ELSE NULL END AS workflow_version,
CASE WHEN octet_length((checkpoint->'channel_values'->'skill_workflow')::text)<=131072 THEN (checkpoint->'channel_values'->'skill_workflow')::text ELSE NULL END AS inline_workflow
FROM checkpoints WHERE thread_id=$1 AND checkpoint_ns='' ORDER BY checkpoint_id DESC LIMIT 1"""
BLOB_SQL = """SELECT type,octet_length(blob) AS bytes,
CASE WHEN octet_length(blob)<=131072 THEN blob ELSE NULL END AS bounded_blob
FROM checkpoint_blobs WHERE thread_id=$1 AND checkpoint_ns='' AND channel='skill_workflow' AND version=$2 LIMIT 1"""

def safe_word(value, upper=False):
    pattern = r'[A-Z][A-Z0-9_]{1,95}' if upper else r'[A-Za-z][A-Za-z0-9_.:/-]{0,95}'
    if isinstance(value,str) and re.fullmatch(pattern,value) and not any(x in value.lower() for x in ('password','webhook','secret','token=','http:','https:')):
        return value
    return None

def flags(body):
    body=text_content(body); low=body.casefold()
    tests={'recursion_limit':('recursion limit','recursion_limit','recursion_capped'),
        'loop_guard':('loop_capped','连续 3 次','no progress','未遵循当前恢复契约','重复调用'),
        'token_budget':('token_capped','token budget','token_budget'),
        'timeout':('timed out','timeout','time limit','超时'),
        'cancelled':('cancelled','canceled','取消','interrupted'),
        'sandbox_capacity':('failedscheduling','insufficient memory','insufficient cpu','sandbox startup'),
        'lease_or_owner':('lease','orphan','owner_worker','ownership lost'),
        'provider_or_output_parse':('outputparser','output parser','invalid json','provider request','rate limit','max tokens','finish_reason'),
        'authority_reload':('authority','重新读取','恢复契约'),
        'browser_runtime':('browser_runtime','no_usable_browser','targetclosederror'),
        'permissions':('permissionerror','permission denied'),
        'missing_file':('filenotfounderror','no such file'),
        'visual_review_required':('visual_review_required','需视觉','视觉验收'),
        'current_url_missing':('host_conversation','当前平台未返回可用的会话链接','当前没有已确认的完整日报链接'),
        'publication_unconfirmed':('平台发布尚未确认','附件发布状态尚未确认'),
        'ocr':('tesseract','pytesseract','easyocr','paddleocr','ocr','文字识别'),
        'data_incomplete':('数据不完整','target_quotes_missing','未取得目标日'),
        'fallback_message':('deerflow_error_fallback','deerflow_waiting_for_user')}
    return [key for key,terms in tests.items() if any(term in low for term in terms)]

def errors(value):
    body=text_content(value)
    result={'flags':flags(body),'exception_classes':sorted(set(re.findall(r'\b(?:[A-Za-z][A-Za-z0-9_]{0,48}(?:Error|Exception)|CancelledError|Refused)\b',body)))[:12],
        'error_codes':[],'statuses':[],'exit_codes':[],'business_codes':[],'raw_text_exported':False}
    for obj in objects(value):
        for key,dest,upper in (('error_code','error_codes',True),('status','statuses',False)):
            word=safe_word(obj.get(key),upper)
            if word and word not in result[dest]:result[dest].append(word)
        for key in ('exitcode','exit_code','returncode'):
            if type(obj.get(key)) is int and -255<=obj[key]<=255:result['exit_codes'].append(obj[key])
        for key in ('code','errcode','StatusCode'):
            if type(obj.get(key)) is int and -2147483648<=obj[key]<=2147483647:result['business_codes'].append(obj[key])
    result['exit_codes']=sorted(set(result['exit_codes']+[int(x) for x in re.findall(r'(?:exit_code|exitcode|exit code|Process exited with code)["\s:=]+(-?\d{1,3})',body,re.I)]))[:8]
    for key in ('error_codes','statuses','business_codes'):result[key]=list(dict.fromkeys(result[key]))[:12]
    return result

def command(call):
    args=call.get('args') if isinstance(call.get('args'),dict) else {}
    name=call.get('name') if call.get('name') in TOOLS else 'OTHER'
    raw=next((args[k] for k in ('command','cmd','bash_command') if isinstance(args.get(k),str)),'')
    path=next((args[k] for k in ('path','file_path','file') if isinstance(args.get(k),str)),'')
    actions=[action for action in ACTIONS if re.search(r'daily\.py["\x27]?\s+'+re.escape(action)+r'(?:\s|$)',raw)]
    categories=[]
    if name=='view_image':categories.append('VIEW_IMAGE')
    if name=='present_files':categories.append('PRESENT_FILES')
    if any(x in raw.casefold() for x in ('tesseract','easyocr','paddleocr','pytesseract','ocr')):categories.append('OCR_CODE_OR_COMMAND')
    if re.search(r'(?i)\b(?:pip|uv)\b.*\binstall\b|playwright\s+install',raw):categories.append('DEPENDENCY_INSTALL_PROPOSAL')
    if re.search(r'(?:requests|httpx)\s*\.\s*post\s*\(|\bcurl\b|urllib\.request|http\.client|\.request\(["\x27]POST',raw):categories.append('HTTP_SEND_CANDIDATE_NOT_PROVEN')
    if 'Image.open' in raw or 'PIL' in raw:categories.append('IMAGE_PROCESSING_CODE')
    if any(x in raw for x in ('browser_runtime','chromium','playwright')):categories.append('BROWSER_CODE_OR_COMMAND')
    if '/SKILL.md' in path or path.endswith('SKILL.md'):categories.append('SKILL_AUTHORITY_READ')
    if path.endswith(('.png','.jpg','.jpeg')):categories.append('IMAGE_PATH')
    if name in ('read_file','write_file','edit_file'):
        categories.append('PROJECT_FILE' if '/mnt/user-data/' in path else 'SKILL_FILE' if '/mnt/skills/' in path else 'OTHER_FILE')
    return {'tool':name,'call':sha(str(call.get('id','')))[:16], 'command_sha256':sha(raw) if raw else None,
        'path_sha256':sha(path) if path else None,'actions':actions,'categories':categories,'command_flags':flags(raw),
        'semantic_fingerprint':sha(json.dumps([name,actions,categories,path],sort_keys=True))[:16]}

def extra_ai(value):
    if not isinstance(value,dict):return {}
    result={}
    for key in ('deerflow_error_fallback','deerflow_waiting_for_user'):
        if type(value.get(key)) is bool:result[key]=value[key]
    for key in ('error_reason','waiting_reason','stop_reason'):
        if key in value:result[key]={'classification':errors(value[key]),'sha256':sha(text_content(value[key]))}
    return result

def seconds(first,last):
    try:return round((last-first).total_seconds(),3)
    except (TypeError,AttributeError):return None

def event_summary(records,thread):
    need(len(records)<=808,'EVENT_COUNT_BOUND');timeline=[];calls={};repeat={};categories={};error_rows=[];partial=0;bytes_read=0;previous=None;final_ai=[]
    for row in records:
        body=row.get('content');full=isinstance(body,str)
        if full:need(len(body.encode())<=16384,'EVENT_ROW_BOUND')
        else:
            partial+=1;parts=[]
            for key in ('head_b64','tail_b64'):
                if row.get(key):
                    data=base64.b64decode(row[key]);need(len(data)<=2048,'EVENT_FRAGMENT_BOUND');parts.append(data.decode('utf-8','ignore'))
            body='\n<OMITTED>\n'.join(parts)
        bytes_read+=len(body.encode());need(bytes_read<=12582912,'EVENT_TOTAL_BOUND')
        entry={'seq':row['seq'],'kind':row['event_type'],'at':iso(row['created_at']),'gap_seconds':seconds(previous,row['created_at']) if previous else None,'partial':not full}
        previous=row['created_at'];obj=None
        if full:
            try:obj=json.loads(body)
            except (ValueError,RecursionError):pass
        if isinstance(obj,dict) and row['event_type']=='llm.ai.response':
            rawcalls=obj.get('tool_calls') if isinstance(obj.get('tool_calls'),list) else []
            entry['calls']=[command(call) for call in rawcalls[:24] if isinstance(call,dict)]
            for c in entry['calls']:
                calls[c['call']]=(row['created_at'],c)
                key=c['command_sha256'] or c['path_sha256']
                if key:repeat.setdefault(key,[]).append(row['seq'])
                for kind in c['categories']+c['actions']:categories[kind]=categories.get(kind,0)+1
            if not rawcalls:
                content=text_content(obj.get('content'));entry.update(final_ai_candidate=True,text_flags=flags(content),current_thread_url_present=('https://cnoocclaw.cnooc.com.cn/workspace/chats/'+thread) in content,host_flags=extra_ai(obj.get('additional_kwargs')))
                final_ai.append(dict(entry))
        elif isinstance(obj,dict) and row['event_type']=='llm.tool.result':
            ident=sha(str(obj.get('tool_call_id','')))[:16];owner=calls.get(ident)
            entry.update(tool=obj.get('name') if obj.get('name') in TOOLS else 'OTHER',call=ident,tool_status=obj.get('status') if obj.get('status') in ('success','error') else None,
                receipt=errors(obj.get('content')),owner_seen=owner is not None,tool_seconds=seconds(owner[0],row['created_at']) if owner else None,
                actions=owner[1]['actions'] if owner else [],categories=owner[1]['categories'] if owner else [])
            if entry['tool']=='present_files':
                artifact=obj.get('artifact') if isinstance(obj.get('artifact'),dict) else {}
                entry['publication_status']=safe_word(artifact.get('publication_status'))
                entry['current_thread_url_present']=('https://cnoocclaw.cnooc.com.cn/workspace/chats/'+thread) in text_content(obj.get('content'))
            bad=entry['tool_status']=='error' or any(x!=0 for x in entry['receipt']['exit_codes']) or bool(entry['receipt']['error_codes'])
            if bad:error_rows.append(dict(entry))
        else:
            entry['classification']=errors(obj if obj is not None else body)
            if row['event_type'] in ('run.error','llm.error'):error_rows.append(dict(entry))
            if not full:
                # Fragments only support positive lexical observations, never absence or execution proof.
                entry['positive_fragment_actions']=[x for x in ACTIONS if re.search(r'daily\.py.{0,8}'+re.escape(x)+r'\b',body)]
                entry['fragment_is_not_full_message']=True
        timeline.append(entry)
    return {'timeline':timeline,'selected_event_count':len(records),'observed_event_content_bytes':bytes_read,'partial_events':partial,
        'selection':'FIRST400_LAST400_AND_FIRST8_EXPLICIT_ERRORS','content_coverage_complete':partial==0 and len(records)<800,
        'middle_omission_possible':len(records)>=800,'proposed_action_counts':categories,'proposal_counts_are_not_execution_proof':True,
        'first_observed_error':error_rows[0] if error_rows else None,'last_observed_error':error_rows[-1] if error_rows else None,
        'last_observed_action':next((r for r in reversed(timeline) if r.get('tool') or r.get('calls')),None),
        'last_ai_candidates':final_ai[-3:],'repeated_exact_command_or_path':[{'sha256':k,'count':len(v),'first_seqs':v[:8],'last_seqs':v[-8:]} for k,v in repeat.items() if len(v)>1][:64]}

def workflow(value):
    if not isinstance(value,dict):return {'status':'NO_PLAIN_WORKFLOW_OBJECT'}
    result={'status':'WORKFLOW_STRUCTURAL_SUMMARY','raw_task_text_or_paths_exported':False}
    for key in ('skill_name','status','current_stage','gate_status','gate_owner_tool','gate_owner_stage','confirmation_surface'):
        result[key]=safe_word(value.get(key))
    for key in ('skill_sha256','loaded_authority_context_fingerprint'):
        v=value.get(key);result[key]=v if isinstance(v,str) and re.fullmatch('[a-f0-9]{64}',v) else None
    for key in ('failed_gate_repeat_count','guard_reengagement_attempts','reengagement_attempts'):
        if type(value.get(key)) is int:result[key]=value[key]
    for key in ('confirmation_ui_active','confirmation_wait_timed_out','confirmation_surface_switch_allowed','hard_stop'):
        if type(value.get(key)) is bool:result[key]=value[key]
    for key in ('resume_documents','loaded_authority_documents','managed_artifacts','delivered_artifacts'):
        result[key+'_count']=len(value[key]) if isinstance(value.get(key),list) else None
    for key in ('gate_detail','guard_detail'):result[key]=errors(value.get(key))
    owner=value.get('gate_owner_receipt_id');mutation=value.get('last_project_mutation_receipt_id')
    result['gate_owner_receipt_sha256']=sha(owner) if isinstance(owner,str) else None
    result['last_project_mutation_receipt_sha256']=sha(mutation) if isinstance(mutation,str) else None
    result['gate_owner_matches_latest_mutation']=owner==mutation if owner and mutation else None
    for key in ('gate_owner_action','resume_pointer'):
        result[key]=command({'name':'bash','args':{'command':value.get(key,'')}})
    evidence=value.get('evidence');result['evidence_count']=len(evidence) if isinstance(evidence,list) else None
    result['recent_evidence']=[{'tool':safe_word(x.get('tool')),'status':safe_word(x.get('status')),'action':command({'name':'bash','args':{'command':x.get('action','')}}),'detail':errors(x.get('detail'))} for x in evidence[-8:] if isinstance(x,dict)] if isinstance(evidence,list) else []
    return result

async def checkpoint(conn,thread):
    out={'status':'INCOMPLETE','application_serializer_used':False,'pickle_or_dynamic_object_loading':False}
    try:
        async with conn.transaction(readonly=True):
            row=await conn.fetchrow(CHECKPOINT_SQL,thread)
            if not row:return {'status':'CHECKPOINT_NOT_PRESENT'}
            out.update(checkpoint_id_sha256=sha(row['checkpoint_id']),parent_id_sha256=sha(row['parent_checkpoint_id']) if row.get('parent_checkpoint_id') else None,
                step=int(row['step']) if isinstance(row.get('step'),str) and re.fullmatch(r'-?\d{1,12}',row['step']) else None,
                timestamp=row.get('timestamp'),source=safe_word(row.get('source')))
            obj=None
            if row.get('inline_workflow'):
                obj=json.loads(row['inline_workflow']);out['encoding']='inline_json'
            elif row.get('workflow_version'):
                blob=await conn.fetchrow(BLOB_SQL,thread,row['workflow_version'])
                if blob:
                    out.update(blob_bytes=blob['bytes'],encoding=safe_word(blob['type']))
                    raw=blob.get('bounded_blob')
                    if raw is not None:
                        need(len(raw)<=131072,'WORKFLOW_BLOB_BOUND')
                        if blob['type']=='json':obj=json.loads(bytes(raw))
                        elif blob['type']=='msgpack':
                            import ormsgpack
                            # Primitive decoder only; no LangGraph object hook / import / constructor.
                            obj=ormsgpack.unpackb(bytes(raw))
                        else:out['unsupported_encoding']=True
                    else:out['blob_skipped_by_size_bound']=True
            out.update(workflow=workflow(obj),status='CHECKPOINT_OBSERVED_READONLY')
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'CHECKPOINT_DECODE_OR_SCHEMA_UNAVAILABLE')
    return out

def metadata(row):
    result={}
    for key,value in dict(row).items():
        if key in ('error_head','error_tail'):continue
        if key in ('run_id','id','task_id','last_run_id','last_thread_id'):result[key+'_sha256']=sha(value) if isinstance(value,str) else None
        elif key in ('status','trigger','stop_reason','context_mode','schedule_type','timezone','overlap_policy','model_name'):result[key]=safe_word(value)
        elif key in ('recursion_limit','message_count','total_input_tokens','total_output_tokens','total_tokens','llm_call_count','attempt_count','run_count'):
            result[key]=int(value) if isinstance(value,str) and re.fullmatch(r'\d{1,20}',value) else value if type(value) is int else None
        elif value is None or isinstance(value,(int,bool)):result[key]=value
        elif key.endswith('_at') or key=='scheduled_for':result[key]=iso(value)
        elif key.endswith('_hash'):result[key]=value if isinstance(value,str) and re.fullmatch('[a-f0-9]{32}',value) else None
    result['error']=errors((row.get('error_head') or '')+'\n'+(row.get('error_tail') or ''))
    result['model_name_present']=row.get('model_name') is not None if 'model_name' in row else None
    if row.get('model_name') and not result.get('model_name'):result['model_name_sha256']=sha(str(row['model_name']))
    result['duration_between_created_updated_seconds']=seconds(row.get('created_at'),row.get('updated_at'))
    return result

async def diagnose_thread(conn,thread):
    out={'thread_id':thread,'status':'INCOMPLETE'}
    async with conn.transaction(readonly=True,isolation='repeatable_read'):
        need(await conn.fetchval('SHOW transaction_read_only')=='on','DATABASE_READONLY_REQUIRED')
        for sql in ("SET LOCAL statement_timeout='5000ms'","SET LOCAL lock_timeout='500ms'","SET LOCAL idle_in_transaction_session_timeout='20000ms'"):
            await conn.execute(sql)
        rows=await conn.fetch(RUN_SQL,thread);out['runs']=[metadata(r) for r in rows]
        try:
            async with conn.transaction(readonly=True):
                schedules=await conn.fetch(SCHEDULE_RUN_SQL,thread)
                out['schedule_occurrences']=[metadata(r) for r in schedules]
                out['source']='SCHEDULED_OCCURRENCE_PRESENT' if schedules else 'NO_SCHEDULE_OCCURRENCE_FOR_THREAD'
                out['source_not_guessed_from_link_order']=True
                out['schedule_tasks']=[]
                for task_id in list(dict.fromkeys(r['task_id'] for r in schedules))[:4]:
                    need(isinstance(task_id,str) and re.fullmatch('[A-Za-z0-9_-]{1,64}',task_id),'DERIVED_TASK_ID_SHAPE')
                    task=await conn.fetchrow(SCHEDULE_SQL,task_id)
                    if task:out['schedule_tasks'].append(metadata(task))
        except Exception as exc:out['schedule_lookup']={'status':'UNAVAILABLE','error_class':type(exc).__name__};out['source']='UNKNOWN'
        out['checkpoint']=await checkpoint(conn,thread)
        if rows:
            run=rows[0]['run_id'];need(isinstance(run,str) and re.fullmatch('[A-Za-z0-9_-]{1,64}',run),'RUN_ID_SHAPE')
            out['latest_run_id_sha256']=sha(run)
            counts=await conn.fetch(COUNT_SQL,thread,run)
            out['event_counts']=[{'kind':safe_word(r['event_type']),'count':r['count'],'first_seq':r['first_seq'],'last_seq':r['last_seq'],'first_at':iso(r['first_at']),'last_at':iso(r['last_at'])} for r in counts]
            out['count_query_truncated_at_10001']=sum(r['count'] for r in counts)>=10001
            events=await conn.fetch(EVENT_SQL,thread,run,KINDS)
            out['latest_run_events']=event_summary(events,thread)
            out['latest_run_events']['all_selected_kind_events_observed']=sum(r['count'] for r in counts if r['event_type'] in KINDS)==len(events) and not out['count_query_truncated_at_10001']
        out['status']='TASK_DURABLE_STATE_OBSERVED_READONLY'
    return out

def fixed_source_hashes():
    result={}
    names=('packages/harness/deerflow/sandbox/tools.py','packages/harness/deerflow/tools/builtins/present_file_tool.py','packages/harness/deerflow/utils/conversation_url.py',
        'app/scheduler/service.py','packages/harness/deerflow/runtime/runs/worker.py','packages/harness/deerflow/runtime/runs/manager.py',
        'packages/harness/deerflow/agents/middlewares/skill_workflow_middleware.py','packages/harness/deerflow/agents/middlewares/loop_detection_middleware.py','packages/harness/deerflow/agents/middlewares/token_budget_middleware.py')
    for relative in names:
        try:
            p=Path('/app/backend')/relative;need(not p.is_symlink(),'SOURCE_SYMLINK');before=p.stat();need(stat.S_ISREG(before.st_mode) and before.st_size<=2097152,'SOURCE_BOUND')
            raw=p.read_bytes();after=p.stat();need((before.st_size,before.st_mtime_ns,before.st_ino)==(after.st_size,after.st_mtime_ns,after.st_ino),'SOURCE_CHANGED')
            result[relative]={'bytes':len(raw),'sha256':sha(raw)}
        except FileNotFoundError:result[relative]={'present':False}
        except Exception as exc:result[relative]={'error_class':type(exc).__name__}
    return result

async def observe(payload):
    global DB_HOST,DB_PORT,DB_ADDRESSES,ALLOW_DB_DNS
    out={'schema':'energy-two-task-child.v1','status':'INCOMPLETE','requested_thread_ids':list(THREADS),'database_readonly':True,
        'application_configuration_read_may_resolve_configured_database_credentials':True,'credentials_exported':False,'secret_api_reads':0,
        'raw_messages_or_commands_exported':False,'provider_requests':0,'work_messages_sent':0,'files_modified':False,'tasks_retried':False,'threads':[]}
    conn=None
    try:
        need(isinstance(payload,dict) and payload=={'thread_ids':list(THREADS)},'EXACT_TWO_THREADS_REQUIRED')
        out['source_hashes']=fixed_source_hashes();pg,get_config=runtime_modules();cfg=get_config();db=cfg.database;need(db.backend=='postgres','POSTGRES_REQUIRED')
        url=db.postgres_url.replace('postgresql+asyncpg://','postgresql://',1);parsed=urlsplit(url);need(parsed.hostname and parsed.scheme in ('postgres','postgresql'),'DATABASE_ADDRESS_REQUIRED')
        DB_HOST,DB_PORT=parsed.hostname,parsed.port or 5432;ALLOW_DB_DNS=True
        addresses=socket.getaddrinfo(DB_HOST,DB_PORT,type=socket.SOCK_STREAM);need(0<len(addresses)<=16,'DATABASE_DNS_BOUND');DB_ADDRESSES={r[4][0] for r in addresses}
        conn=await pg.connect(url,timeout=5,command_timeout=5,server_settings={'default_transaction_read_only':'on','statement_timeout':'5000'})
        for thread in THREADS:
            try:out['threads'].append(await diagnose_thread(conn,thread))
            except Exception as exc:out['threads'].append({'thread_id':thread,'status':'INCOMPLETE','error_class':type(exc).__name__,'error_code':str(exc) if isinstance(exc,Refused) else 'TASK_READ_FAILURE'})
        out['status']='TWO_TASKS_READONLY_COLLECTED' if all(t['status']=='TASK_DURABLE_STATE_OBSERVED_READONLY' for t in out['threads']) else 'TWO_TASKS_PARTIALLY_OBSERVED'
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'READONLY_CHILD_FAILURE')
    finally:
        if conn is not None:
            try:await conn.close(timeout=2)
            except Exception:out['database_close_failed']=True
        ALLOW_DB_DNS=False;DB_ADDRESSES=set()
    return out

def main():
    sys.dont_write_bytecode=True;logging.disable(logging.CRITICAL)
    out={'schema':'energy-two-task-child.v1','status':'INCOMPLETE','requested_thread_ids':list(THREADS),'credentials_exported':False}
    try:
        signal.signal(signal.SIGALRM,lambda *a:(_ for _ in ()).throw(Deadline()));signal.alarm(60)
        sys.addaudithook(audit);raw=sys.stdin.buffer.read(4097);need(len(raw)<=4096,'INPUT_BOUND')
        with contextlib.redirect_stdout(Discard()),contextlib.redirect_stderr(Discard()):out=asyncio.run(observe(json.loads(raw)))
    except Deadline:out.update(error_class='Deadline',error_code='READONLY_CHILD_DEADLINE')
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'READONLY_CHILD_FAILURE')
    finally:signal.alarm(0)
    encoded=json.dumps(out,sort_keys=True)
    if len(encoded.encode())>1572864:
        for item in out.get('threads',[]):
            events=item.get('latest_run_events',{});rows=events.get('timeline',[])
            if len(rows)>120:
                events['timeline']=rows[:40]+rows[-80:];events['export_timeline_trimmed_by_size_bound']=True
                events['timeline_before_export_trim_count']=len(rows)
        encoded=json.dumps(out,sort_keys=True)
    if len(encoded.encode())>1835008:
        for item in out.get('threads',[]):
            events=item.get('latest_run_events',{});events.pop('timeline',None);events['export_timeline_omitted_by_size_bound']=True
        encoded=json.dumps(out,sort_keys=True)
    print(encoded);return int(out['status']=='INCOMPLETE')

if __name__=='__main__':sys.exit(main())

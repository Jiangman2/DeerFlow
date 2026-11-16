"""Inspect only the operator-named successful report's missing Work return."""
import datetime as dt
from types import SimpleNamespace
from zoneinfo import ZoneInfo

THREAD='aa98356f-e332-4019-9d68-0d1e79c30ed7'
DB_HOST=None
DB_PORT=None
DB_ADDRESSES=set()
ALLOW_DB_DNS=False
EXPECTED_SOURCES={}
RUN_SQL="""SELECT run_id,status,created_at,updated_at,
octet_length(to_jsonb(r)->>'last_ai_message') AS message_bytes,
CASE WHEN octet_length(to_jsonb(r)->>'last_ai_message')<=262144 THEN to_jsonb(r)->>'last_ai_message' END AS last_ai_message
FROM runs r WHERE thread_id=$1 ORDER BY created_at DESC LIMIT 2"""
OCCURRENCE_SQL="""SELECT r.id,r.task_id,r.run_id,r.status,r.created_at,
to_jsonb(r)->>'finished_at' AS finished_at,to_jsonb(r)->>'trigger' AS trigger
FROM scheduled_task_runs r WHERE thread_id=$1 ORDER BY created_at DESC LIMIT 2"""
TASK_SQL="""SELECT s.id,s.user_id,s.status,
jsonb_typeof(to_jsonb(s)->'run_context'->'work_notification')='object' AS notification_configured,
to_jsonb(s)->'run_context'->'work_notification'->>'connection_id' AS connection_id,
to_jsonb(s)->'run_context'->'work_notification'->>'binding_key' AS binding_key
FROM scheduled_tasks s WHERE id=$1 LIMIT 1"""
OUTBOX_SQL="""SELECT namespace,status,revision,updated_at,expires_at,octet_length(ciphertext)>0 AS payload_retained
FROM work_integration_records WHERE kind='scheduled_notification' AND key_hash=$1 AND owner_hash=$2 LIMIT 4"""
CONNECTION_SQL="""SELECT status,provider,updated_at,last_seen_at,
jsonb_typeof(metadata_json::jsonb->'_work_notification_target')='object' AS notification_target_present
FROM channel_connections WHERE id=$1 AND owner_user_id=$2 LIMIT 1"""
EVENT_SQL="""SELECT seq,octet_length(content) AS bytes,
CASE WHEN octet_length(content)<=262144 THEN content END AS content
FROM run_events WHERE thread_id=$1 AND run_id=$2 AND event_type='llm.ai.response' ORDER BY seq DESC LIMIT 8"""

def identifier(value):return isinstance(value,str) and re.fullmatch('[A-Za-z0-9_-]{1,64}',value)

def read_source(path):
    target=Path('/app/backend')/path
    need(not target.is_symlink(),'SOURCE_SYMLINK_REFUSED')
    before=target.stat();need(stat.S_ISREG(before.st_mode) and before.st_size<=1048576,'SOURCE_BOUND')
    raw=target.read_bytes();after=target.stat()
    need((before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns),'SOURCE_CHANGED')
    return raw

def extracted(source,names,namespace):
    nodes=[node for node in ast.parse(source).body if isinstance(node,ast.FunctionDef) and node.name in names]
    need({node.name for node in nodes}==set(names),'EXACT_FORMATTING_FUNCTIONS_REQUIRED')
    module=ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]+nodes,type_ignores=[]))
    exec(compile(module,'<isolated-live-formatting>','exec'),namespace)

def formatters():
    raw={name:read_source(name) for name in EXPECTED_SOURCES}
    metadata={name:{'bytes':len(value),'sha256':sha(value)} for name,value in raw.items()}
    need(metadata==EXPECTED_SOURCES,'NOTIFICATION_SOURCE_NOT_QUALIFIED_418')
    namespace={'re':re,'urlsplit':urlsplit,'datetime':dt.datetime,'date':dt.date,'UTC':dt.timezone.utc,'ZoneInfo':ZoneInfo,
               'validate_thread_id':lambda value:value if identifier(value) else (_ for _ in ()).throw(ValueError())}
    extracted(read_source('packages/harness/deerflow/utils/conversation_url.py'),{'validated_web_origin'},namespace)
    extracted(raw['app/scheduler/work_notification_delivery.py'],{'_message','_daily_reader_url','_platform_origin'},namespace)
    extracted(raw['app/scheduler/work_notifications.py'],{'_notification_summary'},namespace)
    config=ast.parse(read_source('app/integrations/work/config.py'))
    callback=next(node.value.value for node in config.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PRODUCTION_CALLBACK' for t in node.targets) and isinstance(node.value,ast.Constant))
    namespace.update(os=os,PRODUCTION_CALLBACK=callback)
    profile=os.getenv('DEER_FLOW_WORK_PROFILE','production').strip()
    need(profile in ('production','uat'),'KNOWN_WORK_PROFILE_REQUIRED')
    origin=namespace['_platform_origin'](profile)
    return namespace,origin,profile,metadata

def formatting_summary(value,thread,formatter,origin):
    text=value if isinstance(value,str) else ''
    need(len(text.encode())<=262144,'RESULT_TEXT_BOUND')
    summary=formatter['_notification_summary'](text)
    rendered=formatter['_message'](SimpleNamespace(title='能源日报',result_text=summary,thread_id=thread,finished_at=''),origin)
    urls=[]
    for match in re.finditer(r'https://[^\s()<>"]+',text):
        accepted=formatter['_daily_reader_url'](match.group(0),origin,thread)
        if accepted and accepted not in urls:urls.append(accepted)
    return {'text_present':bool(text),'text_bytes':len(text.encode()),'text_sha256':sha(text),
            'daily_title_prefix':text.lstrip().startswith('油气与金融市场日报'),
            'reader_label_present':'点击查看日报网页' in text,'valid_current_reader_urls':urls[:4],
            'standalone_reader_lines':sum(bool(re.fullmatch(r'\[点击查看日报网页\]\(https?://[^\s()<>]+\)',line.strip())) for line in text.splitlines()),
            'notification_summary_bytes':len(summary.encode()),'notification_would_be_empty':not rendered,
            'notification_is_link_only':bool(re.fullmatch(r'\[点击查看日报网页\]\(https://[^\s()<>]+\)',rendered)),
            'notification_bytes':len(rendered.encode()),'raw_text_exported':False,'isolated_replay_not_actual_delivery':True}

async def inspect_thread(conn,thread,formatter,origin,profile):
    out={'thread_id':thread,'status':'INCOMPLETE','runs':[],'occurrences':[],'outbox':[]}
    owner=await conn.fetchval('SELECT user_id FROM threads_meta WHERE thread_id=$1 LIMIT 1',thread)
    need(isinstance(owner,str) and owner,'TRUSTED_THREAD_OWNER_REQUIRED')
    rows=await conn.fetch(RUN_SQL,thread)
    for row in rows:
        item={'run_id_sha256':sha(row['run_id']),'status':row['status'],'created_at':iso(row['created_at']),'updated_at':iso(row['updated_at']),
              'stored_last_ai_message_bytes':row['message_bytes']}
        if row['last_ai_message'] is not None:item['stored_result_formatting']=formatting_summary(row['last_ai_message'],thread,formatter,origin)
        events=await conn.fetch(EVENT_SQL,thread,row['run_id']);finals=[];send_candidates=0
        for event in events:
            if not isinstance(event['content'],str):continue
            try:obj=json.loads(event['content'])
            except (ValueError,RecursionError):continue
            if not isinstance(obj,dict):continue
            calls=obj.get('tool_calls') if isinstance(obj.get('tool_calls'),list) else []
            if not calls:finals.append(dict(seq=event['seq'],**formatting_summary(text_content(obj.get('content')),thread,formatter,origin)))
            for call in calls[:24]:
                args=call.get('args') if isinstance(call,dict) and isinstance(call.get('args'),dict) else {}
                command=next((args[k] for k in ('command','cmd','bash_command') if isinstance(args.get(k),str)),'')
                if re.search(r'/bot/v2/hook/|/open-apis/bot/',command):send_candidates+=1
        item.update(last_eight_ai_events_checked=len(events),last_final_ai_candidates=finals[:2],custom_webhook_send_proposals=send_candidates,
                    limited_event_window_cannot_prove_no_earlier_custom_send=True)
        out['runs'].append(item)
    occurrences=await conn.fetch(OCCURRENCE_SQL,thread)
    for occurrence in occurrences:
        task=await conn.fetchrow(TASK_SQL,occurrence['task_id'])
        item={'id_sha256':sha(occurrence['id']),'status':occurrence['status'],'trigger':occurrence['trigger'],
              'created_at':iso(occurrence['created_at']),'finished_at':occurrence['finished_at']}
        if task:
            need(task['user_id']==owner,'SCHEDULE_THREAD_OWNER_MISMATCH')
            item.update(native_notification_configured=bool(task['notification_configured']),connection_id_present=bool(task['connection_id']),
                        binding_key_present=bool(task['binding_key']),task_status=task['status'])
            if identifier(task['connection_id']):
                connection=await conn.fetchrow(CONNECTION_SQL,task['connection_id'],owner)
                item['connection']={k:iso(connection[k]) for k in ('status','provider','updated_at','last_seen_at','notification_target_present')} if connection else {'status':'NOT_PRESENT_FOR_OWNER'}
        out['occurrences'].append(item)
        records=await conn.fetch(OUTBOX_SQL,sha(occurrence['id']),sha(owner))
        for record in records:
            out['outbox'].append({'occurrence_id_sha256':sha(occurrence['id']),
                'expected_profile_namespace':record['namespace']==sha('scheduled-work-notifications:v1:'+profile),
                'status':record['status'],'revision':record['revision'],'updated_at_utc':dt.datetime.fromtimestamp(record['updated_at'],dt.timezone.utc).isoformat(),
                'expires_at_utc':dt.datetime.fromtimestamp(record['expires_at'],dt.timezone.utc).isoformat(),'payload_retained':record['payload_retained']})
    out.update(status='WORK_RETURN_STATE_OBSERVED_READONLY',scheduled_occurrence_present=bool(occurrences),
               send_error_reason_not_persisted_by_current_outbox=True,notification_ciphertext_read=False)
    return out

async def observe(payload):
    global DB_HOST,DB_PORT,DB_ADDRESSES,ALLOW_DB_DNS
    out={'schema':'energy-work-return-inspection-child.v1','status':'INCOMPLETE','database_readonly':True,'files_modified':False,
         'provider_requests':0,'work_messages_sent':0,'task_retry_requested':False,'secret_api_reads':0,'credentials_exported':False,
         'raw_messages_exported':False,'application_configuration_may_resolve_database_credentials':True,'threads':[]}
    conn=None
    try:
        need(payload=={'thread_id':THREAD},'EXACT_NAMED_THREAD_REQUIRED')
        formatter,origin,profile,sources=formatters();out.update(formatter_source_hashes=sources,profile=profile,platform_origin_available=bool(origin),
              selected_scope='EXACT_NAMED_THREAD',loaded_application_modules_verified=False)
        pg,get_config=runtime_modules();db=get_config().database;need(db.backend=='postgres','POSTGRES_REQUIRED')
        url=db.postgres_url.replace('postgresql+asyncpg://','postgresql://',1);parsed=urlsplit(url)
        need(parsed.scheme in ('postgres','postgresql') and parsed.hostname,'DATABASE_ADDRESS_REQUIRED')
        DB_HOST,DB_PORT=parsed.hostname,parsed.port or 5432;ALLOW_DB_DNS=True
        addresses=socket.getaddrinfo(DB_HOST,DB_PORT,type=socket.SOCK_STREAM);need(0<len(addresses)<=16,'DATABASE_DNS_BOUND');DB_ADDRESSES={row[4][0] for row in addresses}
        conn=await pg.connect(url,timeout=5,command_timeout=5,server_settings={'default_transaction_read_only':'on','statement_timeout':'5000'})
        async with conn.transaction(readonly=True,isolation='repeatable_read'):
            need(await conn.fetchval('SHOW transaction_read_only')=='on','DATABASE_READONLY_REQUIRED')
            await conn.execute("SET LOCAL statement_timeout='5000ms'")
            out['threads'].append(await inspect_thread(conn,THREAD,formatter,origin,profile))
        out['status']='WORK_RETURN_DIAGNOSTIC_COLLECTED_NO_CHANGE'
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'READONLY_INSPECTION_FAILURE_REDACTED')
    finally:
        if conn is not None:
            try:await conn.close(timeout=2)
            except Exception:out['database_close_failed']=True
        ALLOW_DB_DNS=False;DB_ADDRESSES=set()
    return out

def main():
    sys.dont_write_bytecode=True;logging.disable(logging.CRITICAL)
    out={'schema':'energy-work-return-inspection-child.v1','status':'INCOMPLETE','credentials_exported':False,'work_messages_sent':0}
    try:
        signal.signal(signal.SIGALRM,lambda *args:(_ for _ in ()).throw(Deadline()));signal.alarm(60)
        sys.addaudithook(audit);raw=sys.stdin.buffer.read(4097);need(len(raw)<=4096,'INPUT_BOUND')
        with contextlib.redirect_stdout(Discard()),contextlib.redirect_stderr(Discard()):out=asyncio.run(observe(json.loads(raw)))
    except Deadline:out.update(error_class='Deadline',error_code='READONLY_CHILD_DEADLINE')
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'READONLY_CHILD_FAILURE_REDACTED')
    finally:signal.alarm(0)
    encoded=json.dumps(out,sort_keys=True)
    need(len(encoded.encode())<=131072,'DIAGNOSTIC_EXPORT_BOUND')
    print(encoded);return int(out['status']=='INCOMPLETE')

if __name__=='__main__':sys.exit(main())

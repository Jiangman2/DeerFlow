"""Additional redacted facts for the one operator-named report task."""
import datetime as diagnostic_datetime

TARGET_THREAD='d934a54e-b894-4970-a32e-9b983c55d0df'
PUBLISHED_NAME=re.compile(r'油气与金融市场日报_([0-9]{4}-[0-9]{2}-[0-9]{2})\.(html|json|txt|png)$')


def report_links(value,thread):
    text=text_content(value)
    result=[]
    for match in re.finditer(r'https://cnoocclaw\.cnooc\.com\.cn/reports/energy-daily/'+re.escape(thread)+r'/([0-9]{4}-[0-9]{2}-[0-9]{2})\.html(?=$|[\s<>)"\x27])',text):
        try:
            diagnostic_datetime.date.fromisoformat(match.group(1))
            if match.group(0) not in result:result.append(match.group(0))
        except ValueError:pass
    return result[:4]


def link_event_summary(records,thread):
    result=original_event_summary(records,thread)
    if len(result.get('timeline',[]))>100:
        result['timeline']=result['timeline'][:20]+result['timeline'][-80:]
        result['timeline_export_trimmed']=True
    link_evidence=[]
    for row in records:
        raw=row.get('content')
        if not isinstance(raw,str):continue
        try:obj=json.loads(raw)
        except (ValueError,RecursionError):continue
        if not isinstance(obj,dict):continue
        if row['event_type']=='llm.ai.response':
            content=text_content(obj.get('content'))
            calls=obj.get('tool_calls') if isinstance(obj.get('tool_calls'),list) else []
            entry={'seq':row['seq'],'final_ai_candidate':not calls,'reader_urls':report_links(content,thread),
                   'reader_label_present':'点击查看日报网页' in content,'starts_exact_daily_title':content.lstrip().startswith('油气与金融市场日报')}
            sends=[]
            for call in calls[:24]:
                args=call.get('args') if isinstance(call,dict) and isinstance(call.get('args'),dict) else {}
                command=next((args.get(k) for k in ('command','cmd','bash_command') if isinstance(args.get(k),str)),'')
                if not command:continue
                if re.search(r'requests\s*\.\s*post|httpx\s*\.\s*post|\bcurl\b|urllib\.request|http\.client',command):
                    sends.append({'tool':call.get('name') if call.get('name') in TOOLS else 'OTHER','command_sha256':sha(command),
                                  'custom_robot_webhook_literal_present':bool(re.search(r'/bot/v2/hook/|/open-apis/bot/',command)),
                                  'reader_label_literal_present':'点击查看日报网页' in command,'current_reader_urls':report_links(command,thread),
                                  'literal_file_read_present':bool(re.search(r'read_text|read_bytes|open\(',command)),
                                  'request_body_and_actual_delivery_not_proven':True})
            if sends:entry['http_send_proposals']=sends
            if entry['final_ai_candidate'] or sends:link_evidence.append(entry)
        elif row['event_type']=='llm.tool.result' and obj.get('name')=='present_files':
            artifact=obj.get('artifact') if isinstance(obj.get('artifact'),dict) else {}
            values=artifact.get('presented_files') if isinstance(artifact.get('presented_files'),list) else []
            safe=[]
            for value in values[:16]:
                if isinstance(value,str) and value.startswith('/mnt/user-data/outputs/') and '..' not in value.split('/') and PUBLISHED_NAME.fullmatch(Path(value).name):safe.append(value)
            link_evidence.append({'seq':row['seq'],'publication_tool_status':obj.get('status') if obj.get('status') in ('success','error') else None,
                                  'normalized_artifact_paths_from_tool':safe,'success_receipt_prefix':text_content(obj.get('content')).startswith('Successfully presented files')})
    result['reader_and_send_evidence']=link_evidence[-32:]
    return result


async def output_metadata(conn,thread):
    need(thread==TARGET_THREAD,'EXACT_REPORT_THREAD_REQUIRED')
    owner=await conn.fetchval('SELECT user_id FROM threads_meta WHERE thread_id=$1 LIMIT 1',thread)
    from deerflow.config.paths import get_paths,make_safe_user_id
    safe_owner=make_safe_user_id(owner) if isinstance(owner,str) and owner else None
    root=get_paths().sandbox_outputs_dir(thread,user_id=safe_owner)
    out={'trusted_metadata_owner_present':bool(safe_owner),'scope':'NAMED_THREAD_OUTPUTS_ONLY','files':[],'raw_content_exported':False}
    if not root.is_dir():return dict(out,status='OUTPUT_DIRECTORY_ABSENT')
    for part in (root,)+tuple(root.parents):need(not part.is_symlink(),'OUTPUT_ROOT_LINK_REFUSED')
    count=0
    for directory,folders,names in os.walk(root,followlinks=False):
        relative=Path(directory).relative_to(root)
        folders[:]=[name for name in folders if not(Path(directory)/name).is_symlink()] if len(relative.parts)<3 else []
        count+=len(names)+len(folders);need(count<=1024,'OUTPUT_METADATA_ENTRY_BOUND')
        for name in names:
            match=PUBLISHED_NAME.fullmatch(name)
            if not match:continue
            path=Path(directory)/name
            if path.is_symlink():continue
            before=path.stat();need(stat.S_ISREG(before.st_mode),'OUTPUT_FILE_TYPE_REQUIRED')
            virtual='/mnt/user-data/outputs/'+path.relative_to(root).as_posix()
            item={'virtual_path':virtual,'bytes':before.st_size,'publication_date':match.group(1),'extension':match.group(2),
                  'flat_reader_target':len(path.relative_to(root).parts)==1,'mtime_utc':diagnostic_datetime.datetime.fromtimestamp(before.st_mtime,diagnostic_datetime.timezone.utc).isoformat()}
            if match.group(2) in ('html','txt','json') and before.st_size<=2097152:
                raw=path.read_bytes();after=path.stat()
                need((before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns),'OUTPUT_CHANGED_DURING_READ')
                item['sha256']=sha(raw)
                if match.group(2)=='txt':item.update(reader_urls=report_links(raw.decode('utf-8','replace'),thread),reader_label_present='点击查看日报网页' in raw.decode('utf-8','replace'))
            out['files'].append(item);need(len(out['files'])<=32,'REPORT_ARTIFACT_COUNT_BOUND')
    out.update(status='REPORT_OUTPUT_METADATA_OBSERVED_READONLY',entries_examined=count,
               requested_flat_html_present=any(f['flat_reader_target'] and f['publication_date']=='2026-11-15' and f['extension']=='html' for f in out['files']))
    return out


async def detailed_diagnose(conn,thread):
    out=await original_diagnose_thread(conn,thread)
    try:
        async with conn.transaction(readonly=True,isolation='repeatable_read'):
            need(await conn.fetchval('SHOW transaction_read_only')=='on','DATABASE_READONLY_REQUIRED')
            await conn.execute("SET LOCAL statement_timeout='5000ms'")
            out['report_outputs']=await output_metadata(conn,thread)
    except Exception as exc:out['report_outputs']={'status':'OUTPUT_METADATA_INCOMPLETE','error_class':type(exc).__name__,'error_code':str(exc) if isinstance(exc,Refused) else 'OUTPUT_READ_FAILED_REDACTED'}
    return out


original_event_summary=event_summary
event_summary=link_event_summary
original_diagnose_thread=diagnose_thread
diagnose_thread=detailed_diagnose
SCHEDULE_SQL=SCHEDULE_SQL.replace('\nFROM scheduled_tasks',", (jsonb_typeof(to_jsonb(r)->'run_context'->'work_notification')='object') AS native_work_notification_configured\nFROM scheduled_tasks")

original_source_hashes=fixed_source_hashes
def fixed_source_hashes():
    result=original_source_hashes()
    for relative in ('app/scheduler/work_notification_delivery.py','app/scheduler/work_notifications.py'):
        path=Path('/app/backend')/relative
        try:
            need(not path.is_symlink(),'NOTIFICATION_SOURCE_LINK')
            before=path.stat();need(stat.S_ISREG(before.st_mode) and before.st_size<=1048576,'NOTIFICATION_SOURCE_BOUND')
            raw=path.read_bytes();after=path.stat()
            need((before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns),'NOTIFICATION_SOURCE_CHANGED')
            result[relative]={'bytes':len(raw),'sha256':sha(raw)}
        except Exception as exc:result[relative]={'error_class':type(exc).__name__}
    return result

"""Join execution-stage facts and Work state without exporting report bodies."""
_original_flags=flags
_original_event_summary=event_summary
_original_diagnose_thread=diagnose_thread


def flags(body):
    result=_original_flags(body)
    low=text_content(body).casefold()
    for name,markers in (
        ('gateway_restart_sweep',('gateway restarted before the run','gateway stopped before the run')),
        ('durable_run_lease_expired',('owning gateway stopped renewing','scheduled_task_orphan_recovered','ownership lost')),
        ('queue_timeout',('queue wait timeout exceeded',)),
        ('news_shortage',('news_supplement_required','news_count','news_summaries_required')),
        ('notification_empty',('empty scheduled work notification','notification body is empty'))):
        if any(marker in low for marker in markers):result.append(name)
    return list(dict.fromkeys(result))


def stage_facts(value):
    result=[]
    for obj in objects(value):
        status=safe_word(obj.get('status'))
        code=safe_word(obj.get('error_code'),True)
        if not status and not code:continue
        item={'status':status,'error_code':code}
        for key in ('publication_date','data_date'):
            value=obj.get(key)
            if isinstance(value,str) and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',value):item[key]=value
        for key in ('version','schema'):
            value=obj.get(key)
            if isinstance(value,str) and re.fullmatch(r'[0-9A-Za-z][0-9A-Za-z_.-]{0,95}',value):item[key]=value
        for key in ('market_news_candidates','company_news_candidates','news_articles_with_original_pages','financial_quotes_available',
                    'target_quotes_available','target_quotes_missing','target_quotes_explicit_nontrading','freight_reference_count'):
            value=obj.get(key)
            if type(value) is int and 0<=value<=100000:item[key]=value
        for key in ('market_data_complete','formal_report_ready','freight_reference_search_complete','conversation_link_available',
                    'html_report_link_available','programmatic_checks_passed','publication_allowed','artifacts_ready','reply_text_ready'):
            if type(obj.get(key)) is bool:item[key]=obj[key]
        for key in ('prepare_seconds','render_seconds'):
            value=obj.get(key)
            if type(value) in (int,float) and 0<=value<=100000:item[key]=value
        files=obj.get('files')
        if isinstance(files,list):
            extensions=[]
            for row in files[:16]:
                path=row.get('path') if isinstance(row,dict) else None
                if isinstance(path,str):
                    suffix=Path(path).suffix.casefold()
                    if suffix in ('.html','.png','.json','.txt'):extensions.append(suffix)
            item['file_extensions_in_receipt']=extensions
            item['reported_file_count']=len(files)
        result.append(item)
        if len(result)==8:break
    return result


def event_summary(records,thread):
    summary=_original_event_summary(records,thread)
    rows={row['seq']:row for row in records}
    for entry in summary['timeline']:
        if entry.get('kind')!='llm.tool.result' or not entry.get('owner_seen') or not entry.get('actions'):continue
        raw=rows[entry['seq']].get('content')
        if not isinstance(raw,str):continue
        try:obj=json.loads(raw)
        except (ValueError,RecursionError):continue
        if isinstance(obj,dict):entry['stage_facts']=stage_facts(obj.get('content'))
    return summary


async def diagnose_thread(conn,thread):
    result=await _original_diagnose_thread(conn,thread)
    try:
        formatter,origin,profile,sources=formatters()
        result['notification_formatter_source_hashes']=sources
        async with conn.transaction(readonly=True,isolation='repeatable_read'):
            result['work_notification']=await inspect_notification(conn,thread,formatter,origin,profile)
    except Exception as exc:
        result['work_notification']={'status':'INCOMPLETE','error_class':type(exc).__name__,
                                     'error_code':str(exc) if isinstance(exc,Refused) else 'NOTIFICATION_READ_REDACTED'}
    result['execution_and_notification_use_separate_readonly_snapshots']=True
    return result

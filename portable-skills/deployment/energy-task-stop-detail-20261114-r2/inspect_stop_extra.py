"""Fixed completed scheduled run: terminal explanation and authority read details."""
TARGET = '7f645bba-9bfd-4786-ab0a-e8636cf255d3'
RUN_CREATED = '2026-11-14T04:45:35.327704+00:00'
RUN_SHA = '37988b84a33cee73da5075227de02c32d461958225bebc747e0f2269915afcb0'
SEQUENCES = [10,11,12,16,17,18,19,20]
RUN_DETAIL_SQL = """SELECT run_id,status,created_at,updated_at,left(to_jsonb(r)->>'stop_reason',96) AS stop_reason
FROM runs r WHERE thread_id=$1 AND created_at=$2::timestamptz ORDER BY created_at LIMIT 2"""
DETAIL_SQL = """SELECT seq,event_type,created_at,octet_length(content) AS bytes,
CASE WHEN octet_length(content)<=65536 THEN content ELSE NULL END AS content
FROM run_events WHERE thread_id=$1 AND run_id=$2 AND seq=ANY($3::bigint[]) ORDER BY seq LIMIT 8"""

def redact_terminal(value,limit=4000):
    """Only user-visible final prose, never hidden reasoning or code blocks."""
    if isinstance(value,list):
        value='\n'.join(part.get('text','') for part in value[:32] if isinstance(part,dict) and part.get('type') in ('text','output_text') and isinstance(part.get('text'),str))
    value=value if isinstance(value,str) else ''
    body=value
    body=re.sub(r'```[\s\S]*?(?:```|$)','[CODE_BLOCK_OMITTED]',body)
    body=re.sub(r'https?://[^\s<>"\x27)\]]+','[URL_REDACTED]',body,flags=re.I)
    body=re.sub(r'(?i)\b(?:bearer|basic)\s+[A-Za-z0-9+/_=.-]+','[AUTH_REDACTED]',body)
    body=re.sub(r'(?i)(?:["\x27]?\b(?:[A-Z0-9_]*PASSWORD|[A-Z0-9_]*SECRET|[A-Z0-9_]*TOKEN|[A-Z0-9_]*API_KEY|WEBHOOK|AUTHORIZATION|密码|密钥|凭证)["\x27]?\s*[:=：是为]\s*)(?:"[^"\n]*"|\x27[^\x27\n]*\x27|[^\s,，;；]+)','[SENSITIVE_ASSIGNMENT_REDACTED]',body)
    body=re.sub(r'(?:/[A-Za-z0-9_.-]+){2,}(?:[^\s`，。；]*)','[PATH_REDACTED]',body)
    body=re.sub(r'\b(?:\d{1,3}\.){3}\d{1,3}\b','[IP_REDACTED]',body)
    body=re.sub(r'\b[A-Za-z0-9+/_.=-]{32,}\b','[OPAQUE_IDENTIFIER_REDACTED]',body)
    body=re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b','[EMAIL_REDACTED]',body)
    return {'redacted_user_visible_terminal_text':body[:limit], 'truncated':len(body)>limit,
        'source_character_count':len(text_content(value)),'raw_content_sha256':sha(text_content(value)),
        'reasoning_fields_exported':False,'code_blocks_exported':False}

def authority_result(obj):
    body=text_content(obj.get('content'))
    known=('保存宿主真实抓到的原页','不使用搜索摘要冒充原文','最多十项','import-news','NEWS_SUPPLEMENT_REQUIRED',
           '程序检查日期窗口','页面缺少机器可读时间','语义去重','连续 3 次','恢复契约','重新读取','authority','blocked','Error:')
    return {'tool':safe_word(obj.get('name')),'tool_status':safe_word(obj.get('status')),
        'content_sha256':sha(body),'content_bytes':len(body.encode()),'literal_markers_present':[x for x in known if x in body],
        'failure':errors(obj.get('content')),'host_call_id_prefix_authority_reload':str(obj.get('tool_call_id','')).startswith('authority-reload-'),
        'tool_call_id_sha256':sha(str(obj.get('tool_call_id',''))),'raw_authority_text_exported':False}

def prepare_details(obj):
    accepted=[]
    for candidate in objects(obj.get('content')):
        if candidate.get('status')!='NEWS_SUPPLEMENT_REQUIRED':continue
        item={'status':'NEWS_SUPPLEMENT_REQUIRED'}
        for key in ('market_news_candidates','company_news_candidates','news_articles_with_original_pages','financial_quotes_available','freight_reference_count'):
            if type(candidate.get(key)) is int and 0<=candidate[key]<=10000:item[key]=candidate[key]
        for key in ('prepare_seconds',):
            if type(candidate.get(key)) in (int,float) and 0<=candidate[key]<=10000:item[key]=candidate[key]
        for key in ('publication_date','data_date'):
            value=candidate.get(key)
            if isinstance(value,str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):item[key]=value
        for key in ('market_data_complete','freight_reference_search_complete','collection_restarted'):
            if type(candidate.get(key)) is bool:item[key]=candidate[key]
        for key in ('project','research_file','news_file'):
            value=candidate.get(key)
            if isinstance(value,str):item[key+'_sha256']=sha(value)
        item['next_action_structure']=command({'name':'bash','args':{'command':candidate.get('next_command','')}})
        accepted.append(item)
    return {'records':accepted,'receipt_errors':errors(obj.get('content'))}

def details(rows):
    need(len(rows)==8 and [r['seq'] for r in rows]==SEQUENCES,'EXACT_EVENT_SET_REQUIRED')
    result=[];authority=[]
    for row in rows:
        raw=row.get('content');need(isinstance(raw,str) and len(raw.encode())<=65536,'DETAIL_CONTENT_BOUND')
        obj=json.loads(raw);need(isinstance(obj,dict),'EVENT_OBJECT_REQUIRED')
        entry={'seq':row['seq'],'at':iso(row['created_at']),'kind':row['event_type'],'stored_bytes':row['bytes']}
        if row['seq']==10:
            need(row['event_type']=='llm.tool.result' and obj.get('name')=='bash','PREPARE_RESULT_REQUIRED')
            entry['prepare']=prepare_details(obj)
        elif row['seq']==20:
            need(row['event_type']=='llm.ai.response' and not obj.get('tool_calls'),'EXACT_TERMINAL_AI_REQUIRED')
            entry.update(redact_terminal(obj.get('content')),host_flags=extra_ai(obj.get('additional_kwargs')))
        elif row['seq'] in (11,16,18):
            need(row['event_type']=='llm.ai.response','AUTHORITY_PROPOSAL_REQUIRED')
            entry['proposals']=[command(x) for x in obj.get('tool_calls',[])[:8] if isinstance(x,dict)]
        else:
            need(row['event_type']=='llm.tool.result' and obj.get('name')=='read_file','AUTHORITY_RESULT_REQUIRED')
            entry['authority']=authority_result(obj);authority.append(entry['authority'])
        result.append(entry)
    return {'events':result,'authority_three_result_contents_identical':len({r['content_sha256'] for r in authority})==1,
        'all_authority_results_success':all(r['tool_status']=='success' for r in authority),
        'terminal_text_is_model_explanation_not_independent_proof':True}

async def diagnose_thread(conn,thread):
    if thread!=TARGET:return {'thread_id':thread,'status':'OUT_OF_NARROW_SCOPE_NOT_READ'}
    import datetime
    async with conn.transaction(readonly=True,isolation='repeatable_read'):
        need(await conn.fetchval('SHOW transaction_read_only')=='on','DATABASE_READONLY_REQUIRED')
        for sql in ("SET LOCAL statement_timeout='5000ms'","SET LOCAL lock_timeout='500ms'","SET LOCAL idle_in_transaction_session_timeout='10000ms'"):
            await conn.execute(sql)
        rows=await conn.fetch(RUN_DETAIL_SQL,TARGET,datetime.datetime.fromisoformat(RUN_CREATED))
        need(len(rows)==1 and sha(rows[0]['run_id'])==RUN_SHA,'FIXED_COMPLETED_RUN_REQUIRED')
        need(rows[0]['status']=='success' and rows[0]['updated_at'].isoformat()=='2026-11-14T04:48:02.662332+00:00','FIXED_RUN_TERMINAL_STATE_CHANGED')
        records=await conn.fetch(DETAIL_SQL,TARGET,rows[0]['run_id'],SEQUENCES)
        return {'thread_id':thread,'status':'TASK_DURABLE_STATE_OBSERVED_READONLY','fixed_run_sha256':RUN_SHA,'run':metadata(rows[0]),'detail':details(records)}

# The inherited observer imports configuration only, permits only the database
# socket and invokes this fixed, narrow reader. No source files need re-reading.
def fixed_source_hashes():return {'inspection_scope':'FIXED_PERSISTED_EVENTS_ONLY_NO_NEW_SOURCE_READS'}

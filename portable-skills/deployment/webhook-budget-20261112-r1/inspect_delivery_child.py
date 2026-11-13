"""Fixed-thread delivery timeline: database reads and redacted structural signals only."""
import ast
import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import re
import shlex
import signal
import socket
import sys
from urllib.parse import urlsplit

THREAD = '545097ca-bd47-4416-888e-b6579229c865'
RUNTIME_OBSERVER = None
DB_HOST = None
DB_PORT = None
DB_ADDRESSES = set()
ALLOW_DB_DNS = False
RUN_SQL = """SELECT run_id,status,created_at,updated_at,stop_reason,message_count,
CASE WHEN octet_length(kwargs_json::jsonb #>> '{config,recursion_limit}')<=20 THEN kwargs_json::jsonb #>> '{config,recursion_limit}' ELSE NULL END AS configured_recursion_limit
FROM runs WHERE thread_id=$1 ORDER BY created_at DESC LIMIT 2"""
EVENT_KINDS = ['llm.ai.response','llm.tool.result','run.error','llm.error']
EVENT_SQL = """WITH first_events AS (
 SELECT seq FROM run_events WHERE thread_id=$1 AND run_id=$2 AND event_type=ANY($3::text[]) ORDER BY seq ASC LIMIT 8),
 last_events AS (
 SELECT seq FROM run_events WHERE thread_id=$1 AND run_id=$2 AND event_type=ANY($3::text[]) ORDER BY seq DESC LIMIT 32),
 selected AS (
 SELECT seq,event_type,created_at,octet_length(content) AS bytes
 FROM run_events WHERE thread_id=$1 AND run_id=$2 AND seq IN (SELECT seq FROM first_events UNION SELECT seq FROM last_events)),
 budget AS (
 SELECT *,sum(least(bytes,16384)) OVER (ORDER BY seq DESC) AS cumulative_bytes FROM selected)
SELECT b.seq,b.event_type,b.created_at,b.bytes,
 CASE WHEN b.bytes<=16384 AND b.cumulative_bytes<=131072 THEN e.content ELSE NULL END AS content,
 CASE WHEN b.bytes>16384 AND b.cumulative_bytes<=131072 THEN encode(substring(convert_to(e.content,'UTF8') FROM 1 FOR 8192),'base64') ELSE NULL END AS head_b64,
 CASE WHEN b.bytes>16384 AND b.cumulative_bytes<=131072 THEN encode(substring(convert_to(e.content,'UTF8') FROM b.bytes-8192+1 FOR 8192),'base64') ELSE NULL END AS tail_b64
FROM budget b JOIN run_events e ON e.thread_id=$1 AND e.run_id=$2 AND e.seq=b.seq ORDER BY b.seq LIMIT 40"""
KNOWN_TOOLS = {'bash','present_files','read_file','write_file','edit_file','web_search','web_fetch','view_image','tool_search'}
KNOWN_ACTIONS = ('doctor','prepare','select','import-news','build','accept','_acquire')
KNOWN_STATUSES = {'ENVIRONMENT_OBSERVED','FAILED','READY_FOR_NEWS_SUMMARIES','NEWS_SUPPLEMENT_REQUIRED','SOURCES_ALREADY_PREPARED','SUMMARY_BATCH_READY','VERIFIED_SOURCE_EVIDENCE_IMPORTED','REPORT_GENERATED_VISUAL_REVIEW_REQUIRED','REPORT_ACCEPTED','ACQUISITION_FINISHED'}
KNOWN_EXCEPTIONS = ('GraphRecursionError','RecursionError','RuntimeError','PermissionError','FileNotFoundError','NameError','TypeError','ValueError','SyntaxError','TimeoutError','ConnectionError','HTTPError','TargetClosedError')
KNOWN_ERRORS = {'UNEXPECTED_FAILURE','BROWSER_RUNTIME_UNAVAILABLE','NO_USABLE_BROWSER','CREDENTIALS_NOT_CONFIGURED','PROVIDER_REQUEST_FAILED','INPUTS_CHANGED_REBUILD','NEWS_COUNT','NEWS_SUMMARIES_REQUIRED'}

class Refused(ValueError):
    pass

class Deadline(BaseException):
    pass

def need(value, code):
    if not value:
        raise Refused(code)

def sha(value):
    return hashlib.sha256(value.encode() if isinstance(value,str) else value).hexdigest()

def iso(value):
    return value.isoformat() if hasattr(value,'isoformat') else value

def text_content(value):
    if isinstance(value,str):
        return value
    if isinstance(value,list):
        return '\n'.join(part.get('text','') for part in value[:32] if isinstance(part,dict) and isinstance(part.get('text'),str))
    if isinstance(value,dict):
        return json.dumps(value,ensure_ascii=False)
    return ''

def signals(value):
    body=text_content(value)
    current_urls=[]
    for match in re.finditer(r'https?://[^\s<>"\x27)]+',body):
        try:
            target=urlsplit(match.group(0))
            if target.scheme=='https' and target.hostname=='cnoocclaw.cnooc.com.cn' and target.path.rstrip('/')=='/workspace/chats/'+THREAD and not target.query and not target.fragment and target.username is None and target.port in (None,443):
                current_urls.append(sha(match.group(0)))
        except ValueError:
            pass
    return {'current_thread_url_present':bool(current_urls),'current_thread_url_sha256s':sorted(set(current_urls))[:4],'thread_url_origin_basis':'KNOWN_OPERATOR_PUBLIC_ORIGIN',
        'full_daily_link_label_present':'点击查看完整日报' in body,
        'incomplete_data_marker_present':any(term in body for term in ('数据不完整','行情数据不完整','数据未取得','未取得','未返回','缺失')),
        'old_price_not_used_marker_present':any(term in body for term in ('未用旧价','没有用旧价','不补旧价','不以旧价','未使用旧价')),
        'attachment_claim_present':any(term in body for term in ('成品见附件','见附件','HTML/PNG','HTML、JSON','附件中')),
        'exception_classes':[term for term in KNOWN_EXCEPTIONS if term in body],
        'recursion_capped_marker_present':any(term in body.casefold() for term in ('recursion_limit','recursion limit','recursion_capped'))}

def url_summary(value):
    if not isinstance(value,str) or len(value)>4096:
        return {'url_kind':'DYNAMIC_OR_UNKNOWN'}
    try:
        parsed=urlsplit(value)
        if parsed.scheme not in ('http','https') or not parsed.hostname:
            return {'url_kind':'DYNAMIC_OR_UNKNOWN'}
        host=parsed.hostname.casefold()
        path=parsed.path.casefold()
        return {'url_kind':'LITERAL_HTTP_URL','target_url_sha256':sha(value),'target_hostname_sha256':sha(host),
            'target_host_class':'CNOOC_DOMAIN' if host=='cnooc.com.cn' or host.endswith('.cnooc.com.cn') else 'FEISHU_DOMAIN' if host=='feishu.cn' or host.endswith('.feishu.cn') else 'LARK_DOMAIN' if host=='larksuite.com' or host.endswith('.larksuite.com') else 'OTHER_HOST_REDACTED',
            'target_path_kind':'CUSTOM_ROBOT_WEBHOOK' if '/bot/v2/hook/' in path or '/open-apis/bot/' in path else 'NATIVE_APP_MESSAGES' if '/im/v1/messages' in path else 'OTHER_PATH_REDACTED',
            'query_present':bool(parsed.query),'userinfo_present':parsed.username is not None or parsed.password is not None}
    except ValueError:
        return {'url_kind':'DYNAMIC_OR_UNKNOWN'}

def literal_node(node,depth=0):
    """Read only JSON-shaped AST literals. Never execute or resolve expressions."""
    if depth>4:
        raise Refused('DYNAMIC_PAYLOAD')
    if isinstance(node,ast.Constant) and isinstance(node.value,(str,int,float,bool,type(None))):
        need(not isinstance(node.value,str) or len(node.value)<=16384,'DYNAMIC_PAYLOAD')
        return node.value
    if isinstance(node,ast.Dict) and len(node.keys)<=64:
        result={}
        for key,value in zip(node.keys,node.values):
            need(isinstance(key,ast.Constant) and isinstance(key.value,str) and len(key.value)<=64,'DYNAMIC_PAYLOAD')
            result[key.value]=literal_node(value,depth+1)
        return result
    if isinstance(node,(ast.List,ast.Tuple)) and len(node.elts)<=64:
        return [literal_node(part,depth+1) for part in node.elts]
    raise Refused('DYNAMIC_PAYLOAD')

def payload_summary(value,proven,argument_kind=None):
    result={'literal_payload_proven':bool(proven),'payload_presence_basis':'LITERAL_ONLY' if proven else 'DYNAMIC_PAYLOAD_UNKNOWN',
        'request_argument_kind':argument_kind if argument_kind in ('json','data','content','curl_data') else 'UNKNOWN',
        'encoding_basis':'STATIC_LITERAL_REPRESENTATION_NOT_WIRE_BODY','wire_payload_bytes_unknown':True}
    if not proven:
        result.update(current_thread_url_present=None,full_daily_link_label_present=None,incomplete_data_marker_present=None,literal_payload_representation_bytes=None,message_type='UNKNOWN')
        return result
    if argument_kind=='json':
        obj=value
        body=json.dumps(value,ensure_ascii=False,separators=(',',':'))
    elif isinstance(value,str):
        try:
            obj=json.loads(value)
        except (ValueError,RecursionError):
            obj=None
        body=value
    else:
        # data=dict is generally form encoded; content=dict has no proven JSON
        # serialization. Its literal structure cannot prove a Work message type.
        obj=None
        body=json.dumps(value,ensure_ascii=False,separators=(',',':'))
    result.update(signals(body),literal_payload_representation_bytes=len(body.encode()),message_type=obj.get('msg_type') if isinstance(obj,dict) and obj.get('msg_type') in ('text','post','interactive','image','share_chat','share_user') else 'OTHER_OR_NOT_JSON')
    return result

def python_sources(command):
    """Only quoted heredocs have an unexpanded literal Python source contract."""
    found=[]
    pattern=r"(?m)^[ \t]*((?:[^\s]+/)?python(?:3(?:\.[0-9]+)?)?[^\n]*?)<<\s*(['\"])([A-Za-z_][A-Za-z0-9_]*)\2[ \t]*\n(.*?)\n\3(?:\n|$)"
    for match in re.finditer(pattern,command,re.S):
        try:
            header=shlex.split(match.group(1))
            if header and re.fullmatch(r'(?:.*/)?python(?:3(?:\.[0-9]+)?)?',header[0]) and all(part in ('-I','-B','-u','-') for part in header[1:]):
                found.append((match.group(4),True))
        except ValueError:
            pass
    if not found and '\n' not in command and not any(term in command for term in ('$','`')):
        try:
            parts=shlex.split(command)
            if parts and re.fullmatch(r'(?:.*/)?python(?:3(?:\.[0-9]+)?)?',parts[0]) and '-c' in parts:
                at=parts.index('-c')
                if at+1<len(parts):
                    found.append((parts[at+1],True))
        except ValueError:
            pass
    return found[:4]

def post_candidates(command):
    result=[]
    for source,shell_literal in python_sources(command):
        try:
            tree=ast.parse(source)
        except (SyntaxError,ValueError,RecursionError):
            continue
        for node in ast.walk(tree):
            if len(result)>=8:
                break
            if not isinstance(node,ast.Call) or not isinstance(node.func,ast.Attribute) or node.func.attr!='post':
                continue
            base=node.func.value
            if not isinstance(base,ast.Name) or base.id not in ('requests','httpx'):
                continue
            entry={'method':'POST','client_kind':base.id,'evidence_basis':'STATIC_PROPOSED_COMMAND_NOT_EXECUTION'}
            urlnode=node.args[0] if node.args else next((item.value for item in node.keywords if item.arg=='url'),None)
            entry.update(url_summary(urlnode.value if isinstance(urlnode,ast.Constant) and isinstance(urlnode.value,str) and shell_literal else None))
            value=None;proven=False;argument_kind=None
            for key in ('json','data','content'):
                selected=[item.value for item in node.keywords if item.arg==key]
                if len(selected)!=1:
                    continue
                argument_kind=key
                try:
                    value=literal_node(selected[0]);proven=shell_literal
                except Refused:
                    pass
                break
            entry['payload']=payload_summary(value,proven,argument_kind)
            result.append(entry)
    # curl: no shell expansion, pipes, redirects, command substitution or chained commands.
    if re.search(r'\bcurl\b',command):
        entry={'method':'POST_CANDIDATE','client_kind':'curl','evidence_basis':'STATIC_PROPOSED_COMMAND_NOT_EXECUTION'}
        clean=not any(term in command for term in ('$','`','\n',';','&&','||','|','>','<'))
        parts=[]
        try:
            parts=shlex.split(command)
        except ValueError:
            clean=False
        if clean and parts and parts[0].rsplit('/',1)[-1]=='curl' and not any(part in ('-K','--config','-T','--upload-file') or part.startswith('--config=') for part in parts):
            urls=[part for part in parts[1:] if part.startswith(('http://','https://'))]
            entry.update(url_summary(urls[0] if len(urls)==1 else None))
            values=[]
            for index,part in enumerate(parts[:-1]):
                if part in ('-d','--data','--data-raw','--data-binary','--json'):
                    values.append(parts[index+1])
            explicit_methods=[]
            for index,part in enumerate(parts):
                if part in ('-X','--request') and index+1<len(parts):
                    explicit_methods.append(parts[index+1].upper())
                elif part.startswith('--request='):
                    explicit_methods.append(part.split('=',1)[1].upper())
                elif part.startswith('-X') and len(part)>2:
                    explicit_methods.append(part[2:].upper())
            non_post=any(part in ('--get','--head') or re.fullmatch('-[A-Za-z]+',part) and any(letter in part[1:] for letter in ('G','I')) for part in parts[1:]) or any(method!='POST' for method in explicit_methods)
            if non_post:
                entry['method']='NON_POST_OR_UNKNOWN'
            proven=not non_post and len(values)==1 and not values[0].startswith('@')
            # Only JSON literal bodies are proven. Shell syntax outside this subset remains unknown.
            try:
                if proven:
                    json.loads(values[0])
            except (ValueError,RecursionError):
                proven=False
            entry['payload']=payload_summary(values[0] if proven else None,proven,'curl_data')
        else:
            entry.update(url_summary(None));entry['payload']=payload_summary(None,False)
        result.append(entry)
    if not result and re.search(r'(?:requests|httpx)\s*\.\s*post\s*\(',command):
        result.append({'method':'POST_CANDIDATE','client_kind':'python_unknown_shell_context','evidence_basis':'STATIC_PROPOSED_COMMAND_NOT_EXECUTION',**url_summary(None),'payload':payload_summary(None,False)})
    return result[:8]

def command_summary(call):
    args=call.get('args') if isinstance(call.get('args'),dict) else {}
    command=args.get('command',args.get('cmd',args.get('bash_command','')))
    command=command if isinstance(command,str) else ''
    name=call.get('name') if isinstance(call.get('name'),str) and call['name'] in KNOWN_TOOLS else 'other'
    return {'tool':name,'tool_call_id_sha256':sha(str(call.get('id',''))),'command_sha256':sha(command) if command else None,
        'daily_actions':[action for action in KNOWN_ACTIONS if re.search(r'daily\.py["\x27]?\s+'+re.escape(action)+r'(?:\s|$)',command)],
        'command_static_signals':signals(command),'post_candidates':post_candidates(command),
        'ai_proposal_is_not_execution_proof':True,'external_file_payload_not_read':True}

def objects(value):
    found=[]
    def visit(item,depth=0):
        if depth>3 or len(found)>=16:
            return
        if isinstance(item,dict):
            found.append(item)
            for key in ('content','stdout','stderr','result','output'):
                if key in item:
                    visit(item[key],depth+1)
        elif isinstance(item,list):
            for part in item[:8]:
                visit(part,depth+1)
        elif isinstance(item,str):
            try:
                visit(json.loads(item),depth+1)
            except (ValueError,RecursionError):
                for line in item.splitlines()[:128]:
                    if '{' in line:
                        try:
                            visit(json.JSONDecoder().raw_decode(line[line.index('{'):])[0],depth+1)
                        except (ValueError,RecursionError):
                            pass
    visit(value)
    return found

def receipt_summary(value):
    body=text_content(value)
    result={'signals':signals(body),'starts_with_error_prefix':body.lstrip().casefold().startswith('error:'),'structured_receipts':[]}
    exits={int(number) for number in re.findall(r'(?:exitcode|exit_code|exit code|Process exited with code)["\s:=]+(-?\d{1,3})',body,re.I)}
    codes=set()
    http={int(number) for number in re.findall(r'(?:HTTP/[12](?:\.\d)?\s+|http_status["\s:=]+|status_code["\s:=]+)([1-5]\d{2})\b',body,re.I)}
    for obj in objects(value):
        safe={}
        status=obj.get('status')
        if isinstance(status,str) and status in KNOWN_STATUSES:
            safe['status']=status
        error=obj.get('error_code')
        if isinstance(error,str):
            safe['error_code']=error if error in KNOWN_ERRORS else 'OTHER_REDACTED';safe['error_code_sha256']=sha(error)
        exception=obj.get('error_class')
        if isinstance(exception,str) and exception in KNOWN_EXCEPTIONS:
            safe['error_class']=exception
        for key in ('ready_for_report','market_data_complete','credentials_configured'):
            if type(obj.get(key)) is bool:
                safe[key]=obj[key]
        for key in ('exitcode','exit_code','returncode'):
            number=obj.get(key)
            if type(number) is int and -255<=number<=255:
                exits.add(number)
        for key in ('http_status','status_code'):
            number=obj.get(key)
            if type(number) is int and 100<=number<=599:
                http.add(number)
        for key in ('code','StatusCode','errcode'):
            number=obj.get(key)
            if type(number) is int and -2147483648<=number<=2147483647:
                codes.add(number)
        if safe and safe not in result['structured_receipts']:
            result['structured_receipts'].append(safe)
    result.update(exit_codes=sorted(exits)[:8],http_status_codes=sorted(http)[:8],provider_business_codes=sorted(codes)[:8],business_code_fields_need_sender_context=True,http_200_alone_not_delivery_acceptance=True)
    return result

def decode_record(row):
    if isinstance(row.get('content'),str):
        raw=row['content'].encode();need(len(raw)<=16384,'EVENT_BYTES_BOUND')
        return raw.decode(),len(raw),False
    pieces=[];count=0
    for key in ('head_b64','tail_b64'):
        value=row.get(key)
        if value is not None:
            need(isinstance(value,str) and len(value)<=12000,'EVENT_FRAGMENT_ENCODING_BOUND')
            raw=base64.b64decode(value,validate=False);need(len(raw)<=8192,'EVENT_FRAGMENT_BOUND');count+=len(raw);pieces.append(raw.decode('utf-8','ignore'))
    return '\n<OMITTED_MIDDLE>\n'.join(pieces),count,True

def summarize_events(records):
    need(len(records)<=40,'EVENT_COUNT_BOUND')
    events=[];calls={};bytes_read=0;terminal_ai=[];publications=[];posts=[]
    for row in records:
        body,length,partial=decode_record(row);bytes_read+=length;need(bytes_read<=131072,'EVENT_TOTAL_BYTES_BOUND')
        kind=row['event_type'];need(kind in EVENT_KINDS,'EVENT_KIND_BOUND')
        event={'seq':row['seq'],'kind':kind,'created_at':iso(row['created_at']),'stored_bytes':row['bytes'],'observed_content_bytes':length,'partial_content':partial}
        if not body:
            event['content_skipped_by_sql_total_budget']=True;events.append(event);continue
        if partial:
            event.update(partial_signals=signals(body),partial_fragments_cannot_prove_payload_absence_or_tool_execution=True)
            events.append(event);continue
        try:
            obj=json.loads(body)
        except (ValueError,RecursionError):
            obj=None
        if kind=='llm.ai.response' and isinstance(obj,dict):
            rawcalls=obj.get('tool_calls') if isinstance(obj.get('tool_calls'),list) else []
            summaries=[command_summary(call) for call in rawcalls[:16] if isinstance(call,dict)]
            event['proposed_calls']=summaries;event['model_text_signals']=signals(obj.get('content'));event['model_text_is_not_execution_proof']=True
            for summary in summaries:
                calls[summary['tool_call_id_sha256']]={'seq':row['seq'],'summary':summary}
                if summary['post_candidates']:
                    posts.append({'call_seq':row['seq'],'tool_call_id_sha256':summary['tool_call_id_sha256'],'post_candidates':summary['post_candidates'],'actual_result_present_in_selected_window':False})
            if not rawcalls:
                terminal_ai.append({'seq':row['seq'],'created_at':iso(row['created_at']),'signals':signals(obj.get('content')),'candidate_final_ai_not_independently_terminal_proven':True})
        elif kind=='llm.tool.result' and isinstance(obj,dict):
            ident=sha(str(obj.get('tool_call_id','')));name=obj.get('name') if isinstance(obj.get('name'),str) and obj['name'] in KNOWN_TOOLS else 'other'
            state=obj.get('status') if isinstance(obj.get('status'),str) and obj['status'] in ('success','error') else None
            receipt=receipt_summary(obj.get('content'))
            event.update(tool=name,tool_call_id_sha256=ident,tool_status=state,receipt=receipt,owning_proposal_present_in_selected_window=ident in calls)
            if ident in calls:
                event['owning_proposal']=calls[ident]['summary']
            for post in posts:
                if post['tool_call_id_sha256']==ident:
                    post.update(actual_result_present_in_selected_window=True,result_seq=row['seq'],tool_status=state,receipt=receipt,paired_tool_result_does_not_by_itself_prove_http_delivery=True)
            if name=='present_files':
                artifact=obj.get('artifact') if isinstance(obj.get('artifact'),dict) else {}
                content=text_content(obj.get('content'))
                publications.append({'seq':row['seq'],'created_at':iso(row['created_at']),'tool_status':state,'success_receipt_prefix_present':content.startswith('Successfully presented files'),'artifact_publication_status':artifact.get('publication_status') if artifact.get('publication_status') in ('success','failed') else None,'signals':signals(content)})
        else:
            event['error_signals']=signals(obj.get('content') if isinstance(obj,dict) else body)
        events.append(event)
    last_ai=terminal_ai[-1] if terminal_ai else None
    for post in posts:
        post['proposed_before_latest_ai_candidate']=post['call_seq']<last_ai['seq'] if last_ai else None
        post['result_before_latest_ai_candidate']=post.get('result_seq')<last_ai['seq'] if last_ai and post.get('result_seq') is not None else None
        post['prior_present_files_receipt_count']=sum(item['seq']<post['call_seq'] for item in publications)
    command_counts={}
    for call in calls.values():
        key=call['summary']['command_sha256']
        if key:
            command_counts[key]=command_counts.get(key,0)+1
    return {'events':events,'event_count':len(events),'event_content_bytes_read':bytes_read,'event_content_budget_bytes':131072,'event_window_selection':'FIRST8_AND_LAST32_LATEST_RUN',
        'window_may_omit_middle_or_content':len(records)==40 or any(row['partial_content'] or row.get('content_skipped_by_sql_total_budget') for row in events),
        'post_candidate_timeline':posts,'present_files_receipts':publications,'ai_without_toolcalls_candidates':terminal_ai,'latest_ai_without_toolcalls_candidate':last_ai,
        'repeated_command_hash_counts':[{'command_sha256':key,'proposed_count':count} for key,count in sorted(command_counts.items()) if count>1],
        'raw_messages_exported':False,'raw_commands_exported':False,'webhook_urls_exported':False,'dynamic_payload_absence_not_inferred':True}

def audit(event,args):
    if event=='open':
        mode=args[1] if len(args)>1 else None;flags=args[2] if len(args)>2 else 0
        writing=isinstance(mode,str) and any(token in mode for token in ('w','a','x','+')) or isinstance(flags,int) and flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)
        need(not writing,'FILE_WRITE_BLOCKED')
    if event in ('os.mkdir','os.remove','os.rmdir','os.rename','os.link','os.symlink','os.truncate','os.chmod','os.chown','os.utime','subprocess.Popen','os.system','sqlite3.connect','fcntl.flock'):
        raise Refused('MUTATION_PROCESS_OR_LOCK_BLOCKED')
    if event=='socket.getaddrinfo':
        need(ALLOW_DB_DNS and args[0]==DB_HOST and args[1] in (DB_PORT,str(DB_PORT),None),'NON_DATABASE_DNS_BLOCKED')
    if event=='socket.connect':
        target=args[1];need(isinstance(target,tuple) and len(target)>=2 and target[0] in DB_ADDRESSES and target[1]==DB_PORT,'NON_DATABASE_NETWORK_BLOCKED')
    if event in ('socket.bind','socket.gethostbyname','socket.gethostbyaddr','socket.sendto'):
        raise Refused('NON_DATABASE_NETWORK_BLOCKED')

def runtime_modules():
    import asyncpg
    from deerflow.config import get_app_config
    return asyncpg,get_app_config

async def diagnose(conn):
    result={'runs':[],'status':'INCOMPLETE'}
    async with conn.transaction(readonly=True,isolation='repeatable_read'):
        need(await conn.fetchval('SHOW transaction_read_only')=='on','DATABASE_READONLY_REQUIRED')
        for sql in ("SET LOCAL statement_timeout='5000ms'","SET LOCAL lock_timeout='500ms'","SET LOCAL idle_in_transaction_session_timeout='10000ms'"):
            await conn.execute(sql)
        rows=await conn.fetch(RUN_SQL,THREAD);need(len(rows)<=2,'RUN_COUNT_BOUND')
        for row in rows:
            scalar=row.get('configured_recursion_limit')
            result['runs'].append({'run_id_sha256':sha(row['run_id']),'status':row.get('status') if row.get('status') in ('pending','running','success','error','timeout','interrupted') else 'other',
                'created_at':iso(row.get('created_at')),'updated_at':iso(row.get('updated_at')),'message_count':row.get('message_count'),
                'stop_reason':row.get('stop_reason') if row.get('stop_reason') in (None,'recursion_capped','loop_capped','token_capped','safety_capped','awaiting_human_input') else 'other',
                'projected_config_recursion_limit':int(scalar) if isinstance(scalar,str) and re.fullmatch(r'[0-9]{1,7}',scalar) else None})
        if not rows:
            result['status']='NAMED_THREAD_RUN_NOT_FOUND';return result
        run=rows[0]['run_id'];need(isinstance(run,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',run),'RUN_ID_SHAPE')
        events=await conn.fetch(EVENT_SQL,THREAD,run,EVENT_KINDS)
        result.update(summarize_events([dict(row) for row in events]),status='DELIVERY_TIMELINE_OBSERVED_READONLY',run_selection_basis='LATEST_RUN_AS_OF_REPEATABLE_READ_SNAPSHOT',latest_run_not_proof_of_specific_user_attempt=True)
    return result

async def observe(payload):
    global DB_HOST,DB_PORT,DB_ADDRESSES,ALLOW_DB_DNS
    out={'schema':'energy-webhook-budget-child.v1','status':'INCOMPLETE','requested_thread_id':THREAD,'database_readonly':True,'database_credentials_used':False,'configuration_load_only':True,
        'application_configuration_read_may_resolve_configured_credentials':True,'credentials_exported':False,'raw_messages_exported':False,'provider_requests':0,'work_messages_sent':0,
        'files_modified':False,'locks_probed':False,'provider_instantiated':False,'run_or_gate_modified':False}
    conn=None;stage='input'
    try:
        need(isinstance(payload,dict) and set(payload)=={'thread_id'} and payload['thread_id']==THREAD,'FIXED_THREAD_REQUIRED')
        stage='configuration_only';pg,get_config=runtime_modules();cfg=get_config()
        if RUNTIME_OBSERVER is not None:
            try:
                out['runtime_summary']=RUNTIME_OBSERVER(cfg)
            except Exception as exc:
                out['runtime_summary']={'status':'INCOMPLETE','error_class':type(exc).__name__,'error_code':'RUNTIME_SUMMARY_FAILURE_REDACTED'}
        stage='database_connection';db=cfg.database;need(db.backend=='postgres','POSTGRES_REQUIRED')
        url=db.postgres_url.replace('postgresql+asyncpg://','postgresql://',1);parsed=urlsplit(url)
        need(parsed.scheme in ('postgres','postgresql') and parsed.hostname,'DATABASE_ADDRESS_REQUIRED')
        DB_HOST,DB_PORT=parsed.hostname,parsed.port or 5432;ALLOW_DB_DNS=True
        addresses=socket.getaddrinfo(DB_HOST,DB_PORT,type=socket.SOCK_STREAM);need(0<len(addresses)<=16,'DATABASE_DNS_BOUND');DB_ADDRESSES={row[4][0] for row in addresses}
        out['database_credentials_used']=True
        conn=await pg.connect(url,timeout=5,command_timeout=5,server_settings={'default_transaction_read_only':'on','statement_timeout':'5000'})
        stage='bounded_named_thread_timeline';out.update(await diagnose(conn))
    except Exception as exc:
        out.update(last_step=stage,error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'DELIVERY_INSPECTION_FAILURE_REDACTED')
    finally:
        if conn is not None:
            try:
                await conn.close(timeout=2)
            except Exception as exc:
                out['database_close_error_class']=type(exc).__name__
        ALLOW_DB_DNS=False;DB_ADDRESSES=set()
    return out

class Discard:
    def write(self,value):
        return len(value)
    def flush(self):
        pass

def main():
    sys.dont_write_bytecode=True;logging.disable(logging.CRITICAL)
    out={'schema':'energy-webhook-budget-child.v1','status':'INCOMPLETE','credentials_exported':False,'work_messages_sent':0}
    try:
        signal.signal(signal.SIGALRM,lambda *args:(_ for _ in ()).throw(Deadline()));signal.alarm(45)
        sys.addaudithook(audit);raw=sys.stdin.buffer.read(4097);need(len(raw)<=4096,'INPUT_BOUND');payload=json.loads(raw)
        with contextlib.redirect_stdout(Discard()),contextlib.redirect_stderr(Discard()):
            out=asyncio.run(observe(payload))
    except Deadline:
        out.update(error_class='Deadline',error_code='CHILD_DEADLINE')
    except Exception as exc:
        out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'DELIVERY_CHILD_FAILURE_REDACTED')
    finally:
        signal.alarm(0)
    print(json.dumps(out,sort_keys=True));return int(out['status']=='INCOMPLETE')

if __name__=='__main__':
    sys.exit(main())

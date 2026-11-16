"""Pure static classification. Never evaluate commands, read payload files, or send."""
FIXED_RUN_SHA256 = '03c66c03186bbf7365ee886846fc816c0b18ee85648c66af7629fb0e80be8ab5'
FIXED_SEQUENCES = [87, 88, 89, 90] + list(range(99, 121))
_original_command_summary = command_summary
_original_receipt_summary = receipt_summary
_original_summarize_events = summarize_events
UNKNOWN = object()


class EncodedShape:
    def __init__(self, tree, representation): self.tree, self.representation = tree, representation


def contains_unknown(value):
    if value is UNKNOWN or isinstance(value, EncodedShape): return True
    if isinstance(value, dict): return any(contains_unknown(item) for item in value.values())
    if isinstance(value, list): return any(contains_unknown(item) for item in value)
    return False


def dotted(node):
    if isinstance(node, ast.Name): return node.id
    if isinstance(node, ast.Attribute): return dotted(node.value) + '.' + node.attr
    return ''


def static_value(node, values, depth=0):
    if depth > 6: raise Refused('STATIC_VALUE_BOUND')
    if isinstance(node, ast.Name) and node.id in values: return values[node.id]
    if isinstance(node, (ast.Constant, ast.Str, ast.Num, ast.NameConstant)):
        value = getattr(node, 'value', getattr(node, 's', getattr(node, 'n', None)))
        need(type(value) in (str, bytes, int, float, bool, type(None)), 'STATIC_VALUE_TYPE')
        need(not isinstance(value, (str, bytes)) or len(value) <= 32768, 'STATIC_VALUE_BOUND')
        return value
    if isinstance(node, ast.Dict) and len(node.keys) <= 64:
        result = {}
        for key, value in zip(node.keys, node.values):
            name = static_value(key, values, depth+1)
            need(isinstance(name, str) and len(name) <= 64, 'STATIC_KEY_BOUND')
            try: result[name] = static_value(value, values, depth+1)
            except (ValueError, TypeError, OverflowError, RecursionError): result[name] = UNKNOWN
        return result
    if isinstance(node, (ast.List, ast.Tuple)) and len(node.elts) <= 64:
        return [static_value(part, values, depth+1) for part in node.elts]
    if isinstance(node, ast.Call) and dotted(node.func) == 'json.dumps' and len(node.args) == 1:
        kwargs = {item.arg: static_value(item.value, values, depth+1) for item in node.keywords}
        need(set(kwargs) <= {'ensure_ascii', 'separators', 'indent'}, 'STATIC_JSON_OPTIONS')
        value = static_value(node.args[0], values, depth+1)
        if contains_unknown(value): return EncodedShape(value, 'string')
        return json.dumps(value, **kwargs)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'encode' and not node.keywords and len(node.args) <= 1:
        text = static_value(node.func.value, values, depth+1)
        encoding = static_value(node.args[0], values, depth+1) if node.args else 'utf-8'
        need(isinstance(text, (str, EncodedShape)) and encoding in ('utf-8', 'utf8', 'ascii'), 'STATIC_ENCODING')
        if isinstance(text, EncodedShape): return EncodedShape(text.tree, 'bytes')
        return text.encode(encoding)
    raise Refused('DYNAMIC_VALUE_UNKNOWN')


def value_or_unknown(node, values):
    try: return True, static_value(node, values)
    except (ValueError, TypeError, OverflowError, RecursionError): return False, None


def message_shape(value, known):
    result = {'payload_static_known': known and not contains_unknown(value), 'payload_shape_static_known':known, 'wire_body_not_proven': True,
              'body_representation': 'unknown', 'payload_bytes': None, 'json_decodable': None,
              'message_type_key': 'unknown', 'message_type': 'unknown', 'known_field_presence': {}}
    if not known: return result
    if isinstance(value, EncodedShape):
        obj = value.tree; result.update(body_representation=value.representation, json_decodable=True, dynamic_values_remain_unknown=True)
    elif isinstance(value, dict):
        obj = value; result['body_representation'] = 'dictionary'
    elif isinstance(value, (str, bytes)):
        result.update(body_representation='bytes' if isinstance(value, bytes) else 'string', payload_bytes=len(value if isinstance(value, bytes) else value.encode()))
        try: obj = json.loads(value); result['json_decodable'] = True
        except (ValueError, UnicodeError, RecursionError): obj = None; result['json_decodable'] = False
    else: obj = None; result['body_representation'] = 'other_literal'
    if not isinstance(obj, dict): return result
    keys = [key for key in ('msg_type', 'msgtype') if key in obj]
    result['message_type_key'] = '+'.join(keys) if keys else 'missing'
    kind = obj.get(keys[0]) if len(keys) == 1 else None
    result['message_type'] = kind if kind in ('text', 'post', 'markdown', 'interactive', 'image') else 'other_or_unknown'
    for path in ('content', 'content.text', 'content.post', 'content.markdown', 'content.content', 'text', 'text.content', 'markdown', 'markdown.content', 'markdown.title', 'post', 'post.zh_cn', 'post.zh_cn.content', 'text.text'):
        current = obj
        present = True
        for part in path.split('.'):
            if not isinstance(current, dict) or part not in current: present = False; break
            current = current[part]
        result['known_field_presence'][path] = {'present': present, 'type': ('unknown' if current is UNKNOWN else 'dict' if isinstance(current, dict) else 'list' if isinstance(current, list) else 'str' if isinstance(current, str) else 'other') if present else 'missing'}
    result['top_level_key_count'] = len(obj)
    if contains_unknown(obj):
        result['current_thread_url_present_in_static_payload'] = None
        result['current_link_label_in_static_payload'] = None
    else:
        try: text = json.dumps(obj, ensure_ascii=False)
        except (ValueError, TypeError, RecursionError): text = None
        result['current_thread_url_present_in_static_payload'] = signals(text)['current_thread_url_present'] if text is not None else None
        result['current_link_label_in_static_payload'] = '点击查看完整日报' in text if text is not None else None
    return result


def sources_for_analysis(command):
    found = python_sources(command)
    if found: return found
    # An unquoted heredoc can be shell-expanded. Parse its shape but never claim
    # its string literals are the command's executed values.
    pattern = r"(?m)^.*\bpython(?:3(?:\.[0-9]+)?)?[^\n]*<<\s*([A-Za-z_][A-Za-z0-9_]*)\s*\n(.*?)\n\1(?:\n|$)"
    return [(match.group(2), False) for match in re.finditer(pattern, command, re.S)][:4]


def sender_static_summary(command):
    summaries = []
    for source, shell_literal in sources_for_analysis(command):
        try:
            tree = ast.parse(source)
            need(sum(1 for _ in ast.walk(tree)) <= 12000, 'AST_BOUND')
        except (ValueError, SyntaxError, RecursionError): continue
        aliases = {}; clients = {}; values = {}; requests = {}
        for statement in tree.body:
            if isinstance(statement, ast.Import):
                for entry in statement.names:
                    aliases[entry.asname or entry.name.split('.')[0]] = entry.name if entry.asname else entry.name.split('.')[0]
            elif isinstance(statement, ast.ImportFrom):
                for entry in statement.names: aliases[entry.asname or entry.name] = (statement.module or '') + '.' + entry.name
            simple = isinstance(statement, (ast.Assign, ast.AnnAssign, ast.Expr, ast.Import, ast.ImportFrom))
            usable_values = values if simple else {}
            for node in ast.walk(statement):
                if not isinstance(node, ast.Call) or len(summaries) >= 16: continue
                name = dotted(node.func); root, _, suffix = name.partition('.')
                name = aliases.get(root, root) + ('.'+suffix if suffix else '')
                kwargs = {entry.arg: entry.value for entry in node.keywords if entry.arg}
                client = None; method = 'unknown'; body = None; argument = 'unknown'; construct = False
                if name in ('requests.post', 'httpx.post') or root in clients and name.endswith('.post'):
                    client = name.split('.')[0] if root not in clients else clients[root]; method = 'POST'
                elif name in ('requests.request', 'httpx.request'):
                    client = name.split('.')[0]
                    ok, value = value_or_unknown(node.args[0] if node.args else kwargs.get('method'), usable_values)
                    method = value.upper() if ok and isinstance(value, str) and value.upper() in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE') else 'unknown'
                elif name.endswith('.request') and root in clients and clients[root] == 'http.client':
                    client = 'http.client'
                    ok, value = value_or_unknown(node.args[0] if node.args else kwargs.get('method'), usable_values)
                    method = value.upper() if ok and isinstance(value, str) and value.upper() in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE') else 'unknown'
                    body = kwargs.get('body', node.args[2] if len(node.args) > 2 else None); argument = 'body'
                elif name == 'urllib.request.Request':
                    client = 'urllib.request'; construct = True
                    body = kwargs.get('data', node.args[1] if len(node.args) > 1 else None); argument = 'data'
                    ok, value = value_or_unknown(kwargs.get('method'), usable_values)
                    method = value.upper() if ok and isinstance(value, str) and value.upper() in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE') else 'POST_BY_DATA' if body is not None else 'unknown'
                elif name == 'urllib.request.urlopen':
                    client = 'urllib.request'
                    arg = node.args[0] if node.args else kwargs.get('url')
                    if isinstance(arg, ast.Name) and arg.id in requests:
                        summaries.append(dict(requests[arg.id], construction_only=False, matched_urlopen=True)); continue
                    body = kwargs.get('data', node.args[1] if len(node.args) > 1 else None); argument = 'data'; method = 'POST_BY_DATA' if body is not None else 'unknown'
                if client:
                    if body is None:
                        for key in ('json', 'data', 'content', 'body'):
                            if key in kwargs: body = kwargs[key]; argument = key; break
                    known, value = value_or_unknown(body, usable_values)
                    entry = {'client': client, 'method': method, 'argument_kind': argument, 'construction_only': construct,
                             'static_proposal_not_execution': True, 'shell_literal_source': shell_literal,
                             'conditional_or_function_context': not simple,
                             'payload': message_shape(value, known and shell_literal and simple),
                             'external_payload_files_read': False}
                    summaries.append(entry)
                    if construct and isinstance(statement, ast.Assign) and statement.value is node:
                        for target in statement.targets:
                            if isinstance(target, ast.Name): requests[target.id] = entry
            if isinstance(statement, ast.Assign):
                ok, value = value_or_unknown(statement.value, values)
                for target in statement.targets:
                    if not isinstance(target, ast.Name): continue
                    values.pop(target.id, None)
                    if ok: values[target.id] = value
                    name = dotted(statement.value.func) if isinstance(statement.value, ast.Call) else ''
                    root, _, suffix = name.partition('.'); name = aliases.get(root, root) + ('.'+suffix if suffix else '')
                    if name in ('requests.Session', 'httpx.Client', 'httpx.AsyncClient'): clients[target.id] = name.split('.')[0]
                    elif name in ('http.client.HTTPConnection', 'http.client.HTTPSConnection'): clients[target.id] = 'http.client'
            elif not simple:
                values.clear(); clients.clear(); requests.clear()
    if re.search(r'\bcurl\b', command):
        summaries.append({'client': 'curl', 'method': 'unknown', 'static_proposal_not_execution': True,
                          'data_at_file_syntax_present':bool(re.search(r'(?:--data(?:-binary|-raw)?|-d)\s+[@\x27\"]',command)),
                          'external_payload_files_read':False,
                          'payload':message_shape(None,False)})
    return {'send_candidates': summaries[:16], 'source_fragments':len(sources_for_analysis(command)),
            'external_python_program_referenced':bool(re.search(r'\bpython(?:3(?:\.[0-9]+)?)?[^\n]*\s[^\s]+\.py(?:\s|$)',command)),
            'external_program_contents_read':False,
            'proxy_environment_names_mentioned':bool(re.search(r'\b(?:http_proxy|https_proxy|HTTP_PROXY|HTTPS_PROXY|NO_PROXY|no_proxy)\b',command)),
            'explicit_trust_env_false_syntax':bool(re.search(r'trust_env\s*=\s*False',command)),
            'dynamic_or_external_payload_not_inferred':True}


def classified_error_markers(value):
    # Only output fixed enums. These are text observations, never a diagnosis.
    body = text_content(value).casefold()
    rules = {
        'ENCODING_UTF8_OR_UNICODE': ('utf-8', 'utf8', 'unicodeencodeerror', 'unicodedecodeerror', 'encoding error'),
        'JSON_PARSE_OR_SERIALIZE': ('invalid json', 'json decode', 'jsondecodeerror', 'json parse', 'json格式', 'json 格式', 'unmarshal'),
        'INVALID_MESSAGE_TYPE': ('invalid msg_type', 'invalid msgtype', 'invalid message type', 'msg_type is', 'msgtype is'),
        'MISSING_REQUIRED_FIELD': ('required field', 'missing field', 'parameter missing', '参数缺失', '参数缺少'),
        'EMPTY_MESSAGE_CONTENT': ('content is empty', 'empty content', 'empty message', '内容为空'),
        'CONTENT_LENGTH_LIMIT': ('too long', 'too large', 'length limit', 'exceeds', '长度超', '超过长度', '超出长度'),
        'INVALID_PARAMETER': ('invalid param', 'parameter invalid', '参数错误', '参数不合法', 'bad request'),
        'PROXY_OR_CONNECT_FAILURE': ('proxyerror', 'proxy error', 'connectionreseterror', 'connection refused', 'connectionerror', 'connecttimeout'),
        'AUTH_OR_SIGNATURE_FAILURE': ('invalid token', 'invalid signature', 'unauthorized', 'authentication failed', '签名错误', '鉴权失败'),
        'EXPLICIT_SUCCESS_TEXT': ('success', '成功'),
    }
    result = [name for name, needles in rules.items() if any(needle in body for needle in needles)]
    return {'known_error_marker_classes':result or ['UNCLASSIFIED'], 'marker_presence_not_verified_root_cause':True,
            'raw_error_text_exported':False, 'raw_message_body_exported':False}


def command_summary(call):
    result = _original_command_summary(call)
    args = call.get('args') if isinstance(call.get('args'), dict) else {}
    command = next((args[key] for key in ('command','cmd','bash_command') if isinstance(args.get(key),str)), '')
    result['sender_structure'] = sender_static_summary(command)
    if result['host_receipt_write_proposed']:
        content = next((args[key] for key in ('content','text') if isinstance(args.get(key),str)), '')
        try: projection = json.loads(content)
        except (ValueError,RecursionError): projection = None
        if isinstance(projection,dict):
            callid = projection.get('tool_call_id')
            result['projection_parser_prerequisites'] = {
                'tool_call_id_valid':isinstance(callid,str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,256}',callid) is not None,
                'tool_call_id_type':'string' if isinstance(callid,str) else 'missing' if callid is None else 'other',
                'content_starts_with_accepted_prefix':isinstance(projection.get('content'),str) and projection['content'].startswith(('Successfully presented files','Error:','File delivery incomplete')),
                'artifact_status':projection.get('artifact',{}).get('publication_status') if isinstance(projection.get('artifact'),dict) and projection['artifact'].get('publication_status') in ('success','failed') else 'missing_or_other',
                'caller_projection_not_host_attestation':True}
    return result


def receipt_summary(value):
    result = _original_receipt_summary(value)
    result.update(classified_error_markers(value))
    for obj in objects(value):
        for name in ('conversation_reason','reason'):
            if obj.get(name) in ('HOST_CONVERSATION_IDENTITY_INCOMPLETE','HOST_CONVERSATION_URL_INVALID','HOST_CONVERSATION_URL_CONFLICT','HOST_CONVERSATION_URL_UNAVAILABLE','HOST_PUBLICATION_IDENTITY_INCOMPLETE','HOST_PUBLICATION_NOT_CONFIRMED'):
                result.setdefault('known_delivery_reason_codes',[]).append(obj[name])
    return result


def summarize_events(records):
    need(all(row['seq'] in FIXED_SEQUENCES for row in records),'FIXED_EVENT_SEQUENCE_REQUIRED')
    result = _original_summarize_events(records)
    result['event_window_selection'] = 'FIXED_SEQ_87_90_AND_99_120'
    result['window_may_omit_middle_or_content'] = True
    result['full_run_or_wire_payload_not_observed'] = True
    return result


async def diagnose(conn):
    result={'runs':[],'status':'INCOMPLETE','fixed_run_sha256':FIXED_RUN_SHA256}
    async with conn.transaction(readonly=True,isolation='repeatable_read'):
        need(await conn.fetchval('SHOW transaction_read_only')=='on','DATABASE_READONLY_REQUIRED')
        for sql in ("SET LOCAL statement_timeout='5000ms'","SET LOCAL lock_timeout='500ms'","SET LOCAL idle_in_transaction_session_timeout='10000ms'"): await conn.execute(sql)
        rows=await conn.fetch(RUN_SQL,THREAD);need(len(rows)<=2,'RUN_COUNT_BOUND')
        selected=[row for row in rows if sha(row['run_id'])==FIXED_RUN_SHA256]
        need(len(selected)==1,'FIXED_OPERATOR_RUN_NOT_IN_LATEST_TWO')
        row=selected[0];run=row['run_id'];need(isinstance(run,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',run),'RUN_ID_SHAPE')
        result['runs']=[{'run_id_sha256':sha(run),'created_at':iso(row.get('created_at')),'updated_at':iso(row.get('updated_at')),
                        'status':row.get('status') if row.get('status') in ('success','error','running','interrupted','timeout') else 'other'}]
        events=await conn.fetch(EVENT_SQL,THREAD,run,EVENT_KINDS,FIXED_SEQUENCES)
        result.update(summarize_events([dict(row) for row in events]),status='DELIVERY_TIMELINE_OBSERVED_READONLY',run_selection_basis='EXACT_OPERATOR_RUN_SHA256_BOUND',latest_run_not_proof_of_specific_user_attempt=False)
    return result

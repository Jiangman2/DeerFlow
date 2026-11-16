"""Observe explicit webhook execution and private receipts without sending."""
from pathlib import PurePosixPath
from itertools import islice

_base_word = safe_word
_base_command = command
_base_stage_facts = stage_facts
_base_diagnose = diagnose_thread

INPUT_SQL = """SELECT CASE WHEN octet_length((kwargs_json::jsonb->'input')::text)<=65536
THEN (kwargs_json::jsonb->'input')::text END AS bounded_input
FROM runs WHERE thread_id=$1 AND run_id=$2 LIMIT 1"""
PROMPT_SQL = """SELECT s.user_id,octet_length(s.prompt) AS prompt_bytes,
CASE WHEN octet_length(s.prompt)<=65536 THEN s.prompt END AS bounded_prompt
FROM scheduled_task_runs r JOIN scheduled_tasks s ON s.id=r.task_id
WHERE r.thread_id=$1 ORDER BY r.created_at DESC LIMIT 1"""


def safe_word(value, upper=False):
    if isinstance(value, str) and re.fullmatch(r'WEBHOOK_[A-Z0-9_]{1,80}', value):
        return value
    return _base_word(value, upper)


def target_summary(value):
    if not isinstance(value, str) or len(value) > 2048:
        return {'target_present': False}
    try:
        parsed = urlsplit(value)
        supported = (parsed.scheme == 'https' and parsed.hostname in ('open.w.cnooc.com.cn', 'open.feishu.cn', 'open.larksuite.com')
            and parsed.port in (None, 443) and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment
            and re.fullmatch(r'/open-apis/bot/v2/hook/[A-Za-z0-9_-]{16,256}', parsed.path) is not None
            and not any(char.isspace() for char in value))
        return {'target_present': True, 'target_sha256': sha(value), 'matches_sender_endpoint_policy': supported,
                'https': parsed.scheme == 'https', 'query_present': bool(parsed.query), 'fragment_present': bool(parsed.fragment),
                'v2_hook_path': '/bot/v2/hook/' in parsed.path, 'url_exported': False}
    except ValueError:
        return {'target_present': True, 'target_sha256': sha(value), 'matches_sender_endpoint_policy': False, 'url_exported': False}


def description_targets(text):
    if not isinstance(text, str):
        return {'available': False, 'raw_text_exported': False}
    targets = []
    for match in re.finditer(r'''https?://[^\s<>"'()\[\]`]+''', text):
        value = match.group(0).rstrip('.,;，。；')
        if ('/bot/' in value or '/hook/' in value or '/webhook' in value) and value not in targets:
            targets.append(value)
        if len(targets) == 4:break
    return {'available': True, 'text_bytes': len(text.encode()), 'webhook_url_candidates': [target_summary(v) for v in targets],
            'raw_text_exported': False, 'urls_exported': False}


def command(call):
    result = _base_command(call)
    args = call.get('args') if isinstance(call.get('args'), dict) else {}
    raw = next((args[k] for k in ('command', 'cmd', 'bash_command') if isinstance(args.get(k), str)), '')
    path = next((args[k] for k in ('path', 'file_path', 'file') if isinstance(args.get(k), str)), '')
    if re.search(r'send_webhook\.py(?:["\x27]?\s|$)', raw):
        result['actions'].append('send-webhook')
        result['categories'].append('SKILL_WEBHOOK_SENDER')
    if path.replace('\\', '/').endswith('/delivery/webhook-request.json'):
        result['categories'].append('WEBHOOK_REQUEST_FILE')
        text = next((args[k] for k in ('content', 'text') if isinstance(args.get(k), str)), None)
        if isinstance(text, str) and len(text.encode()) <= 8192:
            try:
                obj = json.loads(text)
                result['webhook_request_shape_valid'] = isinstance(obj, dict) and set(obj) == {'webhook_url'}
                if result['webhook_request_shape_valid']:
                    result['webhook_request_target'] = target_summary(obj['webhook_url'])
            except ValueError:
                result['webhook_request_json_valid'] = False
    return result


def stage_facts(value):
    facts = _base_stage_facts(value)
    for obj in objects(value):
        status, code = safe_word(obj.get('status')), safe_word(obj.get('error_code'), True)
        if not any(isinstance(v, str) and v.startswith('WEBHOOK_') for v in (status, code)):continue
        item = {'status': status, 'error_code': code}
        for key in ('retry_allowed', 'report_generated', 'automatic_retry'):
            if type(obj.get(key)) is bool:item[key] = obj[key]
        for key in ('http_status', 'messages_sent_this_call'):
            if type(obj.get(key)) is int and 0 <= obj[key] <= 999:item[key] = obj[key]
        if obj.get('delivery_status') in ('SENDING', 'SENT', 'FAILED', 'UNKNOWN'):
            item['delivery_status'] = obj['delivery_status']
        codes = obj.get('provider_codes')
        if isinstance(codes, list):item['provider_codes'] = [n for n in codes[:8] if type(n) is int and -2147483648 <= n <= 2147483647]
        facts.append(item)
    return facts[:16]


def command_project(records):
    projects = set()
    for row in records:
        if row.get('event_type') != 'llm.ai.response' or not isinstance(row.get('content'), str):continue
        try:obj = json.loads(row['content'])
        except ValueError:continue
        if not isinstance(obj, dict):continue
        for call in obj.get('tool_calls', [])[:24] if isinstance(obj.get('tool_calls'), list) else []:
            args = call.get('args') if isinstance(call, dict) and isinstance(call.get('args'), dict) else {}
            raw = next((args[k] for k in ('command', 'cmd', 'bash_command') if isinstance(args.get(k), str)), '')
            if 'daily.py' not in raw and 'send_webhook.py' not in raw:continue
            try:tokens = shlex.split(raw)
            except ValueError:continue
            for index, token in enumerate(tokens[:-1]):
                if token == '--workspace':
                    virtual = PurePosixPath(tokens[index + 1])
                    if (str(virtual).startswith('/mnt/user-data/workspace/') and '..' not in virtual.parts
                            and not any(char in str(virtual) for char in '$`\n\r')
                            and len(str(virtual)) <= 1024):projects.add(str(virtual))
    return sorted(projects)


def read_project_file(path, root, limit):
    need(root in path.parents and not path.is_symlink(), 'NAMED_PROJECT_PATH_REQUIRED')
    for parent in (path,) + tuple(path.parents):need(not parent.is_symlink(), 'NAMED_PROJECT_SYMLINK_REJECTED')
    before = path.stat();need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit, 'NAMED_PROJECT_FILE_BOUND')
    raw = path.read_bytes();after = path.stat()
    need((before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns) and len(raw) <= limit,
         'NAMED_PROJECT_FILE_CHANGED')
    return raw


def project_observation(project, root, output, thread):
    out = {'project_path_exported': False, 'project_path_sha256': sha(str(project)), 'status': 'PROJECT_NOT_PRESENT'}
    if not project.is_dir():return out
    out['status'] = 'NAMED_PROJECT_OBSERVED_READONLY'
    result_path = project / 'RESULT.json'
    if result_path.is_file():
        receipt = json.loads(read_project_file(result_path, root, 131072))
        out['result_status'] = safe_word(receipt.get('status'))
        version = receipt.get('version')
        out['result_version'] = version if isinstance(version, str) and re.fullmatch('[0-9A-Za-z_.-]{1,80}', version) else None
        out['result_has_current_thread'] = receipt.get('conversation_thread_id') == thread
        files = receipt.get('files')
        if isinstance(files, list) and len(files) == 4:
            copies = []
            for item in files:
                raw_path = item.get('path') if isinstance(item, dict) else None
                if not isinstance(raw_path, str):continue
                suffix = Path(raw_path).suffix
                if suffix not in ('.html', '.png', '.json', '.txt'):continue
                name = Path(raw_path).name
                file = output / name
                entry = {'extension': suffix, 'published_copy_present': file.is_file()}
                if file.is_file():
                    value = read_project_file(file, output.parent, 67108864)
                    entry['published_copy_matches_receipt'] = len(value) == item.get('bytes') and sha(value) == item.get('sha256')
                copies.append(entry)
            out['published_copies'] = copies
    request_path = project / 'delivery/webhook-request.json'
    out['webhook_request_file_present'] = request_path.is_file()
    if request_path.is_file():
        try:
            request = json.loads(read_project_file(request_path, root, 8192))
            out['webhook_request_shape_valid'] = isinstance(request, dict) and set(request) == {'webhook_url'}
            if out['webhook_request_shape_valid']:out['webhook_request_target'] = target_summary(request['webhook_url'])
        except ValueError:out['webhook_request_json_valid'] = False
    directory = project / 'delivery'
    out['send_journals'] = []
    if directory.is_dir():
        need(not directory.is_symlink(), 'NAMED_PROJECT_SYMLINK_REJECTED')
        names = list(islice(directory.iterdir(), 33));need(len(names) <= 32, 'NAMED_DELIVERY_DIRECTORY_BOUND')
        for path in names:
            if not re.fullmatch(r'webhook-[a-f0-9]{64}\.json', path.name):continue
            journal = json.loads(read_project_file(path, root, 16384))
            item = {'status': journal.get('status') if journal.get('status') in ('SENDING', 'SENT', 'FAILED', 'UNKNOWN') else 'OTHER',
                    'reason': safe_word(journal.get('reason'), True), 'journal_name_sha256': sha(path.name)}
            for key in ('http_status',):
                if type(journal.get(key)) is int and 0 <= journal[key] <= 999:item[key] = journal[key]
            if isinstance(journal.get('provider_codes'), list):
                item['provider_codes'] = [n for n in journal['provider_codes'][:8] if type(n) is int and -2147483648 <= n <= 2147483647]
            value = journal.get('target_sha256')
            if isinstance(value, str) and re.fullmatch('[a-f0-9]{64}', value):item['target_sha256'] = value
            out['send_journals'].append(item)
    out['webhook_token_examined_only_for_target_comparison'] = bool(out['webhook_request_file_present'])
    out['webhook_urls_exported'] = False
    return out


async def diagnose_thread(conn, thread):
    result = await _base_diagnose(conn, thread)
    try:
        async with conn.transaction(readonly=True, isolation='repeatable_read'):
            owner = await conn.fetchval('SELECT user_id FROM threads_meta WHERE thread_id=$1 LIMIT 1', thread)
            need(identifier(owner), 'TRUSTED_THREAD_OWNER_REQUIRED')
            prompt = await conn.fetchrow(PROMPT_SQL, thread)
            if prompt:
                need(prompt['user_id'] == owner, 'SCHEDULE_THREAD_OWNER_MISMATCH')
                result['current_schedule_prompt'] = dict(description_targets(prompt['bounded_prompt']),
                    current_snapshot_not_original_run_proof=True)
            runs = await conn.fetch(RUN_SQL, thread)
            if runs:
                run = runs[0]['run_id'];need(identifier(run), 'RUN_ID_SHAPE')
                row = await conn.fetchrow(INPUT_SQL, thread, run)
                if row and isinstance(row['bounded_input'], str):
                    value = json.loads(row['bounded_input'])
                    messages = value.get('messages', []) if isinstance(value, dict) else []
                    text = '\n'.join(text_content(m.get('content')) for m in messages[:32]
                        if isinstance(m, dict) and m.get('role', m.get('type')) in ('user', 'human'))
                    result['persisted_run_input'] = description_targets(text)
                records = await conn.fetch(EVENT_SQL, thread, run, KINDS)
                projects = command_project(records)
                result['owning_command_project_count'] = len(projects)
                if len(projects) == 1:
                    from deerflow.config.paths import get_paths
                    paths = get_paths()
                    base = paths.sandbox_user_data_dir(thread, user_id=owner)
                    relative = PurePosixPath(projects[0]).relative_to('/mnt/user-data')
                    result['project_delivery'] = project_observation(base / Path(*relative.parts), base, base / 'outputs', thread)
                else:result['project_delivery'] = {'status': 'SINGLE_OWNING_PROJECT_NOT_ESTABLISHED', 'files_read': False}
        result['explicit_webhook_inspection_readonly'] = True
    except Exception as exc:
        result['explicit_webhook_inspection'] = {'status': 'INCOMPLETE', 'error_class': type(exc).__name__,
            'error_code': str(exc) if isinstance(exc, Refused) else 'WEBHOOK_DIAGNOSTIC_REDACTED'}
    return result

"""Small read-only notification snapshot for the newly supplied personal chat."""
PERSONAL_INPUT_SQL = """SELECT
left(kwargs_json::jsonb #>> '{input,messages,0,content}',2048) AS original_input,
left(metadata_json::jsonb->>'scheduled_task_id',64) AS task_id,
left(metadata_json::jsonb->>'scheduled_task_run_id',64) AS occurrence_id
FROM runs WHERE thread_id=$1 AND run_id=$2 AND user_id=$3 LIMIT 1"""
PERSONAL_PROMPT_SQL = """SELECT left(prompt,2048) AS prompt
FROM scheduled_tasks WHERE id=$1 AND user_id=$2 LIMIT 1"""


def reader_facts(value, thread):
    text = value if isinstance(value, str) else ''
    need(len(text.encode('utf-8')) <= 262144, 'PERSONAL_TEXT_BOUND')
    urls = []
    for value in re.findall(r'https://[^\s()<>"\x27]+', text):
        try:
            parsed = urlsplit(value)
            match = re.fullmatch('/reports/energy-daily/' + re.escape(thread) + r'/([0-9]{4}-[0-9]{2}-[0-9]{2})\.html', parsed.path)
            good = (parsed.scheme == 'https' and parsed.hostname in ('cnoocclaw.cnooc.com.cn', 'heai.cnooc.com.cn')
                    and parsed.port in (None, 443) and parsed.username is None and parsed.password is None
                    and not parsed.query and not parsed.fragment and match is not None
                    and dt.date.fromisoformat(match.group(1)).isoformat() == match.group(1))
            if good and value not in urls:
                urls.append(value)
        except (ValueError, TypeError):
            continue
    return {'text_present': bool(text), 'text_bytes': len(text.encode('utf-8')), 'text_sha256': sha(text),
            'valid_current_html_links': urls[:4],
            'reader_label_present': '点击查看日报网页' in text,
            'standalone_reader_lines': sum(bool(re.fullmatch(r'\[点击查看日报网页\]\(https://[^\s()<>]+\)', line.strip())) for line in text.splitlines()),
            'raw_text_exported': False, 'actual_delivery_not_replayed': True}


def explicit_daily(value):
    return isinstance(value, str) and re.match(r'^\s*/energy-market-daily(?:\s|$)', value) is not None


async def diagnose_current_personal(conn, thread):
    out = {'thread_id': thread, 'status': 'INCOMPLETE', 'runs': [], 'occurrences': [], 'outbox': [],
           'notification_ciphertext_read': False, 'original_task_replayed': False,
           'formatter_functions_executed': False, 'project_files_read': False}
    names = ('app/scheduler/work_notification_delivery.py', 'app/scheduler/work_notifications.py',
             'packages/harness/deerflow/persistence/scheduled_task_runs/sql.py')
    out['notification_sources'] = {name: {'bytes': len(raw), 'sha256': sha(raw)} for name in names for raw in [read_source(name)]}
    out['formatter_matches_prior_418_parent'] = all(out['notification_sources'].get(name) == row for name, row in EXPECTED_SOURCES.items())
    async with conn.transaction(readonly=True, isolation='repeatable_read'):
        need(await conn.fetchval('SHOW transaction_read_only') == 'on', 'DATABASE_READONLY_REQUIRED')
        for sql in ("SET LOCAL statement_timeout='5000ms'", "SET LOCAL lock_timeout='500ms'", "SET LOCAL idle_in_transaction_session_timeout='20000ms'"):
            await conn.execute(sql)
        owner = await conn.fetchval('SELECT user_id FROM threads_meta WHERE thread_id=$1 LIMIT 1', thread)
        need(identifier(owner), 'TRUSTED_THREAD_OWNER_REQUIRED')
        runs = await conn.fetch(NOTIFY_RUN_SQL, thread)
        occurrences = await conn.fetch(NOTIFY_OCCURRENCE_SQL, thread)
        need(len(runs) <= 2 and len(occurrences) <= 2, 'PERSONAL_ROW_BOUND')
        for run in runs:
            need(identifier(run['run_id']), 'RUN_ID_SHAPE')
            original = await conn.fetchrow(PERSONAL_INPUT_SQL, thread, run['run_id'], owner)
            need(original is not None, 'RUN_THREAD_OWNER_MISMATCH')
            matching = [o for o in occurrences if o['run_id'] == run['run_id']]
            item = {'run_id_sha256': sha(run['run_id']), 'status': run['status'],
                    'created_at': iso(run['created_at']), 'updated_at': iso(run['updated_at']),
                    'stored_message_bytes': run['message_bytes'],
                    'stored_result_read_complete': run['message_bytes'] is None or run['message_bytes'] <= 262144,
                    'stored_result': reader_facts(run['last_ai_message'], thread),
                    'original_input_explicit_daily': explicit_daily(original['original_input']),
                    'run_metadata_matches_occurrence': any(original['task_id'] == o['task_id'] and original['occurrence_id'] == o['id'] for o in matching)}
            events = await conn.fetch(NOTIFY_EVENT_SQL, thread, run['run_id'])
            need(len(events) <= 8, 'PERSONAL_EVENT_BOUND')
            item['ai_events_read'] = len(events)
            item['ai_events_exceeding_read_bound'] = sum(e.get('bytes', 0) > 262144 for e in events)
            finals = []
            for event in events:
                if not isinstance(event['content'], str):
                    continue
                try:
                    message = json.loads(event['content'])
                except (ValueError, RecursionError):
                    continue
                if isinstance(message, dict) and not message.get('tool_calls'):
                    finals.append(dict(seq=event['seq'], **reader_facts(text_content(message.get('content')), thread)))
            item['last_final_ai_candidates'] = finals[:2]
            out['runs'].append(item)
        for occurrence in occurrences:
            task = await conn.fetchrow(NOTIFY_TASK_SQL, occurrence['task_id'])
            need(task is not None and task['user_id'] == owner, 'SCHEDULE_THREAD_OWNER_MISMATCH')
            prompt = await conn.fetchrow(PERSONAL_PROMPT_SQL, occurrence['task_id'], owner)
            item = {'id_sha256': sha(occurrence['id']), 'status': occurrence['status'], 'trigger': occurrence['trigger'],
                    'created_at': iso(occurrence['created_at']), 'finished_at': iso(occurrence['finished_at']),
                    'native_notification_configured': bool(task['notification_configured']),
                    'connection_id_present': bool(task['connection_id']), 'binding_key_present': bool(task['binding_key']),
                    'task_status': task['status'], 'current_prompt_explicit_daily': explicit_daily(prompt['prompt'] if prompt else None)}
            if identifier(task['connection_id']):
                connection = await conn.fetchrow(NOTIFY_CONNECTION_SQL, task['connection_id'], owner)
                item['connection'] = {k: iso(connection[k]) for k in ('status', 'provider', 'updated_at', 'last_seen_at', 'notification_target_present')} if connection else {'status': 'NOT_PRESENT_FOR_OWNER'}
            out['occurrences'].append(item)
            records = await conn.fetch(NOTIFY_OUTBOX_SQL, sha(occurrence['id']), sha(owner))
            need(len(records) <= 4, 'PERSONAL_OUTBOX_BOUND')
            for record in records:
                out['outbox'].append({'occurrence_id_sha256': sha(occurrence['id']), 'status': record['status'], 'revision': record['revision'],
                    'updated_at_utc': dt.datetime.fromtimestamp(record['updated_at'], dt.timezone.utc).isoformat(),
                    'expires_at_utc': dt.datetime.fromtimestamp(record['expires_at'], dt.timezone.utc).isoformat(),
                    'payload_retained': record['payload_retained']})
        out['scheduled_occurrence_present'] = bool(occurrences)
        out['status'] = 'TASK_DURABLE_STATE_OBSERVED_READONLY'
    return out

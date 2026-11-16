"""Distinguish scheduler notifications from channel-chat delivery for one chat."""
_before_personal_delivery = diagnose_thread
_personal_stage_facts = stage_facts


def stage_facts(value):
    result = _personal_stage_facts(value)
    for obj in objects(value):
        code = safe_word(obj.get('error_code'), True)
        if not isinstance(code, str) or not code.startswith('WEBHOOK_'):continue
        item = {'error_code': code}
        stage = safe_word(obj.get('stage'))
        if stage:item['stage'] = stage
        if obj.get('file_extension') in ('.html', '.png', '.json', '.txt'):item['file_extension'] = obj['file_extension']
        if type(obj.get('provider_request_started')) is bool:item['provider_request_started'] = obj['provider_request_started']
        result.append(item)
    return result[:24]

CHANNEL_ROUTE_SQL = """SELECT m.provider,m.created_at,m.updated_at,
(m.external_topic_id<>'') AS topic_present,c.status AS connection_status,
(c.owner_user_id=m.owner_user_id) AS connection_owner_matches
FROM channel_conversations m LEFT JOIN channel_connections c ON c.id=m.connection_id
WHERE m.thread_id=$1 AND m.owner_user_id=$2 ORDER BY m.updated_at DESC LIMIT 5"""


async def diagnose_thread(conn, thread):
    result = await _before_personal_delivery(conn, thread)
    try:
        async with conn.transaction(readonly=True, isolation='repeatable_read'):
            owner = await conn.fetchval('SELECT user_id FROM threads_meta WHERE thread_id=$1 LIMIT 1', thread)
            need(identifier(owner), 'TRUSTED_THREAD_OWNER_REQUIRED')
            rows = await conn.fetch(CHANNEL_ROUTE_SQL, thread, owner)
            need(len(rows) <= 5, 'CHANNEL_ROUTE_ROW_BOUND')
            result['work_channel_route'] = {
                'mappings': [{'provider': safe_word(row['provider']),
                              'connection_status': safe_word(row['connection_status']),
                              'connection_owner_matches': row['connection_owner_matches'] is True,
                              'topic_present': row['topic_present'] is True,
                              'created_at': iso(row['created_at']), 'updated_at': iso(row['updated_at'])} for row in rows],
                'limit': 5, 'external_chat_ids_exported': False, 'mapping_is_not_actual_send_proof': True}
        source = read_source('app/channels/manager.py')
        tree = ast.parse(source)
        finals = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                  and isinstance(node.func, ast.Name) and node.func.id == 'OutboundMessage'
                  and any(k.arg == 'is_final' and isinstance(k.value, ast.Constant) and k.value.value is True for k in node.keywords)]
        result['channel_manager_source'] = {'bytes': len(source), 'sha256': sha(source),
            'final_outbound_sites': len(finals),
            'final_sites_passing_response_text': sum(any(k.arg == 'text' and isinstance(k.value, ast.Name)
                and k.value.id == 'response_text' for k in node.keywords) for node in finals),
            'reader_label_literal_present': '点击查看日报网页' in source.decode('utf-8'),
            'source_observation_not_actual_delivery_proof': True, 'application_imported': False}
    except Exception as exc:
        result['personal_delivery_inspection'] = {'status': 'INCOMPLETE', 'error_class': type(exc).__name__,
            'error_code': str(exc) if isinstance(exc, Refused) else 'PERSONAL_DELIVERY_READ_REDACTED'}
    return result

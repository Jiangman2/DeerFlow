def observe(payload):
    need(isinstance(payload, dict) and payload.get('mode') in ('qualify', 'verify'), 'CHILD_MODE')
    need(set(payload) == ({'mode', 'config', 'candidate', 'sources'} if payload['mode'] == 'qualify'
                          else {'mode', 'config', 'sources'}), 'CHILD_PAYLOAD_KEYS')
    expected = payload['sources']
    need(isinstance(expected, dict) and set(expected) == set(SOURCE_PATHS), 'SOURCE_SET_CHANGED')
    raw = bounded_read(CONFIG, 65536)
    check_config(raw, payload['config'])
    need(payload['config']['max_concurrent_runs'] == 32 and payload['config']['multi_instance'] is True, 'CURRENT_CONCURRENCY_NOT_PRESERVED')
    versions = {name: importlib.metadata.version(name) for name in PACKAGES}
    need(versions == PACKAGES, 'GATEWAY_LIBRARY_VERSIONS_CHANGED')
    rows, total = {}, 0
    for path in sorted(SOURCE_PATHS):
        content = bounded_read(path, 1048576)
        total += len(content)
        need(total <= 4194304, 'SOURCE_TOTAL_BOUND')
        row = {'bytes': len(content), 'sha256': digest(content)}
        need(row == expected[path], 'FIXED_SOURCE_HASH_CHANGED')
        rows[path] = row
    target = '/app/backend/app/scheduler/work_notification_delivery.py'
    code = bounded_read(target, 65536)
    if payload['mode'] == 'qualify':
        asset = payload['candidate']
        need(set(asset) == {'base64', 'bytes', 'sha256'}, 'CANDIDATE_KEYS')
        code = base64.b64decode(asset['base64'], validate=True)
        need(0 < len(code) <= 65536 and len(code) == asset['bytes'] and digest(code) == asset['sha256'], 'CANDIDATE_HASH')
    compile(code, '<notification-candidate>', 'exec')
    cases = qualify_functions(code)
    need(bounded_read(CONFIG, 65536) == raw, 'CONFIG_READBACK_CHANGED')
    return {'schema': 'personal-notification-qualification-child.v1', 'status': 'PERSONAL_NOTIFICATION_READONLY_QUALIFIED',
            'application_imported': False, 'database_access': False, 'persistent_files_modified': False,
            'provider_requests': 0, 'work_messages_sent': 0, 'credentials_exported': False,
            'raw_configuration_exported': False, 'config_sha256': digest(raw),
            'scheduler_recursion_limit': 10000, 'max_recursion_limit': 10000, 'max_concurrent_runs': 32,
            'scheduler_multi_instance': True, 'versions': versions, 'source_files_verified': len(rows),
            'source_hashes': rows, 'candidate_sha256': digest(code), 'isolated_notification_cases': cases,
            'postgres_query_compiled': True, 'loaded_application_modules_verified': False, 'stable_readback': True}

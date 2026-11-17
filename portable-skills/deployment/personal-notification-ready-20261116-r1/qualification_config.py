def check_config(raw, expected):
    need(len(raw) == expected['bytes'] and digest(raw) == expected['sha256'], 'CONFIG_HASH_CHANGED')
    parsed = parse_yaml(raw)
    scheduler = parsed.get('scheduler')
    need(isinstance(scheduler, dict), 'SCHEDULER_MAPPING_REQUIRED')
    need(type(scheduler.get('recursion_limit')) is int and scheduler['recursion_limit'] == 10000, 'SCHEDULER_BUDGET_CHANGED')
    need(type(parsed.get('max_recursion_limit')) is int and parsed['max_recursion_limit'] == 10000, 'MAX_RECURSION_LIMIT_CHANGED')
    need(type(scheduler.get('max_concurrent_runs')) is int and scheduler['max_concurrent_runs'] == expected['max_concurrent_runs'], 'CONCURRENCY_CHANGED')
    need(type(scheduler.get('multi_instance')) is bool and scheduler['multi_instance'] is expected['multi_instance'], 'MULTI_INSTANCE_MODE_CHANGED')
    ownership = parsed.get('run_ownership')
    need(isinstance(ownership, dict) and ownership.get('heartbeat_enabled') is True, 'RUN_OWNERSHIP_HEARTBEAT_REQUIRED')
    return parsed

"""Read each explicitly named sender project in the same trusted owner bucket."""
_single_project_diagnosis = diagnose_thread


async def diagnose_thread(conn, thread):
    result = await _single_project_diagnosis(conn, thread)
    if result.get('project_delivery', {}).get('status') != 'SINGLE_OWNING_PROJECT_NOT_ESTABLISHED':return result
    try:
        async with conn.transaction(readonly=True, isolation='repeatable_read'):
            owner = await conn.fetchval('SELECT user_id FROM threads_meta WHERE thread_id=$1 LIMIT 1', thread)
            need(identifier(owner), 'TRUSTED_THREAD_OWNER_REQUIRED')
            runs = await conn.fetch(RUN_SQL, thread)
            if not runs:return result
            run = runs[0]['run_id'];need(identifier(run), 'RUN_ID_SHAPE')
            records = await conn.fetch(EVENT_SQL, thread, run, KINDS)
            projects = command_project(records)
            need(0 < len(projects) <= 4, 'NAMED_SENDER_PROJECT_COUNT_BOUND')
            from deerflow.config.paths import get_paths
            base = get_paths().sandbox_user_data_dir(thread, user_id=owner)
            observed = []
            for path in projects:
                relative = PurePosixPath(path).relative_to('/mnt/user-data')
                try:entry = project_observation(base / Path(*relative.parts), base, base / 'outputs', thread)
                except Exception as exc:
                    entry = {'project_path_sha256': sha(str(base / Path(*relative.parts))), 'status': 'INCOMPLETE',
                             'error_class': type(exc).__name__, 'error_code': str(exc) if isinstance(exc, Refused) else 'PROJECT_READ_REDACTED'}
                observed.append(entry)
            result['project_delivery_candidates'] = observed
            result['each_project_explicit_in_sender_commands'] = True
            result['project_selected_for_retry_or_mutation'] = False
    except Exception as exc:
        result['multiple_sender_project_inspection'] = {'status': 'INCOMPLETE', 'error_class': type(exc).__name__,
            'error_code': str(exc) if isinstance(exc, Refused) else 'PROJECT_READ_REDACTED'}
    return result

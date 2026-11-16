"""Select the sender's own project and inspect ownership, without chmod/send."""
_all_command_projects = command_project
_delivery_observation = project_observation


def command_project(records):
    sender_records = []
    for row in records:
        if row.get('event_type') != 'llm.ai.response' or not isinstance(row.get('content'), str):continue
        try:value = json.loads(row['content'])
        except ValueError:continue
        calls = value.get('tool_calls') if isinstance(value, dict) else None
        if not isinstance(calls, list):continue
        selected = []
        for call in calls[:24]:
            if not isinstance(call, dict):continue
            args = call.get('args') if isinstance(call.get('args'), dict) else {}
            raw = next((args[key] for key in ('command', 'cmd', 'bash_command') if isinstance(args.get(key), str)), '')
            if re.search(r'send_webhook\.py(?:["\x27]?\s|$)', raw):selected.append(call)
        if selected:sender_records.append(dict(row, content=json.dumps({'tool_calls': selected})))
    return _all_command_projects(sender_records) if sender_records else _all_command_projects(records)


def path_metadata(path, root, role):
    need(path == root or root in path.parents, 'NAMED_PROJECT_PATH_REQUIRED')
    item = {'role': role, 'comparison_uid': 1000, 'comparison_gid': 1000,
            'access_basis': 'POSIX_MODE_BITS_NOT_ACL_OR_EFFECTIVE_ACCESS', 'path_exported': False}
    try:
        info = path.lstat()
        need(not stat.S_ISLNK(info.st_mode), 'NAMED_PROJECT_SYMLINK_REJECTED')
        for parent in path.parents:need(not parent.is_symlink(), 'NAMED_PROJECT_SYMLINK_REJECTED')
        item.update(present=True, uid=info.st_uid, gid=info.st_gid, mode=oct(stat.S_IMODE(info.st_mode)),
                    regular=stat.S_ISREG(info.st_mode), directory=stat.S_ISDIR(info.st_mode),
                    owner_matches_uid1000=info.st_uid == 1000,
                    chmod_requires_owner_or_privilege=info.st_uid != 1000)
        bits = (info.st_mode >> (6 if info.st_uid == 1000 else 3 if info.st_gid == 1000 else 0)) & 7
        item.update(mode_readable_by_uid1000=bool(bits & 4), mode_writable_by_uid1000=bool(bits & 2),
                    mode_searchable_by_uid1000=bool(bits & 1))
    except FileNotFoundError:item['present'] = False
    return item


def project_observation(project, root, output, thread):
    result = _delivery_observation(project, root, output, thread)
    roles = [('user_data', root), ('project', project), ('result', project / 'RESULT.json'),
             ('delivery_directory', project / 'delivery'), ('webhook_request', project / 'delivery/webhook-request.json'),
             ('published_output_directory', output)]
    result['file_ownership'] = [path_metadata(path, root, role) for role, path in roles]
    result['permission_probe_called_sender'] = False
    result['permission_probe_changed_modes'] = False
    result['project_selection_basis'] = 'UNIQUE_LITERAL_WORKSPACE_OF_OBSERVED_SEND_WEBHOOK_COMMANDS'
    return result

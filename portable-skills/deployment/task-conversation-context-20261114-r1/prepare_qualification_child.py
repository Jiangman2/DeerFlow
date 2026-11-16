"""Build a read-only, no-application-import qualification child template."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PARENT = ROOT.parent / 'scheduler-budget-20261113-r5/qualify_budget_child.py'


def replace_function(source, name, replacement):
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == name)
    lines = source.splitlines(True)
    return ''.join(lines[:node.lineno - 1]) + replacement.rstrip() + '\n' + ''.join(lines[node.end_lineno:])


def main():
    source = PARENT.read_text(encoding='utf-8')
    source = source.replace('import base64\n', 'import ast\nimport base64\nimport builtins\nimport re\nimport shlex\nfrom types import SimpleNamespace\nfrom urllib.parse import urlsplit\n')
    source = source.replace("SOURCE_PATHS = frozenset((", "TOOLS_PATH = '/app/backend/packages/harness/deerflow/sandbox/tools.py'\nHELPER_PATH = '/app/backend/packages/harness/deerflow/utils/conversation_url.py'\nSOURCE_PATHS = frozenset((\n    TOOLS_PATH,")
    source = source.replace('scheduler-budget-qualification-child.v1', 'task-conversation-context-qualification-child.v1')
    source = source.replace('SCHEDULER_BUDGET_READONLY_QUALIFIED', 'TASK_CONTEXT_READONLY_QUALIFIED')
    source = replace_function(source, 'observe', '''def synthetic_context_checks(tool_source, helper_source):
    """Run exact source functions with synthetic identity and transport only.

    No application package is imported, no real sandbox is acquired, and no
    command is executed. Full-module behavior is separately tested offline.
    """
    compile(tool_source, TOOLS_PATH, 'exec', dont_inherit=True)
    compile(helper_source, HELPER_PATH, 'exec', dont_inherit=True)
    helper_names = {'validated_web_origin', 'current_conversation_url'}
    tool_names = {'_is_windows', '_channel_identity_prefix', '_conversation_context_prefix', 'bash_tool'}
    helper_tree, tools_tree = ast.parse(helper_source), ast.parse(tool_source)
    helper_nodes = [node for node in helper_tree.body if isinstance(node, ast.FunctionDef) and node.name in helper_names]
    tool_nodes = [node for node in tools_tree.body if isinstance(node, ast.FunctionDef) and node.name in tool_names]
    need({node.name for node in helper_nodes} == helper_names and {node.name for node in tool_nodes} == tool_names, 'CONTEXT_SOURCE_FUNCTIONS_REQUIRED')
    for node in helper_nodes + tool_nodes:
        node.decorator_list = []
    def valid_thread(value):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value):
            raise ValueError('synthetic invalid thread')
        return value
    origin = 'https://context-qualification.invalid'
    fake_os = SimpleNamespace(name='posix', getenv=lambda key, default=None: {'DEER_FLOW_CONVERSATION_WEB_URL': origin}.get(key, default))
    global_scope = {'os': fake_os, 're': re, 'urlsplit': urlsplit, 'shlex': shlex, 'Runtime': object,
        'CHANNEL_USER_ID_ENV': 'DEERFLOW_CHANNEL_USER_ID', '_CHANNEL_USER_ID_CONTEXT_KEY': 'channel_user_id',
        '_CHANNEL_USER_ID_MAX_LEN': 256, 'CONVERSATION_URL_ENV': 'DEERFLOW_CONVERSATION_URL',
        'validate_thread_id': valid_thread, 'get_config': lambda: {}, 'get_app_config': lambda: SimpleNamespace(sandbox=None)}
    captured = []
    sandbox = SimpleNamespace(execute_command=lambda command, **kwargs: captured.append({'command': command, **kwargs}) or 'synthetic-ok')
    def limited_import(name, *args, **kwargs):
        need(name == 'deerflow.config.app_config', 'SYNTHETIC_IMPORT_SCOPE')
        return SimpleNamespace(get_app_config=lambda: SimpleNamespace(sandbox=None))
    global_scope.update({'__builtins__': dict(vars(builtins), __import__=limited_import),
        'ensure_sandbox_initialized': lambda runtime: sandbox, 'read_active_secrets': lambda context: None,
        '_github_env_from_runtime': lambda runtime: None, 'is_local_sandbox': lambda runtime: runtime.local,
        'is_host_bash_allowed': lambda: True, 'ensure_thread_directories_exist': lambda runtime: None,
        'get_thread_data': lambda runtime: {}, 'validate_local_bash_command_paths': lambda command, data: None,
        'replace_virtual_paths_in_command': lambda command, data: command,
        '_apply_cwd_prefix': lambda command, data: command, '_truncate_bash_output': lambda output, maximum: output,
        'mask_secret_values': lambda output, env: output, 'mask_local_paths_in_output': lambda output, data: output,
        'VIRTUAL_PATH_PREFIX': '/mnt/user-data', 'SandboxError': type('SyntheticSandboxError', (Exception,), {}),
        '_sanitize_error': lambda error, runtime: type(error).__name__})
    for node in helper_nodes + tool_nodes:
        module = ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[]))
        exec(compile(module, '<isolated-fixed-source-function>', 'exec'), global_scope)
    prefix = 'export DEERFLOW_CONVERSATION_URL=' + origin + '/workspace/chats/context-check; '
    cases = 0
    for local in (False, True):
        runtime = SimpleNamespace(context={'thread_id': 'context-check'}, config={}, local=local)
        need(global_scope['bash_tool'](runtime, 'true') == 'synthetic-ok', 'SYNTHETIC_TRANSPORT_FAILED')
        call = captured[-1]
        need(call['command'].startswith(prefix) and call['env'] is None, 'SYNTHETIC_CONTEXT_TRANSPORT_CHANGED')
        cases += 1
    for thread in (None, '../invalid'):
        runtime = SimpleNamespace(context={'thread_id': thread}, config={}, local=False)
        need(global_scope['_conversation_context_prefix'](runtime) == 'unset DEERFLOW_CONVERSATION_URL; ', 'SYNTHETIC_STALE_CONTEXT_NOT_CLEARED')
        cases += 1
    fake_os.name = 'nt'
    need(global_scope['bash_tool'](SimpleNamespace(context={'thread_id': 'context-check'}, config={}, local=True), 'true') == 'synthetic-ok'
         and captured[-1]['command'] == 'true', 'SYNTHETIC_WINDOWS_COMMAND_CHANGED')
    return cases + 1


def observe(payload):
    need(isinstance(payload, dict) and payload.get('mode') in ('qualify', 'verify'), 'CHILD_MODE')
    need(set(payload) == ({'mode', 'config', 'candidate', 'sources'} if payload['mode'] == 'qualify'
                          else {'mode', 'config', 'sources'}), 'CHILD_PAYLOAD_KEYS')
    expected_sources = payload['sources']
    need(isinstance(expected_sources, dict) and set(expected_sources) == SOURCE_PATHS, 'SOURCE_SET_CHANGED')
    raw = bounded_read(CONFIG, 65536)
    check_config(raw, payload['config'])
    need(payload['config']['scheduler_recursion_limit'] == 10000, 'EXISTING_BUDGET_NOT_PRESERVED')
    result = {'schema': 'task-conversation-context-qualification-child.v1', 'status': 'INCOMPLETE',
              'application_imported': False, 'provider_requests': 0, 'database_access': False,
              'persistent_files_modified': False, 'raw_configuration_exported': False,
              'credentials_exported': False, 'config_sha256': digest(raw),
              'scheduler_recursion_limit': 10000, 'max_recursion_limit': 10000,
              'isolated_source_functions_not_loaded_application_modules': True, 'commands_executed': 0, 'work_messages_sent': 0}
    versions = {name: importlib.metadata.version(name) for name in PACKAGES}
    need(versions == PACKAGES, 'GATEWAY_LIBRARY_VERSIONS_CHANGED')
    result['versions'] = versions
    rows, contents, total = {}, {}, 0
    for path in sorted(SOURCE_PATHS):
        content = bounded_read(path, 1048576)
        total += len(content)
        need(total <= 4194304, 'SOURCE_TOTAL_BOUND')
        row = {'bytes': len(content), 'sha256': digest(content)}
        need(row == expected_sources[path], 'FIXED_SOURCE_HASH_CHANGED')
        rows[path], contents[path] = row, content
    target = contents[TOOLS_PATH]
    if payload['mode'] == 'qualify':
        candidate = payload['candidate']
        need(set(candidate) == {'base64', 'bytes', 'sha256'}, 'CANDIDATE_KEYS')
        target = base64.b64decode(candidate['base64'], validate=True)
        need(len(target) == candidate['bytes'] and digest(target) == candidate['sha256'] and len(target) <= 131072, 'CANDIDATE_HASH')
    checks = synthetic_context_checks(target, contents[HELPER_PATH])
    need(bounded_read(CONFIG, 65536) == raw, 'CONFIG_READBACK_CHANGED')
    need(all(bounded_read(path, 1048576) == contents[path] for path in SOURCE_PATHS), 'SOURCE_READBACK_CHANGED')
    result.update(status='TASK_CONTEXT_READONLY_QUALIFIED', source_files_verified=len(rows), source_hashes=rows,
                  stable_readback=True, context_injection_synthetic_checks_accepted=True, synthetic_context_checks=checks)
    return result
''')
    compile(source, '<context-qualification-child>', 'exec')
    (ROOT / 'qualify_context_child.py').write_text(source, encoding='utf-8', newline='\n')
    print('READONLY_QUALIFICATION_CHILD_PREPARED')


if __name__ == '__main__':
    main()

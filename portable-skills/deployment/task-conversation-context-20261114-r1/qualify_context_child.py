"""Read-only YAML/source qualification in an existing Gateway; never import app."""
import ast
import base64
import builtins
import re
import shlex
from types import SimpleNamespace
from urllib.parse import urlsplit
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import stat
import sys

CONFIG = '/app/backend/config.yaml'
TOOLS_PATH = '/app/backend/packages/harness/deerflow/sandbox/tools.py'
HELPER_PATH = '/app/backend/packages/harness/deerflow/utils/conversation_url.py'
SOURCE_PATHS = frozenset((
    TOOLS_PATH,
    '/app/backend/packages/harness/deerflow/config/app_config.py',
    '/app/backend/packages/harness/deerflow/config/scheduler_config.py',
    '/app/backend/app/gateway/services.py',
    '/app/backend/packages/harness/deerflow/agents/lead_agent/agent.py',
    '/app/backend/app/channels/manager.py',
    '/app/backend/app/integrations/work/notifications.py',
    '/app/backend/app/scheduler/work_notification_delivery.py',
    '/app/backend/app/scheduler/work_notifications.py',
    '/app/backend/packages/harness/deerflow/tools/builtins/present_file_tool.py',
    '/app/backend/packages/harness/deerflow/utils/conversation_url.py',
    '/app/backend/app/gateway/routers/uploads.py',
    '/app/backend/app/gateway/upload_document_memory_guard.py',
    '/etc/deerflow-openssl/openssl.cnf',
))
PACKAGES = {'langchain': '1.2.15', 'langchain-core': '1.3.3', 'langgraph': '1.1.9'}


class Refused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def audit(event, args):
    if event == 'open':
        mode = args[1] if len(args) > 1 else None
        flags = args[2] if len(args) > 2 else 0
        need(not (isinstance(mode, str) and any(x in mode for x in 'wax+')),
             'CHILD_WRITE_BLOCKED')
        need(not (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)),
             'CHILD_WRITE_BLOCKED')
    if event.startswith(('socket.', 'subprocess.', 'os.exec', 'os.spawn')) or event in (
        'os.system', 'os.remove', 'os.rename', 'os.mkdir', 'os.rmdir', 'os.chmod', 'os.chown', 'os.link', 'os.symlink',
    ):
        raise Refused('CHILD_SIDE_EFFECT_BLOCKED')


def bounded_read(path, limit):
    need(path == CONFIG or path in SOURCE_PATHS, 'CHILD_PATH_SCOPE')
    target = Path(path)
    # A directory-projected ConfigMap has Kubernetes' own ..data symlink.
    # It is permitted only inside this fixed OpenSSL projection, not elsewhere.
    if path == '/etc/deerflow-openssl/openssl.cnf':
        resolved = target.resolve(strict=True)
        need(Path('/etc/deerflow-openssl') in resolved.parents, 'OPENSSL_LINK_SCOPE')
        target = resolved
    else:
        need(not target.is_symlink(), 'FIXED_FILE_SYMLINK')
    fd = os.open(str(target), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= limit, 'FIXED_FILE_BOUND')
        raw = os.read(fd, limit + 1)
        after = os.fstat(fd)
        signature = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        need(len(raw) == before.st_size and signature(before) == signature(after) == signature(target.stat()),
             'FIXED_FILE_CHANGED')
        return raw
    finally:
        os.close(fd)


def parse_yaml(raw):
    import yaml

    class StrictLoader(yaml.SafeLoader):
        pass

    def mapping(loader, node, deep=False):
        need(node.tag == 'tag:yaml.org,2002:map', 'YAML_MAPPING_TAG')
        result = {}
        for key_node, value_node in node.value:
            need(key_node.tag != 'tag:yaml.org,2002:merge', 'YAML_MERGE_REFUSED')
            key = loader.construct_object(key_node, deep=deep)
            need(isinstance(key, str) and key not in result, 'YAML_DUPLICATE_OR_NONSTRING_KEY')
            result[key] = loader.construct_object(value_node, deep=deep)
        return result

    StrictLoader.add_constructor('tag:yaml.org,2002:map', mapping)
    # No aliases: avoids cyclic comparison and implicit edits through shared nodes.
    class NoAliases(StrictLoader):
        def compose_node(self, parent, index):
            need(not self.check_event(yaml.AliasEvent), 'YAML_ALIAS_REFUSED')
            return super().compose_node(parent, index)

    parsed = yaml.load(raw.decode('utf-8'), Loader=NoAliases)
    need(isinstance(parsed, dict), 'YAML_ROOT_MAPPING_REQUIRED')
    return parsed


def check_config(raw, expected):
    need(len(raw) == expected['bytes'] and digest(raw) == expected['sha256'], 'CONFIG_HASH_CHANGED')
    parsed = parse_yaml(raw)
    scheduler = parsed.get('scheduler')
    need(isinstance(scheduler, dict), 'SCHEDULER_MAPPING_REQUIRED')
    budget = scheduler.get('recursion_limit')
    maximum = parsed.get('max_recursion_limit')
    need(type(budget) is int and budget == expected['scheduler_recursion_limit'], 'SCHEDULER_BUDGET_CHANGED')
    need(type(maximum) is int and maximum == 10000, 'MAX_RECURSION_LIMIT_CHANGED')
    return parsed


def synthetic_context_checks(tool_source, helper_source):
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


def main():
    result = {'schema': 'task-conversation-context-qualification-child.v1', 'status': 'INCOMPLETE',
              'application_imported': False, 'provider_requests': 0, 'database_access': False,
              'persistent_files_modified': False, 'credentials_exported': False, 'raw_errors_exported': False}
    try:
        raw = sys.stdin.buffer.read(262145)
        need(0 < len(raw) <= 262144, 'CHILD_INPUT_BOUND')
        payload = json.loads(raw)
        sys.addaudithook(audit)
        result = observe(payload)
    except Exception as exc:
        result['error_class'] = type(exc).__name__
        result['error_code'] = str(exc) if isinstance(exc, Refused) else 'CHILD_QUALIFICATION_FAILED'
    print(json.dumps(result, sort_keys=True), flush=True)
    return 0 if result['status'] == 'TASK_CONTEXT_READONLY_QUALIFIED' else 1


if __name__ == '__main__':
    sys.exit(main())

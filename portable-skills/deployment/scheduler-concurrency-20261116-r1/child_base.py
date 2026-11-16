"""Read-only YAML/source qualification in an existing Gateway; never import app."""
import base64
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import stat
import sys

CONFIG = '/app/backend/config.yaml'
SOURCE_PATHS = frozenset((
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


def observe(payload):
    need(isinstance(payload, dict) and payload.get('mode') in ('qualify', 'verify'), 'CHILD_MODE')
    need(set(payload) == ({'mode', 'config', 'candidate', 'sources'} if payload['mode'] == 'qualify'
                          else {'mode', 'config', 'sources'}), 'CHILD_PAYLOAD_KEYS')
    expected_sources = payload['sources']
    need(isinstance(expected_sources, dict) and set(expected_sources) == SOURCE_PATHS, 'SOURCE_SET_CHANGED')
    raw = bounded_read(CONFIG, 65536)
    before = check_config(raw, payload['config'])
    result = {'schema': 'scheduler-budget-qualification-child.v1', 'status': 'INCOMPLETE',
              'application_imported': False, 'provider_requests': 0, 'database_access': False,
              'persistent_files_modified': False, 'raw_configuration_exported': False,
              'credentials_exported': False, 'config_sha256': digest(raw),
              'scheduler_recursion_limit': payload['config']['scheduler_recursion_limit'],
              'max_recursion_limit': 10000}
    if payload['mode'] == 'qualify':
        candidate = payload['candidate']
        need(set(candidate) == {'base64', 'bytes', 'sha256', 'scheduler_recursion_limit'}, 'CANDIDATE_KEYS')
        next_raw = base64.b64decode(candidate['base64'], validate=True)
        after = check_config(next_raw, candidate)
        after['scheduler']['recursion_limit'] = 300
        need(before == after and before['scheduler']['recursion_limit'] == 300, 'YAML_SEMANTIC_SCOPE_EXCEEDED')
        need(candidate['scheduler_recursion_limit'] == 10000, 'CANDIDATE_BUDGET_REQUIRED')
        result.update(candidate_sha256=digest(next_raw), candidate_scheduler_recursion_limit=10000,
                      semantic_change_only_scheduler_recursion_limit=True)
    versions = {name: importlib.metadata.version(name) for name in PACKAGES}
    need(versions == PACKAGES, 'GATEWAY_LIBRARY_VERSIONS_CHANGED')
    result['versions'] = versions
    rows, total = {}, 0
    for path in sorted(SOURCE_PATHS):
        content = bounded_read(path, 1048576)
        total += len(content)
        need(total <= 4194304, 'SOURCE_TOTAL_BOUND')
        row = {'bytes': len(content), 'sha256': digest(content)}
        need(row == expected_sources[path], 'FIXED_SOURCE_HASH_CHANGED')
        rows[path] = row
    need(bounded_read(CONFIG, 65536) == raw, 'CONFIG_READBACK_CHANGED')
    result.update(status='SCHEDULER_BUDGET_READONLY_QUALIFIED', source_files_verified=len(rows),
                  source_hashes=rows, stable_readback=True)
    return result


def main():
    result = {'schema': 'scheduler-budget-qualification-child.v1', 'status': 'INCOMPLETE',
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
    return 0 if result['status'] == 'SCHEDULER_BUDGET_READONLY_QUALIFIED' else 1


if __name__ == '__main__':
    sys.exit(main())

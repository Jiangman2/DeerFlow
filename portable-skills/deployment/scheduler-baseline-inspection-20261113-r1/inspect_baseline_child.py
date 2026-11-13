"""Fixed config integers and source hashes; no application import or writes."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import stat
import sys

CONFIG = '/app/backend/config.yaml'
FILES = (
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
)
READ_BYTES = 0
MAX_READ_BYTES = 4194304 + 2 * 65536


class Refused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def audit(event, args):
    if event == 'open':
        mode, flags = args[1:3]
        need(not (isinstance(mode, str) and any(value in mode for value in 'wax+'))
             and not (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)), 'WRITE_BLOCKED')
    if event.startswith(('socket.', 'subprocess.', 'os.exec', 'os.spawn')) or event in (
        'os.system', 'os.remove', 'os.rename', 'os.mkdir', 'os.rmdir', 'os.chmod', 'os.chown', 'os.link', 'os.symlink', 'sqlite3.connect'):
        raise Refused('SIDE_EFFECT_BLOCKED')


def read(path, limit):
    global READ_BYTES
    need(path == CONFIG or path in FILES, 'FIXED_READ_SCOPE')
    limit = min(limit, MAX_READ_BYTES - READ_BYTES)
    need(limit > 0, 'TOTAL_READ_BOUND_REACHED')
    target = Path(path)
    if path == '/etc/deerflow-openssl/openssl.cnf':
        target = target.resolve(strict=True)
        need(Path('/etc/deerflow-openssl') in target.parents, 'OPENSSL_PROJECTION_SCOPE')
    else:
        need(not any(part.is_symlink() for part in (target,) + tuple(target.parents)), 'FIXED_FILE_LINK')
    fd = os.open(str(target), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        first = os.fstat(fd)
        need(stat.S_ISREG(first.st_mode) and 0 < first.st_size <= limit, 'FIXED_FILE_BOUND')
        raw = os.read(fd, limit)
        READ_BYTES += len(raw)
        need(READ_BYTES <= MAX_READ_BYTES, 'TOTAL_READ_BOUND_REACHED')
        stamp = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        need(len(raw) == first.st_size and stamp(first) == stamp(os.fstat(fd)) == stamp(target.stat()), 'FIXED_FILE_CHANGED')
        return raw
    finally:
        os.close(fd)


def config_numbers(raw):
    import yaml
    class Loader(yaml.SafeLoader):
        def compose_node(self, parent, index):
            need(not self.check_event(yaml.AliasEvent), 'YAML_ALIAS_REFUSED')
            return super().compose_node(parent, index)
    def mapping(loader, node, deep=False):
        value = {}
        for key_node, item in node.value:
            need(key_node.tag != 'tag:yaml.org,2002:merge', 'YAML_MERGE_REFUSED')
            key = loader.construct_object(key_node, deep=deep)
            need(isinstance(key, str) and key not in value, 'YAML_DUPLICATE_KEY')
            value[key] = loader.construct_object(item, deep=deep)
        return value
    Loader.add_constructor('tag:yaml.org,2002:map', mapping)
    data = yaml.load(raw.decode('utf-8'), Loader=Loader)
    need(isinstance(data, dict) and isinstance(data.get('scheduler'), dict), 'CONFIG_MAPPING_REQUIRED')
    budget, maximum = data['scheduler'].get('recursion_limit'), data.get('max_recursion_limit')
    return {'scheduler_recursion_limit': budget if type(budget) is int else None,
            'max_recursion_limit': maximum if type(maximum) is int else None,
            'both_budgets_are_integers': type(budget) is type(maximum) is int}


def main():
    out = {'status': 'INCOMPLETE', 'schema': 'scheduler-current-runtime-child.v1', 'application_imported': False,
           'persistent_files_modified': False, 'provider_requests': 0, 'explicit_database_queries': 0,
           'credentials_exported': False, 'raw_configuration_exported': False, 'source_text_exported': False}
    try:
        incoming = sys.stdin.buffer.read(8193)
        need(0 < len(incoming) <= 8192, 'CHILD_INPUT_BOUND')
        payload = json.loads(incoming)
        need(set(payload) == {'skip_secret_paths'} and isinstance(payload['skip_secret_paths'], list)
             and len(payload['skip_secret_paths']) <= 14 and set(payload['skip_secret_paths']) <= set(FILES) | {CONFIG}, 'PAYLOAD_SCOPE')
        skip = set(payload['skip_secret_paths'])
        sys.addaudithook(audit)
        rows, total, config_raw = {}, 0, None
        out['files'] = rows
        for path in (CONFIG,) + FILES:
            if path in skip:
                rows[path] = {'read': False, 'reason': 'SECRET_PROJECTION_NOT_READ'}
                continue
            try:
                if path != CONFIG:
                    need(total < 4194304, 'TOTAL_READ_BOUND_REACHED')
                raw = read(path, 65536 if path == CONFIG else min(1048576, 4194304 - total))
                if path != CONFIG:
                    total += len(raw)
                rows[path] = {'present': True, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                if path == CONFIG:
                    config_raw = raw
                    out['configuration'] = dict(rows[path], scheduler_recursion_limit=None, max_recursion_limit=None,
                                                both_budgets_are_integers=False)
                    try:
                        out['configuration'].update(config_numbers(raw))
                    except Exception as exc:
                        out['configuration']['budget_parse_error'] = {'error_class': type(exc).__name__,
                            'error_code': str(exc) if isinstance(exc, Refused) else 'YAML_PARSE_FAILED_REDACTED'}
            except FileNotFoundError:
                rows[path] = {'present': False}
            except Exception as exc:
                if isinstance(exc, Refused) and str(exc) == 'TOTAL_READ_BOUND_REACHED':
                    raise
                rows[path] = {'present': None, 'error_class': type(exc).__name__,
                              'error_code': str(exc) if isinstance(exc, Refused) else 'READ_FAILED_REDACTED'}
        out['files'] = rows
        if config_raw is not None:
            out['runtime_config_stable_readback'] = read(CONFIG, 65536) == config_raw
            need(out['runtime_config_stable_readback'], 'RUNTIME_CONFIG_CHANGED')
        else:
            out['runtime_config_stable_readback'] = None
        out['content_bytes_read'] = READ_BYTES
        out['max_content_bytes_read'] = MAX_READ_BYTES
        out['versions'] = {}
        for name in ('langchain', 'langchain-core', 'langgraph'):
            try:
                version = importlib.metadata.version(name)
                out['versions'][name] = version if re.fullmatch('[A-Za-z0-9.+_-]{1,80}', version) else 'UNRECOGNIZED'
            except importlib.metadata.PackageNotFoundError:
                out['versions'][name] = None
        out['status'] = 'CURRENT_GATEWAY_FIXED_RUNTIME_OBSERVED'
    except Exception as exc:
        out.update(error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else 'READONLY_CHILD_FAILED_REDACTED')
    print(json.dumps(out, sort_keys=True))
    return 0 if out['status'] != 'INCOMPLETE' else 1


if __name__ == '__main__':
    sys.exit(main())

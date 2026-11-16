"""Load sealed readonly postcheck resources and refuse every wrong-host mode."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace


def need(ok):
    if not ok:
        raise ValueError('SEALED_ENTRY_QUALIFICATION_FAILED')


def main():
    need(sys.version_info[:2] == (3, 7))
    path = Path(sys.argv[1])
    spec = importlib.util.spec_from_file_location('sealed_readonly_postcheck', str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    need(bool(module.PARENT_ASSET) and bool(module.FIELD_DEFAULTING))
    parent = module.load_parent()
    need(parent.load_code().encode('utf-8') and parent.load_child())
    calls = []
    def refuse(*args, **kwargs):
        calls.append('unexpected')
        raise AssertionError('NO_PROCESS_OR_FILE_WRITE_AUTHORIZED')
    parent.subprocess.Popen = refuse
    parent.private_write = refuse
    module.os.uname = lambda: SimpleNamespace(nodename='offline-wrong-host')
    checked = []
    for flag in ('--verify-readonly', '--verify-and-save-receipt'):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            need(module.main([flag]) == 1)
        result = json.loads(output.getvalue())
        need(result['error_code'] == 'DATAX01_REQUIRED' and result['commands'] == 0 and result['persistent_files_modified'] is False)
        checked.append(flag)
    need(not calls)
    print(json.dumps({'schema': 'task-context-postcheck-python37-sealed.v1', 'status': 'SEALED_RESOURCES_AND_WRONG_HOST_GUARDS_ACCEPTED',
        'python': sys.version, 'operator_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'field_proof_bound': True, 'field_report_sha256': module.FIELD_DEFAULTING['field_report_sha256'],
        'parent_source_loaded_without_main': True, 'parent_gateway_code_decoded_not_compiled': True,
        'wrong_host_mode_checks': len(checked), 'commands': 0, 'files_modified': False,
        'provider_requests': 0, 'production_requests': 0}, sort_keys=True))


if __name__ == '__main__':
    main()

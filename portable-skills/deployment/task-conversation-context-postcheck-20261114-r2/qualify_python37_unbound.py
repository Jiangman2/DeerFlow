"""Load the parent asset under Python 3.7; keep field proof unbound and refuse."""
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace


def need(ok):
    if not ok:
        raise ValueError('UNBOUND_ENTRY_QUALIFICATION_FAILED')


def main():
    need(sys.version_info[:2] == (3, 7))
    template_path, parent_path = map(Path, sys.argv[1:3])
    spec = importlib.util.spec_from_file_location('unbound_readonly_postcheck', str(template_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    need(not module.FIELD_DEFAULTING and not module.PARENT_ASSET)
    original = parent_path.read_bytes()
    module.PARENT_ASSET = {'base64': base64.b64encode(original).decode('ascii'), 'sha256': hashlib.sha256(original).hexdigest()}
    parent = module.load_parent()
    need(parent.load_code().encode('utf-8') and parent.load_child())
    commands = []
    def refuse(*args, **kwargs):
        commands.append('unexpected')
        raise AssertionError('NO_PROCESS_OR_FILE_WRITE_AUTHORIZED')
    parent.subprocess.Popen = refuse
    parent.private_write = refuse
    checked = []
    for hostname, expected in (('local-only', 'DATAX01_REQUIRED'), ('datax-01', 'FIELD_DEFAULTING_PROOF_NOT_BOUND')):
        module.os.uname = lambda: SimpleNamespace(nodename=hostname)
        for flag in ('--verify-readonly', '--verify-and-save-receipt'):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                need(module.main([flag]) == 1)
            result = json.loads(output.getvalue())
            need(result['error_code'] == expected and result['commands'] == 0 and result['persistent_files_modified'] is False)
            checked.append(expected)
    need(not commands)
    module.PARENT_ASSET['sha256'] = '0' * 64
    refused = False
    try:
        module.load_parent()
    except module.Refused:
        refused = True
    need(refused)
    print(json.dumps({'schema': 'task-context-postcheck-python37-unbound.v1', 'status': 'PARENT_ASSET_AND_UNBOUND_PROOF_GUARDS_ACCEPTED',
        'python': sys.version, 'template_sha256': hashlib.sha256(template_path.read_bytes()).hexdigest(),
        'parent_sha256': hashlib.sha256(original).hexdigest(), 'field_proof_bound': False, 'production_release_created': False,
        'parent_source_loaded_without_main': True, 'parent_gateway_code_decoded_not_compiled': True,
        'wrong_host_and_unbound_mode_checks': len(checked), 'tampered_parent_rejected': True, 'commands': 0,
        'files_modified': False, 'provider_requests': 0, 'production_requests': 0}, sort_keys=True))


if __name__ == '__main__':
    main()

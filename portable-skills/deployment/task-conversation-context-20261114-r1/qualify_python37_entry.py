"""Run in local Python 3.7 with --network none against a sealed release."""
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace


def main():
    path = Path(sys.argv[1])
    spec = importlib.util.spec_from_file_location('fixed_sealed_operator', str(path))
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    need = lambda condition: condition or (_ for _ in ()).throw(ValueError('OFFLINE_ENTRY_GUARD_FAILED'))
    need(sys.version_info[:2] == (3, 7))
    code = host.load_code().encode('utf-8')
    child = host.load_child().encode('utf-8')
    need(len(code) == host.TOOLS_CANDIDATE['bytes'] and hashlib.sha256(code).hexdigest() == host.TOOLS_CANDIDATE['sha256'])
    # In particular, do not compile tools.py here: it targets Gateway Python.
    need(b':=' in code)
    calls = []
    def no_command(*args, **kwargs):
        calls.append('command')
        raise AssertionError('NO_EXTERNAL_COMMAND_AUTHORIZED')
    host.subprocess.Popen = no_command
    host.private_write = no_command
    host.os.uname = lambda: SimpleNamespace(nodename='offline-qualification-not-datax01')
    for flag in ('--activate-task-context-once', '--verify-readonly', '--verify-and-save-receipt'):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            need(host.main([flag]) == 1)
        report = json.loads(output.getvalue())
        need(report['error_code'] == 'DATAX01_REQUIRED' and report['commands'] == 0 and not report['once_claimed'])
    need(not calls)
    for asset_name, loader in (('CODE_ASSET', host.load_code), ('CHILD_ASSET', host.load_child)):
        original = getattr(host, asset_name)
        setattr(host, asset_name, dict(original, sha256='0' * 64))
        refused = False
        try:
            loader()
        except host.Refused:
            refused = True
        need(refused)
        setattr(host, asset_name, original)
    print(json.dumps({'schema': 'task-context-python37-entry-qualification.v1', 'status': 'SEALED_RESOURCES_AND_WRONG_HOST_GUARDS_ACCEPTED',
        'python': sys.version, 'operator_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'candidate_sha256': hashlib.sha256(code).hexdigest(), 'child_sha256': hashlib.sha256(child).hexdigest(),
        'source_contains_newer_python_syntax': True, 'gateway_source_compiled_on_operator': False,
        'wrong_host_modes_checked': 3, 'tampered_assets_rejected': 2, 'provider_requests': 0, 'production_requests': 0,
        'commands': 0, 'files_modified': False}, sort_keys=True))


if __name__ == '__main__':
    main()

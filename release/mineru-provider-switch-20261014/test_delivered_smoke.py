"""Offline, disposable-container acceptance of the actual diagnostic archive."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from unittest.mock import patch

assert os.environ.get('SMOKE_FIXTURE_ONLY') == '1'
os.umask(0o077)
root = Path(tempfile.mkdtemp(prefix='mineru-delivery-fixture-', dir='/run'))
expected = {'audit_mineru.py', 'audit_capacity.py', 'probe_async.py', 'async_probe_remote.py',
            'candidate-adapter.py', 'run-datax01-async-smoke.sh', 'ASYNC-SMOKE-RUNBOOK.txt',
            'SMOKE-VERIFICATION.json', 'SMOKE-LOCK.json', 'PACKAGE.SHA256'}
archive = '/artifact/deerflow-mineru-async-smoke-20261014-r1.tar.gz'
with tarfile.open(archive, 'r:gz') as source:
    assert set(source.getnames()) == expected
    for member in source.getmembers():
        assert member.isfile() and member.name in expected and '/' not in member.name
        (root / member.name).write_bytes(source.extractfile(member).read())
for line in (root / 'PACKAGE.SHA256').read_text().splitlines():
    sha, name = line.split('  ', 1)
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == sha
subprocess.run(['bash', '-n', str(root / 'run-datax01-async-smoke.sh')], check=True)
sys.path.insert(0, str(root))
c = importlib.import_module('probe_async')
sources, source_sha = c.reviewed_sources()
assert source_sha == hashlib.sha256((root / 'candidate-adapter.py').read_bytes()).hexdigest()
compile(sources['candidate-adapter.py'], '<actual-candidate>', 'exec')
compile(sources['async_probe_remote.py'], '<actual-probe>', 'exec')
gate = root / 'gate'
gate.mkdir(mode=0o700)
worker = {'metadata': {'name': 'fixture-worker', 'uid': 'fixture-worker-id'},
          'status': {'containerStatuses': [{'name': 'adapter', 'ready': True}]}}

def get(resource, name):
    if resource == 'pod': return worker
    assert resource == 'deployment' and name in c.EXPECTED
    return {'metadata': {'uid': c.EXPECTED[name][0]}, 'spec': {'fixture': name}}

token = 'fixture-key-not-a-real-key'
health = {'http_status': 200, 'source_sha256': c.SOURCE_SHA, 'parser': {'active': 0, 'waiting': 0}}
terminal = {'async_smoke': 'PASS', 'post_attempts': 1, 'accepted_task_id': 'fixture-task', 'ready_for_rollout': False}
with patch.object(c, 'GATE', gate), patch.object(c.socket, 'gethostname', return_value='datax-01'), \
        patch.object(c.a, 'get', side_effect=get), \
        patch.object(c.a, 'digest', side_effect=lambda spec: c.EXPECTED[spec['fixture']][1]), \
        patch.object(c.c, 'owned', return_value=[worker]), patch.object(c.c, 'health', return_value=health), \
        patch.object(c.getpass, 'getpass', return_value=token) as password, \
        patch.object(c, 'execute_probe', return_value=(0, [terminal], False)) as execute, \
        patch.object(c.a, 'emit') as emit, patch.object(sys, 'argv', ['probe_async.py', '--probe-once']):
    assert c.main() == 0
    assert execute.call_count == 1
    args = execute.call_args.args
    assert args[1] == sources['async_probe_remote.py'] and args[2] == sources['candidate-adapter.py']
    assert args[3] == source_sha and args[4] == token
    receipt = emit.call_args.args[0]
    path = Path(receipt['report'])
    raw = path.read_bytes()
    assert token.encode() not in raw
    assert hashlib.sha256(raw).hexdigest() == receipt['report_sha256']
    assert path.stat().st_mode & 0o777 == 0o600
    result = json.loads(raw)
    assert result['async_smoke'] == 'PASS' and result['ready_for_rollout'] is False
    try:
        c.main()
        raise AssertionError('second_submission_was_not_blocked')
    except c.a.CheckFailure as exc:
        assert str(exc) == 'already_started_use_status_do_not_resubmit'
    assert password.call_count == execute.call_count == 1
    with patch.object(sys, 'argv', ['probe_async.py', '--status']):
        assert c.main() == 0
    assert password.call_count == execute.call_count == 1
    assert token not in repr(emit.call_args_list)

print(json.dumps({'delivered_async_package': 'PASS', 'files': len(expected),
    'manifest_and_source_locks': 'PASS', 'controller_terminal_receipt': 'PASS',
    'repeat_guard_and_status': 'PASS', 'bash_syntax': 'PASS', 'real_provider_requests': 0,
    'real_cluster_access': False, 'image_acceptance': False}))

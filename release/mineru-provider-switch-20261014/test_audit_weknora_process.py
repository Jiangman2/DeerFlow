import ast
import contextlib
import hashlib
import io
import json
from pathlib import Path
import signal
import stat
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import audit_weknora_process as audit


def record():
    return {'effective_uid': 0, 'process': {'uid': [1000]*4, 'gid': [1000]*4},
            'binary_read_error': {'errno': 13}, 'tools_present': {'gosu': True}}


class Tests(unittest.TestCase):
    def test_python37_syntax(self):
        ast.parse(Path(audit.__file__).read_text(encoding='utf-8'), feature_version=(3, 7))
        ast.parse(audit.REMOTE, feature_version=(3, 7))

    def test_only_drop_to_same_uid(self):
        self.assertTrue(audit.may_probe_as_process_user(record()))

    def test_no_root_process_switch(self):
        r = record(); r['process']['uid'] = [0]*4
        self.assertFalse(audit.may_probe_as_process_user(r))

    def test_no_mixed_uid_switch(self):
        r = record(); r['process']['uid'][0] = 0
        self.assertFalse(audit.may_probe_as_process_user(r))

    def test_no_nonroot_switch(self):
        r = record(); r['effective_uid'] = 1000
        self.assertFalse(audit.may_probe_as_process_user(r))

    def test_no_unrelated_error_fallback(self):
        r = record(); r['binary_read_error']['errno'] = 2
        self.assertFalse(audit.may_probe_as_process_user(r))

    def test_no_missing_gosu_fallback(self):
        r = record(); r['tools_present']['gosu'] = False
        self.assertFalse(audit.may_probe_as_process_user(r))

    def test_permission_classification(self):
        self.assertIn('SUCCEEDED_AS_SAME_NONROOT', audit.classify(record(), {'binary_sha256': 'a'*64}))

    def test_probe_argv(self):
        response = json.dumps({'process_stable': True, 'signals_sent': 0}).encode()
        with patch.object(audit, 'kube', return_value=response) as call:
            audit.probe((1000, 1000))
            self.assertEqual(call.call_args[0][0][:10],
                ['exec', audit.POD, '-c', 'app', '--', 'gosu', '1000:1000', 'python3', '-I', '-B'])

    def test_unstable_probe_rejected(self):
        with patch.object(audit, 'kube', return_value=b'{"process_stable":false,"signals_sent":0}'):
            with self.assertRaisesRegex(audit.CheckFailure, 'process_probe_invalid'):
                audit.probe()

    def test_exact_baseline(self):
        spec = {'replicas': 1}
        d = {'metadata': {'uid': audit.DEP_UID}, 'spec': spec}
        p = {'metadata': {'uid': audit.POD_UID}, 'status': {'containerStatuses': [
            {'name': 'app', 'ready': True, 'containerID': audit.CID, 'restartCount': 0}]}}
        with patch.object(audit, 'DEP_SPEC', audit.digest(spec)), patch.object(audit, 'kube',
                side_effect=[json.dumps(d).encode(), json.dumps(p).encode()]):
            audit.baseline()

    def test_changed_baseline_rejected(self):
        d = {'metadata': {'uid': 'changed'}, 'spec': {}}
        p = {'metadata': {'uid': audit.POD_UID}}
        with patch.object(audit, 'kube', side_effect=[json.dumps(d).encode(), json.dumps(p).encode()]):
            with self.assertRaisesRegex(audit.CheckFailure, 'app_deployment_changed'):
                audit.baseline()

    def run_remote(self, deny=False):
        # Execute the exact embedded program with synthetic proc files. No real
        # /proc, subprocess, environment read, signal, or Kubernetes connection.
        mask = 1 << (int(getattr(signal, 'SIGTSTP', 20)) - 1)
        status = 'Uid:\t1000\t1000\t1000\t1000\nGid:\t1000\t1000\t1000\t1000\nSigCgt:\t%016x\n' % mask
        stat_text = '1 (safe test ) name) ' + ' '.join(['0']*19+['12345'])
        binary = b'A'*1048567+b'github.com/hibiken/asynq\tv0.26.0\nSend signal TSTP to stop processing new tasks'
        class Binary(io.BytesIO):
            def fileno(self): return 42
        reads = []
        def fake_open(path, mode='r', **kwargs):
            reads.append(path)
            if path == '/proc/1/status': return io.StringIO(status)
            if path == '/proc/1/stat': return io.StringIO(stat_text)
            if path == '/proc/1/exe':
                if deny: raise PermissionError(13, 'SENSITIVE_TEXT_MUST_NOT_ESCAPE')
                return Binary(binary)
            raise AssertionError(path)
        output = io.StringIO()
        with patch('builtins.open', side_effect=fake_open), patch('os.geteuid', return_value=0, create=True), \
             patch('os.getegid', return_value=0, create=True), patch('os.access', return_value=not deny), \
             patch('os.readlink', return_value='/app/WeKnora'), patch('shutil.which', return_value='/fake/tool'), \
             patch('os.fstat', return_value=SimpleNamespace(st_mode=stat.S_IFREG|0o755,
                     st_size=len(binary), st_uid=0, st_gid=0)), \
             patch('subprocess.run', return_value=SimpleNamespace(returncode=0)) as run, \
             patch.object(signal, 'SIGTSTP', getattr(signal, 'SIGTSTP', 20), create=True), \
             contextlib.redirect_stdout(output):
            exec(compile(audit.REMOTE, '<remote>', 'exec'), {})
        self.assertEqual(set(reads), {'/proc/1/status', '/proc/1/stat', '/proc/1/exe'})
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args[0][0], ['grep', '-aFq', 'probe-marker'])
        self.assertNotIn('SENSITIVE_TEXT', output.getvalue())
        return json.loads(output.getvalue()), binary

    def test_embedded_program_hash_and_split_markers(self):
        r, binary = self.run_remote()
        self.assertEqual(r['binary_sha256'], hashlib.sha256(binary).hexdigest())
        self.assertTrue(all(r['markers'].values()))
        self.assertTrue(r['process_stable'])
        self.assertTrue(r['process']['tstp_handler_installed'])
        self.assertEqual(r['process']['start_ticks'], 12345)
        self.assertFalse(r['safe_to_signal_proven'])

    def test_embedded_permission_redaction(self):
        r, _ = self.run_remote(deny=True)
        self.assertEqual(r['binary_read_error']['errno'], 13)
        self.assertNotIn('binary_sha256', r)
        self.assertTrue(audit.may_probe_as_process_user(r))

    def test_no_mutation_commands(self):
        tree = ast.parse(audit.REMOTE)
        calls = [n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        for name in ('kill', 'killpg', 'chmod', 'chown', 'unlink', 'remove', 'putenv', 'setuid', 'setgid'):
            self.assertNotIn(name, calls)


if __name__ == '__main__':
    unittest.main()

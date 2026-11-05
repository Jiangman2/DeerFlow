"""Linux Python 3.7 protected receipts and inherited heartbeat qualification."""
import ast
import asyncio
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


def literal(source, name):
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError('missing embedded source')


source = Path(sys.argv.pop(1)).read_text(encoding='utf-8')
child = literal(source, 'CHILD')
common = literal(child, 'COMMON')
guard = {'__name__': 'synthetic_guard'}
exec(literal(source, 'GUARD'), guard)
heartbeat = {'__name__': 'synthetic_heartbeat'}
exec(common, heartbeat)
spec = importlib.util.spec_from_file_location('heartbeat_fixture', Path(__file__).with_name('test_weknora_company_enrollment_linux.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class Qualification(unittest.TestCase):
    def test_all_five_sources_compile(self):
        for text in (source, child, common, literal(child, 'BOOTSTRAP'), literal(source, 'GUARD')):
            compile(text, '<isolated-py37>', 'exec')

    def test_live_heartbeat(self):
        asyncio.run(fixture.heartbeat_test(heartbeat, False))

    def test_closed_host_stops_child(self):
        asyncio.run(fixture.heartbeat_test(heartbeat, True))

    def protected(self, mode):
        old = guard['RECEIPTS']
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'report.json'
                data = json.dumps({'status': 'WEKNORA_COMPANY_AUTO_ENROLLMENT_RUNTIME_ACCEPTED_BROWSER_PENDING',
                    'runtime_accepted': True, 'auto_enrollment_deployed': True}).encode()
                path.write_bytes(data)
                path.chmod(0o600)
                pin = {'path': str(path), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                if mode == 'tamper':
                    path.write_bytes(data.replace(b'true', b'nulL', 1))
                elif mode == 'writable':
                    path.chmod(0o666)
                elif mode == 'symlink':
                    link = Path(directory) / 'link.json'
                    link.symlink_to(path)
                    pin['path'] = str(link)
                guard['RECEIPTS'] = [pin]
                if mode == 'good':
                    self.assertEqual(guard['check_receipts'](), [pin])
                else:
                    with self.assertRaises(guard['Refused']):
                        guard['check_receipts']()
        finally:
            guard['RECEIPTS'] = old

    def test_complete_protected_receipt(self):
        self.protected('good')

    def test_tampered_receipt(self):
        self.protected('tamper')

    def test_writable_receipt(self):
        self.protected('writable')

    def test_symlink_receipt(self):
        self.protected('symlink')

    def test_changed_or_unready_deployment_rejected(self):
        d = {'metadata': {'uid': 'synthetic', 'generation': 387}, 'spec': {'replicas': 70},
             'status': {'observedGeneration': 387, 'replicas': 70, 'updatedReplicas': 70, 'readyReplicas': 70, 'availableReplicas': 70}}
        old = guard['PINS']
        guard['PINS'] = {'gateway': ['synthetic', 387, guard['digest'](d['spec'])]}
        try:
            guard['check_deployment'](d, 'gateway')
            for changed in ('generation', 'not-ready'):
                bad = copy.deepcopy(d)
                if changed == 'generation':
                    bad['metadata']['generation'] = 388
                else:
                    bad['status']['readyReplicas'] = 69
                with self.assertRaises(guard['Refused']):
                    guard['check_deployment'](bad, 'gateway')
        finally:
            guard['PINS'] = old


if __name__ == '__main__':
    unittest.main()

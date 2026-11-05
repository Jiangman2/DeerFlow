"""Offline checks of exact operator scripts and their preclaim failure boundary."""
import ast
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
PACKAGE = Path(os.environ.get('FRONTEND_PROGRESS_PACKAGE', 'C:/Users/15285/Downloads/portal-data-qa-progress-20261022-r2'))
PRIOR = Path(os.environ.get('FRONTEND_PROGRESS_PRIOR', 'C:/Users/15285/Downloads/portal-data-qa-progress-20261021-r1'))
spec = importlib.util.spec_from_file_location('frontend_r2_builder', HERE / 'package-data-qa-progress-frontend-r2.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def load(role):
    path = PACKAGE / ('preload-portal-frontend-data-qa-progress-' + role + '-20261022-r2.py')
    return builder.load_source(path.read_text(encoding='utf-8'), builder.PINS[role][0])


class Tests(unittest.TestCase):
    def test_transport_and_script_hashes(self):
        receipt = json.loads((PACKAGE / 'PACKAGE.json').read_text())
        self.assertEqual(builder.sha_file(PACKAGE / builder.ARCHIVE), builder.TRANSPORT['sha256'])
        for row in receipt['scripts'].values():
            path = PACKAGE / row['file']
            self.assertEqual(path.stat().st_size, row['bytes'])
            self.assertEqual(builder.sha_file(path), row['sha256'])

    def test_actual_archive_blobs_diffids_and_budget_projection(self):
        module = load('0015')
        importer, unused, unused2 = module.load_inputs()
        with (PACKAGE / builder.ARCHIVE).open('rb') as source:
            prepared = module.inspect_archive(source.fileno(), importer, time.monotonic())
        self.assertEqual(module.EARLY_BUDGET, {k: prepared[k] for k in module.EARLY_BUDGET})
        self.assertEqual(len(prepared['chains']), 11)
        self.assertEqual(module.MANIFEST, builder.MANIFEST)

    def test_exact_host_and_protected_current_image(self):
        for role, (host, uid, unused) in builder.PINS.items():
            module = load(role)
            self.assertEqual(module.NODES, {host: uid})
            self.assertIn('sha256:ee145e6357e4f6b63003d786b7c6b0f28cf6bbe663e1b5589f92f7c829bb89e5', module.PROTECTED.values())
            self.assertTrue(module.CONTROL.endswith('20261022-r2'))

    def test_native_import_and_budget_code_unchanged(self):
        names = {'load_inputs', 'inspect_archive', 'seal_source', 'worker_budget', 'budget', 'controller_class', 'rewrite_private', 'cache_projection', 'assemble_complete'}
        for role in builder.PINS:
            def segments(path):
                source = path.read_text(encoding='utf-8')
                return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name in names}
            prior = PRIOR / ('preload-portal-frontend-data-qa-progress-' + role + '-20261021-r1.py')
            current = PACKAGE / ('preload-portal-frontend-data-qa-progress-' + role + '-20261022-r2.py')
            self.assertEqual(segments(prior), segments(current))

    def main_failure(self, role, wrong_host=False, prior_marker=False):
        module = load(role)
        host = 'datax-02' if role == '0015' else 'ecs-zdz-pro-0015'
        if not wrong_host:
            host = builder.PINS[role][0]
        importer = SimpleNamespace(write_new=lambda *a: None, run_bounded=lambda *a, **kw: None)
        accounting = SimpleNamespace(write_new=lambda *a: None, run_bounded=lambda *a, **kw: None)
        resource = SimpleNamespace(BudgetIO=lambda *a: accounting)
        output = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(module.os, 'uname', return_value=SimpleNamespace(nodename=host, machine='aarch64'), create=True))
            stack.enter_context(patch.object(module.os, 'geteuid', return_value=0, create=True))
            stack.enter_context(patch.object(module.os, 'name', 'posix'))
            stack.enter_context(patch.object(module.sys, 'flags', SimpleNamespace(isolated=1, dont_write_bytecode=1)))
            stack.enter_context(patch.object(module.sys, 'argv', ['preload', '--preload-frontend-data-qa-progress-once']))
            tools = stack.enter_context(patch.object(module.shutil, 'which', return_value='/usr/bin/tool'))
            stack.enter_context(patch.object(module, 'load_inputs', return_value=(importer, resource, SimpleNamespace())))
            stack.enter_context(patch.object(module, 'trusted_directory', return_value=123))
            stack.enter_context(patch.object(module.os.path, 'lexists', return_value=prior_marker))
            stack.enter_context(patch.object(module.os, 'close'))
            mkdir = stack.enter_context(patch.object(module.os, 'mkdir', side_effect=AssertionError('CLAIM_FORBIDDEN')))
            gate = stack.enter_context(patch.object(module, 'early_preload_gate', side_effect=module.Refused('root_nodefs_headroom')))
            stack.enter_context(contextlib.redirect_stdout(output))
            self.assertEqual(module.main(), 1)
            mkdir.assert_not_called()
            if wrong_host:
                tools.assert_not_called()
                gate.assert_not_called()
            elif prior_marker:
                gate.assert_not_called()
            else:
                gate.assert_called_once()
        receipt = json.loads(output.getvalue())
        self.assertEqual(receipt['mutation_once_claimed'], prior_marker)
        self.assertEqual(receipt['preflight_retry_available'], not prior_marker)
        self.assertFalse(receipt['image_import_requested'])
        return receipt

    def test_wrong_other_frontend_host_stops_before_tools(self):
        for role in builder.PINS:
            self.assertEqual(self.main_failure(role, True)['error_code'], 'fixed_frontend_node_required')

    def test_capacity_failure_leaves_once_unclaimed(self):
        for role in builder.PINS:
            self.assertEqual(self.main_failure(role)['error_code'], 'root_nodefs_headroom')

    def test_existing_marker_blocks_preflight_and_retry(self):
        for role in builder.PINS:
            self.assertEqual(self.main_failure(role, prior_marker=True)['error_code'], 'once_exists_review_required')

    def probe(self, role, failure=None):
        module = load(role)
        module.INPUT = str(PACKAGE / builder.ARCHIVE)
        parent = module.CONTROL.rsplit('/', 1)[0]
        paths = ({'content': '/mnt/data/containerd/content', 'snapshot': '/mnt/data/containerd/snapshot'}
                 if role == '0015' else module.EXPECTED_IMAGEFS)
        def observed(path):
            device = 2 if role == '0015' and path == '/var/lib' else 1
            available = 90 * module.GIB
            if (failure == 'root' and path == '/var/lib') or (failure == 'imagefs' and path == paths['snapshot']):
                available = module.GIB
            return dict(path=path, device=device, capacity_bytes=100 * module.GIB, available_bytes=available,
                        total_inodes=2000000, available_inodes=1800000, fragment_size=4096)
        calls = []
        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            raw = b'{}' if 'imagefsinfo' in argv else b'KEY PARENT KIND\n' if 'snapshots' in argv else b''
            return raw, b'', {'returncode': 0, 'error_code': None}
        importer = SimpleNamespace(SOCKET='/run/containerd/containerd.sock', parse=json.loads)
        resource = SimpleNamespace(observed_directory=observed)
        imagefs = SimpleNamespace(imagefs_paths=lambda unused: paths)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(module.os, 'sysconf', return_value=4096, create=True))
            if os.name == 'nt':
                stack.enter_context(patch.object(module.os, 'O_NOFOLLOW', 0, create=True))
                stack.enter_context(patch.object(module.os, 'O_NONBLOCK', 0, create=True))
                original_open = module.os.open
                stack.enter_context(patch.object(module.os, 'open', side_effect=lambda path, flags: original_open(path, flags | os.O_BINARY)))
            result = module.early_preload_gate(importer, resource, imagefs, ['/usr/bin/ctr', '/usr/bin/crictl', None], SimpleNamespace(run_bounded=runner), time.monotonic())
        self.assertEqual(len(calls), 3)
        self.assertTrue(all('import' not in args and 'tag' not in args for args, kwargs in calls))
        self.assertTrue(all(kwargs['timeout'] <= 60 and kwargs['limit'] <= 4 * module.MIB for args, kwargs in calls))
        self.assertTrue(result['input_hash_verified'])
        self.assertFalse(result['files_written'])
        self.assertEqual(result['capacity']['devices'][0]['paths'][0], parent)

    def test_preclaim_real_budget_passes_for_both_layouts(self):
        for role in builder.PINS:
            self.probe(role)

    def test_preclaim_real_budget_refuses_root_and_imagefs_pressure(self):
        for role in builder.PINS:
            for failure in ('root', 'imagefs'):
                with self.assertRaises(Exception) as caught:
                    self.probe(role, failure)
                self.assertIn(str(caught.exception), ('root_nodefs_headroom', 'full_image_resource_budget'))


if __name__ == '__main__':
    unittest.main()

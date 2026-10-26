"""Validate the actual operator package without containerd or Kubernetes writes."""
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
PACKAGE = Path(os.environ.get('NOTICE_FRONTEND_PACKAGE', 'C:/Users/15285/Downloads/portal-nonconfidential-notice-20261023-r2'))
PRIOR = Path(os.environ.get('NOTICE_FRONTEND_PRIOR', 'C:/Users/15285/Downloads/portal-data-qa-token-20261022-r1'))
spec = importlib.util.spec_from_file_location('prior_tests', HERE / 'test_data_qa_progress_frontend_r2.py')
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
record = json.loads((PACKAGE / 'PACKAGE.json').read_text(encoding='utf-8'))
RELEASE = record['schema'][len('portal-'):-len('-frontend-package.v1')]
assert RELEASE in ('nonconfidential-notice', 'mobile-notice-removal', 'mobile-chat-spacing', 'chat-notice-layout')
FLAG = '--preload-frontend-' + RELEASE + '-once'
REVISION = record['tag'].rsplit(':', 1)[1].replace('-' + RELEASE, '')
prior.PACKAGE = PACKAGE
prior.builder.ARCHIVE = record['archive']
prior.builder.TRANSPORT = record['transport']
prior.builder.MANIFEST = record['manifest']
prior.builder.CONFIG = record['config']


def load(role):
    return prior.builder.load_source((PACKAGE / record['scripts'][role]['file']).read_text(encoding='utf-8'), prior.builder.PINS[role][0])


prior.load = load


class Tests(prior.Tests):
    def test_exact_host_and_protected_current_image(self):
        for role, (host, uid, unused) in prior.builder.PINS.items():
            module = load(role)
            self.assertEqual(module.NODES, {host: uid})
            self.assertIn('sha256:ee145e6357e4f6b63003d786b7c6b0f28cf6bbe663e1b5589f92f7c829bb89e5', module.PROTECTED.values())
            self.assertIn('sha256:b6c17e31ebcadb3c514fd2c3f87752e0eb84ee3e28ccc921366bac6a0bf91cd5', module.PROTECTED.values())
            self.assertTrue(module.CONTROL.endswith('portal-frontend-' + RELEASE + '-preload-' + REVISION))
            if RELEASE in ('mobile-notice-removal', 'mobile-chat-spacing', 'chat-notice-layout'):
                self.assertIn('sha256:b37036e9b226f8605e3da98d9f81cb072fc30da29cb5b972702c5ed6ec535638', module.PROTECTED.values())
            if RELEASE in ('mobile-chat-spacing', 'chat-notice-layout'):
                self.assertIn('sha256:0af4bb16a0924a02f6cfcb927c137642329b5831d01a5eded8c4f61ee8ad43f7', module.PROTECTED.values())
            self.assertEqual(module.TAG, record['tag'])

    def test_native_import_and_budget_code_unchanged(self):
        for role in record['scripts']:
            older = (PRIOR / ('preload-portal-frontend-data-qa-token-' + role + '-20261022-r1.py')).read_text(encoding='utf-8')
            newer = (PACKAGE / record['scripts'][role]['file']).read_text(encoding='utf-8')
            newer = newer.replace(FLAG, '--preload-frontend-data-qa-token-once')
            def functions(source):
                return {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(source).body
                        if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
            self.assertEqual(functions(older), functions(newer))
            a, b = load(role), prior.builder.load_source(older, prior.builder.PINS[role][0])
            self.assertEqual(a.SOURCES, b.SOURCES)
            self.assertEqual(a.SOURCE_PINS, b.SOURCE_PINS)

    def main_failure(self, role, wrong_host=False, prior_marker=False):
        module = load(role)
        host = ('datax-02' if role == '0015' else 'ecs-zdz-pro-0015') if wrong_host else prior.builder.PINS[role][0]
        importer = SimpleNamespace(write_new=lambda *a: None, run_bounded=lambda *a, **kw: None)
        resource = SimpleNamespace(BudgetIO=lambda *a: SimpleNamespace(write_new=lambda *a: None, run_bounded=lambda *a, **kw: None))
        output = io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(module.os, 'uname', return_value=SimpleNamespace(nodename=host, machine='aarch64'), create=True))
            stack.enter_context(patch.object(module.os, 'geteuid', return_value=0, create=True))
            stack.enter_context(patch.object(module.os, 'name', 'posix'))
            stack.enter_context(patch.object(module.sys, 'flags', SimpleNamespace(isolated=1, dont_write_bytecode=1)))
            stack.enter_context(patch.object(module.sys, 'argv', ['preload', FLAG]))
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


if __name__ == '__main__':
    unittest.main(verbosity=2)

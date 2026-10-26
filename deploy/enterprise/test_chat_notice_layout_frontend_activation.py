"""Exercise the combined layout rollout transport with synthetic cluster responses."""
import ast
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def load(filename):
    spec = importlib.util.spec_from_file_location(filename.replace('-', '_'), HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prior = load('test_nonconfidential_notice_frontend_activation.py')
builder = load('package-chat-notice-layout-frontend-activation.py')


class Tests(prior.Tests):
    @classmethod
    def setUpClass(cls):
        if os.environ.get('CHAT_NOTICE_ACTIVATION_SCRIPT'):
            candidate = Path(os.environ['CHAT_NOTICE_ACTIVATION_SCRIPT'])
            cls.source = candidate.read_text(encoding='utf-8')
            with patch.dict(os.environ, {'NOTICE_ACTIVATION_SCRIPT': str(candidate)}):
                super().setUpClass()
            return
        package = {'schema': 'portal-chat-notice-layout-frontend-package.v1',
                   'archive_blob_and_diffid_validation': 'PASS',
                   'manifest': 'sha256:' + 'a' * 64, 'config': 'sha256:' + 'b' * 64,
                   'tag': 'deerflow.local/deer-flow-frontend:synthetic-spacing-test'}
        cls.source = builder.template(package)
        common = prior.prior.load('package-data-qa-progress-frontend-activation.py').prior_common(prior.prior.PRIOR)
        cls.source = builder.replace(cls.source, 'FRONT_COMMON', 'FRONT_COMMON = ' + repr(common))
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary) / 'synthetic.py'
            candidate.write_text(cls.source, encoding='utf-8')
            with patch.dict(os.environ, {'NOTICE_ACTIVATION_SCRIPT': str(candidate)}):
                super().setUpClass()

    def test_host_python37_and_no_backend_dependencies(self):
        if os.sys.version_info >= (3, 8):
            ast.parse(self.source, feature_version=(3, 7))
        else:
            ast.parse(self.source)
        self.assertNotIn('BACKEND_COMMON', self.source)
        self.assertNotIn('schema_check(', self.source)
        self.assertEqual(self.original_front_pin['generation'], 228)

    def test_only_proven_noop_r2_failure_can_resume(self):
        # The new release instead binds the accepted 228 receipt.
        self.assertNotIn('read_failed_r2', self.source)
        self.assertEqual(self.mod.PRIOR_RECEIPT['sha256'],
                         '584fccd523377cda7ff4e8f7c1321d7f17a8ee3aff5a9626a95a06558258e41e')
        self.assertIn('FRONTEND_MOBILE_NOTICE_REMOVAL_RUNTIME_ACCEPTED_BROWSER_PENDING', self.source)
        self.assertIn('--activate-frontend-chat-notice-layout-once', self.source)
        self.assertEqual(self.mod.ONCE, 'portal-frontend-chat-notice-layout-activation-20261024-r1.once')


if __name__ == '__main__':
    unittest.main(verbosity=2)

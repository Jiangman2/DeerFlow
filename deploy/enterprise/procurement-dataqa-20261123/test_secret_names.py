import base64
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import dataqa_config as dq


class SecretNameTests(unittest.TestCase):
    def test_reported_underscore_record_generates_valid_name(self):
        for suffix in ('b_ipf29j', '_leading', 'trailing_', 'abc-def'):
            record = Path('/opt/df-procurement-dataqa-r4-' + suffix)
            name = dq.secret_name_for(record)
            self.assertLessEqual(len(name), 63)
            self.assertIsNotNone(re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*', name))
            self.assertEqual(name, dq.secret_name_for(record))
        self.assertNotEqual(dq.secret_name_for(Path('/opt/one')), dq.secret_name_for(Path('/opt/two')))

    def test_invalid_name_rejected_before_kubernetes(self):
        with patch('dataqa_config.subprocess.run') as run:
            for name in ('dataqa-b_ipf29j', '-bad', 'bad-', 'Bad', 'x' * 64):
                with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'INVALID_DATAQA_SECRET_NAME'):
                    dq.create_key_secret(['kubectl'], name, 'synthetic-key')
            run.assert_not_called()

    def test_failure_receipt_redacts_key_and_base64(self):
        key = 'synthetic-test-only-key'
        encoded = base64.b64encode(key.encode()).decode()
        with tempfile.TemporaryDirectory() as temp:
            record = Path(temp)
            with patch('dataqa_config.subprocess.run', return_value=SimpleNamespace(returncode=1, stderr=f'error {key} {encoded}'.encode())):
                with self.assertRaisesRegex(RuntimeError, 'DATAQA_SECRET_CREATE_FAILED'):
                    dq.create_key_secret(['kubectl'], 'valid-name', key, record=record)
            content = (record / 'secret-create.PRIVATE.err').read_text()
            self.assertNotIn(key, content)
            self.assertNotIn(encoded, content)
            self.assertIn('error', content)

"""Retain installer boundaries and test Linux path literals without normalization."""
from pathlib import Path, PurePosixPath
import ast
import json
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
source = (HERE.parent / 'webhook-skill-20261116-r1/test_installation.py').read_text(encoding='utf-8')
source = source.replace('portal-energy-webhook-skill-install-20261116-r2', 'portal-energy-webhook-skill-install-20261116-r3')
source = source.replace('.energy-webhook-skill-install-20261116-r2-', '.energy-webhook-skill-install-20261116-r3-')
source = source.replace("if __name__ == '__main__':\n    unittest.main()", '')
exec(compile(source, '<retained-installer-boundaries>', 'exec'), globals())


class PosixPathChecks(unittest.TestCase):
    def test_raw_server_literal_is_absolute_on_posix(self):
        tree = ast.parse(FILES[builder.NAME].decode())
        node = next(n for n in tree.body if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == 'OLD_INSTALL' for t in n.targets))
        literal = ast.literal_eval(node.value.args[0])
        self.assertEqual(literal, builder.EXPECTED_PATH)
        self.assertTrue(PurePosixPath(literal).is_absolute())
        self.assertNotIn('\\', literal)

    def test_prior_serialization_reproduces_the_linux_missing_file_cause(self):
        prior = builder.REPO / 'release/energy-webhook-skill-20261116-r2/install-energy-webhook-skill-20261116-r2.py'
        tree = ast.parse(prior.read_text(encoding='utf-8'))
        node = next(n for n in tree.body if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == 'OLD_INSTALL' for t in n.targets))
        literal = ast.literal_eval(node.value.args[0])
        self.assertFalse(PurePosixPath(literal).is_absolute())
        self.assertEqual(literal.replace('\\', '/'), builder.EXPECTED_PATH)

    def test_skill_zip_verifier_and_fixtures_are_exactly_unchanged(self):
        prior = builder.REPO / 'release/energy-webhook-skill-20261116-r2'
        self.assertEqual(ASSETS['package'], (prior / 'energy-market-daily-2.1.7-public-code.zip').read_bytes())
        self.assertEqual(ASSETS['verifier'], (prior / 'verify_child.sealed.py').read_bytes())
        self.assertEqual(host['OFFLINE_TEST_COUNT'], 57)
        self.assertTrue(META['skill_payload_unchanged_from_r2'])

    def test_absent_receipt_has_specific_redacted_error(self):
        with patch.dict(host, {'read': lambda path: (_ for _ in ()).throw(FileNotFoundError('private path'))}):
            with self.assertRaisesRegex(host['Refused'], '^ACCEPTED_SKILL_INSTALL_RECEIPT_NOT_FOUND$'):
                host['qualification_binding']()


if __name__ == '__main__':
    unittest.main()

"""Retain multi-node and privacy checks with the new exact 110-file inventory."""
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
source = (HERE.parent / 'html-visibility-20261114-r1/test_views.py').read_text(encoding='utf-8')
source = source.replace('sandbox-0636d6b8', 'sandbox-0147427f').replace('ecs-znws-0004', 'ecs-znws-0003')
source = source.replace('len(rows), 108', 'len(rows), 110').replace("result['difference_count'], 109", "result['difference_count'], 111")
source = source.replace("if __name__ == '__main__':\n    unittest.main()", '')
exec(compile(source, '<retained-multi-node-readonly-checks>', 'exec'), globals())


class CountChecks(unittest.TestCase):
    def test_child_count_is_bound_to_the_actual_expected_rows(self):
        import ast
        tree = ast.parse(CHILD)
        values = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for name in node.targets:
                    if isinstance(name, ast.Name) and name.id in ('EXPECTED_ROWS', 'EXPECTED_SKILL_COUNT'):
                        values[name.id] = ast.literal_eval(node.value)
        self.assertEqual(values['EXPECTED_SKILL_COUNT'], len(values['EXPECTED_ROWS']))
        self.assertEqual(values['EXPECTED_SKILL_COUNT'], 110)

    def test_file_count_bug_is_not_a_reason_to_reinstall(self):
        self.assertIn('--verify-readonly', RAW.decode())
        self.assertNotIn('--install-webhook-skill-once', RAW.decode())
        self.assertIn('get(\'run\') == 57', RAW.decode())


if __name__ == '__main__':unittest.main()

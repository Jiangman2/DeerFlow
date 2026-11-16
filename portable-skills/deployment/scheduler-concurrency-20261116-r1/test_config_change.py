"""Only the operator-approved scheduler limit may change; budgets stay intact."""
import importlib.util
from pathlib import Path
import unittest

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('scheduler_limit_edit',HERE/'config_change.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

BASE=b'''# retained configuration\nmax_recursion_limit: 10000\nscheduler:\n  enabled: true\n  multi_instance: true\n  max_concurrent_runs: 3 # global, not per Pod\n  recursion_limit: 10000\nsandbox:\n  replicas: 6\n  idle_timeout: 600\ndatabase:\n  password: FAKE_KEEP_PRIVATE\n'''


class ConfigChangeTests(unittest.TestCase):
    def test_only_one_literal_changes_and_comment_is_preserved(self):
        result=module.replace_concurrency(BASE,3)
        self.assertEqual(result,BASE.replace(b'max_concurrent_runs: 3 #',b'max_concurrent_runs: 16 #'))

    def test_absent_value_can_use_confirmed_default_without_rewriting_file(self):
        raw=BASE.replace(b'  max_concurrent_runs: 3 # global, not per Pod\n',b'')
        result=module.replace_concurrency(raw,3)
        self.assertEqual(result,raw.replace(b'scheduler:\n',b'scheduler:\n  max_concurrent_runs: 16\n'))

    def test_current_production_value_must_match_fresh_config(self):
        with self.assertRaisesRegex(module.Refused,'CURRENT_CONCURRENCY_MISMATCH'):
            module.replace_concurrency(BASE,8)

    def test_already_sixteen_is_no_change_and_larger_is_not_downgraded(self):
        raw=BASE.replace(b'max_concurrent_runs: 3',b'max_concurrent_runs: 16')
        self.assertEqual(module.replace_concurrency(raw,16),raw)
        with self.assertRaisesRegex(module.Refused,'CONCURRENCY_DECREASE_NOT_AUTHORIZED'):
            module.replace_concurrency(BASE.replace(b'max_concurrent_runs: 3',b'max_concurrent_runs: 32'),32)

    def test_crlf_bytes_are_preserved(self):
        raw=BASE.replace(b'\n',b'\r\n')
        self.assertEqual(module.replace_concurrency(raw,3),raw.replace(b'max_concurrent_runs: 3 #',b'max_concurrent_runs: 16 #'))

    def test_duplicate_scheduler_key_is_refused(self):
        raw=BASE+b'scheduler:\n  max_concurrent_runs: 3\n'
        with self.assertRaisesRegex(module.Refused,'YAML_DUPLICATE_KEY'):
            module.replace_concurrency(raw,3)

    def test_yaml_alias_cannot_change_an_unrelated_section(self):
        raw=BASE.replace(b'scheduler:\n',b'scheduler: &shared\n')+b'other: *shared\n'
        with self.assertRaisesRegex(module.Refused,'CONFIGURATION_CHANGE_SCOPE_EXCEEDED'):
            module.replace_concurrency(raw,3)

    def test_noninteger_limit_and_missing_scheduler_are_rejected(self):
        for raw in (BASE.replace(b'max_concurrent_runs: 3',b'max_concurrent_runs: true'),b'max_recursion_limit: 10000\n'):
            with self.assertRaises(module.Refused):module.replace_concurrency(raw,3)


if __name__=='__main__':unittest.main()

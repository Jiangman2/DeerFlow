"""Offline regression tests for explicitly authorized task interruption."""

import contextlib
import copy
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def activation_namespace():
    candidate = Path('/candidate.py')
    source = candidate if candidate.is_file() else Path(__file__).with_name('activate-oa-compat.py')
    namespace = {'__name__': 'interruption_test'}
    exec(compile(source.read_bytes(), str(source), 'exec'), namespace)
    return namespace


def active_observation():
    return {
        'status': 'PASS',
        'revisions': ['0020_ppt_confirmation'],
        'database_readonly': True,
        'schema_migrated': False,
        'active_runs': 23,
        'active_oa_jobs': 7,
        'active_callback_leases': 2,
    }


class ModeReached(Exception):
    """The parser and host check passed, before external evidence is read."""


class AuthorizedInterruptionTests(unittest.TestCase):
    def setUp(self):
        self.n = activation_namespace()

    def assert_invalid_flags(self, *flags):
        self.n['prior_failure_evidence'] = lambda: self.fail('Invalid flags must stop before evidence reads')
        self.n['command'] = lambda *args, **kwargs: self.fail('Invalid flags must stop before Kubernetes access')
        with patch('sys.argv', ['activate'] + list(flags)), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                self.n['main']()
        self.assertEqual(raised.exception.code, 2)

    def assert_valid_mode_reaches_preflight(self, *flags):
        def evidence():
            raise ModeReached()

        self.n['prior_failure_evidence'] = evidence
        self.n['command'] = lambda *args, **kwargs: self.fail('No Kubernetes call belongs in this parser test')
        with patch.object(self.n['os'], 'geteuid', return_value=0, create=True), \
                patch.object(self.n['os'], 'uname', return_value=SimpleNamespace(nodename='datax-01'), create=True), \
                patch('sys.argv', ['activate'] + list(flags)), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(ModeReached):
                self.n['main']()

    def test_tasks_drained_and_allow_interrupt_are_mutually_exclusive(self):
        self.assert_invalid_flags('--resume-compatibility-once', '--tasks-drained', '--allow-interrupt-active-tasks')

    def test_resume_still_requires_explicit_task_policy(self):
        self.assert_invalid_flags('--resume-compatibility-once')

    def test_readonly_inspection_rejects_allow_interrupt(self):
        self.assert_invalid_flags('--inspect-tasks-readonly', '--allow-interrupt-active-tasks')

    def test_readonly_inspection_rejects_tasks_drained(self):
        self.assert_invalid_flags('--inspect-tasks-readonly', '--tasks-drained')

    def test_allow_interrupt_is_not_an_implicit_resume_mode(self):
        self.assert_invalid_flags('--allow-interrupt-active-tasks')

    def test_explicit_interruption_mode_reaches_normal_preflight(self):
        self.assert_valid_mode_reaches_preflight('--resume-compatibility-once', '--allow-interrupt-active-tasks')

    def test_drained_mode_remains_available(self):
        self.assert_valid_mode_reaches_preflight('--resume-compatibility-once', '--tasks-drained')

    def test_readonly_inspection_remains_available(self):
        self.assert_valid_mode_reaches_preflight('--inspect-tasks-readonly')

    def test_allowing_interruption_returns_nonzero_counts_unchanged(self):
        observation = active_observation()
        before = copy.deepcopy(observation)
        calls = []

        def remote(pod, args):
            calls.append((pod, args))
            return observation

        self.n['remote'] = remote
        pod = {'metadata': {'name': 'synthetic-gateway'}}
        result = self.n['database_gate'](pod, False)
        self.assertIs(result, observation)
        self.assertEqual(observation, before)
        self.assertEqual(calls, [(pod, {'action': 'database'})])

    def test_drained_mode_still_refuses_each_active_category_and_keeps_counts(self):
        for key in ('active_runs', 'active_oa_jobs', 'active_callback_leases'):
            with self.subTest(category=key):
                observation = active_observation()
                for category in ('active_runs', 'active_oa_jobs', 'active_callback_leases'):
                    observation[category] = 1 if category == key else 0
                self.n['remote'] = lambda *args: observation
                with self.assertRaisesRegex(self.n['Refused'], '^TASKS_STILL_ACTIVE$') as raised:
                    self.n['database_gate']({}, True)
                self.assertEqual(raised.exception.observation, observation)

    def test_allowing_interruption_does_not_bypass_schema_or_readonly_guards(self):
        for changes in ({'revisions': ['0021_oa_result_sets']}, {'revisions': []},
                        {'database_readonly': False}, {'database_readonly': 1}):
            with self.subTest(changes=changes):
                observation = dict(active_observation(), **changes)
                self.n['remote'] = lambda *args: observation
                with self.assertRaisesRegex(self.n['Refused'], '^DATABASE_BASELINE_CHANGED$'):
                    self.n['database_gate']({}, False)

    def test_allowing_interruption_rejects_missing_or_malformed_counts(self):
        for key in ('active_runs', 'active_oa_jobs', 'active_callback_leases'):
            for value in (None, True, False, -1, '7', 7.0, [], {}):
                with self.subTest(category=key, value=value):
                    observation = active_observation()
                    observation[key] = value
                    self.n['remote'] = lambda *args: observation
                    with self.assertRaisesRegex(self.n['Refused'], '^TASK_COUNT_SHAPE$'):
                        self.n['database_gate']({}, False)
            with self.subTest(category=key, missing=True):
                observation = active_observation()
                observation.pop(key)
                self.n['remote'] = lambda *args: observation
                with self.assertRaisesRegex(self.n['Refused'], '^TASK_COUNT_SHAPE$'):
                    self.n['database_gate']({}, False)

    def test_allowing_interruption_does_not_bypass_child_failure(self):
        commands = []

        def command(args, *unused, **kwargs):
            commands.append(args)
            return {'status': 'INCOMPLETE', 'error_class': 'TimeoutError', 'raw_errors_exported': False}

        self.n['command'] = command
        pod = {'metadata': {'name': 'synthetic-gateway'}, 'spec': {'containers': [{'name': 'gateway'}]}}
        with self.assertRaisesRegex(self.n['Refused'], '^LIVE_CHECK_FAILED$'):
            self.n['database_gate'](pod, False)
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0][0], 'exec')


if __name__ == '__main__':
    unittest.main()

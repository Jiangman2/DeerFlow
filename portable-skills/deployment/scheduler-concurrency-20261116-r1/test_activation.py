"""Exercise the derived rollout, semantic scope and lease-aware startup."""
import ast
import asyncio
import base64
import copy
import datetime as dt
import importlib.util
import json
import logging
from pathlib import Path
import types
import unittest
from unittest import mock

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('concurrency_builder',HERE/'build.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
HOST,CHILD,EXPECTED=builder.build()
host=types.ModuleType('concurrency_host');exec(compile(HOST,'<host>','exec'),host.__dict__)
child=types.ModuleType('concurrency_child');exec(compile(CHILD,'<child>','exec'),child.__dict__)
BASE=b'max_recursion_limit: 10000\nrun_ownership:\n  heartbeat_enabled: true\nscheduler:\n  enabled: true\n  multi_instance: false\n  max_concurrent_runs: 3 # preserve\n  recursion_limit: 10000\nkeep: true\n'


def replace(raw):
    with mock.patch.object(host,'OLD_CONFIG_BYTES',len(raw)),mock.patch.object(host,'OLD_CONFIG_SHA',host.sha(raw)):
        return host.replace_scheduler(raw)


# Retain the field-qualified r5 CAS, ownership/defaulting, endpoint, privacy,
# bounded real-subprocess and journal tests. Adapt only the release bindings
# and the synthetic qualification interface; historical tests stay unchanged.
parent=(HERE.parent/'scheduler-budget-20261113-r5/test_scheduler_budget.py').read_text(encoding='utf-8')
parent=parent.replace('415','418').replace('416','419').replace('len(sources), 13','len(sources), 17')
parent=parent.replace('--activate-scheduler-budget-once','--activate-concurrency-once')
parent=parent.replace('SCHEDULER_BUDGET_10000_DEPLOYED_RUNTIME_ACCEPTED','SCHEDULER_CONCURRENCY_32_DEPLOYED_RUNTIME_ACCEPTED')
old="""        with self.assertRaisesRegex(host.Refused, 'NOT_SEALED'):
            host.load_child()"""
new="""        self.assertEqual(host.load_child(), CHILD.decode())
        with mock.patch.object(host,'CHILD_ASSET',{}):
            with self.assertRaisesRegex(host.Refused,'NOT_SEALED'):
                host.load_child()"""
assert parent.count(old)==1;parent=parent.replace(old,new)
old="b'max_recursion_limit: 10000\\nscheduler:\\n  recursion_limit: 300\\nkeep: true\\n'"
assert parent.count(old)==1;parent=parent.replace(old,repr(BASE))
old="""                    'scheduler_recursion_limit': 300, 'candidate_scheduler_recursion_limit': 10000,
                    'max_recursion_limit': 10000, 'source_files_verified': 13, 'versions': child.PACKAGES,
                    'semantic_change_only_scheduler_recursion_limit': True"""
new="""                    'scheduler_recursion_limit':10000,'max_recursion_limit':10000,'max_concurrent_runs':3,
                    'candidate_max_concurrent_runs':32,'candidate_multi_instance':True,'run_ownership_heartbeat_enabled':True,
                    'source_files_verified':17,'versions':child.PACKAGES,
                    'semantic_change_only_scheduler_concurrency_and_multi_instance':True"""
assert parent.count(old)==1;parent=parent.replace(old,new)
tree=ast.parse(parent)
selected=[node for node in tree.body if isinstance(node,(ast.Import,ast.ImportFrom,ast.FunctionDef))
          or isinstance(node,ast.ClassDef) and node.name not in ('BudgetReplacementTests','StrictYamlTests')]
exec(compile(ast.fix_missing_locations(ast.Module(body=selected,type_ignores=[])),'<retained-r5-rollout-boundaries>','exec'),globals())


class ConcurrencyScopeTests(unittest.TestCase):
    def test_only_two_scheduler_values_and_no_budget_change(self):
        candidate=replace(BASE)
        self.assertEqual(candidate,BASE.replace(b'multi_instance: false',b'multi_instance: true').replace(b'max_concurrent_runs: 3 #',b'max_concurrent_runs: 32 #'))
        self.assertEqual(candidate.count(b'10000'),2)

    def test_missing_mode_is_inserted_as_true_with_crlf_preserved(self):
        raw=BASE.replace(b'  multi_instance: false\n',b'').replace(b'\n',b'\r\n')
        candidate=replace(raw)
        self.assertIn(b'scheduler:\r\n  multi_instance: true\r\n',candidate)
        self.assertNotIn(b'\n',candidate.replace(b'\r\n',b''))

    def test_default_concurrency_and_mode_can_both_be_omitted(self):
        raw=BASE.replace(b'  max_concurrent_runs: 3 # preserve\n',b'').replace(b'  multi_instance: false\n',b'')
        candidate=replace(raw)
        before=child.check_config(raw,{'bytes':len(raw),'sha256':child.digest(raw),'max_concurrent_runs':3,'multi_instance':False})
        after=child.check_config(candidate,{'bytes':len(candidate),'sha256':child.digest(candidate),'max_concurrent_runs':32,'multi_instance':True})
        expected=copy.deepcopy(before);expected['scheduler'].update(max_concurrent_runs=32,multi_instance=True)
        self.assertEqual(after,expected)

    def test_fixed_original_and_duplicate_or_noninteger_values_are_rejected(self):
        with self.assertRaises(host.Refused):host.replace_scheduler(BASE)
        for raw in (BASE.replace(b': 3 #',b': true #'),BASE.replace(b': 3 #',b': "3" #'),
                    BASE+b'scheduler:\n  max_concurrent_runs: 3\n',
                    BASE.replace(b'  multi_instance: false',b'  multi_instance: true')):
            with self.assertRaises(host.Refused):replace(raw)

    def test_semantic_two_field_difference_and_heartbeat_requirement(self):
        old={'bytes':len(BASE),'sha256':child.digest(BASE),'max_concurrent_runs':3,'multi_instance':False}
        candidate=replace(BASE)
        new={'bytes':len(candidate),'sha256':child.digest(candidate),'max_concurrent_runs':32,'multi_instance':True}
        before=child.check_config(BASE,old);after=child.check_config(candidate,new)
        expected=copy.deepcopy(before);expected['scheduler'].update(max_concurrent_runs=32,multi_instance=True)
        self.assertEqual(after,expected)
        no_heartbeat=BASE.replace(b'heartbeat_enabled: true',b'heartbeat_enabled: false')
        with self.assertRaisesRegex(child.Refused,'HEARTBEAT_REQUIRED'):
            child.check_config(no_heartbeat,dict(old,bytes=len(no_heartbeat),sha256=child.digest(no_heartbeat)))

    def test_strict_yaml_refuses_duplicate_alias_merge_and_unsafe_tag(self):
        for raw in (b'a: 1\na: 2\n',b'a: &v [1]\nb: *v\n',b'a: {<<: {x: 1}}\n',b'a: !!python/object/apply:os.system [echo]\n'):
            with self.assertRaises(Exception):child.parse_yaml(raw)

    def test_qualification_requires_exact_existing_seventeen_sources(self):
        self.assertEqual(set(child.SOURCE_PATHS),set(host.EXPECTED_SOURCES))
        self.assertEqual(len(host.EXPECTED_SOURCES),17)
        self.assertIn('gateway-task-context-86bbcfd1fb7f',host.OVERLAYS)
        self.assertIn('gateway-reader-notify-0a8ffbe51790',host.OVERLAYS)

    def test_activation_returns_after_one_patch_without_waiting(self):
        op=host.Operator('child');original=fixture()
        op.expected=original['spec'];current=copy.deepcopy(original)
        current['metadata']['generation']=419
        with mock.patch.object(op,'preflight',return_value={}),mock.patch.object(op,'claim'),mock.patch.object(op,'cutover') as cutover,\
             mock.patch.object(op,'get',return_value=current),mock.patch.object(op,'verify') as verify,mock.patch.object(op,'rollout') as wait:
            op.run()
        cutover.assert_called_once();verify.assert_not_called();wait.assert_not_called()
        self.assertEqual(op.report['status'],'GATEWAY_419_CONCURRENCY_32_ROLLOUT_REQUESTED')
        self.assertTrue(op.report['verify_readonly_available'])

    def test_build_is_deterministic_across_repeated_calls(self):
        self.assertEqual(builder.build()[:2],(HOST,CHILD))


class LeaseAwareStartupTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self,multi):
        path=builder.ROOT/'backend/app/scheduler/service.py'
        raw=path.read_bytes()
        self.assertEqual(builder.sha(raw),host.EXPECTED_SOURCES['/app/backend/app/scheduler/service.py']['sha256'])
        klass=next(node for node in ast.parse(raw).body if isinstance(node,ast.ClassDef) and node.name=='ScheduledTaskService')
        start=next(node for node in klass.body if isinstance(node,ast.AsyncFunctionDef) and node.name=='start')
        tree=ast.fix_missing_locations(ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),start],type_ignores=[]))
        scope={'asyncio':asyncio,'datetime':dt.datetime,'UTC':dt.timezone.utc,'_RESTART_RECOVERY_ERROR':'restart','logger':logging.getLogger('test')}
        exec(compile(tree,'<actual-deployed-scheduler-start>','exec'),scope)
        event=asyncio.Event()
        state=types.SimpleNamespace(_task=None,_multi_instance=multi,_stop=event,_skip_next_lease_reconciliation=False,
                                    _reconcile_active_state=mock.AsyncMock(),_notification_recovery_options=lambda:{},
                                    _task_run_repo=types.SimpleNamespace(mark_stale_active_runs=mock.AsyncMock(return_value=0)),
                                    _task_repo=types.SimpleNamespace(cancel_stuck_once_tasks=mock.AsyncMock(return_value=0)),
                                    _run_loop=event.wait,start_notifications=mock.Mock())
        await scope['start'](state)
        try:
            if multi:
                state._reconcile_active_state.assert_awaited_once()
                state._task_run_repo.mark_stale_active_runs.assert_not_awaited()
                state._task_repo.cancel_stuck_once_tasks.assert_not_awaited()
                self.assertTrue(state._skip_next_lease_reconciliation)
            else:
                state._task_run_repo.mark_stale_active_runs.assert_awaited_once()
                state._task_repo.cancel_stuck_once_tasks.assert_awaited_once()
        finally:
            event.set();await state._task

    async def test_multi_instance_uses_lease_reconciliation_not_global_restart_sweep(self):await self.exercise(True)
    async def test_legacy_false_mode_reproduces_the_restart_sweep(self):await self.exercise(False)


if __name__=='__main__':unittest.main()

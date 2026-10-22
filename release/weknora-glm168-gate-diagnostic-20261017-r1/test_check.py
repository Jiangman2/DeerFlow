import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock,patch

spec=importlib.util.spec_from_file_location('gate_diagnostic',os.environ.get('CHECK',str(Path(__file__).with_name('check.py'))))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def row():return dict(read_only='on',max_connections=100,reserved_connections=3,client_backends=18,
    database_client_backends=18,parallel_workers=39,idle_in_transaction=0,idle_in_transaction_aborted=0,
    oldest_idle_transaction_seconds=None,lock_waiters=0,waiter_roles=[],blocker_roles=[])


def snapshot(current=11,peak=16):return dict(values=dict(v1_memory_usage=[current*m.GIB],v1_memory_peak=[peak*m.GIB]))


class Tests(unittest.TestCase):
    def test_healthy_gate(self):self.assertTrue(m.gate(row())['original_gate_would_pass_now'])

    def test_each_failure_has_specific_condition(self):
        for key,value,condition in (('client_backends',59,'connection_headroom'),('lock_waiters',1,'no_lock_waiters'),('idle_in_transaction',1,'no_idle_transactions')):
            r=row();r[key]=value;out=m.gate(r)
            self.assertFalse(out['original_gate_conditions'][condition]);self.assertFalse(out['original_gate_would_pass_now'])

    def test_gate_threshold_exact(self):
        r=row();r['max_connections']=103;r['reserved_connections']=3;r['client_backends']=60
        self.assertFalse(m.gate(r)['original_gate_would_pass_now'])
        r['client_backends']=59;self.assertTrue(m.gate(r)['original_gate_would_pass_now'])

    def test_parallel_workers_not_connections(self):
        out=m.gate(row());self.assertEqual(out['client_backends'],18);self.assertEqual(out['parallel_workers'],39)
        self.assertTrue(out['original_gate_would_pass_now'])

    def test_history_not_reconstructed(self):self.assertEqual(m.gate(row())['historical_failed_fifth_sample'],'NOT_RETAINED_NO_RETROSPECTIVE_CAUSE_CLAIM')

    def test_unknown_role_not_exported(self):
        r=row();r['blocker_roles']=[dict(backend_type='SECRET',records=1)]
        with self.assertRaises(m.Failure):m.gate(r)

    def test_non_numeric_count_rejected(self):
        r=row();r['lock_waiters']=True
        with self.assertRaises(m.Failure):m.gate(r)

    def test_nan_age_rejected(self):
        r=row();r['oldest_idle_transaction_seconds']=float('nan')
        with self.assertRaises(m.Failure):m.gate(r)

    def test_16Gi_peak_explains_both_formula_blocks(self):
        out=m.memory_inputs(snapshot(),2*m.GIB)
        self.assertEqual(out['observed_input_used_by_old_formula_bytes'],16*m.GIB)
        self.assertEqual(out['scenarios'][0]['estimated_required_bytes'],36*m.GIB)
        self.assertEqual(out['scenarios'][1]['estimated_required_bytes'],28*m.GIB)
        self.assertTrue(all(not x['estimate_would_pass'] for x in out['scenarios']))

    def test_low_current_rss_does_not_remove_historical_peak(self):
        out=m.memory_inputs(snapshot(current=2,peak=16),1*m.GIB)
        self.assertEqual(out['observed_input_used_by_old_formula_bytes'],16*m.GIB)
        self.assertEqual(out['historical_peak_breakdown'],'NOT_MEASURED_DO_NOT_SUBTRACT_CURRENT_CACHE')

    def test_v2_supported(self):
        out=m.memory_inputs(dict(values=dict(v2_memory_current=[5*m.GIB],v2_memory_peak=[8*m.GIB])))
        self.assertEqual(out['cgroup_lifetime_peak_bytes'],8*m.GIB)

    def test_missing_peak_not_zero(self):
        with self.assertRaises(m.Failure):m.memory_inputs(dict(values=dict(v1_memory_usage=[123])))

    def test_sql_readonly_bounded_and_no_raw_queries(self):
        self.assertTrue(m.GATE_SQL.startswith('BEGIN READ ONLY'))
        self.assertIn("statement_timeout='5s'",m.GATE_SQL);self.assertIn("lock_timeout='1s'",m.GATE_SQL)
        for text in ('SELECT query','query,','api_key','pg_terminate_backend','pg_cancel_backend'):self.assertNotIn(text,m.GATE_SQL)

    def test_projection_excludes_extra_fields(self):
        r=row();r['query']='SECRET';self.assertNotIn('SECRET',json.dumps(m.gate(r)))

    def test_memory_probe_real_linux(self):
        r=subprocess.run([sys.executable,'-I','-B','-c',m.MEMORY_CODE],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr);out=json.loads(r.stdout)
        self.assertIn('VmRSS',out['process_memory_bytes']);self.assertNotIn('cmdline',out)

    def test_safe_private_reads_and_symlink_refusal(self):
        with tempfile.TemporaryDirectory(dir='/var/lib') as tmp:
            p=Path(tmp)/'x';p.write_bytes(b'x');p.chmod(0o600)
            self.assertEqual(m.safe_read(p,m.hashlib.sha256(b'x').hexdigest()),b'x')
            link=Path(tmp)/'link';link.symlink_to(p)
            with self.assertRaises(OSError):m.safe_read(link,m.hashlib.sha256(b'x').hexdigest())

    def test_wrong_host_before_reads(self):
        with patch.object(m.socket,'gethostname',return_value='other'),patch.object(m,'load',side_effect=AssertionError('read')):
            with self.assertRaises(m.Failure):m.main()

    def test_database_failure_values_printed_before_resource_failure(self):
        old=dict(glm168_review='INCOMPLETE',phase='FRESH_SAMPLE_5',failure=dict(check='database_headroom_requires_review'),
            current_pod=dict(uid='uid',name='app'),cgroup_before=snapshot(),samples=[])
        app=dict(metadata=dict(uid='uid',name='app'));pg=dict(metadata=dict(name='pg'))
        c=types.SimpleNamespace(obs=types.SimpleNamespace(identity=lambda p:['uid'],container=lambda p:dict(name='pg'),
            call=Mock(side_effect=[json.dumps(dict(row(),lock_waiters=1)).encode(),b'{}']*3),resource_snapshot=Mock(side_effect=RuntimeError('resource_read_failed'))),
            e=types.SimpleNamespace(runtime_env=Mock(),review=types.SimpleNamespace(pg_binding=Mock(return_value=(pg,['pg'])),DB_SHELL='safe')),
            deep=types.SimpleNamespace(cgroup=Mock(return_value=snapshot()),cgroup_delta=Mock(return_value={})),
            f=types.SimpleNamespace(waits=lambda r:{}),r=types.SimpleNamespace(sw=types.SimpleNamespace(same=lambda a,b:a==b)))
        frozen=types.SimpleNamespace(APP_UID='uid',bind_inputs=Mock(return_value=({}, {}, {})),current=Mock(return_value=({},app)),ENV_KEYS={'model':'MODEL'},CURRENT={})
        output=io.StringIO()
        with patch.object(m,'safe_read',return_value=json.dumps(old).encode()),patch.object(m.time,'sleep'),redirect_stdout(output):
            self.assertEqual(m.collect(frozen,c),1)
        rows=[json.loads(x) for x in output.getvalue().splitlines()]
        self.assertEqual(rows[1]['database_gate']['lock_waiters'],1)
        self.assertFalse(rows[1]['database_gate']['original_gate_would_pass_now'])
        self.assertIn('memory_unavailable',rows[2]);self.assertFalse(rows[-1]['ready_to_expand'])

    def test_no_mutation_or_old_collect_calls(self):
        import ast
        tree=ast.parse(Path(m.__file__).read_text())
        names={x.func.attr for x in ast.walk(tree) if isinstance(x,ast.Call) and isinstance(x.func,ast.Attribute)}
        self.assertFalse(names&{'collect','dry_run','proposal','apply','signal_once','reconcile','capture_startup_limit'})

    def test_error_redaction(self):self.assertNotIn('SECRET',json.dumps(m.error(ValueError('SECRET'))))


if __name__=='__main__':unittest.main(verbosity=2)

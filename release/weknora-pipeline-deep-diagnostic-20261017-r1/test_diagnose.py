import ast
import importlib.util
import json
import io
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

import package


def load_target():
    target = os.environ.get('DIAGNOSTIC')
    if target:
        spec = importlib.util.spec_from_file_location('qualified_diagnostic', target)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    else:
        mod = types.ModuleType('qualified_diagnostic'); mod.__file__ = 'source-with-packaged-projection.py'
        exec(compile(package.payload(), mod.__file__, 'exec'), mod.__dict__)
    return mod


d = load_target()


def stats(values):
    values = sorted(values)
    return dict(count=len(values), p50_seconds=values[math.ceil(.5*len(values))-1] if values else None,
                p95_seconds=values[math.ceil(.95*len(values))-1] if values else None)


def log_stats(raw):
    out = {k:[] for k in ('parse_completed','parse_failed','dispatch_elapsed','dispatch_wait_interactive','dispatch_wait_bulk')}
    text = raw.decode()
    import re
    m = re.search(r' status=(completed|failed) elapsed_seconds=([0-9.]+)', text)
    if m: out['parse_'+m[1]].append(float(m[2]))
    return out, 0


def observer():
    return types.SimpleNamespace(summary_stats=stats, log_stats=log_stats, require=d.need)


def qrow(epoch, processed=100, failed=5, pending=20):
    return dict(redis_sample_epoch=epoch, queues={'default':dict(pending=pending, active=1, retry=0,
                 processed_total=processed, failed_total=failed, oldest_pending_seconds=10)})


class PureTests(unittest.TestCase):
    def test_payload_compiles_on_current_interpreter(self):
        compile(package.payload(), 'delivery', 'exec')
        self.assertIsInstance(d.SPAN_HELPER, str)

    def test_embedded_projection_contains_no_mutator(self):
        tree = ast.parse(d.SPAN_HELPER)
        names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        self.assertEqual(names, package.FUNCTIONS)
        self.assertFalse(any(isinstance(n, (ast.Import, ast.ImportFrom)) for n in tree.body))
        self.assertIn('BEGIN READ ONLY', d.SPAN_HELPER)
        self.assertIn('LIMIT 20000', d.SPAN_HELPER)

    def test_safe_error_redacts_external_exception(self):
        self.assertNotIn('SECRET', json.dumps(d.safe_error(ValueError('SECRET customer.pdf'))))

    def test_safe_error_only_safe_check(self):
        self.assertEqual(d.safe_error(d.Failure('bounded_read'))['check'], 'bounded_read')
        self.assertEqual(d.safe_error(d.Failure('SECRET /private/file'))['check'], 'read_unavailable')

    def test_timestamp_nanoseconds(self):
        self.assertEqual(d.timestamp('2026-10-17T20:00:00.123456789+08:00 text'), '2026-10-17T12:00:00+00:00')

    def test_timestamp_invalid(self):
        self.assertIsNone(d.timestamp('2026-99-17T20:00:00Z SECRET'))
        self.assertIsNone(d.timestamp('SECRET'))

    def test_log_redacts_credentials_and_document(self):
        a = d.Aggregate(observer(), 'app')
        a.line(b'2026-10-17T12:00:00Z [ImageMultimodal] Caption failed for customer.pdf: enterprise GLM: HTTP status 503 Bearer SECRET\n')
        r = a.result()
        self.assertEqual(r['marker_lines']['image_caption_failure'], 1)
        self.assertEqual(r['marker_lines']['glm_http_5xx'], 1)
        self.assertNotIn('SECRET', json.dumps(r)); self.assertNotIn('customer.pdf', json.dumps(r))

    def test_numeric_timings_only(self):
        a = d.Aggregate(observer(), 'worker')
        a.line(b'2026-10-17T12:00:00Z Parse finished request_id=abc filename=SECRET status=completed elapsed_seconds=1.234\n')
        self.assertEqual(a.result()['timings']['parse_completed']['p50_seconds'], 1.234)
        self.assertNotIn('SECRET', json.dumps(a.result()))

    def test_oversize_line_is_not_saved(self):
        a = d.Aggregate(observer(), 'app'); a.line(b'SECRET'*(d.LINE_BYTES//6+1))
        self.assertEqual(a.result()['oversized_lines_dropped'], 1)
        self.assertNotIn('SECRET', json.dumps(a.result()))

    def test_timing_cap(self):
        a = d.Aggregate(observer(), 'worker')
        with patch.object(d, 'TIMING_CAP', 1):
            for _ in range(2): a.line(b'Parse finished request_id=x status=completed elapsed_seconds=1.0\n')
        self.assertTrue(a.result()['timing_cap_reached'])
        self.assertEqual(a.result()['timings']['parse_completed']['count'], 1)

    def test_follow_command_only_logs(self):
        command = d.follow_command('weknora-app-abc', 'app', '2026-10-17T12:00:00Z')
        self.assertIn('--follow=true', command); self.assertIn('--tail=-1', command)
        self.assertNotIn('--limit-bytes=524288', command)
        self.assertNotIn('exec', command)

    def test_follow_target_rejects_shell_and_url(self):
        for value in ('bad;kill', '../x', 'https://evil', 'UPPER'):
            with self.assertRaises(d.Failure): d.follow_command(value, 'app', 'x')

    def test_cgroup_delta_throttled_period_not_cpu_time(self):
        a = dict(pod='p', container_identity=['same'], values={'v2_cpu_stat':dict(nr_periods=100,nr_throttled=20),
                      'v1_memory_failcnt':[2]})
        b = dict(pod='p', container_identity=['same'], values={'v2_cpu_stat':dict(nr_periods=120,nr_throttled=25),
                      'v1_memory_failcnt':[4]})
        r = d.cgroup_delta(a,b)
        self.assertEqual(r['counters']['v2_cpu_stat']['throttled_period_fraction'], .25)
        self.assertEqual(r['counters']['v1_memory_failcnt']['delta'], 2)

    def test_cgroup_counter_reset(self):
        a = dict(pod='p',container_identity='same',values={'v2_cpu_stat':{'nr_periods':10}})
        b = dict(pod='p',container_identity='same',values={'v2_cpu_stat':{'nr_periods':1}})
        self.assertTrue(d.cgroup_delta(a,b)['counters']['v2_cpu_stat']['counter_reset'])

    def test_cgroup_changed_container_not_compared(self):
        self.assertFalse(d.cgroup_delta({'container_identity':'old'},{'container_identity':'new'})['available'])

    def test_resource_spec_no_env_or_credentials(self):
        pod=dict(metadata=dict(name='p'),spec=dict(nodeName='n'))
        o=types.SimpleNamespace(container=lambda p:dict(env=[dict(value='SECRET')],resources=dict(limits=dict(cpu='2',memory='4Gi',secret='SECRET'))),identity=lambda p:['p','uid'])
        r=d.resource_spec(o,pod)
        self.assertEqual(r['limits'],dict(cpu='2',memory='4Gi')); self.assertNotIn('SECRET',json.dumps(r))

    def test_queue_delta_and_not_document_count(self):
        r=d.queue_summary([dict(queues=qrow(100)),dict(queues=qrow(160,120,7,25))])['default']
        self.assertEqual(r['processed_attempts_delta'],20)
        self.assertEqual(r['estimated_successful_attempts_delta'],18)
        self.assertEqual(r['pending_net_change'],5)
        self.assertEqual(r['processed_attempts_per_minute'],20)

    def test_queue_midwindow_counter_reset(self):
        r=d.queue_summary([dict(queues=qrow(100,100)),dict(queues=qrow(120,1)),dict(queues=qrow(160,150))])['default']
        self.assertTrue(r['counter_reset_or_rollover']); self.assertNotIn('processed_attempts_delta',r)

    def test_queue_insufficient_samples(self):
        self.assertEqual(d.queue_summary([]),{})
        self.assertEqual(d.queue_summary([dict(queues=qrow(1))]),{})

    def test_failure_does_not_claim_measurements(self):
        r=d.summarize(dict(diagnostic='INCOMPLETE',samples=[],warnings=[],utc_started='x',utc_finished='y'))
        self.assertFalse(r['ready_to_expand']); self.assertFalse(r['baseline_stable'])
        self.assertEqual(r['local_model_waiters'],'NOT_MEASURED')

    def test_sql_is_read_only_and_no_secret_columns(self):
        self.assertIn('BEGIN READ ONLY',d.DB_SQL)
        self.assertIn("statement_timeout='5s'",d.DB_SQL)
        self.assertNotIn('api_key',d.DB_SQL); self.assertNotIn('UPDATE ',d.DB_SQL)
        self.assertNotIn('error_message',d.DB_SQL)

    def test_database_projection(self):
        h=types.SimpleNamespace(sql_read=lambda p,s:dict(transaction_read_only='on',max_connections=100,
            all_connections=10,this_database=5,idle_in_transaction=0,models=[dict(id=d.MODELS[0],max_concurrency=0)]))
        row=d.database_snapshot(h,{})
        self.assertTrue(row['zero_model_limit_means_startup_default_not_infinite'])

    def test_database_unknown_model_rejected(self):
        h=types.SimpleNamespace(sql_read=lambda p,s:dict(transaction_read_only='on',max_connections=100,
            all_connections=10,this_database=5,idle_in_transaction=0,models=[dict(id='SECRET',max_concurrency=2)]))
        with self.assertRaises(d.Failure): d.database_snapshot(h,{})

    def test_cgroup_probe_compiles(self):
        compile(d.CGROUP_CODE,'remote_probe','exec')
        self.assertNotIn('environ',d.CGROUP_CODE); self.assertNotIn('write(',d.CGROUP_CODE)

    def probe_fixture(self, mapping):
        def opened(path, *args, **kwargs):
            if path not in mapping: raise FileNotFoundError(path)
            return io.StringIO(mapping[path])
        out=io.StringIO()
        with patch('builtins.open',side_effect=opened), patch('sys.stdout',out):
            exec(compile(d.CGROUP_CODE,'probe','exec'),{'__name__':'probe'})
        return json.loads(out.getvalue())

    def test_v1_cgroup_resolves_child_not_host(self):
        row=self.probe_fixture({
            '/proc/self/cgroup':'4:memory:/kubepods/pod-a/container-a\n3:cpu,cpuacct:/kubepods/pod-a/container-a\n',
            '/proc/self/mountinfo':'22 21 0:2 / /sys/fs/cgroup/memory ro - cgroup cgroup rw,memory\n23 21 0:3 / /sys/fs/cgroup/cpu,cpuacct ro - cgroup cgroup rw,cpu,cpuacct\n',
            '/sys/fs/cgroup/memory/kubepods/pod-a/container-a/memory.limit_in_bytes':'4096\n',
            '/sys/fs/cgroup/memory/memory.limit_in_bytes':'999999\n',
            '/sys/fs/cgroup/cpu,cpuacct/kubepods/pod-a/container-a/cpu.stat':'nr_periods 100\nnr_throttled 5\nthrottled_time 123\n'})
        self.assertEqual(row['values']['v1_memory_limit'],[4096])
        self.assertEqual(row['values']['v1_cpu_stat']['nr_throttled'],5)
        self.assertNotIn('999999',json.dumps(row))

    def test_v1_cgroup_bind_mount_root(self):
        row=self.probe_fixture({'/proc/self/cgroup':'4:memory:/pod/container\n',
            '/proc/self/mountinfo':'22 21 0:2 /pod/container /sys/fs/cgroup/memory ro - cgroup cgroup rw,memory\n',
            '/sys/fs/cgroup/memory/memory.limit_in_bytes':'2048\n'})
        self.assertEqual(row['values']['v1_memory_limit'],[2048])

    def test_cgroup_unmatched_mount_no_global_fallback(self):
        row=self.probe_fixture({'/proc/self/cgroup':'4:memory:/pod/container\n',
            '/proc/self/mountinfo':'22 21 0:2 /other /sys/fs/cgroup/memory ro - cgroup cgroup rw,memory\n',
            '/sys/fs/cgroup/memory/memory.limit_in_bytes':'999999\n'})
        self.assertEqual(row['values'],{})

    def test_v2_unlimited_memory_not_zero(self):
        row=self.probe_fixture({'/proc/self/cgroup':'0::/pod/container\n',
            '/proc/self/mountinfo':'22 21 0:2 /pod/container /sys/fs/cgroup ro - cgroup2 cgroup rw\n',
            '/sys/fs/cgroup/memory.max':'max\n', '/sys/fs/cgroup/cpu.max':'200000 100000\n',
            '/sys/fs/cgroup/memory.events':'low 0\nmax 4\noom 1\noom_kill 1\n'})
        self.assertEqual(row['values']['v2_memory_max'],['max'])
        self.assertEqual(row['values']['v2_memory_events']['oom_kill'],1)

    def test_real_cgroup_read_only_probe(self):
        r=subprocess.run([sys.executable,'-I','-B','-c',d.CGROUP_CODE],stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10,check=True)
        value=json.loads(r.stdout)
        self.assertEqual(value['source'],'CURRENT_EXEC_CONTAINER_CGROUP')
        self.assertIsInstance(value['values'],dict); self.assertTrue(value['values'])
        self.assertEqual(value['identity_binding'],'PROC_SELF_CGROUP_AND_MOUNTINFO')

    def test_embedded_index_plan_rejects_seq_scan(self):
        h=types.ModuleType('h'); h.__dict__.update(obs=observer(),math=math,json=json)
        exec(compile(d.SPAN_HELPER,'h','exec'),h.__dict__)
        self.assertEqual(h.clean_plan([{'Plan':{'Node Type':'Limit','Plans':[{'Node Type':'Index Scan', 'Relation Name':'knowledge_processing_spans'}]}}]),['Limit','Index Scan'])
        with self.assertRaises(d.Failure): h.clean_plan([{'Plan':{'Node Type':'Seq Scan'}}])

    def test_projection_cutoff_injection_rejected(self):
        h=types.ModuleType('h'); h.__dict__.update(obs=observer(),math=math,json=json)
        exec(compile(d.SPAN_HELPER,'h','exec'),h.__dict__)
        with self.assertRaises(d.Failure): h.span_sql('1;DELETE')

    def test_model_shell_only_read_commands(self):
        self.assertIn('ZCOUNT',d.MODEL_SHELL)
        for value in ('ZREM','ZADD','SET ', 'EVAL','HGETALL'): self.assertNotIn(value,d.MODEL_SHELL)

    def test_model_leases_not_waiters(self):
        raw=('T\t%d\n' % int(time.time())+''.join('M\t%s\t3\n' % x for x in d.MODELS)+'END\n').encode()
        calls=[]
        def call(args,**kwargs): calls.append(args); return raw
        o=types.SimpleNamespace(call=call,container=lambda p:dict(name='redis'))
        r=d.model_snapshot(o,({'metadata':{'name':'redis-pod'}},'0','app'))
        self.assertEqual(len(r['holders']),4); self.assertEqual(r['local_waiters'],'NOT_MEASURED')
        self.assertEqual(calls[0][0],'exec'); self.assertIn('ZCOUNT',calls[0][7])

    def test_initialize_imports_only_frozen_reader(self):
        switch="""from types import SimpleNamespace as N
def load_review():
 act=N(review=N())
 readers=N(obs=N(require=lambda ok,msg:None),e=act)
 return N(TARGET=%r,o=N(r=readers))
def main(): raise AssertionError('MUTATOR_CLI_MUST_NOT_RUN')
""" % d.CAPS
        prior="""from pathlib import Path
TOOL=Path('/var/lib/frozen.py')
TOOL_SHA='pinned'
def bind_receipt(): pass
def safe_read(path,expected): return %r
def main(): raise AssertionError('OLD_CLI_MUST_NOT_RUN')
""" % switch.encode()
        with patch.object(d,'safe_read',return_value=prior.encode()):
            old,o,act,h=d.initialize()
        self.assertTrue(callable(h.span_snapshot)); self.assertTrue(callable(o.require))
        self.assertEqual(vars(act.review),{})

    def test_database_write_denied(self):
        h=types.SimpleNamespace(sql_read=lambda p,s:dict(transaction_read_only='off'))
        with self.assertRaises(d.Failure): d.database_snapshot(h,{})

    def test_launch_preserves_both_exit_codes(self):
        text=package.launcher('a'*64)
        self.assertIn('PIPESTATUS',text)
        self.assertIn('test "${RESULT[1]}" -eq 0',text)
        self.assertNotIn('systemd-run',text)

    def test_only_local_process_signals(self):
        source=Path(d.__file__).read_text() if Path(d.__file__).is_file() else package.payload().decode()
        tree=ast.parse(source)
        names={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
        self.assertFalse(names & {'signal_once','apply_after_drain','reconcile','killpg'})
        self.assertNotIn('SIGTSTP',source)


class StreamTests(unittest.TestCase):
    def execute(self, code, budget=1000000, delay=.4):
        a=d.Aggregate(observer(),'app'); stop=threading.Event(); ready=threading.Event(); box={}
        command=[sys.executable,'-I','-B','-u','-c',code]
        def run(): box.update(row=d.stream(command,a,stop,ready,budget=budget))
        t=threading.Thread(target=run); t.start(); self.assertTrue(ready.wait(3)); time.sleep(delay)
        stop.set(); t.join(5); self.assertFalse(t.is_alive())
        return a.result(),box['row']

    def test_stream_aggregates_without_storing_raw(self):
        a,r=self.execute("import time; print('2026-10-17T12:00:00Z [ImageMultimodal] Caption failed SECRET',flush=True); time.sleep(20)")
        self.assertTrue(r['followed_requested_window']); self.assertEqual(a['lines'],1)
        self.assertEqual(a['marker_lines']['image_caption_failure'],1)
        self.assertNotIn('SECRET',json.dumps([a,r]))

    def test_early_exit_flag(self):
        a,r=self.execute("print('done')")
        self.assertFalse(r['followed_requested_window']); self.assertTrue(r['client_exit_before_window_end'])

    def test_stream_byte_budget(self):
        a,r=self.execute("print('x'*10000)",budget=100)
        self.assertEqual(r['failure']['check'],'log_stream_byte_budget')

    def test_stream_oversized_line_bounded(self):
        a,r=self.execute("import time; print('SECRET'*20000,flush=True); print('2026-10-17T12:00:00Z enterprise GLM: HTTP status 503',flush=True); time.sleep(20)")
        self.assertEqual(a['oversized_lines_dropped'],1)
        self.assertEqual(a['marker_lines']['glm_http_5xx'],1)
        self.assertNotIn('SECRET',json.dumps([a,r]))

    def test_partial_line_not_misclassified(self):
        a,r=self.execute("import sys,time; sys.stdout.write('SECRET'); sys.stdout.flush(); time.sleep(20)")
        self.assertEqual(a['lines'],0); self.assertTrue(r['partial_line_discarded'])

    def test_stderr_never_printed(self):
        a,r=self.execute("import sys; sys.stderr.write('SECRET TOKEN'); sys.exit(1)")
        self.assertEqual(r['stderr_bytes'],12); self.assertNotIn('SECRET',json.dumps([a,r]))

    def test_empty_stream_not_proof_of_no_work(self):
        a,r=self.execute("import time; time.sleep(20)")
        self.assertTrue(r['empty_stream_is_not_proof_of_no_activity']); self.assertEqual(a['lines'],0)


class LinuxFileTests(unittest.TestCase):
    def private(self):
        return tempfile.TemporaryDirectory(prefix='deep-test-',dir='/var/lib')

    def test_safe_read_and_hash(self):
        with self.private() as root:
            p=Path(root)/'x'; p.write_bytes(b'abc'); p.chmod(0o600)
            self.assertEqual(d.safe_read(p),'abc'.encode())
            with self.assertRaises(d.Failure): d.safe_read(p,'0'*64)

    def test_no_symlink_read(self):
        with self.private() as root:
            p=Path(root)/'x'; p.write_bytes(b'abc'); q=Path(root)/'link'; q.symlink_to(p)
            with self.assertRaises(OSError): d.safe_read(q)

    def test_world_writable_helper_rejected(self):
        with self.private() as root:
            p=Path(root)/'x'; p.write_bytes(b'abc'); p.chmod(0o666)
            with self.assertRaises(d.Failure): d.safe_read(p)

    def test_parent_writable_rejected(self):
        with self.private() as root:
            p=Path(root)/'x'; p.write_bytes(b'abc'); Path(root).chmod(0o777)
            with self.assertRaises(d.Failure): d.safe_read(p)

    def test_evidence_write_does_not_replace(self):
        with self.private() as root:
            p=Path(root); d.save(p,'SUMMARY.json',{'n':1})
            with self.assertRaises(FileExistsError): d.save(p,'SUMMARY.json',{'n':2})
            self.assertEqual(json.loads((p/'SUMMARY.json').read_text()),{'n':1})

    def test_status_does_not_load_helpers_or_read_cluster(self):
        with tempfile.TemporaryDirectory(prefix=d.PREFIX,dir='/var/lib') as root:
            d.save(Path(root),'SUMMARY.json',{'stored':'only'})
            with patch.object(d.socket,'gethostname',return_value='datax-01'), patch.object(d,'initialize',side_effect=AssertionError('cluster')), patch.object(d,'emit') as out:
                self.assertEqual(d.main(['--status',root]),0)
            self.assertEqual(out.call_args_list[0][0][0],{'stored':'only'})


class CollectionTests(unittest.TestCase):
    def fixture(self):
        def pod(name):
            return dict(metadata=dict(name=name,uid=name),spec=dict(nodeName='node'),status={})
        workers=[pod('worker-%d'%n) for n in range(16)]
        app,disp,reader=pod('app'),pod('dispatcher'),pod('reader')
        pods={'weknora-app':[app],'dispatcher':[disp],'workers':workers,'weknora-docreader':[reader]}
        o=observer(); o.WORKERS=['workers']; o.DISPATCHER='dispatcher'
        o.container=lambda p:dict(name='app',resources=dict(limits=dict(memory='4Gi')))
        o.identity=lambda p:[p['metadata']['uid']]
        o.resource_snapshot=lambda p:[]
        o.health=lambda p:dict(role='dispatcher',counters=dict(busy=10,waiting=0,quarantined=0))
        prior=types.SimpleNamespace(pod_status=lambda o,p:dict(pod=p['metadata']['name']))
        def capacities(row,caps,status):
            self.assertEqual(caps,d.CAPS); self.assertEqual(status,'active')
        act=types.SimpleNamespace(review=types.SimpleNamespace(pg_binding=lambda p:({},'same-pg')),
             redis_binding=lambda p:({},'0','host'),snapshot=lambda b:dict(qrow(int(time.time())),effective_servers=[]),check_capacities=capacities)
        h=types.SimpleNamespace(span_snapshot=lambda p,c:dict(groups=[]),database_waits=lambda p:dict(groups=[]))
        return prior,o,act,h,pods

    def test_complete_collection_keeps_configuration_unchanged(self):
        old,o,act,h,pods=self.fixture()
        def fake_stream(cmd,a,stop,ready,**kwargs):
            ready.set(); stop.wait(3); return dict(followed_requested_window=True)
        probe=lambda o,p:dict(pod=p['metadata']['name'],container_identity=o.identity(p),values={'v2_cpu_stat':{'nr_periods':1}})
        with tempfile.TemporaryDirectory(prefix='deep-collection-',dir='/var/lib') as root:
            r=dict(diagnostic='INCOMPLETE',warnings=[],samples=[],utc_started=d.utc())
            with patch.object(d,'initialize',return_value=(old,o,act,h)), patch.object(d,'inventory',return_value=(pods,{'same':1})), patch.object(d,'stream',side_effect=fake_stream), patch.object(d,'cgroup',side_effect=probe), patch.object(d,'model_snapshot',return_value={'holders':{}}), patch.object(d,'database_snapshot',return_value={}), patch.object(d,'SAMPLES',2), patch.object(d,'INTERVAL',0), patch.object(d,'emit'):
                d.collect(Path(root),r)
            self.assertEqual(r['diagnostic'],'COMPLETE'); self.assertTrue(r['baseline_stable'])
            self.assertEqual(len(r['samples']),2); self.assertEqual(len(r['log_aggregates']),19)
            self.assertEqual(len(r['cgroup_deltas']),19)

    def test_identity_drift_never_grants_acceptance(self):
        old,o,act,h,pods=self.fixture()
        def fake_stream(cmd,a,stop,ready,**kwargs): ready.set(); stop.wait(3); return dict(followed_requested_window=True)
        with tempfile.TemporaryDirectory(prefix='deep-collection-',dir='/var/lib') as root:
            r=dict(diagnostic='INCOMPLETE',warnings=[],samples=[],utc_started=d.utc())
            with patch.object(d,'initialize',return_value=(old,o,act,h)), patch.object(d,'inventory',side_effect=[(pods,{'n':1}),(pods,{'n':2})]), patch.object(d,'stream',side_effect=fake_stream), patch.object(d,'cgroup',return_value=None), patch.object(d,'model_snapshot',return_value={}), patch.object(d,'database_snapshot',return_value={}), patch.object(d,'SAMPLES',1), patch.object(d,'emit'):
                with self.assertRaises(d.Failure): d.collect(Path(root),r)
            self.assertEqual(r['diagnostic'],'INCOMPLETE')


if __name__=='__main__':
    unittest.main(verbosity=2)

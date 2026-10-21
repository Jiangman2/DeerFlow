import ast
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch
from contextlib import redirect_stdout, redirect_stderr

spec=importlib.util.spec_from_file_location('candidate_review',os.environ.get('REVIEW',str(Path(__file__).with_name('review.py'))))
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def app():
    return dict(metadata=dict(name='app',uid=m.APP_UID),status=dict(phase='Running'),
        spec=dict(nodeName='datax-03',containers=[dict(name='app',image='unchanged',env=[
            dict(name=m.ENV_KEYS[k],value=str(v)) for k,v in dict(m.CURRENT,model=128).items()])]))


def deployment():
    return dict(metadata=dict(uid='deployment',resourceVersion='123'),spec=dict(replicas=1,template=dict(spec=app()['spec'])))


def metric():
    return dict(node='datax-03',memory=dict(availableBytes=44*m.GIB),
        pods=[dict(pod='app',memory=dict(workingSetBytes=3*m.GIB))])


def cgroup():return dict(values=dict(v1_memory_usage=[11*m.GIB],v1_memory_peak=[12*m.GIB]))


def node():return dict(status=dict(allocatable=dict(cpu='32',memory='128Gi')))


def context():
    c=types.SimpleNamespace()
    c.obs=types.SimpleNamespace(identity=lambda p:[p['metadata']['uid']],ready=lambda p:True,
        container=lambda p:p['spec']['containers'][0],WORKERS=['worker'],DISPATCHER='dispatcher',
        ThreadPoolExecutor=__import__('concurrent.futures',fromlist=['ThreadPoolExecutor']).ThreadPoolExecutor)
    a=app();d=deployment();workers=[dict(metadata=dict(name='w'+str(i),uid=str(i))) for i in range(16)]
    pods={'weknora-app':[a],'worker':workers,'dispatcher':[dict(metadata=dict(name='dispatcher',uid='d'))]}
    c.obs.baseline=Mock(return_value=({},pods,dict(stable=True),{}))
    c.obs.health=Mock(return_value=dict(role='worker'));c.obs.resource_snapshot=Mock(return_value=[metric()])
    c.obs.get=Mock(return_value=node());c.obs.digest=m.digest
    c.obs.write_new=lambda p,x:write(p,x)
    c.e=types.SimpleNamespace(runtime_env=Mock(),review=types.SimpleNamespace(pg_binding=Mock(return_value=({},['pg']))),
        redis_binding=Mock(return_value='binding'),check_capacities=Mock(),check_resource_rows=Mock())
    def snap(_):return dict(server_identity_hashes=['server'],effective_servers=[dict(pool=k,concurrency=v,status='active') for k,v in m.CURRENT.items()],queues={})
    c.e.snapshot=Mock(side_effect=snap)
    c.r=types.SimpleNamespace(sw=types.SimpleNamespace(same=lambda x,y:x==y,placement=Mock(return_value={}),binary=Mock(return_value=dict(pid=1))),
        check_glm=Mock(return_value={'glm':1}),app_endpoint=Mock(),queue_projection=lambda s:dict(pools=s['effective_servers'],queues={}),
        mineru_check=Mock(return_value=dict(dispatcher=dict(live=16,quarantined=0))),dry_run=Mock(return_value=[dict(op='test')]))
    c.deep=types.SimpleNamespace(cgroup=Mock(return_value=cgroup()),cgroup_delta=Mock(return_value=dict(available=True,counters=dict(v1_cpu_stat=dict(nr_throttled=0)))))
    c.sw=types.SimpleNamespace(u=types.SimpleNamespace(app_metric_freshness=Mock(),trends=Mock(return_value={})))
    return c,d,a


def write(path,row):
    raw=(json.dumps(row,sort_keys=True)+'\n').encode();fd=os.open(str(path),os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'wb') as f:f.write(raw)
    return raw


class Tests(unittest.TestCase):
    def test_preferred168_and_alternative136(self):
        for n,total in ((128,168),(96,136)):
            source=deployment()['spec'];before=copy.deepcopy(source)
            result=m.proposal(source,n)
            self.assertEqual(source,before)
            env=result['template']['spec']['containers'][0]['env']
            self.assertEqual(next(x['value'] for x in env if x['name']==m.ENV_KEYS['enrichment']),str(n))
            self.assertEqual(sum(m.CURRENT.values())-64+n,total)
            next(x for x in env if x['name']==m.ENV_KEYS['enrichment'])['value']='64'
            self.assertEqual(result,source)

    def test_unknown_candidate(self):
        with self.assertRaises(m.Failure):m.proposal(deployment()['spec'],256)

    def test_model_drift(self):
        d=deployment()['spec'];d['template']['spec']['containers'][0]['env'][-1]['value']='256'
        with self.assertRaises(m.Failure):m.proposal(d,128)

    def test_duplicate_env(self):
        d=deployment()['spec'];env=d['template']['spec']['containers'][0]['env'];env.append(env[0])
        with self.assertRaises(m.Failure):m.proposal(d,128)

    def test_valuefrom_and_envfrom(self):
        for key in ('valueFrom','envFrom'):
            d=deployment()['spec'];c=d['template']['spec']['containers'][0]
            if key=='valueFrom':c['env'][0][key]={}
            else:c[key]=[{}]
            with self.assertRaises(m.Failure):m.proposal(d,128)

    def test_quantities(self):
        self.assertEqual(m.quantity('2000m','cpu'),2);self.assertEqual(m.quantity('1Gi','memory'),m.GIB)
        self.assertEqual(m.quantity('132554368Ki','memory'),132554368*1024)

    def test_unknown_quantity_fail_closed(self):
        for value in ('NaN','1e6','SECRET','-1','1GiB'):
            with self.subTest(value=value),self.assertRaises(m.Failure):m.quantity(value,'memory')

    def test_app_init_max_and_overhead(self):
        p=app();p['spec']['containers'][0]['resources']=dict(requests=dict(cpu='2',memory='4Gi'))
        p['spec']['initContainers']=[dict(resources=dict(requests=dict(cpu='4',memory='2Gi')))]
        p['spec']['overhead']=dict(cpu='100m',memory='1Gi')
        row=m.pod_requests(p);self.assertEqual(row['cpu'],m.Decimal('4.1'));self.assertEqual(row['memory'],5*m.GIB)

    def test_restartable_init_accounting(self):
        p=app();p['spec']['containers'][0]['resources']=dict(requests=dict(cpu='2'))
        p['spec']['initContainers']=[dict(restartPolicy='Always',resources=dict(requests=dict(cpu='1'))),dict(resources=dict(requests=dict(cpu='5')))]
        self.assertEqual(m.pod_requests(p)['cpu'],6)

    def test_limits_without_requests_accounted(self):
        p=app();p['spec']['containers'][0]['resources']=dict(limits=dict(memory='4Gi'))
        self.assertEqual(m.pod_requests(p)['memory'],4*m.GIB)

    def test_pod_level_resources_rejected(self):
        p=app();p['spec']['resources']=dict(requests=dict(cpu='1'))
        with self.assertRaises(m.Failure):m.pod_requests(p)

    def test_budget_not_applied(self):
        p=app();before=copy.deepcopy(p)
        row=m.budget(128,dict(cpu=m.Decimal(10),memory=m.Decimal(30*m.GIB)),p,node(),cgroup(),metric())
        self.assertEqual(row['resources']['limits']['memory'],'32Gi');self.assertEqual(p,before)
        self.assertFalse(row['cpu_limit_proposed']);self.assertTrue(row['linear_memory_scenario_is_not_load_acceptance'])

    def test_budget_reservation_pressure(self):
        with self.assertRaisesRegex(m.Failure,'reservation_headroom'):
            m.budget(128,dict(cpu=m.Decimal(31),memory=m.Decimal(30*m.GIB)),app(),node(),cgroup(),metric())

    def test_budget_peak_pressure(self):
        cg=cgroup();cg['values']['v1_memory_peak']=[16*m.GIB]
        with self.assertRaisesRegex(m.Failure,'memory_scenario'):
            m.budget(128,dict(cpu=m.Decimal(1),memory=m.Decimal(1)),app(),node(),cg,metric())

    def test_budget_node_reserve_pressure(self):
        row=metric();row['memory']['availableBytes']=30*m.GIB
        with self.assertRaisesRegex(m.Failure,'node_reserve'):
            m.budget(128,dict(cpu=m.Decimal(1),memory=m.Decimal(1)),app(),node(),cgroup(),row)

    def test_each_pressure_counter(self):
        for counters in (dict(v1_memory_failcnt=dict(delta=1)),dict(v2_memory_events=dict(oom_kill=1)),
                         dict(v1_cpu_stat=dict(nr_throttled=1)),dict(v1_cpu_stat=dict(counter_reset=True))):
            with self.subTest(counters=counters),self.assertRaises(m.Failure):m.check_pressure(dict(available=True,counters=counters))

    def test_no_pressure(self):m.check_pressure(dict(available=True,counters=dict(v1_cpu_stat=dict(nr_throttled=0))))

    def test_sql_readonly_and_no_raw_values(self):
        self.assertIn("statement_timeout='5s'",m.EXTRA_SQL);self.assertIn("lock_timeout='1s'",m.EXTRA_SQL)
        self.assertTrue(m.EXTRA_SQL.startswith('BEGIN READ ONLY'))
        for text in ('UPDATE ','INSERT ','DELETE ','SELECT query','api_key'):self.assertNotIn(text,m.EXTRA_SQL)

    def test_safe_error(self):
        self.assertNotIn('SECRET',json.dumps(m.error(ValueError('SECRET'))))
        self.assertEqual(m.error(RuntimeError('postgres_identity_changed'))['check'],'postgres_identity_changed')

    def prepare(self):
        c,d,a=context();plan=dict(candidate=d['spec'],placement={},candidate_binary='a'*64)
        activation=dict(configmap={},document_catalog_after={})
        mocks=[patch.object(m,'bind_inputs',return_value=(plan,activation,{})),patch.object(m,'current',return_value=(d,a)),
            patch.object(m,'startup',return_value={'limit':128}),patch.object(m,'reservations',return_value=(dict(cpu=m.Decimal(10),memory=m.Decimal(30*m.GIB)),a)),
            patch.object(m,'database',return_value=dict(waits=dict(groups=[]),connections={})),patch.object(m.time,'sleep')]
        for obj in mocks:obj.start();self.addCleanup(obj.stop)
        return c,d,a,dict(glm168_review='INCOMPLETE',samples=[],blockers=[],candidates=[])

    def test_complete_both_candidates_dry_run_only(self):
        c,d,a,report=self.prepare()
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()):
            root=Path(tmp);m.collect(c,root,report)
            self.assertEqual(report['glm168_review'],'PASS');self.assertEqual(c.r.dry_run.call_count,2)
            self.assertEqual(len(report['candidate_budgets']['128']),4)
            plan=json.loads((root/'PROPOSAL-128.private.json').read_bytes())
            self.assertFalse(plan['apply_permitted']);self.assertFalse(plan['resource_budget_included_in_candidate'])
            self.assertTrue(plan['resource_budget_approval_required']);self.assertTrue(plan['maintenance_window_required'])
            self.assertEqual(plan['model_default'],128);self.assertEqual(plan['mineru_slots'],16)

    def test_quarantine_blocks_both(self):
        c,d,a,report=self.prepare();c.r.mineru_check.side_effect=RuntimeError('mineru_16_not_healthy_for_expansion')
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()):m.collect(c,Path(tmp),report)
        self.assertEqual(report['glm168_review'],'BLOCKED');c.r.dry_run.assert_not_called()

    def test_changed_consumers_blocks(self):
        c,d,a,report=self.prepare();c.e.snapshot.side_effect=[dict(server_identity_hashes=['s'],effective_servers=[],queues={}),dict(server_identity_hashes=['changed'],effective_servers=[],queues={})]
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()),self.assertRaisesRegex(m.Failure,'consumer_identity'):
            m.collect(c,Path(tmp),report)
        c.r.dry_run.assert_not_called()

    def test_fallback136_when168_budget_blocked(self):
        c,d,a,report=self.prepare();real=m.budget
        def scenario(n,*args):
            if n==128:raise m.Failure('candidate_memory_scenario_insufficient')
            return real(n,*args)
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()),patch.object(m,'budget',side_effect=scenario):
            m.collect(c,Path(tmp),report)
        self.assertEqual([x['review'] for x in report['candidates']],['BLOCKED_RESOURCE_SCENARIO','PASS'])
        self.assertEqual(c.r.dry_run.call_count,1)

    def test_persistent_wait_blocks(self):
        c,d,a,report=self.prepare();m.database.return_value['waits']['groups']=[dict(backend_type='client backend',wait_type='IO',max_query_age_seconds=31)]
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()):m.collect(c,Path(tmp),report)
        self.assertEqual(report['glm168_review'],'BLOCKED');c.r.dry_run.assert_not_called()

    def test_new_oom_blocks(self):
        c,d,a,report=self.prepare();c.deep.cgroup_delta.return_value=dict(available=True,counters=dict(v2_memory_events=dict(oom=1)))
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()),self.assertRaisesRegex(m.Failure,'new_memory_oom'):
            m.collect(c,Path(tmp),report)
        c.r.dry_run.assert_not_called()

    def test_final_resource_check_is_required(self):
        c,d,a,report=self.prepare();c.e.check_resource_rows.side_effect=[None,None,None,RuntimeError('node_pressure_or_unready')]
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()),self.assertRaises(RuntimeError):m.collect(c,Path(tmp),report)
        c.r.dry_run.assert_not_called()

    def test_public_does_not_export_specs(self):
        out=m.public(dict(glm168_review='PASS',samples=[],secret='SECRET',candidate={'env':'SECRET'}))
        self.assertNotIn('SECRET',json.dumps(out));self.assertFalse(out['ready_to_apply'])
        self.assertEqual(out['preferred_total_slots'],168)

    def test_wrong_host_before_helpers(self):
        with patch.object(m.socket,'gethostname',return_value='other'),patch.object(m,'bootstrap',side_effect=AssertionError('helper')):
            with self.assertRaisesRegex(m.Failure,'root_datax01'):m.main(['--review'])

    def test_status_only_reads_saved_report(self):
        fake=types.SimpleNamespace(f=types.SimpleNamespace(safe_read=Mock(return_value=b'{"glm168_review":"PASS","samples":[]}')))
        with patch.object(m.sys,'platform','linux'),patch.object(m.os,'geteuid',return_value=0,create=True),patch.object(m.socket,'gethostname',return_value='datax-01'),patch.object(m,'bootstrap',return_value=fake) as boot,redirect_stdout(io.StringIO()),patch.dict(sys.modules,resource=types.SimpleNamespace(RLIMIT_CORE=1,setrlimit=Mock())):
            self.assertEqual(m.main(['--status','/var/lib/weknora-glm168-review-abcdefgh']),0)
        boot.assert_called_once_with(chain=False);fake.f.safe_read.assert_called_once()

    def test_overall_pass_is_not_preferred168_pass(self):
        out=m.public(dict(glm168_review='PASS',samples=[],candidates=[dict(enrichment=128,review='BLOCKED_RESOURCE_SCENARIO'),dict(enrichment=96,review='PASS')]))
        self.assertFalse(out['preferred168_ready_for_controlled_review']);self.assertFalse(out['ready_to_apply'])

    def test_no_mutation_modes(self):
        for flag in ('--apply','--resume','--rollback','--reconcile'):
            with redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):m.main([flag])

    def test_no_old_mutator_calls(self):
        source=Path(m.__file__).read_text();tree=ast.parse(source)
        calls={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
        forbidden={'signal_once','capture_startup_limit','apply_after_drain','apply','reconcile','kill','terminate','patch_call'}
        self.assertFalse(calls&forbidden)


if __name__=='__main__':unittest.main(verbosity=2)

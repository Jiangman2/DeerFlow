import ast
import base64
import copy
import json
import contextlib
import hashlib
import io
import signal
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch, Mock

import tune_weknora88 as m
from test_observe_pipeline import field
from test_weknora_tune_placement import fixture as placement_fixture


def spec():
    return {'replicas': 1, 'strategy': {'type': 'Recreate'}, 'template': {'spec': {
        'nodeSelector': {'kubernetes.io/hostname': 'datax-03'},
        'containers': [{'name': 'app', 'image': 'unchanged', 'env': [
            {'name': 'CREDENTIAL', 'valueFrom': {'secretKeyRef': {'name': 'secret', 'key': 'key'}}},
            *[{'name': m.review.SETTINGS[k][1], 'value': str(v)} for k, v in m.CURRENT.items()]]}]}}}


def dep(s=None):
    return {'metadata': {'uid': m.process.DEP_UID, 'resourceVersion': '10', 'generation': 1},
            'spec': s or spec(), 'status': {'observedGeneration': 1, 'updatedReplicas': 1, 'availableReplicas': 1}}


def pod():
    return {'metadata': {'name': 'test-app', 'uid': 'test-uid'}, 'spec': {'nodeName': 'datax-03'},
            'status': {'containerStatuses': [{'name': 'app', 'imageID': 'same', 'containerID': 'same', 'restartCount': 0}]}}


def snap(caps=None, state='stopped', active=0):
    return {'effective_servers': [{'pool': k, 'concurrency': v, 'status': state, 'active': active}
                                  for k,v in (caps or m.CURRENT).items()],
            'queues': {'default': {'active': active, 'pending': 5000}},
            'server_identity_hashes': sorted(m.CURRENT)}


def redis_bytes(other=False, duplicate=False):
    qs = {'core': {'default': 1}, 'postprocess': {'postprocess': 1}, 'enrichment': {'summary': 1},
          'maintenance': {'low': 1}, 'shared': {'default': 1, 'summary': 1}, 'wiki': {'wiki': 1}}
    rows = ['T\t%d\t1' % time.time(), 'Q\tdefault\t5000\t0\t0\t0\t0\t0\t100\t0\t0\t0']
    for k,v in m.CURRENT.items():
        blob = field(1, 'test-app')+field(2,1)+field(3,'same' if duplicate else k)+field(4,v)
        blob += b''.join(field(5,field(1,q)+field(2,w)) for q,w in qs[k].items())
        blob += field(7, 'stopped')+field(9,0)
        rows.append('H\t%d\t%s' % (len(blob), base64.b64encode(blob+b'\n').decode()))
    if other:
        blob = field(1,'other-app')+field(3,'other')+field(7,'active')+field(9,1)
        rows.append('H\t%d\t%s' % (len(blob), base64.b64encode(blob+b'\n').decode()))
    return ('\n'.join(rows)+'\nEND\n').encode()


class Tests(unittest.TestCase):
    def test_syntax37(self):
        ast.parse(Path(m.__file__).read_text(), feature_version=(3, 7))
        ast.parse(m.SIGNAL_CODE, feature_version=(3, 7))

    def test_target_budget(self):
        self.assertEqual(sum(m.TARGET.values()), 88)
        self.assertEqual(m.TARGET['core']+m.TARGET['shared'], 20)
        self.assertEqual(m.TARGET_ENV['WEKNORA_MODEL_MAX_CONCURRENCY'], '128')

    def test_exact_env_only_change(self):
        s = spec(); before = copy.deepcopy(s)
        out = m.change_env(s, m.TARGET_ENV)
        self.assertEqual(s, before)
        self.assertEqual(out['template']['spec']['containers'][0]['env'][0], s['template']['spec']['containers'][0]['env'][0])
        p = m.patch_plan(dep(s), out)
        self.assertEqual([x['op'] for x in p], ['test', 'test', 'test', 'replace'])
        self.assertEqual(p[-1]['path'], '/spec/template/spec/containers/0/env')
        self.assertEqual(out['template']['spec']['containers'][0]['image'], 'unchanged')

    def test_duplicate_env_rejected(self):
        s = spec(); e=s['template']['spec']['containers'][0]['env']; e.append(e[-1])
        with self.assertRaisesRegex(m.obs.CheckFailure,'duplicate_environment'):
            m.change_env(s,m.TARGET_ENV)

    def test_secret_concurrency_rejected(self):
        s=spec(); s['template']['spec']['containers'][0]['env'][1]={'name':'WEKNORA_ASYNQ_CORE_CONCURRENCY','valueFrom':{}}
        with self.assertRaisesRegex(m.obs.CheckFailure,'concurrency_not_literal'):
            m.change_env(s,m.TARGET_ENV)

    def test_image_change_rejected(self):
        s=spec(); target=copy.deepcopy(s); target['template']['spec']['containers'][0]['image']='other'
        with self.assertRaisesRegex(m.obs.CheckFailure,'change_outside'):
            m.patch_plan(dep(s),target)

    def test_pending_not_required_empty(self):
        self.assertTrue(m.drain_complete(snap()))

    def test_active_blocks_restart(self):
        self.assertFalse(m.drain_complete(snap(active=1)))

    def test_missing_stopped_pool_blocks(self):
        r=snap(); r['effective_servers'].pop()
        self.assertFalse(m.drain_complete(r))

    def test_active_pool_blocks_even_zero_handlers(self):
        self.assertFalse(m.drain_complete(snap(state='active')))

    def test_redis_claimed_list_blocks(self):
        r=snap(); r['queues']['default']['active']=1
        self.assertFalse(m.drain_complete(r))

    def test_effective_not_configured_capacity(self):
        with self.assertRaisesRegex(m.obs.CheckFailure,'effective_capacity'):
            m.check_capacities(snap(),m.TARGET)

    def test_stable_unique_heartbeat_snapshot(self):
        with patch.object(m.obs,'call',return_value=redis_bytes()):
            r=m.snapshot(({'metadata':{'name':'redis'},'spec':{'containers':[{'name':'redis'}]}},'0','test-app'))
        self.assertEqual(len(r['server_identity_hashes']),6)
        self.assertTrue(m.drain_complete(r))

    def test_other_active_host_blocked(self):
        with patch.object(m.obs,'call',return_value=redis_bytes(other=True)):
            with self.assertRaisesRegex(m.obs.CheckFailure,'other_active_asynq_host'):
                m.snapshot(({'metadata':{'name':'redis'},'spec':{'containers':[{'name':'redis'}]}},'0','test-app'))

    def test_duplicate_server_ids_blocked(self):
        with patch.object(m.obs,'call',return_value=redis_bytes(duplicate=True)):
            with self.assertRaisesRegex(m.obs.CheckFailure,'six_unique_server_ids'):
                m.snapshot(({'metadata':{'name':'redis'},'spec':{'containers':[{'name':'redis'}]}},'0','test-app'))

    def test_node_pin_required(self):
        d,p,n,objects=placement_fixture(); d['spec']['template']['spec'].pop('volumes')
        with self.assertRaisesRegex(m.obs.CheckFailure,'volume_shape_changed'):
            m.scheduling_gate(d,p)

    def test_cordoned_node_blocked(self):
        d,p,n,objects=placement_fixture(); n['spec']['unschedulable']=True
        with patch.object(m.placement.audit,'named',side_effect=lambda kind,name:objects[name]), \
             patch.object(m.obs,'get',return_value={'items':[n]}):
            with self.assertRaisesRegex(m.obs.CheckFailure,'node_cordoned'):
                m.scheduling_gate(d,p)

    def test_node_taint_blocked(self):
        d,p,n,objects=placement_fixture(); n['spec']['taints']=[{'key':'maintenance','effect':'NoSchedule'}]
        with patch.object(m.placement.audit,'named',side_effect=lambda kind,name:objects[name]), \
             patch.object(m.obs,'get',return_value={'items':[n]}):
            with self.assertRaisesRegex(m.obs.CheckFailure,'taint_not_tolerated'):
                m.scheduling_gate(d,p)

    def test_all_affinity_terms_must_pin(self):
        d,p,n,objects=placement_fixture()
        objects[m.placement.VOLUMES[0][5]]['spec']['nodeAffinity']['required']['nodeSelectorTerms'].append({})
        with patch.object(m.placement.audit,'named',side_effect=lambda kind,name:objects[name]):
            with self.assertRaisesRegex(m.obs.CheckFailure,'pv_node_affinity_changed'):
                m.scheduling_gate(d,p)

    def test_drain_requires_three_stable_samples(self):
        r={}; captures=[]
        with patch.object(m,'deployment'),patch.object(m,'app_for',return_value=pod()),patch.object(m,'redis_binding'), \
             patch.object(m,'snapshot',side_effect=[snap(active=1),snap(),snap(),snap()]), \
             patch.object(m,'write',side_effect=lambda p,r:captures.append(r)),patch.object(m.time,'sleep'),patch.object(m.obs,'emit'):
            m.wait_drain(Path('/op'),{},[],m.CURRENT,sorted(m.CURRENT),r)
        self.assertEqual([v['stable_zero_samples'] for v in captures if 'stable_zero_samples' in v],[0,1,2,3])
        self.assertEqual(r['live_stop_behavior'],'SIX_POOLS_STOPPED_AND_ZERO_ACTIVE_CONFIRMED')

    def test_drain_identity_change_blocks(self):
        with patch.object(m,'deployment'),patch.object(m,'app_for',return_value=pod()),patch.object(m,'redis_binding'), \
             patch.object(m,'snapshot',return_value=snap()):
            with self.assertRaisesRegex(m.obs.CheckFailure,'server_identity_changed'):
                m.wait_drain(Path('/op'),{},[],m.CURRENT,['different'],{})

    def test_patch_unknown_never_resubmitted(self):
        with tempfile.TemporaryDirectory() as td:
            op=Path(td); (op/'PATCH-REQUESTED.json').touch()
            plan={'original':spec(),'candidate':m.change_env(spec(),m.TARGET_ENV)}
            with patch.object(m,'deployment',return_value=dep()),patch.object(m,'patch_call') as call:
                with self.assertRaisesRegex(m.obs.CheckFailure,'patch_outcome_not_target'):
                    m.mutate(op,plan,'forward',{},continuing=True)
                call.assert_not_called()

    def test_patch_accepted_resume_only_accepts(self):
        with tempfile.TemporaryDirectory() as td:
            op=Path(td); (op/'PATCH-REQUESTED.json').touch()
            plan={'original':spec(),'candidate':m.change_env(spec(),m.TARGET_ENV)}
            with patch.object(m,'deployment',return_value=dep(plan['candidate'])),patch.object(m,'accept') as accept, \
                 patch.object(m,'patch_call') as call:
                m.mutate(op,plan,'forward',{},continuing=True)
                accept.assert_called_once(); call.assert_not_called()

    def test_continue_does_not_send_signal(self):
        with tempfile.TemporaryDirectory() as td:
            op=Path(td); plan={'original':spec(),'candidate':m.change_env(spec(),m.TARGET_ENV),
                              'original_pod':m.obs.identity(pod())}
            sig={'pod_identity':m.obs.identity(pod()),'server_ids':sorted(m.CURRENT)}
            with patch.object(m,'deployment',return_value=dep()),patch.object(m,'app_for',return_value=pod()), \
                 patch.object(m,'load',return_value=sig),patch.object(m,'wait_drain',side_effect=m.obs.CheckFailure('waiting')), \
                 patch.object(m.obs,'call') as call:
                with self.assertRaisesRegex(m.obs.CheckFailure,'waiting'):
                    m.mutate(op,plan,'forward',{},continuing=True)
                call.assert_not_called()

    def test_previous_success_rejected_before_live_reads(self):
        with patch.object(m,'previous_release',side_effect=m.obs.CheckFailure('previous_release_changed')), \
             patch.object(m,'deployment') as baseline,patch.object(m,'write') as write:
            with self.assertRaisesRegex(m.obs.CheckFailure,'previous_release_changed'):
                m.prepare({})
        baseline.assert_not_called(); write.assert_not_called()

    def test_placement_drift_blocks_before_signal(self):
        with tempfile.TemporaryDirectory() as td:
            plan={'original':spec(),'candidate':m.change_env(spec(),m.TARGET_ENV),'placement':{'pv':'old'},'original_pod':m.obs.identity(pod())}
            with patch.object(m,'deployment',return_value=dep()),patch.object(m,'app_for',return_value=pod()), \
                 patch.object(m,'check_dependencies'),patch.object(m,'scheduling_gate',return_value={'pv':'different'}), \
                 patch.object(m,'write') as write,patch.object(m.obs,'call') as call:
                with self.assertRaisesRegex(m.obs.CheckFailure,'binding_changed_since_prepare'):
                    m.mutate(Path(td),plan,'forward',{})
            write.assert_not_called(); call.assert_not_called()

    def test_placement_drift_after_drain_blocks_patch(self):
        with tempfile.TemporaryDirectory() as td:
            plan={'original':spec(),'candidate':m.change_env(spec(),m.TARGET_ENV),'placement':{'pv':'old'},'original_pod':m.obs.identity(pod())}
            sig={'pod_identity':m.obs.identity(pod()),'server_ids':sorted(m.CURRENT)}
            with patch.object(m,'deployment',return_value=dep()),patch.object(m,'app_for',return_value=pod()), \
                 patch.object(m,'load',return_value=sig),patch.object(m,'wait_drain',return_value=pod()), \
                 patch.object(m,'database'),patch.object(m,'resources'),patch.object(m,'check_dependencies'), \
                 patch.object(m,'scheduling_gate',return_value={'pv':'different'}), \
                 patch.object(m,'write') as write,patch.object(m,'patch_call') as call:
                with self.assertRaisesRegex(m.obs.CheckFailure,'binding_changed_since_prepare'):
                    m.mutate(Path(td),plan,'forward',{},continuing=True)
            write.assert_not_called(); call.assert_not_called()

    def test_signal_code_has_only_one_tstp(self):
        tree=ast.parse(m.SIGNAL_CODE)
        calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='kill']
        self.assertEqual(len(calls),1)
        self.assertEqual(ast.unparse(calls[0]),'os.kill(1, signal.SIGTSTP)')
        for name in ('SIGSTOP','SIGTERM','SIGKILL','SIGCONT'):
            self.assertNotIn(name,m.SIGNAL_CODE)

    def test_no_live_patch_before_wait(self):
        source=Path(m.__file__).read_text()
        function=source[source.index('def mutate('):source.index('def accept(')]
        self.assertLess(function.index('wait_drain('),function.index('patch_call(patchfile, False)'))
        self.assertLess(function.index("write(op/'PATCH-REQUESTED.json'"),function.index('patch_call(patchfile, False)'))

    def test_complete_forward_mock_flow(self):
        with tempfile.TemporaryDirectory() as td:
            op=Path(td); before=spec(); after=m.change_env(before,m.TARGET_ENV)
            plan={'original':before,'candidate':after,'placement':{'verified':'same'},'original_pod':m.obs.identity(pod())}; events=[]
            sig={'start_ticks':100,'uid':[1000]*4,'gid':[1000]*4}
            def store(p,v): events.append(p.name); return b'{}'
            def patcher(p,dry):
                events.append('dry' if dry else 'LIVE_PATCH'); return dep(after)
            env={m.review.SETTINGS[k][1]:str(v) for k,v in dict(m.CURRENT,model=64).items()}
            env['AUTO_MIGRATE']='false'
            with patch.object(m,'deployment',return_value=dep(before)),patch.object(m,'app_for',return_value=pod()), \
                 patch.object(m,'check_dependencies'),patch.object(m,'database'),patch.object(m,'resources'), \
                 patch.object(m,'scheduling_gate',return_value=plan['placement']),patch.object(m,'runtime_env',return_value=env), \
                 patch.object(m,'binary_probe',return_value=sig),patch.object(m,'redis_binding'), \
                 patch.object(m,'snapshot',side_effect=[snap(state='active'),snap()]), \
                 patch.object(m,'write',side_effect=store),patch.object(m,'patch_call',side_effect=patcher), \
                 patch.object(m.obs,'call',side_effect=lambda *a,**kw:(events.append('SIGNAL') or b'{"tstp_sent_once":true,"pid":1,"start_ticks":100}')), \
                 patch.object(m,'wait_drain',side_effect=lambda *a:(events.append('DRAIN') or pod())), \
                 patch.object(m,'accept',side_effect=lambda *a:events.append('ACCEPT')), \
                 patch.object(Path,'read_bytes',return_value=b'{}'),patch.object(m.obs,'emit'):
                m.mutate(op,plan,'forward',{})
            self.assertLess(events.index('dry'),events.index('SIGNAL-REQUESTED.json'))
            self.assertLess(events.index('SIGNAL-REQUESTED.json'),events.index('SIGNAL'))
            self.assertLess(events.index('DRAIN'),events.index('PATCH-REQUESTED.json'))
            self.assertLess(events.index('PATCH-REQUESTED.json'),events.index('LIVE_PATCH'))
            self.assertLess(events.index('PATCH-ACK.json'),events.index('ACCEPT'))
            self.assertEqual(events.count('SIGNAL'),1)
            self.assertEqual(events.count('LIVE_PATCH'),1)

    def run_signal_code(self, wrong_hash=False, wrong_uid=False):
        binary=b'test-executable'; expected={'start_ticks':100,'binary_sha256':hashlib.sha256(binary).hexdigest()}
        if wrong_hash: expected['binary_sha256']='0'*64
        def fakeopen(path,*args,**kw):
            if path=='/proc/1/status':
                return io.StringIO('Uid:\t1000 1000 1000 1000\nGid:\t1000 1000 1000 1000\nSigCgt:\t0000000000080000\n')
            if path=='/proc/1/stat': return io.StringIO('1 (WeKnora) '+' '.join(['0']*19+['100']))
            if path=='/proc/1/exe': return io.BytesIO(binary)
            raise AssertionError(path)
        with patch('builtins.open',side_effect=fakeopen),patch('os.geteuid',return_value=0 if wrong_uid else 1000,create=True), \
             patch('os.getegid',return_value=1000,create=True),patch('os.readlink',return_value='/app/WeKnora'), \
             patch('sys.argv',['signal',json.dumps(expected)]),patch('os.kill') as kill, \
             patch.object(signal,'SIGTSTP',20,create=True),contextlib.redirect_stdout(io.StringIO()):
            if wrong_hash or wrong_uid:
                with self.assertRaises(AssertionError): exec(m.SIGNAL_CODE,{})
                kill.assert_not_called()
            else:
                exec(m.SIGNAL_CODE,{})
                kill.assert_called_once_with(1,20)

    def test_exact_remote_signal_single_call(self): self.run_signal_code()
    def test_wrong_binary_never_signals(self): self.run_signal_code(wrong_hash=True)
    def test_wrong_exec_uid_never_signals(self): self.run_signal_code(wrong_uid=True)


if __name__=='__main__': unittest.main()



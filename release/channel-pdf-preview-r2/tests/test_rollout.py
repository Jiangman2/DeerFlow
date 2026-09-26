import ast
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name,path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mod = load('r2_rollout', ROOT/'rollout-candidates.py')
v = mod.load_helper(ROOT/'validate-candidates.py')


def before(component):
    return {'metadata':{'uid':component+'-uid','generation':1},'spec':{
        'replicas':1,'selector':{'matchLabels':{'app':component}},
        'strategy':{'type':'RollingUpdate','rollingUpdate':{'maxSurge':1,'maxUnavailable':0}},
        'template':{'metadata':{'labels':{'app':component}},'spec':{
            'nodeSelector':{'kubernetes.io/hostname':'ecs-zdz-pro-0022'},
            'initContainers':[{'name':'init','image':'unchanged-init'}],
            'containers':[{'name':component,'image':v.BASE[component],'imagePullPolicy':'Never',
                'resources':{'limits':{'cpu':'4','memory':'16Gi'}},
                'envFrom':[{'secretRef':{'name':'channel-secret'}}],
                'env':[{'name':'GATEWAY_WORKERS','value':'1'}],
                'volumeMounts':[{'name':'config','mountPath':'/app/config.yaml','subPath':'config.yaml'}]}],
            'volumes':[{'name':'config','configMap':{'name':'feishu-specific-config'}}]}}}}


class FakeRelease(mod.Release):
    def __init__(self, directory):
        super().__init__(v,directory)
        self.before = {c:before(c) for c in v.BASE}
        self.live = copy.deepcopy(self.before)
        self.history = []
        self.fail_frontend_once = False
        self.race = False
        self.active = False

    def get(self, kind, name=None, selector=None):
        if kind == 'deployment': return copy.deepcopy(self.live[name])
        raise AssertionError((kind,name))

    def apply(self,c,old,new):
        self.check_live_spec(c,old)
        mod.guarded_patch(self.before[c]['metadata']['uid'],old,new,c)
        self.live[c]['spec'] = copy.deepcopy(new)
        self.history.append((c,copy.deepcopy(new)))

    def no_activity(self):
        if self.active: raise RuntimeError('active work')

    def healthy(self,c,spec,candidate,timeout=900):
        self.check_live_spec(c,spec)
        if c=='frontend' and candidate and self.fail_frontend_once:
            self.fail_frontend_once = False
            if self.race: self.live['gateway']['spec']['template']['spec']['containers'][0]['resources']['limits']['cpu']='6'
            raise RuntimeError('simulated frontend HTTP failure')

    def diagnostics(self): pass


class RolloutTests(unittest.TestCase):
    def test_patch_only_image_and_temporary_strategy(self):
        original = before('gateway')
        target = mod.with_changes(original['spec'],'gateway',v.CANDIDATE['gateway'],{'type':'Recreate'})
        ops = mod.guarded_patch(original['metadata']['uid'],original['spec'],target,'gateway')
        self.assertEqual([o['path'] for o in ops if o['op']!='test'],['/spec/template/spec/containers/0/image','/spec/strategy'])
        restored = mod.with_changes(target,'gateway',v.BASE['gateway'],original['spec']['strategy'])
        self.assertEqual(restored,original['spec'])
        for field in ('env','envFrom','resources','volumeMounts'):
            self.assertEqual(target['template']['spec']['containers'][0][field],original['spec']['template']['spec']['containers'][0][field])
        self.assertEqual(target['template']['spec']['initContainers'],original['spec']['template']['spec']['initContainers'])

    def test_reject_unowned_field_changes(self):
        base = before('gateway')['spec']
        for field,value in (('replicas',2),('paused',True)):
            modified = copy.deepcopy(base)
            modified[field]=value
            with self.assertRaises(RuntimeError): mod.guarded_patch('uid',base,modified,'gateway')

    def test_http_probe_syntax(self):
        ast.parse(mod.GATEWAY_HTTP)
        result = subprocess.run(['node','--check','-'],input=mod.FRONTEND_HTTP,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('ProxyHandler({})',mod.GATEWAY_HTTP)
        self.assertIn("redirect:'manual'",mod.FRONTEND_HTTP)

    def test_success_and_original_strategy_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = FakeRelease(Path(tmp))
            release.rollout()
            self.assertEqual([c for c,_ in release.history],['gateway','frontend','gateway'])
            self.assertEqual(release.history[0][1]['strategy'],{'type':'Recreate'})
            for c in v.BASE:
                self.assertEqual(release.live[c]['spec'],mod.with_changes(release.before[c]['spec'],c,v.CANDIDATE[c]))

    def test_activity_race_stops_before_any_patch(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = FakeRelease(Path(tmp))
            release.active = True
            with self.assertRaises(RuntimeError): release.rollout()
            self.assertEqual(release.history,[])

    def test_frontend_failure_restores_both_exact_specs(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = FakeRelease(Path(tmp))
            release.fail_frontend_once = True
            with self.assertRaisesRegex(RuntimeError,'simulated frontend'): release.rollout()
            for c in v.BASE: self.assertEqual(release.live[c]['spec'],release.before[c]['spec'])
            gateway_updates = [s for c,s in release.history if c=='gateway']
            self.assertEqual(gateway_updates[1]['strategy'],{'type':'Recreate'})
            self.assertEqual(gateway_updates[1]['template']['spec']['containers'][0]['image'],v.BASE['gateway'])

    def test_external_edit_prevents_clobbering_during_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = FakeRelease(Path(tmp))
            release.fail_frontend_once = release.race = True
            with self.assertRaisesRegex(RuntimeError,'rollback incomplete'): release.rollout()
            self.assertEqual(release.live['gateway']['spec']['template']['spec']['containers'][0]['resources']['limits']['cpu'],'6')
            self.assertEqual(release.live['frontend']['spec'],release.before['frontend']['spec'])

    def test_uncertain_gateway_patch_response_is_rolled_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = FakeRelease(Path(tmp))
            real_apply = release.apply
            failed = False
            def uncertain(c, old, new):
                nonlocal failed
                real_apply(c, old, new)
                if not failed:
                    failed = True
                    raise RuntimeError('simulated lost patch response')
            with patch.object(release,'apply',side_effect=uncertain):
                with self.assertRaisesRegex(RuntimeError,'lost patch response'): release.rollout()
            for c in v.BASE: self.assertEqual(release.live[c]['spec'],release.before[c]['spec'])

    def test_rollback_failure_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = FakeRelease(Path(tmp))
            release.live['gateway']['spec'] = mod.with_changes(release.before['gateway']['spec'],'gateway',v.CANDIDATE['gateway'])
            with patch.object(release,'healthy',side_effect=RuntimeError('image unavailable')), patch.object(mod,'emit') as emit:
                with self.assertRaisesRegex(RuntimeError,'rollback incomplete'): release.rollback()
            self.assertFalse(any('channel_pdf_r2_rollback=PASS' in str(c) for c in emit.call_args_list))

    def test_guarded_patch_uses_private_file_not_secret_argv(self):
        with tempfile.TemporaryDirectory() as tmp:
            release = mod.Release(v,Path(tmp))
            release.before = {'gateway':before('gateway')}
            base = release.before['gateway']['spec']
            target = mod.with_changes(base,'gateway',v.CANDIDATE['gateway'],{'type':'Recreate'})
            with patch.object(release,'check_live_spec'), patch.object(release,'ctl') as ctl:
                release.apply('gateway',base,target)
            args = ctl.call_args.args
            self.assertIn('--type=json',args)
            self.assertNotIn('channel-secret',' '.join(args))
            files = list(Path(tmp).glob('patch-*.json'))
            self.assertEqual(len(files),1)
            self.assertEqual(json.loads(files[0].read_bytes())[1]['value'],base)

    def test_backup_checksums_and_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            release = mod.Release(v,directory)
            sums = {}
            for c in v.BASE:
                p = release.save(c+'.before.json',before(c)); sums[p.name]=mod.sha(p.read_bytes())
            release.save('BACKUP.SHA256.json',sums)
            self.assertEqual(set(mod.load_backups(directory)),set(v.BASE))
            release.save('gateway.before.json',{'tampered':True})
            with self.assertRaises(RuntimeError): mod.load_backups(directory)

    def test_no_database_writes_or_force_flag(self):
        source = (ROOT/'rollout-candidates.py').read_text(encoding='utf-8')
        self.assertIn('BEGIN READ ONLY',source)
        self.assertNotIn('UPDATE runs',source)
        self.assertNotIn('--force',source)
        self.assertNotIn("'scale'",source)
        self.assertNotIn("'delete'",source)


if __name__=='__main__': unittest.main()

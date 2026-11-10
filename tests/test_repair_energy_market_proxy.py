"""Offline boundary tests. No cluster, supplier, or credential access."""
import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

PATH = Path(__file__).resolve().parents[1] / 'release/energy-market-released-20261110/proxy/repair/repair-energy-market-proxy-20261110-r1.py'
spec = importlib.util.spec_from_file_location('proxy_repair_host', PATH)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)


def deployment(name=host.NAME):
    return {'metadata': {'name': name, 'uid': host.UID, 'generation': 2, 'resourceVersion': '123'},
            'spec': {'replicas': 1, 'strategy': {'type': 'Recreate'}, 'selector': {'matchLabels': {'app': host.NAME}},
                     'template': {'spec': {'nodeSelector': {'kubernetes.io/hostname': host.NODE},
                       'volumes': [{'name': 'code', 'configMap': {'name': host.OLD_CM}},
                                   {'name': 'skills', 'persistentVolumeClaim': {'claimName': host.PVC}},
                                   {'name': 'credential', 'secret': {'secretName': 'existing-provider', 'defaultMode': 256}}],
                       'containers': [{'name': 'collector', 'image': 'fixed-image', 'imagePullPolicy': 'Never',
                          'command': ['python3'], 'args': ['-I','-B','-u','/opt/collector/energy_market_collector.py'],
                          'env': [{'name': 'HOME', 'value': '/private'}],
                          'volumeMounts': [{'name': 'skills', 'mountPath': '/opt/energy-skill', 'subPath': 'public/energy-market-daily', 'readOnly': True},
                                           {'name': 'credential', 'mountPath': '/run/energy-credential', 'readOnly': True}]}]}}},
            'status': {'observedGeneration': 2, 'replicas': 1, 'updatedReplicas': 1, 'readyReplicas': 1, 'availableReplicas': 1}}


def pod(name='collector-good', phase='Running', uid='pod-uid', rs_uid='rs-uid'):
    d = deployment()
    p = {'metadata': {'name': name, 'uid': uid, 'ownerReferences': [{'kind': 'ReplicaSet', 'uid': rs_uid, 'name': 'collector-rs', 'controller': True}]},
         'spec': copy.deepcopy(d['spec']['template']['spec']),
         'status': {'phase': phase, 'conditions': [{'type': 'Ready', 'status': 'True'}],
                    'containerStatuses': [{'name': 'collector', 'ready': True, 'imageID': host.IMAGE_ID,
                                           'containerID': 'containerd://id', 'restartCount': 0,
                                           'state': {'running': {'startedAt': '2026-11-10T00:00:00Z'}}}]}}
    p['spec']['nodeName'] = host.NODE
    return p


def replicasets():
    return [{'metadata': {'name': 'collector-rs', 'uid': 'rs-uid',
                         'ownerReferences': [{'kind': 'Deployment', 'uid': host.UID, 'controller': True}]}}]


class RepairTests(unittest.TestCase):
    def plan(self, d):
        with mock.patch.object(host, 'OLD_SPEC', host.identity(d)['spec_sha256']):
            return host.plan(d)

    def test_host_guard_rejects_windows_before_privilege_lookup(self):
        with mock.patch.object(host.os, 'name', 'nt'):
            with self.assertRaisesRegex(host.Refused, '') as cm:host.host_guard()
        self.assertEqual(cm.exception.code, 'DATAX01_ROOT_REQUIRED')

    def test_unsealed_rejected_explicit_mock(self):
        with mock.patch.multiple(host, CODE_B64='UNSEALED', CODE_SHA256='UNSEALED', FRESH_PROBE_B64='UNSEALED', FRESH_PROBE_SHA256='UNSEALED'):
            with self.assertRaises(host.Refused) as cm:host.sealed()
        self.assertEqual(cm.exception.code, 'UNSEALED_TOOL')

    def test_plan_only_two_allowed_mutations_and_full_tests(self):
        d = deployment(); prior = copy.deepcopy(d)
        patch, expected = self.plan(d)
        self.assertEqual(d, prior)
        self.assertEqual([x['op'] for x in patch], ['test','test','test','replace','add'])
        self.assertEqual(patch[2]['value'], d['spec'])
        self.assertEqual(patch[0]['value'], host.UID)
        self.assertEqual(patch[1]['value'], '123')
        reverted = copy.deepcopy(expected)
        reverted['template']['spec']['volumes'][0]['configMap']['name'] = host.OLD_CM
        self.assertEqual(reverted['template']['spec']['containers'][0]['env'].pop(), {'name': host.PROXY_ENV, 'value': host.PROXY})
        self.assertEqual(reverted, d['spec'])
        self.assertEqual(expected['template']['spec']['volumes'][2], d['spec']['template']['spec']['volumes'][2])

    def test_plan_rejects_existing_proxy(self):
        d = deployment(); d['spec']['template']['spec']['containers'][0]['env'].append({'name':host.PROXY_ENV,'value':host.PROXY})
        with self.assertRaises(host.Refused) as cm:self.plan(d)
        self.assertEqual(cm.exception.code, 'PROXY_ALREADY_CONFIGURED')

    def test_plan_rejects_wrong_generation(self):
        d = deployment();d['metadata']['generation'] = 3
        with self.assertRaises(host.Refused):self.plan(d)

    def test_plan_rejects_wrong_code_volume(self):
        d = deployment();d['spec']['template']['spec']['volumes'][0]['configMap']['name'] = 'other'
        with self.assertRaises(host.Refused) as cm:self.plan(d)
        self.assertEqual(cm.exception.code, 'CODE_VOLUME_CHANGED')

    def test_plan_rejects_rebuilt_or_mutable_image(self):
        d = deployment();d['spec']['template']['spec']['containers'][0]['imagePullPolicy'] = 'Always'
        with self.assertRaises(host.Refused):self.plan(d)

    def test_failed_historical_pods_not_counted(self):
        good=pod();bad=pod('failed','Failed','old')
        self.assertEqual(host.choose_pod([bad,good],replicasets(),deployment()),good)

    def test_unready_pending_owned_pod_blocks(self):
        with self.assertRaises(host.Refused):host.choose_pod([pod(),pod('pending','Pending','other')],replicasets(),deployment())

    def test_foreign_owner_is_never_selected(self):
        with self.assertRaises(host.Refused):host.choose_pod([pod(rs_uid='foreign')],replicasets(),deployment())

    def test_terminating_pod_not_reused(self):
        p=pod();p['metadata']['deletionTimestamp']='now'
        with self.assertRaises(host.Refused):host.choose_pod([p],replicasets(),deployment())

    def test_image_id_mismatch_blocks(self):
        p=pod();p['status']['containerStatuses'][0]['imageID']='sha256:wrong'
        with self.assertRaises(host.Refused):host.choose_pod([p],replicasets(),deployment())

    def test_runtime_detects_same_pod_container_restart(self):
        a=pod();b=copy.deepcopy(a);b['status']['containerStatuses'][0]['containerID']='containerd://new'
        self.assertNotEqual(host.runtime(a),host.runtime(b))

    def test_scope_forbids_secret_api_and_arbitrary_mutations(self):
        r=host.Repair()
        for args in (['get','secret','anything'],['delete','pod','any'],['apply','-f','-'],['patch','deployment','gateway']):
            with self.subTest(args=args),self.assertRaises(host.Refused):r.validate_command(args,None)

    def test_scope_rejects_modified_patch_and_noncollector_exec(self):
        r=host.Repair();r.patch=[];r.exec_names={'known'}
        with self.assertRaises(host.Refused):r.validate_command(['patch','deployment',host.NAME,'--type=json','--patch-file=/dev/stdin','-o','json'],b'[{}]')
        with self.assertRaises(host.Refused):r.validate_command(['exec','-i','known','-c','gateway','--','python3','-I','-B','-c','pass'],None)

    def test_receipt_hash_binding_before_json(self):
        with mock.patch.object(host,'secure_read',return_value=b'{}'):
            with self.assertRaises(host.Refused) as cm:host.bind_receipt()
        self.assertEqual(cm.exception.code,'PROXY_RECEIPT_CHANGED')

    def test_receipt_requires_accepted_stable_readback(self):
        raw=host.canonical({'status':'CONFIGURED_PROXY_PATHS_OBSERVED_NO_CHANGE','stable_readback':False})
        with mock.patch.object(host,'secure_read',return_value=raw),mock.patch.object(host,'RECEIPT_BYTES',len(raw)),mock.patch.object(host,'RECEIPT_SHA',host.sha(raw)):
            with self.assertRaises(host.Refused):host.bind_receipt()

    def test_receipt_summary_never_exports_original(self):
        raw=host.canonical({'status':'CONFIGURED_PROXY_PATHS_OBSERVED_NO_CHANGE','stable_readback':True,'sensitive_fake':'do-not-echo'})
        with mock.patch.object(host,'secure_read',return_value=raw),mock.patch.object(host,'RECEIPT_BYTES',len(raw)),mock.patch.object(host,'RECEIPT_SHA',host.sha(raw)):
            value=host.bind_receipt()
        self.assertEqual(set(value),{'path','bytes','sha256'})

    def test_protected_change_blocks_before_patch(self):
        r=host.Repair();r.protected=mock.Mock(return_value={'new':1});r.command=mock.Mock()
        with self.assertRaises(host.Refused) as cm:r.apply(deployment(),{'old':1},{},pod(),'source')
        self.assertEqual(cm.exception.code,'BASELINE_CHANGED_BEFORE_PATCH')
        r.command.assert_not_called();self.assertEqual(r.report['collector_patch_requests'],0)

    def test_active_collection_prevents_restart(self):
        r=host.Repair();r.execute=mock.Mock(return_value={'status':'ORIGINAL_AND_COLLECTOR_VERIFIED','original_files':158,'active_original_processes':1})
        with self.assertRaises(host.Refused) as cm:r.qualify(pod(),host.OLD_CODE)
        self.assertEqual(cm.exception.code,'ACTIVE_COLLECTION_WAIT_NO_RESTART')

    def test_patch_uncertain_flag_is_set_before_dispatch_and_never_retries(self):
        r=host.Repair();d=deployment()
        r.protected=mock.Mock(return_value={});r.infrastructure=mock.Mock(return_value={});r.qualify=mock.Mock();r.get=mock.Mock(return_value=d)
        r.command=mock.Mock(side_effect=host.Refused('COMMAND_TIMEOUT'))
        with mock.patch.object(host,'OLD_SPEC',host.identity(d)['spec_sha256']):
            with self.assertRaises(host.Refused):r.apply(d,{}, {},pod(),'source')
        self.assertTrue(r.report['collector_patch_may_have_applied']);self.assertEqual(r.report['collector_patch_requests'],1)
        self.assertEqual(r.command.call_count,1)

    def test_prepare_requests_once_even_failure(self):
        r=host.Repair();r.execute=mock.Mock(side_effect=host.Refused('TIMEOUT'))
        with self.assertRaises(host.Refused):r.request(pod(),'POST','2026-11-09')
        with self.assertRaises(host.Refused) as cm:r.request(pod(),'POST','2026-11-09')
        self.assertEqual(cm.exception.code,'PREPARE_ALREADY_REQUESTED');self.assertEqual(r.execute.call_count,1)

    def test_prepare_once_then_only_readonly_get_polling(self):
        r=host.Repair()
        r.request=mock.Mock(side_effect=[{'status':'COLLECTING'},{'status':'COLLECTING','active_data_date':'2026-11-09'},
                                        {'status':'WAIT_DATA','target_response_validated':True}])
        with mock.patch.object(host.time,'sleep'):out=r.collect_once(pod(),'2026-11-09')
        self.assertEqual([c.args[1] for c in r.request.call_args_list],['POST','GET','GET'])
        self.assertEqual(out['status'],'WAIT_DATA');self.assertFalse(r.report['formal_report_ready']);self.assertIsNone(r.phase_deadline)

    def test_incomplete_stops_without_second_collection(self):
        r=host.Repair();r.request=mock.Mock(side_effect=[{'status':'COLLECTING'},{'status':'INCOMPLETE','error_code':'PROVIDER_REQUEST_FAILED'}])
        with self.assertRaises(host.Refused) as cm:r.collect_once(pod(),'2026-11-09')
        self.assertEqual(cm.exception.code,'COLLECTION_INCOMPLETE_NO_RETRY')
        self.assertEqual(r.request.call_count,2)

    def test_other_active_date_stops_without_reprepare(self):
        r=host.Repair();r.request=mock.Mock(side_effect=[{'status':'COLLECTING'},{'status':'NOT_REQUESTED','active_data_date':'2026-11-08'}])
        with self.assertRaises(host.Refused):r.collect_once(pod(),'2026-11-09')
        self.assertEqual(r.request.call_count,2)

    def test_wait_data_without_target_proof_rejected(self):
        r=host.Repair();r.request=mock.Mock(side_effect=[{'status':'COLLECTING'},{'status':'WAIT_DATA','target_response_validated':False}])
        with self.assertRaises(host.Refused) as cm:r.collect_once(pod(),'2026-11-09')
        self.assertEqual(cm.exception.code,'TARGET_RESPONSE_NOT_VALIDATED')

    def test_endpoint_delay_only_reobserves_and_never_repatches(self):
        r=host.Repair();d=deployment();p=pod()
        r.get=mock.Mock(return_value=d);r.selected=mock.Mock(side_effect=[host.Refused('SERVICE_BACKEND_CHANGED'),p])
        r.command=mock.Mock()
        with mock.patch.object(host.time,'sleep'):
            self.assertEqual(r.wait_selected(d),p)
        self.assertEqual(r.selected.call_count,2);r.command.assert_not_called()

    def test_endpoint_wait_does_not_tolerate_runtime_identity_change(self):
        r=host.Repair();d=deployment();r.get=mock.Mock(return_value=d)
        r.selected=mock.Mock(side_effect=host.Refused('CACHED_IMAGE_ID_CHANGED'))
        with self.assertRaises(host.Refused) as cm:r.wait_selected(d)
        self.assertEqual(cm.exception.code,'CACHED_IMAGE_ID_CHANGED');self.assertEqual(r.selected.call_count,1)

    def test_command_output_bound_and_no_retry(self):
        r=host.Repair()
        def launch(args,**kw):
            kw['stdout'].write(b'x'*20);kw['stdout'].flush()
            p=mock.Mock();p.poll.return_value=0;return p
        with mock.patch.object(host.subprocess,'Popen',side_effect=launch) as popen:
            with self.assertRaises(host.Refused) as cm:r.command(['get','deployment',host.NAME,'-o','json'],bound=10)
        self.assertEqual(cm.exception.code,'COMMAND_OUTPUT_BOUND');self.assertEqual(popen.call_count,1)

    def test_command_timeout_no_retry_and_local_process_stopped(self):
        r=host.Repair();p=mock.Mock();p.poll.return_value=None
        with mock.patch.object(host.os,'name','nt'),mock.patch.object(host.subprocess,'Popen',return_value=p) as popen:
            with self.assertRaises(host.Refused) as cm:r.command(['get','deployment',host.NAME,'-o','json'],timeout=0)
        self.assertEqual(cm.exception.code,'COMMAND_TIMEOUT');p.kill.assert_called_once();self.assertEqual(popen.call_count,1)

    def test_command_budget_before_process(self):
        r=host.Repair();r.commands=host.MAX_COMMANDS
        with mock.patch.object(host.subprocess,'Popen') as popen:
            with self.assertRaises(host.Refused):r.command(['get','deployment',host.NAME,'-o','json'])
        popen.assert_not_called()

    def test_request_child_never_outputs_untrusted_string(self):
        self.assertNotIn('str(exc)',host.REQUEST)
        self.assertIn("else 'UNKNOWN'",host.REQUEST)
        self.assertIn("r.read(8193)",host.REQUEST)
        self.assertNotIn('os.environ',host.REQUEST)

    def test_qualifier_reads_no_environment_or_credential(self):
        self.assertNotIn('os.environ',host.QUALIFY)
        self.assertNotIn('/run/energy-credential',host.QUALIFY)
        self.assertIn('entry.original_files()',host.QUALIFY)

    def test_python37_host_syntax(self):
        ast.parse(PATH.read_text(encoding='utf-8'),feature_version=(3,7))

    @unittest.skipUnless(os.name=='posix','Linux ConfigMap projected symlink semantics')
    def test_projected_configmap_symlink_stays_under_fixed_mount(self):
        funcs=[n for n in ast.parse(host.QUALIFY).body if isinstance(n,ast.FunctionDef) and n.name in ('read','projected_code')]
        module=ast.fix_missing_locations(ast.Module(body=funcs,type_ignores=[]))
        import pathlib
        scope={'pathlib':pathlib,'os':os,'stat':stat}
        exec(compile(module,'<projection>','exec'),scope)
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'mount';root.mkdir();epoch=root/'..2026';epoch.mkdir()
            (epoch/host.CODE_KEY).write_bytes(b'code')
            (root/'..data').symlink_to(epoch.name,target_is_directory=True)
            (root/host.CODE_KEY).symlink_to('..data/'+host.CODE_KEY)
            self.assertEqual(scope['projected_code'](root),b'code')
            (root/host.CODE_KEY).unlink();outside=Path(td)/'outside';outside.write_bytes(b'wrong')
            (root/host.CODE_KEY).symlink_to(outside)
            with self.assertRaises(ValueError):scope['projected_code'](root)


if __name__=='__main__':unittest.main()

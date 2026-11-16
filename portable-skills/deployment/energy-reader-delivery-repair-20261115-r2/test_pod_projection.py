"""Reproduce r1's bulk-output failure and verify bounded Pod projections."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('projection_repair_sealer',HERE/'seal_repair.py')
sealer=importlib.util.module_from_spec(spec);spec.loader.exec_module(sealer)
RAW,ASSETS,_=sealer.build()
scope={'__name__':'projection_repair_operator'};exec(compile(RAW,'<r2>','exec'),scope)

def operation():return scope['Repair'](ASSETS)

def line(index=0,phase='Running',deleting='<none>',ready='True',maps='gateway-fixed,openssl-fixed'):
    return 'sandbox-%04d uid-%04d %s %s %s ReplicaSet gateway-fixed-rs rs-uid %s\n'%(index,index,phase,deleting,ready,maps)

def fake_process(stdout_raw=b'',stderr_raw=b''):
    def launch(*args,**kwargs):
        kwargs['stdout'].write(stdout_raw);kwargs['stderr'].write(stderr_raw)
        kwargs['stdout'].flush();kwargs['stderr'].flush()
        return types.SimpleNamespace(returncode=0,poll=lambda:0)
    return launch

class PodProjectionTests(unittest.TestCase):
    def test_r1_bulk_json_above_four_mib_reproduces_original_failure(self):
        original=sealer.REPO/'release/energy-reader-work-repair-20261115-r1/repair-energy-reader-work-20261115-r1.py'
        old={'__name__':'original_r1_output_repro'};exec(compile(original.read_bytes(),'<r1>','exec'),old)
        op=old['Repair'](old['load_assets']())
        raw=json.dumps({'items':[{'metadata':{'name':'gateway-fixture','annotations':{'synthetic':'x'*65536}}} for _ in range(70)]}).encode()
        self.assertGreater(len(raw),4194304)
        with patch.object(old['subprocess'],'Popen',side_effect=fake_process(raw)):
            with self.assertRaisesRegex(old['Refused'],'COMMAND_OUTPUT_BOUND'):op.command(['get','pods','-o','json'])
        self.assertFalse(op.claimed);self.assertEqual(op.out['gateway_patch_requests'],0)

    def test_seventy_gateway_pods_use_nine_columns_and_no_full_json(self):
        op=operation();calls=[];raw=''.join(line(n) for n in range(70)).encode()
        def command(args,*rest,**kwargs):calls.append((args,kwargs));return raw
        op.command=command
        dep={'metadata':{'name':'gateway'},'spec':{'selector':{'matchLabels':{'app':'gateway'}}}}
        with patch.object(op,'get') as full_read:rows=op.pods(dep)
        self.assertEqual(len(rows),70);full_read.assert_not_called()
        self.assertEqual(len(calls),1);self.assertEqual(calls[0][0][-1],scope['POD_COLUMNS'])
        self.assertTrue(calls[0][1]['plain']);self.assertIn('--no-headers',calls[0][0])
        self.assertNotIn('json',calls[0][0]);self.assertLess(len(raw),16000)
        self.assertEqual(rows[0]['metadata']['ownerReferences'][0]['uid'],'rs-uid')
        self.assertEqual(rows[0]['spec']['volumes'][0]['configMap']['name'],'gateway-fixed')
        self.assertNotIn('containers',rows[0]['spec'])

    def test_not_ready_pending_and_terminating_pods_are_excluded(self):
        op=operation();op.command=lambda *args,**kwargs:(line(0)+line(1,phase='Pending')+line(2,ready='False')+line(3,deleting='2026-11-15T05:00:00Z')).encode()
        rows=op.pods({'metadata':{'name':'gateway'},'spec':{'selector':{'matchLabels':{'app':'gateway'}}}})
        self.assertEqual([row['metadata']['name'] for row in rows],['sandbox-0000'])

    def test_malformed_and_duplicate_identity_rows_are_rejected(self):
        op=operation();dep={'metadata':{'name':'gateway'},'spec':{'selector':{'matchLabels':{'app':'gateway'}}}}
        for raw,code in [(b'not enough columns\n','POD_PROJECTION_COLUMNS_REQUIRED'),((line()+line()).encode(),'DUPLICATE_PROJECTED_POD_IDENTITY'),
                         (line(maps='illegal/secret').encode(),'POD_CONFIGMAP_REFERENCES_INVALID')]:
            with self.subTest(code=code):
                op.command=lambda *args,**kwargs:raw
                with self.assertRaisesRegex(scope['Refused'],code):op.pods(dep)

    def test_only_serving_nginx_fetches_one_full_pod_and_checks_identity(self):
        op=operation();op.command=lambda *args,**kwargs:line().encode()
        dep={'metadata':{'name':scope['BINDING']['nginx']['deployment']},'spec':{'selector':{'matchLabels':{'app':'serving-nginx'}}}}
        full={'metadata':{'name':'sandbox-0000','uid':'uid-0000'},'spec':{'containers':[{'name':'nginx','args':['original literal startup']}]},
              'status':{'phase':'Running','conditions':[{'type':'Ready','status':'True'}]}}
        with patch.object(op,'get',return_value=full) as get:self.assertEqual(op.pods(dep),[full])
        get.assert_called_once_with('pod','sandbox-0000')
        changed=copy.deepcopy(full);changed['metadata']['uid']='replaced-uid'
        with patch.object(op,'get',return_value=changed):
            with self.assertRaisesRegex(scope['Refused'],'SERVING_POD_CHANGED_AFTER_PROJECTION'):op.pods(dep)

    def test_stdout_bound_reports_sizes_and_command_kind_without_raw_values(self):
        op=operation();raw=b'SYNTHETIC_PRIVATE_CONTENT'+b'x'*524288
        with patch.object(scope['subprocess'],'Popen',side_effect=fake_process(raw)):
            with self.assertRaisesRegex(scope['Refused'],'COMMAND_OUTPUT_BOUND'):op.command(['get','pods','--no-headers','-o',scope['POD_COLUMNS']],plain=True)
        self.assertEqual(op.out['last_command_kind'],'GET_PODS_PROJECTED')
        self.assertEqual(op.out['command_output_failure']['stdout_bytes'],len(raw))
        self.assertEqual(op.out['command_output_failure']['stdout_limit'],524288)
        self.assertNotIn('SYNTHETIC_PRIVATE_CONTENT',json.dumps(op.out))

    def test_stderr_bound_is_distinguished_without_exporting_its_contents(self):
        op=operation();raw=b'PRIVATE_ERROR'+b'x'*65536
        with patch.object(scope['subprocess'],'Popen',side_effect=fake_process(b'{}',raw)):
            with self.assertRaisesRegex(scope['Refused'],'COMMAND_OUTPUT_BOUND'):op.command(['get','deployment','gateway','-o','json'])
        failure=op.out['command_output_failure']
        self.assertEqual(failure['stderr_bytes'],len(raw));self.assertEqual(failure['stdout_bytes'],2)
        self.assertNotIn('PRIVATE_ERROR',json.dumps(op.out))

    def test_claimed_original_r1_prevents_a_second_activation(self):
        op=operation()
        with tempfile.TemporaryDirectory() as directory,patch.object(op,'preflight') as preflight:
            with patch.dict(scope,{'CONTROL':Path(directory)/'new-control','PRIOR_CONTROL':Path(directory)}):
                with self.assertRaisesRegex(scope['Refused'],'PRIOR_REPAIR_CLAIMED'):op.activate()
        preflight.assert_not_called();self.assertFalse(op.claimed)

if __name__=='__main__':unittest.main()

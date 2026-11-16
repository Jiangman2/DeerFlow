"""Guard concurrent changes and verify a one-reference Nginx cutover."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

import activate_reader as activation
import nginx_reader


class ActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.control=Path(self.temp.name)/'control'
        self.source='events {}\nhttp { server { listen 2026;\n location /api/ { proxy_set_header X-Forwarded-Proto $forwarded_proto; proxy_pass http://$gateway_upstream; }\n} }'
        self.cm={'metadata':{'uid':activation.CM_UID},'immutable':True,'data':{'nginx.conf':self.source,'preserved.txt':'unchanged'}}
        self.rows={name:{'metadata':{'name':name,'uid':row[0],'generation':row[1]},'spec':{'test':name}} for name,row in activation.BASELINES.items()}
        self.rows['nginx']['spec']={'template':{'spec':{'volumes':[{'name':'nginx-config','configMap':{'name':activation.OLD_CM,'defaultMode':420}}]}}}
        baseline={n:(r['metadata']['uid'],r['metadata']['generation'],activation.sha(activation.canonical(r['spec']))) for n,r in self.rows.items()}
        self.patchers=[patch.object(activation,'CONTROL',self.control),patch.object(activation,'SOURCE_SHA',activation.sha(self.source.encode())),
                       patch.object(activation,'CM_DATA',activation.sha(activation.canonical(self.cm['data']))),patch.object(activation,'BASELINES',baseline),
                       patch.object(activation,'private_write',side_effect=lambda path,v:path.write_bytes(v if isinstance(v,bytes) else activation.canonical(v)))]
        for p in self.patchers:p.start()
        self.operation=activation.Operation(nginx_reader)
        self.operation.nginx_pod=lambda d:{'metadata':{'name':'nginx-fixture','uid':'pod-fixture'}}
        self.operation.syntax=lambda p,s:None
        self.calls=[]
        def command(args,stdin=None,as_json=True):
            self.calls.append((args,stdin))
            if args[:2]==['get','configmap']:return copy.deepcopy(self.cm)
            if args[:1]==['create']:return json.loads(stdin)
            if args[:1]==['patch']:return {}
            raise AssertionError(args)
        self.operation.command=command
        self.operation.deployments=lambda:copy.deepcopy(self.rows)
        self.operation.verify=lambda plan:True

    def tearDown(self):
        for p in reversed(self.patchers):p.stop()
        self.temp.cleanup()

    def test_only_configmap_reference_changes_original_is_backed_up(self):
        self.operation.activate()
        plan=json.loads((self.control/'PLAN.json').read_bytes())
        expected=copy.deepcopy(self.rows['nginx']['spec']);expected['template']['spec']['volumes'][0]['configMap']['name']=activation.NEW_CM
        self.assertEqual(plan['candidate_spec'],expected)
        self.assertEqual(json.loads((self.control/'ORIGINAL-CONFIGMAP.json').read_bytes()),self.cm)
        self.assertEqual(json.loads(next(body for args,body in self.calls if args[0]=='create'))['data']['preserved.txt'],'unchanged')
        patches=[json.loads(body) for args,body in self.calls if args[0]=='patch']
        self.assertEqual(len(patches),1)
        self.assertEqual([p['op'] for p in patches[0]],['test','test','replace'])
        self.assertEqual(patches[0][1]['value'],self.rows['nginx']['spec'])
        self.assertEqual(self.operation.out['nginx_patch_requests'],1)

    def test_protected_service_change_refuses_before_claim_or_mutation(self):
        self.rows['gateway']['spec']['colleague_change']=True
        with self.assertRaisesRegex(activation.Refused,'PROTECTED_DEPLOYMENT_BASELINE_CHANGED'):self.operation.activate()
        self.assertFalse(self.control.exists());self.assertFalse(self.calls)

    def test_changed_configmap_refuses_before_claim_or_mutation(self):
        self.cm['data']['nginx.conf']+='\n# colleague change\n'
        with self.assertRaisesRegex(activation.Refused,'NGINX_CONFIGMAP_BASELINE_CHANGED'):self.operation.activate()
        self.assertFalse(self.control.exists());self.assertFalse(any(args[0] in ('create','patch') for args,body in self.calls))

    def test_concurrent_service_change_after_configuration_create_never_patches(self):
        count=[0]
        def deployments():
            count[0]+=1;rows=copy.deepcopy(self.rows)
            if count[0]>=3:rows['nginx']['spec']['concurrent_change']=True
            return rows
        self.operation.deployments=deployments
        with self.assertRaisesRegex(activation.Refused,'DEPLOYMENT_CHANGED_BEFORE_PATCH'):self.operation.activate()
        self.assertTrue(self.control.exists());self.assertFalse(any(args[0]=='patch' for args,body in self.calls))

    def test_once_marker_never_replays(self):
        self.control.mkdir()
        with self.assertRaisesRegex(activation.Refused,'ONCE_MARKER_EXISTS_DO_NOT_REPLAY'):self.operation.activate()
        self.assertFalse(any(args[0] in ('create','patch') for args,body in self.calls))

if __name__=='__main__':unittest.main()

"""Synthetic wrapper tests; no cluster or database is contacted."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class HostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path=Path(__file__).with_name('package-oa-schema-compatibility-check.py')
        spec=importlib.util.spec_from_file_location('oa_schema_package',path)
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        cls.source,cls.child=module.build()

    def execute(self, fault=None):
        ns={'__name__':'host_test'}; exec(self.source,ns)
        responses=[]
        for name,expected in ns['EXPECTED'].items():
            before={'metadata':{'uid':expected['uid'],'generation':expected['generation']},'spec':{'replicas':1,'template':{'spec':{'containers':[{'image':expected['image']}]} }},'status':{'observedGeneration':expected['generation'],'updatedReplicas':1,'readyReplicas':1,'replicas':1}}
            after=copy.deepcopy(before)
            observation={'status':'SYNTHETIC_SCHEMA_COMPATIBILITY_ACCEPTED'}
            if name=='gateway':
                if fault=='uid': before['metadata']['uid']='different'
                if fault=='generation': before['metadata']['generation']+=1
                if fault=='image': before['spec']['template']['spec']['containers'][0]['image']='different'
                if fault=='rollout': before['status']['updatedReplicas']=0
                if fault=='after': after['spec']['replicas']=2
                if fault=='candidate': observation={'status':'INCOMPLETE'}
            responses.extend([before,observation,after])
        calls=[]
        def run(args,**kwargs):
            calls.append(args)
            self.assertFalse({'apply','patch','delete','create'} & set(args))
            return SimpleNamespace(returncode=0,stdout=json.dumps(responses.pop(0)).encode(),stderr=b'')
        output=io.StringIO()
        with patch.object(sys,'argv',['check','--verify-compatibility-offline']),patch('subprocess.run',run),contextlib.redirect_stdout(output):
            try: code=ns['main']()
            except AssertionError: code=1
        return code,calls,output.getvalue()

    def test_both_consumers_checked(self):
        code,calls,out=self.execute()
        self.assertEqual(code,0); self.assertEqual(len(calls),6)
        result=json.loads(out.splitlines()[-1])
        self.assertFalse(result['production_database_access'])
        self.assertFalse(result['ready_for_rollout'])

    def test_drift_stops_before_child_or_after_verification(self):
        for fault in ('uid','generation','image','rollout','after'):
            with self.subTest(fault=fault):
                code,calls,_=self.execute(fault)
                self.assertEqual(code,1)
                self.assertEqual(len(calls),3 if fault=='after' else 1)

    def test_failed_child_is_nonzero(self):
        code,_,out=self.execute('candidate')
        self.assertEqual(code,1)
        self.assertEqual(json.loads(out.splitlines()[-1])['status'],'INCOMPLETE')


if __name__=='__main__': unittest.main()

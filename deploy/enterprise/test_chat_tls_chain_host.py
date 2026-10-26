"""Controller qualification on real Python 3.7, entirely fake Kubernetes."""
import base64
import builtins
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('followup_host', str(ROOT/'inspect-portal-chat-tls-20261025-r3.py'))
H = importlib.util.module_from_spec(spec)
spec.loader.exec_module(H)


class HostTests(unittest.TestCase):
    def exercise(self, drift=False, bad_report=False):
        calls = []
        gateway = {'metadata': {'name': 'gateway', 'uid': H.GATEWAY_UID, 'generation': 388 if drift else 387},
            'spec': {'replicas': 70}}
        rs = {'metadata': {'uid': 'rs', 'ownerReferences': [{'uid':H.GATEWAY_UID, 'controller': True}]}}
        pods = [{'metadata': {'name':'g'+str(i),'uid':'p'+str(i),'ownerReferences':[{'uid':'rs','controller':True}]},
            'spec':{'nodeName':'ecs-zdz-pro-%04d'%i},
            'status':{'phase':'Running','containerStatuses':[{'name':'gateway','ready':True,'containerID':'cid'+str(i),'restartCount':0}]}}
                for i in range(18,23)]
        def query(args, *unused, **kw):
            calls.append(('get',args[1]))
            if args[0] != 'get': raise AssertionError(args)
            return {'items':{'deployments':[gateway], 'replicasets':[rs], 'pods':pods}[args[1]]}
        def runtime(source,pod,container,mode):
            calls.append(('exec',mode))
            observation = {'status':'TLS_CHAIN_SAMPLES_COLLECTED_NOT_REPAIRED','samples':[{'connection_attempted':True,'tls_attempted':True,'status':'TLS_VERIFIED'} for _ in range(4)]}
            return {'observation':observation}
        shared = zlib.decompress(base64.b64decode(H.SHARED_HOST)).decode()+'\nimport builtins\nquery=builtins._tls_fake_query\nruntime_exec=builtins._tls_fake_runtime\n'
        with tempfile.TemporaryDirectory() as d:
            prior=Path(d)/'prior.json';prior.write_text('{}')
            reports=Path(d)/'reports';reports.mkdir()
            config={ 'PRIOR_PATH':str(prior), 'PRIOR_SHA':'wrong' if bad_report else hashlib.sha256(prior.read_bytes()).hexdigest(),
                'GATEWAY_SPEC':hashlib.sha256(json.dumps(gateway['spec'],sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                'SHARED_HOST':base64.b64encode(zlib.compress(shared.encode())).decode()}
            sink=io.StringIO()
            with patch.multiple(H,**config), patch.object(H.socket,'gethostname',return_value='datax-01'), \
                    patch.object(H.tempfile,'mkdtemp',return_value=str(reports)), \
                    patch.object(builtins,'_tls_fake_query',query,create=True), patch.object(builtins,'_tls_fake_runtime',runtime,create=True), \
                    patch.object(sys,'argv',['script','--inspect-certificate-chains-readonly']), contextlib.redirect_stdout(sink):
                code=H.main()
            result=json.loads(sink.getvalue().splitlines()[-1])
            if 'report_path' in result:
                self.assertEqual(hashlib.sha256(Path(result['report_path']).read_bytes()).hexdigest(), result['report_sha256'])
        return code,result,calls

    def test_two_nodes_eight_connections_and_report(self):
        code,r,calls=self.exercise()
        self.assertEqual(code,0)
        self.assertEqual(r['connections_attempted'],8)
        self.assertEqual(r['origin_http_requests'],0)
        self.assertFalse(r['production_workload_modified'])
        self.assertEqual(calls.count(('exec','control')),1)
        self.assertEqual(calls.count(('exec','failed_node')),1)
        self.assertEqual(len([c for c in calls if c[0]=='exec']),2)

    def test_changed_deployment_no_exec(self):
        code,r,calls=self.exercise(drift=True)
        self.assertEqual(code,1)
        self.assertEqual(r['error_code'],'GATEWAY_BASELINE_CHANGED')
        self.assertFalse(any(c[0]=='exec' for c in calls))

    def test_changed_receipt_no_kubernetes_access(self):
        code,r,calls=self.exercise(bad_report=True)
        self.assertEqual(code,1)
        self.assertEqual(r['error_code'],'PRIOR_REPORT_CHANGED')
        self.assertEqual(calls,[])


if __name__=='__main__':
    unittest.main(verbosity=2)

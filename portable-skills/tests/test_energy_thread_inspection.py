"""Named-thread selection, no-mutation boundary and stored market evidence."""
import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

SOURCE=Path(__file__).resolve().parents[1]/'deployment/public-runtime-20261112-r1/inspect-energy-daily-thread-20261112-r1.py'
SPEC=importlib.util.spec_from_file_location('energy_thread_probe',SOURCE)
host=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(host)
child=types.ModuleType('energy_thread_probe_child');exec(compile(host.CHILD,'<fixed-child>','exec'),child.__dict__)


class HostTests(unittest.TestCase):
    def row(self,thread=None,**changes):
        values=dict(name='sandbox-01234567',uid='12345678-1234-1234-1234-123456789abc',thread=thread or host.THREAD,node='ecs-zdz-pro-0017',phase='Running',deleting='<none>',ready='true')
        values.update(changes);return ' '.join(values.values())

    def test_python37_operator_syntax(self):
        ast.parse(SOURCE.read_text(encoding='utf-8'),feature_version=(3,7))

    def test_only_fixed_thread_is_selected(self):
        raw='\n'.join((self.row(),self.row(thread='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',name='sandbox-unrelated')))
        self.assertEqual(len(host.candidates(raw)),1)
        self.assertEqual(host.candidates(self.row(phase='Failed')),[])
        self.assertEqual(host.candidates(self.row(deleting='2026-11-12T00:00:00Z')),[])

    def test_multiple_live_named_pods_refuse(self):
        with self.assertRaisesRegex(host.Refused,'NAMED_THREAD_POD_AMBIGUOUS'):
            host.candidates(self.row()+'\n'+self.row(name='sandbox-duplicate'))

    def test_no_pod_means_no_exec_target(self):
        operation=host.Operation()
        with self.assertRaisesRegex(host.Refused,'READONLY_FIXED_COMMAND_REQUIRED'):
            operation.command(['exec','sandbox-01234567','--','python3'])

    def test_mutations_secrets_and_other_pods_are_refused(self):
        operation=host.Operation();operation.pod='sandbox-01234567'
        for args in (['create','-f','-'],['delete','pod',operation.pod],['patch','deployment','gateway'],['get','secret','deer-flow-env'],['get','pod','sandbox-other','-o','json']):
            with self.assertRaisesRegex(host.Refused,'READONLY_FIXED_COMMAND_REQUIRED'):
                operation.command(args)

    def test_arbitrary_python_exec_is_refused(self):
        operation=host.Operation();operation.pod='sandbox-01234567'
        args=['exec','-i',operation.pod,'-c','sandbox','--','python3','-I','-B','-c','print("arbitrary")']
        with self.assertRaisesRegex(host.Refused,'READONLY_FIXED_COMMAND_REQUIRED'):
            operation.command(args)

    def test_pod_metadata_cannot_change_thread(self):
        chosen=self.row().split()
        pod={'metadata':{'name':chosen[0],'uid':chosen[1],'annotations':{'deerflow.io/thread-id':'other'}},'spec':{'nodeName':chosen[3]},'status':{}}
        with self.assertRaisesRegex(host.Refused,'NAMED_THREAD_POD_CHANGED'):
            host.pod_identity(pod,chosen)

    def test_annotation_column_escapes_dot_and_exec_child_has_no_network_import(self):
        self.assertIn('annotations.deerflow\\.io/thread-id',host.COLUMNS)
        tree=ast.parse(host.CHILD)
        imported=set()
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):imported.update(name.name for name in node.names)
            elif isinstance(node,ast.ImportFrom):imported.add(node.module)
        self.assertFalse(imported & {'requests','urllib','http','socket','subprocess','playwright'})


@unittest.skipUnless(os.name=='posix','Actual child filesystem behavior is Linux-only')
class StoredMarketTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='thread-probe-')
        self.root=Path(self.temporary.name)
        self.patch=patch.object(child,'ROOT',self.root);self.patch.start();child.BYTES_READ=0
        self.project=self.root/'energy-daily-20261111';self.project.mkdir()

    def tearDown(self):
        self.patch.stop();self.temporary.cleanup()

    def save(self,path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(json.dumps(value,ensure_ascii=False).encode())

    def fixture(self):
        target='2026-11-11'
        cache={'errcode':'0','data':[]}
        market={'data_date':target,'retrieved_at':'2026-11-12T00:33:00+08:00','market_complete':False,'provider_requests':4,'observations':[],'oil':[],'gas':[],'freight':[],'acquisition_failures':{}}
        for index,code in enumerate(child.CODES):
            available=code=='XA0942'
            close='31.825' if available else ''
            cache['data'].append({'code_name':code,'value_date':target,'close':close,'unit':'unit','item':'not exported'})
            market['observations'].append({'code':code,'date':target,'raw_close':close,'status':'available' if available else 'missing','value':float(close) if available else None})
            kind='oil' if index<3 else 'gas' if index<6 else 'freight'
            market[kind].append({'code':code,'date':target,'status':'available' if available else 'missing','value':float(close) if available else None})
        self.save(self.project/'market.json',market)
        state={'publication_date':'2026-11-12','data_date':target,'prepared_at':'2026-11-12T00:33:00+08:00',
               'input_hashes':{'market':hashlib.sha256((self.project/'market.json').read_bytes()).hexdigest()}}
        self.save(self.project/'PROJECT.json',state);self.save(self.project/'cache/jkx/jkx_new_2026-11-11.json',cache)
        return market,cache,state

    def test_jkm_only_payload_is_reported_without_assuming_provider_schedule(self):
        self.fixture();result=child.inspect_project(self.project)
        self.assertEqual(result['publication_date'],'2026-11-12')
        self.assertTrue(result['market_matches_project_input_hash'])
        series={row['code']:row for row in result['target_cache']['series']}
        self.assertEqual(series['XA0942']['close_classes'],['numeric_positive'])
        self.assertEqual(series['LD0221']['close_classes'],['recognized_missing'])
        self.assertNotIn('尚未发布',json.dumps(result,ensure_ascii=False))
        self.assertNotIn('31.825',json.dumps(result))
        self.assertTrue(result['stable_readback'])

    def test_explicit_weekend_is_distinguished_from_blank(self):
        _,cache,_=self.fixture()
        for row in cache['data']:row['close']='周六'
        self.save(self.project/'cache/jkx/jkx_new_2026-11-11.json',cache)
        result=child.inspect_project(self.project)
        self.assertTrue(all(row['close_classes']==['explicit_nontrading'] for row in result['target_cache']['series']))

    def test_market_modified_after_prepare_is_visible(self):
        market,_,_=self.fixture();market['market_complete']=True
        self.save(self.project/'market.json',market)
        self.assertFalse(child.inspect_project(self.project)['market_matches_project_input_hash'])

    def test_missing_target_cache_does_not_probe_elsewhere(self):
        self.fixture();(self.project/'cache/jkx/jkx_new_2026-11-11.json').unlink()
        result=child.inspect_project(self.project)
        self.assertEqual(result['target_cache'],{'present':False,'external_cache_not_inspected':True})

    def test_path_escape_and_symlinks_are_rejected(self):
        self.fixture()
        with self.assertRaisesRegex(child.Refused,'PROJECT_SCOPE'):
            child.inspect_project(self.root/'..'/'energy-daily-private')
        target=self.project/'market.json';target.unlink();target.symlink_to(self.root/'other')
        with self.assertRaisesRegex(child.Refused,'PATH_LINK_REFUSED'):
            child.inspect_project(self.project)

    def test_invalid_date_does_not_become_a_filesystem_path(self):
        _,_,state=self.fixture();state['data_date']='../../credential'
        self.save(self.project/'PROJECT.json',state)
        with self.assertRaisesRegex(child.Refused,'STORED_DATE_INVALID'):
            child.inspect_project(self.project)

    def test_no_production_business_file_writes(self):
        self.fixture()
        before={str(path.relative_to(self.project)):path.read_bytes() for path in self.project.rglob('*') if path.is_file()}
        child.inspect_project(self.project)
        after={str(path.relative_to(self.project)):path.read_bytes() for path in self.project.rglob('*') if path.is_file()}
        self.assertEqual(before,after)

    def test_output_listing_excludes_unrelated_files_and_exported_bodies(self):
        out=self.project/'outputs';out.mkdir()
        (out/'油气日报_20261112.txt').write_bytes(b'public report body must not be exported')
        (out/'credentials.private.json').write_bytes(b'must not be read')
        result=child.report_files(out)
        self.assertEqual(len(result['files']),1)
        self.assertEqual(result['files'][0]['name'],'油气日报_20261112.txt')
        self.assertNotIn('public report body',json.dumps(result))
        self.assertFalse(result['file_bodies_exported'])

    def test_output_limit_is_32(self):
        out=self.project/'outputs';out.mkdir()
        for index in range(35):(out/('energy-daily-%d.txt'%index)).write_bytes(b'x')
        result=child.report_files(out)
        self.assertEqual(len(result['files']),32)
        self.assertTrue(result['additional_matching_files'])


if __name__=='__main__':unittest.main()

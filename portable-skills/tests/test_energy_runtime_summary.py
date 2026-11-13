"""Runtime metadata summaries never load an application or read config bodies."""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

FILE=Path(__file__).resolve().parents[1]/'deployment/webhook-budget-20261112-r1/runtime_summary.py'
SPEC=importlib.util.spec_from_file_location('fixed_runtime_summary',FILE)
runtime=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(runtime)


class SummaryTests(unittest.TestCase):
    def observe(self,config):
        with patch.object(runtime.importlib.metadata,'version',side_effect=lambda name:{'langchain':'1.0.1','langgraph':'1.0.2','langchain-core':'1.0.3'}[name]),\
             patch.object(runtime,'_source_hash',return_value=({'present':False,'error_code':'FIXED_SOURCE_NOT_PRESENT'},0)),\
             patch.dict(runtime.sys.modules,{runtime.APP_CONFIG_MODULE:types.SimpleNamespace()}):
            return runtime.observe_runtime(config)

    def test_exact_numeric_and_boolean_whitelist_without_credentials(self):
        cfg=types.SimpleNamespace(scheduler=types.SimpleNamespace(recursion_limit=300),max_recursion_limit=10000,password='private-password')
        for index,key in enumerate(runtime.SWITCHES):setattr(cfg,key,types.SimpleNamespace(enabled=bool(index%2),api_key='private-key'))
        result=self.observe(cfg)
        self.assertEqual(result['budgets'],{'scheduler_recursion_limit':300,'max_recursion_limit':10000})
        self.assertEqual(set(result['feature_switches']),set(runtime.SWITCHES))
        self.assertIs(result['feature_switches']['summarization'],False)
        self.assertNotIn('private-',json.dumps(result));self.assertFalse(result['configuration_loaded_again'])

    def test_missing_or_invalid_attributes_remain_unknown(self):
        result=self.observe(types.SimpleNamespace(scheduler=types.SimpleNamespace(recursion_limit=True),max_recursion_limit='private',memory=types.SimpleNamespace(enabled='true')))
        self.assertEqual(result['budgets'],{'scheduler_recursion_limit':None,'max_recursion_limit':None})
        self.assertTrue(all(value is None for value in result['feature_switches'].values()))
        self.assertNotIn('private',json.dumps(result))

    def test_fixed_package_metadata_versions_without_importing_packages(self):
        cfg=types.SimpleNamespace()
        with patch.object(runtime.importlib.metadata,'version',side_effect=['1.2.3','private token',runtime.importlib.metadata.PackageNotFoundError()]) as version,\
             patch.object(runtime,'_source_hash',return_value=({'present':False},0)):
            result=runtime.observe_runtime(cfg)
        self.assertEqual([call.args[0] for call in version.call_args_list],list(runtime.PACKAGES))
        self.assertEqual(result['library_versions'],{'langchain':'1.2.3','langgraph':None,'langchain-core':None})
        self.assertNotIn('private token',json.dumps(result))

    def test_cached_config_metadata_does_not_read_body_or_export_nondefault_path(self):
        cfg=types.SimpleNamespace()
        with tempfile.TemporaryDirectory(prefix='runtime-config-owner-') as temporary:
            config_path=Path(temporary)/'config.yaml';config_path.write_text('PASSWORD: private-credential-body',encoding='utf-8')
            module=types.SimpleNamespace(_app_config=cfg,_app_config_is_custom=False,_app_config_path=config_path,_app_config_signature=(1,2,'a'*64))
            with patch.dict(runtime.sys.modules,{runtime.APP_CONFIG_MODULE:module}),patch.object(runtime.os,'open',side_effect=AssertionError('must not open configuration')):
                result=runtime._cached_config_metadata(cfg)
            serialized=json.dumps(result)
            self.assertNotIn(str(config_path),serialized);self.assertNotIn('private-credential-body',serialized)
            self.assertTrue(result['supplied_config_is_module_cached_instance']);self.assertFalse(result['configuration_body_read'])
            self.assertEqual(result['path_sha256'],hashlib.sha256(str(config_path).encode()).hexdigest())
            self.assertEqual(result['cached_content_sha256'],'a'*64);self.assertTrue(result['file_metadata']['regular'])

    def test_default_path_metadata_and_missing_module_are_explicit(self):
        cfg=object()
        with patch.dict(runtime.sys.modules,{runtime.APP_CONFIG_MODULE:None}):
            self.assertFalse(runtime._cached_config_metadata(cfg)['module_already_loaded'])
        module=types.SimpleNamespace(_app_config=object(),_app_config_path=runtime.DEFAULT_CONFIG_PATH)
        with patch.dict(runtime.sys.modules,{runtime.APP_CONFIG_MODULE:module}):result=runtime._cached_config_metadata(cfg)
        self.assertEqual(result['known_path'],runtime.DEFAULT_CONFIG_PATH)
        self.assertFalse(result['supplied_config_is_module_cached_instance'])

    def test_hashes_only_four_fixed_sources_with_a_total_bound(self):
        cfg=types.SimpleNamespace()
        calls=[]
        def hashed(path,remaining):
            calls.append((path,remaining));return {'present':True,'sha256':'a'*64},100
        with patch.object(runtime,'_source_hash',side_effect=hashed),patch.object(runtime.importlib.metadata,'version',return_value='1.0'):
            result=runtime.observe_runtime(cfg)
        self.assertEqual([path for path,_ in calls],list(runtime.SOURCE_PATHS.values()))
        self.assertEqual([remaining for _,remaining in calls],[runtime.TOTAL_SOURCE_LIMIT-offset for offset in (0,100,200,300)])
        self.assertEqual(result['source_bytes_read'],400);self.assertTrue(result['all_fixed_sources_verified'])

    def test_source_hash_is_stable_bounded_and_never_exports_contents(self):
        with tempfile.TemporaryDirectory(prefix='runtime-source-') as temporary:
            path=Path(temporary)/'source.py';path.write_bytes(b'private-source-content')
            row,count=runtime._source_hash(path,1024)
            self.assertEqual(count,len(b'private-source-content'));self.assertEqual(row['sha256'],hashlib.sha256(b'private-source-content').hexdigest())
            self.assertNotIn('private-source-content',json.dumps(row))
            row,count=runtime._source_hash(path,2);self.assertEqual(row['error_code'],'FIXED_SOURCE_BOUND');self.assertEqual(count,0)
            missing,count=runtime._source_hash(Path(temporary)/'absent.py',1024)
            self.assertEqual(missing['error_code'],'FIXED_SOURCE_NOT_PRESENT');self.assertEqual(count,0)

    @unittest.skipUnless(os.name=='posix','POSIX source symlink fixture')
    def test_source_symlinks_are_refused(self):
        with tempfile.TemporaryDirectory(prefix='runtime-link-') as temporary:
            root=Path(temporary);source=root/'source.py';source.write_bytes(b'fixed')
            link=root/'link.py';link.symlink_to(source)
            row,count=runtime._source_hash(link,1024)
            self.assertEqual(row['error_code'],'SOURCE_LINK_REFUSED');self.assertEqual(count,0)

    def test_no_application_imports_network_process_or_config_reload_calls(self):
        tree=ast.parse(FILE.read_text(encoding='utf-8'))
        imports=[];calls=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):imports.extend(item.name for item in node.names)
            elif isinstance(node,ast.ImportFrom):imports.append(node.module)
            elif isinstance(node,ast.Call) and isinstance(node.func,ast.Name):calls.append(node.func.id)
        self.assertEqual(set(imports),{'hashlib','importlib.metadata','os','pathlib','re','stat','sys'})
        self.assertFalse(set(calls)&{'get_app_config','reload_app_config','get_sandbox_provider','create_chat_model','exec','eval'})
        ast.parse(FILE.read_text(encoding='utf-8'),feature_version=(3,7))


if __name__=='__main__':
    unittest.main()

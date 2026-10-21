import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('checked_followup',os.environ.get('FOLLOWUP',str(Path(__file__).with_name('check.py'))))
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)


def fixture():
    return dict(transaction_read_only='on',max_connections=100,reserved_connections=3,
        client_backends=20,database_client_backends=10,database_parallel_workers=4,idle_in_transaction=0,
        global_setting=dict(present=True,value=128,valid_numeric_nonsecret=True),
        models=[dict(id=c.MODELS[2],stored_override=None,mode='INHERIT_GLOBAL')])


class Tests(unittest.TestCase):
    def test_json_override_not_nonexistent_column(self):
        self.assertIn("parameters->>'max_concurrency'",c.MODEL_SQL)
        self.assertNotIn("'max_concurrency',max_concurrency",c.MODEL_SQL)

    def test_sql_read_only_bounded_timeout(self):
        for sql in (c.MODEL_SQL,c.WAIT_SQL):
            self.assertTrue(sql.startswith('BEGIN READ ONLY'))
            self.assertIn("statement_timeout='5s'",sql)
            self.assertIn("lock_timeout='1s'",sql)
            self.assertTrue(sql.rstrip().endswith('ROLLBACK;'))
            for word in ('UPDATE ','INSERT ','DELETE ','CREATE ','ALTER '):self.assertNotIn(word,sql)

    def test_no_secret_or_raw_query_selection(self):
        self.assertNotIn('api_key',c.MODEL_SQL);self.assertNotIn('SELECT query',c.WAIT_SQL)
        self.assertNotIn('query,',c.WAIT_SQL);self.assertNotIn('SELECT parameters ',c.MODEL_SQL)

    def test_inherit_model_does_not_mean_infinite(self):
        row=c.models(fixture());self.assertEqual(row['models'][0]['mode'],'INHERIT_GLOBAL')
        self.assertEqual(row['global_setting']['value'],128)

    def test_missing_global_not_invented_zero(self):
        row=fixture();row['global_setting']=None
        self.assertIsNone(c.models(row)['global_setting'])

    def test_unknown_model_rejected(self):
        row=fixture();row['models'][0]['id']='SECRET'
        with self.assertRaises(c.Failure):c.models(row)

    def test_invalid_mode_rejected(self):
        row=fixture();row['models'][0]['mode']='SECRET'
        with self.assertRaises(c.Failure):c.models(row)

    def test_non_read_only_rejected(self):
        row=fixture();row['transaction_read_only']='off'
        with self.assertRaises(c.Failure):c.models(row)

    def test_parallel_workers_kept_separate(self):
        row=c.waits(dict(transaction_read_only='on',groups=[dict(backend_type='parallel worker',wait_type='IPC',
            wait_event='MessageQueueReceive',records=39,parallel_follower_records=39,max_query_age_seconds=28.3)]))
        self.assertEqual(row['groups'][0]['backend_type'],'parallel worker')
        self.assertTrue(row['parallel_workers_are_not_app_connections'])

    def test_unknown_event_not_exported(self):
        with self.assertRaises(c.Failure):c.waits(dict(transaction_read_only='on',groups=[dict(
            backend_type='client backend',wait_type='IPC',wait_event='SECRET',records=1,parallel_follower_records=0,max_query_age_seconds=0)]))

    def test_nan_age_rejected(self):
        with self.assertRaises(c.Failure):c.waits(dict(transaction_read_only='on',groups=[dict(
            backend_type='client backend',wait_type='IPC',wait_event='OTHER',records=1,parallel_follower_records=0,max_query_age_seconds=float('nan'))]))

    def test_safe_error(self):
        self.assertNotIn('SECRET',json.dumps(c.safe_error(ValueError('SECRET'))))

    def test_private_inputs(self):
        with tempfile.TemporaryDirectory(dir='/var/lib') as root:
            path=Path(root)/'x';path.write_bytes(b'x');path.chmod(0o600)
            self.assertEqual(c.safe_read(path),b'x')
            with self.assertRaises(c.Failure):c.safe_read(path,'0'*64)

    def test_symlink_refused(self):
        with tempfile.TemporaryDirectory(dir='/var/lib') as root:
            path=Path(root)/'x';path.write_bytes(b'x');link=Path(root)/'link';link.symlink_to(path)
            with self.assertRaises(OSError):c.safe_read(link)

    def test_resource_report_binding(self):
        report=dict(resources_start=[dict(node='n')],samples=[dict(utc='x',resources=[])])
        old=types.SimpleNamespace(summarize=lambda r:dict(bound=True))
        with patch.object(c,'safe_read',return_value=json.dumps(report).encode()):
            result=c.stored_resources(old,dict(bound=True))
            self.assertEqual(result['source'],'PREVIOUS_CAPTURE_NOT_FRESH_NODE_METRICS')
            with self.assertRaises(c.Failure):c.stored_resources(old,dict(bound=False))

    def test_wrong_host_before_reads(self):
        with patch.object(c.socket,'gethostname',return_value='not-datax01'),patch.object(c,'safe_read',side_effect=AssertionError('read')):
            with self.assertRaises(c.Failure):c.main()


if __name__=='__main__':unittest.main(verbosity=2)

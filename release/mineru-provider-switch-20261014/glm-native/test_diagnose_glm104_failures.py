import ast
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch

import diagnose_glm104_failures as f
import diagnose_glm104_mineru as d


class FailureTests(unittest.TestCase):
    def test_python37(self):
        ast.parse(Path(f.__file__).read_text(encoding='utf-8'), feature_version=(3,7))

    def test_rules(self):
        for text, want in (
            ('invalid token', 'AUTHENTICATION_OR_PERMISSION'), ('too many requests', 'RATE_OR_QUOTA'),
            ('no healthy upstream', 'UPSTREAM_UNAVAILABLE'), ('context deadline exceeded', 'DEADLINE_OR_TIMEOUT'),
            ('task abc not found', 'TASK_NOT_FOUND'), ('maximum context length', 'INPUT_TOO_LARGE'),
            ('unsupported file format', 'FILE_OR_FORMAT_REJECTED'), ('field required', 'REQUEST_VALIDATION'),
            ('choices=null', 'EMPTY_OR_INVALID_RESPONSE'), ('x509: invalid certificate', 'TLS_OR_DNS'),
            ('HTTP status 503', 'HTTP_5XX'), ('HTTP 400', 'HTTP_4XX'), ('SECRET', 'UNCLASSIFIED')):
            with self.subTest(text=text): self.assertEqual(f.classify(text),want)

    def test_provider_hint_not_stage_inference(self):
        self.assertEqual(f.hint('summary failed'), 'NOT_IDENTIFIED')
        self.assertEqual(f.hint('DeepSeek upstream'), 'DEEPSEEK_MENTIONED')
        self.assertEqual(f.hint('GLM and MinerU'), 'MULTIPLE_MENTIONED')

    def test_codes(self):
        for text,want in (('{"code":20009,"msg":"SECRET"}','20009'),
                          ('code=10020;','10020'), ('code=SECRET;',None),
                          ('code=123456789;',None), ('customer number 1234',None)):
            self.assertEqual(f.business_code(text),want)

    def test_worker_redaction(self):
        raw=b'2026-10-17T08:00:00Z MinerU upstream request rejected: Enterprise MinerU HTTP 400 during asynchronous submission: {"code":20009,"msg":"field required Bearer SECRET customer.pdf"}\n'
        out=f.summarize_logs(raw,d,'worker')
        for secret in ('SECRET','customer.pdf','Bearer','field required'):
            self.assertNotIn(secret,json.dumps(out))
        self.assertEqual(out['groups'][0]['reason_hint'],'REQUEST_VALIDATION')
        self.assertEqual(out['groups'][0]['business_code'],'20009')
        self.assertTrue(out['provider_mention_is_not_causal_attribution'])

    def test_no_worker_inference_from_other_log(self):
        self.assertFalse(f.summarize_logs(b'ERROR Deepseek timed out',d,'worker')['groups'])

    def test_app_error_only(self):
        out=f.summarize_logs(b'INFO Deepseek normal\nERROR Deepseek status code: 503 SECRET\n',d,'app')
        self.assertEqual(len(out['groups']),1)
        self.assertEqual(out['groups'][0]['reason_hint'],'HTTP_5XX')
        self.assertEqual(out['groups'][0]['provider_hint'],'DEEPSEEK_MENTIONED')
        self.assertNotIn('SECRET',json.dumps(out))

    def test_log_caps(self):
        out=f.summarize_logs(b'x'*f.LIMIT,d,'worker')
        self.assertTrue(out['coverage']['byte_limit_reached'])
        self.assertFalse(out['coverage']['complete_history_proven'])
        raw=''.join('Enterprise MinerU HTTP 400 during task status: code=%d;\n'%i for i in range(65))
        out=f.summarize_logs(raw.encode(),d,'worker')
        self.assertEqual(len(out['groups']),60)
        self.assertEqual(out['omitted_groups'],5)
        self.assertEqual(out['omitted_marker_lines'],5)

    def test_empty_logs(self):
        out=f.summarize_logs(b'',d,'worker')
        self.assertIsNone(out['coverage']['first_utc'])
        self.assertEqual(out['groups'],[])

    def test_no_error_details(self):
        self.assertNotIn('SECRET',json.dumps(f.safe_error(RuntimeError('SECRET'))))

    def test_read_commands(self):
        calls=[]
        obs=types.SimpleNamespace(call=lambda a,**kw:calls.append((a,kw)) or b'', container=lambda p:{'name':'worker'})
        f.log_read(obs,d,{'metadata':{'name':'test-worker'}},'worker')
        self.assertEqual(calls[0][0][0],'logs')
        self.assertIn('--since-time='+f.SINCE,calls[0][0])
        self.assertEqual(len(calls),1)

    def test_failure_not_retried(self):
        calls=[]
        def fail(*a,**kw): calls.append(1); raise RuntimeError('SECRET')
        obs=types.SimpleNamespace(call=fail,container=lambda p:{'name':'app'})
        out=f.log_read(obs,d,{'metadata':{'name':'test-app'}},'app')
        self.assertEqual(len(calls),1)
        self.assertIn('unavailable',out)
        self.assertNotIn('SECRET',json.dumps(out))

    def test_plan(self):
        good=[{'Plan':{'Node Type':'Limit','Plans':[{'Node Type':'Index Scan','Relation Name':'knowledge_processing_spans'}]}}]
        self.assertEqual(f.check_plan(good),['Limit','Index Scan'])
        for bad in ([{'Plan':{'Node Type':'Seq Scan'}}], [], [{'Plan':{'Node Type':'Index Scan','Relation Name':'secret'}}]):
            with self.assertRaises(f.CheckFailure): f.check_plan(bad)

    def row(self):
        return dict(read_only='on',sample_limit=2000,sampled_failed_spans=1,
            documents_with_sampled_failed_spans=1,sample_cap_reached=False,omitted_groups=0,omitted_buckets=0,
            oldest_started_epoch=1,newest_started_epoch=2,groups=[],buckets=[])

    def test_database_empty_and_allowlist(self):
        row=self.row(); row['SECRET']='SECRET'
        out=f.clean_database(row)
        self.assertNotIn('SECRET',json.dumps(out))
        self.assertTrue(out['final_document_status_not_read'])
        self.assertTrue(out['cancelled_spans_excluded'])

    def test_database_reject_bad_shapes(self):
        for key,val in (('read_only','off'),('sampled_failed_spans',2001),('sample_cap_reached','true'),('omitted_groups',float('nan'))):
            row=self.row(); row[key]=val
            with self.assertRaises(f.CheckFailure): f.clean_database(row)

    def test_database_reject_unknown_labels(self):
        row=self.row(); row['groups']=[dict(stage='SECRET')]
        with self.assertRaises(f.CheckFailure): f.clean_database(row)

    def test_sql_static_read_only(self):
        sql=f.aggregate_sql()
        self.assertTrue(sql.startswith('BEGIN READ ONLY;'))
        self.assertIn("status='failed'",sql)
        self.assertIn('LIMIT 2000',sql)
        self.assertIn('left(coalesce(error_detail',sql)
        for forbidden in ('UPDATE ', 'DELETE ', 'INSERT ', 'metadata', 'api_key', 'filename'):
            self.assertNotIn(forbidden,sql)

    def test_binding_failure_no_sql(self):
        obs=types.SimpleNamespace(call=lambda *a,**kw:self.fail('SQL after failed binding'))
        def denied(*a): raise RuntimeError('SECRET')
        review=types.SimpleNamespace(pg_binding=denied)
        with self.assertRaises(RuntimeError): f.database_read(obs,review,{})

    def test_collect_does_not_mutate_or_accept_partial(self):
        pod={'metadata':{'name':'app'}}
        fake=types.SimpleNamespace(inventory=lambda obs:({'weknora-app':[pod]},'stable'))
        obs=types.SimpleNamespace(WORKERS=[])
        rows=[]
        with patch.object(f,'database_read',side_effect=RuntimeError('SECRET')), \
             patch.object(f,'log_read',return_value={'role':'app'}),patch.object(f,'emit',side_effect=rows.append):
            self.assertEqual(f.collect(fake,obs,None),1)
        self.assertEqual(rows[-1]['production_writes'],0)
        self.assertEqual(rows[-1]['failure_diagnostic'],'INCOMPLETE')
        self.assertNotIn('SECRET',json.dumps(rows))


if __name__=='__main__': unittest.main()

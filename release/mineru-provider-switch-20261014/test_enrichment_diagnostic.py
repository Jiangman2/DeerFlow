import ast
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock

import enrichment_diagnostic as m


class DiagnosticTests(unittest.TestCase):
    def test_sql_read_only_bounded_no_content(self):
        sql = m.span_sql(1800000000)
        self.assertIn('BEGIN READ ONLY', sql)
        self.assertIn('statement_timeout', sql)
        self.assertIn('ORDER BY id DESC LIMIT 20000', sql)
        self.assertIn('AS MATERIALIZED', sql)
        self.assertIn('ROLLBACK', sql)
        for term in ('error_message', 'error_detail', 'knowledge_id', 'span_id', 'metadata', 'SELECT *'):
            self.assertNotIn(term, sql)
        for term in ('UPDATE ', 'DELETE ', 'INSERT ', 'ALTER ', 'CREATE ', 'ANALYZE '):
            self.assertNotIn(term, sql)

    def test_cutoff_is_integer_not_sql_input(self):
        for value in ('1; DROP TABLE x', True, -1, 1.2):
            with self.assertRaises(m.obs.CheckFailure):
                m.span_sql(value)

    def test_explain_accepts_only_bounded_primary_index_access(self):
        good = [{'Plan': {'Node Type':'Limit', 'Plans':[{'Node Type':'Index Scan Backward',
                 'Relation Name':'knowledge_processing_spans'}]}}]
        # PostgreSQL normally says Index Scan plus Scan Direction Backward.
        good[0]['Plan']['Plans'][0]['Node Type'] = 'Index Scan'
        self.assertEqual(m.clean_plan(good), ['Limit','Index Scan'])
        for bad in ('Seq Scan','Sort','Bitmap Heap Scan'):
            good[0]['Plan']['Plans'][0]['Node Type'] = bad
            with self.assertRaises(m.obs.CheckFailure):
                m.clean_plan(good)

    def test_schema_gate(self):
        m.check_schema({'transaction_read_only':'on','ordinary_table':True,'id_primary_key':True,
                        'columns_match':True})
        for k in ('ordinary_table','id_primary_key','columns_match'):
            row = dict(transaction_read_only='on',ordinary_table=True,id_primary_key=True,columns_match=True)
            row[k] = False
            with self.assertRaises(m.obs.CheckFailure):
                m.check_schema(row)

    def test_parse_model_counts_only_allowlist(self):
        raw = ('T\t1800000000\n' + ''.join('M\t%s\t%d\n' % (x,i) for i,x in enumerate(m.review.MODEL_IDS))+'END\n').encode()
        out = m.parse_models(raw)
        self.assertEqual(out['holders'][m.review.MODEL_IDS[2]], 2)
        for bad in (raw.replace(b'\t2\n', b'\t-1\n'), raw.replace(b'END\n',b''),
                    raw.replace(m.review.MODEL_IDS[0].encode(), b'unknown-secret-model')):
            with self.assertRaises(m.obs.CheckFailure):
                m.parse_models(bad)

    def test_model_script_reads_only_no_lease_prune(self):
        sh = m.MODEL_SHELL
        self.assertIn('ZCOUNT', sh)
        self.assertNotIn('ZREMRANGE', sh)
        self.assertNotIn('EVAL', sh)
        self.assertNotIn('SCAN ', sh)
        self.assertIn('REDISCLI_AUTH', sh)

    def test_logs_export_only_counts(self):
        raw = b'[ImageMultimodal] OCR failed for secret-url: PRIVATE_DOCUMENT\n[VLM] Calling OpenAI-compatible API, model=SECRET\n[ModelLimiter] acquire failed for key=private failing open: password\n'
        out = m.log_counts(raw)
        self.assertEqual(out['ocr_error_lines'], 1)
        self.assertEqual(out['vlm_start_lines'], 1)
        self.assertEqual(out['limiter_fail_open_lines'], 1)
        self.assertNotIn('SECRET', json.dumps(out))
        self.assertNotIn('PRIVATE', json.dumps(out))
        self.assertNotIn('password', json.dumps(out))

    def test_summary_does_not_fabricate_substage_timings(self):
        report = m.new_report()
        out = m.summarize(report)
        self.assertEqual(out['substage_attribution'], 'NOT_SEPARATELY_INSTRUMENTED')
        self.assertEqual(out['ready_to_expand'], False)
        self.assertIsNone(out['model_holders'])
        self.assertNotIn('bottleneck_proven', out)

    def test_model_holders_not_waiters_or_provider_concurrency(self):
        r = m.new_report()
        r['samples'] = [{'models': {'holders':{x:i for i,x in enumerate(m.review.MODEL_IDS)}}}]*2
        out = m.summarize(r)
        self.assertEqual(out['model_holders'][m.review.MODEL_IDS[2]]['max'], 2)
        self.assertEqual(out['local_model_waiter_count'], 'NOT_MEASURED')
        self.assertEqual(out['provider_actual_concurrency'], 'NOT_MEASURED')

    def test_clean_spans_rejects_unexpected_strings_and_nonfinite(self):
        row = {'transaction_read_only':'on','sample_limit':20000,'sample_rows':10,'recent_rows':3,
               'sample_cap_reached':False,'cutoff_epoch':1800000000,'oldest_sample_created_epoch':1799999999,
               'groups':[dict(category='multimodal.image',total=3,done=2,running=1,failed=0,
                              skipped=0,cancelled=0,other_status=0,valid_done_duration_count=2,
                              done_p50_seconds=2.,done_p95_seconds=3.,running_max_age_seconds=5.)]}
        self.assertEqual(m.clean_spans(row)['recent_rows'],3)
        for key,val in [('category','PRIVATE_DOCUMENT'),('done_p50_seconds',float('nan'))]:
            bad = json.loads(json.dumps(row)); bad['groups'][0][key] = val
            with self.assertRaises(m.obs.CheckFailure):
                m.clean_spans(bad)

    def test_import_python37_and_diagnostic_has_no_mutator_calls(self):
        tree = ast.parse(Path(m.__file__).read_text(encoding='utf-8'), feature_version=(3,7))
        calls = {n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
        self.assertFalse(calls & {'mutate','patch_call','wait_drain','prepare','kill','send_signal'})
        self.assertEqual((m.SAMPLES-1)*m.INTERVAL,180)

    def test_run_releases_lock_and_stores_safe_report_after_failure(self):
        with patch.object(m,'host_gate'), patch.object(m,'read_lock',return_value=9), \
             patch.object(m.obs,'safe_parent'), patch.object(m.tempfile,'mkdtemp',return_value='/var/lib/test'), \
             patch.object(Path,'chmod'), patch.object(m,'collect',side_effect=RuntimeError('SECRET')), \
             patch.object(m.obs,'write_new',return_value=b'{}') as write, \
             patch.object(m.obs,'emit') as emit, patch.object(m.os,'close') as close:
            self.assertEqual(m.main(),1)
            close.assert_called_once_with(9)
            self.assertNotIn('SECRET',str(write.call_args)+str(emit.call_args_list))


if __name__ == '__main__':
    unittest.main()

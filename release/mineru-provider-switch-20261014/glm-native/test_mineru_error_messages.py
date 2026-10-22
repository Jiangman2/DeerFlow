import ast
import json
from pathlib import Path
import types
import unittest
from unittest.mock import patch

import diagnose_glm104_failures as f
import diagnose_glm104_mineru as d
import diagnose_mineru_error_messages as m


class MessageTests(unittest.TestCase):
    def decode(self, value):
        return m.decode_detail(value, f)

    def test_python37(self):
        ast.parse(Path(m.__file__).read_text(encoding='utf-8'), feature_version=(3,7))

    def test_unicode_json(self):
        value = self.decode(json.dumps(dict(code=400,msg='当前请求超过最大并发数量，请稍后重试')))
        self.assertTrue(value['unicode_escape_seen'])
        self.assertEqual(value['body_format'], 'JSON')
        self.assertIn('并发数量', value['message_patterns'][0])
        self.assertEqual(value['business_codes'], ['400'])

    def test_already_decoded(self):
        value = self.decode('{"code":400,"msg":"请求过于频繁"}')
        self.assertFalse(value['unicode_escape_seen'])
        self.assertEqual(value['message_patterns'], ['请求过于频繁'])

    def test_outer_json_string(self):
        value = self.decode(json.dumps(json.dumps(dict(code=400,msg='请求频繁'))))
        self.assertEqual(value['outer_string_decodes'], 1)
        self.assertEqual(value['message_patterns'], ['请求频繁'])

    def test_nested_message(self):
        value = self.decode('{"error":{"code":"400","message":"too many tasks"},"secret":"NEVER_EXPORT"}')
        self.assertEqual(value['message_patterns'], ['too many tasks'])
        self.assertNotIn('secret', json.dumps(value))

    def test_validation_list(self):
        value = self.decode('{"detail":[{"loc":["body","SECRET"],"msg":"field required"}]}')
        self.assertEqual(value['message_patterns'], ['field required'])
        self.assertNotIn('SECRET', json.dumps(value))

    def test_json_cut_mid_unicode(self):
        value = self.decode('{"code":400,"msg":"\\u8bf7\\u6c42\\u9891\\u7e41\\u12')
        self.assertEqual(value['body_format'], 'JSON_PREFIX')
        self.assertEqual(value['message_patterns'], ['请求频繁'])

    def test_full_field_before_truncated_other_field(self):
        value = self.decode('{"code":400,"msg":"too many requests","data":{"secret":"XX')
        self.assertEqual(value['message_patterns'], ['too many requests'])
        self.assertNotIn('secret', json.dumps(value))

    def test_300_character_warning(self):
        self.assertTrue(self.decode('x'*300)['adapter_preview_may_be_truncated'])
        self.assertFalse(self.decode('x'*299)['adapter_preview_may_be_truncated'])

    def test_no_unknown_text_exported(self):
        value = self.decode(json.dumps(dict(msg='UNIQUE_SECRET private-customer.docx https://private/a?token=X 123456 北京客户名称 not found')))
        text = json.dumps(value, ensure_ascii=False)
        for secret in ('UNIQUE_SECRET','private-customer','https','北京客户名称','123456'):
            self.assertNotIn(secret,text)
        self.assertIn('not found',text)

    def test_credential_shape_redaction(self):
        for text in ('token=capacity Bearer invalid', 'api_key="file missing"',
                     'password=quota', 'authorization: denied'):
            with self.subTest(text=text):
                self.assertEqual(m.project(text), '[REDACTED]')

    def test_filename_and_quotes(self):
        self.assertEqual(m.project('invalid.pdf "quota exceeded"'), '[REDACTED]')

    def test_no_substring_of_secret(self):
        self.assertEqual(m.project('XXconcurrencyYY secretlimit123'), '[REDACTED]')

    def test_fixed_vocabulary_only(self):
        text = m.project('VULNERABLE x509: Certificate unknown output /data/files 4492 123.4.5.6')
        for token in text.split():
            self.assertIn(token, m.WORDS | {'[REDACTED]'})

    def test_unknown_field_and_code(self):
        value = self.decode('{"code":"CREDENTIAL_VALUE","customer":"private","secret":42}')
        self.assertEqual(value['business_codes'], [])
        self.assertEqual(value['message_patterns'], [])
        self.assertEqual(value['known_field_names'], ['code'])

    def test_boolean_is_not_business_code(self):
        self.assertEqual(self.decode('{"code":true,"msg":"error"}')['business_codes'], [])

    def test_html502(self):
        value = self.decode('<html><title>502 Bad Gateway</title><body>PRIVATE</body></html>')
        self.assertEqual(value['reason_hints'], ['UPSTREAM_UNAVAILABLE'])
        self.assertNotIn('PRIVATE', json.dumps(value))
        self.assertIn('bad gateway', value['message_patterns'][0])

    def test_unclosed_invalid_escape_does_not_execute(self):
        value = self.decode('{"msg":"error\\qSECRET"}')
        self.assertEqual(value['message_patterns'], ['error'])

    def line(self, detail, code=400, operation='asynchronous submission'):
        return '2026-10-17T08:00:00Z WARN Enterprise MinerU HTTP %d during %s: %s\n'%(code,operation,detail)

    def test_aggregate(self):
        raw=(self.line('{"code":400,"msg":"请求过于频繁"}')*3+self.line('Bad Gateway',502,'task status')).encode()
        value=m.aggregate(raw,d,f)
        self.assertEqual(value['http400_502_marker_lines'],4)
        self.assertEqual(len(value['groups']),2)
        self.assertEqual(value['groups'][0]['marker_lines'],3)
        self.assertFalse(value['coverage']['complete_history_proven'])
        self.assertEqual(value['coverage']['first_retained_utc'],'2026-10-17T08:00:00+00:00')

    def test_unrelated_logs_not_classified(self):
        value=m.aggregate((self.line('error',429)+'ERROR Deepseek PRIVATE\n').encode(),d,f)
        self.assertEqual(value['http400_502_marker_lines'],0)
        self.assertEqual(value['groups'],[])

    def test_pattern_bound_and_omitted(self):
        raw=''.join(self.line('{"code":%d,"msg":"error"}'%n) for n in range(280)).encode()
        value=m.aggregate(raw,d,f)
        self.assertEqual(len(value['groups']),24)
        self.assertEqual(value['omitted_marker_lines'],256)

    def test_limits_are_reported(self):
        with patch.object(m,'LIMIT',4),patch.object(m,'TAIL',1):
            value=m.aggregate(b'junk',d,f)
        self.assertTrue(value['coverage']['byte_limit_reached'])
        self.assertTrue(value['coverage']['line_limit_reached'])

    def test_one_read_only_logs_command(self):
        calls=[]
        obs=types.SimpleNamespace(container=lambda p:{'name':'worker'},call=lambda a,**kw:calls.append((a,kw)) or b'')
        value=m.collect_one(obs,d,f,{'metadata':{'name':'worker'}})
        self.assertEqual(len(calls),1)
        self.assertEqual(calls[0][0][0],'logs')
        self.assertIn('--limit-bytes=2097152',calls[0][0])
        self.assertEqual(value['http400_502_marker_lines'],0)

    def test_read_failure_no_retry_no_raw(self):
        calls=[]
        def fail(*a,**kw): calls.append(1); raise RuntimeError('SECRET')
        obs=types.SimpleNamespace(container=lambda p:{'name':'worker'},call=fail)
        value=m.collect_one(obs,d,f,{'metadata':{'name':'worker'}})
        self.assertEqual(calls,[1]); self.assertIn('unavailable',value)
        self.assertNotIn('SECRET',json.dumps(value))

    def collect_fixture(self, signature='same', unavailable=False):
        pods={'workers':[{'metadata':{'name':'w%d'%n}} for n in range(16)]}
        diagnostic=types.SimpleNamespace(inventory=unittest.mock.Mock(side_effect=[(pods,'same'),(pods,signature)]))
        obs=types.SimpleNamespace(WORKERS=['workers'])
        rows=[]
        with patch.object(m,'collect_one',return_value={'unavailable':{}} if unavailable else {'http400_502_marker_lines':2}), \
             patch.object(m,'emit',side_effect=rows.append):
            code=m.collect(diagnostic,obs,f)
        return code,rows

    def test_complete_not_recovery(self):
        code,rows=self.collect_fixture()
        self.assertEqual(code,0); self.assertEqual(rows[-1]['matching_marker_lines'],32)
        for field in ('production_writes','database_queries','pod_exec','provider_requests','health_requests','signals_sent'):
            self.assertEqual(rows[-1][field],0)
        self.assertTrue(rows[-1]['collection_is_not_recovery_or_expansion_approval'])

    def test_drift_incomplete(self):
        code,rows=self.collect_fixture(signature='changed')
        self.assertEqual(code,1); self.assertEqual(rows[-1]['message_diagnostic'],'INCOMPLETE')

    def test_missing_read_incomplete(self):
        code,rows=self.collect_fixture(unavailable=True)
        self.assertEqual(code,1); self.assertEqual(rows[-1]['unavailable_reads'],16)

    def test_changed_inventory_aborts(self):
        diagnostic=types.SimpleNamespace(inventory=lambda obs:({'workers':[]},'same'))
        with self.assertRaises(m.DiagnosticFailure):
            m.collect(diagnostic,types.SimpleNamespace(WORKERS=['workers']),f)


if __name__=='__main__': unittest.main()

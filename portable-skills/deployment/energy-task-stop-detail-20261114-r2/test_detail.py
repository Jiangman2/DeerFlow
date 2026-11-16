import asyncio
import datetime
import importlib.util
import json
from pathlib import Path
import unittest

HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('stop_seal',HERE/'seal.py');seal=importlib.util.module_from_spec(s);s.loader.exec_module(seal)
HOST,CHILD=seal.build();c={'__name__':'child_test'};exec(compile(CHILD,'<child>','exec'),c);h={'__name__':'host_test'};exec(compile(HOST,'<host>','exec'),h)
NOW=datetime.datetime.fromisoformat('2026-11-14T04:48:02.662332+00:00')

def rows():
    results=[]
    for seq in c['SEQUENCES']:
        if seq==10:kind='llm.tool.result';obj={'name':'bash','content':json.dumps({'status':'NEWS_SUPPLEMENT_REQUIRED','market_news_candidates':2,'company_news_candidates':4,'prepare_seconds':14.1,'publication_date':'2026-11-14','data_date':'2026-11-13','next_command':'python daily.py build --workspace /private/task'})}
        elif seq==20:kind='llm.ai.response';obj={'content':'由于新闻不足，需要补充原文；尚未构建。','tool_calls':[],'additional_kwargs':{'reasoning_content':'HIDDEN_SHOULD_NOT_EXPORT'}}
        elif seq in (11,16,18):kind='llm.ai.response';obj={'tool_calls':[{'name':'read_file','id':str(seq),'args':{'path':'/mnt/skills/public/energy-market-daily/references/evidence.md'}}]}
        else:kind='llm.tool.result';obj={'name':'read_file','status':'success','tool_call_id':str(seq-1),'content':'保存宿主真实抓到的原页；不使用搜索摘要冒充原文。'}
        raw=json.dumps(obj,ensure_ascii=False);results.append({'seq':seq,'event_type':kind,'created_at':NOW,'bytes':len(raw.encode()),'content':raw})
    return results

class Tx:
    async def __aenter__(self):return self
    async def __aexit__(self,*a):return False
class DB:
    def __init__(self):self.calls=[]
    def transaction(self,**kwargs):assert kwargs.get('readonly') is True;return Tx()
    async def fetchval(self,q):assert q=='SHOW transaction_read_only';return 'on'
    async def execute(self,q):assert q.startswith('SET LOCAL ')
    async def fetch(self,q,*args):
        self.calls.append((q,args))
        if q==c['RUN_DETAIL_SQL']:return [{'run_id':'synthetic-run','status':'success','created_at':NOW,'updated_at':NOW}]
        if q==c['DETAIL_SQL']:return rows()
        raise AssertionError(q)

class Tests(unittest.TestCase):
    def test_only_one_fixed_target(self):self.assertEqual(c['THREADS'],(seal.TARGET,));self.assertEqual(h['THREADS'],[seal.TARGET])
    def test_sealed_asset(self):self.assertEqual(h['assets']().encode(),CHILD)
    def test_no_wide_db_queries(self):
        self.assertNotIn('COUNT_SQL',CHILD.decode());self.assertNotIn('FROM checkpoints',CHILD.decode());self.assertNotIn('FROM scheduled_tasks',CHILD.decode())
    def test_exact_eight_event_scope(self):
        self.assertIn('seq=ANY($3::bigint[])',c['DETAIL_SQL']);self.assertIn('LIMIT 8',c['DETAIL_SQL'])
        self.assertEqual(c['SEQUENCES'],[10,11,12,16,17,18,19,20])
    def test_terminal_prose_but_not_reasoning(self):
        out=c['details'](rows());text=json.dumps(out,ensure_ascii=False);self.assertIn('由于新闻不足',text);self.assertNotIn('HIDDEN_SHOULD_NOT_EXPORT',text);self.assertNotIn('/private/task',text)
    def test_exact_authority_read_equality(self):
        out=c['details'](rows());self.assertTrue(out['authority_three_result_contents_identical']);self.assertTrue(out['all_authority_results_success'])
        changed=rows();changed[4]['content']=json.dumps({'name':'read_file','status':'error','content':'Error: blocked recovery'},ensure_ascii=False);out=c['details'](changed);self.assertFalse(out['authority_three_result_contents_identical']);self.assertFalse(out['all_authority_results_success'])
    def test_redacts_urls_keys_paths_tokens_code(self):
        source='停止原因清楚。https://private.invalid/webhook/SECRET_VALUE\nJKX_API_PASSWORD=secret-value\nAuthorization: Bearer abc.def.ghi\n路径 /mnt/user-data/workspace/a.json\n```python\nprint(secret)\n```\n'+'a'*64
        out=json.dumps(c['redact_terminal'](source),ensure_ascii=False)
        for s in ('private.invalid','SECRET_VALUE','secret-value','abc.def.ghi','/mnt/user-data','print(secret)','a'*64):self.assertNotIn(s,out)
        self.assertIn('停止原因清楚',out)
    def test_terminal_bound(self):self.assertEqual(len(c['redact_terminal']('中文。'*3000)['redacted_user_visible_terminal_text']),4000)
    def test_content_reasoning_block_not_exported(self):
        result=c['redact_terminal']([{'type':'reasoning','text':'HIDDEN_SECRET_VALUE'},{'type':'text','text':'任务已停止。'}]);text=json.dumps(result,ensure_ascii=False)
        self.assertNotIn('HIDDEN_SECRET_VALUE',text);self.assertIn('任务已停止',text)
    def test_chinese_secret_assignment_redacted(self):
        self.assertNotIn('SECRET_VALUE',json.dumps(c['redact_terminal']('密码是SECRET_VALUE，未发送。'),ensure_ascii=False))
    def test_prepare_counts_only(self):
        row=c['details'](rows())['events'][0]['prepare']['records'][0];self.assertEqual(row['market_news_candidates'],2);self.assertEqual(row['company_news_candidates'],4);self.assertEqual(row['next_action_structure']['actions'],['build'])
    def test_unknown_run_stops_before_events(self):
        db=DB()
        with self.assertRaisesRegex(c['Refused'],'FIXED_COMPLETED_RUN_REQUIRED'):asyncio.run(c['diagnose_thread'](db,seal.TARGET))
        self.assertEqual(len(db.calls),1)
    def test_successful_fixed_run_only_two_selects(self):
        db=DB();old=c['RUN_SHA'];c['RUN_SHA']=c['sha']('synthetic-run')
        try:out=asyncio.run(c['diagnose_thread'](db,seal.TARGET))
        finally:c['RUN_SHA']=old
        self.assertEqual(len(db.calls),2);self.assertEqual(out['status'],'TASK_DURABLE_STATE_OBSERVED_READONLY')
    def test_event_set_drift_refused(self):
        with self.assertRaisesRegex(c['Refused'],'EXACT_EVENT_SET_REQUIRED'):c['details'](rows()[:-1])
    def test_mutation_host_denied(self):
        op=h['Operation']('child')
        with self.assertRaises(h['Refused']):op.command(['patch','deployment','gateway'])
        self.assertEqual(op.queries,0)
    def test_original_inspector_immutable_reference(self):
        original=HERE.parents[2]/'release/energy-two-task-inspection-20261114-r1/inspect-energy-two-tasks-20261114-r1.py'
        import hashlib
        self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(),'c08de2e49c13a8c12bf1b51479eec593da1e3e11ed2aff9dd9e2036bceb47808')

if __name__=='__main__':unittest.main()

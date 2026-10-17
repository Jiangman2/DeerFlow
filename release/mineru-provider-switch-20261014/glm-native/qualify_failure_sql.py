"""LOCAL ONLY: execute diagnostic SQL in one disposable, networkless PG16."""
import json
import subprocess
import sys
import diagnose_glm104_failures as f


def main():
    cid=sys.argv[1]
    obj=json.loads(subprocess.check_output(['docker','inspect',cid]))[0]
    assert obj['Config']['Labels'].get('codex.task')=='glm104-failure-sql-test-20261017'
    assert obj['HostConfig']['NetworkMode']=='none' and not obj['HostConfig']['PortBindings']
    assert not obj['HostConfig']['Binds']
    def sql(s):
        p=subprocess.run(['docker','exec','-i',cid,'psql','-X','-qAt','-U','postgres',
                          '-v','ON_ERROR_STOP=1'],input=s.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
        if p.returncode: raise RuntimeError(p.stderr.decode())  # Synthetic local data only.
        return p.stdout.decode().strip()
    sql("""CREATE TABLE knowledge_processing_spans(id bigserial PRIMARY KEY,knowledge_id varchar,
 name varchar,kind varchar,status varchar,error_code varchar,error_message text,error_detail text,
 started_at timestamptz,finished_at timestamptz);
 CREATE INDEX idx_test_status_started ON knowledge_processing_spans(status,started_at);
 INSERT INTO knowledge_processing_spans(knowledge_id,name,kind,status,started_at)
 SELECT 'synthetic-'||g,'docreader','stage','done',TIMESTAMPTZ '2026-10-17T08:00:00Z' FROM generate_series(1,30000) g;
 INSERT INTO knowledge_processing_spans(knowledge_id,name,kind,status,error_code,error_message,error_detail,started_at,finished_at) VALUES
 ('private-document-a','docreader','stage','failed','DOCREADER_PARSE_FAILED','MinerU HTTP 400','field required SECRET', '2026-10-17T08:01:00Z','2026-10-17T08:03:00Z'),
 ('private-document-a','parse','root','failed','DOCREADER_PARSE_FAILED','MinerU HTTP 400','field required SECRET', '2026-10-17T08:01:00Z','2026-10-17T08:03:00Z'),
 ('private-document-b','postprocess.question.batch[1]','generation','failed','QUESTION_FAILED','DeepSeek: service unavailable','SECRET', '2026-10-17T08:02:00Z','2026-10-17T08:04:00Z'),
 ('private-document-c','multimodal.image[0]','subspan','failed','MULTIMODAL_VLM_FAILED','enterprise GLM: HTTP status 503','SECRET','2026-10-17T08:03:00Z','2026-10-17T08:06:00Z'),
 ('private-document-d','postprocess.summary','generation','failed','SUMMARY_FAILED','context deadline exceeded','SECRET','2026-10-17T08:04:00Z','2026-10-17T08:08:00Z'),
 ('private-document-e','embedding','stage','cancelled','UPSTREAM_FAILED','SECRET','SECRET','2026-10-17T08:04:00Z',now()),
 ('private-document-f','SECRET','subspan','failed','SECRET','SECRET','SECRET','2026-10-17T08:04:00Z','2026-10-17T08:09:00Z'),
 ('private-document-old','docreader','stage','failed','UNKNOWN','SECRET','SECRET','2026-10-16T08:00:00Z',now());
 ANALYZE knowledge_processing_spans;
 """)
    schema=json.loads(sql(f.SCHEMA_SQL)); assert schema['read_only']=='on' and all(schema[k] for k in ('columns_match','ordinary_table','failure_index'))
    plan=f.check_plan(json.loads(sql(f.TX+'EXPLAIN (FORMAT JSON) '+f.SAMPLE_SQL+'; ROLLBACK;')))
    raw=sql(f.aggregate_sql()); result=f.clean_database(json.loads(raw))
    assert result['sampled_failed_spans']==6 and result['documents_with_sampled_failed_spans']==5
    assert 'SECRET' not in raw and 'private-document' not in raw
    groups={g['stage']:g for g in result['groups']}
    assert groups['postprocess.question']['provider_hint']=='DEEPSEEK_MENTIONED'
    assert groups['postprocess.summary']['provider_hint']=='NOT_IDENTIFIED'
    assert groups['multimodal.image']['provider_hint']=='GLM_MENTIONED'
    assert groups['docreader']['reason_hint']=='REQUEST_VALIDATION'
    assert groups['other']['code']=='OTHER'
    # Time filter, cap and read-only enforcement are exercised, not just parsed.
    sql("INSERT INTO knowledge_processing_spans(knowledge_id,name,kind,status,started_at) SELECT 'synthetic-more','docreader','stage','failed',TIMESTAMPTZ '2026-10-17T08:10:00Z' FROM generate_series(1,2100)")
    cap=f.clean_database(json.loads(sql(f.aggregate_sql())))
    assert cap['sample_cap_reached'] and cap['sampled_failed_spans']==2000
    print(json.dumps(dict(synthetic_pg16='PASS',index_plan=plan,redaction='PASS',sample_cap='PASS',
        stage_classification='PASS',provider_non_inference='PASS',production_connections=0)))


if __name__=='__main__': main()

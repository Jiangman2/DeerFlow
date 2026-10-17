#!/usr/bin/env python3
"""Bounded, content-free failure classification; Python 3.7+, no mutations."""
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import sys
import types
from concurrent.futures import ThreadPoolExecutor

PRIOR = Path('/var/lib/weknora-glm104-diagnostic-tool-7whC6f8S/check.py')
PRIOR_SHA = '9756acd719f1fc78e84afbda621bdb0af32bbb15334d08ca0d197bb076dc5c74'
SINCE = '2026-10-17T07:45:00Z'
DB_SINCE = '2026-10-17T07:00:00Z'
LIMIT = 2097152
TAIL = 15000
SAMPLE = 2000
TX = "BEGIN READ ONLY; SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='1s'; "
# The first matching phrase is a hint, never a root-cause verdict. All output
# labels are constants. Neither body excerpts nor hashes of documents are sent.
RULES = (
 ('AUTHENTICATION_OR_PERMISSION', r'unauthorized|forbidden|invalid api key|invalid token|token expired|authentication failed|鉴权失败|无权限|令牌过期'),
 ('RATE_OR_QUOTA', r'rate limit|too many requests|quota exceeded|concurrency limit|限流|配额|并发限制'),
 ('UPSTREAM_UNAVAILABLE', r'no available (backend|server|instance)|no healthy upstream|service unavailable|bad gateway|upstream connect|connection refused|服务不可用|服务异常|无可用'),
 ('DEADLINE_OR_TIMEOUT', r'timed out|timeout|deadline exceeded|超时'),
 ('TASK_NOT_FOUND', r'task.{0,80}(not found|does not exist|不存在)|invalid task.{0,20}id'),
 ('INPUT_TOO_LARGE', r'context length|maximum context|too many tokens|payload too large|file too large|exceeds.{0,30}(size|limit)|超出.{0,20}(长度|大小)|文件过大'),
 ('FILE_OR_FORMAT_REJECTED', r'unsupported.{0,30}(file|format)|invalid.{0,30}(pdf|file|image)|corrupt|encrypted|password.protected|文件损坏|格式不支持|不支持.{0,10}格式'),
 ('REQUEST_VALIDATION', r'validation error|field required|missing required|invalid parameter|unprocessable entity|参数错误|参数校验|参数不合法'),
 ('EMPTY_OR_INVALID_RESPONSE', r'choices.{0,5}null|empty response|invalid json|non.json|empty completion|incomplete or empty completion'),
 ('TLS_OR_DNS', r'certificate_verify_failed|certificate verify failed|x509:|no such host|name resolution|证书'),
 ('HTTP_5XX', r'(http|status|status code|response code|error, status code)[: =]+5[0-9]{2}'),
 ('HTTP_4XX', r'(http|status|status code|response code|error, status code)[: =]+4[0-9]{2}'),
)
REASONS = tuple(x[0] for x in RULES) + ('UNCLASSIFIED',)
HINTS = ('DEEPSEEK_MENTIONED', 'GLM_MENTIONED', 'MINERU_MENTIONED', 'MULTIPLE_MENTIONED', 'NOT_IDENTIFIED')
STAGES = ('root', 'docreader', 'chunking', 'embedding', 'multimodal', 'postprocess',
          'multimodal.image', 'embedding.batch', 'postprocess.question',
          'postprocess.summary', 'postprocess.graph', 'postprocess.wiki', 'other')
KINDS = ('root', 'stage', 'subspan', 'generation', 'other')
CODES = ('DOCREADER_TIMEOUT', 'DOCREADER_UNAVAILABLE', 'DOCREADER_PARSE_FAILED',
 'CHUNKING_FAILED', 'EMBEDDING_RATE_LIMIT', 'EMBEDDING_PROVIDER_FAIL',
 'VECTORSTORE_WRITE_FAILED', 'MULTIMODAL_VLM_FAILED', 'MULTIMODAL_ALL_FAILED',
 'TASK_TIMEOUT', 'UNKNOWN', 'UPSTREAM_FAILED', 'QUESTION_GENERATION_FAILED',
 'SUMMARY_GENERATION_FAILED', 'QUESTION_FAILED', 'SUMMARY_FAILED', 'GRAPH_EXTRACT_FAILED', 'OTHER')


class CheckFailure(RuntimeError):
    pass


def need(ok, code):
    if not ok:
        raise CheckFailure(code)


def emit(row):
    print(json.dumps(row, ensure_ascii=True, sort_keys=True, allow_nan=False), flush=True)


def safe_error(exc):
    return dict(error_type=type(exc).__name__, check=str(exc) if isinstance(exc, CheckFailure)
                else 'READ_UNAVAILABLE', raw_error_printed=False)


def load_helpers():
    for p in (PRIOR.parent,) + tuple(PRIOR.parent.parents):
        s = p.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'unsafe_helper_parent')
    fd = os.open(str(PRIOR), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
             and not s.st_mode & 0o022 and s.st_size <= 1048576, 'unsafe_helper')
        raw = f.read(1048577)
    need(hashlib.sha256(raw).hexdigest() == PRIOR_SHA, 'helper_hash_changed')
    d = types.ModuleType('frozen_failure_diagnostic'); d.__file__ = str(PRIOR)
    exec(compile(raw, str(PRIOR), 'exec'), d.__dict__)
    d.bind_receipt()
    sw = types.ModuleType('frozen_failure_switch'); sw.__file__ = str(d.TOOL)
    exec(compile(d.safe_read(d.TOOL, d.TOOL_SHA), str(d.TOOL), 'exec'), sw.__dict__)
    chain = sw.load_review()
    return d, chain.o.r.obs, chain.o.r.e.review


def classify(text):
    return next((label for label, pattern in RULES if re.search(pattern, text, re.I)), 'UNCLASSIFIED')


def hint(text):
    found = [tag for word, tag in (('deepseek', 'DEEPSEEK_MENTIONED'),
        ('glm', 'GLM_MENTIONED'), ('mineru', 'MINERU_MENTIONED')) if word in text.lower()]
    return 'MULTIPLE_MENTIONED' if len(found) > 1 else (found[0] if found else 'NOT_IDENTIFIED')


def business_code(detail):
    # Numeric codes only, explicitly labelled; never emit arbitrary error fields.
    m = re.search(r'(?:["\']?(?:code|error_code)["\']?\s*[:=]\s*["\']?)(-?[0-9]{1,8})(?=["\'\s,;}])', detail)
    return m[1] if m else None


def summarize_logs(raw, d, role):
    lines = raw.decode('utf-8', 'replace').splitlines()
    times = [t for t in (d.stamp(line) for line in lines) if t]
    groups = {}
    for line in lines:
        match = re.search(r'Enterprise MinerU HTTP ([1-5][0-9]{2}) during '
                          + d.OPERATIONS + r':\s*(.*)$', line)
        if role == 'worker':
            if not match:
                continue
            status, operation, detail = match.groups()
            detail = detail[:2048]
            provider = 'MINERU_MENTIONED'
        else:
            # Do not classify ordinary prompts or information lines as failures.
            if not re.search(r'\b(ERROR|WARN|error|warning)\b|\bfailed\b|\bfailure\b', line):
                continue
            status, operation, detail = None, 'app_error_line', line[:8192]
            provider = hint(detail)
        key = (status, operation, classify(detail), business_code(detail), provider)
        row = groups.setdefault(key, dict(http_status=status, operation=operation, reason_hint=key[2],
            business_code=key[3], provider_hint=provider, marker_lines=0, first_utc=d.stamp(line), last_utc=None))
        row['marker_lines'] += 1; row['last_utc'] = d.stamp(line)
    ordered = sorted(groups.values(), key=lambda x: -x['marker_lines'])
    return dict(coverage=dict(requested_since_utc=SINCE, first_utc=times[0] if times else None,
        last_utc=times[-1] if times else None, bytes_read=len(raw), lines_read=len(lines),
        byte_limit_reached=len(raw) >= LIMIT, line_limit_reached=len(lines) >= TAIL,
        complete_history_proven=False), groups=ordered[:60], omitted_groups=max(0, len(ordered)-60),
        omitted_marker_lines=sum(x['marker_lines'] for x in ordered[60:]),
        marker_counts_are_not_unique_documents=True, classifications_are_hints=True,
        provider_mention_is_not_causal_attribution=True, raw_logs_saved=False)


def log_read(obs, d, pod, role):
    row = dict(pod=pod['metadata']['name'], role=role)
    try:
        raw = obs.call(['logs', pod['metadata']['name'], '-c', obs.container(pod)['name'],
            '--since-time='+SINCE, '--timestamps=true', '--tail='+str(TAIL),
            '--limit-bytes='+str(LIMIT)], timeout=25, limit=LIMIT+4096)
        row.update(summarize_logs(raw, d, role))
    except Exception as exc:
        row['unavailable'] = safe_error(exc)
    return row


def quote(value):
    return "'" + value.replace("'", "''") + "'"


SAMPLE_SQL = """SELECT id,knowledge_id,name,kind,error_code,started_at,finished_at,
 left(coalesce(error_message,''),1024)||' '||left(coalesce(error_detail,''),2048) AS err
 FROM public.knowledge_processing_spans
 WHERE status='failed' AND started_at >= TIMESTAMPTZ '%s'
 ORDER BY started_at DESC LIMIT %d""" % (DB_SINCE, SAMPLE)

SCHEMA_SQL = TX + """
WITH wanted(attname,typname) AS (VALUES ('id','int8'),('knowledge_id','varchar'),
 ('name','varchar'),('kind','varchar'),('status','varchar'),('error_code','varchar'),
 ('error_message','text'),('error_detail','text'),('started_at','timestamptz'),('finished_at','timestamptz'))
SELECT json_build_object('read_only',current_setting('transaction_read_only'),
 'columns_match',(SELECT count(*)=10 FROM wanted w JOIN pg_attribute a ON a.attname=w.attname
 JOIN pg_type t ON t.oid=a.atttypid AND t.typname=w.typname
 WHERE a.attrelid=to_regclass('public.knowledge_processing_spans') AND NOT a.attisdropped),
 'ordinary_table',exists(SELECT 1 FROM pg_class WHERE oid=to_regclass('public.knowledge_processing_spans') AND relkind='r'),
 'failure_index',exists(SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
 JOIN pg_am m ON m.oid=c.relam
 JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=i.indkey[0]
 JOIN pg_attribute b ON b.attrelid=i.indrelid AND b.attnum=i.indkey[1]
 WHERE i.indrelid=to_regclass('public.knowledge_processing_spans') AND i.indisvalid AND i.indisready
 AND i.indpred IS NULL AND m.amname='btree' AND a.attname='status' AND b.attname='started_at'));
ROLLBACK;"""


def stage_case():
    return "CASE WHEN kind='root' THEN 'root' WHEN name IN (%s) THEN name %s ELSE 'other' END" % (
        ','.join(quote(x) for x in STAGES[1:6]), ' '.join(
            "WHEN name ~ %s THEN %s" % (quote('^'+x.replace('.', '[.]')+r'([.\[]|$)'), quote(x))
            for x in STAGES[6:-1]))


def aggregate_sql():
    reason = 'CASE ' + ' '.join('WHEN err ~* %s THEN %s' % (quote(p), quote(k)) for k,p in RULES) + " ELSE 'UNCLASSIFIED' END"
    # Model-name presence only. Do not infer a provider from its processing stage.
    provider = """CASE WHEN ((err ILIKE '%deepseek%')::int+(err ILIKE '%glm%')::int+(err ILIKE '%mineru%')::int)>1 THEN 'MULTIPLE_MENTIONED'
     WHEN err ILIKE '%deepseek%' THEN 'DEEPSEEK_MENTIONED' WHEN err ILIKE '%glm%' THEN 'GLM_MENTIONED'
     WHEN err ILIKE '%mineru%' THEN 'MINERU_MENTIONED' ELSE 'NOT_IDENTIFIED' END"""
    return TX + """
WITH recent AS MATERIALIZED (%s), chosen AS MATERIALIZED (
 SELECT knowledge_id, started_at,finished_at,%s AS stage,
 CASE WHEN kind IN ('root','stage','subspan','generation') THEN kind ELSE 'other' END AS kind,
 CASE WHEN error_code IN (%s) THEN error_code ELSE 'OTHER' END AS code,
 %s AS reason_hint,%s AS provider_hint FROM recent
), grouped AS (
 SELECT stage,kind,code,reason_hint,provider_hint,count(*) AS failed_spans,
 count(DISTINCT knowledge_id) AS documents_with_sampled_failed_spans,
 extract(epoch FROM min(finished_at)) AS first_finished_epoch,
 extract(epoch FROM max(finished_at)) AS last_finished_epoch
 FROM chosen GROUP BY stage,kind,code,reason_hint,provider_hint
), buckets AS (
 SELECT floor(extract(epoch FROM finished_at)/300)*300 AS finished_bucket_epoch,
 count(*) AS failed_spans FROM chosen WHERE finished_at IS NOT NULL GROUP BY 1
), top_groups AS (SELECT * FROM grouped ORDER BY failed_spans DESC,stage,kind,code,reason_hint,provider_hint LIMIT 80),
 top_buckets AS (SELECT * FROM buckets ORDER BY finished_bucket_epoch DESC LIMIT 48)
SELECT json_build_object('read_only',current_setting('transaction_read_only'),
 'sample_limit',%d,'sampled_failed_spans',(SELECT count(*) FROM recent),
 'documents_with_sampled_failed_spans',(SELECT count(DISTINCT knowledge_id) FROM recent),
 'oldest_started_epoch',(SELECT extract(epoch FROM min(started_at)) FROM recent),
 'newest_started_epoch',(SELECT extract(epoch FROM max(started_at)) FROM recent),
 'sample_cap_reached',(SELECT count(*)=%d FROM recent),
 'omitted_groups',greatest(0,(SELECT count(*) FROM grouped)-80),
 'omitted_buckets',greatest(0,(SELECT count(*) FROM buckets)-48),
 'groups',coalesce((SELECT json_agg(top_groups) FROM top_groups),'[]'::json),
 'buckets',coalesce((SELECT json_agg(top_buckets) FROM top_buckets),'[]'::json)); ROLLBACK;
""" % (SAMPLE_SQL, stage_case(), ','.join(quote(x) for x in CODES), reason, provider, SAMPLE, SAMPLE)


def check_plan(plan):
    need(isinstance(plan, list) and len(plan)==1, 'explain_shape')
    nodes = [plan[0].get('Plan', {})]; kinds = []
    while nodes:
        p = nodes.pop(); k = p.get('Node Type'); kinds.append(k)
        need(k in ('Limit','Index Scan','Index Only Scan') and len(kinds)<=5, 'unbounded_failure_sample_plan')
        if k != 'Limit':
            need(p.get('Relation Name')=='knowledge_processing_spans', 'unexpected_plan_relation')
        nodes.extend(p.get('Plans', []))
    need('Limit' in kinds and any(k.startswith('Index') for k in kinds), 'indexed_limit_required')
    return kinds


def number(value, nullable=False):
    need((nullable and value is None) or (type(value) in (int,float) and math.isfinite(value)
         and 0<=value<=10**12), 'invalid_aggregate_number')
    return value


def clean_database(row):
    need(row.get('read_only')=='on' and row.get('sample_limit')==SAMPLE
         and type(row.get('sample_cap_reached')) is bool, 'database_response_shape')
    out = dict(scope='LATEST_2000_FAILED_SPANS_BY_START_TIME_NOT_ALL_FAILED_DOCUMENTS',
        started_since_utc=DB_SINCE, read_only='on', sample_limit=SAMPLE,
        sample_cap_reached=row['sample_cap_reached'], groups=[], buckets=[],
        cancelled_spans_excluded=True, final_document_status_not_read=True,
        provider_mention_is_not_causal_attribution=True, groups_are_not_additive_documents=True)
    for key in ('sampled_failed_spans','documents_with_sampled_failed_spans','omitted_groups','omitted_buckets'):
        out[key]=number(row.get(key)); need(type(out[key]) is int and out[key]<=SAMPLE, 'count_bound')
    need(out['documents_with_sampled_failed_spans']<=out['sampled_failed_spans'], 'document_count_bound')
    for key in ('oldest_started_epoch','newest_started_epoch'):
        out[key]=number(row.get(key), True)
    need(isinstance(row.get('groups'),list) and len(row['groups'])<=80, 'group_bound')
    for g in row['groups']:
        safe={}
        for key,allowed in (('stage',STAGES),('kind',KINDS),('code',CODES),('reason_hint',REASONS),('provider_hint',HINTS)):
            need(g.get(key) in allowed, 'unknown_aggregate_label'); safe[key]=g[key]
        for key in ('failed_spans','documents_with_sampled_failed_spans'):
            safe[key]=number(g.get(key)); need(type(safe[key]) is int and safe[key]<=SAMPLE, 'group_count_bound')
        for key in ('first_finished_epoch','last_finished_epoch'):
            safe[key]=number(g.get(key), True)
        out['groups'].append(safe)
    need(isinstance(row.get('buckets'),list) and len(row['buckets'])<=48, 'bucket_bound')
    for g in row['buckets']:
        out['buckets'].append({k:number(g.get(k)) for k in ('finished_bucket_epoch','failed_spans')})
    return out


def database_read(obs, review, app):
    pg, binding = review.pg_binding(app)
    def query(sql):
        return json.loads(obs.call(['exec',pg['metadata']['name'],'-c',obs.container(pg)['name'],
            '--','sh','-c',review.DB_SHELL,'failure-diagnostic',sql], timeout=20, limit=65536))
    s=query(SCHEMA_SQL)
    need(s.get('read_only')=='on' and all(s.get(k) is True for k in
         ('columns_match','ordinary_table','failure_index')), 'schema_or_index_unreviewed')
    plan=check_plan(query(TX+'EXPLAIN (FORMAT JSON) '+SAMPLE_SQL+'; ROLLBACK;'))
    result=clean_database(query(aggregate_sql()))
    _, after=review.pg_binding(app)
    need(after==binding, 'postgres_identity_changed')
    result['sample_plan_node_types']=plan
    return result


def collect(d, obs, review):
    pods, initial=d.inventory(obs)
    app=pods['weknora-app'][0]
    emit(dict(failure_diagnostic='RUNNING', phase='FAILED_SPAN_STAGE_AND_REASON_COUNTS', production_writes=0))
    unavailable=0
    try:
        emit(dict(database_failures=database_read(obs, review, app)))
    except Exception as exc:
        unavailable+=1; emit(dict(database_failures_unavailable=safe_error(exc)))
    emit(dict(failure_diagnostic='RUNNING', phase='BOUNDED_UPSTREAM_REASON_CLASSIFICATION', raw_logs_saved=False))
    targets=[(p,'worker') for n in obs.WORKERS for p in pods[n]]+[(app,'app')]
    with ThreadPoolExecutor(max_workers=3) as pool:
        for row in pool.map(lambda item:log_read(obs,d,item[0],item[1]), targets):
            unavailable+=int('unavailable' in row); emit(dict(error_reason_evidence=row))
    _, final=d.inventory(obs)
    stable=initial==final
    emit(dict(failure_diagnostic='COMPLETE' if stable and not unavailable else 'INCOMPLETE',
        baseline_stable=stable, unavailable_reads=unavailable, production_writes=0,
        provider_requests=0, documents_submitted=0, signals_sent=0, raw_logs_saved=False,
        automatic_retry=False, collection_is_not_recovery_or_expansion_approval=True))
    return 0 if stable and not unavailable else 1


def main():
    try:
        need(len(sys.argv)==1, 'no_arguments_supported')
        need(os.geteuid()==0 and socket.gethostname().split('.')[0]=='datax-01', 'wrong_host_or_identity')
        emit(dict(failure_diagnostic='RUNNING', phase='BIND_CURRENT104_READ_ONLY_HELPERS'))
        return collect(*load_helpers())
    except Exception as exc:
        emit(dict(failure_diagnostic='INCOMPLETE', failure=safe_error(exc), production_writes=0,
            provider_requests=0, signals_sent=0, automatic_retry=False))
        return 1


if __name__=='__main__':
    sys.exit(main())

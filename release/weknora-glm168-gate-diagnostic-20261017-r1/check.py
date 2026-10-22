#!/usr/bin/env python3
"""Short read-only diagnosis of failed168 gates. No expansion or server dry-run."""
import datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import time
import types

TOOL=Path('/var/lib/weknora-glm168-review-tool-SbrjoiV6/review.py')
TOOL_SHA='598a1870583051a5038e91fd8e36a772c9467cfa65132985d3d334565ca0d7b8'
REPORT=Path('/var/lib/weknora-glm168-review-md9qnn6g/RESULT.json')
REPORT_SHA='773f022727c01981071b16e3fd20d6e84cb8ca61f7227f079e0355186bba4a9b'
GIB=1024**3

GATE_SQL="""BEGIN READ ONLY;
SET LOCAL statement_timeout='5s'; SET LOCAL lock_timeout='1s';
WITH activity AS (SELECT pid,backend_type,datname,state,wait_event_type,wait_event,query_start,xact_start FROM pg_stat_activity),
 waiting AS (SELECT *,pg_blocking_pids(pid) AS blockers FROM activity
   WHERE datname=current_database() AND state='active' AND wait_event_type='Lock'),
 blocker_roles AS (SELECT CASE WHEN a.backend_type IN ('client backend','autovacuum worker','parallel worker')
   THEN a.backend_type ELSE 'other' END AS backend_type,count(DISTINCT a.pid) AS records
   FROM activity a WHERE a.pid IN (SELECT unnest(blockers) FROM waiting) GROUP BY 1),
 waiter_roles AS (SELECT CASE WHEN backend_type IN ('client backend','autovacuum worker','parallel worker')
   THEN backend_type ELSE 'other' END AS backend_type,
   CASE WHEN wait_event IN ('relation','transactionid','tuple','extend','advisory','virtualxid','object','spectoken')
   THEN wait_event ELSE 'OTHER' END AS wait_event,count(*) AS records,
   max(greatest(0,extract(epoch FROM now()-query_start))) AS max_query_age_seconds
   FROM waiting GROUP BY 1,2)
SELECT json_build_object('read_only',current_setting('transaction_read_only'),
 'max_connections',current_setting('max_connections')::int,
 'reserved_connections',current_setting('superuser_reserved_connections')::int,
 'client_backends',(SELECT count(*) FROM activity WHERE backend_type='client backend'),
 'database_client_backends',(SELECT count(*) FROM activity WHERE backend_type='client backend' AND datname=current_database()),
 'parallel_workers',(SELECT count(*) FROM activity WHERE backend_type='parallel worker' AND datname=current_database()),
 'idle_in_transaction',(SELECT count(*) FROM activity WHERE backend_type='client backend' AND datname=current_database() AND state='idle in transaction'),
 'idle_in_transaction_aborted',(SELECT count(*) FROM activity WHERE backend_type='client backend' AND datname=current_database() AND state='idle in transaction (aborted)'),
 'oldest_idle_transaction_seconds',(SELECT max(greatest(0,extract(epoch FROM now()-xact_start))) FROM activity
    WHERE datname=current_database() AND state IN ('idle in transaction','idle in transaction (aborted)')),
 'lock_waiters',(SELECT count(*) FROM waiting),
 'waiter_roles',(SELECT coalesce(json_agg(waiter_roles),'[]'::json) FROM waiter_roles),
 'blocker_roles',(SELECT coalesce(json_agg(blocker_roles),'[]'::json) FROM blocker_roles));
ROLLBACK;"""

MEMORY_CODE=r'''import json,os,re
def read(path,limit=65536):
 with open(path) as f:value=f.read(limit+1)
 if len(value)>limit:raise ValueError('bounded')
 return value
out={'process_memory_bytes':{},'memory_stat':{},'unavailable':[]}
for line in read('/proc/1/status').splitlines():
 m=re.fullmatch(r'(VmRSS|VmHWM|RssAnon|RssFile|RssShmem|VmSwap):\s+([0-9]+) kB',line)
 if m:out['process_memory_bytes'][m[1]]=int(m[2])*1024
groups={}
for line in read('/proc/self/cgroup').splitlines():
 parts=line.split(':',2)
 if len(parts)==3:
  for k in parts[1].split(','):groups[k]=parts[2]
root=None;kind=None
for line in read('/proc/self/mountinfo',262144).splitlines():
 fields=line.split();i=fields.index('-');fs=fields[i+1]
 if fs not in ('cgroup','cgroup2'):continue
 group=groups.get('memory') if fs=='cgroup' and 'memory' in fields[i+3].split(',') else groups.get('') if fs=='cgroup2' else None
 if group is None or not group.startswith('/'):continue
 base,mount=fields[3:5]
 if '\\' in base+mount or (mount!='/sys/fs/cgroup' and not mount.startswith('/sys/fs/cgroup/')):continue
 if group==base:relative=''
 elif base=='/':relative=group.lstrip('/')
 elif group.startswith(base.rstrip('/')+'/'):relative=group[len(base):].lstrip('/')
 else:continue
 if '..' in relative.split('/'):continue
 root=mount+('/'+relative if relative else '');kind=fs;break
if root is None:out['unavailable'].append('memory_controller_path')
else:
 wanted={'rss','cache','mapped_file','active_file','inactive_file','active_anon','inactive_anon','swap',
 'total_rss','total_cache','total_mapped_file','total_active_file','total_inactive_file','total_active_anon','total_inactive_anon','total_swap',
 'anon','file','shmem','file_mapped','kernel','slab','slab_reclaimable','slab_unreclaimable','kernel_stack','pagetables','sock'}
 for line in read(root+'/memory.stat').splitlines():
  parts=line.split()
  if len(parts)==2 and parts[0] in wanted and parts[1].isdigit():out['memory_stat'][parts[0]]=int(parts[1])
 out['controller']=kind
print(json.dumps(out))
'''


class Failure(RuntimeError):pass


def need(ok,label):
    if not ok:raise Failure(label)


def emit(value):print(json.dumps(value,sort_keys=True,allow_nan=False),flush=True)


def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()


def error(exc):
    label=str(exc) if isinstance(exc,RuntimeError) and re.fullmatch('[a-z0-9_]{1,120}',str(exc)) else 'read_unavailable'
    return dict(error_type=type(exc).__name__,check=label,raw_error_printed=False)


def safe_read(path,expected):
    for p in (path.parent,)+tuple(path.parent.parents):
        s=p.lstat();need(stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022,'unsafe_private_parent')
    fd=os.open(str(path),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    with os.fdopen(fd,'rb') as f:
        before=os.fstat(f.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_uid==0 and before.st_nlink==1 and not before.st_mode&0o022 and before.st_size<=4194304,'unsafe_private_file')
        raw=f.read(4194305);after=os.fstat(f.fileno())
    identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mode,s.st_uid,s.st_nlink,s.st_mtime_ns,s.st_ctime_ns)
    need(identity(before)==identity(after)==identity(path.lstat()) and len(raw)==before.st_size,'private_file_changed')
    need(hashlib.sha256(raw).hexdigest()==expected,'pinned_hash_changed')
    return raw


def load():
    m=types.ModuleType('frozen168_review');m.__file__=str(TOOL)
    exec(compile(safe_read(TOOL,TOOL_SHA),str(TOOL),'exec'),m.__dict__)
    return m,m.bootstrap()


def gate(row):
    need(row.get('read_only')=='on','sql_not_read_only')
    counts=('max_connections','reserved_connections','client_backends','database_client_backends','parallel_workers',
        'idle_in_transaction','idle_in_transaction_aborted','lock_waiters')
    out={}
    for k in counts:
        v=row.get(k);need(type(v) is int and 0<=v<10**6,'database_count_shape');out[k]=v
    age=row.get('oldest_idle_transaction_seconds')
    need(age is None or (type(age) in (int,float) and 0<=age<10**10),'transaction_age_shape')
    out['oldest_idle_transaction_seconds']=age
    for key in ('waiter_roles','blocker_roles'):
        values=row.get(key);need(isinstance(values,list) and len(values)<=40,'lock_roles_shape')
        projected=[]
        for v in values:
            need(v.get('backend_type') in ('client backend','autovacuum worker','parallel worker','other'),'unknown_backend_role')
            need(type(v.get('records')) is int and 0<=v['records']<10**6,'lock_role_count')
            clean=dict(backend_type=v['backend_type'],records=v['records'])
            if key=='waiter_roles':
                need(v.get('wait_event') in ('relation','transactionid','tuple','extend','advisory','virtualxid','object','spectoken','OTHER'),'unknown_lock_event')
                age=v.get('max_query_age_seconds');need(type(age) in (int,float) and 0<=age<10**10,'lock_age_shape')
                clean.update(wait_event=v['wait_event'],max_query_age_seconds=age)
            projected.append(clean)
        out[key]=projected
    usable=out['max_connections']-out['reserved_connections'];need(usable>0,'database_capacity_shape')
    conditions=dict(connection_headroom=out['client_backends']<usable*.6,
        no_lock_waiters=out['lock_waiters']==0,no_idle_transactions=out['idle_in_transaction']==0)
    out.update(original_gate_conditions=conditions,original_gate_would_pass_now=all(conditions.values()),
        connection_gate_exclusive_threshold=usable*.6,query_age_is_not_lock_wait_duration=True,
        historical_failed_fifth_sample='NOT_RETAINED_NO_RETROSPECTIVE_CAUSE_CLAIM')
    return out


def memory_inputs(snapshot,working_set=None):
    values=snapshot.get('values',{})
    def scalar(*keys):
        for key in keys:
            v=values.get(key)
            if isinstance(v,list) and len(v)==1 and type(v[0]) is int and v[0]>=0:return v[0]
        return None
    current=scalar('v2_memory_current','v1_memory_usage');peak=scalar('v2_memory_peak','v1_memory_peak')
    need(current is not None and peak is not None,'memory_operands_unavailable')
    need(working_set is None or type(working_set) is int and working_set>=0,'working_set_shape')
    observed=max(current,peak,working_set or 0)
    scenarios=[]
    for n,limit in ((128,32*GIB),(96,24*GIB)):
        required=Decimal(observed)*n/64+4*GIB
        scenarios.append(dict(enrichment=n,fixed_proposed_limit_bytes=limit,estimated_required_bytes=int(required),
            estimate_would_pass=required<=limit,max_allowed_observed_input_bytes=int(Decimal(limit-4*GIB)*64/n)))
    return dict(cgroup_current_bytes=current,cgroup_lifetime_peak_bytes=peak,pod_working_set_bytes=working_set,
        observed_input_used_by_old_formula_bytes=observed,scenarios=scenarios,
        historical_peak_breakdown='NOT_MEASURED_DO_NOT_SUBTRACT_CURRENT_CACHE',
        formula_is_conservative_planning_not_a_measurement_or_approval=True)


def memory(c,app):
    raw=c.obs.call(['exec',app['metadata']['name'],'-c','app','--','python3','-I','-B','-c',MEMORY_CODE],timeout=15,limit=16384)
    row=json.loads(raw)
    need(set(row)<=set(('process_memory_bytes','memory_stat','unavailable','controller')),'memory_probe_shape')
    for key in ('process_memory_bytes','memory_stat'):
        need(isinstance(row.get(key),dict) and len(row[key])<=40 and all(type(v) is int and 0<=v<2**63 for v in row[key].values()),'memory_counter_shape')
    need(set(row['process_memory_bytes'])<=set(('VmRSS','VmHWM','RssAnon','RssFile','RssShmem','VmSwap')),'unknown_process_memory_field')
    need(isinstance(row.get('unavailable'),list) and row['unavailable'] in ([],['memory_controller_path']),'memory_unavailable_shape')
    row['counters_have_different_scopes_do_not_sum']=True
    return row


def collect(m,c):
    report=json.loads(safe_read(REPORT,REPORT_SHA))
    need(report.get('glm168_review')=='INCOMPLETE' and report.get('phase')=='FRESH_SAMPLE_5'
        and report.get('failure',{}).get('check')=='database_headroom_requires_review'
        and report.get('current_pod',{}).get('uid')==m.APP_UID,'unexpected_failed_review')
    saved_sets=[x['memory']['workingSetBytes'] for s in report.get('samples',[]) for r in s.get('resources',[])
        for x in r.get('pods',[]) if x.get('pod')==report['current_pod']['name']]
    need(all(type(x) is int and x>=0 for x in saved_sets),'historical_working_set_shape')
    historical=memory_inputs(report['cgroup_before'],max(saved_sets) if saved_sets else None)
    historical['saved_working_sets_bytes']=saved_sets
    emit(dict(gate_diagnostic='HISTORICAL_MEMORY_OPERANDS',failed_report_sha256=REPORT_SHA,
        memory=historical,database_fifth_sample='NOT_RETAINED',production_writes=0))
    plan,activation,_=m.bind_inputs(c);dep,app=m.current(c,plan);identity=c.obs.identity(app)
    c.e.runtime_env(app,{m.ENV_KEYS[k]:str(v) for k,v in dict(m.CURRENT,model=128).items()})
    pg,pg_identity=c.e.review.pg_binding(app)
    before=c.deep.cgroup(c.obs,app)
    result=dict(gate_diagnostic='INCOMPLETE',utc_started=utc(),historical_memory=historical,
        production_writes=0,provider_requests=0,ready_to_expand=False,expansion_retried=False,
        original_evidence_modified=False,historical_failed_database_values='NOT_RETAINED',samples=[])
    for i in range(3):
        row=dict(sample=i+1,utc=utc())
        # Keep every read result, even when a gate fails. A failed gate is a finding, not collection failure.
        try:
            _,now=c.e.review.pg_binding(app);need(c.r.sw.same(pg_identity,now),'postgres_identity_changed')
            raw=c.obs.call(['exec',pg['metadata']['name'],'-c',c.obs.container(pg)['name'],'--',
                'sh','-c',c.e.review.DB_SHELL,'diagnostic',GATE_SQL],timeout=20,limit=16384)
            row['database_gate']=gate(json.loads(raw))
            raw=c.obs.call(['exec',pg['metadata']['name'],'-c',c.obs.container(pg)['name'],'--',
                'sh','-c',c.e.review.DB_SHELL,'diagnostic',c.f.WAIT_SQL],timeout=20,limit=16384)
            row['database_waits']=c.f.waits(json.loads(raw))
        except Exception as exc:row['database_unavailable']=error(exc)
        emit(dict(gate_diagnostic='SAMPLE_DATABASE',**row))
        try:
            rows=c.obs.resource_snapshot({'app':[app]});c.sw.u.app_metric_freshness(app,rows)
            need(len(rows)==1 and not rows[0].get('unavailable'),'resource_unavailable')
            row['app_resources']=rows
            row['memory_detail']=memory(c,app)
            row['current_memory_scenario']=memory_inputs(c.deep.cgroup(c.obs,app),rows[0]['pods'][0]['memory']['workingSetBytes'])
        except Exception as exc:row['memory_unavailable']=error(exc)
        result['samples'].append(row);emit(dict(gate_diagnostic='SAMPLE_COMPLETE',**row))
        m.current(c,plan,identity)
        if i<2:time.sleep(5)
    result['cgroup_delta']=c.deep.cgroup_delta(before,c.deep.cgroup(c.obs,app))
    m.current(c,plan,identity);_,end=c.e.review.pg_binding(app)
    need(c.r.sw.same(pg_identity,end),'postgres_identity_changed')
    complete=all(not any(k.endswith('_unavailable') for k in x) for x in result['samples'])
    result.update(gate_diagnostic='COMPLETE_WITH_FINDINGS' if complete else 'INCOMPLETE',baseline_stable=True,utc_finished=utc())
    emit(result);return 0 if complete else 1


def main():
    need(sys.platform=='linux' and os.geteuid()==0 and socket.gethostname().split('.')[0]=='datax-01','root_datax01_required')
    need(len(sys.argv)==1,'no_arguments')
    import resource
    os.umask(0o077);resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    m,c=load();return collect(m,c)


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:emit(dict(gate_diagnostic='INCOMPLETE',failure=error(exc),production_writes=0,ready_to_expand=False));sys.exit(1)

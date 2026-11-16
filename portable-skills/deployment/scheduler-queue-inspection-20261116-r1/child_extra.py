"""Bounded scheduler queue metadata; no prompts, events or recovery actions."""
import datetime as dt

TITLE = '油气与金融市场日报'
DB_HOST = None
DB_PORT = None
DB_ADDRESSES = set()
ALLOW_DB_DNS = False
COUNTS_SQL = """SELECT status,count(*)::int AS count,min(created_at) AS oldest_created_at,
min(started_at) AS oldest_started_at,max(attempt_count) AS maximum_attempt_count,
count(*) FILTER (WHERE trigger='manual')::int AS manual_count
FROM scheduled_task_runs WHERE status=ANY($1::text[]) GROUP BY status ORDER BY status"""
ACTIVE_SQL = """SELECT s.id,s.task_id,s.thread_id,s.run_id,s.status,s.created_at,s.started_at,s.attempt_count,
s.lease_expires_at AS launch_lease_expires_at,r.status AS underlying_status,
r.lease_expires_at AS run_lease_expires_at,r.updated_at AS run_updated_at,
r.owner_worker_id IS NOT NULL AS run_owner_present,
CASE WHEN s.error ILIKE '%already has an active run%' OR s.error ILIKE '%already running%'
OR s.error ILIKE '%conflict%' THEN 'OVERLAP_CONFLICT'
WHEN s.error ILIKE '%queue wait timeout%' THEN 'QUEUE_TIMEOUT'
WHEN s.error IS NOT NULL THEN 'OTHER_REDACTED' ELSE 'NONE' END AS error_kind
FROM scheduled_task_runs s LEFT JOIN runs r ON r.run_id=s.run_id
WHERE s.status=ANY($1::text[]) ORDER BY s.created_at,s.id LIMIT 64"""
TASKS_SQL = """SELECT id,user_id,status,schedule_type,context_mode,run_count,next_run_at,
last_run_at,created_at,updated_at,lease_expires_at
FROM scheduled_tasks WHERE title=$1 AND status=ANY($2::text[])
ORDER BY created_at DESC,id LIMIT 9"""
TARGET_SQL = """SELECT s.id,s.task_id,s.thread_id,s.run_id,s.status,s.trigger,s.created_at,
s.started_at,s.finished_at,s.attempt_count,s.lease_expires_at AS launch_lease_expires_at,
r.status AS underlying_status,r.updated_at AS run_updated_at,
r.lease_expires_at AS run_lease_expires_at,r.owner_worker_id IS NOT NULL AS run_owner_present,
EXISTS(SELECT 1 FROM scheduled_task_runs older WHERE older.thread_id=s.thread_id
AND older.status IN ('queued','launching','running') AND (older.created_at,older.id)<(s.created_at,s.id)) AS older_same_thread,
EXISTS(SELECT 1 FROM runs busy WHERE busy.thread_id=s.thread_id AND busy.status IN ('pending','running')) AS thread_has_active_run,
CASE WHEN s.error ILIKE '%already has an active run%' OR s.error ILIKE '%already running%'
OR s.error ILIKE '%conflict%' THEN 'OVERLAP_CONFLICT'
WHEN s.error ILIKE '%queue wait timeout%' THEN 'QUEUE_TIMEOUT'
WHEN s.error IS NOT NULL THEN 'OTHER_REDACTED' ELSE 'NONE' END AS error_kind
FROM scheduled_task_runs s LEFT JOIN runs r ON r.run_id=s.run_id
WHERE s.task_id=$1 ORDER BY s.created_at DESC,s.id DESC LIMIT 3"""


def age(value, now):
    return max(0, int((now-value).total_seconds())) if value is not None else None


def lease_state(value, now):
    return 'UNKNOWN_OR_ABSENT' if value is None else 'NOT_EXPIRED' if value >= now else 'EXPIRED'


def occurrence_summary(row, now):
    return {'occurrence_sha256': sha(row['id']), 'task_sha256': sha(row['task_id']),
            'thread_sha256': sha(row['thread_id']), 'run_id_present': row['run_id'] is not None,
            'status': row['status'], 'created_at': iso(row['created_at']), 'age_seconds': age(row['created_at'], now),
            'started_at': iso(row['started_at']), 'attempt_count': row['attempt_count'],
            'launch_lease_state': lease_state(row['launch_lease_expires_at'], now),
            'underlying_status': row['underlying_status'],
            'run_lease_state': lease_state(row['run_lease_expires_at'], now),
            'run_updated_at': iso(row['run_updated_at']), 'run_owner_present': bool(row['run_owner_present']),
            'error_kind': row['error_kind']}


def source_snapshot():
    root = Path('/app/backend')
    names = ('app/scheduler/service.py', 'packages/harness/deerflow/persistence/scheduled_task_runs/sql.py',
             'packages/harness/deerflow/config/scheduler_config.py')
    summary, source = {}, {}
    for name in names:
        path = root/name
        before = path.stat()
        need(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= 524288, 'SCHEDULER_SOURCE_BOUND')
        raw = path.read_bytes()
        after = path.stat()
        need((before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns)
             and len(raw)==before.st_size, 'SCHEDULER_SOURCE_CHANGED')
        summary[name] = {'bytes':len(raw),'sha256':sha(raw)}
        source[name] = raw.decode('utf-8')
    sql = source[names[1]]
    assignments = [node for node in ast.parse(sql).body if isinstance(node,(ast.Assign,ast.AnnAssign))
                   and ((isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name) and node.target.id=='EXECUTING_RUN_STATUSES')
                        or isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='EXECUTING_RUN_STATUSES' for t in node.targets))]
    need(len(assignments)==1 and ast.literal_eval(assignments[0].value)==('launching','running'), 'SCHEDULER_SLOT_STATUSES_CHANGED')
    return summary


def findings(counts, active, targeted, config):
    signals = []
    slots = sum(row['count'] for row in counts if row['status'] in ('launching','running'))
    limit = config['max_concurrent_runs']
    if slots >= limit:
        signals.append('GLOBAL_EXECUTING_COUNT_REACHES_FRESH_CONFIG_LIMIT')
    if config['enabled'] is False:
        signals.append('FRESH_CONFIG_BACKGROUND_SCHEDULER_DISABLED')
    if any(row['underlying_status'] in ('success','error','timeout','interrupted') for row in active):
        signals.append('EXECUTING_OCCURRENCE_HAS_TERMINAL_DURABLE_RUN')
    if any(row['status']=='running' and row['underlying_status'] in ('pending','running')
           and row['run_lease_state']=='EXPIRED' for row in active):
        signals.append('RUNNING_OCCURRENCE_HAS_EXPIRED_RUN_LEASE')
    if any(row['status']=='launching' and row['launch_lease_state']=='EXPIRED' for row in active):
        signals.append('EXPIRED_LAUNCH_CLAIM_OBSERVED')
    if any(row.get('older_same_thread') or row.get('thread_has_active_run') and row['status']=='queued' for row in targeted):
        signals.append('TARGET_THREAD_HAS_EARLIER_OR_ACTIVE_RUN')
    if any(row['status']=='queued' and row['attempt_count']>0 and row['error_kind']=='OVERLAP_CONFLICT' for row in targeted):
        signals.append('TARGET_LAUNCH_ATTEMPT_REQUEUED_FOR_OVERLAP')
    if any(row['status']=='queued' and row['age_seconds']>config['queue_timeout_seconds'] for row in targeted):
        signals.append('TARGET_QUEUE_OLDER_THAN_FRESH_CONFIG_TIMEOUT')
    if not signals:
        signals.append('NO_SINGLE_CAUSE_PROVEN_FROM_THIS_METADATA_SNAPSHOT')
    return {'executing_count':slots,'fresh_config_global_limit':limit,'signals':signals,
            'fresh_config_not_loaded_scheduler_instance':True,
            'one_pod_observation_cannot_prove_fleet_poller_health':True,
            'lease_expiry_is_observation_not_cleanup_authorization':True}


async def inspect_database(conn, config):
    now = await conn.fetchval('SELECT CURRENT_TIMESTAMP')
    counts = [dict(row) for row in await conn.fetch(COUNTS_SQL,['queued','launching','running'])]
    for row in counts:
        row['oldest_age_seconds'] = age(row['oldest_created_at'],now)
        row['oldest_created_at'] = iso(row['oldest_created_at'])
        row['oldest_started_at'] = iso(row['oldest_started_at'])
    active = [occurrence_summary(row,now) for row in await conn.fetch(ACTIVE_SQL,['launching','running'])]
    tasks = await conn.fetch(TASKS_SQL,TITLE,['enabled','dispatching'])
    exact = len(tasks)==2 and len({row['user_id'] for row in tasks})==1
    targets, occurrences = [], []
    if exact:
        for task in tasks:
            target = {key:iso(task[key]) for key in ('status','schedule_type','context_mode','run_count','next_run_at','last_run_at','created_at','updated_at','lease_expires_at')}
            target.update(task_sha256=sha(task['id']),next_run_is_past=bool(task['next_run_at'] and task['next_run_at']<now))
            target['occurrences'] = []
            for row in await conn.fetch(TARGET_SQL,task['id']):
                item = occurrence_summary(row,now)
                item.update(trigger=row['trigger'],finished_at=iso(row['finished_at']),
                            older_same_thread=row['older_same_thread'],thread_has_active_run=row['thread_has_active_run'])
                target['occurrences'].append(item)
                occurrences.append(item)
            targets.append(target)
    return {'database_observed_at_utc':iso(now),'queue_counts':counts,'executing_rows':active,
            'executing_rows_limit':64,'executing_rows_may_be_truncated':len(active)==64,
            'task_scope':'EXACT_TWO_ENABLED_SAME_TITLE_SAME_OWNER' if exact else 'TITLE_MATCH_AMBIGUOUS_NO_TASK_DETAIL_READ',
            'matching_enabled_tasks_up_to_nine':len(tasks),'targets':targets,
            'findings':findings(counts,active,occurrences,config)}


async def observe(payload):
    global DB_HOST,DB_PORT,DB_ADDRESSES,ALLOW_DB_DNS
    out={'schema':'scheduler-queue-inspection-child.v1','status':'INCOMPLETE','database_readonly':True,
         'files_modified':False,'provider_requests':0,'work_messages_sent':0,'task_retry_requested':False,
         'secret_api_reads':0,'credentials_exported':False,'raw_messages_or_prompts_exported':False,
         'application_configuration_may_resolve_database_credentials':True,
         'live_application_poller_state_inspected':False}
    conn = None
    try:
        need(payload=={'title':TITLE},'EXACT_SCREENSHOT_TITLE_REQUIRED')
        out['source_hashes'] = source_snapshot()
        pg,get_config = runtime_modules()
        app_config = get_config()
        fields = ('enabled','multi_instance','poll_interval_seconds','lease_seconds','max_concurrent_runs','queue_timeout_seconds','recursion_limit')
        config = {key:getattr(app_config.scheduler,key) for key in fields}
        need(type(config['enabled']) is bool and type(config['max_concurrent_runs']) is int and 1 <= config['max_concurrent_runs'] <= 32,'SCHEDULER_CONFIGURATION_INVALID')
        out['fresh_config_scheduler'] = config
        candidate = type(app_config.scheduler)(**dict(app_config.scheduler.model_dump(),max_concurrent_runs=16))
        need(candidate.max_concurrent_runs==16,'SCHEDULER_MODEL_REJECTS_16')
        out.update(requested_max_concurrent_runs=16,live_configuration_model_accepts_16=True,
                   configuration_or_application_instance_changed=False)
        db = app_config.database
        need(db.backend=='postgres','POSTGRES_REQUIRED')
        url = db.postgres_url.replace('postgresql+asyncpg://','postgresql://',1)
        parsed = urlsplit(url)
        need(parsed.scheme in ('postgres','postgresql') and parsed.hostname,'DATABASE_ADDRESS_REQUIRED')
        DB_HOST,DB_PORT = parsed.hostname,parsed.port or 5432
        ALLOW_DB_DNS = True
        addresses = socket.getaddrinfo(DB_HOST,DB_PORT,type=socket.SOCK_STREAM)
        need(0<len(addresses)<=16,'DATABASE_DNS_BOUND')
        DB_ADDRESSES = {row[4][0] for row in addresses}
        conn = await pg.connect(url,timeout=5,command_timeout=4,
                               server_settings={'default_transaction_read_only':'on','statement_timeout':'4000','lock_timeout':'1000'})
        async with conn.transaction(readonly=True,isolation='repeatable_read'):
            need(await conn.fetchval('SHOW transaction_read_only')=='on','DATABASE_READONLY_REQUIRED')
            out.update(await inspect_database(conn,config))
        need(out['source_hashes']==source_snapshot(),'SCHEDULER_SOURCES_CHANGED_DURING_INSPECTION')
        out['status'] = 'SCHEDULER_QUEUE_METADATA_OBSERVED_NO_CHANGE'
    except Exception as exc:
        out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'READONLY_INSPECTION_FAILURE_REDACTED')
    finally:
        if conn is not None:
            try:await conn.close(timeout=2)
            except Exception:out['database_close_failed']=True
        ALLOW_DB_DNS=False
        DB_ADDRESSES=set()
    return out


def main():
    sys.dont_write_bytecode=True
    logging.disable(logging.CRITICAL)
    out={'schema':'scheduler-queue-inspection-child.v1','status':'INCOMPLETE','credentials_exported':False,'work_messages_sent':0}
    try:
        signal.signal(signal.SIGALRM,lambda *args:(_ for _ in ()).throw(Deadline()))
        signal.alarm(40)
        sys.addaudithook(audit)
        raw=sys.stdin.buffer.read(4097)
        need(len(raw)<=4096,'INPUT_BOUND')
        with contextlib.redirect_stdout(Discard()),contextlib.redirect_stderr(Discard()):
            out=asyncio.run(observe(json.loads(raw)))
    except Deadline:out.update(error_class='Deadline',error_code='READONLY_CHILD_DEADLINE')
    except Exception as exc:out.update(error_class=type(exc).__name__,error_code=str(exc) if isinstance(exc,Refused) else 'READONLY_CHILD_FAILURE_REDACTED')
    finally:signal.alarm(0)
    encoded=json.dumps(out,sort_keys=True)
    need(len(encoded.encode())<=131072,'DIAGNOSTIC_EXPORT_BOUND')
    print(encoded)
    return int(out['status']=='INCOMPLETE')


if __name__=='__main__':sys.exit(main())

#!/usr/bin/env python3
"""Read-only WeKnora scaling review, Python 3.7+. No apply/restart/signal mode."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import time

import observe_pipeline as obs

VERSION = '20261016-r1'
SETTINGS = {
    'core': ('asynq.core_concurrency', 'WEKNORA_ASYNQ_CORE_CONCURRENCY', 8),
    'postprocess': ('asynq.postprocess_concurrency', 'WEKNORA_ASYNQ_POSTPROCESS_CONCURRENCY', 2),
    'enrichment': ('asynq.enrichment_concurrency', 'WEKNORA_ASYNQ_ENRICHMENT_CONCURRENCY', 12),
    'maintenance': ('asynq.maintenance_concurrency', 'WEKNORA_ASYNQ_MAINTENANCE_CONCURRENCY', 4),
    'shared': ('asynq.shared_concurrency', 'WEKNORA_ASYNQ_SHARED_CONCURRENCY', 6),
    'wiki': ('asynq.wiki_concurrency', 'WEKNORA_WIKI_ASYNQ_CONCURRENCY', 8),
    'model': ('model.max_concurrency', 'WEKNORA_MODEL_MAX_CONCURRENCY', 32),
}
MODEL_IDS = ('deerflow-cnooc-deepseek-v4-flash', 'deerflow-cnooc-qwen3-embedding-8b', 'deerflow-dashscope-vlm')
ENV_KEYS = tuple(x[1] for x in SETTINGS.values()) + ('AUTO_MIGRATE', 'GOMAXPROCS', 'GOMEMLIMIT')
require = obs.require


def proposal():
    return {
        'worker_first_step': {'core': 8, 'postprocess': 4, 'enrichment': 24,
                              'maintenance': 4, 'shared': 4, 'wiki': 12},
        'worker_second_step_review_only': {'core': 8, 'postprocess': 4, 'enrichment': 48,
                                          'maintenance': 8, 'shared': 4, 'wiki': 16},
        'model_default_steps_review_only': [64, 128],
        'deepseek_local_limit_separate_step': 64,
        'per_model_override_review_required': True,
        'replicas': 1, 'mineru_slots': 16, 'max_parse_handlers': 12,
        'parse_headroom_not_strict_interactive_reservation': 4,
        'provider_capacity_approval_required': False,
        'queue_empty_required': False, 'old_claimed_tasks_must_finish': True,
        'ready_to_apply': False, 'apply_automatically': False,
        'resource_and_restart_review_required': True,
        'note': 'Planning ladder, not measured safe capacity. Expand further only while completed throughput improves.',
    }


def integer(value):
    if type(value) is int and -(2**63) <= value < 2**63:
        return value
    if isinstance(value, str) and re.fullmatch('[+-]?[0-9]{1,19}', value):
        n = int(value)
        return n if -(2**63) <= n < 2**63 else None
    return None


def resolve(pool, row, env):
    default = SETTINGS[pool][2]
    val, source = None, 'DEFAULT'
    if row and not row.get('bootstrap'):
        val = integer(row.get('value'))
        if val is not None:
            source = 'DATABASE'
    if val is None and integer(env) is not None:
        val, source = integer(env), 'ENVIRONMENT'
    if val is None:
        val = default
    if pool != 'model' and val < 1:
        val, source = default, 'NONPOSITIVE_WORKER_DEFAULT'
    return {'predicted_next_start': val, 'source': source,
            'production_binary_equivalence': 'NOT_PROVEN_BY_LOCAL_SOURCE',
            'requires_restart': pool != 'model'}


def sql_review():
    values = ','.join("('%s', %d)" % (v[0], v[2]) for v in SETTINGS.values())
    ids = ','.join("'%s'" % v for v in MODEL_IDS)
    return """BEGIN READ ONLY;
SET LOCAL statement_timeout = '5s';
SET LOCAL lock_timeout = '1s';
WITH wanted(key, default_value) AS (VALUES %s), settings AS (
 SELECT s.key,
 CASE WHEN NOT s.is_secret AND (s.value #>> '{}') ~ '^[+-]?[0-9]{1,18}$'
      THEN (s.value #>> '{}')::bigint ELSE NULL END AS value,
 (btrim(s.last_modified_by) = '' AND s.value::text = w.default_value::text) AS bootstrap,
 s.requires_restart, s.is_secret, s.updated_at
 FROM public.system_settings s JOIN wanted w ON w.key = s.key
), models AS (
 SELECT id,
 CASE WHEN (parameters->>'max_concurrency') ~ '^[+-]?[0-9]{1,18}$'
      THEN (parameters->>'max_concurrency')::bigint ELSE NULL END AS max_concurrency
 FROM public.models WHERE deleted_at IS NULL AND id IN (%s)
), connections AS (
 SELECT count(*) AS "all", count(*) FILTER (WHERE state = 'active') AS active,
 count(*) FILTER (WHERE wait_event_type = 'Lock') AS lock_waiters,
 count(*) FILTER (WHERE state = 'idle in transaction') AS idle_in_transaction,
 count(*) FILTER (WHERE datname = current_database()) AS this_database,
 current_setting('max_connections')::int AS max_connections,
 current_setting('superuser_reserved_connections')::int AS reserved_connections
 FROM pg_stat_activity WHERE backend_type = 'client backend'
)
SELECT json_build_object('transaction_read_only', current_setting('transaction_read_only'),
 'settings', coalesce((SELECT json_agg(settings) FROM settings), '[]'::json),
 'models', coalesce((SELECT json_agg(models) FROM models), '[]'::json),
 'connections', (SELECT row_to_json(connections) FROM connections));
ROLLBACK;""" % (values, ids)


DB_SHELL = r'''set -eu
test -n "${POSTGRES_PASSWORD:-}" && test -n "${POSTGRES_USER:-}" && test -n "${POSTGRES_DB:-}"
export PGPASSWORD="$POSTGRES_PASSWORD"
export PGCONNECT_TIMEOUT=5
export PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=5000 -c lock_timeout=1000 -c idle_in_transaction_session_timeout=10000'
exec_cmd_status=0
psql -X -qAt --no-password -v ON_ERROR_STOP=1 -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1" || exec_cmd_status=$?
unset PGPASSWORD
exit "$exec_cmd_status"
'''


def clean_database(row):
    require(row.get('transaction_read_only') == 'on', 'database_not_read_only')
    result = {'transaction_read_only': 'on', 'settings': [], 'models': [], 'connections': {}}
    require(isinstance(row.get('settings'), list) and len(row['settings']) <= 7, 'settings_shape')
    seen = set()
    for s in row['settings']:
        key = s.get('key')
        require(key in {v[0] for v in SETTINGS.values()} and key not in seen, 'unexpected_setting_key')
        seen.add(key)
        require(s.get('is_secret') is False, 'numeric_setting_marked_secret')
        require(type(s.get('bootstrap')) is bool and type(s.get('requires_restart')) is bool, 'setting_flags')
        result['settings'].append({'key': key, 'value': integer(s.get('value')),
                                  'bootstrap': s['bootstrap'], 'requires_restart': s['requires_restart']})
    require(isinstance(row.get('models'), list) and len(row['models']) <= 3, 'models_shape')
    seen = set()
    for s in row['models']:
        require(s.get('id') in MODEL_IDS and s['id'] not in seen, 'unexpected_model_id')
        seen.add(s['id'])
        result['models'].append({'id': s['id'], 'max_concurrency': integer(s.get('max_concurrency'))})
    for key in ('all', 'active', 'lock_waiters', 'idle_in_transaction', 'this_database',
                'max_connections', 'reserved_connections'):
        v = row.get('connections', {}).get(key)
        if v is not None:
            require(type(v) is int and v >= 0, 'database_counter_invalid')
            result['connections'][key] = v
    return result


def pg_binding(app):
    sts = json.loads(obs.call(['get', 'statefulset', 'weknora-postgres', '--show-managed-fields=false', '-o', 'json']))
    require(sts['spec'].get('replicas') == 1, 'postgres_replicas_require_review')
    sel = sts['spec']['selector']
    require(not sel.get('matchExpressions') and sel.get('matchLabels'), 'postgres_selector')
    ps = obs.get('pods', selector=','.join(k+'='+v for k,v in sorted(sel['matchLabels'].items())))['items']
    ps = [p for p in ps if obs.controlled_by(p, {sts['metadata']['uid']}) and obs.ready(p)]
    require(len(ps) == 1, 'postgres_ready_pod_not_unique')
    p = ps[0]
    ae, pe = obs.literal_env(obs.container(app)), obs.literal_env(obs.container(p))
    require(ae.get('DB_DRIVER', 'postgres') == 'postgres', 'database_driver_not_postgres')
    require(ae.get('DB_HOST') in ('weknora-postgres', 'weknora-postgres.deer-flow',
            'weknora-postgres.deer-flow.svc', 'weknora-postgres.deer-flow.svc.cluster.local')
            and ae.get('DB_PORT', '5432') == '5432', 'database_address_not_reviewed')
    require(ae.get('DB_NAME') and ae['DB_NAME'] == pe.get('POSTGRES_DB'), 'database_name_binding')
    require(ae.get('DB_USER') and ae['DB_USER'] == pe.get('POSTGRES_USER'), 'database_user_binding')
    refs = []
    for c, key in ((obs.container(app), 'DB_PASSWORD'), (obs.container(p), 'POSTGRES_PASSWORD')):
        rows = [e for e in c.get('env', []) if e['name'] == key]
        require(len(rows) == 1, 'database_credential_reference_missing')
        r = rows[0].get('valueFrom', {}).get('secretKeyRef')
        require(r and r.get('name') and r.get('key') and not r.get('optional'), 'database_credential_reference_required')
        refs.append(r)
    require(refs[0] == refs[1], 'database_credential_reference_mismatch')
    slices = obs.get('endpointslices', selector='kubernetes.io/service-name=weknora-postgres')['items']
    eps = [e for s in slices for e in s.get('endpoints', [])]
    require(eps and all(e.get('targetRef', {}).get('uid') == p['metadata']['uid']
                       and e.get('conditions', {}).get('ready') is True for e in eps), 'database_endpoint_binding')
    return p, {'statefulset_uid': sts['metadata']['uid'], 'spec_sha256': obs.digest(sts['spec']),
               'pod_identity': obs.identity(p)}


ENV_SHELL = 'set -eu\nfor key in ' + ' '.join(ENV_KEYS) + r'''; do
 v=$(printenv "$key" 2>/dev/null || true)
 printf '%s\t%s\n' "$key" "$v"
done
'''


def parse_env(raw):
    result = {}
    for line in raw.decode('utf-8').splitlines():
        pair = line.split('\t')
        require(len(pair) == 2 and pair[0] in ENV_KEYS and pair[0] not in result, 'unexpected_env_output')
        k, v = pair
        if not v:
            result[k] = None
        elif k == 'AUTO_MIGRATE':
            result[k] = v if v in ('true', 'false') else 'INVALID_REDACTED'
        elif k == 'GOMEMLIMIT':
            result[k] = v if re.fullmatch('[0-9]{1,12}(B|KiB|MiB|GiB)?|off', v) else 'INVALID_REDACTED'
        else:
            result[k] = v if integer(v) is not None else 'INVALID_REDACTED'
    return result


PROCESS_SHELL = r'''set -eu
pid=$1
case "$pid" in ''|*[!0-9]*) exit 20;; esac
test -r "/proc/$pid/exe" && test -r "/proc/$pid/status"
printf 'PID\t%s\n' "$pid"
sha256sum "/proc/$pid/exe" | awk '{print "BIN\t" $1}'
awk '/^SigCgt:/ {print "SigCgt\t" $2}' "/proc/$pid/status"
for item in asynq_package stop_marker; do
 case "$item" in
 asynq_package) needle='github.com/hibiken/asynq';;
 stop_marker) needle='Send signal TSTP to stop processing new tasks';;
 esac
 code=0
 LC_ALL=C grep -aFq "$needle" "/proc/$pid/exe" || code=$?
 case "$code" in 0) hit=1;; 1) hit=0;; *) exit 21;; esac
 printf 'MARK\t%s\t%s\n' "$item" "$hit"
done
'''


def parse_process(raw, pid):
    result = {'safe_to_signal_proven': False, 'markers_are_not_behavioral_proof': True}
    seen = set()
    for line in raw.decode('ascii').splitlines():
        p = line.split('\t')
        require(p[0] in ('PID', 'BIN', 'SigCgt', 'MARK'), 'process_projection_unexpected')
        key = ':'.join(p[:2]) if p[0] == 'MARK' else p[0]
        require(key not in seen, 'process_projection_duplicate'); seen.add(key)
        if p[0] == 'PID':
            require(len(p) == 2 and p[1] == str(pid), 'process_pid_changed')
            result['pid'] = pid
        elif p[0] == 'BIN':
            require(len(p) == 2 and re.fullmatch('[a-f0-9]{64}', p[1]), 'process_binary_hash')
            result['binary_sha256'] = p[1]
        elif p[0] == 'SigCgt':
            require(len(p) == 2 and re.fullmatch('[0-9a-fA-F]{16}', p[1]), 'process_signal_mask')
            result['tstp_handler_installed'] = bool(int(p[1], 16) & (1 << 19))
        else:
            require(len(p) == 3 and p[1] in ('asynq_package', 'stop_marker') and p[2] in ('0', '1'), 'process_marker')
            result[p[1] + '_present'] = p[2] == '1'
    require(all(k in result for k in ('pid', 'binary_sha256', 'tstp_handler_installed')), 'process_projection_incomplete')
    return result


def redis_projection(raw, host):
    result = obs.parse_redis(raw, host)
    pids = set()
    for line in raw.decode('ascii').splitlines():
        p = line.split('\t')
        if p[0] != 'H':
            continue
        data = base64.b64decode(p[2], validate=True)[:-1]
        if not data:
            continue
        info = obs.server_info(data, host)
        if info is not None:
            f = obs.protobuf(data)
            xs = f.get(2, [])
            require(len(xs) == 1 and type(xs[0]) is int and 0 < xs[0] < 4194304, 'heartbeat_pid_invalid')
            pids.add(xs[0])
    result['process_ids'] = sorted(pids)
    return result


def sample(binding):
    p, db, host = binding
    raw = obs.call(['exec', p['metadata']['name'], '-c', obs.container(p)['name'], '--',
                    'sh', '-c', obs.REDIS_SHELL, 'review', db], timeout=30, limit=1048576)
    return redis_projection(raw, host)


def deployment_projection(d, p):
    spec = d['spec']['template']['spec']; c = spec['containers'][0]
    return {'deployment_uid': d['metadata']['uid'], 'generation': d['metadata']['generation'],
            'spec_sha256': obs.digest(d['spec']), 'replicas': d['spec']['replicas'],
            'strategy': d['spec'].get('strategy'), 'termination_grace_seconds': spec.get('terminationGracePeriodSeconds', 30),
            'pre_stop_present': bool(c.get('lifecycle', {}).get('preStop')),
            'pre_stop_sha256': obs.digest(c.get('lifecycle', {}).get('preStop')),
            'startup_command_sha256': obs.digest([c.get('command'), c.get('args')]),
            'resources': c.get('resources', {}), 'node': p['spec']['nodeName'],
            'pod_identity': obs.identity(p), 'image': c['image'],
            'config_mounts': [{'mount_path': x['mountPath'], 'read_only': x.get('readOnly', False),
                              'sub_path_present': bool(x.get('subPath'))} for x in c.get('volumeMounts', [])]}


def collect(report):
    obs.read_expansion()
    deps, pods, signature, safe = obs.baseline()
    report['deployment_baselines'] = safe
    app = pods['weknora-app'][0]
    report['app_restart_baseline'] = deployment_projection(deps['weknora-app'], app)
    report['stage'] = 'CONFIGURATION_AND_PROCESS'
    def attempt(label, fn, persist=True):
        obs.emit({'tuning_review': 'RUNNING', 'check': label})
        try:
            value = fn()
            if persist:
                report[label] = value
            return value
        except Exception as exc:
            report.setdefault('unavailable', {})[label] = obs.failure(exc)
            return None
    env = attempt('runtime_env', lambda: parse_env(obs.call(['exec', app['metadata']['name'], '-c',
                  obs.container(app)['name'], '--', 'sh', '-c', ENV_SHELL], limit=8192)))
    pg = attempt('database_binding', lambda: pg_binding(app), persist=False)
    if pg:
        pg_pod, pg_sig = pg
        report['database_binding'] = pg_sig  # Never persist full Pod/environment.
        database = attempt('database_review', lambda: clean_database(json.loads(obs.call(
            ['exec', pg_pod['metadata']['name'], '-c', obs.container(pg_pod)['name'], '--',
             'sh', '-c', DB_SHELL, 'review', sql_review()], timeout=25, limit=65536))))
    else:
        database = None
    binding = attempt('redis_binding', lambda: obs.redis_binding(pods), persist=False)
    if binding:
        report['redis_binding'] = {'verified': True, 'db': int(binding[1]), 'credentials_exported': False}
        report['samples'] = []
        for i in range(3):
            r = attempt('sample_temporary', lambda: sample(binding))
            report.pop('sample_temporary', None)
            if r:
                report['samples'].append(r)
            if i < 2:
                time.sleep(10)
        if report['samples']:
            pids = report['samples'][-1]['process_ids']
            if len(pids) == 1:
                attempt('process_identity', lambda: parse_process(obs.call(['exec', app['metadata']['name'],
                    '-c', obs.container(app)['name'], '--', 'sh', '-c', PROCESS_SHELL, 'review', str(pids[0])],
                    timeout=35, limit=4096), pids[0]))
            else:
                report.setdefault('unavailable', {})['process_identity'] = {'check': 'one_worker_process_required'}
    if database is not None and env is not None:
        rows = {x['key']: x for x in database['settings']}
        report['settings_resolution_prediction'] = {k: resolve(k, rows.get(v[0]), env.get(v[1]))
                                                     for k, v in SETTINGS.items()}
        observed = report.get('samples', [{}])[-1].get('effective_pool_summary', {}) if report.get('samples') else {}
        report['configured_vs_running'] = {
            k: {'predicted_next_start': report['settings_resolution_prediction'][k]['predicted_next_start'],
                'heartbeat_capacity': observed.get(k, {}).get('effective_concurrency'),
                'matches': observed.get(k, {}).get('effective_concurrency') ==
                           report['settings_resolution_prediction'][k]['predicted_next_start']}
            for k in SETTINGS if k != 'model'}
    report['stage'] = 'RESOURCE_AND_STABILITY_RECHECK'
    extra = {'postgres': [pg[0]]} if pg else {}
    attempt('resources', lambda: obs.resource_snapshot(dict(pods, **extra)))
    _, _, after, _ = obs.baseline()
    require(signature == after, 'baseline_or_process_changed')
    if pg:
        _, pg_after = pg_binding(app)
        require(pg_sig == pg_after, 'database_baseline_changed')
        db_after = clean_database(json.loads(obs.call(['exec', pg_pod['metadata']['name'],
            '-c', obs.container(pg_pod)['name'], '--', 'sh', '-c', DB_SHELL, 'review', sql_review()],
            timeout=25, limit=65536)))
        if database:
            for k in ('settings', 'models'):
                require(sorted(database[k], key=lambda x: json.dumps(x, sort_keys=True)) ==
                        sorted(db_after[k], key=lambda x: json.dumps(x, sort_keys=True)),
                        'persisted_configuration_changed_during_review')
        report['database_connections_final'] = db_after['connections']
    report['baseline_stable'] = True
    report['restart_strategy_assessment'] = {
        'direct_rollout_restart_approved': False,
        'local_source_findings': ['Worker pool allocation is fixed at process startup.',
            'Persisted numeric settings may override environment; bootstrap defaults are special.',
            'Local Asynq construction has no explicit ShutdownTimeout; upstream default is 8 seconds.',
            'SIGTSTP/Stop can stop claiming while running tasks finish; live binary behavior still needs qualification.'],
        'safe_path_to_qualify': 'STOP_OLD_CLAIMING_THEN_VERIFY_SIX_STOPPED_HEARTBEATS_AND_ZERO_ACTIVE_THEN_CONFIG_RESTART',
        'all_pending_tasks_must_finish': False, 'signals_sent': 0,
        'remaining_gates': ['QUALIFY_EXACT_BINARY_STOP_BEHAVIOR', 'REVIEW_MEMORY_AND_DATABASE_HEADROOM',
                            'BACKUP_AND_CAS_SETTINGS_CHANGE', 'CONTROLLED_RESTART_AND_EFFECTIVE_CAPACITY_CHECK',
                            '3_10_30_DOCUMENT_ACCEPTANCE_AND_QUEUE_DRAIN_RATE_COMPARISON'],
    }


def main():
    require(sys.platform == 'linux' and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01',
            'run_on_datax01_as_root')
    require(len(sys.argv) == 1, 'no_arguments_no_mutation_modes')
    os.umask(0o077); obs.safe_parent('/var/lib')
    root = Path(tempfile.mkdtemp(prefix='weknora-tuning-review-', dir='/var/lib'))
    os.chmod(str(root), 0o700)
    report = {'version': VERSION, 'stage': 'BASELINE', 'utc_started': obs.utc(),
              'production_writes': 0, 'provider_requests': 0, 'queue_mutations': 0,
              'signals_sent': 0, 'credentials_exported': False, 'raw_logs_saved': False,
              'ready_to_apply': False, 'proposed_ladder': proposal()}
    obs.emit({'tuning_review_evidence': str(root), 'production_writes': 0, 'provider_requests': 0})
    code = 1
    try:
        collect(report)
        report['tuning_review'] = 'COMPLETE' if not report.get('unavailable') else 'PARTIAL'
        code = 0 if report['tuning_review'] == 'COMPLETE' else 1
    except Exception as exc:
        report['tuning_review'] = 'INCOMPLETE'; report['failure'] = obs.failure(exc)
    report['utc_finished'] = obs.utc()
    raw = obs.write_new(root / 'RESULT.json', report)
    # Report is allowlisted and credential-free. Always print it so a home-directory
    # permission issue cannot block review or require unsafe chmod of old evidence.
    print('SAFE_REPORT_BEGIN', flush=True)
    obs.emit(report)
    print('SAFE_REPORT_END', flush=True)
    obs.emit({'tuning_review': report['tuning_review'], 'report': str(root / 'RESULT.json'),
              'report_sha256': hashlib.sha256(raw).hexdigest(), 'production_writes': 0,
              'ready_to_apply': False, 'automatic_retry': False})
    return code


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        obs.emit({'tuning_review': 'INCOMPLETE', 'failure': obs.failure(exc), 'production_writes': 0})
        sys.exit(1)

"""Bounded post-change observation. No production writes, signals or uploads."""
import hashlib
from pathlib import Path
import tempfile
import time

import observe_pipeline as obs
import tune_weknora88 as tune

SAMPLES = 21
INTERVAL = 30


def public_summary(report):
    samples = report['samples']
    assessment = obs.interpret(samples)
    workers = [s['active_workers'] for s in samples if s.get('worker_reads_complete')]
    dispatchers = [s['dispatcher'] for s in samples if s.get('dispatcher')]
    dates = [s['redis']['redis_sample_epoch'] for s in samples if 'redis' in s]
    return {
        'observation': report.get('observation', 'INCOMPLETE'),
        'sample_count': len(samples), 'sample_window_seconds': dates[-1]-dates[0] if len(dates)>1 else None,
        'queue_trends': assessment['all_queue_trends'],
        'mineru_active_sample_min': min(workers) if workers else None,
        'mineru_active_sample_max': max(workers) if workers else None,
        'dispatcher_waiting_max': max((s['waiting'] for s in dispatchers), default=None),
        'dispatcher_quarantined_max': max((s['quarantined'] for s in dispatchers), default=None),
        'resource_and_database_samples': report.get('resource_and_database_samples', []),
        'runtime_guard_warnings': report.get('runtime_guard_warnings', []),
        'latency_last_10m_bounded_tail': report.get('latency_last_10m_bounded_tail'),
        'log_coverage': report.get('log_coverage', []),
        'failure': report.get('failure'),
        'model_actual_occupancy': 'NOT_MEASURED',
        'real_document_end_to_end': 'REQUIRES_USER_3_10_30_DOCUMENT_ACCEPTANCE',
        'performance_acceptance': 'PENDING_REVIEW',
        'task_counters_are_not_document_counts': True,
        'production_writes': 0, 'signals_sent': 0, 'provider_probe_submissions': 0,
        'automatic_tuning': False, 'automatic_rollback': False,
    }


def collect(root, report):
    plan = tune.read_plan()
    success = tune.load(tune.ROOT/'forward'/'SUCCESS.json')
    obs.require(success.get('configuration_switch') == 'PASS' and success.get('effective_pools') == tune.TARGET
                and success.get('model_governor_startup_limit') == tune.TARGET_MODEL, 'accepted_88_change_required')
    d = tune.deployment(); app = tune.app_for(d, plan['candidate'])
    _, pods, signature, report['baseline'] = obs.baseline()
    obs.require(obs.identity(pods['weknora-app'][0]) == obs.identity(app), 'observation_app_changed')
    binding = tune.redis_binding(app)
    targets = [p for n in obs.WORKERS + [obs.DISPATCHER] for p in pods[n]]
    tune.runtime_env(app, tune.TARGET_ENV)
    for i in range(SAMPLES):
        began = time.monotonic()
        tune.app_for(tune.deployment(), plan['candidate'], obs.identity(app))
        row = {'number': i+1, 'utc': obs.utc()}
        with obs.ThreadPoolExecutor(max_workers=4) as pool:
            f = pool.submit(tune.snapshot, binding)
            states = list(pool.map(obs.health, targets))
            row['redis'] = f.result()
        tune.check_capacities(row['redis'], tune.TARGET, 'active')
        row['workers'] = [x for x in states if x.get('role') == 'worker']
        row['worker_reads_complete'] = len(row['workers']) == 16
        row['active_workers'] = sum(x['counters']['active'] for x in row['workers']) if row['worker_reads_complete'] else None
        row['dispatcher'] = next((x['counters'] for x in states if x.get('role') == 'dispatcher'), None)
        row['unavailable_reads'] = [x for x in states if 'unavailable' in x]
        report['samples'].append(row)
        obs.write_new(root/('sample-%02d.json' % (i+1)), row)
        obs.emit({'post_tune_observation': 'RUNNING', 'sample': i+1, 'of': SAMPLES,
                  'active_workers': row['active_workers'], 'dispatcher': row['dispatcher']})
        if i % 5 == 0 or i == SAMPLES-1:
            db = tune.database(app)
            rows = obs.resource_snapshot({'app': [app], 'docreader': pods['weknora-docreader'],
                                          'redis': pods['weknora-redis']})
            rec = {'sample': i+1, 'utc': obs.utc(), 'database_connections': db['connections'], 'resources': rows}
            report['resource_and_database_samples'].append(rec)
            # Apply the same thresholds to the sampled app row without another
            # Kubelet read. Do not mutate a stressed system or auto-rollback.
            try:
                app_rows = [dict(r, pods=[p for p in r.get('pods', []) if p.get('pod') == app['metadata']['name']])
                            for r in rows if r.get('node') == app['spec']['nodeName']]
                tune.check_resource_rows(app, db, app_rows, expanding=True)
            except Exception as exc:
                report['runtime_guard_warnings'].append({'sample': i+1, **obs.failure(exc)})
            obs.write_new(root/('resources-%02d.json' % (i+1)), rec)
        if i < SAMPLES-1:
            time.sleep(max(0, INTERVAL-(time.monotonic()-began)))
    obs.emit({'post_tune_observation': 'BOUNDED_LOG_AGGREGATES', 'raw_logs_saved': False})
    with obs.ThreadPoolExecutor(max_workers=4) as pool:
        logs = list(pool.map(obs.logs, targets + pods['weknora-app'] + pods['weknora-docreader']))
    values = {}
    for r in logs:
        for key, series in r.get('values', {}).items():
            values.setdefault(key, []).extend(series)
    report['latency_last_10m_bounded_tail'] = {k: obs.summary_stats(v) for k,v in values.items()}
    report['log_coverage'] = [{k:v for k,v in row.items() if k!='values'} for row in logs]
    _, _, after, _ = obs.baseline()
    obs.require(after == signature, 'baseline_changed_during_observation')
    obs.require(all(s.get('worker_reads_complete') and s.get('dispatcher') for s in report['samples']),
                'incomplete_worker_or_dispatcher_samples')
    report['baseline_stable'] = True


def run():
    # The caller's Linux/datax-01/root gate runs before this function.
    obs.safe_parent('/var/lib')
    root = Path(tempfile.mkdtemp(prefix='weknora-tune88-observe-', dir='/var/lib'))
    root.chmod(0o700)
    report = {'utc_started': obs.utc(), 'observation': 'INCOMPLETE', 'samples': [],
              'resource_and_database_samples': [], 'runtime_guard_warnings': [],
              'production_writes': 0, 'provider_probe_submissions': 0, 'signals_sent': 0}
    obs.emit({'post_tune_observation_evidence': str(root), 'samples': SAMPLES, 'interval_seconds': INTERVAL})
    code = 1
    try:
        collect(root, report)
        report['observation'] = 'COMPLETE'; code = 0
    except KeyboardInterrupt:
        report['failure'] = {'check': 'OPERATOR_INTERRUPTED', 'raw_error_printed': False}; code = 130
    except Exception as exc:
        report['failure'] = obs.failure(exc)
    report['utc_finished'] = obs.utc()
    raw = obs.write_new(root/'RESULT.json', report)
    obs.emit({'post_tune_summary': public_summary(report)})
    obs.emit({'report': str(root/'RESULT.json'), 'report_sha256': hashlib.sha256(raw).hexdigest(),
              'observation': report['observation'], 'production_writes': 0,
              'collection_completion_is_not_performance_acceptance': True})
    return code

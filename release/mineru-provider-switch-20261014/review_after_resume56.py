#!/usr/bin/env python3
"""Fresh56-bound diagnostics and optional once-only GLM wire probe. No apply."""
from concurrent.futures import ThreadPoolExecutor
import datetime
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import socket
import subprocess
import sys
import time
import warnings

import diagnose_mineru_quarantine as helper
import recover_weknora56_consumers as recovery

ROOT = Path('/var/lib/weknora-after-resume56-20261016-r1')
RECEIPT = recovery.ROOT/'RESULT-8be6349330bb45dc81bf6521b11b0aa3.json'
RECEIPT_SHA = 'c7a61e61da2054205a73eb29eb5d3400a2f7d73f4fa6ba7e0b43ec171382f90e'
APP_UID = 'd64b460f-b27e-4a6f-afde-c5583b7d0455'
MAX_LOG = 1048576
REMOTE_SOURCE = None  # Embedded by the qualified packaging tool, not operator input.
require, emit = helper.require, helper.emit
obs = e = None


def timestamp(line):
    m = re.match(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d{1,9})?(Z|[+-]\d\d:\d\d) ', line)
    if not m:
        return None
    try:
        fraction = (m[2] or '')[:7]
        return datetime.datetime.fromisoformat(m[1] + fraction + ('+00:00' if m[3] == 'Z' else m[3])).astimezone(datetime.timezone.utc).isoformat()
    except ValueError:
        return None


def error_classes(line):
    # Export only our fixed enums; never a provider message, URL, task ID or filename.
    labels = []
    patterns = (
        ('PROVIDER_TASK_DEADLINE', r'TimeoutError: Enterprise MinerU task [A-Za-z0-9._-]+ exceeded \d+ seconds'),
        ('PROVIDER_RESULT_DEADLINE', r'TimeoutError: Enterprise MinerU result for task [A-Za-z0-9._-]+ was not ready'),
        ('PROVIDER_STATUS_POLL_TRANSIENT', r'Transient MinerU poll (?:HTTP |transport )?failure .*operation=task status '),
        ('PROVIDER_RESULT_POLL_TRANSIENT', r'Transient MinerU poll (?:HTTP |transport )?failure .*operation=task result '),
        ('PROVIDER_ASYNC_TASK_FAILED', r'RuntimeError: Enterprise MinerU asynchronous task failed:'),
        ('PROVIDER_INVALID_RESULT', r'RuntimeError: Enterprise MinerU returned (?:an invalid|no parseable|unexpected task status|a non-JSON)'),
        ('LOCAL_LIBREOFFICE_TIMEOUT', r'RuntimeError: LibreOffice timed out after \d+s'),
        ('UPSTREAM_READ_TIMEOUT', r'(?:requests\.exceptions|urllib3\.exceptions)\.ReadTimeout(?:Error)?:'),
        ('UPSTREAM_CONNECT_TIMEOUT', r'requests\.exceptions\.ConnectTimeout:'),
        ('UPSTREAM_TLS_ERROR', r'requests\.exceptions\.SSLError:'),
        ('CLIENT_DISCONNECTED', r'Parser response client disconnected before body delivery'),
        ('ADAPTER_UNCLASSIFIED_EXCEPTION', r'MinerU adapter request failed'),
    )
    for label, pattern in patterns:
        if re.search(pattern, line):
            labels.append(label)
    m = re.search(r'Enterprise MinerU HTTP ([45]\d\d) during (asynchronous submission|task status|task result|synchronous parse):', line)
    if m:
        labels.append('PROVIDER_HTTP_%s_%s' % (m[1], m[2].upper().replace(' ', '_')))
    return labels


def log_report(raw, ip_to_name, now, stats):
    lines = raw.decode('utf-8', errors='replace').splitlines()
    stamps, errors, last_errors, events, open_parses = [], {}, [], [], {}
    for line in lines:
        stamp = timestamp(line)
        if stamp:
            stamps.append(stamp)
        for label in error_classes(line):
            errors[label] = errors.get(label, 0) + 1
            last_errors.append({'utc': stamp, 'class': label})
        q = re.search(r'MinerU dispatcher quarantined backend=([0-9.]+) cooldown_seconds=[0-9.]+ reason=(.*)$', line)
        if q:
            events.append({'utc': stamp, 'pod': ip_to_name.get(q[1], 'NOT_IN_CURRENT_POOL'),
                           'reason': q[2] if q[2] in helper.REASONS else 'UNCLASSIFIED'})
        p = re.search(r'Parse started request_id=([a-f0-9]{12}) ', line)
        if p:
            open_parses[p[1]] = stamp
        p = re.search(r'Parse finished request_id=([a-f0-9]{12}) ', line)
        if p:
            open_parses.pop(p[1], None)
    ages = [round((now-datetime.datetime.fromisoformat(s)).total_seconds(), 1)
            for s in open_parses.values() if s and now >= datetime.datetime.fromisoformat(s)]
    series, transport = stats(raw)
    return {'bytes_read': len(raw), 'lines_read': len(lines),
            'bounded_window': 'LAST_45_MIN_MAX_10000_LINES_1MiB',
            'limit_may_truncate': len(raw) >= MAX_LOG or len(lines) >= 10000,
            'first_observed_utc': min(stamps) if stamps else None,
            'last_observed_utc': max(stamps) if stamps else None,
            'statistics': {k: obs.summary_stats(v) for k, v in series.items()},
            'dispatcher_transport_errors': transport, 'error_class_counts': errors,
            'recent_error_classes': last_errors[-12:], 'quarantine_events': events[-24:],
            'parse_starts_without_finish_in_window': len(open_parses),
            'oldest_unmatched_start_age_seconds': max(ages) if ages else None,
            'unmatched_start_is_not_inflight_proof': True, 'quarantine_membership_proven': False,
            'raw_logs_saved': False, 'request_ids_exported': False}


CONFIG_KEYS = ('MINERU_TIMEOUT_SECONDS', 'MINERU_DISPATCH_REQUEST_TIMEOUT_SECONDS',
               'MINERU_DISPATCH_INTERACTIVE_REQUEST_TIMEOUT_SECONDS', 'MINERU_DISPATCH_WAIT_TIMEOUT_SECONDS',
               'MINERU_DISPATCH_PROBE_TIMEOUT_SECONDS', 'MINERU_DISPATCH_FAILURE_COOLDOWN_SECONDS',
               'MINERU_POLL_INTERVAL_SECONDS')


def numeric_config(pod):
    # Only numeric allowlisted variables are ever read, not a whole env dump.
    code = "import json,os,re; keys=" + repr(CONFIG_KEYS) + "; out={}\n"
    code += "for k in keys:\n v=os.environ.get(k); out[k]=float(v) if v and re.fullmatch(r'(?:[0-9]{1,7}(?:\\.[0-9]{1,6})?|\\.[0-9]{1,6})',v) else ('UNSET' if v is None else 'INVALID')\nprint(json.dumps(out))"
    raw = obs.call(['exec', pod['metadata']['name'], '-c', obs.container(pod)['name'], '--', 'python3', '-I', '-B', '-c', code], limit=4096)
    row = json.loads(raw)
    require(set(row) == set(CONFIG_KEYS) and all(type(v) in (int, float) or v in ('UNSET', 'INVALID') for v in row.values()), 'numeric_config_shape')
    return row


APP_POLICY = r'''import json,os,re,ssl
host='aiapi.cnooc.com.cn'
def exact(name): return host in [s.strip().lower() for s in os.environ.get(name,'').split(',')]
out={'env_exact_ssrf_allowlist':exact('SSRF_WHITELIST_EXTRA') or exact('SSRF_WHITELIST'),
 'database_or_wildcard_policy':'NOT_EVALUATED','go_ssrf_acceptance':'NOT_TESTED',
 'exact_no_proxy':exact('NO_PROXY') or exact('no_proxy'),
 'proxy_environment_present':any(bool(os.environ.get(k)) for k in ('HTTP_PROXY','HTTPS_PROXY','http_proxy','https_proxy'))}
v=os.environ.get('VLM_HTTP_TIMEOUT_SECONDS')
out['configured_vlm_timeout_seconds']=int(v) if v and re.fullmatch('[0-9]{1,6}',v) else ('UNSET_DEFAULT180' if v is None else 'INVALID')
try:
 c=ssl.create_default_context(); out['python_default_tls_context']='PASS' if c.check_hostname and c.verify_mode==ssl.CERT_REQUIRED else 'FAIL'
except Exception: out['python_default_tls_context']='FAIL'
print(json.dumps(out))
'''


def bind():
    r = e.locked_receipt(RECEIPT, RECEIPT_SHA)
    s = e.load(recovery.ROOT/'SUCCESS.json')
    require(r.get('consumer_recovery') == s.get('consumer_recovery') == 'PASS'
            and r.get('new_pod', {}).get('uid') == s.get('new_pod', {}).get('uid') == APP_UID,
            'accepted_recovery_receipt_changed')
    plan = recovery.load_plan()
    d = e.deployment()
    p = recovery.exact_app(d, plan['target'])
    require(p['metadata']['uid'] == APP_UID, 'new_app_pod_changed_requires_review')
    e.runtime_env(p, e.CURRENT_ENV)
    recovery.endpoint_check(p, plan['service'])
    snap = e.snapshot(e.redis_binding(p)); e.check_capacities(snap, e.CURRENT, 'active')
    return d, p, plan, snap


def safe_failure(exc):
    return {'class': type(exc).__name__, 'check': str(exc) if isinstance(exc, (helper.AuditFailure, obs.CheckFailure)) else 'READ_UNAVAILABLE', 'raw_error_printed': False}


def audit(report):
    d, app, plan, snap = bind()
    _, pods, signature, _ = obs.baseline()
    dispatcher = pods[obs.DISPATCHER][0]
    workers = [p for n in obs.WORKERS for p in pods[n]]
    require(dispatcher['metadata']['uid'] == helper.DISPATCH_UID, 'dispatcher_changed')
    ipmap = {p['status']['podIP']: p['metadata']['name'] for p in workers}
    report['app'] = {'pod': app['metadata']['name'], 'uid': APP_UID, 'identity': obs.identity(app),
                     'deployment_spec_sha256': obs.digest(d['spec']), 'worker_slots': 56, 'pools_active': True}
    report['initial_queues'] = snap['queues']
    report['samples'] = []
    started = time.monotonic()
    for i in range(3):
        delay = started + 35*i - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        emit({'review': 'MINERU_SAMPLE', 'sample': i+1, 'of': 3})
        h = obs.health(dispatcher)
        with ThreadPoolExecutor(max_workers=4) as pool:
            local = list(pool.map(obs.health, workers))
        remote = helper.remote_health(obs, dispatcher, workers)
        sample = {'utc': obs.utc(), 'dispatcher': h, 'workers': local, 'dispatcher_to_workers': remote}
        report['samples'].append(sample)
        emit({'sample': i+1, 'dispatcher': h, 'busy_or_unavailable': [x for x in local if x.get('unavailable') or x.get('counters', {}).get('active')]})
    report['log_evidence'] = {}
    for pod in [dispatcher] + workers:
        name = pod['metadata']['name']
        emit({'review': 'BOUNDED_LOG_CLASSIFICATION', 'pod': name})
        try:
            raw = obs.call(['logs', name, '-c', obs.container(pod)['name'], '--timestamps=true', '--since=45m',
                            '--tail=10000', '--limit-bytes='+str(MAX_LOG)], timeout=25, limit=MAX_LOG+1024)
            row = log_report(raw, ipmap, datetime.datetime.now(datetime.timezone.utc), obs.log_stats)
            try:
                row['numeric_timeout_config'] = numeric_config(pod)
            except Exception as exc:
                row['numeric_timeout_config'] = {'unavailable': safe_failure(exc)}
            report['log_evidence'][name] = row
            emit({'pod_log_evidence': name, **row})
        except Exception as exc:
            report['log_evidence'][name] = {'unavailable': safe_failure(exc)}
    report['app_policy'] = json.loads(obs.call(['exec', app['metadata']['name'], '-c', 'app', '--',
                                               'python3', '-I', '-B', '-c', APP_POLICY], limit=4096))
    emit({'glm_app_policy': report['app_policy'], 'policy_modified': False})
    report['resource_review'] = []
    for i in range(3):
        emit({'review': 'READ_ONLY_88_RESOURCE_PREFLIGHT', 'sample': i+1})
        try:
            db = e.database(app)
            rows = e.resources(app, db)
            report['resource_review'].append({'sample': i+1, 'result': 'PASS', 'database': db, 'resources': rows})
        except Exception as exc:
            report['resource_review'].append({'sample': i+1, 'result': 'FAIL', 'failure': safe_failure(exc)})
        if i < 2:
            time.sleep(10)
    d2, app2, _, after_snap = bind()
    _, _, after, _ = obs.baseline()
    require(recovery.same_json(signature, after) and recovery.same_json(obs.identity(app), obs.identity(app2)), 'baseline_changed_during_read')
    report['final_queues'] = after_snap['queues']
    candidate = e.change_env(d2['spec'], e.TARGET_ENV)
    raw = obs.write_new(ROOT/'NEXT88-PROPOSAL.private.json', {'original': d2['spec'], 'candidate': candidate,
              'pod_identity': obs.identity(app2), 'recovery_receipt_sha256': RECEIPT_SHA,
              'apply_authorized_by_this_file': False, 'old88_plan_reusable': False})
    report['next88'] = {'proposal_sha256': hashlib.sha256(raw).hexdigest(), 'source_pod_uid': APP_UID,
                        'target_pools': e.TARGET, 'model_default_target': 128, 'mineru_workers_unchanged': 16,
                        'private_configuration_exported': False, 'server_dry_run': 'NOT_PERFORMED',
                        'signals_sent': 0, 'ready_to_apply': False, 'old88_continue_permitted': False}
    report['next88']['resource_samples_pass'] = all(x['result'] == 'PASS' for x in report['resource_review'])
    report['next88']['quarantine_zero_in_three_samples'] = all(
        x['dispatcher'].get('counters', {}).get('quarantined') == 0 for x in report['samples'])
    report['baseline_digest'] = obs.digest(signature)
    report['audit'] = 'COMPLETE'
    report['current_quarantine_membership'] = 'NOT_EXPOSED_BY_EXISTING_API'
    report['source_contract'] = {'native_vlm': 'TEXT_FIRST_NONSTREAM', 'glm_documented': 'IMAGE_FIRST_SINGLE_IMAGE',
        'reviewed_go_source_sha256': '541671fc80dc6056e4f9265a4e33928faeeb204428d1b559c14df9423f950349',
        'source_markers_are_not_native_go_acceptance': True}
    return app


BOOTSTRAP = """import json,sys,hashlib,resource
resource.setrlimit(resource.RLIMIT_CORE,(0,0))
data=json.loads(sys.stdin.buffer.read(262145))
raw=data['source'].encode()
assert hashlib.sha256(raw).hexdigest()==data['sha256']
ns={'__name__':'isolated_glm_wire_probe'}
try:
 exec(compile(raw,'<isolated_glm_wire_probe>','exec'),ns)
 result=ns['run'](data['token'])
except Exception:
 result={'wire_probe':'UNCONFIRMED','failure':'REMOTE_PREFLIGHT_OR_PROTOCOL_FAILURE','maximum_submissions':2,'raw_error_printed':False}
print(json.dumps(result),flush=True)
"""


def remote_probe(app, token):
    require(isinstance(REMOTE_SOURCE, str) and len(REMOTE_SOURCE) < 65536, 'embedded_probe_missing')
    data = json.dumps({'source': REMOTE_SOURCE, 'sha256': hashlib.sha256(REMOTE_SOURCE.encode()).hexdigest(), 'token': token}).encode()
    # Token and source only through the encrypted exec stdin, never argv/env/files.
    proc = subprocess.Popen(['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=390s', '-n', 'deer-flow',
             'exec', '-i', app['metadata']['name'], '-c', 'app', '--', 'python3', '-I', '-B', '-c', BOOTSTRAP],
             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    begin = time.monotonic()
    out = bytearray(); err = bytearray()
    try:
        with selectors.DefaultSelector() as sel:
            os.set_blocking(proc.stdin.fileno(), False)
            sel.register(proc.stdin, selectors.EVENT_WRITE, None)
            offset = 0
            sel.register(proc.stdout, selectors.EVENT_READ, out)
            sel.register(proc.stderr, selectors.EVENT_READ, err)
            last = begin
            while sel.get_map():
                require(time.monotonic()-begin < 385, 'glm_exec_deadline_outcome_uncertain')
                for key, _ in sel.select(.3):
                    if key.data is None:
                        offset += os.write(key.fileobj.fileno(), data[offset:offset+4096])
                        if offset == len(data):
                            sel.unregister(key.fileobj); proc.stdin.close()
                        continue
                    part = os.read(key.fileobj.fileno(), 8192)
                    if not part:
                        sel.unregister(key.fileobj)
                    else:
                        require(len(key.data)+len(part) <= 65536, 'glm_output_limit')
                        key.data.extend(part)
                if time.monotonic()-last >= 15:
                    emit({'glm_wire_probe': 'RUNNING', 'elapsed_seconds': round(time.monotonic()-begin), 'maximum_submissions': 2})
                    last = time.monotonic()
        require(proc.wait(timeout=3) == 0 and token.encode() not in out, 'glm_transport_uncertain_no_repeat')
        result = json.loads(out)
        require(result.get('wire_probe') in ('COMPLETE', 'UNCONFIRMED') and result.get('maximum_submissions') == 2, 'glm_receipt_shape')
        return result
    finally:
        if proc.poll() is None:
            proc.kill()  # Only local exec transport. Child has its own 180s alarm/request.
        proc.wait(); proc.stdout.close(); proc.stderr.close()
        if not proc.stdin.closed:
            proc.stdin.close()


def main(argv=None):
    global e, obs
    args = list(sys.argv[1:] if argv is None else argv)
    report = {'audit': 'INCOMPLETE', 'production_writes': 0, 'signals_sent': 0, 'automatic_retry': False,
              'workers_restarted': False, 'queue_mutations': 0, 'ready_to_apply88': False,
              'business_documents_submitted': 0, 'provider_submissions_maximum': 0}
    created = False; locks = []
    try:
        require(args in (['--audit'], ['--audit-and-glm-once'], ['--status']), 'choose_one_supported_mode')
        require(socket.gethostname().split('.')[0] == 'datax-01' and hasattr(os, 'geteuid') and os.geteuid() == 0, 'run_on_datax01_as_root')
        import fcntl
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0)); os.umask(0o077)
        recovery.initialize(); e, obs = recovery.e, recovery.obs
        if args == ['--status']:
            if recovery.exists(ROOT/'RESULT.json'):
                emit(e.load(ROOT/'RESULT.json'))
            else:
                emit({'result_present': False, 'started': recovery.exists(ROOT), 'automatic_retry': False,
                      'instruction': 'DO_NOT_REPEAT_IF_STARTED', 'production_writes': 0})
            return 0
        for path in (e.ROOT/'controller.lock', e.PREVIOUS_ROOT/'controller.lock'):
            fd = helper.open_safe(path); locks.append(fd)
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        helper.safe_parent(ROOT.parent)
        ROOT.mkdir(mode=0o700); created = True
        helper.safe_parent(ROOT)
        obs.write_new(ROOT/'STARTED.json', {'mode': args[0], 'utc': obs.utc(), 'maximum_submissions': 2 if args[0].endswith('once') else 0})
        emit({'fresh_review_evidence': str(ROOT), 'production_writes': 0, 'consumer_slots_kept': 56, 'maximum_glm_submissions': 2 if args[0].endswith('once') else 0})
        app = audit(report)
        if args == ['--audit-and-glm-once']:
            # Require a real terminal; getpass must not silently fall back to echoed stdin.
            with open('/dev/tty', 'rb+') as tty:
                require(os.isatty(tty.fileno()), 'hidden_terminal_required')
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                token = getpass.getpass('GLM API key (hidden, never saved): ')
            require(re.fullmatch(r'[!-~]{8,4096}', token) is not None, 'credential_shape')
            _, current, _, _ = bind()
            require(recovery.same_json(obs.identity(current), obs.identity(app)), 'app_changed_before_probe')
            obs.write_new(ROOT/'GLM-REQUESTED.json', {'utc': obs.utc(), 'pod_uid': APP_UID, 'maximum_submissions': 2})
            report['provider_submissions_maximum'] = 2
            report['glm'] = remote_probe(app, token); del token
            _, final_app, _, _ = bind()
            require(recovery.same_json(obs.identity(final_app), obs.identity(app)), 'app_changed_during_glm_probe')
            _, _, final_sig, _ = obs.baseline()
            require(obs.digest(final_sig) == report['baseline_digest'], 'baseline_changed_during_glm_probe')
            emit({'glm_compatibility_evidence': report['glm']})
        else:
            report['glm'] = {'wire_probe': 'NOT_REQUESTED'}
        report['remaining_gates'] = ['REVIEW_QUARANTINE_CAUSE_AND_STABLE16_HEALTH',
          'NATIVE_WEKNORA_GLM_IMPLEMENTATION_AND_REAL_PATH_ACCEPTANCE', 'FRESH88_SERVER_DRY_RUN_AND_CONTROLLED_SWITCH']
    except (Exception, KeyboardInterrupt) as exc:
        report['failure'] = safe_failure(exc) if obs else {'check': str(exc) if isinstance(exc, helper.AuditFailure) else 'PREFLIGHT_UNAVAILABLE', 'raw_error_printed': False}
    finally:
        for fd in locks:
            os.close(fd)
    if created:
        report['utc_finished'] = obs.utc()
        raw = obs.write_new(ROOT/'RESULT.json', report)
        emit({'audit': report['audit'], 'failure': report.get('failure'), 'next88': report.get('next88'),
              'report': str(ROOT/'RESULT.json'), 'report_sha256': hashlib.sha256(raw).hexdigest(),
              'automatic_retry': False, 'ready_to_apply88': False, 'production_writes': 0,
              'instruction': 'SEND_OUTPUT; ON_UNCERTAINTY_USE_STATUS_NO_REPEAT'})
    else:
        emit(report)
    return 0 if report['audit'] == 'COMPLETE' and 'failure' not in report else 1


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Resume only the unstarted GLM comparison after the reviewed TTY failure.

No audit replay, signals, image/config/model writes, or uncertain resubmission.
The old tool and its failure receipt stay immutable. Python 3.7+ on Linux.
"""
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import warnings

OLD_TOOL = Path('/var/lib/weknora-fresh-review-tool-QopUFgmF/review.py')
OLD_TOOL_SHA = 'a1285327361c8acb27df3c924b644e99ff140e8d0a93e5c976d63c2c1402d3c0'
OLD_RESULT_SHA = '8bcab35025df2103d855f91f8563ff1ed70235bb4fdd89b32eae7cf50df98e19'
OLD_PROPOSAL_SHA = 'ae5460e5bbc58b2ac1af995b2c9fd4cad0928fe6ae03dd78fe411560e63e1a52'
ROOT = Path('/var/lib/weknora-glm-wire-resume-20261016-r1')
review = None


class CheckFailure(Exception):
    pass


def require(ok, check):
    if not ok:
        raise CheckFailure(check)


def emit(row):
    print(json.dumps(row, sort_keys=True, ensure_ascii=True), flush=True)


def safe_read(path, digest):
    for parent in reversed([path.parent] + list(path.parent.parents)):
        s = parent.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'unsafe_helper_ancestor')
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1 and not s.st_mode & 0o022
                and 0 < s.st_size <= 1048576, 'unsafe_helper_file')
        raw = f.read(1048577)
    require(hashlib.sha256(raw).hexdigest() == digest, 'installed_helper_digest_changed')
    return raw


def initialize():
    global review
    raw = safe_read(OLD_TOOL, OLD_TOOL_SHA)
    # Non-main import of the exact previously installed delivery; no probe runs.
    exec(compile(raw, str(OLD_TOOL), 'exec'), {'__name__': 'glm_recovery_import', '__file__': str(OLD_TOOL)})
    review = sys.modules['review_after_resume56']
    review.recovery.initialize()
    review.e, review.obs = review.recovery.e, review.recovery.obs


def previous_zero_submission():
    old = review.e.locked_receipt(review.ROOT/'RESULT.json', OLD_RESULT_SHA)
    require(old.get('audit') == 'COMPLETE' and old.get('failure', {}).get('class') == 'UnsupportedOperation'
            and old.get('provider_submissions_maximum') == 0 and 'glm' not in old,
            'previous_not_known_pre_submission_failure')
    require(not review.recovery.exists(review.ROOT/'GLM-REQUESTED.json'),
            'previous_submission_possible_do_not_repeat')
    require(old.get('production_writes') == 0 and old.get('signals_sent') == 0
            and old.get('next88', {}).get('source_pod_uid') == review.APP_UID
            and old['next88'].get('proposal_sha256') == OLD_PROPOSAL_SHA,
            'previous_scope_or_proposal_changed')
    proposal = review.e.locked_receipt(review.ROOT/'NEXT88-PROPOSAL.private.json', OLD_PROPOSAL_SHA)
    require(proposal.get('apply_authorized_by_this_file') is False, 'previous_proposal_is_not_apply_authority')
    return old, proposal


def verify_live(old, proposal):
    d, app, _, _ = review.bind()
    require(review.recovery.same_json(review.obs.identity(app), old['app']['identity']), 'app_process_changed')
    require(d['spec'] == proposal['original']
            and review.e.change_env(d['spec'], review.e.TARGET_ENV) == proposal['candidate'],
            'proposal_live_source_changed')
    _, pods, signature, _ = review.obs.baseline()
    require(review.obs.digest(signature) == old['baseline_digest'], 'production_baseline_changed')
    return app, pods


def check_terminal():
    # /dev/tty cannot seek. BufferedRandom (open rb+) incorrectly requires it.
    # Inspect a raw fd; no seek, read, echo toggle or terminal writes here.
    import termios
    fd = os.open('/dev/tty', os.O_RDWR | getattr(os, 'O_NOCTTY', 0))
    try:
        require(os.isatty(fd), 'hidden_terminal_required')
        settings = termios.tcgetattr(fd)
        require(isinstance(settings, list) and len(settings) == 7, 'terminal_attributes_unavailable')
    finally:
        os.close(fd)


def hidden_token():
    check_terminal()
    # Escalate getpass fallback to an error BEFORE it can echo a credential.
    with warnings.catch_warnings():
        warnings.simplefilter('error', getpass.GetPassWarning)
        token = getpass.getpass('GLM API key (hidden, never saved): ')
    require(isinstance(token, str) and re.fullmatch(r'[!-~]{8,4096}', token) is not None, 'credential_shape')
    return token


def run(report):
    report['phase'] = 'VERIFY_PREVIOUS_ZERO_SUBMISSIONS'
    old, proposal = previous_zero_submission()
    report['previous_provider_submissions'] = 'ZERO_VERIFIED_BEFORE_REQUESTED_RECORD'
    report['previous_evidence_modified'] = False
    report['phase'] = 'VERIFY_CURRENT_RECOVERY56'
    app, pods = verify_live(old, proposal)
    report['mineru_health_before'] = review.obs.health(pods[review.obs.DISPATCHER][0])
    report['existing88_proposal'] = {'sha256': OLD_PROPOSAL_SHA, 'source_pod_uid': review.APP_UID,
                                   'ready_to_apply': False, 'old88_continue_permitted': False}
    report['phase'] = 'READ_HIDDEN_CREDENTIAL'
    emit({'glm_resume': 'READY_FOR_HIDDEN_INPUT', 'previous_submissions': 0, 'maximum_new_requests': 2,
          'consumer_slots_kept': 56, 'production_writes': 0})
    token = hidden_token()
    report['phase'] = 'FINAL_PRE_REQUEST_BASELINE'
    current, _ = verify_live(old, proposal)
    require(review.recovery.same_json(review.obs.identity(current), review.obs.identity(app)), 'app_changed_before_request')
    # O_EXCL durable boundary; any subsequent uncertainty forbids another run.
    review.obs.write_new(ROOT/'GLM-REQUESTED.json', {'utc': review.obs.utc(), 'pod_uid': review.APP_UID,
                       'previous_result_sha256': OLD_RESULT_SHA, 'maximum_submissions': 2})
    report['phase'] = 'GLM_WIRE_COMPARISON'
    report['provider_submissions_maximum'] = 2
    report['glm'] = review.remote_probe(app, token)
    del token
    review.obs.write_new(ROOT/'GLM-RECEIPT.json', report['glm'])
    emit({'glm_compatibility_evidence': report['glm']})
    report['phase'] = 'POST_REQUEST_BASELINE'
    _, pods = verify_live(old, proposal)
    report['mineru_health_after'] = review.obs.health(pods[review.obs.DISPATCHER][0])
    report['production_baseline'] = 'UNCHANGED'
    report['collection'] = 'COMPLETE'
    report['native_weknora_acceptance'] = 'NOT_TESTED'
    report['remaining_gates'] = ['REVIEW_GLM_WIRE_RESULTS', 'NATIVE_WEKNORA_GLM_INTEGRATION_ACCEPTANCE',
                               'FRESH88_DRY_RUN_AND_CONTROLLED_SWITCH_REVIEW']


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    report = {'collection': 'UNCONFIRMED', 'phase': 'PREFLIGHT', 'production_writes': 0,
              'signals_sent': 0, 'provider_submissions_maximum': 0, 'automatic_retry': False,
              'ready_to_apply88': False, 'credentials_saved': False, 'audit_repeated': False}
    created = False; locks = []
    try:
        require(sys.platform == 'linux' and hasattr(os, 'geteuid') and os.geteuid() == 0
                and socket.gethostname().split('.')[0] == 'datax-01', 'run_on_datax01_as_root')
        require(args in (['--probe-once'], ['--status']), 'explicit_supported_mode_required')
        import fcntl
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0)); os.umask(0o077)
        initialize()
        if args == ['--status']:
            if review.recovery.exists(ROOT/'RESULT.json'):
                emit(review.e.load(ROOT/'RESULT.json'))
            else:
                emit({'result_present': False, 'started': review.recovery.exists(ROOT),
                      'requested': review.recovery.exists(ROOT/'GLM-REQUESTED.json'),
                      'automatic_retry': False, 'production_writes': 0})
            return 0
        for path in (review.e.ROOT/'controller.lock', review.e.PREVIOUS_ROOT/'controller.lock'):
            fd = review.helper.open_safe(path); locks.append(fd)
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        require(not review.recovery.exists(ROOT), 'operation_exists_use_status_no_repeat')
        review.helper.safe_parent(ROOT.parent)
        ROOT.mkdir(mode=0o700); created = True; review.helper.safe_parent(ROOT)
        review.obs.write_new(ROOT/'STARTED.json', {'utc': review.obs.utc(), 'maximum_submissions': 2,
                             'previous_result_sha256': OLD_RESULT_SHA})
        emit({'glm_resume_evidence': str(ROOT), 'production_writes': 0, 'audit_repeated': False})
        run(report)
    except (Exception, KeyboardInterrupt) as exc:
        if isinstance(exc, CheckFailure):
            failure = {'check': str(exc), 'class': type(exc).__name__, 'raw_error_printed': False}
        elif review is not None:
            failure = review.safe_failure(exc)
        else:
            failure = {'check': 'PREFLIGHT_UNAVAILABLE', 'class': type(exc).__name__, 'raw_error_printed': False}
        report['failure'] = failure
    finally:
        if created:
            report['utc_finished'] = review.obs.utc()
            raw = review.obs.write_new(ROOT/'RESULT.json', report)
            emit({'collection': report['collection'], 'phase': report['phase'], 'failure': report.get('failure'),
                  'report': str(ROOT/'RESULT.json'), 'report_sha256': hashlib.sha256(raw).hexdigest(),
                  'provider_submissions_maximum': report['provider_submissions_maximum'],
                  'production_writes': 0, 'ready_to_apply88': False,
                  'instruction': 'SEND_RESULT; ON_UNCERTAINTY_USE_STATUS_DO_NOT_REPEAT'})
        elif 'failure' in report:
            emit(report)
        for fd in locks:
            os.close(fd)
    return 0 if report['collection'] == 'COMPLETE' and 'failure' not in report else 1


if __name__ == '__main__':
    sys.exit(main())

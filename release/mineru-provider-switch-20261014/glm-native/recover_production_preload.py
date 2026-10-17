"""One exact pre-import transfer failure; immutable old evidence, no rollout."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import signal
import socket
import stat
import sys

import export_weknora_base as io
import preload_native_candidate as cli
import production_node_preload as node
import preload_transfer_transport as transport

OLD_SOURCE = Path('/var/lib/weknora-glm-release-prepare-20261017-r1')
OLD_SOURCE_SHA = 'e43299487f344d432d443440ce75208463baa90c572271e8526289805cfad878'
OLD_NODE = Path('/var/lib/weknora-glm-production-preload-20261017-r1')
OLD_NODE_SHA = 'c3389fb2a2f45bb581cf08d14a22d45741a2df7f07b0a5f32594599127745d88'
OLD_NODE_STARTED_SHA = 'd658ca443caa6373366b308b699bffc0c04bc9ec11a6fc256c7a083ccc1b7b9d'
SOURCE = Path('/var/lib/weknora-glm-release-transfer-recovery-20261017-r1')
TARGET = Path('/var/lib/weknora-glm-production-preload-20261017-r1-fix1')
REMOTE_CODE = None
REMOTE_SOURCES = None
PHASE = 'PREFLIGHT'
need = transport.need


def read(path, expected=None):
    io.safe_dir(path.parent)
    before = io.private_meta(path)
    need(before[2] <= 1048576, 'receipt_size_bound')
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as f:
        raw = f.read(1048577)
    need(io.private_meta(path) == before and len(raw) == before[2], 'receipt_changed')
    digest = hashlib.sha256(raw).hexdigest()
    need(expected is None or digest == expected, 'receipt_hash_changed')
    return json.loads(raw, object_pairs_hook=node.verify.unique), digest


def bind_source():
    row, sha = read(OLD_SOURCE/'RESULT.json', OLD_SOURCE_SHA)
    need(row.get('weknora_release_prepare') == 'UNCONFIRMED' and
         row.get('phase') == 'PRELOAD_DATAX03_KEEP_CURRENT_APP_RUNNING' and
         row.get('failed_check') == 'remote_preload_not_passed_USE_STATUS' and
         row.get('remote_operation_attempted') is True and row.get('production_writes') == 0,
         'source_failure_boundary_changed')
    need(not os.path.lexists(str(OLD_SOURCE/'REMOTE-RESULT.json')), 'previous_remote_success_requires_review')
    review, review_sha = read(OLD_SOURCE/'REVIEW.json')
    need(review.get('production_writes') == 0 and review.get('slots_changed') is False,
         'previous_review_boundary')
    return dict(previous_source_result_sha256=sha, previous_review_sha256=review_sha,
                previous_review_is_snapshot_not_fresh_activation_approval=True)


def bind_target():
    row, sha = read(OLD_NODE/'RESULT.json', OLD_NODE_SHA)
    started, started_sha = read(OLD_NODE/'STARTED.json', OLD_NODE_STARTED_SHA)
    need(row.get('production_node_preload') == 'UNCONFIRMED' and row.get('imports_attempted') == 0 and
         row.get('phase') == 'RECEIVE_VERIFIED_ARCHIVE' and
         row.get('failed_check') == 'archive_excess_bytes_NO_IMPORT' and
         row.get('production_writes') == 0 and row.get('production_workloads_mutated') is False,
         'target_failure_boundary_changed')
    need(started.get('candidate') == cli.IMAGE and started.get('already_present') is False,
         'target_started_boundary_changed')
    names = {p.name for p in OLD_NODE.iterdir()}
    need(names == {'STARTED.json', 'RESULT.json', 'status.py', 'candidate.oci.tar.partial'},
         'old_target_mutation_boundary_requires_review')
    partial = io.private_meta(OLD_NODE/'candidate.oci.tar.partial')
    need(partial[2] == cli.ARCHIVE_BYTES, 'old_partial_size_requires_review')
    return dict(previous_target_result_sha256=sha, previous_target_started_sha256=started_sha,
                previous_imports='ZERO_VERIFIED_PRE_IMPORT_BOUNDARY',
                old_partial='RETAINED_ROOT_PRIVATE_POTENTIALLY_CONTAINS_AUTH_INPUT',
                old_partial_content_read=False, original_evidence_modified=False,
                historical_contamination_cause='NOT_PROVEN_BY_SAFE_RECEIPTS',
                protocol='PROMPT_GATED_SUDO_AND_ARCHIVE_FRAME_V2')


@contextmanager
def lock(root):
    import fcntl
    io.safe_dir(root.parent)
    fd = os.open(str(root)+'.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'rb') as f:
        s = os.fstat(f.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1 and not s.st_mode & 0o022, 'unsafe_lock')
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def safe_error(exc):
    text = str(exc)
    return text if type(exc).__name__ in ('RuntimeError', 'CheckFailure', 'PreloadFailure', 'RecoveryFailure', 'AcceptanceFailure') and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', text) else 'SAFE_DIAGNOSTIC_REQUIRED'


def node_main(status=False):
    node.ROOT = TARGET
    if status:
        return node.status()
    with lock(OLD_NODE), lock(TARGET):
        need(not os.path.lexists(str(TARGET)), 'previous_recovery_USE_STATUS')
        binding = bind_target()
        original_receive, original_record = node.receive_archive, node.record
        original_status = node.status_program
        need(REMOTE_SOURCES is not None, 'packaged_remote_sources_missing')

        def status_program():
            return (node.boot(REMOTE_SOURCES) +
                    'sys.exit(sys.modules["recover_production_preload"].main(["--target-status"]))\n').encode()

        def record(name, row):
            if name == 'STARTED.json':
                row = dict(row, recovery_binding=binding)
            original_record(name, row)

        node.receive_archive = lambda stream: original_receive(transport.FramedInput(stream))
        node.record = record
        node.status_program = status_program
        try:
            # Existing complete image may be reused; no reimport or repeated pin on uncertainty.
            return node.accept()
        finally:
            node.receive_archive, node.record, node.status_program = original_receive, original_record, original_status


def phase(name):
    global PHASE
    PHASE = name
    io.emit(dict(production_preload_recovery='RUNNING', phase=name, production_writes=0))


def save(name, row):
    previous = io.ROOT
    try:
        io.ROOT = SOURCE
        io.write_new(name, row)
    finally:
        io.ROOT = previous


def source_run():
    import recover_enrichment_gateway as frozen
    import production_release_review as review
    import transfer_base as trust
    need(not os.path.lexists(str(SOURCE)), 'previous_recovery_USE_STATUS')
    binding = bind_source()
    io.safe_dir(SOURCE.parent)
    SOURCE.mkdir(mode=0o700)
    save('STARTED.json', binding)
    result = dict(production_preload_recovery='UNCONFIRMED', production_writes=0,
                  production_workloads_mutated=False, model_changed=False, consumer_slots_changed=False,
                  build_started=False, provider_requests=0, rollout='NOT_PERFORMED', ready_for_rollout=False,
                  original_evidence_modified=False, automatic_retry=False, remote_operation_attempted=False)
    try:
        phase('BIND_ACCEPTANCE_AND_CURRENT_APP')
        a = frozen.load_original()
        review.accepted(a)
        p, q = a.native.initialize()
        package, guard, plan = q.package()
        guard.live(plan, binary=True)
        phase('VERIFY_EXISTING_SOURCE_ARCHIVE')
        candidate, _ = p.source(q)
        raw = cli.command(['ssh-keyscan', '-T', '5', '-t', 'ecdsa', trust.HOST], timeout=15, limit=65536)
        trust.private_write(SOURCE/'known_hosts', trust.known_host(raw))
        need(REMOTE_CODE is not None, 'packaged_receiver_missing')
        prompt = transport.marker()
        args = transport.ssh_command(REMOTE_CODE, SOURCE/'known_hosts', trust.HOST, prompt)
        guard.live(plan, binary=True)
        phase('TRANSFER_AND_IMPORT_CANDIDATE_ONLY')
        save('REMOTE-REQUESTED.json', dict(target=str(TARGET), original_target_sha256=OLD_NODE_SHA,
             image=cli.IMAGE, archive_sha256=cli.ARCHIVE_SHA, protocol='PROMPT_GATED_SUDO_AND_ARCHIVE_FRAME_V2'))
        result['remote_operation_attempted'] = True
        remote = transport.send(args, candidate, q.opened, cli.ARCHIVE_BYTES, cli.ARCHIVE_SHA, prompt, io.emit)
        save('REMOTE-RESULT.json', remote)
        phase('FINAL_PRODUCTION_BASELINE')
        guard.live(plan, binary=True)
        need(bind_source() == binding, 'previous_source_evidence_changed')
        result.update(production_preload_recovery='PASS', candidate=remote['candidate'],
                      production_baseline='UNCHANGED', candidate_preloaded_node='datax-03',
                      imports_attempted=remote['imports_attempted'],
                      remaining_gate='MODEL_PROVISIONING_AND_CONTROLLED_ROLLOUT_REVIEW',
                      old_partial_retained_private=True)
    except BaseException as exc:
        result.update(phase=PHASE, error_type=type(exc).__name__, failed_check=safe_error(exc), raw_error_printed=False)
    save('RESULT.json', result)
    _, sha = read(SOURCE/'RESULT.json')
    io.emit(dict(result, report=str(SOURCE/'RESULT.json'), report_sha256=sha,
                 instruction='USE_STATUS_NO_REPEAT_RECOVERY_NO_ROLLOUT'))
    return 0 if result['production_preload_recovery'] == 'PASS' else 1


def source_status():
    records = {}
    if SOURCE.exists():
        io.safe_dir(SOURCE)
        for name in ('STARTED.json', 'REMOTE-REQUESTED.json', 'REMOTE-RESULT.json', 'RESULT.json'):
            if (SOURCE/name).exists():
                row, sha = read(SOURCE/name)
                records[name] = dict(sha256=sha, fields={k: row.get(k) for k in
                    ('production_preload_recovery', 'production_node_preload', 'phase', 'failed_check', 'imports_attempted')})
    io.emit(dict(production_preload_recovery_status='READ_ONLY', records=records, files_modified=False,
                 ssh_connections=0, remote_state_not_requeried=True, production_writes=0, provider_requests=0))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    for mode in ('recover-once', 'status', 'target-receive-once', 'target-status'):
        modes.add_argument('--'+mode, action='store_true')
    args = parser.parse_args(argv)
    target = args.target_receive_once or args.target_status
    need(os.geteuid() == 0 and platform.machine() == 'aarch64' and
         socket.gethostname().split('.')[0] == ('datax-03' if target else 'datax-01'), 'root_arm64_exact_node_required')
    os.umask(0o077)
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    def interrupted(sig, frame):
        raise RuntimeError('interrupted_USE_STATUS')
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP, signal.SIGALRM):
        signal.signal(sig, interrupted)
    signal.alarm(1800)
    try:
        if target:
            return node_main(args.target_status)
        if args.status:
            return source_status()
        with lock(OLD_SOURCE), lock(SOURCE):
            return source_run()
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        io.emit(dict(production_preload_recovery='UNCONFIRMED', error_type=type(exc).__name__,
                     failed_check=safe_error(exc), production_writes=0, ready_for_rollout=False))
        sys.exit(1)

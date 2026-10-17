#!/usr/bin/env python3
"""Prepared, not released: continue only the fixed target's unrequested phase B."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import sys
import tarfile
import types
import uuid

OP = 'abf96dde50174b1eb7caf70a4e6711b1'
PRIOR = 'e8104bf11adc4959ad0b6078e7c97990'
TARGET_CODE_SHA = '2e71b02dd3d26b7d8b6d00824b95170a8e50c7eed0ede99222161ae54aeeafc5'
TARGET_CODE_BYTES = 46761
PROFILE_SHA = 'fdeef64d8c1ced80251f733e0e83cad589032712c1b8a476affc1797105002d2'
ALLOCATION_SHA = '0dd4db700f5c3d5a0a14b23b1d356d7473967df139ce69b1739a3a5448b0484d'
FAIL_SHA = '4a9f9824ca153f962a8d7191342811b03f8d6c9ef799b2f388ca51b5f1d9833a'
FAIL_BYTES = 6151
REPORT_SHA = '3b1c3d86982efd87a5322392276d49170ee50d547f103e61d4c2ef172718af16'
WIRE_SHA = 'b3f08cc0ca0f8cb75488ce37a26531071699f5eb5d9097687604851753d72aa9'
WIRE_BYTES = 122307
SSH_FINGERPRINT = 'SHA256:VIGU1xEuM2WSdXfS2dCu0qqZjSgbl1UeIXDjZEOPWMI'
SEALED_IDENTITY = (64785, 14340598, 0, 0o700)
CONTROL = Path('/var/lib/portal-ppt-native-preload-r3-control-' + OP)
TARGET = Path('/var/lib/portal-ppt-native-preload-r3-target-' + OP)
ROOT = Path('/var/lib')
PUBLIC_KEY = Path('/etc/ssh/ssh_host_ecdsa_key.pub')
ONCE = ROOT / ('portal-ppt-native-phase-b-resume-r1-' + OP + '.STARTED.json')
# Finalization must bind the read-only checkpoint inventory before release.
# Each value is {"sha256": <64 hex>, "bytes": <positive integer>}.
CHECKPOINT_SHA256 = {'FAIL.json': {'bytes': 6151,
               'sha256': '4a9f9824ca153f962a8d7191342811b03f8d6c9ef799b2f388ca51b5f1d9833a'},
 'PHASE-A-PASS.json': {'bytes': 81,
                       'sha256': '820bae21a180e553d059dea5f929ff040ac35a573a705f0c7e88c4f8f22fbfd7'},
 'PHASE-A-REQUESTED.json': {'bytes': 66,
                            'sha256': '1e22bc00cf6ee599125907813433ad1fc84dda4d90511c966658d04153613db3'},
 'PREPARED.json': {'bytes': 2871,
                   'sha256': '94ae14afd5f72e83bd5f0fb9add65f499c32179e473bd3e48b6abc3156036fc5'},
 'RUN-REQUESTED.json': {'bytes': 1098,
                        'sha256': '4aa1f24eee6f123af46b77e3ab22c6ed075f4caf01d0234e2996bdeef0b6f362'},
 'STARTED.json': {'bytes': 1098,
                  'sha256': '4aa1f24eee6f123af46b77e3ab22c6ed075f4caf01d0234e2996bdeef0b6f362'},
 'frontend.PHASE-A-PASS.json': {'bytes': 86,
                                'sha256': '72deb294c168a9d6f69ffae0597504bc33639f8336015a4d3ba4074ea04f54b5'},
 'frontend.PHASE-A-REQUESTED.json': {'bytes': 75,
                                     'sha256': '068f59d9db508e3032f857b27bbb3a582510dc139747aa54f2c01e743fd5fcf4'},
 'frontend.SEALED.json': {'bytes': 175,
                          'sha256': '1f955678a1503615a1bec2336dbf6945b16eea96556b2deca2c74490ac078832'},
 'gateway-forward.PHASE-A-PASS.json': {'bytes': 87,
                                       'sha256': 'b746cd2d7e3e3f944d4846615e583ea958137743212baab7c3f3c0113c5a2a59'},
 'gateway-forward.PHASE-A-REQUESTED.json': {'bytes': 82,
                                            'sha256': '95a299bda0effa1d91d67bd40b20494d46f110cfd372aa85df9310f57901cfbb'},
 'gateway-forward.SEALED.json': {'bytes': 182,
                                 'sha256': '75c2f132a27c6cb349fe3044f81f998b43d0aa38ffa8e4c5a0c2ee97bb8f3dbb'},
 'gateway-rollback-compatible.PHASE-A-PASS.json': {'bytes': 87,
                                                   'sha256': '84911673e43e47436393745c004b7cfa0079097fb740e78f8b052c4e9c1e13de'},
 'gateway-rollback-compatible.PHASE-A-REQUESTED.json': {'bytes': 94,
                                                        'sha256': 'b8bdfd420cdb84bf147dc4f3130348715f6c4a77d426c167a7ac8ff92f88a01e'},
 'gateway-rollback-compatible.SEALED.json': {'bytes': 194,
                                             'sha256': '453ef42adae220209c4134fe95b179714b175d6f63ec8fec57ffb9857b9883f6'},
 'ppt-master-agent-host-forward.PHASE-A-PASS.json': {'bytes': 87,
                                                     'sha256': '72a30f1ad5b3adc842c1c171dff026993b81c6c1639c389961211dd1da11dffc'},
 'ppt-master-agent-host-forward.PHASE-A-REQUESTED.json': {'bytes': 96,
                                                          'sha256': 'c1f357d7be90bb66fa017c1151dd8f25c59dfba83674d73852fc364007f8ea40'},
 'ppt-master-agent-host-forward.SEALED.json': {'bytes': 198,
                                               'sha256': '66be535a6d2c9a560caaaf030f51660f92ed7137e23c1fd02b80450928a850ca'},
 'ppt-master-runner-forward.PHASE-A-PASS.json': {'bytes': 87,
                                                 'sha256': 'b12b03107d5c438e545946dcacf48642934d3df48d2b22369887753893623fc1'},
 'ppt-master-runner-forward.PHASE-A-REQUESTED.json': {'bytes': 92,
                                                      'sha256': 'a0f77bdf84e469dc8d34d7d7b9d4c9109f1d769c3f7ee6ab029691edae09598a'},
 'ppt-master-runner-forward.SEALED.json': {'bytes': 193,
                                           'sha256': '4b018a03c8106cefe698be242a7b661803b0e12dab195ea2a81a448aee28ae4c'},
 'ppt-master-runner-rollback-compatible.PHASE-A-PASS.json': {'bytes': 87,
                                                             'sha256': 'b646fb0db2d017d8d56cc2105dd9e029df0fcb98aeb92f2c9a0542b33ec99711'},
 'ppt-master-runner-rollback-compatible.PHASE-A-REQUESTED.json': {'bytes': 104,
                                                                  'sha256': '992deaed95054f60b570330474ef24e5b34fa7b8758411de45ef44daffab1439'},
 'ppt-master-runner-rollback-compatible.SEALED.json': {'bytes': 205,
                                                       'sha256': 'b7275a3617645c6dd2178fda868495dc665197ed2e1c1805c5f16344f692f466'}}
RELEASE_STATE = 'REVIEWED_FOR_MANUAL_EXECUTION'
INVENTORY_SHA256 = 'ccfb0e3efe75efab543da7a6596fd8137662a30f4a5abbf6f85660f7cac0105a'
INVENTORY_COLLECTED_AT_UTC = '2026-10-17T12:48:42Z'
INVENTORY_SCOPE = 'HISTORICAL_CHECKPOINT_BINDING_NOT_A_FRESH_STORAGE_RESERVATION'


def require(value, code):
    if not value:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode()


def identity(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def private_read(path, limit):
    for parent in (path.parent,) + tuple(path.parent.parents):
        value = parent.lstat()
        require(stat.S_ISDIR(value.st_mode) and value.st_uid == 0 and not value.st_mode & 0o022, 'private_parent_required')
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and before.st_size <= limit, 'private_file_required')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        require(len(raw) <= limit and identity(before) == identity(os.fstat(fd)) == identity(path.lstat()), 'private_file_changed')
        return raw
    finally:
        os.close(fd)


def host_identity():
    require(sys.platform.startswith('linux') and os.geteuid() == 0, 'linux_root_required')
    require(socket.gethostname().split('.')[0] == 'datax-02' and os.uname().machine in ('aarch64', 'arm64'), 'target_host_platform')
    fields = private_read(PUBLIC_KEY, 4096).split()
    require(len(fields) >= 2 and fields[0] == b'ecdsa-sha2-nistp256', 'host_public_key_type')
    fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(base64.b64decode(fields[1], validate=True)).digest()).decode().rstrip('=')
    require(fingerprint == SSH_FINGERPRINT, 'target_host_key_changed')
    return dict(hostname='datax-02', platform='linux/arm64', ssh_public_key_fingerprint=fingerprint,
                kubernetes_node_uid_freshly_checked=False, runtime_preflight_still_required=True)


def load_target():
    path = CONTROL / 'preload_native_r3.py'
    raw = private_read(path, 128 * 1024)
    require(len(raw) == TARGET_CODE_BYTES and sha(raw) == TARGET_CODE_SHA, 'frozen_target_code_changed')
    module = types.ModuleType('fixed_r3_phase_b_target')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    require(module.ALLOCATION_SHA == ALLOCATION_SHA and module.PROFILE_SHA == PROFILE_SHA, 'frozen_target_constants')
    return module, module.load_modules(True)


def inspect_checkpoint(module, profile):
    module.B.safe_dir(TARGET)
    expected = {'STARTED.json', 'PREPARED.json', 'RUN-REQUESTED.json', 'FAIL.json',
                'PHASE-A-REQUESTED.json', 'PHASE-A-PASS.json'}
    for row in profile['images']:
        expected.update(row['role'] + ending for ending in ('.SEALED.json', '.PHASE-A-REQUESTED.json', '.PHASE-A-PASS.json'))
    require(len(profile['images']) == 6 and len(expected) == 24 and set(CHECKPOINT_SHA256) == expected,
            'checkpoint_inventory_not_bound')
    require(set(os.listdir(TARGET)) == expected, 'original_checkpoint_advanced_or_changed')
    receipts = {}
    for name in sorted(expected):
        pin = CHECKPOINT_SHA256[name]
        raw = private_read(TARGET / name, 65536)
        require(len(raw) == pin['bytes'] and sha(raw) == pin['sha256'], 'checkpoint_receipt_changed')
        receipts[name] = module.B.parse(raw)
    require(CHECKPOINT_SHA256['FAIL.json'] == dict(sha256=FAIL_SHA, bytes=FAIL_BYTES), 'fixed_failure_pin')
    failure = receipts['FAIL.json']
    require(failure['operation'] == OP and failure['prior_operation'] == PRIOR
            and failure['profile_sha256'] == PROFILE_SHA and failure['target'] == 'datax-02'
            and failure['status'] == 'INCOMPLETE' and failure['phase_a'] == 'PASS'
            and failure['phase_b'] == 'NOT_REQUESTED' and failure['image_rows'] == []
            and failure['error_code'] == 'r3_storage_budget_insufficient', 'phase_b_boundary_not_proven')
    require(receipts['PHASE-A-REQUESTED.json'] == dict(mode='FULL_OCI_CONTENT_ONLY', automatic_retry=False)
            and receipts['PHASE-A-PASS.json'] == dict(all_fixed_outer_blobs_present=True, all_refs_and_config_bound=True),
            'phase_a_receipts_invalid')
    for row in profile['images']:
        role = row['role']
        require(receipts[role + '.PHASE-A-REQUESTED.json'] == dict(role=role, phase='PHASE-A', automatic_retry=False), 'phase_a_role_request')
        passed = receipts[role + '.PHASE-A-PASS.json']
        require(set(passed) == {'returncode', 'stdout_bytes', 'stderr_bytes', 'seconds'} and passed['returncode'] == 0
                and all(type(passed[key]) is int and 0 <= passed[key] <= 2 * 1024 ** 2 for key in ('stdout_bytes', 'stderr_bytes'))
                and type(passed['seconds']) in (int, float) and 0 <= passed['seconds'] < 2400, 'phase_a_role_pass')
        sealed = receipts[role + '.SEALED.json']
        require(set(sealed) == {'role', 'archive_sha256', 'archive_bytes', 'metadata_only_bytes'}
                and all(sealed[key] == row[key] for key in ('role', 'archive_sha256', 'archive_bytes'))
                and type(sealed['metadata_only_bytes']) is int and 0 < sealed['metadata_only_bytes'] <= 4 * module.B.META_LIMIT + 65536,
                'sealed_receipt_invalid')
    wire = private_read(CONTROL / 'PLAN-WIRE.json', WIRE_BYTES)
    require(len(wire) == WIRE_BYTES and sha(wire) == WIRE_SHA, 'fixed_wire_plan_changed')
    plan = module.unwire_plan(module.B.parse(wire))
    module.validate_plan(profile, plan)
    return plan, receipts


def open_sealed(module, root_path='/'):
    root_fd = os.open(root_path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    opt_fd = sealed_fd = None
    try:
        opt_fd = os.open('opt', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        name = module.state_paths(OP)['sealed_name']
        sealed_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=opt_fd)
        value = os.fstat(sealed_fd)
        require(stat.S_ISDIR(value.st_mode)
                and (value.st_dev, value.st_ino, value.st_uid, stat.S_IMODE(value.st_mode)) == SEALED_IDENTITY,
                'sealed_directory_identity_changed')
        result, sealed_fd = sealed_fd, None
        return result
    finally:
        for fd in (sealed_fd, opt_fd, root_fd):
            if fd is not None:
                os.close(fd)


def metadata_fd(module, sealed_fd, row, image, receipt, maximum):
    size = receipt['metadata_only_bytes']
    require(size <= maximum, 'metadata_tar_exceeds_allocation_bound')
    fd = os.open(row['basename'] + '.metadata.tar', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=sealed_fd)
    try:
        before = module.fd_regular(fd, size)
        wanted = {'oci-layout', 'index.json', 'blobs/sha256/' + row['manifest_digest'][7:],
                  'blobs/sha256/' + row['config_digest'][7:]}
        seen = set()
        os.lseek(fd, 0, os.SEEK_SET)
        with os.fdopen(os.dup(fd), 'rb') as stream:
            with tarfile.open(fileobj=stream, mode='r:') as archive:
                for member in archive:
                    require(member.name in wanted and member.name not in seen and member.isfile()
                            and not member.linkname and not member.pax_headers, 'metadata_exact_four_members')
                    seen.add(member.name)
        require(seen == wanted, 'metadata_exact_four_members')
        small_row = dict(row, archive_bytes=size)
        small_image = dict(image, outer_blobs={dig: image['outer_blobs'][dig] for dig in (row['manifest_digest'], row['config_digest'])})
        # Frozen verifier checks config/manifest hashes, tag, platform and every layer/diff ID.
        module.archive_parts(fd, small_row, small_image)
        require(identity(before) == identity(os.fstat(fd)), 'metadata_file_changed')
        os.lseek(fd, 0, os.SEEK_SET)
        return fd
    except BaseException:
        os.close(fd)
        raise


def continue_phase_b(module, profile, plan, sealed_fd, metadata):
    module.all_content_present(plan)
    module.verify_references(profile)
    module.B.collision_check(profile)
    module.storage_check(profile, plan, 'BEFORE_PHASE_B', sealed_fd)
    module.write_receipt('PHASE-B-REQUESTED.json', dict(mode='ORIGINAL_METADATA_ONLY_OCI_UNPACK_FROM_STORE', automatic_retry=False))
    module.REPORT['phase_b'] = 'REQUESTED'
    for row in profile['images']:
        module.all_content_present(plan)
        module.verify_references(profile)
        module.B.collision_check(profile)
        module.storage_check(profile, plan, 'BEFORE_PHASE_B', sealed_fd)
        module.request_import('PHASE-B', row, metadata[row['role']], profile, plan, sealed_fd)
        receipt, config = module.B.complete_row(row)
        module.REPORT['image_rows'].append(receipt)
        if row['role'] == 'ppt-master-agent-host-forward':
            try:
                module.REPORT['host_runtime_inputs'] = module.B.host_projection(config, row['config_digest'])
            except module.B.Failure as exc:
                module.REPORT['host_runtime_inputs'] = dict(state='NOT_COLLECTED', error_code=exc.code)
        del config
    for row in profile['images']:
        module.B.complete_row(row)
    module.write_receipt('PHASE-B-PASS.json', dict(all_six_complete=True))
    module.REPORT['phase_b'] = 'PASS'
    try:
        module.REPORT['postgres_cache'] = module.B.postgres_cache(profile)
    except Exception as exc:
        module.REPORT['postgres_cache'] = dict(state='NOT_COLLECTED', error_code=exc.code if isinstance(exc, module.B.Failure) else 'METADATA_UNAVAILABLE', scope='LOCAL_NODE_CACHE_ONLY_NOT_CLUSTER_ABSENCE')
    module.REPORT.update(status='COMPLETE_PRELOAD', image_import_may_be_partial=False)


def safe_error(exc, module):
    code = exc.code if module and isinstance(exc, module.B.Failure) else str(exc) if isinstance(exc, ValueError) else 'PHASE_B_CONTINUATION_FAILED'
    return code if re.fullmatch('[A-Za-z0-9_]{1,120}', code) else 'PHASE_B_CONTINUATION_FAILED'


def run():
    module = lock = evidence = sealed_fd = None
    metadata = {}
    result = dict(schema='portal-ppt-native-phase-b-resume.v1', operation=OP, status='INCOMPLETE',
                  prior_report_sha256=REPORT_SHA, prior_target_failure_sha256=FAIL_SHA,
                  target_code_sha256=TARGET_CODE_SHA, original_evidence_preserved=True,
                  automatic_retry=False, ready_for_rollout=False, native_runtime_passed=False,
                  source_archives_copied=False, new_ssh_or_scp=False, existing_images_deleted=False,
                  phase_a_repeated=False, workload_mutation=False, database_access=False, model_access=False,
                  image_import_may_be_partial=None, target_import_requested=None, phase_b=None,
                  state_scope='THIS_PHASE_B_CONTINUATION', prior_phase_a='UNKNOWN_UNTIL_LOCKED_CHECKPOINT_CHECK')
    rc = 1
    try:
        require(RELEASE_STATE == 'REVIEWED_FOR_MANUAL_EXECUTION', 'preparation_not_released')
        def interrupted(signum, frame):
            raise ValueError('interrupted_no_retry')
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, interrupted)
        result['host_identity'] = host_identity()
        os.umask(0o077)
        module, profile = load_target()
        lock = module.common_lock()
        plan, receipts = inspect_checkpoint(module, profile)
        require(not os.path.lexists(ONCE), 'phase_b_continuation_already_started')
        result.update(target_import_requested=False, image_import_may_be_partial=False,
                      phase_b='NOT_REQUESTED', prior_phase_a='PASS')
        sealed_fd = open_sealed(module)
        images = {row['role']: row for row in plan['images']}
        for row in profile['images']:
            metadata[row['role']] = metadata_fd(module, sealed_fd, row, images[row['role']], receipts[row['role'] + '.SEALED.json'], plan['allocation']['maximum_metadata_import_bytes'])
        evidence = module.B.fresh(ROOT / ('portal-ppt-native-phase-b-resume-' + uuid.uuid4().hex))
        result['evidence'] = str(evidence)
        module.B.write_new(ONCE, encoded(dict(operation=OP, evidence=str(evidence), automatic_retry=False)))
        module.STATE = evidence
        module.IMPORT_REQUESTED = False
        module.REPORT = module.base_report(OP)
        module.REPORT.update(phase_a='PASS', phase_a_provenance='ORIGINAL_PHASE_A_EXACT_CHECKPOINT', image_import_may_be_partial=False)
        result.update(image_import_may_be_partial=False, checkpoint_sha256=CHECKPOINT_SHA256)
        module.write_receipt('STARTED.json', result)
        module.B.progress('LOCAL_PHASE_B_CONTINUATION', operation=OP)
        continue_phase_b(module, profile, plan, sealed_fd, metadata)
        result.update(status='COMPLETE_PRELOAD', image_import_may_be_partial=False,
                      target_import_requested=True, phase_b='PASS', target_report=module.REPORT)
        rc = 0
    except (Exception, KeyboardInterrupt) as exc:
        result['error_code'] = safe_error(exc, module)
        if evidence is not None and module is not None and module.REPORT is not None:
            requested = bool(module.IMPORT_REQUESTED)
            result.update(target_import_requested=requested, image_import_may_be_partial=requested,
                          phase_b=module.REPORT['phase_b'])
            module.REPORT.update(status='INCOMPLETE', error_code=result['error_code'],
                                 image_import_may_be_partial=requested, target_import_requested=requested)
            result['target_report'] = module.REPORT
            try:
                module.write_receipt('FAIL.json', module.REPORT)
            except Exception:
                result['failure_write_status'] = 'UNAVAILABLE_OR_ALREADY_EXISTS'
    finally:
        try:
            raw = encoded(result)
            if evidence is not None:
                try:
                    module.B.write_new(evidence / 'REPORT.json', raw)
                except Exception:
                    result['report_write_status'] = 'UNAVAILABLE_OR_ALREADY_EXISTS'
                    raw = encoded(result)
            summary = dict(status=result['status'], operation=OP, report_sha256=sha(raw), report_bytes=len(raw), exitcode=rc,
                           target_import_requested=result['target_import_requested'],
                           image_import_may_be_partial=result['image_import_may_be_partial'], automatic_retry=False)
            if result.get('error_code'):
                summary['error_code'] = result['error_code']
            try:
                require(module is not None, 'report_handoff_module_unavailable')
                summary['download_report'] = module.handoff_report(raw)
            except Exception:
                summary['download_report_status'] = 'HANDOFF_UNAVAILABLE_SAFE_REPORT_PRINTED'
                sys.stdout.buffer.write(raw); sys.stdout.buffer.flush()
            print(json.dumps(summary, sort_keys=True), flush=True)
        finally:
            for fd in list(metadata.values()) + [sealed_fd, lock]:
                if fd is not None:
                    os.close(fd)
    return rc


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resume-phase-b', action='store_true', required=True)
    parser.parse_args()
    raise SystemExit(run())

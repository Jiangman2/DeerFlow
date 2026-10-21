"""One private offline MinerU candidate. No import, credentials, rollout or scale."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
import uuid

from build_resources import check_limits, private_network
from guard_mineru import (CheckFailure, GIB, SpaceGuard, archive_input, available_memory,
                         live_state, require, seal_new_export, verify_package)
from isolated_buildkit import (IsolatedBuildkit, build_arguments, emit, json_receipt,
                               opt_path, run, write_json)
from prepare_oci import fresh_directory

HERE = Path(__file__).resolve().parent
STAGE = 'PREFLIGHT'


def canary_dockerfile(token):
    require(re.fullmatch(r'ppt_runtime_resource_ready_[a-f0-9]{32}', token), 'invalid_canary_token')
    return ('FROM ppt-relay-mineru-base AS candidate\nRUN --network=none '
            '/app/.venv/bin/python -I -S -c "import time; print(\'' + token +
            '\', flush=True); time.sleep(25)"\n')


class Builder(IsolatedBuildkit):
    def __init__(self, package, output, plan):
        self.package, self.output, self.plan = package, output, plan
        # Namespace is required by the frozen bounded-runtime helper; not a PPT workload.
        self.token = 'pptrelay' + uuid.uuid4().hex[:16]
        self.slice, self.cgroup = self.token+'.slice', '/'+self.token+'.slice'
        self.address = 'unix://' + str(output/'buildkit.sock')
        self.units, self.roots = [], {}
        self.daemon_pid, self.device, self.phase_number = None, None, 0
        self.started, self.next_cluster_check = time.monotonic(), 0
        self.production_state = None
        self.binary = {name: shutil.which(name) for name in ('systemctl', 'systemd-run', 'buildkitd', 'buildctl')}
        self.space = SpaceGuard(output.parent)

    def guard(self, source=False, force=False):
        require(time.monotonic()-self.started < 4*3600, 'four_hour_build_deadline')
        self.space.check()
        require(available_memory() >= 4*GIB, 'available_memory_reserve_breached')
        if source or force or time.monotonic() >= self.next_cluster_check:
            verify_package(self.package)
            archive_input(self.plan)
            state = live_state(self.plan, source=source)
            require(self.production_state is None or state == self.production_state, 'production_identity_drift')
            self.production_state = state
            self.next_cluster_check = time.monotonic()+30
        if self.roots:
            check_limits(self.roots, self.cgroup, self.device)
        if self.daemon_pid:
            private_network(self.daemon_pid)

    def phase(self, label, command, canary=False):
        global STAGE
        STAGE = label
        self.guard(force=True)
        emit('mineru_build_progress phase=%s number=%s total=5' % (label, self.phase_number+1))
        return super().phase(label, command, canary=canary)

    def build(self):
        python, locked = Path(sys.executable).resolve(), self.plan['base']
        identity = dict(source_image=locked['image'], source_image_id=locked['config_digest'],
                        archive_sha256=locked['archive_sha256'])
        identity_path = self.output/'mineru-identity.json'
        write_json(identity_path, identity)
        layout = self.output/'mineru-base'
        base = json_receipt(self.phase('stage-mineru', [python, '-B', self.package/'stage_base.py',
            '--archive', archive_input(self.plan), '--output', layout, '--identity', identity_path]), 'audited_oci_base')
        require(all(base[k] == locked[k] for k in ('manifest_digest', 'config_digest', 'compressed_layer_bytes'))
                and base['layers_verified'] == locked['layers'], 'staged_base_identity_changed')
        receipt = self.output/'mineru-base.json'
        write_json(receipt, base)
        write_json(self.output/'BUILD-SPACE.json', self.space.check())

        def arguments(context, **kwargs):
            return build_arguments(self.binary['buildctl'], self.address, context, 'mineru',
                layout, base['manifest_digest'], self.cgroup, **kwargs)

        context = fresh_directory(self.output/'resource-canary')
        token = 'ppt_runtime_resource_ready_' + uuid.uuid4().hex
        (context/'Dockerfile.mineru').write_text(canary_dockerfile(token), encoding='utf-8')
        self.phase('resource-canary', arguments(context)+['--no-cache'], canary=token)
        emit('build_executor_limits=PASS cpu=150% memory=12GiB read=80MiBps write=40MiBps parallelism=1')
        self.phase('source-check-mineru', arguments(self.package, stage='source-check'))
        package_sha = hashlib.sha256((self.package/'PACKAGE.SHA256').read_bytes()).hexdigest()
        image = 'deerflow.local/deer-flow-mineru-adapter:20261015-dedicated-async-r1-fix1-' + package_sha[:16]
        partial, metadata = self.output/'mineru.candidate.oci.tar.partial', self.output/'mineru-build-metadata.json'
        self.phase('candidate-mineru', arguments(self.package, image=image, output=partial, metadata=metadata))
        seal_new_export(partial, self.output, 3*GIB)
        seal_new_export(metadata, self.output, 16*1024**2)
        result = json_receipt(self.phase('verify-mineru', [python, '-B', self.package/'verify_candidate.py',
            '--archive', partial, '--output', self.output/'mineru-candidate-check', '--image', image,
            '--metadata', metadata, '--base', layout, '--base-receipt', receipt,
            '--plan', self.package/'BUILD-PLAN.json']), 'audited_oci_base')
        self.guard(source=True, force=True)
        final = self.output/'mineru.candidate.oci.tar'
        require(not os.path.lexists(str(final)), 'candidate_already_exists')
        partial.rename(final)
        with Path(str(final)+'.sha256').open('x', encoding='ascii') as f:
            f.write(result['archive_sha256']+'  '+final.name+'\n')
            f.flush()
            os.fsync(f.fileno())
        emit('candidate_component=PASS name=mineru-adapter image='+image)
        return dict(mineru_candidate_build='PASS', components={'mineru-adapter': result},
            package_manifest_sha256=package_sha, production_baseline=self.production_state,
            resource_slice=self.slice, network='PRIVATE_DAEMON_ALL_RUN_NONE', database_migrations=[],
            production_workloads_mutated=False, images_imported=False, ready_for_rollout=False,
            credentials_saved=False, concurrency_changed=False, runtime_canary='REQUIRED',
            build_revision='20261015-fix1', candidate_source_mode='0644',
            legacy_source_permissions='0666_EXACT_HASH_AND_METADATA_LOCKED_NO_LIVE_CHMOD',
            business_document_quality='NOT_TESTED', concurrent_64='NOT_TESTED')


def main():
    global STAGE
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True)
    modes = p.add_mutually_exclusive_group(required=True)
    modes.add_argument('--check-only', action='store_true')
    modes.add_argument('--build-only-ack', choices=['YES'])
    args = p.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0 and platform.machine() in ('arm64', 'aarch64')
            and socket.gethostname().split('.')[0] == 'datax-01', 'requires_root_arm64_datax01')
    os.umask(0o077)
    package, output = opt_path(HERE), opt_path(args.output)
    plan = verify_package(package)
    archive = archive_input(plan)
    require(not os.path.lexists(str(output)) and output.parent.is_dir() and output.parent != Path('/opt')
            and package != output and package not in output.parents and output not in package.parents
            and output not in archive.parents, 'requires_new_separate_nested_output')
    for name in ('systemctl', 'systemd-run', 'buildkitd', 'buildctl', 'kubectl', 'findmnt', 'ps'):
        require(shutil.which(name), 'missing_local_tool:'+name)
    require(shutil.which('buildkit-runc') or shutil.which('runc'), 'missing_local_oci_runtime')
    require(available_memory() >= 16*GIB, 'requires_16GiB_available_memory')
    processes = run(['ps', '-eo', 'comm=,args='])
    require(not re.search(r'(?m)^\s*(buildctl|nerdctl)\s|^\s*ctr\s.*\bimages\s+(import|export)\b', processes),
            'other_image_operation_active')
    builder = Builder(package, output, plan)
    builder.guard(source=True, force=True)
    if args.check_only:
        emit(json.dumps(dict(mineru_build_setup='PASS', ready_for_build=True,
            resource_limits_live_verified=False, archive_rehash='DURING_BOUNDED_BUILD',
            build_space=builder.space.check(), production_workloads_mutated=False,
            images_imported=False, ready_for_rollout=False)))
        return
    import fcntl
    lock_path = Path('/run/lock/deerflow-ppt-relay-build.lock')
    for parent in lock_path.parents:
        s = parent.lstat()
        sticky = parent == Path('/run/lock') and s.st_mode & stat.S_ISVTX
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and (not s.st_mode & 0o022 or sticky), 'unsafe_lock_parent')
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        s = os.fstat(lock.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1 and not s.st_mode & 0o022, 'unsafe_build_lock')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        builder.guard(force=True)
        fresh_directory(output)
        write_json(output/'production.before.json', builder.production_state)
        emit('mineru_build_evidence='+str(output))
        def interrupted(signum, frame):
            raise CheckFailure('build_interrupted_stop_owned_units_only')
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, interrupted)
        try:
            try:
                STAGE = 'START_ISOLATED_BUILDKIT'
                builder.start()
                result = builder.build()
            finally:
                builder.stop()
            builder.roots, builder.daemon_pid = {}, None
            builder.guard(source=True, force=True)
        except BaseException as exc:
            write_json(output/'BUILD-FAILED.json', dict(stage=STAGE, error_type=type(exc).__name__,
                failed_check=str(exc) if isinstance(exc, CheckFailure) else None,
                production_workloads_mutated=False, ready_for_rollout=False, automatic_retry=False))
            raise
        result['utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        write_json(output/'BUILD-RESULT.json', result)
        emit('mineru_candidate_build=PASS production_workloads_mutated=no images_imported=no ready_for_rollout=no')
        emit('build_result='+str(output/'BUILD-RESULT.json'))


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(json.dumps(dict(mineru_build='FAIL', stage=STAGE, error_type=type(exc).__name__,
            failed_check=str(exc) if isinstance(exc, CheckFailure) else None,
            raw_error_printed=False, automatic_retry=False, production_workloads_mutated=False,
            images_imported=False, ready_for_rollout=False)), flush=True)
        sys.exit(1)

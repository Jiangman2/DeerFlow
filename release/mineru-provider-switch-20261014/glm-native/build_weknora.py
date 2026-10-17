"""One offline WeKnora GLM candidate; no import, rollout, signal or model call."""
import argparse
import datetime
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import socket
import stat
import sys
import time
import uuid
from build_guard import (GIB, CheckFailure, SpaceGuard, available_memory, checksum,
                         live, package, require, runtime_archive, safe_file)
from build_resources import check_limits, private_network
from isolated_buildkit import IsolatedBuildkit, json_receipt, opt_path, run, write_json
from prepare_oci import fresh_directory

HERE = Path(__file__).resolve().parent
STAGE = 'PREFLIGHT'


def arguments(binary, address, context, cgroup, bases, target='candidate', output=None, image=None, metadata=None):
    args = [binary, '--addr', address, 'build', '--progress=plain', '--frontend', 'dockerfile.v0',
            '--local', 'context='+str(context), '--local', 'dockerfile='+str(context),
            '--opt', 'filename=Dockerfile.weknora', '--opt', 'platform=linux/arm64',
            '--opt', 'force-network-mode=none', '--opt', 'cgroup-parent='+cgroup, '--opt', 'target='+target]
    for name, value in sorted(bases.items()):
        path, digest = value
        args += ['--oci-layout', name+'='+str(path), '--opt', 'context:weknora-glm-'+name+'=oci-layout://'+name+'@'+digest]
    if output is not None:
        if target == 'qualification-evidence':
            args += ['--output', 'type=local,dest='+str(output)]
        else:
            args += ['--output', 'type=oci,oci-mediatypes=true,compression=gzip,name='+image+',dest='+str(output),
                     '--metadata-file', str(metadata)]
    return args


class Builder(IsolatedBuildkit):
    def __init__(self, output, plan):
        self.package, self.output, self.plan = HERE, output, plan
        self.token = 'pptrelay'+uuid.uuid4().hex[:16]  # frozen helper's private namespace
        self.slice, self.cgroup = self.token+'.slice', '/'+self.token+'.slice'
        self.address = 'unix://'+str(output/'buildkit.sock')
        self.units, self.roots = [], {}
        self.daemon_pid, self.device, self.phase_number = None, None, 0
        self.started, self.next_cluster_check = time.monotonic(), 0
        self.binary = {n:shutil.which(n) for n in ('systemctl','systemd-run','buildkitd','buildctl')}
        self.space = SpaceGuard(output.parent)

    def guard(self, source=False, force=False):
        require(time.monotonic()-self.started < 4*3600, 'build_deadline_four_hours')
        self.space.check()
        require(available_memory() >= 4*GIB, 'memory_reserve')
        if source or force or time.monotonic() >= self.next_cluster_check:
            package(HERE)
            runtime_archive(self.plan)
            live(self.plan, binary=source)
            self.next_cluster_check = time.monotonic()+30
        if self.roots:
            check_limits(self.roots, self.cgroup, self.device)
        if self.daemon_pid:
            private_network(self.daemon_pid)

    def phase(self, label, command, canary=False):
        global STAGE
        STAGE = label
        self.guard(force=True)
        return super().phase(label, command, canary=canary)

    def stage_base(self, name, archive, identity):
        identity_path = self.output/(name+'-identity.json')
        write_json(identity_path, identity)
        layout = self.output/name
        receipt = json_receipt(self.phase('stage-'+name, [sys.executable, '-B', HERE/'stage_base.py',
            '--archive', archive, '--output', layout, '--identity', identity_path]), 'audited_oci_base')
        write_json(self.output/(name+'-receipt.json'), receipt)
        return layout, receipt

    def build(self):
        runtime = self.plan['runtime_base']
        base, br = self.stage_base('runtime-base', runtime_archive(self.plan), {
            'source_image':runtime['image'], 'source_image_id':runtime['config_digest'],
            'archive_sha256':runtime['archive_sha256']})
        require(br['manifest_digest'] == runtime['manifest_digest'] and br['layers_verified'] == 20, 'runtime_base_changed')
        builder, gr = self.stage_base('go-base', HERE/'builder.oci.tar', {
            'source_image':self.plan['builder']['image'], 'source_image_id':self.plan['builder']['config_digest'],
            'archive_sha256':self.plan['builder']['archive_sha256']})
        require(gr['manifest_digest'] == self.plan['builder']['manifest_digest'] and gr['layers_verified'] == 7,
                'builder_base_changed')
        bases = {'runtime-base':(base, br['manifest_digest']), 'go-base':(builder, gr['manifest_digest'])}
        def cmd(context, **kw):
            return arguments(self.binary['buildctl'], self.address, context, self.cgroup, bases, **kw)
        canary = fresh_directory(self.output/'canary')
        token = 'ppt_runtime_resource_ready_'+uuid.uuid4().hex
        (canary/'Dockerfile.weknora').write_text('FROM weknora-glm-go-base AS candidate\nRUN --network=none '
            'python3 -I -S -c "import time; print(\''+token+'\',flush=True); time.sleep(25)"\n', encoding='ascii')
        self.phase('resource-canary', cmd(canary)+['--no-cache'], canary=token)
        context = self.output/'context'
        json_receipt(self.phase('stage-context', [sys.executable, '-B', HERE/'stage_context.py',
            '--archive', HERE/'context.tar', '--output', context, '--sha256', self.plan['context_sha256']]), 'context_stage')
        image = 'deerflow.local/weknora-app:20261016-native-glm-r1-'+self.plan['context_sha256'][:16]
        partial, meta = self.output/'weknora.candidate.oci.tar.partial', self.output/'build-metadata.json'
        self.phase('compile-and-runtime-check', cmd(context, output=partial, image=image, metadata=meta))
        # Only files just created by our private BuildKit exporter are sealed.
        for path in (partial, meta):
            s = path.lstat()
            require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1, 'unsafe_new_export')
            fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
            try:
                t = os.fstat(fd)
                require((t.st_dev,t.st_ino) == (s.st_dev,s.st_ino), 'export_raced')
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
        safe_file(partial, 3*GIB)
        proof = self.output/'qualification'
        require(not proof.exists(), 'new_evidence_required')
        self.phase('export-test-evidence', cmd(context, target='qualification-evidence', output=proof))
        result = json_receipt(self.phase('verify-candidate', [sys.executable, '-B', HERE/'verify_native_candidate.py',
            '--archive', partial, '--output', self.output/'candidate-check', '--metadata', meta,
            '--base', base, '--base-receipt', self.output/'runtime-base-receipt.json',
            '--plan', HERE/'BUILD-PLAN.json', '--evidence', proof, '--image', image]), 'audited_oci_base')
        self.guard(source=True, force=True)
        final = partial.with_name('weknora.candidate.oci.tar')
        require(not final.exists(), 'candidate_exists')
        partial.rename(final)
        return {'weknora_glm_candidate_build':'PASS', 'component':result, 'archive':str(final),
                'production_baseline':self.plan['production_baseline'], 'production_workloads_mutated':False,
                'images_imported':False, 'provider_requests':0, 'model_configuration_changed':False,
                'consumer_slots_changed':False, 'database_migrations':[], 'ready_for_rollout':False,
                'native_intranet_acceptance':'REQUIRED', 'enrichment_acceptance':'REQUIRED'}


def main():
    global STAGE
    p = argparse.ArgumentParser()
    p.add_argument('--output', required=True, type=Path)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check-only', action='store_true')
    mode.add_argument('--build-only-ack', choices=['YES'])
    a = p.parse_args()
    require(os.name == 'posix' and os.geteuid() == 0 and platform.machine() in ('aarch64','arm64') and
            socket.gethostname().split('.')[0] == 'datax-01', 'requires_root_arm64_datax01')
    os.umask(0o077)
    opt_path(HERE)
    output = opt_path(a.output)
    require(output.parent == HERE.parent and output.name == 'build1' and not output.exists(), 'new_sibling_build1_required')
    plan = package(HERE)
    require(plan['scope'] == 'WEKNORA_NATIVE_GLM_BUILD_ONLY', 'wrong_scope')
    for name in ('systemctl','systemd-run','buildctl','buildkitd','kubectl','findmnt','ps'):
        require(shutil.which(name), 'missing_tool:'+name)
    require(shutil.which('runc') or shutil.which('buildkit-runc'), 'missing_runtime')
    require(available_memory() >= 16*GIB, 'requires_16GiB_available_memory')
    require(not re.search(r'(?m)^\s*(buildctl|nerdctl)\s|^\s*ctr\s.*\bimages\s+(import|export)\b',
                          run(['ps','-eo','comm=,args='])), 'other_image_operation_active')
    build = Builder(output, plan)
    build.guard(source=True, force=True)
    if a.check_only:
        print(json.dumps({'weknora_build_preflight':'PASS', 'build_started':False,
                          'space':build.space.check(), 'production_writes':0, 'ready_for_rollout':False}), flush=True)
        return
    import fcntl
    path = Path('/run/lock/deerflow-ppt-relay-build.lock')
    for parent in path.parents:
        s = parent.lstat()
        sticky = parent == Path('/run/lock') and s.st_mode & stat.S_ISVTX
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and (not s.st_mode & 0o022 or sticky), 'unsafe_lock_parent')
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'r+') as lock:
        s = os.fstat(lock.fileno())
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1 and not s.st_mode & 0o022, 'unsafe_build_lock')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fresh_directory(output)
        write_json(output/'BASELINE.json', plan['production_baseline'])
        def interrupted(signum, frame):
            raise CheckFailure('interrupted_stop_only_owned_build_units')
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, interrupted)
        try:
            try:
                STAGE = 'START_ISOLATED_BUILDKIT'
                build.start()
                result = build.build()
            finally:
                build.stop()
            build.roots, build.daemon_pid = {}, None
            build.guard(source=True, force=True)
        except BaseException as exc:
            write_json(output/'BUILD-FAILED.json', {'stage':STAGE,'error_type':type(exc).__name__,
                'failed_check':str(exc) if isinstance(exc, CheckFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
                'production_writes':0, 'images_imported':False, 'automatic_retry':False})
            raise
        result['utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        write_json(output/'BUILD-RESULT.json', result)
        print(json.dumps({'weknora_glm_candidate_build':'PASS', 'report':str(output/'BUILD-RESULT.json'),
                          'report_sha256':checksum(output/'BUILD-RESULT.json'), 'ready_for_rollout':False}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'weknora_glm_build':'FAIL', 'stage':STAGE, 'error_type':type(exc).__name__,
              'failed_check':str(exc) if isinstance(exc, CheckFailure) else 'SAFE_DIAGNOSTIC_REQUIRED',
              'production_writes':0, 'images_imported':False, 'provider_requests':0,
              'automatic_retry':False, 'ready_for_rollout':False}), flush=True)
        sys.exit(1)

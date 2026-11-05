"""Opt-in persistent, bounded Buildx lanes on a DEDICATED Docker build host.

Never changes an existing builder, default builder, Kubernetes workload or old
release script. A lane has one local owner, two lanes may build different images.
All callers must use the same pool directory; direct Docker access is privileged
and cannot be fenced by a Python lock. Native systemd BuildKit is NOT this driver.
"""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import time

GIB = 1024 ** 3


def require(ok, check):
    if not ok:
        raise RuntimeError(check)


def docker(*args, timeout=45):
    r = subprocess.run(['docker', *map(str, args)], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=timeout)
    require(r.returncode == 0, 'docker_command_failed:' + args[0])
    require(len(r.stdout) < 8 * GIB // 1024, 'docker_output_oversize')
    return r.stdout.decode('utf-8')


def dedicated_host():
    require(not re.fullmatch(r'datax-\d+|ecs-zdz-pro-\d+', socket.gethostname().split('.')[0])
            and not Path('/etc/kubernetes/admin.conf').exists(), 'dedicated_build_host_required')


def budget(cpu, memory, lanes, lane_cpu, lane_gib, reserve_cpu, reserve_gib, parallelism):
    require(1 <= lanes <= 4 and 1 <= parallelism <= 4, 'bounded_lane_and_step_counts_required')
    require(lane_cpu >= 1 and lane_gib >= 2 and reserve_cpu >= 1 and reserve_gib >= 2,
            'invalid_resource_or_reserve')
    require(lanes * lane_cpu + reserve_cpu <= cpu, 'cpu_budget_exceeds_host')
    require((lanes * lane_gib + reserve_gib) * GIB <= memory, 'memory_budget_exceeds_host')
    return {'cpu_cores': lanes * lane_cpu, 'memory_bytes': int(lanes * lane_gib * GIB),
            'reserve_cpu': reserve_cpu, 'reserve_bytes': int(reserve_gib * GIB)}


def private_root(root):
    require(root.is_dir() and not root.is_symlink(), 'pool_directory_missing_or_link')
    if os.name != 'nt':
        for item in (root, *root.parents):
            s = item.lstat()
            require(stat.S_ISDIR(s.st_mode) and not s.st_mode & 0o022
                    and s.st_uid in (0, os.geteuid()), 'unsafe_pool_ancestor')


@contextmanager
def lock(file):
    # Kernel locks automatically release on process exit; never unlink the inode.
    flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(file, flags, 0o600)
    acquired = False
    try:
        s = os.fstat(fd)
        require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1, 'unsafe_lock_file')
        if s.st_size == 0:
            os.write(fd, b'0')
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
        yield
    finally:
        if acquired:
            if os.name == 'nt':
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def write(file, value):
    with file.open('x', encoding='utf-8', newline='\n') as f:
        json.dump(value, f, indent=2)
        f.write('\n')


def config_text(parallelism):
    require(type(parallelism) is int and 1 <= parallelism <= 4, 'invalid_parallelism')
    return ('[worker.oci]\n  enabled = true\n  max-parallelism = %d\n'
            '  gc = true\n  reservedSpace = "2GB"\n  maxUsedSpace = "20GB"\n'
            '  minFreeSpace = "10GB"\n[worker.containerd]\n  enabled = false\n' % parallelism)


def create_command(name, config, image, cpu, memory):
    require(re.fullmatch(r'deerflow-pool-[a-z0-9-]+', name), 'invalid_builder_name')
    return ['buildx', 'create', '--name', name, '--driver', 'docker-container',
            '--buildkitd-config', str(config), '--driver-opt', 'image=' + image,
            '--driver-opt', 'cpu-period=100000', '--driver-opt', 'cpu-quota=' + str(int(cpu * 100000)),
            '--driver-opt', 'memory=' + str(memory), '--driver-opt', 'memory-swap=' + str(memory),
            '--driver-opt', 'restart-policy=no']


def verify_lane(row):
    info = json.loads(docker('inspect', row['container']))[0]
    require(info['State']['Running'], 'lane_not_running_use_explicit_start')
    cfg = info['HostConfig']
    require(cfg['Memory'] == row['memory_bytes'] and cfg['MemorySwap'] == row['memory_bytes']
            and cfg['CpuPeriod'] == 100000 and cfg['CpuQuota'] == int(row['cpu'] * 100000),
            'lane_resource_drift')
    require(info['Image'] == row['image_id'], 'builder_image_drift')
    actual = docker('exec', row['container'], 'cat', '/etc/buildkit/buildkitd.toml')
    # Buildx may add entitlements to the file. The solver's resource setting must remain exact.
    values = re.findall(r'^\s*max-parallelism\s*=\s*(\d+)\s*$', actual, re.M)
    require(values == [str(row['parallelism'])], 'solver_parallelism_drift')
    return {'name': row['builder'], 'resources': 'PASS', 'max_parallelism': row['parallelism']}


def reject_unmanaged_builders(plan):
    running = set(docker('ps', '--filter', 'name=buildx_buildkit_', '--format', '{{.Names}}').split())
    owned = {r['container'] for r in plan['lanes'].values()}
    require(running <= owned, 'unbudgeted_buildkit_container')


def read_plan(root):
    private_root(root)
    file = root / 'POOL.json'
    require(file.is_file() and not file.is_symlink() and file.stat().st_size < 65536,
            'pool_plan_invalid')
    plan = json.loads(file.read_text(encoding='utf-8'))
    require(plan['version'] == 1, 'pool_version')
    require(docker('context', 'show').strip() == plan['docker_context'], 'docker_context_changed')
    current = json.loads(docker('info', '--format', '{{json .}}'))
    require(current['ID'] == plan['daemon_id'], 'docker_daemon_changed')
    budget(current['NCPU'], current['MemTotal'], len(plan['lanes']), plan['lane_cpu'],
           plan['lane_gib'], plan['reserve_cpu'], plan['reserve_gib'], plan['parallelism'])
    return plan


@contextmanager
def lease(root, lane):
    dedicated_host()
    root = Path(root).absolute()
    plan = read_plan(root)
    require(lane in plan['lanes'], 'unknown_pool_lane')
    # One build per lane; other lanes continue. A snapshot/unique tag/output belongs to each job.
    with lock(root / (lane + '.lock')):
        reject_unmanaged_builders(plan)
        for row in plan['lanes'].values():
            verify_lane(row)
        yield plan['lanes'][lane]['builder']


def run_job(args):
    """Generic lane entry for other images; build success is not runtime acceptance."""
    from frontend_slim import run_build, write_json
    context = args.context.resolve(strict=True)
    recipe = args.dockerfile.resolve(strict=True)
    require(context.is_dir() and recipe.is_file() and recipe.is_relative_to(context),
            'dockerfile_must_be_in_context')
    require(not args.output.exists() and not args.output.is_relative_to(context), 'fresh_external_output_required')
    require(re.fullmatch(r'[a-z0-9][a-z0-9./:-]+:[a-zA-Z0-9_.-]+', args.tag)
            and not args.tag.endswith(':latest'), 'versioned_tag_required')
    # Do not overwrite a candidate tag owned by another operation.
    old = subprocess.run(['docker', 'image', 'inspect', args.tag], stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, timeout=30)
    require(old.returncode != 0, 'candidate_tag_already_exists')
    with lease(args.root, args.lane) as builder:
        args.output.mkdir(mode=0o700, parents=True)
        report = dict(image_build='UNCONFIRMED', builder=builder, image=args.tag,
                      runtime_acceptance='NOT_TESTED', production_writes=0, ready_for_rollout=False)
        write_json(args.output / 'STARTED.json', report)
        try:
            cmd = ['docker', 'buildx', 'build', '--builder', builder, '--platform', args.platform,
                   '--file', str(recipe), '--progress=plain', '--provenance=false',
                   '--metadata-file', str(args.output / 'METADATA.json'), '--tag', args.tag, '--load']
            if args.target:
                cmd += ['--target', args.target]
            for value in args.oci_context:
                name, location = value.split('=', 1)
                directory, digest = location.rsplit('@', 1)
                require(re.fullmatch(r'[a-z][a-z0-9-]{0,40}', name) and
                        re.fullmatch(r'sha256:[a-f0-9]{64}', digest), 'invalid_oci_context')
                layout = Path(directory).resolve(strict=True)
                require((layout / 'oci-layout').is_file() and (layout / 'index.json').is_file(),
                        'oci_layout_missing')
                cmd += ['--build-context', name + '=oci-layout://' + layout.as_posix() + '@' + digest]
            for value in args.build_arg:
                require(re.fullmatch(r'[A-Z][A-Z0-9_]*=[^\r\n]+', value), 'invalid_build_arg')
                require(not re.search(r'KEY|TOKEN|PASSWORD|SECRET|CREDENTIAL', value.split('=', 1)[0]),
                        'credential_build_arg_forbidden_use_secrets_in_reviewed_recipe')
                cmd += ['--build-arg', value]
            report['seconds'] = run_build(cmd + [str(context)], args.output)
            report['image_build'] = 'PASS'
        finally:
            write_json(args.output / 'RESULT.json', report)
            print(json.dumps(report))


def initialize(args):
    dedicated_host()
    require(re.fullmatch(r'[a-z0-9][a-z0-9-]{1,24}', args.name), 'invalid_pool_name')
    require(re.fullmatch(r'[\w./:-]+@sha256:[a-f0-9]{64}', args.buildkit_image), 'pin_buildkit_image')
    lanes = args.lanes.split(',')
    require(len(set(lanes)) == len(lanes) and all(re.fullmatch(r'[a-z][a-z0-9-]{0,15}', x)
            for x in lanes), 'invalid_lane_names')
    current = json.loads(docker('info', '--format', '{{json .}}'))
    require(current['OSType'] == 'linux', 'linux_docker_required')
    limits = budget(current['NCPU'], current['MemTotal'], len(lanes), args.cpu, args.memory_gib,
                    args.reserve_cpu, args.reserve_gib, args.parallelism)
    # This initializes a dedicated pool, not an addition to an unbudgeted collection of builders.
    existing = docker('ps', '-a', '--filter', 'name=buildx_buildkit_', '--format', '{{.ID}}').strip()
    require(not existing, 'existing_buildkit_containers_require_resource_review')
    root = args.root.absolute()
    require(not root.exists() and not root.is_symlink(), 'fresh_pool_directory_required')
    root.mkdir(parents=True, mode=0o700)
    private_root(root)
    plan = dict(version=1, name=args.name, docker_context=docker('context', 'show').strip(),
                daemon_id=current['ID'], buildkit_image=args.buildkit_image, budget=limits,
                lane_cpu=args.cpu, lane_gib=args.memory_gib, reserve_cpu=args.reserve_cpu,
                reserve_gib=args.reserve_gib, parallelism=args.parallelism, lanes={})
    write(root / 'INIT-REQUESTED.json', plan)
    for lane in lanes:
        name = 'deerflow-pool-' + args.name + '-' + lane
        config = root / (lane + '.toml')
        with config.open('x', encoding='utf-8', newline='\n') as f:
            f.write(config_text(args.parallelism))
        docker(*create_command(name, config, args.buildkit_image, args.cpu, int(args.memory_gib * GIB)))
        docker('buildx', 'inspect', name, '--bootstrap', timeout=240)
        container = 'buildx_buildkit_' + name + '0'
        info = json.loads(docker('inspect', container))[0]
        expected = json.loads(docker('image', 'inspect', args.buildkit_image))[0]
        require(info['Image'] == expected['Id'], 'unrecognized_builder_image')
        row = dict(builder=name, container=container, image_id=info['Image'],
                   cpu=args.cpu, memory_bytes=int(args.memory_gib * GIB), parallelism=args.parallelism)
        verify_lane(row)
        plan['lanes'][lane] = row
        write(root / (lane + '-CREATED.json'), row)
    write(root / 'POOL.json', plan)
    print(json.dumps({'build_pool': 'CREATED', 'lanes': list(plan['lanes']), 'budget': limits,
                      'executor_concurrency_test': 'REQUIRED', 'production_writes': 0}))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='operation', required=True)
    init = sub.add_parser('init')
    init.add_argument('--root', type=Path, required=True)
    init.add_argument('--name', required=True)
    init.add_argument('--buildkit-image', required=True)
    init.add_argument('--lanes', default='frontend,backend')
    init.add_argument('--cpu', type=float, default=2)
    init.add_argument('--memory-gib', type=float, default=5)
    init.add_argument('--reserve-cpu', type=float, default=2)
    init.add_argument('--reserve-gib', type=float, default=4)
    init.add_argument('--parallelism', type=int, default=2)
    status = sub.add_parser('status')
    status.add_argument('--root', type=Path, required=True)
    job = sub.add_parser('run')
    job.add_argument('--root', type=Path, required=True)
    job.add_argument('--lane', required=True)
    job.add_argument('--context', type=Path, required=True)
    job.add_argument('--dockerfile', type=Path, required=True)
    job.add_argument('--output', type=Path, required=True)
    job.add_argument('--tag', required=True)
    job.add_argument('--platform', choices=['linux/amd64', 'linux/arm64'], required=True)
    job.add_argument('--target')
    job.add_argument('--build-arg', action='append', default=[])
    job.add_argument('--oci-context', action='append', default=[], help='NAME=ABSOLUTE_LAYOUT@sha256:MANIFEST')
    args = p.parse_args()
    os.umask(0o077)
    if args.operation == 'init':
        initialize(args)
    elif args.operation == 'run':
        run_job(args)
    else:
        plan = read_plan(args.root)
        print(json.dumps({'build_pool': 'RESOURCE_CHECK_PASS', 'budget': plan['budget'],
                          'lanes': [verify_lane(row) for row in plan['lanes'].values()],
                          'production_writes': 0, 'files_modified': False}))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'build_pool': 'UNCONFIRMED', 'error_type': type(exc).__name__,
                          'check': str(exc) if type(exc) is RuntimeError else 'SAFE_DIAGNOSTIC_REQUIRED',
                          'automatic_retry': False, 'production_writes': 0}))
        sys.exit(1)

"""Two bounded synthetic builds; no provider calls, push, or production access.

Consumes the already hash-verified frontend OCI archive, never downloads a base.
Proves actual RUN intervals overlap and checks their bounded cgroup-v2 ancestor
from each parent builder namespace. Does not qualify systemd/cgroup-v1 builds.
"""
import argparse
import concurrent.futures
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import time
import uuid

from build_pool import GIB, dedicated_host, docker, read_plan, require, verify_lane, write
from frontend_slim import audit_oci, capture

PROBE = r"""
const started=Date.now();console.log('POOL_PROBE_START '+JSON.stringify({started}));
setTimeout(()=>console.log('POOL_PROBE_END '+JSON.stringify({started,finished:Date.now()})),20000);
"""

# The RUN namespace hides its bounded ancestors. Inspect the SAME live RUN from
# the parent builder namespace, not from the fixture's private cgroup root.
CGROUP_PROBE = r"""
set -eu
printf 'CPU '; cat /sys/fs/cgroup/cpu.max
printf 'MEM '; cat /sys/fs/cgroup/memory.max
for f in /proc/[0-9]*/cmdline; do
  test -r "$f" || continue
  cmd=$(tr '\000' ' ' < "$f") || continue
  if test "$cmd" = 'node /tmp/pool-probe.cjs '; then
    printf 'RUN '; cat "${f%/cmdline}/cgroup"
  fi
done
"""


def check_executor(row):
    verify_lane(row)
    text = docker('exec', row['container'], 'sh', '-c', CGROUP_PROBE)
    cpu = re.search(r'^CPU (\d+) (\d+)$', text, re.M)
    memory = re.search(r'^MEM (\d+)$', text, re.M)
    require(cpu and memory, 'bounded_parent_cgroup_required')
    require(0 < int(cpu[1]) / int(cpu[2]) <= row['cpu'] and
            0 < int(memory[1]) <= row['memory_bytes'], 'kernel_resource_limit_drift')
    paths = re.findall(r'^RUN 0::(/buildkit/[a-zA-Z0-9_-]+)$', text, re.M)
    require(len(paths) <= 1, 'unexpected_concurrent_probe')
    return bool(paths)


def run(args):
    dedicated_host()
    plan = read_plan(args.pool)
    require(len(plan['lanes']) >= 2, 'two_lanes_required')
    require(not args.out.exists(), 'fresh_output_required')
    image = json.loads(capture(['docker', 'image', 'inspect', args.image]))[0]
    receipt = audit_oci(args.archive, image)
    args.out.mkdir(parents=True, mode=0o700)
    write(args.out / 'BASE.json', receipt)
    layout = args.out / 'layout'; layout.mkdir()
    # audit_oci has rejected paths/links and verified layer bytes. Only this layout is written.
    with tarfile.open(args.archive, 'r:') as tar:
        for item in tar.getmembers():
            target = layout / item.name
            require(target.resolve().is_relative_to(layout.resolve()), 'layout_escape')
            if item.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open('xb') as dst, tar.extractfile(item) as src:
                    while True:
                        data = src.read(1024 * 1024)
                        if not data: break
                        dst.write(data)
    manifest = json.loads((layout / 'index.json').read_text())['manifests'][0]['digest']
    token = uuid.uuid4().hex[:12]
    scripts = Path(__file__).resolve().parent
    def job(lane):
        context = args.out / (lane + '-context'); context.mkdir()
        (context / 'probe.cjs').write_text(PROBE, encoding='utf-8')
        recipe = ('FROM fixturebase\nCOPY probe.cjs /tmp/pool-probe.cjs\n'
                  'ENV EXPECT_CPU=%s EXPECT_MEMORY=%s\n'
                  'RUN --network=none node /tmp/pool-probe.cjs\n' %
                  (plan['lanes'][lane]['cpu'], plan['lanes'][lane]['memory_bytes']))
        (context / 'Dockerfile').write_text(recipe, encoding='utf-8')
        output = args.out / (lane + '-build')
        cmd = [sys.executable, '-B', str(scripts / 'build_pool.py'), 'run', '--root', str(args.pool),
               '--lane', lane, '--context', str(context), '--dockerfile', str(context / 'Dockerfile'),
               '--output', str(output), '--tag', 'deerflow-pool-probe:' + token + '-' + lane,
               '--platform', 'linux/' + image['Architecture'], '--oci-context',
               'fixturebase=' + str(layout) + '@' + manifest]
        with (args.out / (lane + '-control.log')).open('xb') as log:
            process = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
            start, bounded_samples = time.monotonic(), 0
            try:
                while process.poll() is None:
                    require(time.monotonic() - start < 300, 'parallel_probe_deadline')
                    if check_executor(plan['lanes'][lane]):
                        bounded_samples += 1
                    time.sleep(1)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=15)
        require(process.returncode == 0, 'lane_probe_failed:' + lane)
        require(bounded_samples >= 3, 'three_live_bounded_executor_samples_required:' + lane)
        text = (output / 'BUILD.log').read_text(encoding='utf-8')
        lines = re.findall(r'^#\d+\s+[\d.]+\s+POOL_PROBE_END (\{[^\n]+\})$', text, re.M)
        require(len(lines) == 1, 'exact_run_receipt_required:' + lane)
        row = json.loads(lines[0]); row['lane'] = lane
        row['bounded_executor_samples'] = bounded_samples
        return row
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        rows = list(executor.map(job, list(plan['lanes'])[:2]))
    overlap = min(r['finished'] for r in rows) - max(r['started'] for r in rows)
    require(overlap >= 5000, 'actual_run_overlap_not_proven')
    result = dict(parallel_image_build='PASS', actual_run_overlap_ms=overlap, lanes=rows,
                  native_systemd_cgroup_v1='NOT_TESTED', production_writes=0)
    write(args.out / 'RESULT.json', result)
    print(json.dumps(result))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pool', type=Path, required=True)
    p.add_argument('--archive', type=Path, required=True)
    p.add_argument('--image', required=True)
    p.add_argument('--out', type=Path, required=True)
    run(p.parse_args())

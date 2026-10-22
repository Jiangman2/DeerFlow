"""Generate a credential-free one-Service CAS switch package; no cluster access."""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile

HERE = Path(__file__).absolute().parent
NAME = 'mineru-bluegreen16-switch-20261015-r1'
MEMBERS = ('switch_bluegreen16.py', 'switch_route_probe.py', 'stage_bluegreen16.py',
           'prepare_bluegreen16.py', 'stage_runtime_probe.py', 'SWITCH-RUNBOOK.txt')
LOCKS = {
    'stage_bluegreen16.py': '7818d32892d0bb51dd249f6c430dd27a32304972f798d40104ef383c6c2f83af',
    'prepare_bluegreen16.py': '1064d935beb33a5e1dc2f741d1f9474fa4cfcc29ac17d6529d5dcca56e8292b4',
    'stage_runtime_probe.py': 'a5e23fa8b381c6443c86ceabba0e19db14d5a22072a04962d86da1f651e6f503',
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    destination = Path(args.output).absolute()
    assert not destination.exists(), 'never_replace_a_delivered_package'
    test = subprocess.run([sys.executable, '-B', '-m', 'unittest', 'test_switch_bluegreen16',
        'test_recover_stage_permissions', 'test_stage_bluegreen16', 'test_prepare_bluegreen16', '-q'],
        cwd=str(HERE), capture_output=True, text=True, timeout=60)
    assert test.returncode == 0 and 'Ran 137 tests' in test.stderr and 'OK' in test.stderr, test.stderr
    data = {name: (HERE/name).read_bytes() for name in MEMBERS}
    for name, digest in LOCKS.items():
        assert sha(data[name]) == digest, 'frozen_source_changed:'+name
    assert data['switch_bluegreen16.py'].count(b'__ROUTE_PROBE_SHA256__') == 1
    data['switch_bluegreen16.py'] = data['switch_bluegreen16.py'].replace(
        b'__ROUTE_PROBE_SHA256__', sha(data['switch_route_probe.py']).encode('ascii'))
    for name, content in data.items():
        if name.endswith('.py'):
            ast.parse(content, feature_version=(3, 7))
    namespace = {'__name__': 'packaged_switch_check', '__file__': str(HERE/'switch_bluegreen16.py')}
    exec(compile(data['switch_bluegreen16.py'], '<packaged-switch>', 'exec'), namespace)
    assert namespace['ROUTE_PROBE_SHA'] == sha(data['switch_route_probe.py'])
    assert namespace['STAGED_SHA'] == '344c3b216135b994344c9aaa88c3797def297b6a1b6141ae9ef194941b558975'
    verification = dict(revision=NAME, scope='LOCAL_TESTS_NOT_PRODUCTION_ACCEPTANCE',
        local_tests_passed=137, local_tests_failed=0, python37_grammar='PASS', bash_syntax='CHECKED_BY_PACKAGER',
        runtime='Windows Python3.11', kubernetes_runtime='PENDING_OPERATOR_EXECUTION',
        tested_boundaries=['PINNED_STAGE_BINDINGS', 'EXACT_UID_VERSION_FULL_SPEC_CAS',
            'SINGLE_SELECTOR_WRITE', 'UNKNOWN_OUTCOME_NO_RETRY', 'OLD_BUSY_QUEUE_ALLOWED',
            'EXPLICIT_ROUTE_ONLY_ROLLBACK', 'NO_DRAIN_OR_FINAL16_CLAIM', 'STATUS_READ_ONLY'],
        candidate_image_modified=False, frozen_stage_implementation='BYTE_FOR_BYTE_UNCHANGED',
        credentials_included=False, upgrade_workspace_modified=False,
        production_writes_by_packager=0, provider_requests=0,
        new_pool_initial_slots=6, old_reserved_slots=10, local_slot_ceiling=16,
        final16_expansion=False, business_document_acceptance='REQUIRED_AFTER_SWITCH',
        files={name: sha(content) for name, content in data.items()})
    data['SWITCH-VERIFICATION.json'] = (json.dumps(verification, sort_keys=True, indent=2)+'\n').encode()
    data['PACKAGE.SHA256'] = ''.join(sha(data[n])+'  '+n+'\n' for n in sorted(data)).encode('ascii')
    destination.mkdir(parents=True, exist_ok=False)
    archive = destination/(NAME+'.tar.gz')
    with tarfile.open(archive, 'w:gz', format=tarfile.USTAR_FORMAT) as tar:
        for name, content in sorted(data.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.uid, info.gid, info.mtime = len(content), 0o600, 0, 0, 0
            info.uname = info.gname = 'root'
            tar.addfile(info, io.BytesIO(content))
    archive_sha = sha(archive.read_bytes())
    starter = '''#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
test "$(id -u)" -eq 0
test "$(hostname -s)" = datax-01
cd /home/cnooc
PACKAGE=__PACKAGE__
test -f "$PACKAGE" && test ! -L "$PACKAGE"
test -d /var/lib && test ! -L /var/lib
test "$(stat -c '%u:%a' /var/lib)" = '0:755'
TOOL=$(mktemp -d /var/lib/mineru-bluegreen16-switch-tool-XXXXXXXX)
install -o root -g root -m 0600 "$PACKAGE" "$TOOL/package.tar.gz"
test "$(sha256sum "$TOOL/package.tar.gz" | awk '{print $1}')" = __ARCHIVE_SHA__
tar --no-same-owner --no-same-permissions -xzf "$TOOL/package.tar.gz" -C "$TOOL"
cd "$TOOL"
sha256sum -c PACKAGE.SHA256
printf 'switch_tool=%s/switch_bluegreen16.py\\n' "$TOOL"
printf 'switch_log=%s/switch.log\\n' "$TOOL"
printf 'status_command=sudo python3 -I -B %s/switch_bluegreen16.py --status\\n' "$TOOL"
set +e
python3 -I -B -u "$TOOL/switch_bluegreen16.py" --switch 2>&1 | tee "$TOOL/switch.log"
RESULT=("${PIPESTATUS[@]}")
set -e
printf 'switch_exit_code=%s tee_exit_code=%s\\n' "${RESULT[0]}" "${RESULT[1]}"
printf 'switch_log=%s/switch.log\\n' "$TOOL"
printf 'status_command=sudo python3 -I -B %s/switch_bluegreen16.py --status\\n' "$TOOL"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
'''.replace('__PACKAGE__', archive.name).replace('__ARCHIVE_SHA__', archive_sha)
    start = destination/('START-'+NAME+'.sh')
    start.write_bytes(starter.encode('ascii'))
    subprocess.run(['C:/Program Files/Git/bin/bash.exe', '-n', str(start)], check=True, timeout=20)
    (destination/'SWITCH-VERIFICATION.json').write_bytes(data['SWITCH-VERIFICATION.json'])
    (destination/'SWITCH-RUNBOOK.txt').write_bytes(data['SWITCH-RUNBOOK.txt'])
    with tarfile.open(archive, 'r:gz') as tar:
        assert set(tar.getnames()) == set(data) and len(tar.getmembers()) == len(data)
        for member in tar.getmembers():
            assert member.isfile() and member.mode == 0o600 and '/' not in member.name
            assert tar.extractfile(member).read() == data[member.name]
    print(json.dumps(dict(archive=str(archive), archive_sha256=archive_sha, bytes=archive.stat().st_size,
        starter=str(start), starter_sha256=sha(start.read_bytes()), members=len(data),
        tests_passed=137, bash_syntax='PASS', credentials_included=False)))


if __name__ == '__main__':
    main()

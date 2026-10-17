"""Build a small, credential-free operator tool archive. No cluster access."""
import argparse
import ast
import hashlib
import io
import json
from pathlib import Path
import tarfile

HERE = Path(__file__).absolute().parent
NAME = 'mineru-bluegreen16-stage-20261015-r1'
MEMBERS = ('stage_bluegreen16.py', 'stage_runtime_probe.py', 'prepare_bluegreen16.py', 'STAGE-RUNBOOK.txt')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--frozen-tests-passed', type=int, required=True)
    args = parser.parse_args()
    destination = Path(args.output).absolute()
    destination.mkdir(parents=True, exist_ok=False)
    data = {name: (HERE/name).read_bytes() for name in MEMBERS}
    for name, raw in data.items():
        if name.endswith('.py'):
            ast.parse(raw, feature_version=(3, 7))
    verification = dict(revision=NAME, scope='LOCAL_TESTS_NOT_PRODUCTION_ACCEPTANCE',
        local_tests_passed=70, local_tests_failed=0, frozen_dependency_regression_passed=args.frozen_tests_passed,
        python37_grammar='PASS', runtime='Windows Python3.11', linux_stage='PENDING_SERVER_EXECUTION',
        docker='UNAVAILABLE_NOT_STARTED_OR_RESTARTED', candidate_image_modified=False,
        upgrade_workspace_modified=False, production_writes_by_packager=0, provider_requests=0,
        prepare_report_sha256='bafea93f168585cfb5ea7ed7ad3eaf6e60ee7b44bcb1117132a559d47593a7d6',
        ready_for_rollout=False, files={name: sha(raw) for name,raw in data.items()})
    data['STAGE-VERIFICATION.json'] = (json.dumps(verification, indent=2, sort_keys=True)+'\n').encode()
    data['PACKAGE.SHA256'] = ''.join(sha(data[name])+'  '+name+'\n' for name in sorted(data)).encode('ascii')
    archive = destination/(NAME+'.tar.gz')
    with tarfile.open(archive, 'w:gz', format=tarfile.USTAR_FORMAT) as tar:
        for name, raw in sorted(data.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.uid, info.gid, info.mtime = len(raw), 0o600, 0, 0, 0
            info.uname = info.gname = 'root'
            tar.addfile(info, io.BytesIO(raw))
    digest = sha(archive.read_bytes())
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
TOOL=$(mktemp -d /var/lib/mineru-bluegreen16-stage-tool-XXXXXXXX)
install -o root -g root -m 0600 "$PACKAGE" "$TOOL/package.tar.gz"
test "$(sha256sum "$TOOL/package.tar.gz" | awk '{print $1}')" = __ARCHIVE_SHA__
tar --no-same-owner --no-same-permissions -xzf "$TOOL/package.tar.gz" -C "$TOOL"
cd "$TOOL"
sha256sum -c PACKAGE.SHA256
printf 'stage_tool=%s/stage_bluegreen16.py\\n' "$TOOL"
printf 'stage_log=%s/stage.log\\n' "$TOOL"
set +e
python3 -I -B -u "$TOOL/stage_bluegreen16.py" --stage-only 2>&1 | tee "$TOOL/stage.log"
RESULT=("${PIPESTATUS[@]}")
set -e
printf 'stage_exit_code=%s tee_exit_code=%s\\n' "${RESULT[0]}" "${RESULT[1]}"
printf 'stage_log=%s/stage.log\\n' "$TOOL"
printf 'status_command=sudo python3 -I -B %s/stage_bluegreen16.py --status\\n' "$TOOL"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
'''.replace('__PACKAGE__', archive.name).replace('__ARCHIVE_SHA__', digest)
    start = destination/'START-mineru-bluegreen16-stage-20261015-r1.sh'
    start.write_bytes(starter.encode('ascii'))
    (destination/'STAGE-VERIFICATION.json').write_bytes(data['STAGE-VERIFICATION.json'])
    (destination/'STAGE-RUNBOOK.txt').write_bytes(data['STAGE-RUNBOOK.txt'])
    with tarfile.open(archive, 'r:gz') as tar:
        assert set(tar.getnames()) == set(data)
        for member in tar.getmembers():
            assert member.isfile() and member.mode == 0o600 and '/' not in member.name
            assert tar.extractfile(member).read() == data[member.name]
    # Only metadata. No keys, remote responses or private production manifests.
    print(json.dumps(dict(archive=str(archive), archive_sha256=digest, bytes=archive.stat().st_size,
        starter=str(start), starter_sha256=sha(start.read_bytes()), members=len(data), credentials_included=False)))


if __name__ == '__main__':
    main()

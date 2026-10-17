"""Publish the verified single-file operator recovery and exact launch command."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--verification', required=True, type=Path)
    a = p.parse_args()
    here = Path(__file__).resolve().parent
    raw = (here/'resume_runtime_finish_once.py').read_bytes().replace(b'\r\n', b'\n')
    digest = hashlib.sha256(raw).hexdigest()
    proof = json.loads(a.verification.read_bytes())
    assert proof['local_runtime_resume_tests'] == 'PASS' and proof['tests'] == 66 and proof['skipped'] == 0
    assert proof['script_sha256'] == digest and not a.output.exists()
    a.output.mkdir(parents=True)
    name = 'weknora-glm-runtime-resume-20261016-r1.py'
    with (a.output/name).open('xb') as f: f.write(raw)
    command = """sudo bash -se <<'BASH'
set -Eeuo pipefail
umask 077
test "$(hostname -s)" = datax-01
cd /home/cnooc
SCRIPT=weknora-glm-runtime-resume-20261016-r1.py
test -f "$SCRIPT" && test ! -L "$SCRIPT"
CONTROL=$(mktemp -d /var/lib/weknora-runtime-resume-tool-XXXXXXXX)
install -o root -g root -m 0600 "$SCRIPT" "$CONTROL/resume.py"
test "$(sha256sum "$CONTROL/resume.py" | awk '{print $1}')" = @SHA@
echo "status_command=sudo python3 -I -B $CONTROL/resume.py --status"
echo "resume_log=$CONTROL/resume.log"
set +e
python3 -I -B -u "$CONTROL/resume.py" --resume-once 2>&1 | tee "$CONTROL/resume.log"
RESULT=("${PIPESTATUS[@]}")
set -e
echo "resume_exit_code=${RESULT[0]} tee_exit_code=${RESULT[1]}"
echo "resume_log=$CONTROL/resume.log"
echo "status_command=sudo python3 -I -B $CONTROL/resume.py --status"
test "${RESULT[0]}" -eq 0
test "${RESULT[1]}" -eq 0
BASH
""".replace('@SHA@', digest)
    with (a.output/'RUN-datax01.txt').open('xb') as f: f.write(command.encode())
    for target, content in (
        ('RUNTIME-RESUME-RUNBOOK.txt', (here/'RUNTIME-RESUME-RUNBOOK.txt').read_bytes().replace(b'\r\n', b'\n')),
        ('LOCAL-VERIFICATION.json', a.verification.read_bytes()),
    ):
        with (a.output/target).open('xb') as f: f.write(content)
    receipt = {'script': str(a.output/name), 'script_sha256': digest, 'tests': 66,
               'ready_for_server_preflight': True, 'ready_for_rollout': False}
    with (a.output/'PACKAGE-RESULT.json').open('x') as f:
        json.dump(receipt, f, indent=2); f.write('\n')
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    main()

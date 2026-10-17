"""Run offline source and byte-exact delivery tests in two disposable runtimes."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import package

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent


def run(command):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120)
    if result.returncode:
        sys.stdout.buffer.write(result.stdout)
        raise RuntimeError('offline_qualification_failed')
    if b'Ran 52 tests' not in result.stdout or b'\nOK\n' not in result.stdout:
        raise RuntimeError('unexpected_test_result')
    return result.stdout


def main():
    dest = Path(sys.argv[1]).resolve()
    raw = (dest/package.NAME).read_bytes()
    if raw != package.payload(): raise RuntimeError('delivered_bytes_changed')
    images = ('python:3.7.17-slim-bullseye', 'python:3.12-slim-bookworm')
    proof = []
    for image in images:
        for mode in ('source', 'delivery'):
            command = ['docker','run','--rm','--network','none',
                '-v',str(REPO)+':/repo:ro','-v',str(dest)+':/delivery:ro',
                '-w','/repo/release/'+HERE.name]
            if mode == 'delivery': command += ['-e','DIAGNOSTIC=/delivery/'+package.NAME]
            command += [image,'python','-B','-m','unittest','-v','test_diagnose']
            output = run(command)
            name = 'TESTS-'+image.split(':')[1].split('-')[0]+'-'+mode+'.txt'
            (dest/name).write_bytes(output)
            proof.append(dict(runtime=image, target=mode, tests=52, result='PASS', skips=0,
                              log=name, log_sha256=hashlib.sha256(output).hexdigest()))
            print(json.dumps(proof[-1]), flush=True)
    # Validate the embedded Redis shell and the actual launcher without executing either.
    spec = {}; exec(compile(raw, package.NAME, 'exec'), spec)
    syntax = subprocess.run(['docker','run','--rm','-i','--network','none',images[0],'bash','-n'],
        input=spec['MODEL_SHELL'].encode()+b'\n'+(dest/'RUN-DATAX01.txt').read_bytes(),
        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=30)
    if syntax.returncode: raise RuntimeError('shell_syntax_failed')
    # A wrong-host run must stop before reading any helper or opening a connection.
    for image in images:
        refuse = subprocess.run(['docker','run','--rm','--network','none','-v',str(dest)+':/delivery:ro',
            image,'python','-I','-B','/delivery/'+package.NAME,'--observe'],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
        if refuse.returncode != 1 or b'root_datax01_required' not in refuse.stdout or refuse.stderr:
            raise RuntimeError('wrong_host_refusal_failed')
    result = dict(script_sha256=hashlib.sha256(raw).hexdigest(), tests=proof, test_executions=208,
        shell_syntax='PASS', wrong_host_refusal='PASS_BOTH_RUNTIMES',
        real_linux_subprocess_streaming='PASS_SYNTHETIC_ONLY', real_cgroup_probe='PASS_LOCAL_CONTAINER',
        v1_and_v2_mount_mapping='PASS_SYNTHETIC_FIXTURES',
        orchestration_identity_drift='PASS_MOCKED_CLUSTER', frozen_loader_integration='MOCKED_NO_PRODUCTION_CALLS',
        actual_production_helpers_and_sql_execution='NOT_EXECUTED_LOCALLY',
        production_writes=0, provider_requests=0, ready_to_expand=False)
    (dest/'LOCAL-QUALIFICATION.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    sums=''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n'
                 for p in sorted(dest.iterdir()) if p.is_file() and p.name != 'SHA256SUMS')
    (dest/'SHA256SUMS').write_text(sums,encoding='ascii')
    print(json.dumps(result))


if __name__ == '__main__': main()

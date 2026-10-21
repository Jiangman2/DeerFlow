"""Read-only replay of the pinned build package's --check-only. Python 3.7+."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import traceback

PACKAGE = Path('/opt/mineru-build-CFQ08TFu/package')
OUTPUT = PACKAGE.parent/'build1'
MANIFEST = '2d6415d645b806b4ba5faacf2a915f4a6636b52f428d28848f352a6cfdc5c4ee'
LAST = {'step': 'PACKAGE_SAFETY'}


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def require(value, label):
    if not value:
        raise RuntimeError(label)


def pinned_package():
    for p in (PACKAGE,)+tuple(PACKAGE.parents):
        s = p.lstat()
        require(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'unsafe_package_parent')
    def read(name):
        p = PACKAGE/name
        s = p.lstat()
        require(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
                and not s.st_mode & 0o022 and 0 < s.st_size < 8*1024**2, 'unsafe_package_member')
        return p.read_bytes()
    raw = read('PACKAGE.SHA256')
    require(hashlib.sha256(raw).hexdigest() == MANIFEST, 'package_manifest_changed')
    names = set()
    for line in raw.decode('ascii').splitlines():
        m = re.fullmatch(r'([a-f0-9]{64})  ([A-Za-z0-9][A-Za-z0-9_.-]*)', line)
        require(m is not None, 'invalid_package_manifest')
        digest, name = m.groups()
        require(name not in names and hashlib.sha256(read(name)).hexdigest() == digest, 'package_member_changed')
        names.add(name)
    require({p.name for p in PACKAGE.iterdir()} == names | {'PACKAGE.SHA256'}, 'unexpected_package_member')
    require(not os.path.lexists(str(OUTPUT)), 'build_output_exists_do_not_replay')


def error_classes(raw):
    value = raw.decode('utf-8', errors='replace').lower()
    labels = {
        'permission': ('permission denied', 'forbidden', 'unauthorized'),
        'not_found': ('not found', 'no such file', 'notfound'),
        'timeout': ('timeout', 'timed out', 'deadline exceeded'),
        'network': ('connection refused', 'no route to host', 'unable to connect', 'i/o timeout'),
        'dns': ('no such host', 'name resolution'),
        'tls': ('x509:', 'certificate verify failed', 'tls handshake'),
        'invalid_argument': ('unknown flag', 'unrecognized option', 'unknown shorthand flag'),
        'remote_assertion': ('assertionerror',),
        'remote_python': ('traceback (most recent call last)',),
        'output_limit': ('exceeds bound',),
    }
    return sorted(k for k, words in labels.items() if any(w in value for w in words))


def command_summary(args):
    args = [str(x) for x in args]
    program = Path(args[0]).name
    if program == 'ps':
        require(args[1:] == ['-eo', 'comm=,args='], 'diagnostic_disallowed_process_command')
        return {'step': 'READ_PROCESS_TABLE', 'program': program}
    require(program == 'kubectl' and args[1:4] == ['--request-timeout=20s', '-n', 'deer-flow'],
            'diagnostic_disallowed_command')
    operation = args[4]
    if operation == 'get':
        require(args[5] in ('node', 'deployment', 'replicasets', 'pods')
                and args[-3:] == ['--show-managed-fields=false', '-o', 'json'], 'diagnostic_disallowed_get')
        return {'step': 'KUBERNETES_GET', 'program': program, 'resource': args[5],
                'target': args[6] if not args[6].startswith('--') else 'SELECTOR_SCOPED'}
    require(operation == 'exec' and len(args) == 13 and args[6] == '-c'
            and args[7] in ('adapter', 'dispatcher') and args[8:12] ==
            ['--', '/app/.venv/bin/python', '-B', '-c'], 'diagnostic_disallowed_exec')
    return {'step': 'READ_ADAPTER_SOURCE', 'program': program, 'pod': args[5], 'container': args[7]}


def traced_run(original, args, *pos, **kw):
    global LAST
    LAST = command_summary(args)
    emit(dict(LAST, operation_state='START'))
    result = original(args, *pos, **kw)
    emit(dict(LAST, operation_state='PASS' if result.returncode == 0 else 'FAIL',
        exit_code=result.returncode, stdout_bytes=len(result.stdout or b''),
        stderr_bytes=len(result.stderr or b''), stderr_classes=error_classes(result.stderr or b''),
        raw_output_printed=False))
    return result


def source_metadata_probe(original, summary):
    """One extra source-file metadata read ONLY after a failed source exec."""
    code = '''import json,os,stat
p="/app/deerflow_mineru_adapter.py"
s=os.lstat(p)
print(json.dumps(dict(uid=s.st_uid,gid=s.st_gid,mode=oct(stat.S_IMODE(s.st_mode)),links=s.st_nlink,
bytes=s.st_size,regular_file=stat.S_ISREG(s.st_mode),symlink=stat.S_ISLNK(s.st_mode))))
'''
    args = ['kubectl', '--request-timeout=20s', '-n', 'deer-flow', 'exec', summary['pod'],
            '-c', summary['container'], '--', '/app/.venv/bin/python', '-B', '-c', code]
    env = dict(os.environ, KUBECONFIG='/etc/kubernetes/admin.conf')
    for key in ('SSL_CERT_FILE', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE'):
        env.pop(key, None)
    result = original(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, env=env)
    row = {'source_metadata_probe': 'UNCONFIRMED', 'pod': summary['pod'], 'exit_code': result.returncode,
           'stderr_classes': error_classes(result.stderr), 'raw_output_printed': False}
    if result.returncode == 0 and len(result.stdout) <= 4096:
        value = json.loads(result.stdout)
        allowed = {'uid', 'gid', 'mode', 'links', 'bytes', 'regular_file', 'symlink'}
        require(set(value) == allowed and all(type(value[k]) is int for k in ('uid', 'gid', 'links', 'bytes'))
                and re.fullmatch(r'0o[0-7]{1,6}', value['mode'])
                and all(type(value[k]) is bool for k in ('regular_file', 'symlink')), 'unsafe_metadata_receipt')
        row.update(source_metadata_probe='PASS', metadata=value)
    emit(row)


def main():
    global LAST
    require(os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01', 'requires_root_datax01')
    pinned_package()
    emit({'package_integrity': 'PASS', 'build_output_exists': False, 'mode': 'READ_ONLY_CHECK_ONLY'})
    sys.path.insert(0, str(PACKAGE))
    import build_mineru as b
    import guard_mineru as g
    original = subprocess.run
    old_argv = sys.argv
    subprocess.run = lambda args, *pos, **kw: traced_run(original, args, *pos, **kw)
    try:
        sys.argv = ['build_mineru.py', '--check-only', '--output', str(OUTPUT)]
        b.main()
        emit({'preflight_diagnostic': 'PASS_NOW', 'previous_failure': 'NOT_RECLASSIFIED_AS_SUCCESS'})
    except Exception as exc:
        frames = [{'file': Path(f.filename).name, 'function': f.name, 'line': f.lineno}
                  for f in traceback.extract_tb(exc.__traceback__)[-8:]]
        message = str(exc)
        safe_message = (message if isinstance(exc, g.CheckFailure) or re.fullmatch(
            r'Read/control command failed: (kubectl|ps) exit=[0-9]{1,3}|Control command response exceeds bound', message)
            else None)
        emit({'preflight_diagnostic': 'FAILED_CHECK_LOCATED', 'last_operation': LAST,
              'error_type': type(exc).__name__, 'safe_detail': safe_message, 'code_frames': frames,
              'raw_error_printed': False})
        if LAST.get('step') == 'READ_ADAPTER_SOURCE':
            try:
                source_metadata_probe(original, LAST)
            except Exception as detail_exc:
                emit({'source_metadata_probe': 'INCOMPLETE', 'error_type': type(detail_exc).__name__, 'raw_error_printed': False})
    finally:
        subprocess.run = original
        sys.argv = old_argv
    emit({'diagnostic': 'COMPLETE', 'files_modified': False, 'production_workloads_mutated': False,
          'build_started': False, 'images_imported': False, 'provider_requests': 0, 'automatic_retry': False})


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        emit({'diagnostic': 'INCOMPLETE', 'error_type': type(exc).__name__, 'stage': LAST,
              'raw_error_printed': False, 'build_started': False, 'production_workloads_mutated': False})
        sys.exit(1)

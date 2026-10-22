"""Offline runtime checks with named, bounded diagnostics. No model submission."""
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess

ROOT = Path('/tmp/weknora-glm-qualification')


def classes(text):
    return {name: bool(re.search(pattern, text)) for name, pattern in {
        'go_panic': r'(?m)^panic:', 'missing_dictionary': r'Dictionary file does not exist',
        'loader_error': r'error while loading shared libraries|version .* not found',
        'permission_denied': r'Permission denied|permission denied',
        'usage': r'Usage of|probe-once', 'library_not_found': r'not found',
    }.items()}


def step(name, command, stdout, stderr, timeout=60):
    print(json.dumps({'runtime_step': name, 'state': 'START', 'provider_requests': 0}), flush=True)
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                            stdin=subprocess.DEVNULL)
    if len(result.stdout) + len(result.stderr) > 1024**2:
        raise RuntimeError('bounded_output_exceeded:' + name)
    (ROOT/stdout).write_bytes(result.stdout)
    if stderr == stdout:
        (ROOT/stdout).write_bytes(result.stdout + result.stderr)
    else:
        (ROOT/stderr).write_bytes(result.stderr)
    print(json.dumps({'runtime_step': name, 'exit_code': result.returncode,
        'stdout_bytes': len(result.stdout), 'stderr_bytes': len(result.stderr),
        'classes': classes((result.stdout + result.stderr).decode('utf-8', 'replace')),
        'raw_output_printed': False, 'provider_requests': 0}), flush=True)
    if result.returncode:
        raise RuntimeError('runtime_step_failed:' + name)
    return result.stdout


def main():
    ROOT.mkdir(mode=0o700)
    directory = Path('/usr/local/share/weknora/jieba')
    names = {'jieba.dict.utf8', 'hmm_model.utf8', 'user.dict.utf8', 'idf.utf8', 'stop_words.utf8'}
    if directory.is_symlink() or {p.name for p in directory.iterdir()} != names:
        raise RuntimeError('dictionary_set_changed')
    # Only these newly copied build-stage files; final COPY preserves these modes.
    directory.chmod(0o755)
    for name in names:
        p = directory/name
        if not p.is_file() or p.is_symlink():
            raise RuntimeError('dictionary_file_invalid')
        p.chmod(0o644)
    if platform.machine() not in ('aarch64', 'arm64'):
        raise RuntimeError('arm64_required')
    libc = step('libc', ['getconf', 'GNU_LIBC_VERSION'], 'libc.txt', 'libc-error.txt')
    if libc.strip() != b'glibc 2.36':
        raise RuntimeError('runtime_libc_changed')
    step('glm_help_and_jieba_init', ['gosu', '1000:1000', '/usr/local/bin/weknora-glm-probe', '-h'],
         'glm-help.txt', 'glm-help.txt')
    if not classes((ROOT/'glm-help.txt').read_text())['usage']:
        raise RuntimeError('glm_help_not_present')
    raw = step('anydoc_linked_engine', ['gosu', '1000:1000', '/usr/local/bin/weknora-anydoc-probe', '--self-test'],
               'anydoc.json', 'anydoc-error.txt')
    data = json.loads(raw)
    if not (data.get('available') and data.get('self_test') and data.get('version') == '0.1.9'
            and data.get('markdown_bytes', 0) > 0 and not data.get('ocr_required')
            and not data.get('assets_error')):
        raise RuntimeError('anydoc_runtime_self_test_failed')
    raw = step('server_loader', ['ldd', '/app/WeKnora'], 'server-ldd.txt', 'server-ldd-error.txt')
    if b'not found' in raw:
        raise RuntimeError('server_runtime_dependency_missing')
    (ROOT/'PASS.txt').write_text('runtime_loader_and_anydoc=PASS provider_requests=0\n')
    print(json.dumps({'runtime_loader_and_anydoc': 'PASS', 'provider_requests': 0,
                      'native_intranet_acceptance': 'NOT_TESTED'}), flush=True)


if __name__ == '__main__':
    main()

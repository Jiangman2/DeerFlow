"""Verify staged read-only Skill/PVC mounts and build the qualified fixed PNG.

No extraction, shared-directory writes, collection, business request or Work send.
"""
import base64
import contextlib
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import stat
import sys
import tempfile

SKILL = Path('/mnt/skills/public/energy-market-daily')
RUNTIME = Path('/mnt/skills/public/.energy-browser-runtime-20261112-r1/linux-arm64')
PACKAGE_SHA = 'UNSEALED_PACKAGE_SHA'
CANDIDATE_VERSION = 'UNSEALED_CANDIDATE_VERSION'
CANDIDATE_FILES = 0
FINAL_TEXT_PROBE = ''
FINAL_TEXT_PROBE_SHA = 'UNSEALED_FINAL_TEXT_PROBE_SHA'
FIXTURES = {}


class Refused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


def no_links(path):
    for part in (path,) + tuple(path.parents):
        need(not part.is_symlink(), 'READONLY_SOURCE_LINK')


def stamp(state):
    return state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns, state.st_ctime_ns


def read(path, limit=1048576):
    no_links(path)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit, 'READONLY_SOURCE_BOUND')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        need(len(raw) == before.st_size and stamp(before) == stamp(os.fstat(fd)) == stamp(path.stat()), 'READONLY_SOURCE_CHANGED')
        return raw
    finally:
        os.close(fd)


def inventory(root):
    no_links(root)
    rows, total = [], 0
    for directory, folders, names in os.walk(str(root), followlinks=False):
        for name in folders:
            path = Path(directory) / name
            no_links(path)
            need(path.is_dir(), 'READONLY_DIRECTORY_TYPE')
        for name in names:
            path = Path(directory) / name
            raw = read(path)
            total += len(raw)
            rows.append({'path': path.relative_to(root).as_posix(), 'bytes': len(raw), 'sha256': sha(raw)})
            need(len(rows) <= 128 and total <= 32 * 1024 * 1024, 'READONLY_SKILL_TREE_BOUND')
    return sorted(rows, key=lambda row: row['path'])


def verify_skill(expected, root=SKILL):
    rows = inventory(root)
    need(len(rows) == CANDIDATE_FILES and sha(canonical(rows)) == expected, 'STAGED_SKILL_TREE_CHANGED')
    manifest = json.loads(read(root / 'MANIFEST.json'))
    need(manifest.get('schema') == 'energy-daily-portable-files.v1' and manifest.get('version') == CANDIDATE_VERSION, 'STAGED_MANIFEST_IDENTITY')
    files = manifest.get('files')
    need(isinstance(files, dict) and len(files) == CANDIDATE_FILES - 1, 'STAGED_MANIFEST_COUNT')
    actual = {row['path']: {key: row[key] for key in ('bytes', 'sha256')} for row in rows if row['path'] != 'MANIFEST.json'}
    need(files == actual and 'assets/browser-runtime.json' in files, 'STAGED_MANIFEST_CONTENT_CHANGED')
    return rows


def verify_runtime_manifest(expected, root=RUNTIME):
    raw = read(root / 'RUNTIME.json', 262144)
    need(sha(raw) == expected, 'STAGED_RUNTIME_MANIFEST_CHANGED')
    value = json.loads(raw)
    need(value.get('schema') == 'energy-browser-runtime.v1' and value.get('architecture') == 'linux/arm64'
         and value.get('source_image_id') == 'sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8', 'STAGED_RUNTIME_IDENTITY')
    return raw


def write_fixtures(files, root):
    need(isinstance(files, dict) and set(FIXTURES).issubset(files) and len(files) <= 64, 'FIXTURE_SET_REQUIRED')
    for name, expected in FIXTURES.items():
        raw = base64.b64decode(files[name], validate=True)
        need(len(raw) <= 1048576 and sha(raw) == expected, 'FIXTURE_CHANGED')
        target = root / 'tests' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)


def qualify_final_text(daily, project, report, temporary):
    need(FINAL_TEXT_PROBE and sha(FINAL_TEXT_PROBE.encode('utf-8')) == FINAL_TEXT_PROBE_SHA, 'FINAL_TEXT_PROBE_NOT_SEALED')
    active = [True]
    def no_external_calls(event, args):
        if active[0] and (event.startswith('socket.') or event in ('subprocess.Popen', 'os.system', 'sqlite3.connect')):
            raise Refused('COMPOSE_ONLY_EXTERNAL_OPERATION_FORBIDDEN')
    sys.addaudithook(no_external_calls)
    try:
        scope = {'__name__': 'sealed_compose_only_qualification'}
        exec(compile(FINAL_TEXT_PROBE, '<sealed-compose-only-qualification>', 'exec'), scope)
        value = scope['qualify'](daily, project, report, temporary)
        need(isinstance(value, dict) and value.get('complete_artifacts_accepted') is True
             and value.get('missing_artifact_rejected') is True and value.get('messages_sent') == 0
             and value.get('network_requests') == 0, 'COMPOSE_ONLY_CASES_NOT_ACCEPTED')
        return value
    finally:
        active[0] = False


def main():
    out = {'schema': 'energy-delivery-skill-verification-child.v1', 'status': 'INCOMPLETE',
           'provider_requests': 0, 'credentials_read': False, 'credentials_exported': False,
           'shared_skill_modified': False, 'work_messages_sent': 0, 'temporary_project_cleaned': False}
    stage, temporary = 'identity', None
    try:
        sys.dont_write_bytecode = True
        need(os.geteuid() == 1000 and os.getegid() == 1000 and platform.machine() in ('aarch64', 'arm64'), 'FIXED_ARM64_NONROOT_IDENTITY_REQUIRED')
        out.update(uid=os.geteuid(), gid=os.getegid())
        raw = sys.stdin.buffer.read(8388609)
        need(len(raw) <= 8388608, 'PAYLOAD_BOUND')
        payload = json.loads(raw)
        need(set(payload) == {'package_sha256', 'after_tree_sha256', 'runtime_manifest_sha256', 'png_sha256', 'test_files'}
             and payload['package_sha256'] == PACKAGE_SHA
             and all(isinstance(payload[key], str) and re.fullmatch('[0-9a-f]{64}', payload[key])
                     for key in ('after_tree_sha256', 'runtime_manifest_sha256', 'png_sha256')), 'PUBLICATION_PAYLOAD_INVALID')
        need(CANDIDATE_VERSION == '2.1.0-portable' and CANDIDATE_FILES > 107 and FINAL_TEXT_PROBE
             and sha(FINAL_TEXT_PROBE.encode('utf-8')) == FINAL_TEXT_PROBE_SHA, 'FINAL_TEXT_CANDIDATE_NOT_SEALED')
        stage = 'read_staged_shared_skill_and_runtime'
        before = verify_skill(payload['after_tree_sha256'])
        runtime_before = verify_runtime_manifest(payload['runtime_manifest_sha256'])
        binding = json.loads(read(SKILL / 'assets/browser-runtime.json'))
        need(binding == {'schema': 'energy-browser-runtime-binding.v1', 'directory': '../.energy-browser-runtime-20261112-r1/linux-arm64',
                         'manifest_sha256': payload['runtime_manifest_sha256']}, 'STAGED_BINDING_CHANGED')
        with tempfile.TemporaryDirectory(prefix='energy-publish-check-', dir='/tmp') as location:
            temporary = Path(location)
            home, work = temporary / 'home', temporary / 'tmp'
            home.mkdir(); work.mkdir()
            os.environ.clear()
            os.environ.update(HOME=str(home), TMPDIR=str(work), PATH='/usr/local/bin:/usr/bin:/bin', LANG='C.UTF-8',
                              PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
            tempfile.tempdir = str(work)
            write_fixtures(payload['test_files'], temporary)
            sys.path[:0] = [str(SKILL / 'scripts'), str(temporary / 'tests')]
            import test_energy_market_daily as fixtures
            daily = importlib.import_module('daily')
            need(Path(daily.__file__).resolve() == (SKILL / 'scripts/daily.py').resolve() and daily.VERSION == CANDIDATE_VERSION, 'MOUNTED_SKILL_IMPORT_REQUIRED')
            project = temporary / 'project'
            project.mkdir()
            fixtures.project_fixture(project)
            source_files = ('market.json', 'financial.json', 'research.json', 'news.json')
            inputs = {name: sha(read(project / name)) for name in source_files}
            selected, original_runtime = [], daily.runtime_browser
            @contextlib.contextmanager
            def observed_runtime():
                with original_runtime() as executable:
                    need(isinstance(executable, str) and Path(executable).is_file() and os.access(executable, os.X_OK)
                         and not Path(executable).is_relative_to(RUNTIME), 'LOCAL_RUNTIME_COPY_REQUIRED')
                    selected.append(executable)
                    yield executable
            stage = 'build_from_readonly_shared_mounts_as_uid1000'
            daily.runtime_browser = observed_runtime
            try:
                # Keep a real fileno for Playwright's driver stderr transport.
                with open(os.devnull, 'w') as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                    report = daily.build(type('Args', (), {'workspace': str(project)})())
            finally:
                daily.runtime_browser = original_runtime
            need(len(selected) == 1 and not Path(selected[0]).exists() and report['png'].get('browser_fallback_count') == 0, 'MANAGED_RUNTIME_COPY_OR_CLEANUP_FAILED')
            image = next(item for item in report['files'] if item['path'].endswith('.png'))
            png = read(Path(image['path']), 3145728)
            actual_png = sha(png)
            need(actual_png == image['sha256'] and len(png) == image['bytes'], 'GENERATED_PNG_RECEIPT_CHANGED')
            need(inputs == {name: sha(read(project / name)) for name in source_files}, 'FIXED_SOURCES_CHANGED')
            out.update(package_sha256=PACKAGE_SHA, after_tree_sha256=payload['after_tree_sha256'],
                       runtime_manifest_sha256=payload['runtime_manifest_sha256'], png_sha256=actual_png,
                       render_seconds=report['render_seconds'], png=report['png'],
                       automatic_binding_used=True, private_local_copy_cleaned=True, synthetic_sources_unchanged=True)
            if actual_png != payload['png_sha256']:
                out.update(expected_png_sha256=payload['png_sha256'], sample_base64=base64.b64encode(png).decode('ascii'), visual_review_pending=True)
                raise Refused('PUBLISHED_PNG_DIFFERS_FROM_QUALIFIED')
            stage = 'compose_only_complete_and_missing_artifact_cases'
            out['final_text'] = qualify_final_text(daily, project, report, temporary)
        stage = 'stable_shared_source_readback'
        need(before == verify_skill(payload['after_tree_sha256']) and runtime_before == verify_runtime_manifest(payload['runtime_manifest_sha256']), 'READONLY_SOURCES_CHANGED_DURING_BUILD')
        out['status'] = 'NONROOT_ARM64_DELIVERY_SKILL_QUALIFIED'
    except Exception as exc:
        out.update(last_step=stage, error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else 'PUBLICATION_CHECK_FAILED_REDACTED')
    finally:
        out['temporary_project_cleaned'] = temporary is not None and not temporary.exists()
    print(json.dumps(out, sort_keys=True))
    return int(out['status'] == 'INCOMPLETE')


if __name__ == '__main__':
    sys.exit(main())

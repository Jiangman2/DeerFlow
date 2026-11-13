"""Run only synthetic candidate checks and rendering as Kubernetes UID 1000."""
import base64
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import sys
import tempfile
import unittest
import zipfile

RUNTIME = Path('/tmp/energy-browser-qualification/skills/.energy-browser-runtime-20261112-r1/linux-arm64')
PUBLIC = Path('/tmp/energy-browser-qualification/skills/public')


class Refused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def verify_runtime(root=RUNTIME):
    raw = (root / 'RUNTIME.json').read_bytes()
    need(len(raw) <= 262144, 'BROWSER_INVENTORY_BOUND')
    inventory = json.loads(raw)
    need(inventory.get('schema') == 'energy-browser-runtime.v1' and inventory.get('architecture') == 'linux/arm64'
         and inventory.get('source_image_id') == 'sha256:fb36911f899cc43f1228ee4d0701130761b43b3e9792320bf4e0f16f41f0bec8'
         and inventory.get('executable') == 'chrome-linux/headless_shell', 'BROWSER_MANIFEST_IDENTITY')
    rows = inventory['files']
    need(isinstance(rows, list) and 0 < len(rows) <= 256, 'BROWSER_FILE_COUNT')
    seen, total = set(), 0
    for row in rows:
        relative = Path(row['path'])
        need(not relative.is_absolute() and '..' not in relative.parts and row['path'] not in seen, 'BROWSER_INVENTORY_PATH')
        path = root / relative
        need(not any(part.is_symlink() for part in (path,) + tuple(path.parents)), 'BROWSER_LINK_REFUSED')
        state = path.stat()
        need(stat.S_ISREG(state.st_mode) and state.st_nlink == 1 and state.st_size == row['bytes']
             and stat.S_IMODE(state.st_mode) == (0o755 if row['executable'] else 0o644), 'BROWSER_FILE_CHANGED')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        need(digest.hexdigest() == row['sha256'], 'BROWSER_FILE_CHANGED')
        total += state.st_size
        seen.add(row['path'])
    need(total <= 512 * 1024 * 1024, 'BROWSER_TOTAL_BOUND')
    actual = {path.relative_to(root).as_posix() for path in root.rglob('*') if path.is_file()}
    need(actual == seen | {'RUNTIME.json'}, 'BROWSER_EXTRA_FILE')
    need(all(name in seen for name in inventory.get('licenses', [])), 'BROWSER_LICENSE_INVENTORY')
    return inventory, sha(raw)


def extract_package(package, expected, root):
    need(sha(package) == expected, 'PACKAGE_CHANGED')
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        entries = archive.infolist()
        need(0 < len(entries) <= 256 and sum(item.file_size for item in entries) <= 32 * 1024 * 1024, 'PACKAGE_BOUND')
        names = set()
        for item in entries:
            relative = Path(item.filename)
            mode = item.external_attr >> 16
            need(not relative.is_absolute() and '..' not in relative.parts and relative.parts[0] == 'energy-market-daily'
                 and item.filename not in names and not item.is_dir() and item.file_size <= 1048576
                 and not stat.S_ISLNK(mode) and '.private' not in relative.parts, 'PACKAGE_LAYOUT')
            names.add(item.filename)
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(item))
    skill = root / 'energy-market-daily'
    manifest = json.loads((skill / 'MANIFEST.json').read_bytes())
    need(set(names) == {'energy-market-daily/MANIFEST.json'} | {'energy-market-daily/' + name for name in manifest['files']}, 'PACKAGE_MANIFEST_SET')
    for name, item in manifest['files'].items():
        path = skill / name
        need(path.resolve().is_relative_to(skill) and path.is_file(), 'PACKAGE_MANIFEST_PATH')
        data = path.read_bytes()
        need(len(data) == item['bytes'] and sha(data) == item['sha256'], 'PACKAGE_MANIFEST_CHANGED')
    return skill, manifest


def main():
    out = {'schema': 'energy-browser-nonroot-child.v1', 'status': 'INCOMPLETE', 'provider_requests': 0, 'credentials_read': False,
           'credentials_exported': False, 'shared_skill_modified': False, 'work_messages_sent': 0, 'visual_review_pending': True}
    stage = 'identity'
    skill = None
    try:
        need(os.geteuid() == 1000 and os.getegid() == 1000 and platform.machine() in ('aarch64', 'arm64'), 'FIXED_ARM64_NONROOT_IDENTITY_REQUIRED')
        out['uid'], out['gid'], out['architecture'] = os.geteuid(), os.getegid(), platform.machine()
        try:
            Path('/root/.cache/ms-playwright/chromium_headless_shell-1228/chrome-linux/headless_shell').stat()
            out['root_private_browser_accessible'] = True
        except PermissionError:
            out['root_private_browser_accessible'] = False
        need(out['root_private_browser_accessible'] is False, 'ROOT_PRIVATE_CACHE_BOUNDARY_NOT_REPRODUCED')
        raw = sys.stdin.buffer.read(8388609)
        need(len(raw) <= 8388608, 'PAYLOAD_BOUND')
        payload = json.loads(raw)
        need(set(payload) == {'package', 'package_sha256', 'test_files'}, 'PAYLOAD_FIELDS')
        stage = 'verify_copied_browser'
        inventory, inventory_hash = verify_runtime()
        out['runtime'] = dict(inventory, inventory_sha256=inventory_hash, total_bytes=sum(row['bytes'] for row in inventory['files']))
        with tempfile.TemporaryDirectory(prefix='energy-browser-task-', dir='/tmp') as temporary:
            root = Path(temporary)
            home, work = root / 'home', root / 'tmp'
            home.mkdir(); work.mkdir()
            os.environ.clear()
            os.environ.update(HOME=str(home), TMPDIR=str(work), PATH='/usr/local/bin:/usr/bin:/bin', LANG='C.UTF-8',
                              PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
            tempfile.tempdir = str(work)
            stage = 'extract_candidate'
            package = base64.b64decode(payload['package'], validate=True)
            skill, manifest = extract_package(package, payload['package_sha256'], PUBLIC)
            for name, encoded in payload['test_files'].items():
                path = root / 'tests' / name
                need(path.resolve().is_relative_to(root / 'tests') and path.suffix in ('.py', '.json', '.txt'), 'FIXTURE_PATH')
                data = base64.b64decode(encoded, validate=True)
                need(len(data) <= 1048576, 'FIXTURE_BOUND')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            sys.path[:0] = [str(skill / 'scripts'), str(root / 'tests')]
            stage = 'offline_regressions_as_nonroot'
            import test_energy_market_daily as business_tests
            import test_energy_browser_runtime as browser_tests
            suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromModule(module) for module in (business_tests, browser_tests))
            log = io.StringIO()
            result = unittest.TextTestRunner(stream=log).run(suite)
            out['offline_tests'] = {'run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors),
                                    'skipped': len(result.skipped), 'diagnostic_sha256': sha(log.getvalue().encode())}
            need(result.wasSuccessful(), 'NONROOT_REGRESSIONS_FAILED')
            stage = 'actual_nonroot_png_build'
            import daily
            binding = {'schema': 'energy-browser-runtime-binding.v1', 'directory': '../../.energy-browser-runtime-20261112-r1/linux-arm64',
                       'manifest_sha256': inventory_hash}
            (skill / 'assets').mkdir(exist_ok=True)
            binding_raw = json.dumps(binding, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii') + b'\n'
            (skill / 'assets/browser-runtime.json').write_bytes(binding_raw)
            need('REPORT_BROWSER_EXECUTABLE' not in os.environ, 'EXPLICIT_BROWSER_OVERRIDE_UNEXPECTED')
            selected = []
            original_runtime = daily.runtime_browser
            @contextlib.contextmanager
            def observed_runtime():
                with original_runtime() as executable:
                    need(isinstance(executable, str) and Path(executable).is_file() and os.access(executable, os.X_OK)
                         and not Path(executable).is_relative_to(RUNTIME), 'AUTOMATIC_LOCAL_BROWSER_COPY_REQUIRED')
                    selected.append(executable)
                    yield executable
            project = root / 'synthetic'
            project.mkdir()
            business_tests.project_fixture(project)
            before = {name: sha((project / name).read_bytes()) for name in ('market.json', 'financial.json', 'research.json', 'news.json')}
            # Redirect progress so the child emits one bounded JSON object only.
            daily.runtime_browser = observed_runtime
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    report = daily.build(type('Args', (), {'workspace': str(project)})())
            finally:
                daily.runtime_browser = original_runtime
            image = next(item for item in report['files'] if item['path'].endswith('.png'))
            png = Path(image['path']).read_bytes()
            need(len(png) == image['bytes'] <= 3145728 and sha(png) == image['sha256'] and report['png']['width'] == 780, 'SYNTHETIC_IMAGE_INVALID')
            need(before == {name: sha((project / name).read_bytes()) for name in before}, 'SOURCE_FIXTURE_CHANGED')
            need(len(selected) == 1 and not Path(selected[0]).exists() and report['png'].get('browser_fallback_count') == 0, 'AUTOMATIC_BROWSER_HANDOFF_OR_CLEANUP_FAILED')
            out.update(status='NONROOT_ARM64_BROWSER_AND_FIXED_REPORT_QUALIFIED', candidate_version=daily.VERSION,
                       candidate_files=len(manifest['files']) + 1, package_sha256=sha(package), synthetic_sources_unchanged=True,
                       qualification_binding_sha256=sha(binding_raw), qualification_binding=binding,
                       runtime_loader={'automatic_binding_used': True, 'private_local_copy_used': True, 'private_local_copy_cleaned': True,
                                       'explicit_browser_override_used': False, 'source_execution_not_required': True},
                       render={'png': report['png'], 'render_seconds': report['render_seconds'], 'bytes': len(png), 'sha256': sha(png)},
                       sample_base64=base64.b64encode(png).decode('ascii'))
        out['temporary_project_cleaned'] = True
    except Exception as exc:
        out.update(last_step=stage, error_class=type(exc).__name__, error_code=str(exc) if isinstance(exc, Refused) else 'QUALIFICATION_FAILED_REDACTED')
    finally:
        if skill is not None:
            shutil.rmtree(skill, ignore_errors=True)
            out['temporary_skill_copy_cleaned'] = not skill.exists()
    print(json.dumps(out, sort_keys=True))
    return int(out['status'] == 'INCOMPLETE')


if __name__ == '__main__':
    sys.exit(main())

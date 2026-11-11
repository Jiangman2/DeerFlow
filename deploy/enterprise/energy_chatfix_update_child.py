"""Two-file shared Skill update; no credential, provider, database or Work calls."""
import base64
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import sys
import zipfile

ROOT = Path('/mnt/skills/public/energy-market-daily')
TEMP = Path('/tmp')
ORDER = ('scripts/entry.py', 'SKILL.md')
OLD = {
    'SKILL.md': '757bec043e2b0400e5160ff7d8903437b5029f9cc4ce624c4018e53826efd1a7',
    'scripts/entry.py': 'b250a6931dc0547b0d478894db628abb35db5cf282ef9d8021c2e6b1d19f9844',
}
ARCHIVE = 'assets/energy-daily-v1.0.original.zip'
ARCHIVE_SHA = 'eb66843bbe6170d86d33b65efa593be263e778275c6f7d491e1fd0c3c6d98f99'
MANIFEST_SHA = 'e44fbbdd3a6423fabe1bfac58e5a0684a3cf9dd88188eb34e2659201102ff0d9'
LIMITS = {'SKILL.md': 32768, 'scripts/entry.py': 131072}
BROWSER = '/root/.cache/ms-playwright/chromium_headless_shell-1228/chrome-linux/headless_shell'
JOURNAL_NAME = 'JOURNAL.json'


class PublicationFailure(ValueError):
    def __init__(self, cause, publication):
        detail = str(cause)
        super().__init__(detail if isinstance(cause, ValueError) and re.fullmatch('[A-Z_0-9]{1,100}', detail)
                         else 'PUBLICATION_FAILED')
        self.publication = publication


def require(value, code):
    if not value:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n').encode()


def no_symlinks(path):
    path = Path(path)
    require(not any(p.is_symlink() for p in (path, *path.parents)), 'SYMLINK_REFUSED')


def read(path, limit=6 * 1024 * 1024):
    path = Path(path)
    no_symlinks(path)
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size <= limit, 'FILE_BOUND_OR_TYPE')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(fd)
        require(len(raw) <= limit and (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'FILE_CHANGED_DURING_READ')
        return raw
    finally:
        os.close(fd)


def identity(path):
    no_symlinks(path)
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode), 'DIRECTORY_REQUIRED')
    return [info.st_dev, info.st_ino]


def file_attributes(path):
    no_symlinks(path)
    info = path.stat()
    require(stat.S_ISREG(info.st_mode), 'REGULAR_FILE_REQUIRED')
    return dict(uid=info.st_uid, gid=info.st_gid, mode=stat.S_IMODE(info.st_mode))


def inventory(folder):
    identity(folder)
    result, total = {}, 0
    for directory, folders, names in os.walk(folder, followlinks=False):
        for name in folders:
            identity(Path(directory) / name)
        for name in names:
            path = Path(directory) / name
            raw = read(path)
            total += len(raw)
            result[path.relative_to(folder).as_posix()] = sha(raw)
            require(len(result) <= 512 and total <= 32 * 1024 * 1024, 'TREE_BOUND')
    return result


def verify_original(folder):
    raw = read(folder / ARCHIVE)
    require(sha(raw) == ARCHIVE_SHA, 'ORIGINAL_ARCHIVE_CHANGED')
    require(sha(read(folder / 'UPSTREAM.json')) == MANIFEST_SHA, 'ORIGINAL_MANIFEST_CHANGED')
    expected = {}
    total = 0
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for item in archive.infolist():
            p = PurePosixPath(item.filename)
            require(not p.is_absolute() and '..' not in p.parts and ':' not in item.filename
                    and '\\' not in item.filename and not stat.S_ISLNK(item.external_attr >> 16), 'ARCHIVE_PATH')
            if item.is_dir():
                continue
            parts = p.parts[1:] if p.parts[0] == 'energy-daily-v1.0' else p.parts
            name = PurePosixPath(*parts).as_posix()
            require(name not in expected and len(expected) < 158 and item.file_size <= 6 * 1024 * 1024,
                    'ARCHIVE_FILE_BOUND')
            total += item.file_size
            require(total <= 24 * 1024 * 1024, 'ARCHIVE_TOTAL_BOUND')
            expected[name] = sha(archive.read(item))
    require(len(expected) == 158 and inventory(folder / 'upstream') == expected, 'ORIGINAL_TREE_CHANGED')
    return expected


def decode_payload(value):
    require(isinstance(value, dict) and set(value) == {'files', 'sha256'}, 'PAYLOAD_SHAPE')
    require(isinstance(value['files'], dict) and isinstance(value['sha256'], dict)
            and set(value['files']) == set(ORDER) and set(value['sha256']) == set(ORDER), 'TWO_FILES_REQUIRED')
    files = {}
    for name in ORDER:
        text = value['files'][name]
        digest = value['sha256'][name]
        require(isinstance(text, str) and len(text) <= LIMITS[name] * 2, 'PAYLOAD_SIZE')
        require(isinstance(digest, str) and re.fullmatch('[a-f0-9]{64}', digest), 'PAYLOAD_DIGEST')
        raw = base64.b64decode(text, validate=True)
        require(0 < len(raw) <= LIMITS[name] and sha(raw) == digest and digest != OLD[name], 'PAYLOAD_HASH')
        raw.decode('utf-8')
        files[name] = raw
    compile(files['scripts/entry.py'], '<candidate-entry>', 'exec')
    return files


def paths(operation):
    require(isinstance(operation, str) and re.fullmatch('[a-f0-9]{32}', operation), 'OPERATION_REQUIRED')
    workspace = TEMP / ('energy-chatfix-' + operation)
    release = ROOT.parent.parent / ('.energy-market-chatfix-' + operation)
    no_symlinks(workspace)
    no_symlinks(release)
    return workspace, release


def write_new(path, raw, mode=0o600):
    no_symlinks(path)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def save_journal(path, value):
    temporary = path.with_name('.journal-next')
    write_new(temporary, encoded(value))
    os.replace(temporary, path)


def load_entry(folder):
    spec = importlib.util.spec_from_file_location('energy_chatfix_candidate', folder / 'scripts/entry.py')
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    return entry


def smoke(candidate, workspace):
    """No real collection: fixed input generation only, no visual acceptance."""
    entry = load_entry(candidate)
    expected = entry.original_files()
    require(len(expected) == 158, 'CANDIDATE_ORIGINAL_COUNT')
    tests = []
    projects = workspace / 'projects'
    projects.mkdir(mode=0o700)
    normal = projects / 'default'
    before_day = entry.dt.datetime.now(entry.TZ).date().isoformat()
    created = entry.init(normal, None, expected)
    after_day = entry.dt.datetime.now(entry.TZ).date().isoformat()
    require(created['mode'] == 'generate' and created['publication_date'] in (before_day, after_day),
            'DEFAULT_DATE_OR_MODE')
    tests.append('default_beijing_date_and_generate')
    prior = inventory(normal)
    state = entry.inputs_status(normal, entry.project_meta(normal, expected), expected)
    require(state['status'] == 'INPUTS_OBSERVED' and len(state['input_states']) == 3
            and inventory(normal) == prior, 'STATUS_NOT_READONLY')
    tests.append('three_input_status_readonly')
    sample = projects / 'sample-blocked'
    entry.init(sample, '2026-11-07', expected)
    try:
        entry.require_report_inputs(sample, entry.project_meta(sample, expected), expected)
    except entry.WorkflowBlocked as exc:
        require(exc.report.get('error_code') == 'SAMPLE_INPUTS_REQUIRE_REPRODUCTION', 'SAMPLE_GUARD_CODE')
    else:
        raise ValueError('SAMPLE_GUARD_MISSING')
    tests.append('generate_identical_sample_blocked')
    request_market = entry.request_market
    entry.request_market = lambda day: dict(status='INCOMPLETE', data_date=day,
                                            error_code='PROVIDER_REQUEST_FAILED', failure_stage='original_fetch',
                                            connections=1, target_response_validated=False)
    try:
        try:
            entry.refresh_market(normal, entry.project_meta(normal, expected))
        except entry.WorkflowBlocked as exc:
            require(exc.report.get('error_code') == 'COLLECTOR_FAILED'
                    and exc.report.get('collector', {}).get('error_code') == 'PROVIDER_REQUEST_FAILED'
                    and exc.report.get('blocked_outputs') is True, 'STRUCTURED_FAILURE_MISSING')
        else:
            raise ValueError('STRUCTURED_FAILURE_MISSING')
    finally:
        entry.request_market = request_market
    tests.append('structured_collector_failure_without_request')
    reproduce = projects / 'fixed-reproduction'
    entry.init(reproduce, '2026-11-07', expected, mode='reproduce')
    require(Path(BROWSER).is_file(), 'APPROVED_HEADLESS_MISSING')
    wrapper = workspace / 'offline-browser'
    # Keep the original renderer byte-identical. Browser communication uses its pipe.
    write_new(wrapper, ('#!/bin/sh\nexec ' + BROWSER + ' --disable-background-networking '
                       '--disable-component-update --disable-sync '
                       '--host-resolver-rules="MAP * ~NOTFOUND" '
                       '--proxy-server=http://127.0.0.1:9 --proxy-bypass-list="<-loopback>" "$@"\n').encode(), 0o700)
    built = entry.build_report(reproduce, entry.project_meta(reproduce, expected), expected, str(wrapper))
    require(built['status'] == 'ORIGINAL_REPORT_VERIFIED_VISUAL_PENDING'
            and built['mode'] == 'reproduce' and not built.get('formal_report_ready'), 'REPRODUCTION_NOT_ACCEPTED')
    output = reproduce / 'engine/app/output'
    stem = '油气与金融市场日报_2026-11-07'
    for suffix in ('html', 'json'):
        actual = read(output / (stem + '.' + suffix))
        approved = read(candidate / 'upstream/reference' / ('approved-2026-11-07.' + suffix))
        require(actual.replace(b'\r\n', b'\n') == approved.replace(b'\r\n', b'\n'), 'ORIGINAL_REFERENCE_MISMATCH')
    png = read(output / (stem + '.png'), 12 * 1024 * 1024)
    from PIL import Image
    with Image.open(io.BytesIO(png)) as picture:
        dimensions = list(picture.size)
        require(picture.format == 'PNG' and dimensions[0] == 780, 'PNG_SHAPE')
        picture.verify()
    write_new(workspace / 'FIXED-SAMPLE.png', png)
    entry.original_files()
    tests.append('original_fixed_reproduction_html_json_equal_and_png_rendered')
    return dict(tests=tests, test_count=len(tests), png_dimensions=dimensions, png_sha256=sha(png),
                mode='reproduce', publication_date='2026-11-07', data_date='2026-11-06',
                html_json_equal_after_crlf_normalization=True, formal_report_ready=False,
                visual_acceptance_pending=True, provider_requests=0, work_messages_sent=0)


def stage(operation, payload):
    files = decode_payload(payload)
    workspace, release = paths(operation)
    require(not workspace.exists() and not release.exists(), 'OPERATION_ALREADY_EXISTS')
    live_id = identity(ROOT)
    verify_original(ROOT)
    old_tree = inventory(ROOT)
    require(all(old_tree.get(name) == OLD[name] for name in ORDER), 'OLD_WRAPPER_CHANGED')
    attributes = {name: file_attributes(ROOT / name) for name in ORDER}
    workspace.mkdir(mode=0o700)
    candidate = workspace / 'skill'
    shutil.copytree(ROOT, candidate)
    require(inventory(candidate) == old_tree, 'STAGE_COPY_CHANGED')
    for index, name in enumerate(ORDER):
        # A read-only source file may copy as 0444; replace the private copy
        # instead of changing the source or relying on DAC override privileges.
        replacement = workspace / ('staged-file-' + str(index))
        write_new(replacement, files[name])
        os.replace(replacement, candidate / name)
    candidate_tree = dict(old_tree, **{name: sha(files[name]) for name in ORDER})
    require(inventory(candidate) == candidate_tree, 'CANDIDATE_TREE_CHANGED')
    verify_original(candidate)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        checks = smoke(candidate, workspace)
    require(inventory(candidate) == candidate_tree and inventory(ROOT) == old_tree
            and identity(ROOT) == live_id, 'BASELINE_CHANGED_DURING_STAGE')
    manifest = dict(schema='energy-chatfix-stage.v1', operation=operation, live_directory_identity=live_id,
                    old_tree=old_tree, candidate_tree=candidate_tree, candidate_sha256=payload['sha256'],
                    old_file_attributes=attributes, checks=checks, original_files=158, stage_only=True)
    raw = encoded(manifest)
    write_new(workspace / 'MANIFEST.json', raw)
    return dict(status='STAGED_CHATFIX_ACCEPTED', stage_manifest_sha256=sha(raw),
                candidate_sha256=payload['sha256'], checks=checks, original_files=158, live_files_modified=False)


def bound_stage(operation, binding, require_candidate=True):
    require(isinstance(binding, dict) and set(binding) == {'stage_manifest_sha256', 'sha256'}, 'STAGE_BINDING_REQUIRED')
    require(isinstance(binding['sha256'], dict) and set(binding['sha256']) == set(ORDER), 'TWO_HASHES_REQUIRED')
    workspace, release = paths(operation)
    raw = read(workspace / 'MANIFEST.json', 262144)
    require(sha(raw) == binding['stage_manifest_sha256'], 'STAGE_MANIFEST_CHANGED')
    manifest = json.loads(raw)
    require(manifest.get('schema') == 'energy-chatfix-stage.v1' and manifest.get('operation') == operation
            and manifest.get('candidate_sha256') == binding['sha256'], 'CANDIDATE_BINDING_CHANGED')
    require(identity(ROOT) == manifest['live_directory_identity'], 'LIVE_DIRECTORY_CHANGED')
    if require_candidate:
        require(inventory(workspace / 'skill') == manifest['candidate_tree'], 'STAGED_FILES_CHANGED')
        verify_original(workspace / 'skill')
    return workspace, release, manifest


def publish(operation, binding):
    workspace, release, manifest = bound_stage(operation, binding)
    verify_original(ROOT)
    require(inventory(ROOT) == manifest['old_tree'], 'LIVE_BASELINE_CHANGED')
    require(not release.exists(), 'PUBLICATION_ALREADY_CLAIMED')
    release.mkdir(mode=0o700)
    backup = release / 'backup'
    backup.mkdir(mode=0o700)
    journal = dict(schema='energy-chatfix-publication.v1', operation=operation, status='BACKUP_STARTED',
                   stage_manifest_sha256=binding['stage_manifest_sha256'], replacement_order=list(ORDER),
                   applied=[], replacement_may_have_applied=False, automatic_rollback=False)
    save_journal(release / JOURNAL_NAME, journal)
    try:
        write_new(release / 'MANIFEST.json', read(workspace / 'MANIFEST.json', 262144))
        require(sha(read(release / 'MANIFEST.json', 262144)) == binding['stage_manifest_sha256'],
                'BACKUP_MANIFEST_CHANGED')
        for name in ORDER:
            target = backup / name
            target.parent.mkdir(mode=0o700, exist_ok=True)
            write_new(target, read(ROOT / name, LIMITS[name]))
            require(sha(read(target, LIMITS[name])) == OLD[name], 'BACKUP_HASH_MISMATCH')
        journal['status'] = 'BACKUP_VERIFIED'
        save_journal(release / JOURNAL_NAME, journal)
        # Prepare both files and prove ownership before replacing either live
        # file. The maintenance Pod drops capabilities; no CAP_CHOWN is needed.
        for index, name in enumerate(ORDER):
            attributes = file_attributes(ROOT / name)
            require(attributes == manifest['old_file_attributes'][name], 'OLD_FILE_ATTRIBUTES_CHANGED')
            raw = read(workspace / 'skill' / name, LIMITS[name])
            require(sha(raw) == binding['sha256'][name], 'CANDIDATE_HASH_CHANGED')
            temporary = release / ('candidate-' + str(index))
            write_new(temporary, raw, attributes['mode'])
            created = file_attributes(temporary)
            require(all(created[key] == attributes[key] for key in ('uid', 'gid')), 'NEW_FILE_OWNER_MISMATCH')
            os.chmod(temporary, attributes['mode'])
            require(file_attributes(temporary) == attributes, 'NEW_FILE_ATTRIBUTES_MISMATCH')
        progress_tree = dict(manifest['old_tree'])
        for index, name in enumerate(ORDER):
            require(inventory(ROOT) == progress_tree and identity(ROOT) == manifest['live_directory_identity'],
                    'LIVE_CHANGED_BEFORE_REPLACE')
            target = ROOT / name
            temporary = release / ('candidate-' + str(index))
            require(file_attributes(target) == manifest['old_file_attributes'][name], 'OLD_FILE_ATTRIBUTES_CHANGED')
            require(sha(read(temporary, LIMITS[name])) == binding['sha256'][name], 'CANDIDATE_HASH_CHANGED')
            journal.update(status='REPLACE_REQUESTED', next_file=name, replacement_may_have_applied=True)
            save_journal(release / JOURNAL_NAME, journal)
            os.replace(temporary, target)
            journal['applied'].append(name)
            progress_tree[name] = binding['sha256'][name]
            require(sha(read(target, LIMITS[name])) == binding['sha256'][name], 'LIVE_NEW_HASH_MISMATCH')
            journal.update(status='REPLACED_ONE', replacement_may_have_applied=False)
            save_journal(release / JOURNAL_NAME, journal)
        verify_original(ROOT)
        require(inventory(ROOT) == manifest['candidate_tree']
                and identity(ROOT) == manifest['live_directory_identity'], 'PUBLISH_READBACK_FAILED')
        journal.update(status='TWO_FILE_UPDATE_ACCEPTED', directory_inode_preserved=True, original_files=158)
        save_journal(release / JOURNAL_NAME, journal)
        return dict(status=journal['status'], candidate_sha256=binding['sha256'], original_files=158,
                    backup_path=str(backup), directory_inode_preserved=True, applied=list(journal['applied']),
                    stage_manifest_sha256=binding['stage_manifest_sha256'], automatic_rollback=False)
    except BaseException as exc:
        journal['status'] = 'PUBLICATION_INCOMPLETE'
        try:
            save_journal(release / JOURNAL_NAME, journal)
        except BaseException:
            pass
        raise PublicationFailure(exc, dict(status=journal['status'], applied=list(journal['applied']),
                                           replacement_may_have_applied=journal['replacement_may_have_applied'],
                                           next_file=journal.get('next_file'), backup_path=str(backup),
                                           stage_manifest_sha256=binding['stage_manifest_sha256'],
                                           automatic_rollback=False)) from None


def verify(operation, binding):
    workspace, release, manifest = bound_stage(operation, binding, require_candidate=False)
    require(inventory(ROOT) == manifest['candidate_tree'], 'LIVE_POSTUPDATE_MISMATCH')
    verify_original(ROOT)
    return dict(status='CHATFIX_LIVE_FILES_VERIFIED', candidate_sha256=binding['sha256'], original_files=158,
                directory_inode_preserved=True, live_files_modified=False,
                stage_manifest_sha256=binding['stage_manifest_sha256'])


def main():
    out = dict(schema='energy-chatfix-update-child.v1', status='INCOMPLETE', provider_requests=0,
               credentials_read=False, credentials_exported=False, database_access=False, work_messages_sent=0,
               deployment_modified=False, automatic_retry=False, automatic_rollback=False, original_files_modified=False)
    try:
        require(len(sys.argv) == 3 and sys.argv[1] in ('stage', 'publish', 'verify'), 'ACTION_ARGUMENTS')
        if hasattr(signal, 'SIGALRM'):
            def time_limit(signum, frame):
                raise ValueError('CHILD_TIME_LIMIT')
            signal.signal(signal.SIGALRM, time_limit)
            signal.alarm(420 if sys.argv[1] == 'stage' else 120)
        raw = sys.stdin.buffer.read(524289)
        require(len(raw) <= 524288, 'STDIN_BOUND')
        value = json.loads(raw)
        def deny_network(event, args):
            if event in ('socket.connect', 'socket.getaddrinfo', 'socket.bind'):
                raise ValueError('NETWORK_DISABLED')
        sys.addaudithook(deny_network)
        out.update({'stage': stage, 'publish': publish, 'verify': verify}[sys.argv[1]](sys.argv[2], value))
    except Exception as exc:
        detail = str(exc)
        out.update(error_class=type(exc).__name__, error_code=detail if re.fullmatch('[A-Z_0-9]{1,100}', detail) else 'LOCAL_FAILURE_REDACTED')
        if isinstance(exc, PublicationFailure):
            out['publication'] = exc.publication
    finally:
        if hasattr(signal, 'SIGALRM'):
            signal.alarm(0)
    print(json.dumps(out, sort_keys=True))
    return int(out['status'] == 'INCOMPLETE')


if __name__ == '__main__':
    raise SystemExit(main())

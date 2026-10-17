#!/usr/bin/env python3
"""Fixed existing Host OCI archive, read-only diagnostic audit (Python 3.7+).

No image execution, rootfs extraction, network, subprocess or application import.
Only a fresh private REPORT.json is written. This never grants build permission.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import stat
import sys
import tarfile
import time

HERE = Path(__file__).absolute().parent
OUTPUT_BASE = Path('/var/lib/portal-ppt-host-base-verify-r1')
ARCHIVE = Path('/opt/deerflow-build-artifacts/ppt-runtime-repair-r2/build1/agent-host.candidate.oci.tar')
ARCHIVE_SHA = 'ea8bfd699a29032e49fa5127d8dcdd59ad635c34bdce1cfeef006461a4a0bc65'
MANIFEST = 'sha256:126fabc46a5d4d3b9d50924722bf10e7a1003a4d1adbdf72ace8ae25e188f97e'
CONFIG = 'sha256:09ea44f66e1d47269b10e05f11f933f9f08897a1e5a55222a138f0a1b1acfaea'
TAG = 'deerflow.local/deer-flow-aio-sandbox:20260927-ppt-runtime-r2-agent-host-3959a039ee854dca'
LAYERS = 128
COMPRESSED_LAYER_BYTES = 5337237974
KNOWN_HEADER = '8b5bbd065df580d90e27aead4de0e496bc6c8ffe18e5b77a820d76b89a19275c'
LOCKS = {
    'frozen_gateway_replay.py': 'ee7500ca36844d96308a83caef0663766569c42aa935bee2fd2c34f653b4751a',
    'frozen_export_verifier.py': '23f61ad3164f37856c3c320c34a4dd8a0c81020e2bc9d14a783736f7a8a0be0f',
    'EXPECTATIONS.json': '968f8bff22c6072b8c1075386198e6af5f23fff2c8e1e89651720084ee3ee199',
}
MAX_ARCHIVE = 6 * 1024 ** 3
MAX_DEPENDENCY = 512 * 1024 ** 2
MAX_HASHED_SELECTION = 4 * 1024 ** 3
MAX_DEPENDENCY_PATHS = 128
SECONDS = 1800
CHUNK = 1024 ** 2

# Candidates are deliberately bounded observations from the historical recipe,
# not a claim that R7 captured installed distributions or executable locations.
# Never inspect auth/config/user state. Links are classified, never followed.
DEP_FIXED = {
    'usr/bin/python3', 'usr/bin/python3.10', 'usr/local/bin/codex', 'usr/bin/codex',
    'usr/local/lib/node_modules/@openai/codex/package.json',
    'opt/ppt-master-agent-host/PPT_MASTER_UPSTREAM.env',
    'opt/ppt-master-agent-host/skill-release-manifest.json',
    'opt/ppt-master-release-skill/requirements.txt',
}
DEP_PREFIXES = (
    'usr/lib/python3.10/dist-packages', 'usr/local/lib/python3.10/dist-packages',
    'usr/local/lib/python3.10/site-packages', 'opt/ms-playwright',
)
PACKAGE_LEAVES = re.compile(
    r'(?:flask/__init__\.py|werkzeug/__init__\.py|playwright/__init__\.py|'
    r'playwright/driver/package/(?:browsers|package)\.json|'
    r'(?:[Ff]lask|[Ww]erkzeug|playwright)-[0-9][A-Za-z0-9_.+!-]{0,63}\.dist-info/METADATA)\Z')
BROWSER_LEAVES = re.compile(
    r'(?:chromium-[0-9]{1,8}/chrome-linux(?:64)?/chrome|'
    r'chromium_headless_shell-[0-9]{1,8}/chrome-linux/headless_shell|'
    r'chromium_headless_shell-[0-9]{1,8}/chrome-headless-shell-linux64/chrome-headless-shell)\Z')


class Refused(Exception):
    pass


def require(value, code):
    if not value:
        raise Refused(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def check_time(deadline):
    require(time.monotonic() <= deadline, 'deadline_exceeded')


def safe_path(path):
    path = Path(path).absolute()
    for current in [path] + list(path.parents):
        info = current.lstat()
        require(not stat.S_ISLNK(info.st_mode)
                and not getattr(info, 'st_file_attributes', 0) & 1024, 'local_link_or_reparse')
    return path


def read_small(path, limit=4 * CHUNK):
    path = safe_path(path)
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit,
                'local_file_type_or_limit')
        raw = stream.read(limit + 1)
        require(len(raw) == before.st_size and stamp(before) == stamp(os.fstat(stream.fileno())),
                'local_file_changed')
        return raw


def stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def load_inputs():
    contents = {}
    for name, expected in LOCKS.items():
        raw = read_small(HERE / name)
        require(sha(raw) == expected, 'frozen_input_hash_mismatch')
        contents[name] = raw
    api = type(sys)('host_frozen_gateway_replay')
    api.__file__ = str(HERE / 'frozen_gateway_replay.py')
    exec(compile(contents['frozen_gateway_replay.py'], api.__file__, 'exec'), api.__dict__)
    outer = type(sys)('host_frozen_outer_verifier')
    outer.__file__ = str(HERE / 'frozen_export_verifier.py')
    exec(compile(contents['frozen_export_verifier.py'], outer.__file__, 'exec'), outer.__dict__)
    expected = outer.parse_json(contents['EXPECTATIONS.json'])
    require(expected['schema'] == 'portal-ppt-host-fixed-base-expectations.v1'
            and len(expected['targets']) == 13, 'expectations_schema_or_count')
    require(expected['archive']['archive_sha256'] == ARCHIVE_SHA
            and expected['archive']['manifest_digest'] == MANIFEST
            and expected['archive']['config_digest'] == CONFIG
            and expected['archive']['compressed_layer_bytes'] == COMPRESSED_LAYER_BYTES
            and expected['archive']['layers_verified'] == LAYERS, 'historical_identity_disagrees')
    return api, outer, expected


def dependency_path(name):
    if name in DEP_FIXED:
        return True
    for prefix in DEP_PREFIXES[:-1]:
        if name.startswith(prefix + '/'):
            return bool(PACKAGE_LEAVES.fullmatch(name[len(prefix) + 1:]))
    return name.startswith('opt/ms-playwright/') and bool(BROWSER_LEAVES.fullmatch(name[18:]))


def dependency_ancestor(name):
    for prefix in DEP_PREFIXES[:-1]:
        if name.startswith(prefix + '/'):
            leaf = name[len(prefix) + 1:]
            return leaf in ('flask', 'werkzeug', 'playwright', 'playwright/driver', 'playwright/driver/package') or bool(
                re.fullmatch(r'(?:[Ff]lask|[Ww]erkzeug|playwright)-[0-9][A-Za-z0-9_.+!-]{0,63}\.dist-info', leaf))
    if name.startswith('opt/ms-playwright/'):
        return bool(re.fullmatch(r'chromium(?:_headless_shell)?-[0-9]{1,8}(?:/(?:chrome-linux(?:64)?|chrome-headless-shell-linux64))?', name[18:]))
    return False


def ancestors(names):
    return {str(p) for name in names for p in PurePosixPath(name).parents if str(p) != '.'}


def make_replay(api, targets, report):
    """Fixed Host profile over frozen parsing/digest primitives; no DB scope."""
    class HostReplay(api.SourceReplay):
        def __init__(self):
            self.targets = set(targets)
            self.ancestors = ancestors(self.targets | DEP_FIXED | set(DEP_PREFIXES)) | set(DEP_PREFIXES)
            self.state = {}
            self.hashed_bytes = 0
            self.dependency_names = set()
            self.current_layer = None

        def selected(self, name):
            return name in self.targets or name in self.ancestors or dependency_path(name) or dependency_ancestor(name)

        def diagnostic_header(self, member):
            # A known historical name is evidence to collect, not a whitelist.
            # No extraction is performed. Require literal POSIX path disjointness
            # and also disjointness if backslashes were interpreted as separators.
            name = member.name
            identity = sha(name[2:].encode('utf-8', 'surrogateescape') if name.startswith('./')
                           else name.encode('utf-8', 'surrogateescape'))
            require(identity == KNOWN_HEADER and not report['diagnostic_headers'], 'unknown_or_second_unsafe_header')
            require('\\' in name and not name.startswith('/')
                    and not any(ord(c) < 32 for c in name), 'known_header_not_simple_backslash')
            require(member.isfile() and not member.issparse(), 'known_header_type_requires_review_before_replay')
            protected = self.targets | DEP_FIXED | set(DEP_PREFIXES)
            for spelling in (name, name.replace('\\', '/')):
                normalized = spelling[2:] if spelling.startswith('./') else spelling
                parts = normalized.rstrip('/').split('/')
                require(all(p not in ('', '.', '..') for p in parts)
                        and not any(p.startswith('.wh.') for p in parts), 'known_header_can_affect_paths')
                require(not any(api.beneath(normalized, p) or api.beneath(p, normalized)
                                for p in protected), 'known_header_overlaps_selected_scope')
                require(normalized not in self.ancestors, 'known_header_is_selected_ancestor')
            report['diagnostic_headers'].append({
                'layer_digest': self.current_layer, 'name_sha256': identity,
                'raw_tar_name_sha256': sha(name.encode('utf-8', 'surrogateescape')),
                'type_hex': member.type.hex(), 'bytes': member.size,
                'regular_file': True, 'not_whiteout': True, 'not_link': True,
                'strict_posix_disjoint_from_targets_and_ancestors': True,
                'slash_substitution_also_disjoint': True,
                'approval': 'NOT_GRANTED_DIAGNOSTIC_ONLY',
            })

        def layer(self, stream, deadline):
            updates, deletions, names, name_bytes, count = {}, [], set(), 0, 0
            with tarfile.open(fileobj=stream, mode='r|', bufsize=10240, tarinfo=api.BoundedTarInfo) as tf:
                for member in tf:
                    check_time(deadline)
                    count += 1
                    name_bytes += len(member.name.encode('utf-8', 'surrogateescape'))
                    require(count <= api.MAX_HEADERS and name_bytes <= 32 * CHUNK
                            and len(member.name) <= 4096, 'layer_header_limit')
                    require(not member.issparse() and not any(k.startswith('GNU.sparse') for k in member.pax_headers),
                            'sparse_layer_entry')
                    try:
                        name = api.normalized_name(member.name)
                    except api.Refused:
                        self.diagnostic_header(member)
                        tf.members.clear()
                        continue
                    require(name not in names, 'duplicate_layer_path')
                    names.add(name)
                    if not name:
                        require(member.isdir(), 'non_directory_layer_root')
                        tf.members.clear()
                        continue
                    parent, _, leaf = name.rpartition('/')
                    if leaf.startswith('.wh.'):
                        require(member.isfile() and member.size == 0, 'invalid_whiteout')
                        if leaf == '.wh..wh..opq':
                            deletions.append((parent, True))
                        else:
                            leaf = leaf[4:]
                            require(leaf not in ('', '.', '..'), 'invalid_whiteout_leaf')
                            deletions.append(((parent + '/' if parent else '') + leaf, False))
                    elif self.selected(name):
                        if dependency_path(name):
                            self.dependency_names.add(name)
                            require(len(self.dependency_names) <= MAX_DEPENDENCY_PATHS, 'dependency_path_limit')
                        if member.isdir():
                            value = {'kind': 'directory'}
                        elif member.isfile():
                            require(0 <= member.size <= (api.MAX_FILE if name in self.targets else MAX_DEPENDENCY),
                                    'selected_file_limit')
                            digest, copied = hashlib.sha256(), 0
                            source = tf.extractfile(member)
                            while True:
                                check_time(deadline)
                                block = source.read(CHUNK)
                                if not block:
                                    break
                                copied += len(block)
                                self.hashed_bytes += len(block)
                                require(copied <= member.size and self.hashed_bytes <= MAX_HASHED_SELECTION,
                                        'selected_hash_byte_limit')
                                digest.update(block)
                            require(copied == member.size, 'selected_file_truncated')
                            value = {'kind': 'regular', 'bytes': copied, 'sha256': digest.hexdigest(),
                                     'executable_mode': bool(member.mode & 0o111)}
                        else:
                            value = {'kind': 'unsupported_link_or_special', 'type_hex': member.type.hex()}
                            if member.issym() or member.islnk():
                                value['link_target_sha256'] = sha(member.linkname.encode('utf-8', 'surrogateescape'))
                        updates[name] = value
                    tf.members.clear()
            # OCI whiteouts erase lower layers only, even if listed after a new file.
            for name, children in deletions:
                self.delete(name, children_only=children)
            for name, value in updates.items():
                previous = self.state.get(name, {}).get('kind')
                if value['kind'] != 'directory' or previous not in (None, 'directory'):
                    self.delete(name)
                self.state[name] = value
            require(len(self.state) <= 512, 'selected_state_limit')
            return count

    return HostReplay()


def source_checks(api, replay, expected):
    checks = []
    for row in expected['targets']:
        observed = replay.observe(row['target'])
        before = row['expected_before']
        if before['status'] == 'PRESENT':
            passed = observed is not None and all(observed[k] == before[k] for k in ('sha256', 'bytes'))
        else:
            passed = observed is None
        checks.append({'path': '/' + row['target'], 'expected': before,
                       'observed': observed or {'status': 'ABSENT_IN_REPLAYED_PHYSICAL_OCI'}, 'pass': passed})
    return checks


def dependency_checks(replay):
    result = []
    for name in sorted(DEP_FIXED | replay.dependency_names):
        blocked = any(replay.state.get(str(parent), {}).get('kind', 'directory') != 'directory'
                      for parent in PurePosixPath(name).parents if str(parent) != '.')
        result.append({'path': '/' + name, 'observed': replay.state.get(name, {'status': 'NOT_OBSERVED'}),
                       'ancestor_link_or_non_directory': blocked})
    return {'scope': 'STATIC_FIXED_PATH_CANDIDATES_ONLY', 'files': result,
            'imports_launches_and_dynamic_library_resolution': 'NOT_TESTED',
            'versions_and_dependency_completeness': 'NOT_CLAIMED',
            'links_followed': False}


def audit(path, api, outer, expected, report, deadline):
    path = safe_path(path)
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as source:
        before = os.fstat(source.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                and 0 < before.st_size <= MAX_ARCHIVE, 'archive_type_or_size')
        print('host_base_progress=whole_archive_digest', flush=True)
        digest, count = hashlib.sha256(), 0
        while True:
            check_time(deadline)
            block = source.read(CHUNK)
            if not block:
                break
            digest.update(block)
            count += len(block)
            if count % (256 * CHUNK) == 0:
                print('host_base_progress=whole_archive_digest bytes=%d' % count, flush=True)
        require(digest.hexdigest() == ARCHIVE_SHA, 'whole_archive_hash_mismatch')
        report['archive'] = {'path': str(path), 'bytes': count, 'sha256': ARCHIVE_SHA}
        require(stamp(before) == stamp(os.fstat(source.fileno())), 'archive_changed')
        source.seek(0)
        with tarfile.open(fileobj=source, mode='r:', tarinfo=api.BoundedTarInfo) as archive:
            members = {}
            for member in archive:
                check_time(deadline)
                require(len(members) < LAYERS + 10 and member.name not in members, 'outer_members_limit_or_duplicate')
                members[member.name] = member
            def metadata(pin):
                member = members.get(outer.blob_path(pin))
                require(member is not None and member.isfile() and 0 < member.size <= api.MAX_FILE, 'metadata_type_or_size')
                raw = archive.extractfile(member).read(api.MAX_FILE + 1)
                require(len(raw) == member.size and sha(raw) == pin[7:], 'metadata_hash')
                return raw
            manifest_raw, config_raw = metadata(MANIFEST), metadata(CONFIG)
            blobs, ordered = outer.expected_blobs(manifest_raw, config_raw, MANIFEST, CONFIG, LAYERS)
            manifest, config = outer.parse_json(manifest_raw), outer.parse_json(config_raw)
            require(sum(x['size'] for x in manifest['layers']) == COMPRESSED_LAYER_BYTES, 'historical_layer_bytes_mismatch')
            diff_ids = config['rootfs']['diff_ids']
            del config, config_raw
            print('host_base_progress=outer_blob_closure', flush=True)
            report['outer_archive'] = outer.validate_archive(path, blobs, ordered, deadline, MANIFEST, CONFIG, TAG)
            require(report['outer_archive']['sha256'] == ARCHIVE_SHA
                    and report['outer_archive']['bytes'] == count, 'outer_reverification_mismatch')
            replay = make_replay(api, {row['target'] for row in expected['targets']}, report)
            def progress(number):
                replay.current_layer = manifest['layers'][number - 1]['digest']
                print('host_base_progress=layer number=%d total=%d' % (number, LAYERS), flush=True)
            report['layers'] = []
            api.verify_layers(archive, members, manifest, diff_ids, replay, deadline, progress, report['layers'])
            report['source_preconditions'] = source_checks(api, replay, expected)
            report['runtime_dependency_observations'] = dependency_checks(replay)
        require(stamp(before) == stamp(os.fstat(source.fileno())) == stamp(path.stat()), 'archive_changed_during_audit')
    report['all_128_diff_ids_verified'] = True
    report['all_source_preconditions_match'] = all(row['pass'] for row in report['source_preconditions'])
    report['status'] = 'DIAGNOSTIC_COMPLETE_REVIEW_REQUIRED'
    require(report['all_source_preconditions_match'], 'physical_source_preconditions_differ')


def fresh_output(path, base=OUTPUT_BASE):
    path, base = Path(path).absolute(), Path(base).absolute()
    require(path.parent == base and re.fullmatch(r'host-base-[a-f0-9]{16}', path.name), 'output_outside_fixed_scope')
    safe_path(base.parent)
    if not base.exists():
        os.mkdir(str(base), 0o700)
    safe_path(base)
    info = base.stat()
    require(stat.S_ISDIR(info.st_mode), 'output_base_not_directory')
    if os.name == 'posix':
        require(info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700, 'output_base_not_private_root')
    os.mkdir(str(path), 0o700)
    safe_path(path)
    return path


def write_report(directory, report):
    raw = (json.dumps(report, sort_keys=True, ensure_ascii=True, indent=2) + '\n').encode()
    require(len(raw) <= 2 * CHUNK, 'report_size_limit')
    safe_path(directory)
    path = directory / 'REPORT.json'
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    return path, sha(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    report = {'schema': 'portal-ppt-host-existing-oci-diagnostic.v1', 'status': 'INCOMPLETE',
              'archive_identity': {'manifest': MANIFEST, 'config': CONFIG, 'tag': TAG, 'platform': 'linux/arm64'},
              'ready_for_build': False, 'ready_for_rollout': False, 'errors': [], 'diagnostic_headers': [],
              'image_env_or_runtime_config': 'NOT_PROJECTED', 'network_or_subprocess': False,
              'image_execution': False, 'rootfs_extracted': False, 'production_mutation': False,
              'frozen_inputs': LOCKS, 'all_128_diff_ids_verified': False,
              'review_scope': 'PHYSICAL_ARCHIVE_ONLY_NOT_ACTIVE_POD_OR_MOUNTS',
              'automatic_retry': False}
    directory, api, outer = None, None, None
    started = time.monotonic()
    previous = None
    try:
        require(os.name == 'posix' and os.geteuid() == 0 and os.uname().nodename == 'datax-01', 'root_datax_gate')
        directory = fresh_output(args.out)
        def timed_out(signum, frame):
            raise Refused('wall_clock_deadline_exceeded')
        previous = signal.signal(signal.SIGALRM, timed_out)
        signal.setitimer(signal.ITIMER_REAL, SECONDS)
        api, outer, expected = load_inputs()
        audit(ARCHIVE, api, outer, expected, report, started + SECONDS)
    except Exception as exc:
        if isinstance(exc, Refused) or (api and isinstance(exc, api.Refused)):
            code = str(exc)
        elif outer and isinstance(exc, outer.Failure):
            code = exc.code
        else:
            code = 'unexpected_read_failure_details_withheld'
        report['errors'].append(code)
    finally:
        if previous is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous)
    report['elapsed_seconds'] = round(time.monotonic() - started, 3)
    if directory is not None:
        path, digest = write_report(directory, report)
        print(json.dumps({'status': report['status'], 'errors': report['errors'], 'report_path': str(path),
                          'report_sha256': digest, 'ready_for_build': False, 'ready_for_rollout': False}, sort_keys=True))
    else:
        print(json.dumps({'status': 'REFUSED_BEFORE_COLLECTION', 'errors': report['errors']}, sort_keys=True))
    # REVIEW_REQUIRED is intentionally nonzero; wrappers must preserve/export it,
    # stop, and never automatically rerun or treat a diagnostic as approval.
    return 2


if __name__ == '__main__':
    sys.exit(main())

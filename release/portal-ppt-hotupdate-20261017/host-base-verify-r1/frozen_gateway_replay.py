#!/usr/bin/env python3
"""Pinned local Gateway OCI audit. No extraction, Docker, network or application imports.

The only write is a new safe JSON report below this preparation directory.
This does not approve/start a build or alter the frozen UNBOUND packages.
"""
import argparse
import ast
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import time
import uuid

HERE = Path(__file__).absolute().parent
ARCHIVE = Path('C:/Users/15285/Downloads/portal-ppt-gateway-base-20261017-r1/gateway.base.oci.tar')
ARCHIVE_SHA = 'e9a0ba974ebfd9fe9c40ff5d0de9ce0359abf8617d7d534a856ed7703d334098'
ARCHIVE_BYTES = 870312448
EXPORT_SHA = '23f61ad3164f37856c3c320c34a4dd8a0c81020e2bc9d14a783736f7a8a0be0f'
APPLY_SHA = 'fd16dfe706e9b1f6b10ebe876ac8265661f9dea0163e20a7059465ade2883778'
REVIEW_SHA = '06506208a29951f3abd7d561374d2fad71c7b22135e2f68e1dca0e799326162f'
MANIFESTS = {
    'gateway': ('7c0095eb91033e71a81f27ad7aaf0b506b727aff7235185ed2e1811f56329967', 32),
    'gateway-rollback-compatible': ('925a60c72716e9acbbe993b1cb59d190d5834d1ae196806c6149ebe4e0657f08', 1),
}
MIGRATIONS = 'app/backend/packages/harness/deerflow/persistence/migrations/versions'
MAX_FILE = 4 * 1024 * 1024
MAX_SELECTED = 16 * 1024 * 1024
MAX_LAYER = 8 * 1024 ** 3
MAX_EXPANDED = 24 * 1024 ** 3
MAX_HEADERS = 400000
SECONDS = 1800
CHUNK = 1024 * 1024


class Refused(Exception):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def check_time(deadline):
    require(time.monotonic() < deadline, 'audit_timeout')


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def safe_path(path):
    path = Path(path).absolute()
    for part in (path,) + tuple(path.parents):
        info = part.lstat()
        require(not stat.S_ISLNK(info.st_mode) and not getattr(info, 'st_file_attributes', 0) & 1024,
                'linked_input')
    return path


def read_small(path, limit=MAX_FILE):
    path = safe_path(path)
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size <= limit, 'input_type_or_size')
        raw = stream.read(limit + 1)
        require(len(raw) == before.st_size and stamp(before) == stamp(os.fstat(stream.fileno())), 'input_drift')
        return raw


def load_export_verifier():
    path = HERE / 'frozen_export_verifier.py'
    raw = read_small(path)
    require(sha(raw) == EXPORT_SHA, 'export_verifier_changed')
    # Compile the exact verified bytes rather than reopening a mutable path.
    module = type(sys)('frozen_gateway_export_verifier')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def load_packages():
    root = HERE.parent / 'build-inputs-r1'
    require(sha(read_small(root / 'apply_overlay.py')) == APPLY_SHA, 'apply_tool_changed')
    require(sha(read_small(root / 'INDEPENDENT-OVERLAY-REVIEW.json')) == REVIEW_SHA, 'overlay_review_changed')
    packages, payloads = {}, {}
    for name, (expected, count) in MANIFESTS.items():
        raw = read_small(root / name / 'overlay-manifest.json')
        require(sha(raw) == expected, 'overlay_manifest_changed')
        doc = json.loads(raw)
        require(doc['component'] == name and len(doc['files']) == count, 'overlay_scope_changed')
        payload = {}
        for row in doc['files']:
            path = row['target_path']
            require(path.startswith('backend/') and PurePosixPath(path).as_posix() == path
                    and not any(p in ('', '.', '..') for p in path.split('/')), 'overlay_target_path')
            data = read_small(root / name / 'payload' / path)
            require({'sha256': sha(data), 'bytes': len(data)} == row['after'], 'payload_changed')
            ast.parse(data, filename=path)
            payload['app/' + path] = data
        packages[name], payloads[name] = doc, payload
    require(packages['gateway']['migration_graph'] == packages['gateway-rollback-compatible']['migration_graph'],
            'migration_expectations_differ')
    return packages, payloads


def normalized_name(name):
    require(isinstance(name, str) and '\\' not in name and not name.startswith('/')
            and not any(ord(c) < 32 for c in name), 'unsafe_layer_path')
    while name.startswith('./'):
        name = name[2:]
    name = name.rstrip('/')
    if name in ('', '.'):
        return ''
    require(all(p not in ('', '.', '..') for p in name.split('/')), 'unsafe_layer_path')
    return name


def beneath(name, parent):
    return not parent or name == parent or name.startswith(parent + '/')


class DigestReader:
    """Bounded uncompressed bytes, including tar padding/trailer, for diffID."""
    def __init__(self, source, deadline, limit=MAX_LAYER):
        self.source, self.deadline, self.limit = source, deadline, limit
        self.hash = hashlib.sha256()
        self.count = 0

    def read(self, size=-1):
        check_time(self.deadline)
        require(0 <= size <= CHUNK, 'unbounded_layer_read')
        data = self.source.read(size)
        self.count += len(data)
        require(self.count <= self.limit, 'expanded_layer_limit')
        self.hash.update(data)
        return data


class SourceReplay:
    """In-memory fixed-path overlayfs projection; never resolve/extract links."""
    def __init__(self, targets):
        self.targets = set(targets)
        self.ancestors = set()
        for name in list(self.targets) + [MIGRATIONS]:
            self.ancestors.update(str(p) for p in PurePosixPath(name).parents if str(p) != '.')
        self.ancestors.add(MIGRATIONS)
        self.state = {}

    def selected(self, name):
        return name in self.targets or name in self.ancestors or beneath(name, MIGRATIONS)

    def delete(self, path, children_only=False):
        for name in list(self.state):
            if beneath(name, path) and (not children_only or name != path):
                del self.state[name]

    def layer(self, stream, deadline):
        updates, deletions, names, count, retained, name_bytes = {}, [], set(), 0, 0, 0
        with tarfile.open(fileobj=stream, mode='r|', bufsize=10240, tarinfo=BoundedTarInfo) as tf:
            for member in tf:
                check_time(deadline)
                count += 1
                require(count <= MAX_HEADERS, 'layer_header_limit')
                name = normalized_name(member.name)
                name_bytes += len(name.encode('utf-8'))
                require(len(name) <= 4096 and name_bytes <= 32 * CHUNK, 'layer_path_metadata_limit')
                if not name:
                    require(member.isdir(), 'non_directory_layer_root')
                    continue
                require(name not in names, 'duplicate_layer_path')
                names.add(name)
                require(not member.sparse and not any(k.startswith('GNU.sparse') for k in member.pax_headers),
                        'sparse_layer_member')
                parent, leaf = name.rsplit('/', 1) if '/' in name else ('', name)
                if leaf.startswith('.wh.'):
                    require(member.isfile() and member.size == 0, 'invalid_whiteout')
                    if leaf == '.wh..wh..opq':
                        deletions.append((parent, True))
                    else:
                        require(leaf[4:] not in ('', '.', '..'), 'invalid_whiteout')
                        deletions.append(((parent + '/' if parent else '') + leaf[4:], False))
                elif self.selected(name):
                    if member.isdir():
                        item = {'kind': 'directory'}
                    elif member.isfile():
                        require(0 <= member.size <= MAX_FILE, 'selected_file_limit')
                        raw = tf.extractfile(member).read(MAX_FILE + 1)
                        require(len(raw) == member.size, 'truncated_selected_file')
                        retained += len(raw)
                        require(retained <= MAX_SELECTED, 'selected_layer_total_limit')
                        item = {'kind': 'regular', 'sha256': sha(raw), 'bytes': len(raw), 'data': raw}
                    else:
                        # Do not emit or resolve link targets, device numbers or other content.
                        item = {'kind': 'unsupported_link_or_special'}
                    updates[name] = item
                tf.members.clear()  # Streaming iterator otherwise retains every header.
        # OCI whiteouts affect only lower layers, independent of archive order.
        for path, opaque in deletions:
            self.delete(path, children_only=opaque)
        for name, item in updates.items():
            if item['kind'] != 'directory' or self.state.get(name, {}).get('kind') not in (None, 'directory'):
                self.delete(name)
            self.state[name] = item
        require(sum(v.get('bytes', 0) for v in self.state.values()) <= MAX_SELECTED, 'selected_state_total_limit')
        return count

    def observe(self, path):
        for parent in PurePosixPath(path).parents:
            value = self.state.get(str(parent))
            require(value is None or value['kind'] == 'directory', 'selected_ancestor_not_directory')
        value = self.state.get(path)
        require(value is None or value['kind'] == 'regular', 'selected_target_not_regular')
        return value


class BoundedTarInfo(tarfile.TarInfo):
    def _proc_member(self, archive):
        # stdlib tarfile reads PAX/longname data before yielding a member.
        # Bound those allocations before its decoder, not after iteration.
        if self.type in (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK):
            require(0 <= self.size <= 65536, 'layer_extended_header_limit')
        require(self.type != tarfile.GNUTYPE_SPARSE, 'sparse_layer_member')
        return super()._proc_member(archive)

    def _proc_gnusparse_00(self, *args):
        raise Refused('sparse_layer_member')

    _proc_gnusparse_01 = _proc_gnusparse_00
    _proc_gnusparse_10 = _proc_gnusparse_00


def layer_stream(archive, member, media):
    raw = archive.extractfile(member)
    if media in ('application/vnd.oci.image.layer.v1.tar+gzip', 'application/vnd.docker.image.rootfs.diff.tar.gzip'):
        return raw, gzip.GzipFile(fileobj=raw)
    require(media == 'application/vnd.oci.image.layer.v1.tar', 'unsupported_layer_compression_no_fallback')
    return raw, raw


def verify_layers(archive, members, manifest, diff_ids, replay, deadline, progress=None, result=None):
    result, total = ([] if result is None else result), 0
    for number, (row, expected) in enumerate(zip(manifest['layers'], diff_ids), 1):
        if progress:
            progress(number)
        raw, source = layer_stream(archive, members['blobs/sha256/' + row['digest'][7:]], row['mediaType'])
        try:
            reader = DigestReader(source, deadline, limit=min(MAX_LAYER, MAX_EXPANDED - total))
            headers = replay.layer(reader, deadline)
            while reader.read(CHUNK):
                pass
            require('sha256:' + reader.hash.hexdigest() == expected, 'diff_id_mismatch')
            total += reader.count
            result.append({'number': number, 'blob_digest': row['digest'], 'blob_bytes': row['size'],
                           'diff_id': expected, 'uncompressed_bytes': reader.count, 'headers': headers})
        finally:
            source.close()
            if source is not raw:
                raw.close()
    return result


def compare_sources(replay, packages, payloads):
    results = {}
    for name, package in packages.items():
        checks = []
        for row in package['files']:
            path = 'app/' + row['target_path']
            observed = replay.observe(path)
            expected = row['before']
            if expected['status'] == 'ABSENT':
                passed = observed is None
            else:
                passed = observed is not None and observed['sha256'] == expected['sha256'] and observed['bytes'] == expected['bytes']
            checks.append({'path': '/' + path, 'expected': expected,
                           'observed': {k: observed[k] for k in ('sha256', 'bytes')} if observed else {'status': 'ABSENT'},
                           'pass': passed})
        graph = package['migration_graph']
        expected_paths = {'app/' + row['target_path'] for row in graph['files']}
        physical_paths = set()
        for path in replay.state:
            if path.startswith(MIGRATIONS + '/'):
                leaf = path[len(MIGRATIONS) + 1:]
                if leaf == '__init__.py' or leaf == '__pycache__' or leaf.startswith('__pycache__/'):
                    continue
                require('/' not in leaf and re.fullmatch(r'[0-9]{4}_[a-z0-9_]+\.py', leaf), 'unexpected_migration_entry')
                replay.observe(path)
                physical_paths.add(path)
        predicted_paths = physical_paths | {p for p in payloads[name] if p.startswith(MIGRATIONS + '/')}
        require(predicted_paths == expected_paths, 'predicted_migration_path_set_mismatch')
        revisions = {}
        migration_checks = []
        for row in graph['files']:
            path = 'app/' + row['target_path']
            original = replay.observe(path)
            data = payloads[name].get(path, original['data'] if original else None)
            require(data is not None and len(data) == row['bytes'] and sha(data) == row['sha256'], 'predicted_migration_hash_mismatch')
            fields = {}
            for node in ast.parse(data).body:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, ast.AnnAssign) else []
                for target in targets:
                    if isinstance(target, ast.Name) and target.id in ('revision', 'down_revision', 'branch_labels', 'depends_on'):
                        require(target.id not in fields, 'duplicate_migration_metadata')
                        fields[target.id] = ast.literal_eval(node.value)
            require(set(fields) == {'revision', 'down_revision', 'branch_labels', 'depends_on'}
                    and fields['revision'] == row['revision'] and fields['down_revision'] == row['down_revision']
                    and fields['branch_labels'] is None and fields['depends_on'] is None, 'migration_metadata_mismatch')
            require(fields['revision'] not in revisions, 'duplicate_migration_revision')
            revisions[fields['revision']] = fields['down_revision']
            migration_checks.append({'path': '/' + path, 'sha256': sha(data), 'bytes': len(data),
                                     'origin': 'overlay' if path in payloads[name] else 'physical_base'})
        require(all(p is None or p in revisions for p in revisions.values()), 'migration_parent_missing')
        heads = set(revisions) - set(revisions.values())
        require(heads == {graph['expected_head']} == {'0020_ppt_confirmation'} and len(revisions) == 20, 'migration_head_or_count')
        visited, current = set(), graph['expected_head']
        while current is not None:
            require(current not in visited, 'migration_cycle')
            visited.add(current)
            current = revisions[current]
        require(visited == set(revisions), 'migration_not_connected')
        results[name] = {'checks': checks, 'all_before_match': all(x['pass'] for x in checks),
                         'physical_migrations': len(physical_paths), 'predicted_migrations': 20,
                         'predicted_head': graph['expected_head'], 'migration_files': migration_checks}
    return results


def verify(path, report, deadline):
    helper = load_export_verifier()
    packages, payloads = load_packages()
    path = safe_path(path)
    with path.open('rb') as source:
        initial = os.fstat(source.fileno())
        require(stat.S_ISREG(initial.st_mode) and initial.st_nlink == 1 and initial.st_size == ARCHIVE_BYTES, 'archive_type_or_size')
        digest = hashlib.sha256()
        while True:
            check_time(deadline)
            block = source.read(4 * CHUNK)
            if not block:
                break
            digest.update(block)
        require(digest.hexdigest() == ARCHIVE_SHA, 'whole_archive_hash_mismatch')
        report['archive'] = {'path': str(path), 'bytes': initial.st_size, 'sha256': ARCHIVE_SHA}
        require(stamp(initial) == stamp(os.fstat(source.fileno())), 'archive_changed')
        source.seek(0)
        with tarfile.open(fileobj=source, mode='r:') as archive:
            members = {}
            for member in archive:
                require(len(members) < 40 and member.name not in members, 'outer_member_count_or_duplicate')
                members[member.name] = member
            def metadata(digest):
                member = members.get(helper.blob_path(digest))
                require(member is not None and member.isfile() and 0 < member.size <= MAX_FILE, 'metadata_member_invalid')
                raw = archive.extractfile(member).read(MAX_FILE + 1)
                require(len(raw) == member.size and sha(raw) == digest[7:], 'metadata_hash_mismatch')
                return raw
            manifest_raw, config_raw = metadata(helper.MANIFEST), metadata(helper.CONFIG)
            blobs, layer_paths = helper.expected_blobs(manifest_raw, config_raw)
            outer = helper.validate_archive(path, blobs, layer_paths, deadline)
            require(outer['sha256'] == ARCHIVE_SHA and outer['bytes'] == ARCHIVE_BYTES
                    and outer['verified_unique_blobs'] == 31, 'archive_reverification_mismatch')
            report['outer_archive'] = outer
            manifest = helper.parse_json(manifest_raw)
            config = helper.parse_json(config_raw)
            diff_ids = config['rootfs']['diff_ids']
            # No access/projection of config.Env, command arguments or other runtime secrets.
            del config, config_raw
            report['identity'] = {'manifest': helper.MANIFEST, 'config': helper.CONFIG,
                                  'platform': 'linux/arm64', 'tag': helper.TAG}
            targets = {'app/' + row['target_path'] for p in packages.values() for row in p['files']}
            replay = SourceReplay(targets)
            report['layers'] = []
            verify_layers(archive, members, manifest, diff_ids, replay, deadline,
                lambda n: print('Gateway local audit: layer %d/29' % n, file=sys.stderr, flush=True),
                result=report['layers'])
            report['source_preconditions'] = compare_sources(replay, packages, payloads)
        require(stamp(initial) == stamp(os.fstat(source.fileno())) == stamp(path.stat()), 'archive_changed_during_audit')
    report['all_source_preconditions_match'] = all(v['all_before_match'] for v in report['source_preconditions'].values())
    require(report['all_source_preconditions_match'], 'physical_base_source_differs_from_frozen_before')
    report['status'] = 'VERIFIED_OCI_DIFFIDS_AND_FIXED_SOURCE_PRECONDITIONS'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--verify', action='store_true', required=True)
    parser.add_argument('--archive', type=Path, default=ARCHIVE)
    args = parser.parse_args(argv)
    report = {'schema': 'portal-ppt-gateway-local-base-audit.v1', 'status': 'INCOMPLETE',
              'ready_for_build': False, 'ready_for_rollout': False, 'image_env': 'NOT_PROJECTED',
              'rootfs_extracted': False, 'docker_or_network_called': False, 'database_access': False,
              'build_executed': False, 'binding_packages_written': False,
              'frozen_overlay_manifests': {k: v[0] for k, v in MANIFESTS.items()}, 'errors': []}
    try:
        verify(args.archive, report, time.monotonic() + SECONDS)
    except Exception as exc:
        code = str(exc) if isinstance(exc, Refused) else getattr(exc, 'code', 'unexpected_audit_failure_details_withheld')
        report['errors'].append(code)
    # Fixed new report destination; no caller-controlled output directory or overwrite.
    path = HERE / ('BASE-VERIFICATION-' + uuid.uuid4().hex + '.json')
    raw = (json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + '\n').encode()
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    print(json.dumps({'status': report['status'], 'errors': report['errors'], 'report_path': str(path),
                      'report_sha256': sha(raw), 'ready_for_build': False, 'ready_for_rollout': False}, sort_keys=True))
    return 0 if not report['errors'] else 2


if __name__ == '__main__':
    sys.exit(main())

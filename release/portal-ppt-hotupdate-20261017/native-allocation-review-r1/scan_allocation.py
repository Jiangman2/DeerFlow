#!/usr/bin/env python3
"""Fixed local missing-layer allocation observations; never extract or execute."""
import argparse
import ctypes
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import tarfile
import time

HERE = Path(__file__).absolute().parent
PREP = HERE.parent
MERGE = Path('C:/Users/15285/Documents/deer-flow/portal-ppt-merge-20261017')
PINS = {
    'inventory': (MERGE / 'CANDIDATE-IMAGE-INVENTORY.json', 'e6662fbebf2210d73de5f64498fe7548a718570ef303181016802ea2c118dbef'),
    'catalog': (PREP / 'native-cache-diagnostic-r1/CATALOG.json', 'f68a13ce2bd6f50746ccdea53bf2f7b99627f2579c34f9f81b8d1e6b968ab5df'),
    'cache_report': (Path('C:/Users/15285/Downloads/REPORT(3).json'), '5a3b7b992a170204247d27a33b740b78d65f0d89c443032020669023c3e7ba15'),
}
ORDER = ['gateway-forward', 'gateway-rollback-compatible', 'frontend', 'ppt-master-runner-forward', 'ppt-master-runner-rollback-compatible']
CHUNK = 1024 * 1024
WALL_SECONDS = 900
CPU_SECONDS = 240
MAX_HEADERS = 400000
MAX_PATH_BYTES = 64 * CHUNK
START_WALL = START_CPU = None
ACTIVE = None
PROGRESS = 0


class Refused(Exception):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def bound():
    if START_WALL is not None:
        require(time.monotonic() - START_WALL < WALL_SECONDS, 'wall_deadline')
        require(time.process_time() - START_CPU < CPU_SECONDS, 'cpu_deadline')


def stamp(value):
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def safe_path(path):
    path = Path(path).absolute()
    for part in (path,) + tuple(path.parents):
        s = part.lstat()
        require(not stat.S_ISLNK(s.st_mode) and not getattr(s, 'st_file_attributes', 0) & 1024, 'linked_input')
    return path


def pinned(name):
    path, expected = PINS[name]
    safe_path(path)
    raw = path.read_bytes()
    require(len(raw) <= 4 * CHUNK and sha(raw) == expected, 'input_pin_' + name)
    return json.loads(raw)


def cpu_limit():
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        process = kernel.GetCurrentProcess()
        current, system = ctypes.c_size_t(), ctypes.c_size_t()
        kernel.GetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_size_t)]
        kernel.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
        kernel.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        require(kernel.GetProcessAffinityMask(process, ctypes.byref(current), ctypes.byref(system)), 'affinity_query_failed')
        single = current.value & -current.value
        require(single and kernel.SetProcessAffinityMask(process, single), 'affinity_set_failed')
        require(kernel.SetPriorityClass(process, 0x4000), 'below_normal_priority_failed')
        return dict(logical_cpus=1, affinity_mask=single, priority='BELOW_NORMAL', wall_seconds=WALL_SECONDS, cpu_seconds=CPU_SECONDS, enforcement='single-thread affinity plus checked wall/process CPU limits')
    allowed = os.sched_getaffinity(0)
    os.sched_setaffinity(0, {min(allowed)})
    return dict(logical_cpus=1, wall_seconds=WALL_SECONDS, cpu_seconds=CPU_SECONDS)


class Reader:
    """Hash every byte of a bounded stream, including TAR trailing padding."""
    def __init__(self, source, maximum):
        self.source, self.maximum = source, maximum
        self.hash = hashlib.sha256()
        self.count = 0

    def read(self, size):
        bound()
        require(0 <= size <= CHUNK, 'unbounded_read')
        raw = self.source.read(size)
        self.count += len(raw)
        require(self.count <= self.maximum, 'stream_size_bound')
        self.hash.update(raw)
        return raw


def exact(reader, size):
    require(0 <= size <= CHUNK, 'exact_read_bound')
    value = bytearray()
    while len(value) < size:
        raw = reader.read(size - len(value))
        require(raw, 'truncated_tar')
        value.extend(raw)
    return bytes(value)


def skip(reader, size):
    while size:
        raw = reader.read(min(size, CHUNK))
        require(raw, 'truncated_member_data')
        size -= len(raw)


def pax_parse(raw):
    result, offset = {}, 0
    while offset < len(raw):
        space = raw.find(b' ', offset, offset + 24)
        require(space > offset and raw[offset:space].isdigit(), 'pax_record_length')
        size = int(raw[offset:space])
        require(size > space - offset + 3 and offset + size <= len(raw), 'pax_record_bounds')
        record = raw[space + 1:offset + size]
        require(record.endswith(b'\n') and b'=' in record, 'pax_record_shape')
        key, value = record[:-1].split(b'=', 1)
        require(key and key not in result, 'duplicate_pax_key')
        require(not key.startswith(b'GNU.sparse') and key != b'SCHILY.realsize'
                and not (key == b'SCHILY.filetype' and value == b'sparse'), 'sparse_pax_rejected')
        result[key] = value
        offset += size
    return result


def normalize(raw):
    require(isinstance(raw, bytes) and len(raw) <= 4096 and b'\0' not in raw and b'\\' not in raw and not raw.startswith(b'/'), 'unsupported_path')
    while raw.startswith(b'./'):
        raw = raw[2:]
    raw = raw.rstrip(b'/')
    if raw in (b'', b'.'):
        return b''
    require(all(x not in (b'', b'.', b'..') for x in raw.split(b'/')), 'path_traversal')
    return raw


def tar_stats(reader):
    stats = dict(headers=0, entries=0, regular=0, directories=0, symlinks=0, hardlinks=0,
                 sparse=0, xattr_records=0, xattr_value_bytes=0, xattr_name_bytes=0,
                 xattr_files=0, xattr_per_file_4k_roundup_bytes=0, pax_headers=0,
                 gnu_long_headers=0, regular_data_bytes=0, regular_4k_roundup_bytes=0,
                 zero_size_regular=0, symlink_target_bytes=0, hardlink_target_bytes=0,
                 whiteout_markers=0, opaque_whiteout_markers=0, tar_padding_bytes=0)
    parents, directory_names, all_names = set(), set(), set()
    path_bytes = parent_bytes = 0
    pending, global_pax = {}, {}
    long_name = long_link = None
    while True:
        header = exact(reader, 512)
        if header == bytes(512):
            require(exact(reader, 512) == bytes(512), 'single_zero_end_marker')
            stats['tar_padding_bytes'] = 1024
            while True:
                tail = reader.read(CHUNK)
                if not tail:
                    break
                require(not any(tail), 'nonzero_tar_tail')
                stats['tar_padding_bytes'] += len(tail)
            break
        stats['headers'] += 1
        require(stats['headers'] <= MAX_HEADERS, 'header_count_bound')
        info = tarfile.TarInfo.frombuf(header, 'utf-8', 'surrogateescape')
        require(info.size >= 0, 'negative_member_size')
        if info.type in (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK):
            require(info.size <= 65536, 'extended_header_bound')
            data = exact(reader, info.size)
            skip(reader, (-info.size) % 512)
            if info.type in (tarfile.XHDTYPE, tarfile.XGLTYPE):
                fields = pax_parse(data)
                if info.type == tarfile.XGLTYPE:
                    global_pax.update(fields)
                    require(sum(len(k) + len(v) for k, v in global_pax.items()) <= 65536, 'global_pax_bound')
                else:
                    require(not pending, 'multiple_pending_pax')
                    pending = fields
                stats['pax_headers'] += 1
            elif info.type == tarfile.GNUTYPE_LONGNAME:
                require(long_name is None, 'multiple_long_name')
                long_name = data.rstrip(b'\0')
                stats['gnu_long_headers'] += 1
            else:
                require(long_link is None, 'multiple_long_link')
                long_link = data.rstrip(b'\0')
                stats['gnu_long_headers'] += 1
            continue
        require(info.type in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE, tarfile.SYMTYPE, tarfile.LNKTYPE), 'unsupported_or_sparse_header_type_' + info.type.hex())
        fields = dict(global_pax)
        fields.update(pending)
        pending = {}
        require(not any(k.startswith(b'GNU.sparse') for k in fields), 'sparse_rejected')
        size = info.size
        if b'size' in fields:
            require(fields[b'size'].isdigit() and len(fields[b'size']) <= 20, 'pax_size_invalid')
            size = int(fields[b'size'])
        require(size <= reader.maximum, 'member_bound')
        name = normalize(fields.get(b'path', long_name if long_name is not None else info.name.encode('utf-8', 'surrogateescape')))
        link = fields.get(b'linkpath', long_link if long_link is not None else info.linkname.encode('utf-8', 'surrogateescape'))
        long_name = long_link = None
        require(len(link) <= 4096, 'link_target_bound')
        stats['entries'] += 1
        path_bytes += len(name)
        require(path_bytes <= MAX_PATH_BYTES, 'path_memory_bound')
        all_names.add(name)
        parts = name.split(b'/') if name else []
        for n in range(1, len(parts)):
            parent = b'/'.join(parts[:n])
            if parent not in parents:
                parent_bytes += len(parent)
            parents.add(parent)
        require(len(parents) <= MAX_HEADERS and parent_bytes <= MAX_PATH_BYTES, 'parent_count_or_memory_bound')
        xattrs = [(k, v) for k, v in fields.items() if k.startswith((b'SCHILY.xattr.', b'LIBARCHIVE.xattr.'))]
        stats['xattr_records'] += len(xattrs)
        stats['xattr_value_bytes'] += sum(len(v) for _, v in xattrs)
        attribute_name_bytes = sum(len(k.split(b'xattr.', 1)[1]) for k, _ in xattrs)
        stats['xattr_name_bytes'] += attribute_name_bytes
        attribute_bytes = attribute_name_bytes + sum(len(v) for _, v in xattrs)
        stats['xattr_per_file_4k_roundup_bytes'] += ((attribute_bytes + 4095) // 4096) * 4096
        stats['xattr_files'] += int(bool(xattrs))
        if info.type in (tarfile.REGTYPE, tarfile.AREGTYPE):
            stats['regular'] += 1
            stats['regular_data_bytes'] += size
            stats['regular_4k_roundup_bytes'] += ((size + 4095) // 4096) * 4096
            stats['zero_size_regular'] += int(size == 0)
            leaf = parts[-1] if parts else b''
            stats['whiteout_markers'] += int(leaf.startswith(b'.wh.'))
            stats['opaque_whiteout_markers'] += int(leaf == b'.wh..wh..opq')
        else:
            require(size == 0, 'nonregular_data_unsupported')
            if info.type == tarfile.DIRTYPE:
                stats['directories'] += 1
                directory_names.add(name)
            elif info.type == tarfile.SYMTYPE:
                stats['symlinks'] += 1
                stats['symlink_target_bytes'] += len(link)
            else:
                stats['hardlinks'] += 1
                stats['hardlink_target_bytes'] += len(link)
        skip(reader, size + (-size) % 512)
    require(not pending and long_name is None and long_link is None, 'unconsumed_extended_header')
    stats.update(unique_entry_paths=len(all_names), explicit_unique_directory_paths=len(directory_names), parent_directory_paths=len(parents),
                 parents_not_explicit_directories=len(parents - directory_names),
                 xattr_per_file_4k_blocks=stats['xattr_per_file_4k_roundup_bytes'] // 4096,
                 duplicate_entry_paths=stats['entries'] - len(all_names),
                 path_name_bytes=path_bytes, uncompressed_tar_bytes=reader.count)
    return stats


def scan_blob(archive, member, descriptor, layer):
    source = Reader(archive.extractfile(member), descriptor['size'])
    media = descriptor['mediaType']
    if media in ('application/vnd.oci.image.layer.v1.tar+gzip', 'application/vnd.docker.image.rootfs.diff.tar.gzip'):
        expanded = gzip.GzipFile(fileobj=source, mode='rb')
    else:
        require(media == 'application/vnd.oci.image.layer.v1.tar', 'unsupported_layer_media')
        expanded = source
    reader = Reader(expanded, layer['uncompressed_bytes_upper_bound'])
    result = tar_stats(reader)
    if expanded is not source:
        expanded.close()
    # Consume any remaining compressed bytes for the complete descriptor digest.
    while source.read(CHUNK):
        pass
    require(source.count == descriptor['size'] and 'sha256:' + source.hash.hexdigest() == descriptor['digest'], 'compressed_digest_or_size')
    require('sha256:' + reader.hash.hexdigest() == layer['diff_id'], 'expanded_diff_id')
    if layer['bound_kind'] == 'VERIFIED_EXACT':
        require(reader.count == layer['uncompressed_bytes_upper_bound'], 'exact_expanded_size')
    return dict(result, blob_digest=descriptor['digest'], compressed_bytes=source.count,
                diff_id=layer['diff_id'], compressed_digest_verified=True, diff_id_verified=True)


def image_metadata(archive, row):
    members, found = {}, {}
    wanted = {'index.json', 'blobs/sha256/' + row['manifest_digest'][7:], 'blobs/sha256/' + row['config_digest'][7:]}
    for i, member in enumerate(archive):
        bound()
        require(i < 4096 and member.name not in members, 'outer_headers_or_duplicate')
        require(not member.pax_headers and not member.linkname, 'outer_extended_or_link')
        require(member.isfile() or member.isdir(), 'outer_special_type')
        members[member.name] = member
        if member.name in wanted:
            require(member.isfile() and 0 < member.size <= 4 * CHUNK, 'metadata_size_or_type')
            found[member.name] = archive.extractfile(member).read(4 * CHUNK + 1)
    require(set(found) == wanted, 'metadata_missing')
    index = json.loads(found['index.json'])
    require(len(index.get('manifests', [])) == 1 and index['manifests'][0]['digest'] == row['manifest_digest'], 'index_identity')
    docs = {}
    for key in ('manifest_digest', 'config_digest'):
        raw = found['blobs/sha256/' + row[key][7:]]
        require('sha256:' + sha(raw) == row[key], 'metadata_digest')
        docs[key] = json.loads(raw)
    manifest, config = docs['manifest_digest'], docs['config_digest']
    require(manifest['config']['digest'] == row['config_digest'] and config['architecture'] == 'arm64' and config['os'] == 'linux', 'config_identity')
    require(len(manifest['layers']) == len(config['rootfs']['diff_ids']), 'layer_count')
    return members, manifest['layers'], config['rootfs']['diff_ids']


def sum_stats(rows):
    keys = [k for k, value in rows[0].items() if type(value) is int] if rows else []
    return {key: sum(row[key] for row in rows) for key in keys}


def collect(result):
    global ACTIVE
    inventory, catalog, report = pinned('inventory'), pinned('catalog'), pinned('cache_report')
    require(report['status'] == 'COMPLETE_REVIEW_REQUIRED' and report['target_report']['relevant_cache_stable'], 'cache_report_state')
    before, after = [report['target_report']['samples'][k]['snapshots'] for k in ('before', 'after')]
    lookup = {x['role']: x for x in inventory['images']}
    images = {x['role']: x for x in catalog['images']}
    cache, unique_chains, by_group = {}, {}, {}
    result['images'], result['blobs'] = [], cache
    for role in ORDER:
        ACTIVE = dict(role=role, phase='outer_metadata')
        row, image = lookup[role], images[role]
        require(row['archive']['host'] == 'local', 'nonlocal_archive_forbidden')
        path = safe_path(row['archive']['path'])
        initial = path.stat()
        require(stat.S_ISREG(initial.st_mode) and initial.st_size == row['archive']['bytes'], 'archive_size_or_type')
        parent, prefix, missing, image_chains = '', 0, [], []
        with path.open('rb') as handle:
            require(stamp(initial) == stamp(os.fstat(handle.fileno())), 'archive_open_drift')
            with tarfile.open(fileobj=handle, mode='r:') as archive:
                members, descriptors, diffs = image_metadata(archive, row)
                require(len(descriptors) == len(image['layer_indices']), 'catalog_layer_count')
                for position, (desc, diff, index) in enumerate(zip(descriptors, diffs, image['layer_indices'])):
                    layer = catalog['layers'][index]
                    require(desc['digest'] == layer['blob_digest'] and desc['size'] == layer['compressed_bytes'] and diff == layer['diff_id'], 'catalog_layer_identity')
                    chain = diff if not parent else 'sha256:' + sha((parent + ' ' + diff).encode('ascii'))
                    expected = dict(name=chain, parent=parent, kind='Committed')
                    cached = prefix == position and before.get(chain) == expected and after.get(chain) == expected
                    if cached:
                        prefix += 1
                    else:
                        missing.append(chain)
                        member = members.get('blobs/sha256/' + desc['digest'][7:])
                        require(member is not None and member.isfile() and member.size == desc['size'], 'layer_outer_member')
                        if desc['digest'] not in cache:
                            ACTIVE = dict(role=role, phase='layer_statistics', position=position, blob_digest=desc['digest'])
                            cache[desc['digest']] = scan_blob(archive, member, desc, layer)
                            print(json.dumps(dict(role=role, position=position, unique_blobs=len(cache), regular=cache[desc['digest']]['regular'])), flush=True)
                        else:
                            require(cache[desc['digest']]['diff_id'] == diff, 'cross_image_diff_conflict')
                        unique_chains.setdefault(chain, desc['digest'])
                        image_chains.append((chain, desc['digest']))
                    parent = chain
            require(stamp(initial) == stamp(os.fstat(handle.fileno())) == stamp(path.stat()), 'archive_changed')
        require(sum(cache[b]['uncompressed_tar_bytes'] for _, b in image_chains) <= image['expanded_image_upper_bound'], 'image_expanded_bound')
        expected_prefix = next(x['committed_prefix_at_both_samples'] for x in report['target_report']['estimate']['images'] if x['role'] == role)
        require(prefix == expected_prefix, 'reported_cache_prefix_mismatch')
        image_result = dict(role=role, archive_path=str(path), archive_receipt_sha256=row['archive']['sha256'], whole_archive_rehashed=False,
                            stable_stat=True, committed_prefix=prefix, missing_chain_count=len(missing),
                            missing_chains=missing, summed_layer_observations=sum_stats([cache[b] for _, b in image_chains]))
        result['images'].append(image_result)
        group = 'gateway' if role.startswith('gateway') else 'frontend' if role == 'frontend' else 'runner'
        by_group.setdefault(group, {}).update(image_chains)
        result['groups'] = {k: dict(unique_missing_chains=len(v), unique_blobs=len(set(v.values())),
                                   summed_layer_observations=sum_stats([cache[b] for b in v.values()])) for k, v in by_group.items()}
        # Preserve useful Gateway/FE progress without replacing the final output.
        save('PROGRESS-' + role + '.json', dict(image=image_result, groups=result['groups']))
    result['known_local_missing_chains'] = len(unique_chains)
    result['known_local_unique_blobs_decompressed'] = len(cache)
    result['known_local_sum'] = sum_stats([cache[b] for b in unique_chains.values()])
    result['host_tail'] = dict(scanned=False, reason='REMOTE_ONLY_ARCHIVE; cached 128-layer base excluded', missing_chains=1,
                               uncompressed_tar_upper_bound_bytes=8388608, declared_regular_payloads=12,
                               physical_allocation_not_measured=True)
    result['status'] = 'COMPLETE_OBSERVATIONS_REVIEW_REQUIRED'


def save(name, value):
    raw = (json.dumps(value, indent=2, sort_keys=True) + '\n').encode('ascii')
    with (HERE / name).open('xb') as stream:
        stream.write(raw)


def main():
    global START_WALL, START_CPU
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scan', action='store_true')
    args = parser.parse_args()
    require(args.scan, 'explicit_scan_required')
    limits = cpu_limit()
    START_WALL, START_CPU = time.monotonic(), time.process_time()
    result = dict(schema='portal-ppt-local-layer-allocation.v1', status='INCOMPLETE',
                  tool_sha256=sha(Path(__file__).read_bytes()), input_pins={k: dict(path=str(v[0]), sha256=v[1]) for k, v in PINS.items()},
                  limits=limits, no_extraction=True, no_image_execution=True, no_network=True,
                  strict_physical_filesystem_upper_bound=False, ready_for_import=False, ready_for_rollout=False,
                  semantics='Counts each unique missing snapshot chain once; duplicate blobs decoded once. Regular bytes round each header payload separately to 4KiB. Metadata, directories, copy-up, sparse allocation, xattr storage and runtime races are not a proved physical bound.')
    save('STARTED.json', result)
    rc = 0
    try:
        collect(result)
    except Exception as exc:
        result.update(error_code=str(exc) if isinstance(exc, Refused) else type(exc).__name__, failed_at=ACTIVE, automatic_retry=False)
        rc = 1
    result.update(wall_seconds=round(time.monotonic() - START_WALL, 3), cpu_seconds=round(time.process_time() - START_CPU, 3))
    save('ALLOCATION.json', result)
    print(json.dumps(dict(status=result['status'], error_code=result.get('error_code'), wall_seconds=result['wall_seconds'], cpu_seconds=result['cpu_seconds'])), flush=True)
    return rc


if __name__ == '__main__':
    raise SystemExit(main())

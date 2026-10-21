#!/usr/bin/env python3
"""Two fixed Gateway blobs only: same-layer hardlink target accounting."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import tarfile
import time
import types

HERE = Path(__file__).absolute().parent
CORE_SHA = 'b28b9b23c1aabe4921eea4aa17dedecc003dcb5f5ed2577fc25c200d4acac184'
ALLOCATION_SHA = '304460fa19e0b62e4193bebd03316950de2324a1d4f6496ad944c83cc844d1d6'
TARGETS = {
    'sha256:333125b5cee9fb6718bdcb523fc93b4adc71b7c37ada6146a20c193430e549b9': 2,
    'sha256:fd0abd8225aa5e0993e49e492b3d866b71873794c318423326c49f47047304f9': 42,
}


def load_core():
    path = HERE / 'scan_allocation.py'
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != CORE_SHA:
        raise RuntimeError('core_hash_mismatch')
    value = types.ModuleType('allocation_frozen')
    value.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), value.__dict__)
    return value


M = load_core()


def no_known_symlink_parent(name, state):
    parts = name.split(b'/')
    return not any(state.get(b'/'.join(parts[:n]), {}).get('kind') == 'symlink' for n in range(1, len(parts)))


def text_name(name):
    return name.decode('utf-8', 'backslashreplace')


def link_stats(reader):
    state, rows, pending, global_pax = {}, [], {}, {}
    long_name = long_link = None
    headers = entries = symlinks = max_symlink = 0
    while True:
        header = M.exact(reader, 512)
        if header == bytes(512):
            M.require(M.exact(reader, 512) == bytes(512), 'single_zero_end_marker')
            while True:
                tail = reader.read(M.CHUNK)
                if not tail:
                    break
                M.require(not any(tail), 'nonzero_tar_tail')
            break
        headers += 1
        M.require(headers <= M.MAX_HEADERS, 'header_count_bound')
        info = tarfile.TarInfo.frombuf(header, 'utf-8', 'surrogateescape')
        M.require(info.size >= 0, 'negative_member_size')
        if info.type in (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME, tarfile.GNUTYPE_LONGLINK):
            M.require(info.size <= 65536, 'extended_header_bound')
            data = M.exact(reader, info.size)
            M.skip(reader, (-info.size) % 512)
            if info.type in (tarfile.XHDTYPE, tarfile.XGLTYPE):
                fields = M.pax_parse(data)
                if info.type == tarfile.XGLTYPE:
                    global_pax.update(fields)
                    M.require(sum(len(k) + len(v) for k, v in global_pax.items()) <= 65536, 'global_pax_bound')
                else:
                    M.require(not pending, 'multiple_pending_pax')
                    pending = fields
            elif info.type == tarfile.GNUTYPE_LONGNAME:
                M.require(long_name is None, 'multiple_long_name')
                long_name = data.rstrip(b'\0')
            else:
                M.require(long_link is None, 'multiple_long_link')
                long_link = data.rstrip(b'\0')
            continue
        M.require(info.type in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE, tarfile.SYMTYPE, tarfile.LNKTYPE), 'unsupported_or_sparse_type')
        fields = dict(global_pax)
        fields.update(pending)
        pending = {}
        size = info.size
        if b'size' in fields:
            M.require(fields[b'size'].isdigit() and len(fields[b'size']) <= 20, 'invalid_pax_size')
            size = int(fields[b'size'])
        M.require(size <= reader.maximum, 'member_size_bound')
        name = M.normalize(fields.get(b'path', long_name if long_name is not None else info.name.encode('utf-8', 'surrogateescape')))
        target_raw = fields.get(b'linkpath', long_link if long_link is not None else info.linkname.encode('utf-8', 'surrogateescape'))
        long_name = long_link = None
        M.require(len(target_raw) <= 4096, 'target_length_bound')
        entries += 1
        M.require(name not in state, 'duplicate_layer_path')
        if info.type in (tarfile.REGTYPE, tarfile.AREGTYPE):
            state[name] = dict(kind='regular', ultimate=name, size=size, entry=entries)
        else:
            M.require(size == 0, 'nonregular_data')
            if info.type == tarfile.DIRTYPE:
                state[name] = dict(kind='directory')
            elif info.type == tarfile.SYMTYPE:
                state[name] = dict(kind='symlink')
                symlinks += 1
                max_symlink = max(max_symlink, len(target_raw))
            else:
                row = dict(entry=entries, path=text_name(name), raw_target_sha256=M.sha(target_raw),
                           raw_target_bytes=len(target_raw), source_has_no_prior_symlink_parent=no_known_symlink_parent(name, state))
                try:
                    target = M.normalize(target_raw)
                    prior = state.get(target)
                    row['normalized_target'] = text_name(target)
                    row['target_has_no_prior_symlink_parent'] = no_known_symlink_parent(target, state)
                    resolved = prior and prior.get('kind') in ('regular', 'resolved_hardlink') and row['source_has_no_prior_symlink_parent'] and row['target_has_no_prior_symlink_parent']
                    if resolved:
                        row.update(resolution='PRIOR_SAME_LAYER_REGULAR' if prior['kind'] == 'regular' else 'PRIOR_SAME_LAYER_HARDLINK_TO_REGULAR',
                                   ultimate_regular_path=text_name(prior['ultimate']), target_regular_entry=prior['entry'],
                                   target_payload_bytes=prior['size'], target_payload_4k_roundup_bytes=((prior['size'] + 4095) // 4096) * 4096,
                                   requires_lower_target=False)
                        state[name] = dict(kind='resolved_hardlink', ultimate=prior['ultimate'], size=prior['size'], entry=prior['entry'])
                    else:
                        row.update(resolution='PRIOR_SAME_LAYER_REGULAR_NOT_PROVED', requires_lower_target=True,
                                   observed_target_kind=prior['kind'] if prior else 'NOT_PREVIOUSLY_PRESENT')
                        state[name] = dict(kind='unresolved_hardlink')
                except M.Refused:
                    row.update(resolution='TARGET_PATH_UNSUPPORTED', requires_lower_target=True)
                    state[name] = dict(kind='unresolved_hardlink')
                rows.append(row)
        M.skip(reader, size + (-size) % 512)
    M.require(not pending and long_name is None and long_link is None, 'unconsumed_extended_header')
    resolved = [row for row in rows if not row['requires_lower_target']]
    unique = {row['ultimate_regular_path']: row['target_payload_bytes'] for row in resolved}
    return dict(headers=headers, entries=entries, hardlinks=len(rows), rows=rows,
                all_hardlinks_resolve_to_prior_same_layer_regular=len(resolved) == len(rows),
                requires_lower_target_count=len(rows) - len(resolved),
                target_payload_bytes_per_link_sum=sum(row['target_payload_bytes'] for row in resolved),
                distinct_target_regular_count=len(unique), distinct_target_payload_bytes=sum(unique.values()),
                symlinks=symlinks, maximum_symlink_target_bytes=max_symlink,
                uncompressed_tar_bytes=reader.count)


def scan(archive, member, desc, layer):
    compressed = M.Reader(archive.extractfile(member), desc['size'])
    media = desc['mediaType']
    if media in ('application/vnd.oci.image.layer.v1.tar+gzip', 'application/vnd.docker.image.rootfs.diff.tar.gzip'):
        expanded = gzip.GzipFile(fileobj=compressed, mode='rb')
    else:
        M.require(media == 'application/vnd.oci.image.layer.v1.tar', 'unsupported_media')
        expanded = compressed
    reader = M.Reader(expanded, layer['uncompressed_bytes_upper_bound'])
    result = link_stats(reader)
    if expanded is not compressed:
        expanded.close()
    while compressed.read(M.CHUNK):
        pass
    M.require(compressed.count == desc['size'] and 'sha256:' + compressed.hash.hexdigest() == desc['digest'], 'compressed_identity')
    M.require('sha256:' + reader.hash.hexdigest() == layer['diff_id'], 'diff_id_identity')
    M.require(reader.count == layer['uncompressed_bytes_upper_bound'], 'expanded_exact_size')
    return dict(result, blob_digest=desc['digest'], compressed_sha256_verified=True,
                diff_id=layer['diff_id'], diff_id_verified=True, compressed_bytes=compressed.count)


def collect(result):
    allocation_raw = (HERE / 'ALLOCATION.json').read_bytes()
    M.require(M.sha(allocation_raw) == ALLOCATION_SHA, 'allocation_changed')
    allocation = json.loads(allocation_raw)
    inventory, catalog = M.pinned('inventory'), M.pinned('catalog')
    row = next(x for x in inventory['images'] if x['role'] == 'gateway-forward')
    image = next(x for x in catalog['images'] if x['role'] == 'gateway-forward')
    path = M.safe_path(row['archive']['path'])
    initial = path.stat()
    M.require(initial.st_size == row['archive']['bytes'], 'archive_size_changed')
    result['archive_path'] = str(path)
    result['rows'] = []
    with path.open('rb') as handle:
        M.require(M.stamp(initial) == M.stamp(os.fstat(handle.fileno())), 'archive_open_drift')
        with tarfile.open(fileobj=handle, mode='r:') as archive:
            members, descs, diffs = M.image_metadata(archive, row)
            for position, (desc, diff, index) in enumerate(zip(descs, diffs, image['layer_indices'])):
                if desc['digest'] not in TARGETS:
                    continue
                layer = catalog['layers'][index]
                M.require(desc['digest'] == layer['blob_digest'] and desc['size'] == layer['compressed_bytes'] and diff == layer['diff_id'], 'catalog_layer_mismatch')
                item = scan(archive, members['blobs/sha256/' + desc['digest'][7:]], desc, layer)
                previous = allocation['blobs'][desc['digest']]
                M.require(item['hardlinks'] == TARGETS[desc['digest']] == previous['hardlinks'] and item['entries'] == previous['entries'], 'original_stats_mismatch')
                item['layer_position_zero_based'] = position
                result['rows'].append(item)
                print(json.dumps(dict(blob=desc['digest'], hardlinks=item['hardlinks'], lower_required=item['requires_lower_target_count'])), flush=True)
        M.require(M.stamp(initial) == M.stamp(os.fstat(handle.fileno())) == M.stamp(path.stat()), 'archive_changed')
    M.require(len(result['rows']) == 2, 'two_blobs_not_completed')
    unresolved = sum(x['requires_lower_target_count'] for x in result['rows'])
    result.update(status='SAME_LAYER_TARGETS_CONFIRMED' if unresolved == 0 else 'LOWER_TARGET_REVIEW_REQUIRED',
                  hardlinks=sum(x['hardlinks'] for x in result['rows']), requires_lower_target_count=unresolved,
                  maximum_symlink_target_bytes=max(x['maximum_symlink_target_bytes'] for x in result['rows']))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scan-two', action='store_true')
    args = parser.parse_args()
    M.require(args.scan_two, 'explicit_scan_required')
    limits = M.cpu_limit()
    M.START_WALL, M.START_CPU = time.monotonic(), time.process_time()
    result = dict(schema='portal-ppt-local-hardlink-review.v1', status='INCOMPLETE',
                  tool_sha256=M.sha(Path(__file__).read_bytes()), core_sha256=CORE_SHA,
                  original_allocation_sha256=ALLOCATION_SHA, limits=limits,
                  scope='Only two already identified Gateway blobs; no lower layer scan',
                  extracted=False, executed=False, network_access=False, whole_archive_rehashed=False,
                  ready_for_import=False, ready_for_rollout=False, physical_filesystem_upper_bound=False)
    M.save('HARDLINK-STARTED.json', result)
    rc = 0
    try:
        collect(result)
    except Exception as exc:
        result.update(error_code=str(exc) if isinstance(exc, M.Refused) else type(exc).__name__, automatic_retry=False)
        rc = 1
    result.update(wall_seconds=round(time.monotonic() - M.START_WALL, 3), cpu_seconds=round(time.process_time() - M.START_CPU, 3))
    M.save('HARDLINK-REVIEW.json', result)
    print(json.dumps({k:result[k] for k in ('status', 'wall_seconds', 'cpu_seconds')}), flush=True)
    return rc


if __name__ == '__main__':
    raise SystemExit(main())

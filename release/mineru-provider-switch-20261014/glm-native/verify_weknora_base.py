"""Offline OCI archive checks; never extract members or execute image code. Py3.7+."""
import gzip
import hashlib
import json
import re
import tarfile
import time

GIB = 1024**3
DIGEST = re.compile(r'sha256:[a-f0-9]{64}\Z')
MANIFESTS = ('application/vnd.oci.image.manifest.v1+json',
             'application/vnd.docker.distribution.manifest.v2+json')
INDEXES = ('application/vnd.oci.image.index.v1+json',
           'application/vnd.docker.distribution.manifest.list.v2+json')


class CheckFailure(RuntimeError):
    pass


def require(value, label):
    if not value:
        raise CheckFailure(label)


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate_json_key')
        result[key] = value
    return result


def hashed(stream, maximum, emit, stage, rate=40*1024**2):
    h, count, start, last = hashlib.sha256(), 0, time.monotonic(), time.monotonic()
    while True:
        data = stream.read(1024**2)
        if not data:
            break
        count += len(data)
        require(count <= maximum, 'content_size_bound_exceeded')
        h.update(data)
        now = time.monotonic()
        require(now - start < 1800, 'content_read_deadline')
        if now - last >= 15:
            emit({'base_verification': 'RUNNING', 'stage': stage, 'bytes_read': count})
            last = now
        # Pace verification reads; export daemon reads are NOT covered by this rate.
        if rate:
            delay = count / rate - (now - start)
            if delay > 0:
                time.sleep(min(delay, 1.0))
    return 'sha256:' + h.hexdigest(), count


def verify(archive, emit, manifest_digest, config_digest, expected_layers,
           expected_bytes, root_digest=None):
    require(DIGEST.fullmatch(manifest_digest) and DIGEST.fullmatch(config_digest), 'invalid_expected_digest')
    with tarfile.open(str(archive), 'r:') as tar:
        members = {}
        for m in tar:
            name = m.name[2:] if m.name.startswith('./') else m.name
            require(not name.startswith('/') and '..' not in name.split('/') and '\\' not in name,
                    'invalid_archive_path')
            if m.isdir():
                require(name.rstrip('/') in ('blobs', 'blobs/sha256', '.'), 'unexpected_archive_directory')
                continue
            require(m.isfile() and not m.issparse(), 'linked_or_special_archive_member')
            require(name in ('index.json', 'oci-layout') or re.fullmatch(r'blobs/sha256/[a-f0-9]{64}', name),
                    'unexpected_archive_file')
            require(name not in members and len(members) < 4096 and 0 <= m.size <= 8*GIB,
                    'duplicate_or_oversized_archive_member')
            members[name] = m

        def meta(name, expected=None, size=None):
            require(name in members and members[name].size <= 8*1024**2, 'metadata_missing_or_oversized')
            require(size is None or size == members[name].size, 'metadata_size_mismatch')
            with tar.extractfile(members[name]) as s:
                raw = s.read(8*1024**2 + 1)
            require(len(raw) == members[name].size, 'metadata_truncated')
            require(expected is None or 'sha256:' + hashlib.sha256(raw).hexdigest() == expected,
                    'metadata_digest_mismatch')
            result = json.loads(raw, object_pairs_hook=unique)
            require(isinstance(result, dict), 'metadata_not_object')
            return result

        def blob_name(d):
            require(isinstance(d, str) and DIGEST.fullmatch(d), 'invalid_blob_digest')
            return 'blobs/sha256/' + d[7:]

        require(meta('oci-layout') == {'imageLayoutVersion': '1.0.0'}, 'invalid_oci_layout')
        index = meta('index.json')
        require(index.get('schemaVersion') == 2 and len(index.get('manifests', [])) == 1,
                'export_not_single_image')
        root = index['manifests'][0]
        require(root.get('digest') == (root_digest or manifest_digest)
                and type(root.get('size')) is int and root['size'] > 0
                and not root.get('urls') and 'data' not in root,
                'export_manifest_identity_mismatch')
        referenced = {'index.json', 'oci-layout'}
        # The exact tag may point at an index. Follow only its single ARM64
        # manifest; do not extract the archive or accept unrelated blobs.
        desc = root
        if desc.get('mediaType') in INDEXES:
            referenced.add(blob_name(desc['digest']))
            idx = meta(blob_name(desc['digest']), desc['digest'], desc.get('size'))
            require(idx.get('schemaVersion') == 2 and isinstance(idx.get('manifests'), list)
                    and len(idx['manifests']) <= 64, 'invalid_image_index')
            matches = [d for d in idx['manifests'] if d.get('platform', {}).get('os') == 'linux'
                       and d.get('platform', {}).get('architecture') == 'arm64'
                       and d.get('platform', {}).get('variant', '') in ('', 'v8')]
            require(len(matches) == 1, 'arm64_manifest_not_unique')
            desc = matches[0]
        require(desc.get('mediaType') in MANIFESTS and desc.get('digest') == manifest_digest
                and type(desc.get('size')) is int and desc['size'] > 0
                and not desc.get('urls') and 'data' not in desc, 'invalid_manifest_descriptor')
        manifest = meta(blob_name(manifest_digest), manifest_digest, desc['size'])
        require(manifest.get('schemaVersion') == 2 and manifest.get('config', {}).get('digest') == config_digest,
                'manifest_config_mismatch')
        config_desc = manifest['config']
        require(type(config_desc.get('size')) is int and config_desc['size'] > 0
                and not config_desc.get('urls') and 'data' not in config_desc, 'invalid_config_descriptor')
        config = meta(blob_name(config_digest), config_digest, config_desc['size'])
        require(config.get('architecture') == 'arm64' and config.get('os') == 'linux', 'platform_mismatch')
        layers, ids = manifest.get('layers', []), config.get('rootfs', {}).get('diff_ids', [])
        require(config.get('rootfs', {}).get('type') == 'layers' and len(layers) == len(ids) == expected_layers,
                'layer_count_mismatch')
        require(sum(d.get('size', -1) for d in layers) == expected_bytes, 'total_layer_bytes_mismatch')
        referenced.update((blob_name(manifest_digest), blob_name(config_digest)))
        totals, rows = 0, []
        for i, (d, diffid) in enumerate(zip(layers, ids)):
            name = blob_name(d.get('digest'))
            referenced.add(name)
            require(name in members and type(d.get('size')) is int and d['size'] > 0
                    and members[name].size == d['size'], 'layer_missing_or_size_mismatch')
            require(not d.get('urls') and 'data' not in d and DIGEST.fullmatch(diffid), 'invalid_layer_descriptor')
            kind = d.get('mediaType')
            require(kind in ('application/vnd.oci.image.layer.v1.tar+gzip',
                    'application/vnd.oci.image.layer.v1.tar', 'application/vnd.docker.image.rootfs.diff.tar.gzip'),
                    'unsupported_layer_encoding')
            emit({'base_verification': 'RUNNING', 'layer': i+1, 'total_layers': len(layers)})
            with tar.extractfile(members[name]) as stream:
                actual, size = hashed(stream, d['size'], emit, 'compressed')
            require(actual == d['digest'] and size == d['size'], 'layer_digest_mismatch')
            with tar.extractfile(members[name]) as stream:
                if kind.endswith('gzip'):
                    with gzip.GzipFile(fileobj=stream, mode='rb') as expanded:
                        actual, expanded_size = hashed(expanded, 32*GIB, emit, 'diffid', rate=80*1024**2)
                else:
                    actual, expanded_size = hashed(stream, 32*GIB, emit, 'diffid', rate=80*1024**2)
            require(actual == diffid, 'layer_diffid_mismatch')
            totals += expanded_size
            require(totals <= 64*GIB, 'total_expansion_limit')
            rows.append({'digest': d['digest'], 'diff_id': diffid,
                         'compressed_bytes': size, 'uncompressed_bytes': expanded_size})
        require(set(members) == referenced, 'unreferenced_archive_blob')
        return {'base_layer_verification': 'PASS', 'manifest_digest': manifest_digest,
                'config_digest': config_digest, 'platform': 'linux/arm64',
                'layers_verified': len(layers), 'compressed_layer_bytes': expected_bytes,
                'uncompressed_layer_bytes': totals, 'uncompressed_diffids': 'PASS',
                'layers': rows, 'archive_extracted': False, 'image_code_executed': False}

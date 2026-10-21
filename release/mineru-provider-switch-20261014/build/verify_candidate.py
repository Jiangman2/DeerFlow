"""Offline export verification: exact base prefix, runtime config, one-file delta."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import tarfile

from guard_mineru import require, safe_file
from prepare_oci import extract
from verify_base_export import verify as verify_archive
from verify_oci_base import document, verify
from verify_built_image import configuration, preserved


def delta_members(blob, layer, expected):
    require(layer['size'] < 2*1024**2, 'source_delta_compressed_too_large')
    with blob.open('rb') as raw:
        stream = gzip.GzipFile(fileobj=raw, mode='rb') if layer['mediaType'].endswith('gzip') else raw
        with tarfile.open(fileobj=stream, mode='r|') as tar:
            seen = set()
            for member in tar:
                name = member.name[2:] if member.name.startswith('./') else member.name
                name = name.rstrip('/')
                require(name not in seen and len(seen) < 4, 'duplicate_or_excess_delta_member')
                seen.add(name)
                require(member.uid == 0 and member.gid == 0 and not member.mode & 0o6022 and not member.issparse()
                        and not member.linkname, 'unsafe_delta_metadata')
                if member.isdir():
                    require(name in ('app', '.'), 'unexpected_delta_directory')
                    continue
                require(member.isfile() and name == 'app/deerflow_mineru_adapter.py'
                        and member.mode == 0o644 and 0 < member.size < 2*1024**2, 'unexpected_delta_file_or_mode')
                with tar.extractfile(member) as f:
                    require(hashlib.sha256(f.read()).hexdigest() == expected, 'candidate_source_digest_mismatch')
            require('app/deerflow_mineru_adapter.py' in seen, 'missing_source_delta')
        if stream is not raw:
            stream.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('archive', 'output', 'metadata', 'base', 'base-receipt', 'plan'):
        p.add_argument('--'+name, required=True, type=Path)
    p.add_argument('--image', required=True)
    args = p.parse_args()
    safe_file(args.archive, 3*1024**3)
    plan = document(args.plan)
    metadata = document(args.metadata)
    layout = extract(args.archive, args.output)
    result = verify(layout, {'source_image': args.image, 'source_image_id': metadata['containerimage.digest']})
    base_receipt = document(args.base_receipt)
    base = document(args.base / 'blobs/sha256' / base_receipt['manifest_digest'][7:])
    candidate = document(layout / 'blobs/sha256' / result['manifest_digest'][7:])
    old, new = base['layers'], candidate['layers']
    require(len(old) == plan['base']['layers'] and len(new) == len(old)+1 and new[:-1] == old,
            'base_compressed_prefix_or_delta_count_changed')
    preserved(configuration(args.base, base_receipt['manifest_digest']), configuration(layout, result['manifest_digest']))
    checked = verify_archive(args.archive, lambda row: print(json.dumps(row), flush=True),
        manifest_digest=result['manifest_digest'], config_digest=result['config_digest'],
        expected_layers=len(new), expected_bytes=result['compressed_layer_bytes'])
    require(checked['uncompressed_layer_bytes'] <= plan['base']['uncompressed_layer_bytes'] + 2*1024**2,
            'candidate_delta_expansion_exceeded')
    delta_members(layout / 'blobs/sha256' / new[-1]['digest'][7:], new[-1], plan['source']['after_sha256'])
    checksum = hashlib.sha256()
    with args.archive.open('rb') as f:
        for chunk in iter(lambda: f.read(1024**2), b''):
            checksum.update(chunk)
    result.update(image=args.image, archive_sha256=checksum.hexdigest(),
        runtime_config='PRESERVED', base_layer_prefix='PRESERVED', source_delta='ONE_FILE_ONLY',
        source_sha256=plan['source']['after_sha256'], source_mode='0644', uncompressed_diffids='PASS',
        uncompressed_layer_bytes=checked['uncompressed_layer_bytes'])
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()

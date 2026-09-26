"""Validate the selected offline OCI base against the collected runtime image ID."""
import hashlib
import json
import re
import sys
from pathlib import Path


def blob(root, descriptor):
    digest = descriptor['digest']
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
        raise ValueError('invalid_blob_digest')
    data = (root / 'blobs/sha256' / digest.split(':')[1]).read_bytes()
    if hashlib.sha256(data).hexdigest() != digest.split(':')[1]:
        raise ValueError('blob_checksum_mismatch')
    if descriptor.get('size', len(data)) != len(data):
        raise ValueError('blob_size_mismatch')
    return json.loads(data)


def verify(root, lock, component):
    expected = lock['components'][component]
    selected = (root / 'manifest.digest').read_text().strip()
    rows = json.loads((root / 'index.json').read_bytes())['manifests']
    matches = [r for r in rows if r.get('annotations', {}).get('io.containerd.image.name',
                r.get('annotations', {}).get('org.opencontainers.image.ref.name')) == expected['image']]
    if len(matches) != 1 or matches[0]['digest'] != selected:
        raise ValueError('selected_image_identity_mismatch')
    manifest = blob(root, matches[0])
    if 'config' not in manifest:
        raise ValueError('expected_exported_single_platform_manifest')
    config = blob(root, manifest['config'])
    if config.get('os') != 'linux' or config.get('architecture') != 'arm64':
        raise ValueError('expected_linux_arm64_base')
    # CRI implementations may report a configuration ID or a manifest digest.
    # Accept only either exact content-addressed identifier of this same image.
    if expected['runtime_image_id'] not in (selected, manifest['config']['digest']):
        raise ValueError('runtime_image_id_differs_from_exported_base')
    print('audited_oci_base=PASS component={} manifest={} config={}'.format(
        component, selected, manifest['config']['digest']))


if __name__ == '__main__':
    verify(Path(sys.argv[1]), json.loads(Path(sys.argv[2]).read_bytes()), sys.argv[3])

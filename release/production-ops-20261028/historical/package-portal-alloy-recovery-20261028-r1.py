"""Seal a matching healthy-peer Alloy OCI archive from an already downloaded Docker export."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tarfile

HERE = Path(__file__).resolve().parent
OUTPUT = Path('C:/Users/15285/Downloads/portal-alloy-recovery-20261028-r1')
SOURCE = Path('C:/Users/15285/Downloads/portal-alloy-recovery-build-input-20261028-r1/alloy.docker.tar')
MANIFEST = 'sha256:87a8fcab1a8bb39047ee25ccd749b5b89de1eeb41f8f85dac7b906894d1a11b8'
CONFIG = 'sha256:8b089d2834ca96f22974b0ea75528dbd3806c1bf38d67faa4483c72a2e5cab1e'
TARGET = 'docker.io/grafana/alloy:v1.11.0'


def blob(tar, digest):
    raw = tar.extractfile('blobs/sha256/' + digest[7:]).read()
    assert 'sha256:' + hashlib.sha256(raw).hexdigest() == digest
    return raw


def main():
    output = OUTPUT / 'alloy.arm64.oci.tar'
    with tarfile.open(SOURCE) as source, tarfile.open(output, 'w:') as dest:
        manifest = blob(source, MANIFEST)
        info = json.loads(manifest)
        assert info['config']['digest'] == CONFIG
        config = json.loads(blob(source, CONFIG))
        assert config['architecture'] == 'arm64' and config['os'] == 'linux'
        index = {'schemaVersion': 2, 'mediaType': 'application/vnd.oci.image.index.v1+json',
                 'manifests': [{'mediaType': info['mediaType'], 'digest': MANIFEST, 'size': len(manifest),
                                'platform': {'os': 'linux', 'architecture': 'arm64'},
                                'annotations': {'org.opencontainers.image.ref.name': TARGET}}]}
        for name, raw in [('oci-layout', b'{"imageLayoutVersion":"1.0.0"}\n'),
                          ('index.json', (json.dumps(index, sort_keys=True) + '\n').encode())]:
            m = tarfile.TarInfo(name)
            m.size, m.mode, m.mtime = len(raw), 0o644, 0
            dest.addfile(m, io.BytesIO(raw))
        digests = [MANIFEST, CONFIG] + [x['digest'] for x in info['layers']]
        for digest in digests:
            name = 'blobs/sha256/' + digest[7:]
            m = source.getmember(name)
            assert m.isreg()
            h = tarfile.TarInfo(name)
            h.size, h.mode, h.mtime = m.size, 0o644, 0
            with source.extractfile(m) as stream:
                dest.addfile(h, stream)
    raw = output.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    script = HERE / 'restore-portal-alloy-offline-image-20261028-r1.py'
    text = script.read_text(encoding='utf-8')
    # Keep both repository and delivered standalone entry sealed to the same bytes.
    import re
    text = re.sub(r'^ARCHIVE_BYTES = .*$', 'ARCHIVE_BYTES = ' + str(len(raw)), text, flags=re.M)
    text = re.sub(r'^ARCHIVE_SHA256 = .*$', 'ARCHIVE_SHA256 = ' + repr(digest), text, flags=re.M)
    script.write_text(text, encoding='utf-8', newline='\n')
    shutil.copyfile(script, OUTPUT / script.name)
    spec = importlib.util.spec_from_file_location('alloy_recovery', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with output.open('rb') as stream:
        observation = module.verify_archive(stream)
    result = {'schema': 'portal-alloy-recovery-package.v1', 'status': 'VERIFIED_MATCHING_HEALTHY_PEER_IMAGE_NOT_DEPLOYED',
              'archive': output.name, 'archive_bytes': len(raw), 'archive_sha256': digest,
              'image': TARGET, 'manifest_digest': MANIFEST, 'config_digest': CONFIG,
              'healthy_peer_nodes': ['datax-02', 'datax-03', 'ecs-zdz-pro-0022'],
              'source': 'm.daocloud.io/docker.io/grafana/alloy@' + MANIFEST,
              'content_verification': observation, 'source_code_modified': False}
    (OUTPUT / 'PACKAGE.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    (HERE / 'alloy-recovery-package-20261028-r1.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()

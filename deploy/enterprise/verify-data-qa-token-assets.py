"""Record compiled runtime asset pins from the exact qualified OCI; no execution."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import tarfile

MARKERS = {
    'token_pending': '统计中，等待模型返回用量',
    'token_live': '已返回用量，仍在统计',
    'data_qa_progress': '智能问数正在处理您的查询',
    'token_component': 'waitingForUsage',
}


def verify(directory):
    result = json.loads((directory / 'RESULT.json').read_text())
    assert result['status'] == 'LOCAL_RUNTIME_AND_OCI_ACCEPTED_NOT_DEPLOYED'
    archive = directory / 'frontend.oci.tar'
    found, sources = {}, {}
    with tarfile.open(archive, 'r:') as outer:
        index = json.load(outer.extractfile('index.json'))
        assert len(index['manifests']) == 1
        md = index['manifests'][0]['digest']
        assert md == result['image']['id']
        manifest = json.load(outer.extractfile('blobs/sha256/' + md[7:]))
        for layer in manifest['layers']:
            compressed = outer.extractfile('blobs/sha256/' + layer['digest'][7:])
            with gzip.GzipFile(fileobj=compressed) as stream, tarfile.open(fileobj=stream, mode='r|') as inner:
                for member in inner:
                    name = member.name.lstrip('./')
                    if not member.isfile() or not name.startswith('app/frontend/.next/'):
                        continue
                    wanted = name.endswith(('/BUILD_ID', '/routes-manifest.json'))
                    javascript = '/static/chunks/' in name and name.endswith('.js')
                    if not (wanted or javascript):
                        continue
                    assert member.size < 16 * 1024**2
                    raw = inner.extractfile(member).read()
                    matches = [label for label, marker in MARKERS.items() if marker.encode() in raw]
                    if wanted or matches:
                        sources['/' + name] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
                    for label in matches:
                        found.setdefault(label, []).append('/' + name)
    assert set(found) == set(MARKERS), 'COMPILED_FEATURE_MARKER_MISSING'
    assert '/app/frontend/.next/BUILD_ID' in sources and '/app/frontend/.next/routes-manifest.json' in sources
    record = {'schema': 'portal-data-qa-token-compiled-assets.v1', 'image': md,
              'feature_markers': found, 'source_pins': sources, 'production_modified': False,
              'status': 'COMPILED_FEATURES_PINNED_NOT_BROWSER_ACCEPTED'}
    with (directory / 'COMPILED-ASSETS.json').open('x', encoding='utf-8') as stream:
        json.dump(record, stream, indent=2)
    print(json.dumps(record))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualified', type=Path, required=True)
    verify(parser.parse_args().qualified)

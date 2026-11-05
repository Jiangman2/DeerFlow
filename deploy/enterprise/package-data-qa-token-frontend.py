"""Bind the new qualified image to the two already field-proven frontend importers."""
import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tarfile
import time

HERE = Path(__file__).resolve().parent
PRIOR = Path('C:/Users/15285/Downloads/portal-data-qa-progress-20261022-r2')
PINS = {
    '0015': ('ecs-zdz-pro-0015', 'de6ee5e8-dbfb-4272-9ebf-ae82182db085', '7e6ddc2fc22b905f296888b0aea61ad8a3f73d67dd0323079489846cac61596c'),
    'datax02': ('datax-02', '3148d4c1-6a97-42ed-a78c-950dcea5cbec', '1b478db9eb36910b1a5fbb95a4b523539cbafecc64c9079393ee116adbfc7707'),
}
TAG = 'deerflow.local/deer-flow-frontend:20261022-data-qa-token-r1'
ARCHIVE = 'frontend.data-qa-token.full.oci.tar'
OLD = 'sha256:15d1327740e688faf0617d1d8fb9f662618a963624ba7280a6d0efb54ed70475'


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load('preload_r2', 'package-data-qa-progress-frontend-r2.py')
legacy = load('preload_legacy', 'package-data-qa-progress-frontend.py')


def pin(raw):
    return {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}


def build(qualified, output):
    raw = (qualified / 'RESULT.json').read_bytes()
    record = json.loads(raw)
    assert record['status'] == 'LOCAL_RUNTIME_AND_OCI_ACCEPTED_NOT_DEPLOYED'
    assert record['fresh_runtime']['pinned_base_prefix'] == 'PASS'
    assert record['runtime']['http']['local_http'] == 'PASS'
    archive = qualified / 'frontend.oci.tar'
    transport = {'bytes': archive.stat().st_size, 'sha256': base.sha_file(archive)}
    assert transport == {k: record['export'][k] for k in ('bytes', 'sha256')}
    assert record['image']['tag'] == TAG
    with tarfile.open(archive, 'r:') as tar:
        index = json.load(tar.extractfile('index.json'))
        assert len(index['manifests']) == 1
        md = index['manifests'][0]['digest']
        mr = tar.extractfile('blobs/sha256/' + md[7:]).read()
        assert 'sha256:' + pin(mr)['sha256'] == md == record['image']['id']
        manifest = json.loads(mr)
        cd = manifest['config']['digest']
        cr = tar.extractfile('blobs/sha256/' + cd[7:]).read()
        assert 'sha256:' + pin(cr)['sha256'] == cd
        config = json.loads(cr)
        assert config['os'] == 'linux' and config['architecture'] == 'arm64'
        assert len(manifest['layers']) == len(config['rootfs']['diff_ids']) == 11
    chain = ''
    for diff in config['rootfs']['diff_ids']:
        chain = diff if not chain else 'sha256:' + pin((chain + ' ' + diff).encode())['sha256']
    sources = {}
    for role, (host, uid, prior_sha) in PINS.items():
        prior = PRIOR / ('preload-portal-frontend-data-qa-progress-' + role + '-20261022-r2.py')
        prior_raw = prior.read_bytes()
        assert pin(prior_raw)['sha256'] == prior_sha, 'PRIOR_IMPORTER_CHANGED'
        source = prior_raw.decode('utf-8')
        module = base.load_source(source, host)
        protected = dict(module.PROTECTED)
        protected.update({'deerflow.local/deer-flow-frontend:20261021-data-qa-progress-r1': OLD,
                          'deerflow.local/deer-flow-frontend@' + OLD: OLD})
        values = {'TRANSPORT_PIN': transport, 'MANIFEST': md, 'CONFIG': cd,
                  'TAG': TAG, 'INPUT': '/home/cnooc/' + ARCHIVE,
                  'CONTROL': ('/mnt/data/' if role == '0015' else '/var/lib/') + 'portal-frontend-data-qa-token-preload-20261022-r1',
                  'DESCRIPTORS': manifest['layers'], 'CACHED_DESCRIPTORS': [],
                  'FINAL_CHAIN': chain, 'PROTECTED': protected}
        for key, value in values.items():
            source = legacy.replace(source, key, key + ' = ' + repr(value))
        source = legacy.replace(source, 'MANIFEST_BYTES', 'MANIFEST_BYTES, CONFIG_BYTES, LAYER_COUNT = ' + repr((len(mr), len(cr), 11)))
        candidate = base.load_source(source, host)
        importer, unused, unused2 = candidate.load_inputs()
        with archive.open('rb') as stream:
            prepared = candidate.inspect_archive(stream.fileno(), importer, time.monotonic())
        budget = {key: prepared[key] for key in ('manifest', 'chains', 'blob_sizes', 'layer_stats')}
        source = legacy.replace(source, 'EARLY_BUDGET', 'EARLY_BUDGET = ' + repr(budget))
        source = base.replace_once(source, "['--preload-frontend-data-qa-progress-once']", "['--preload-frontend-data-qa-token-once']")
        ast.parse(source, feature_version=(3, 7))
        sources[role] = source
    output.mkdir(exist_ok=False)
    with archive.open('rb') as src, (output / ARCHIVE).open('xb') as dst:
        shutil.copyfileobj(src, dst, length=1024*1024)
    assert base.sha_file(output / ARCHIVE) == transport['sha256']
    scripts = {}
    for role, source in sources.items():
        name = 'preload-portal-frontend-data-qa-token-' + role + '-20261022-r1.py'
        content = source.encode('utf-8')
        (output / name).write_bytes(content)
        scripts[role] = dict(pin(content), file=name, hostname=PINS[role][0],
                             expected_node_uid=PINS[role][1], prior_script_sha256=PINS[role][2])
    result = {'schema': 'portal-data-qa-token-frontend-package.v1', 'manifest': md, 'config': cd,
              'tag': TAG, 'archive': ARCHIVE, 'transport': transport, 'scripts': scripts,
              'qualification_pin': pin(raw), 'source_change_sha256': record['source_change_sha256'],
              'archive_blob_and_diffid_validation': 'PASS', 'exact_host_binding': True,
              'capacity_checked_before_once_claim': True, 'frontend_deployed': False,
              'ready_for_rollout': False, 'fresh_node_uid_check_required_at_activation': True}
    (output / 'PACKAGE.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualified', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    build(args.qualified, args.output)

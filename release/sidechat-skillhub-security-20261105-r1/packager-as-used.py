"""Package a locally qualified sidechat-security image; never contact production.

Reuse the hash-pinned, field-tested node importers and their existing resource,
integrity, CRI and once-only checks. Historical scripts are not modified.
"""
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
TAG = 'deerflow.local/deer-flow-frontend:20261105-sidechat-security-r1'
ARCHIVE = 'frontend.sidechat-security-r1.full.oci.tar'
REVISION = '20261105-r1'
TEMPLATES = {
    '0015': ('receiver/preload-portal-frontend-employee-roster-20261020-r2.CANDIDATE.py',
             'a68dcf1f762f6a871bde3a0319604de118eb938bd0c9e49af68544b9392268f6'),
    'datax02': ('receiver/datax02/preload-portal-frontend-employee-roster-datax02-20261020-r2.CANDIDATE.py',
                'd885b1a68e9427adbebe1ba1e911bbb4d3bc5ff85e965d2bc3dc1608eaf77804'),
}
RECENT = {
    '20261105-new-chat-v41-r1': 'sha256:8a964aebacc5b3b566bc368a03dfd6a333f9f970d4fb0d4a6cb32d8d4ed7577d',
    '20261103-dashboard-navigation-r2': 'sha256:74c2e66ecc1c2e9b483faac428fc9211ff43bc0e0ed59ee141de379c678fdfb5',
    '20261102-usage-heatmap-r2': 'sha256:97a84226f1c9f77871c05cebaae1fa42b7525644edc844bccf75e29518ba44c1',
    '20261102-usage-monitor-r2': 'sha256:f79aa01640d41beae2c249ef03accaaf64563ad45788cb7c97992a73dc11c3d2',
    '20261102-composer-controls-r4': 'sha256:458c15afe850e72ece3451e4ccb22f0349f9b4a03c1c888439a648d9161aa992',
    '20261102-agent-composer-r2': 'sha256:b27c3d82481566e88613aad15a1033f63ff2c04a646db82cc49c2731cc4c9db6',
    '20261101-service-recovery-core-r1': 'sha256:f53ee1253d827a3542f3cc897e8b1c82f8ccf008ddf86bc0b64d6c1074faf944',
    '20261101-service-recovery-r1': 'sha256:1fd0cd93e8234216ed1fff29751983777dcb7fb093625d616903a0b1067257c7',
    '20261101-datalake-session-r1': 'sha256:34f1bd6936ac606c7b39992eaff4aadce65a0bae800c60c400f7ebae4f56eb00',
    '20261031-dashboard-recovery-r1': 'sha256:6fe4c264b3774924eddafa03ec83244c56f1e93af26f13de905308948493b602',
    '20261030-mobile-fit-r1': 'sha256:b9476120110c0e0ea0d146267c8e640b2b74ae6e5d951e99519fe754aa2e004e',
    '20261024-chat-notice-layout-r1': 'sha256:cf701f067e050f014dc4e0178252070fa22806fddc8c4a085c51049451352298',
    '20261028-frontend-polish-r1': 'sha256:1d0f917d767bcb80e9dbb6e9a6281b6730442511fcb1a1e3a35b203f049daf3b',
    '20261028-combined-features-r1': 'sha256:99a1da37496816a6abb0524587069cdf59dcec1bea4d5cc94c58f57c7817bbb2',
}


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load('service_recovery_early_gate', HERE / 'package-data-qa-progress-frontend-r2.py')
legacy = load('service_recovery_full_transport', HERE / 'package-data-qa-progress-frontend.py')


def pin(raw):
    return dict(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())


def build(qualified, importer_root, output):
    result_raw = (qualified / 'RESULT.json').read_bytes()
    record = json.loads(result_raw)
    base.require(record.get('frontend_slim') == 'LOCAL_PASS'
                 and record.get('platform') == 'linux/arm64', 'LOCAL_QUALIFICATION_REQUIRED')
    base.require(record['image']['tag'] == TAG
                 and record['fresh_runtime']['pinned_base_prefix'] == 'PASS'
                 and record['runtime']['http']['local_http'] == 'PASS'
                 and record['export']['exact_qualified_layers'] == 'PASS'
                 and record['export']['blob_and_diffid_hashes'] == 'PASS', 'QUALIFICATION_SCOPE')
    archive = qualified / 'frontend.oci.tar'
    transport = dict(bytes=archive.stat().st_size, sha256=base.sha_file(archive))
    base.require(transport == {k: record['export'][k] for k in transport}, 'EXPORT_CHANGED')
    with tarfile.open(archive, 'r:') as tar:
        index = json.load(tar.extractfile('index.json'))
        base.require(len(index['manifests']) == 1, 'SINGLE_PLATFORM_REQUIRED')
        md = index['manifests'][0]['digest']
        mr = tar.extractfile('blobs/sha256/' + md[7:]).read()
        base.require('sha256:' + pin(mr)['sha256'] == md and md == record['image']['id'], 'MANIFEST_CHANGED')
        manifest = json.loads(mr)
        cd = manifest['config']['digest']
        cr = tar.extractfile('blobs/sha256/' + cd[7:]).read()
        base.require('sha256:' + pin(cr)['sha256'] == cd, 'CONFIG_CHANGED')
        config = json.loads(cr)
        count = len(manifest['layers'])
        base.require(config['os'] == 'linux' and config['architecture'] == 'arm64'
                     and count == len(config['rootfs']['diff_ids']) == record['export']['layers']
                     and 0 < count <= 30, 'PLATFORM_OR_LAYER_COUNT')
    chain = ''
    for diff in config['rootfs']['diff_ids']:
        chain = diff if not chain else 'sha256:' + pin((chain + ' ' + diff).encode())['sha256']
    generated = {}
    for role, (relative, expected) in TEMPLATES.items():
        raw = (importer_root / relative).read_bytes()
        base.require(pin(raw)['sha256'] == expected, 'IMPORTER_CHANGED_' + role)
        source = raw.decode().replace('\r\n', '\n')
        host, uid, _ = base.PINS[role]
        module = base.load_source(source, host)
        protected = dict(module.PROTECTED)
        for tag, digest in RECENT.items():
            protected[module.REPO + ':' + tag] = digest
            protected[module.REPO + '@' + digest] = digest
        values = dict(TRANSPORT_PIN=transport, INPUT='/home/cnooc/' + ARCHIVE,
                      TAG=TAG, MANIFEST=md, CONFIG=cd, FINAL_CHAIN=chain,
                      CACHED_DESCRIPTORS=[], DESCRIPTORS=manifest['layers'], PROTECTED=protected)
        for key, value in values.items():
            source = legacy.replace(source, key, key + ' = ' + repr(value))
        source = legacy.replace(source, 'MANIFEST_BYTES',
                                'MANIFEST_BYTES, CONFIG_BYTES, LAYER_COUNT = ' + repr((len(mr), len(cr), count)))
        source = legacy.replace(source, 'assemble_complete', '''def assemble_complete(fd, directory, controller, original, started):
    # The sealed full export is independently checked by inspect_archive before import.
    time_gate(started)
    controller.report.update(cached_containerd_payload_bytes_read=False, cached_payload_bytes=0,
        transport_kind='FULL_HASH_BOUND_OCI', reused_base_blob_count=0)
    return os.dup(fd)''', function=True)
        candidate = base.load_source(source, host)
        importer, _, _ = candidate.load_inputs()
        with archive.open('rb') as stream:
            prepared = candidate.inspect_archive(stream.fileno(), importer, time.monotonic())
        source = base.prepare_source(source, role, prepared)
        source = base.replace_once(source, "['--preload-frontend-data-qa-progress-once']",
                                   "['--preload-frontend-sidechat-security-once']")
        control = ('/mnt/data/' if role == '0015' else '/var/lib/') + 'portal-frontend-sidechat-security-preload-' + REVISION
        source = legacy.replace(source, 'CONTROL', 'CONTROL = ' + repr(control))
        ast.parse(source, feature_version=(3, 7))
        compile(source, '<sidechat-security-preload>', 'exec')
        generated[role] = (source.encode(), host, uid)
    output.mkdir(exist_ok=False)
    with archive.open('rb') as src, (output / ARCHIVE).open('xb') as dst:
        shutil.copyfileobj(src, dst, length=1024 * 1024)
    base.require(base.sha_file(output / ARCHIVE) == transport['sha256'], 'COPY_CHANGED')
    scripts = {}
    for role, (raw, host, uid) in generated.items():
        filename = 'preload-portal-frontend-sidechat-security-' + role + '-' + REVISION + '.py'
        (output / filename).write_bytes(raw)
        scripts[role] = dict(pin(raw), file=filename, hostname=host, expected_node_uid=uid,
                             prior_importer_sha256=TEMPLATES[role][1])
    receipt = dict(schema='portal-sidechat-security-frontend-package.v1', manifest=md, config=cd,
                   qualified_image_id=record['image']['id'], tag=TAG, archive=ARCHIVE,
                   transport=transport, scripts=scripts, qualification_pin=pin(result_raw),
                   source_inventory_pin=pin((qualified / 'SOURCE.json').read_bytes()),
                   capacity_checked_before_once_claim=True, old_references_preserved=True,
                   fresh_node_uid_check_required_at_activation=True,
                   production_workload_mutation=False, ready_for_rollout=False)
    (output / 'PACKAGE.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('qualified', 'importer-root', 'output'):
        parser.add_argument('--' + key, type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.qualified, args.importer_root, args.output), sort_keys=True))

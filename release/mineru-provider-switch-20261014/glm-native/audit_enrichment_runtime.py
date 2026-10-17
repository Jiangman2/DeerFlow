"""Inspect exact candidate runtime assets and node capacity; never run image code."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import resource
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback

import diagnose_enrichment_preload as frozen

STORE = Path('/opt/weknora-enrichment-preload-resume-20261017-r1/RESULT.json')
STORE_SHA = 'e3a480818f57cea1d41f0eca47edaf7a373a69b813ee62ad89651ca008b737f9'
CANDIDATE = Path('/opt/weknora-glm-verified-ysyi3g70')
BUILD_SHA = 'cb92bb8ebff2b7caabddbc090578d6e427613a6eea1ddd42daa228d1a8892e78'
ARCHIVE_SHA = '471ba1fecc997ce0785da151dac47da313d256506ece353275d04cdcff168f51'
ARCHIVE_BYTES = 737000448
MANIFEST = 'sha256:3bc43f4e4643fd069f59fdcd6a824ed6edb6603c3f2b957ff5b5451b853f4709'
CONFIG = 'sha256:22b73c727cfb445ecf9fabe97f693efd5ceb7cbcb449b593c219e6407aef64c5'
COMPILED = Path('/opt/weknora-glm-runtime-fix-8meva2vp/rebuild')
PACKAGE_SHA = '75c3442c222321f095e01cc5b3fcb75c187f9d486216f4d3f85ceb219852914f'
NATIVE = Path('/var/lib/weknora-glm-native-acceptance-20261017-r1-fix1/RESULT.json')
NATIVE_SHA = '6a1f5a14418d6f16c4edfb48ffcd4860bca6709b7e1595cea63c8fec035489f0'
KUBE = ['kubectl', '--kubeconfig=/etc/kubernetes/admin.conf', '--request-timeout=20s']
PHASE = 'PREFLIGHT'


class AuditFailure(RuntimeError): pass


def need(ok, label):
    if not ok: raise AuditFailure(label)


def emit(row): print(json.dumps(row, sort_keys=True), flush=True)


def phase(value):
    global PHASE
    PHASE = value
    emit(dict(enrichment_runtime_audit='RUNNING', phase=value, production_writes=0))


def directory(path):
    for p in (path,)+tuple(path.parents):
        s = p.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022, 'unsafe_private_directory')


def opened(path, maximum):
    directory(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    f = os.fdopen(fd, 'rb'); s = os.fstat(f.fileno())
    if not (stat.S_ISREG(s.st_mode) and s.st_uid == 0 and s.st_nlink == 1
            and not s.st_mode & 0o022 and 0 < s.st_size <= maximum):
        f.close(); raise AuditFailure('unsafe_private_file')
    return f


def ident(s): return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns


def read(path, expected=None, maximum=8*1024**2):
    with opened(path, maximum) as f:
        before = ident(os.fstat(f.fileno())); raw = f.read(maximum+1)
        need(ident(os.fstat(f.fileno())) == before and len(raw) == before[2], 'file_changed')
    need(expected is None or hashlib.sha256(raw).hexdigest() == expected, 'pinned_file_hash_changed')
    return raw


def checksums(raw):
    rows = {}
    for line in raw.decode('utf-8').splitlines():
        m = re.fullmatch(r'([a-f0-9]{64})  (.+)', line)
        need(m is not None, 'checksum_row_invalid')
        name = m[2]
        need(not name.startswith('/') and '\\' not in name and ':' not in name
             and not any(ord(c) < 32 for c in name) and '..' not in name.split('/')
             and name not in rows, 'checksum_path_invalid')
        rows[name] = m[1]
    need(0 < len(rows) <= 50000, 'checksum_count')
    return rows


def source_assets():
    # Read only two small build records, not the multi-GB build context or cache.
    package = COMPILED/'package'
    names = checksums(read(package/'PACKAGE.SHA256', PACKAGE_SHA, 65536))
    need('BUILD-PLAN.json' in names, 'build_plan_missing')
    plan = json.loads(read(package/'BUILD-PLAN.json', names['BUILD-PLAN.json']))
    finish = plan['runtime_finish']
    need(finish['compile_reused'] is True, 'compile_reuse_binding')
    digest = finish['source_manifest_sha256']
    need(re.fullmatch(r'[a-f0-9]{64}', digest) is not None, 'source_manifest_digest')
    rows = checksums(read(COMPILED/'build1/qualification/source.sha256', digest))
    selected = {'app/'+name: sha for name, sha in rows.items()
                if name.startswith(('migrations/versioned/', 'config/'))
                or name == 'scripts/docker-entrypoint.sh'}
    # Operator-owned config.yaml is not a required source-tree asset; the
    # isolated App will receive an independently authored clean configuration.
    need(any(k.endswith('.up.sql') for k in selected), 'source_runtime_assets_missing')
    return selected, digest


def wanted(name):
    return name.startswith(('app/migrations/versioned/', 'app/config/')) or name == 'app/scripts/docker-entrypoint.sh'


def related(name):
    return wanted(name) or name in ('app', 'app/migrations', 'app/migrations/versioned', 'app/config', 'app/scripts')


def clean_name(name):
    while name.startswith('./'): name = name[2:]
    name = name.rstrip('/')
    need(not name.startswith('/') and '..' not in name.split('/') and '\\' not in name
         and not any(ord(c) < 32 for c in name), 'unsafe_layer_path')
    return name


class Bounded(io.RawIOBase):
    def __init__(self, source, maximum): self.source, self.maximum, self.count = source, maximum, 0
    def readable(self): return True
    def readinto(self, b):
        raw = self.source.read(min(len(b), 1024**2))
        self.count += len(raw); need(self.count <= self.maximum, 'expanded_layer_bound')
        b[:len(raw)] = raw; return len(raw)


def layer_assets(stream, effective, content_classifier=None):
    entries, deleted, opaque = {}, [], []
    with tarfile.open(fileobj=stream, mode='r|') as layer:
        for n, m in enumerate(layer):
            need(n < 500000, 'layer_member_count')
            name = clean_name(m.name)
            parent, _, leaf = name.rpartition('/')
            if leaf == '.wh..wh..opq': opaque.append(parent); continue
            if leaf.startswith('.wh.'):
                deleted.append((parent+'/' if parent else '')+leaf[4:]); continue
            if not related(name): continue
            need(name not in entries, 'duplicate_runtime_member')
            need(m.isdir() or m.isfile() or m.issym() or m.islnk(), 'runtime_special_file')
            need(not m.issparse(), 'runtime_sparse_file')
            kind = 'file' if m.isfile() else 'directory' if m.isdir() else 'link'
            row = dict(kind=kind, uid=m.uid, gid=m.gid, mode=m.mode, bytes=m.size)
            if m.isfile():
                need(0 <= m.size <= 8*1024**2, 'runtime_asset_size')
                with layer.extractfile(m) as member: raw = member.read(8*1024**2+1)
                need(len(raw) == m.size, 'runtime_asset_truncated')
                row['sha256'] = hashlib.sha256(raw).hexdigest()
                if content_classifier is not None:
                    row['content_classification'] = content_classifier(raw)
            entries[name] = row
    # Whiteouts affect only lower layers, irrespective of their order in the tar.
    for name in list(effective):
        if any(name == d or name.startswith(d+'/') for d in deleted) or any(not p or name.startswith(p+'/') for p in opaque):
            del effective[name]
    for name, row in entries.items():
        if row['kind'] != 'directory':
            for old in list(effective):
                if old.startswith(name+'/'): del effective[old]
        effective[name] = row
    need(len(effective) <= 4096, 'runtime_asset_count')


def inspect_archive(path, archive_sha, archive_bytes, manifest_digest, config_digest, content_classifier=None):
    with opened(path, archive_bytes) as f:
        before = ident(os.fstat(f.fileno())); need(before[2] == archive_bytes, 'archive_size_changed')
        h = hashlib.sha256(); start = last = time.monotonic(); count = 0
        for block in iter(lambda: f.read(1024**2), b''):
            h.update(block); count += len(block); now = time.monotonic()
            if now-last >= 15:
                emit(dict(enrichment_runtime_audit='HASHING_PINNED_ARCHIVE', bytes_read=count)); last = now
            time.sleep(max(0, min(.05, count/(64*1024**2)-(now-start))))
        need(h.hexdigest() == archive_sha, 'candidate_archive_hash_changed')
        f.seek(0)
        with tarfile.open(fileobj=f, mode='r:') as outer:
            members = {}
            for m in outer:
                name = clean_name(m.name)
                if m.isdir():
                    need(name in ('', '.', 'blobs', 'blobs/sha256'), 'oci_directory'); continue
                need(m.isfile() and not m.issparse() and not m.linkname and name not in members
                     and len(members) < 4096, 'oci_file')
                need(name in ('index.json', 'oci-layout') or re.fullmatch(r'blobs/sha256/[a-f0-9]{64}', name), 'oci_path')
                members[name] = m
            def meta(digest):
                m = members['blobs/sha256/'+digest[7:]]; need(m.size <= 8*1024**2, 'oci_metadata_bound')
                raw = outer.extractfile(m).read()
                need('sha256:'+hashlib.sha256(raw).hexdigest() == digest, 'oci_metadata_hash')
                return json.loads(raw)
            manifest = meta(manifest_digest); cfg = meta(config_digest)
            need(manifest['config']['digest'] == config_digest and cfg['os'] == 'linux'
                 and cfg['architecture'] == 'arm64', 'candidate_identity')
            effective = {}
            layers = manifest['layers']; need(0 < len(layers) <= 64, 'layer_count')
            for i, descriptor in enumerate(layers):
                emit(dict(enrichment_runtime_audit='READ_RUNTIME_ASSET_HEADERS', layer=i+1, total_layers=len(layers)))
                kind = descriptor['mediaType']; need(kind in ('application/vnd.oci.image.layer.v1.tar+gzip',
                    'application/vnd.oci.image.layer.v1.tar', 'application/vnd.docker.image.rootfs.diff.tar.gzip'), 'layer_format')
                m = members['blobs/sha256/'+descriptor['digest'][7:]]
                need(m.size == descriptor['size'], 'layer_size')
                with outer.extractfile(m) as compressed:
                    expanded = gzip.GzipFile(fileobj=compressed) if kind.endswith('gzip') else compressed
                    try:
                        with io.BufferedReader(Bounded(expanded, 4*1024**3)) as bounded:
                            layer_assets(bounded, effective, content_classifier)
                    finally:
                        if expanded is not compressed: expanded.close()
        need(ident(os.fstat(f.fileno())) == before, 'archive_changed_during_audit')
    return effective


def comparison(expected, actual):
    missing, different = [], []
    for name, digest in sorted(expected.items()):
        row = actual.get(name)
        parents = list(Path(name).parents)[:-1]
        bad_parent = any(actual.get(str(p).replace('\\','/'), {}).get('kind') == 'link' for p in parents)
        if row is None: missing.append(name)
        elif row.get('kind') != 'file' or row.get('sha256') != digest or bad_parent: different.append(name)
    extras = sorted(k for k, v in actual.items() if wanted(k) and v['kind'] != 'directory' and k not in expected)
    groups = {}
    for label, prefix in (('postgres_migrations', 'app/migrations/versioned/'), ('config_assets', 'app/config/')):
        source = [k for k in expected if k.startswith(prefix)]
        groups[label] = dict(expected_files=len(source), present_files=sum(k in actual for k in source),
            missing=[k for k in missing if k.startswith(prefix)], different=[k for k in different if k.startswith(prefix)],
            extra=[k for k in extras if k.startswith(prefix)])
        groups[label]['matches_compiled_source'] = not any(groups[label][k] for k in ('missing','different','extra'))
    return dict(groups=groups, missing=missing, different=different, extra=extras,
                all_runtime_assets_match=not (missing or different or extras))


def compact(result):
    # Complete safe filenames remain in the receipt; keep terminal output bounded.
    output = {k:v for k,v in result.items() if k not in ('runtime_assets','images')}
    output['images_reverified'] = [dict(role=i['role'],content_and_unpack=i['content_and_unpack'],pinned=i['pinned'])
                                  for i in result['images']]
    assets = result['runtime_assets']; groups={}
    for name, group in assets['groups'].items():
        item = {k:v for k,v in group.items() if k not in ('missing','different','extra')}
        for category in ('missing','different','extra'):
            item[category+'_count'] = len(group[category]); item[category+'_first20'] = group[category][:20]
        groups[name] = item
    output['runtime_assets'] = dict(all_runtime_assets_match=assets['all_runtime_assets_match'],groups=groups,
        full_difference_lists='RETAINED_IN_PRIVATE_RESULT_JSON')
    return output


def node_state():
    argv = KUBE+['get', 'node', 'datax-01', '-o', 'json']
    r = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    need(r.returncode == 0 and len(r.stdout) < 1024**2, 'node_read_failed')
    d = json.loads(r.stdout)
    need(d['metadata']['name'] == 'datax-01', 'node_identity')
    conditions = {x['type']:x['status'] for x in d['status'].get('conditions', [])}
    mem = re.search(r'(?m)^MemAvailable:\s+(\d+)\s+kB$', Path('/proc/meminfo').read_text())
    return dict(name='datax-01', uid=d['metadata']['uid'], conditions=conditions,
        unschedulable=d['spec'].get('unschedulable',False), taints=d['spec'].get('taints',[]),
        allocatable=d['status']['allocatable'], host_available_memory_bytes=int(mem[1])*1024 if mem else None,
        capacity_is_not_free_schedulable_resources=True)


def common():
    return dict(production_writes=0, provider_requests=0, production_database_connections=0,
        production_redis_connections=0, image_code_executed=False, archive_extracted=False,
        images_imported=False, pods_created=0, build_started=False, model_changed=False,
        consumer_slots_changed=False, native_enrichment_acceptance='NOT_TESTED', ready_for_rollout=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.parse_args()
    need(sys.platform == 'linux' and os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-01'
         and platform.machine() in ('aarch64','arm64'), 'root_arm64_datax01_required')
    os.umask(0o077); resource.setrlimit(resource.RLIMIT_CORE,(0,0)); os.nice(10)
    def stop(signum, frame): raise AuditFailure('audit_interrupted')
    for sig in (signal.SIGINT,signal.SIGTERM,signal.SIGHUP,signal.SIGALRM): signal.signal(sig,stop)
    signal.alarm(900)
    state = None; p = None
    try:
        phase('BIND_PRELOAD_NATIVE_AND_COMPILED_SOURCE_RECEIPTS')
        p = frozen.load()
        stores = json.loads(read(STORE, STORE_SHA))
        need(stores['enrichment_store_preload_recovery'] == 'PASS' and stores['postgres_reimported'] is False
             and stores['production_writes'] == 0 and len(stores['images']) == 2, 'store_receipt')
        native = json.loads(read(NATIVE,NATIVE_SHA)); need(native['native_glm_acceptance']=='PASS','native_receipt')
        read(CANDIDATE/'BUILD-RESULT.json', BUILD_SHA)
        expected, source_digest = source_assets()
        phase('RECHECK_LOCAL_STORE_IMAGE_IDENTITIES_READ_ONLY')
        frozen.install_trace(p)
        images = [p.image_check(i, pinned=True) for i in p.ITEMS]
        directory(Path('/var/lib'))
        state = Path(tempfile.mkdtemp(prefix='weknora-enrichment-runtime-audit-',dir='/var/lib'))
        phase('READ_FRESH_NODE_CAPACITY')
        node = node_state()
        phase('COMPARE_PINNED_IMAGE_ASSETS_WITH_COMPILED_SOURCE')
        actual = inspect_archive(CANDIDATE/'weknora.candidate.oci.tar', ARCHIVE_SHA, ARCHIVE_BYTES, MANIFEST, CONFIG)
        compared = comparison(expected,actual)
        result = dict(common(), enrichment_runtime_audit='COMPLETE', source_manifest_sha256=source_digest,
            runtime_assets=compared, node=node, images=images, prior_store_report_sha256=STORE_SHA,
            candidate_archive_sha256=ARCHIVE_SHA, fixture_start_approved=False,
            next_action='REVIEW_RUNTIME_ASSET_DIFFERENCES_AND_PREPARE_ISOLATED_APP' if not compared['all_runtime_assets_match']
                        else 'PREPARE_RESOURCE_BOUNDED_ISOLATED_APP_AND_REQUEST_BUDGET')
        p.record(state/'RESULT.json',result); emit(compact(result))
        emit(dict(report=str(state/'RESULT.json'),report_sha256=hashlib.sha256(read(state/'RESULT.json')).hexdigest()))
        return 0
    except (Exception,KeyboardInterrupt) as exc:
        result=dict(common(),enrichment_runtime_audit='INCOMPLETE',phase=PHASE,error_type=type(exc).__name__,
                    failed_check=str(exc) if isinstance(exc,AuditFailure) else p.cli.safe_detail(exc) if p else 'SAFE_DIAGNOSTIC_REQUIRED',
                    last_operation=dict(p.cli.LAST) if p else {},
                    code_frames=[dict(file=Path(x.filename).name,function=x.name,line=x.lineno)
                                 for x in traceback.extract_tb(exc.__traceback__)[-8:]],raw_error_printed=False)
        if state is not None and p is not None: p.record(state/'FAILED.json',result)
        emit(result); return 1
    finally: signal.alarm(0)


if __name__ == '__main__': sys.exit(main())

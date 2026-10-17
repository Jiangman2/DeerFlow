#!/usr/bin/env python3
"""Inspect only the fixed six candidates' cached content and snapshot chains. No import."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import stat
import sys
import tarfile
import time
import types
import uuid
import zlib

OP = 'e8104bf11adc4959ad0b6078e7c97990'
BASE_SHA = '6e1c2532bdec3b90d83f8eb43962412fd0f0d93d98f1590d61f181f9e7e4060f'
SOURCE_FAIL_SHA = 'e7d0b74db03405e4e24a892e24690f17c757b2eafb70fd10876e4e7f3b9f558c'
TARGET_FAIL_SHA = '2552e68ee47e275b80e955ef25d79d726f623cf4f174f411955648f0bb22ccc2'
PROFILE_SHA = 'fdeef64d8c1ced80251f733e0e83cad589032712c1b8a476affc1797105002d2'
CATALOG_SHA = '@@CATALOG_SHA@@'
CATALOG_Z64 = '@@CATALOG_Z64@@'
MARKER = 'PORTAL_CACHE_RESULT='
SOURCE = Path('/var/lib/portal-ppt-native-preload-source-' + OP)
TARGET = Path('/var/lib/portal-ppt-native-preload-target-' + OP)
CONTROL = Path('/var/lib/portal-ppt-native-preload-control-' + OP)
SEALED = Path('/opt/portal-ppt-native-sealed-r2-' + OP)
B = None
STATE = None
REPORT = None


def ensure(condition, code):
    if not condition:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode()


def pack(value):
    return base64.b64encode(zlib.compress(json.dumps(value, separators=(',', ':')).encode(), 9)).decode()


def unpack(value):
    raw = base64.b64decode(value, validate=True)
    decoder = zlib.decompressobj()
    data = decoder.decompress(raw, 2 * 1024 ** 2 + 1)
    ensure(len(data) <= 2 * 1024 ** 2 and decoder.eof and not decoder.unused_data, 'packed_input_limit')
    return json.loads(data)


def wire_plan(plan):
    # Random digests do not compress across distant repeated image chains. Send
    # each chain once and use integer references so SSH stays below argv limits.
    names = sorted(plan['chains'])
    lookup = {name: number for number, name in enumerate(names)}
    values = {key: plan[key] for key in ('schema', 'prior_operation', 'profile_sha256',
                                        'maximum_outer_blob_bytes', 'catalog_sha256')}
    values['blobs'] = sorted(plan['blobs'].items())
    values['chains'] = [[name, lookup.get(plan['chains'][name]['parent'], -1),
                         plan['chains'][name]['uncompressed_bytes_upper_bound']] for name in names]
    values['images'] = [dict(role=row['role'], expanded_groups=row['expanded_groups'],
                            chain_indices=[lookup[layer['chain_id']] for layer in row['layers']])
                        for row in plan['images']]
    return values


def unwire_plan(values):
    plan = {key: values[key] for key in ('schema', 'prior_operation', 'profile_sha256',
                                        'maximum_outer_blob_bytes', 'catalog_sha256')}
    ensure(0 < len(values['chains']) <= 2048 and 0 < len(values['blobs']) <= 2048, 'wire_plan_limit')
    table = values['chains']
    plan['chains'] = {}
    for name, parent, bound in table:
        ensure(name not in plan['chains'] and type(parent) is int and -1 <= parent < len(table), 'wire_chain_shape')
        plan['chains'][name] = dict(chain_id=name, parent='' if parent == -1 else table[parent][0],
                                   uncompressed_bytes_upper_bound=bound)
    plan['blobs'] = dict(values['blobs'])
    ensure(len(plan['blobs']) == len(values['blobs']), 'wire_blob_duplicate')
    plan['images'] = [dict(role=row['role'], expanded_groups=row['expanded_groups'],
                          layers=[dict(plan['chains'][table[index][0]]) for index in row['chain_indices']])
                      for row in values['images']]
    return plan


def private_read(path, maximum):
    # The frozen helper itself must be trusted before executing it.
    path = Path(path)
    for parent in (path.parent,) + tuple(path.parent.parents):
        info = parent.lstat()
        ensure(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
               'private_parent_changed')
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        ensure(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
               and not before.st_mode & 0o022 and before.st_size <= maximum, 'private_file_changed')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(maximum + 1)
        after = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        ensure(len(raw) <= maximum and identity(before) == identity(after) == identity(path.lstat()),
               'private_file_changed_during_read')
        return raw
    finally:
        os.close(fd)


def load_helper(remote=False):
    global B
    path = (CONTROL if remote else SOURCE) / 'preload_native.py'
    raw = private_read(path, 128 * 1024)
    ensure(sha(raw) == BASE_SHA, 'frozen_preloader_changed')
    module = types.ModuleType('portal_frozen_preloader')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    module.DEADLINE = time.monotonic() + 600
    B = module
    def interrupted(signum, frame):
        raise B.Failure('interrupted_no_automatic_retry')
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)
    profile_raw = private_read(path.parent / 'PROFILE.json', 65536)
    ensure(sha(profile_raw) == PROFILE_SHA, 'profile_changed')
    return B.parse(profile_raw)


def failure_binding(remote=False):
    root, expected = (TARGET, TARGET_FAIL_SHA) if remote else (SOURCE, SOURCE_FAIL_SHA)
    raw = private_read(root / 'FAIL.json', 65536)
    ensure(sha(raw) == expected, 'prior_failure_changed')
    record = B.parse(raw)
    ensure(record['operation'] == OP and record['target_import_requested'] is False, 'prior_import_state_changed')
    ensure(not (root / 'IMPORT-ATTEMPT.json').exists() and not (root / 'REPORT.json').exists(), 'attempt_already_advanced')
    if remote:
        ensure(not (root / 'PREPARED.json').exists(), 'target_already_prepared')
    else:
        ensure(sha(private_read(root / 'TARGET-PREPARE.json', 65536)) == TARGET_FAIL_SHA, 'target_receipt_changed')


def catalog_rows(catalog):
    rows = {}
    for row in catalog['images']:
        layers = row.get('layers')
        if layers is None:
            layers = [catalog['layers'][index] for index in row['layer_indices']]
        ensure(row['role'] not in rows, 'catalog_duplicate_role')
        rows[row['role']] = (row, layers)
    return rows


def scan_archive(row, expected, known_layers):
    """Seek only tar headers and small exact metadata in retained root-owned seals."""
    path = SEALED / row['basename']
    B.safe_dir(path.parent)
    fd = B.open_anchored(path)
    try:
        before = os.fstat(fd)
        ensure(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
               and stat.S_IMODE(before.st_mode) == 0o600 and before.st_size == row['archive_bytes'],
               'source_seal_metadata_changed')
        wanted = {'index.json', 'blobs/sha256/' + row['manifest_digest'][7:],
                  'blobs/sha256/' + row['config_digest'][7:]}
        small, blobs = {}, {}
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            with tarfile.open(fileobj=stream, mode='r:') as archive:
                for number, member in enumerate(archive):
                    B.deadline()
                    ensure(number < 4096 and not member.linkname, 'outer_header_shape')
                    if member.isdir():
                        ensure(member.name.rstrip('/') in ('.', 'blobs', 'blobs/sha256'), 'outer_directory_shape')
                        continue
                    ensure(member.isfile(), 'outer_header_shape')
                    if member.name.startswith('blobs/'):
                        ensure(re.fullmatch('blobs/sha256/[a-f0-9]{64}', member.name) is not None,
                               'outer_blob_name')
                        dig = 'sha256:' + member.name.rsplit('/', 1)[1]
                        ensure(dig not in blobs and 0 < member.size <= row['archive_bytes'], 'outer_blob_duplicate_or_size')
                        blobs[dig] = member.size
                    if member.name in wanted:
                        ensure(member.name not in small and member.size <= 8 * 1024 ** 2, 'metadata_member_shape')
                        small[member.name] = archive.extractfile(member).read(8 * 1024 ** 2 + 1)
        ensure(B.identity(before) == B.identity(os.fstat(fd)) == B.identity(path.lstat()), 'source_seal_changed_during_scan')
    finally:
        os.close(fd)
    ensure(set(small) == wanted, 'small_metadata_missing')
    for field in ('manifest_digest', 'config_digest'):
        dig = row[field]
        ensure('sha256:' + sha(small['blobs/sha256/' + dig[7:]]) == dig, 'metadata_digest_mismatch')
    index = B.parse(small['index.json'])
    ensure(index.get('schemaVersion') == 2 and len(index.get('manifests', [])) == 1
           and index['manifests'][0]['digest'] == row['manifest_digest'], 'index_shape')
    manifest = B.parse(small['blobs/sha256/' + row['manifest_digest'][7:]])
    config = B.parse(small['blobs/sha256/' + row['config_digest'][7:]])
    ensure(manifest.get('schemaVersion') == 2 and manifest['config']['digest'] == row['config_digest']
           and config.get('architecture') == 'arm64' and config.get('os') == 'linux', 'manifest_config_binding')
    descriptors, diff_ids = manifest['layers'], config['rootfs']['diff_ids']
    ensure(0 < len(descriptors) == len(diff_ids) <= 512, 'layer_count')
    tail = row['role'] == 'ppt-master-agent-host-forward' and len(known_layers) + 1 == len(descriptors)
    ensure(len(known_layers) == len(descriptors) or tail, 'catalog_layer_count')
    layers, parent = [], ''
    for number, (desc, diff) in enumerate(zip(descriptors, diff_ids)):
        B.digest(desc['digest'])
        B.digest(diff)
        ensure(type(desc['size']) is int and blobs.get(desc['digest']) == desc['size'], 'layer_blob_size')
        if number < len(known_layers):
            known = known_layers[number]
            ensure(known['blob_digest'] == desc['digest'] and known['compressed_bytes'] == desc['size']
                   and known['diff_id'] == diff, 'catalog_layer_binding')
            bound = known['uncompressed_bytes_upper_bound']
        else:
            ensure(tail and number == 128 and expected.get('tail_metadata_required'), 'host_tail_binding')
            bound = 8 * 1024 ** 2
        ensure(type(bound) is int and bound >= 0, 'expanded_bound_shape')
        chain = diff if not parent else 'sha256:' + sha((parent + ' ' + diff).encode())
        layers.append(dict(blob_digest=desc['digest'], compressed_bytes=desc['size'], diff_id=diff,
                           uncompressed_bytes_upper_bound=bound, chain_id=chain, parent=parent))
        parent = chain
    return dict(role=row['role'], basename=row['basename'], archive_sha256=row['archive_sha256'],
                archive_bytes=row['archive_bytes'], manifest_digest=row['manifest_digest'], config_digest=row['config_digest'],
                outer_blobs=blobs, layers=layers, expanded_groups=expected.get('expanded_groups', []),
                source_scan='OUTER_HEADERS_AND_VERIFIED_SMALL_METADATA_ONLY_NO_FULL_REHASH_OR_DECOMPRESSION')


def collect_plan(profile):
    catalog = unpack(CATALOG_Z64)
    # Catalog SHA is over its canonical compact encoding from the delivery builder.
    ensure(sha(json.dumps(catalog, sort_keys=True, separators=(',', ':')).encode()) == CATALOG_SHA, 'catalog_hash')
    rows = catalog_rows(catalog)
    images, blobs, chains = [], {}, {}
    for row in profile['images']:
        expected, layers = rows[row['role']]
        for key in ('basename', 'archive_bytes', 'archive_sha256', 'manifest_digest', 'config_digest'):
            ensure(expected[key] == row[key], 'catalog_image_binding')
        B.progress('SOURCE_SMALL_METADATA', role=row['role'])
        item = scan_archive(row, expected, layers)
        images.append(item)
        for dig, size in item['outer_blobs'].items():
            ensure(dig not in blobs or blobs[dig] == size, 'cross_archive_blob_size')
            blobs[dig] = size
        for layer in item['layers']:
            chain = layer['chain_id']
            if chain in chains:
                ensure(chains[chain]['parent'] == layer['parent'] and chains[chain]['diff_id'] == layer['diff_id'],
                       'cross_archive_chain_conflict')
                chains[chain]['uncompressed_bytes_upper_bound'] = max(
                    chains[chain]['uncompressed_bytes_upper_bound'], layer['uncompressed_bytes_upper_bound'])
            else:
                chains[chain] = dict(layer)
    return dict(schema='portal-ppt-native-cache-plan.v1', prior_operation=OP,
                profile_sha256=PROFILE_SHA, images=images, blobs=blobs, chains=chains,
                maximum_outer_blob_bytes=max(blobs.values()), catalog_sha256=CATALOG_SHA)


def parse_content(raw, wanted):
    found = raw.decode('ascii').split()
    ensure(len(set(found)) == len(found) and set(found) <= set(wanted), 'content_filter_output')
    return sorted(found)


def parse_snapshots(raw, chains):
    lines = raw.decode('utf-8').splitlines()
    ensure(lines and lines[0].split() == ['KEY', 'PARENT', 'KIND'], 'snapshot_table_header')
    selected = {}
    for line in lines[1:]:
        words = line.split()
        if not words or words[0] not in chains:
            continue
        ensure(words[0] not in selected and len(words) in (2, 3), 'snapshot_table_row')
        name = words[0]
        parent, kind = ('', words[1]) if len(words) == 2 else (words[1], words[2])
        ensure(kind in ('Committed', 'Active', 'View'), 'snapshot_kind')
        ensure(parent == chains[name]['parent'], 'snapshot_parent_mismatch')
        selected[name] = dict(name=name, parent=parent, kind=kind)
    return selected


def directory_projection(path):
    value = Path(path).lstat()
    ensure(stat.S_ISDIR(value.st_mode), 'directory_projection_type')
    return dict(path=path, uid=value.st_uid, gid=value.st_gid, mode=oct(stat.S_IMODE(value.st_mode)),
                device=value.st_dev, inode=value.st_ino)


def cache_sample(plan, label):
    found = []
    digests = sorted(plan['blobs'])
    # Independent filters are OR in containerd. Bound each argv and call count.
    for offset in range(0, len(digests), 128):
        batch = digests[offset:offset + 128]
        raw = B.capture(B.CTR + ['content', 'ls', '--quiet'] + ['digest==' + d for d in batch],
                        label + '.CONTENT', timeout=30, limit=65536)
        found.extend(parse_content(raw, batch))
    raw = B.capture(B.CTR + ['snapshots', '--snapshotter', 'overlayfs', 'ls'],
                    label + '.SNAPSHOTS', timeout=30, limit=8 * 1024 ** 2)
    snapshots = parse_snapshots(raw, plan['chains'])
    paths = B.image_fs_paths(B.parse(B.capture(B.CRI + ['imagefsinfo', '--output', 'json'], label + '.IMAGEFS')))
    images = [B.disk(Path(path)) for path in paths]
    ensure(images and len({item['device'] for item in images}) == 1, 'split_imagefs_not_supported')
    return dict(content=sorted(found), snapshots=snapshots, image_filesystems=images,
                root=B.disk('/var/lib'), opt=B.disk('/opt'),
                directories=[directory_projection('/'), directory_projection('/opt')])


def estimate(plan, first, last):
    def fs_identity(sample):
        return (tuple((item['path'], item['device'], item['capacity_bytes']) for item in sample['image_filesystems']),
                tuple((sample[key]['device'], sample[key]['capacity_bytes']) for key in ('root', 'opt')))
    ensure(fs_identity(first) == fs_identity(last), 'filesystem_changed_between_samples')
    ensure(last['opt']['device'] != last['image_filesystems'][0]['device'], 'opt_not_separate_from_imagefs')
    content = set(first['content']) & set(last['content'])
    cached = set()
    for image in plan['images']:
        # Only a complete, ordered committed ancestry gets expanded-size credit.
        for layer in image['layers']:
            chain = layer['chain_id']
            a, b = first['snapshots'].get(chain), last['snapshots'].get(chain)
            if a != b or not a or a['kind'] != 'Committed' or a['parent'] != layer['parent']:
                break
            cached.add(chain)
    missing_content = sum(size for dig, size in plan['blobs'].items() if dig not in content)
    missing_expanded = sum(row['uncompressed_bytes_upper_bound'] for chain, row in plan['chains'].items()
                           if chain not in cached)
    capped = set()
    for image in plan['images']:
        for group in image.get('expanded_groups', []):
            members = {image['layers'][position]['chain_id'] for position in group['layer_positions']}
            ensure(not members & capped and group['uncompressed_bytes_upper_bound'] >= 0, 'expanded_group_overlap')
            capped.update(members)
            upper = sum(plan['chains'][chain]['uncompressed_bytes_upper_bound'] for chain in members - cached)
            missing_expanded -= max(0, upper - group['uncompressed_bytes_upper_bound'])
    image_fs = last['image_filesystems'][0]
    reserve = max(20 * 1024 ** 3, (image_fs['capacity_bytes'] + 4) // 5)
    required = reserve + 2 * 1024 ** 3 + missing_content + 2 * missing_expanded + plan['maximum_outer_blob_bytes']
    rows = []
    for image in plan['images']:
        prefix = 0
        for layer in image['layers']:
            if layer['chain_id'] not in cached:
                break
            prefix += 1
        upper = sum(x['uncompressed_bytes_upper_bound'] for x in image['layers'] if x['chain_id'] not in cached)
        for group in image.get('expanded_groups', []):
            group_upper = sum(image['layers'][p]['uncompressed_bytes_upper_bound'] for p in group['layer_positions']
                              if image['layers'][p]['chain_id'] not in cached)
            upper -= max(0, group_upper - group['uncompressed_bytes_upper_bound'])
        rows.append(dict(role=image['role'], layers=len(image['layers']), committed_prefix_at_both_samples=prefix,
                         expanded_missing_upper_bound=upper))
    return dict(scope='OBSERVED_INCREMENTAL_IMAGEFS_ESTIMATE_ONLY_NOT_A_RESERVATION',
                assumes_all_upload_and_seal_archives_on_separate_opt_filesystem=True,
                available_bytes=image_fs['available_bytes'], image_device=image_fs['device'],
                missing_content_bytes=missing_content, missing_expanded_bytes_upper_bound=missing_expanded,
                import_scratch_maximum_actual_blob=plan['maximum_outer_blob_bytes'],
                reserve_bytes=reserve, extra_headroom_bytes=2 * 1024 ** 3,
                required_bytes=required, bytes_fit_at_sample=image_fs['available_bytes'] >= required,
                available_inodes=image_fs['available_inodes'], required_inodes=100000,
                inode_fit_at_sample=image_fs['available_inodes'] >= 100000, images=rows,
                cache_can_change=True, import_preflight_recheck_required=True, ready_for_import=False)


def remote_collect(plan):
    profile = load_helper(remote=True)
    failure_binding(remote=True)
    ensure(os.uname().machine in ('aarch64', 'arm64') and len(plan['images']) == len(profile['images']) == 6,
           'target_platform_or_plan')
    ensure(plan['prior_operation'] == OP and plan['profile_sha256'] == PROFILE_SHA
           and 0 < len(plan['blobs']) <= 2048 and 0 < len(plan['chains']) <= 2048, 'plan_shape')
    first = cache_sample(plan, 'BEFORE')
    last = cache_sample(plan, 'AFTER')
    failure_binding(remote=True)
    result = dict(schema='portal-ppt-native-cache-diagnostic.v1', status='READ_ONLY_DIAGNOSTIC_COMPLETE',
                prior_operation=OP, source_host='datax-01', target='datax-02',
                samples=dict(before=first, after=last),
                relevant_cache_stable=first['content'] == last['content'] and first['snapshots'] == last['snapshots'],
                archive_transfer=False, image_import=False,
                image_deletion=False, workload_mutation=False, database_access=False, model_access=False,
                ready_for_import=False, ready_for_rollout=False)
    try:
        result['estimate'] = estimate(plan, first, last)
    except ValueError as exc:
        result['estimate'] = dict(status='UNSUPPORTED_REVIEW_REQUIRED', error_code=str(exc),
                                  ready_for_import=False, bytes_fit_at_sample=False)
    return result


def remote_entry(plan):
    ensure(os.geteuid() == 0 and socket.gethostname().split('.')[0] == 'datax-02', 'wrong_target_host')
    try:
        result = remote_collect(plan)
        rc = 0
    except Exception as exc:
        code = exc.code if B is not None and isinstance(exc, B.Failure) else str(exc) if isinstance(exc, ValueError) else 'DIAGNOSTIC_FAILED'
        result = dict(schema='portal-ppt-native-cache-diagnostic.v1', status='INCOMPLETE', prior_operation=OP,
                      error_code=code, failed_phase=B.PHASE if B else 'BINDING', ready_for_import=False, ready_for_rollout=False)
        rc = 1
    print(MARKER + base64.b64encode(encoded(result)).decode(), flush=True)
    return rc


def source_collect():
    global STATE, REPORT
    ensure(sys.platform.startswith('linux') and os.geteuid() == 0
           and socket.gethostname().split('.')[0] == 'datax-01', 'run_as_root_on_datax01')
    os.umask(0o077)
    profile = load_helper()
    failure_binding()
    # Capture operator-delivered bytes before invoking SSH; never reread after prompts.
    script = B.read_file(Path(__file__).absolute(), 256 * 1024)
    run_id = uuid.uuid4().hex
    STATE = B.fresh(Path('/var/lib/portal-ppt-native-cache-source-' + run_id))
    REPORT = dict(schema='portal-ppt-native-cache-diagnostic.v1', status='STARTED', operation=run_id,
                  prior_operation=OP, script_sha256=sha(script), production_mutation=False,
                  automatic_retry=False, ready_for_import=False, ready_for_rollout=False)
    B.write_new(STATE / 'STARTED.json', encoded(REPORT))
    REPORT['node_before'] = B.node_guard(profile)
    plan = collect_plan(profile)
    B.write_new(STATE / 'PLAN.json', encoded(plan))
    known = B.trusted_key(private_read(SOURCE / 'known_hosts', 65536), profile['target'])
    known_path = STATE / 'known_hosts'
    B.write_new(known_path, known)
    # The target uses only the generated digest/size plan. It does not need the
    # local catalog or any config metadata in its embedded executable text.
    text = script.decode()
    ensure(text.count(CATALOG_Z64) == 1, 'embedded_catalog_shape')
    remote_script = text.replace(CATALOG_Z64, '')
    remote_plan = {key: plan[key] for key in ('schema', 'prior_operation', 'profile_sha256', 'blobs',
                                             'maximum_outer_blob_bytes', 'catalog_sha256')}
    fields = ('chain_id', 'parent', 'uncompressed_bytes_upper_bound')
    remote_plan['chains'] = {key: {field: row[field] for field in fields} for key, row in plan['chains'].items()}
    remote_plan['images'] = [dict(role=row['role'], expanded_groups=row['expanded_groups'],
                                 layers=[{field: layer[field] for field in fields} for layer in row['layers']])
                             for row in plan['images']]
    payload = dict(code=remote_script, plan=wire_plan(remote_plan))
    zipped = pack(payload)
    launcher = "import base64,zlib,json; p=json.loads(zlib.decompress(base64.b64decode(" + repr(zipped) + "))); g={'__name__':'portal_cache_remote'}; exec(compile(p['code'],'portal_cache_remote.py','exec'),g); raise SystemExit(g['remote_entry'](g['unwire_plan'](p['plan'])))"
    ensure(len(launcher.encode()) < 110 * 1024, 'ssh_inline_payload_too_large')
    command = 'sudo -p ' + shlex.quote('[portal sudo password] ') + ' python3 -I -B -u -c ' + shlex.quote(launcher)
    rc, raw = B.run(['ssh'] + B.ssh_options(known_path) + ['-tt', 'cnooc@' + profile['target']['ip'], command],
                    'READ_ONLY_TARGET_CACHE', timeout=600, interactive=True, allow_failure=True, limit=2 * 1024 ** 2)
    matches = re.findall(rb'PORTAL_CACHE_RESULT=([A-Za-z0-9+/=]+)', raw)
    ensure(len(matches) == 1, 'remote_receipt_missing_or_multiple')
    target = B.parse(base64.b64decode(matches[0], validate=True))
    ensure(target.get('schema') == 'portal-ppt-native-cache-diagnostic.v1' and target.get('prior_operation') == OP,
           'remote_receipt_binding')
    B.write_new(STATE / 'TARGET-REPORT.json', encoded(target))
    REPORT['target_report'] = target
    REPORT['node_after'] = B.node_guard(profile)
    REPORT['plan_sha256'] = sha(encoded(plan))
    REPORT['status'] = 'COMPLETE_REVIEW_REQUIRED' if rc == 0 and target.get('status') == 'READ_ONLY_DIAGNOSTIC_COMPLETE' else 'INCOMPLETE'
    report_raw = encoded(REPORT)
    B.write_new(STATE / 'REPORT.json', report_raw)
    # No root writes follow handoff of this separate private download directory.
    import pwd
    owner = pwd.getpwnam('cnooc')
    ensure(owner.pw_uid > 0, 'invalid_download_owner')
    download = B.fresh(Path('/var/lib/portal-ppt-native-cache-report-' + run_id))
    B.write_new(download / 'REPORT.json', report_raw)
    os.chown(str(download / 'REPORT.json'), owner.pw_uid, -1)
    os.chown(str(download), owner.pw_uid, -1)
    summary = dict(status=REPORT['status'], report_sha256=sha(report_raw), report_bytes=len(report_raw),
                   download_report=str(download / 'REPORT.json'), evidence=str(STATE),
                   plan_sha256=REPORT['plan_sha256'],
                   cache_stable=target.get('relevant_cache_stable'), estimate=target.get('estimate'),
                   target_directories=target.get('samples', {}).get('after', {}).get('directories'),
                   target_filesystems={key: target.get('samples', {}).get('after', {}).get(key)
                                       for key in ('root', 'opt', 'image_filesystems')},
                   error_code=target.get('error_code'), archive_transfer=False, image_import=False,
                   workload_mutation=False, ready_for_rollout=False, automatic_retry=False)
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)
    return 0 if REPORT['status'] == 'COMPLETE_REVIEW_REQUIRED' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collect', action='store_true', required=True)
    parser.parse_args()
    try:
        raise SystemExit(source_collect())
    except (Exception, KeyboardInterrupt) as exc:
        code = exc.code if B is not None and isinstance(exc, B.Failure) else str(exc) if isinstance(exc, ValueError) else 'DIAGNOSTIC_FAILED'
        result = REPORT or dict(schema='portal-ppt-native-cache-diagnostic.v1', prior_operation=OP)
        result.update(status='INCOMPLETE', error_code=code, automatic_retry=False,
                      archive_transfer=False, image_import=False, workload_mutation=False,
                      ready_for_import=False, ready_for_rollout=False)
        if STATE is not None:
            try:
                B.write_new(STATE / 'FAIL.json', encoded(result))
            except Exception:
                pass
        print(json.dumps(dict(status='INCOMPLETE', error_code=code, evidence=str(STATE),
                              automatic_retry=False, ready_for_rollout=False)), flush=True)
        raise SystemExit(1)

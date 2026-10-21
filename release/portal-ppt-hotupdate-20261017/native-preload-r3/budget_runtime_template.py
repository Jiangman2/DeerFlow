"""Fixed evidence, conservative allocation allowances, and live two-phase gates."""
import copy
import hashlib
import json
import os
import stat

BINDING = json.loads('@@BINDING_JSON@@')
GIB = 1024 ** 3
HEADROOM = 2 * GIB
METADATA = 64 * 1024 ** 2


def require(ok, code):
    if not ok:
        raise ValueError(code)


def committed(plan, snapshots):
    found = set()
    for image in plan['images']:
        for layer in image['layers']:
            row = snapshots.get(layer['chain_id'])
            if not row or row['kind'] != 'Committed' or row['parent'] != layer['parent']:
                break
            found.add(layer['chain_id'])
    return found


def prepare_plan(plan, accepted_report):
    """Caller verifies the original plan/report bytes before invoking this."""
    require(accepted_report['plan_sha256'] == BINDING['plan_sha256'], 'allocation_plan_binding')
    report = accepted_report['target_report']
    first, last = report['samples']['before'], report['samples']['after']
    require(report['relevant_cache_stable'] and first['content'] == last['content']
            and first['snapshots'] == last['snapshots'], 'allocation_cache_not_stable')
    base = committed(plan, first['snapshots']) & committed(plan, last['snapshots'])
    amounts = {}
    local = BINDING['chains']
    host = [r for r in plan['images'] if r['role'] == 'ppt-master-agent-host-forward']
    require(len(host) == 1 and len(host[0]['layers']) == 129, 'allocation_host_shape')
    host_tail = host[0]['layers'][-1]
    require(all(r['chain_id'] in base for r in host[0]['layers'][:-1]), 'allocation_host_base_not_cached')
    for chain, layer in plan['chains'].items():
        if chain in base:
            continue
        if chain in local:
            diff, encodings, amount = local[chain]
            require(layer['diff_id'] == diff, 'allocation_layer_binding')
            amounts[chain] = amount
        else:
            require(layer == host_tail and layer['uncompressed_bytes_upper_bound'] <= 8 * 1024 ** 2,
                    'allocation_unreviewed_missing_layer')
            amounts[chain] = 16 * 1024 ** 2
    for image in plan['images']:
        for layer in image['layers']:
            if layer['chain_id'] in local:
                diff, encodings, amount = local[layer['chain_id']]
                require(layer['diff_id']==diff and [layer['blob_digest'],layer['compressed_bytes']] in encodings,
                        'allocation_image_layer_encoding')
    require(set(local) <= set(amounts) and len(amounts) == len(local) + 1, 'allocation_missing_chain_set')
    require(last['root']['device'] == last['image_filesystems'][0]['device']
            and last['root']['device'] != last['opt']['device'], 'allocation_filesystem_shape')
    result = copy.deepcopy(plan)
    result['allocation'] = dict(schema='portal-native-physical-allowance.v1',
        evidence_sha256=BINDING['allocation_sha256'], hardlink_review_sha256=BINDING['hardlink_review_sha256'],
        physical_by_chain=amounts, filesystems={key:last[key] for key in ('root','opt','image_filesystems')},
        maximum_metadata_import_bytes=max(plan['blobs'][r['manifest_digest']] + plan['blobs'][r['config_digest']]
            for r in plan['images']) + 65536,
        strict_filesystem_upper_bound=False, policy='4K_ROUNDED_DATA_DIRECTORY_SYMLINK_OBJECT_ALLOWANCES_PLUS_64M_METADATA',
        required_cached_chains=len(base))
    return result


def parse_content(raw, wanted):
    values = raw.decode('ascii').split()
    require(len(values) == len(set(values)) and set(values) <= set(wanted), 'allocation_content_filter')
    return set(values)


def parse_snapshots(raw, chains):
    lines = raw.decode('utf-8').splitlines()
    require(lines and lines[0].split() == ['KEY','PARENT','KIND'], 'allocation_snapshot_header')
    rows = {}
    for line in lines[1:]:
        words = line.split()
        if not words or words[0] not in chains:
            continue
        require(len(words) in (2,3) and words[0] not in rows, 'allocation_snapshot_row')
        parent, kind = ('',words[1]) if len(words)==2 else (words[1],words[2])
        require(kind in ('Committed','Active','View') and parent == chains[words[0]]['parent'],
                'allocation_snapshot_parent')
        rows[words[0]] = dict(kind=kind,parent=parent)
    return rows


def reserve(row):
    return max(20 * GIB, (row['capacity_bytes'] + 4) // 5)


def budget(plan, content, snapshots, phase):
    """Reconciles unique content/chains; already allocated bytes are in live free."""
    allocation = plan['allocation']
    cached = committed(plan,snapshots)
    amounts = allocation['physical_by_chain']
    required_base = set(plan['chains']) - set(amounts)
    require(len(required_base) == allocation['required_cached_chains'] and required_base <= cached,
            'allocation_cached_base_lost')
    missing = sum(size for dig,size in plan['blobs'].items() if dig not in content)
    physical = sum(amount for chain,amount in amounts.items() if chain not in cached)
    tiny = allocation['maximum_metadata_import_bytes']
    if phase == 'BEFORE_PHASE_B':
        require(not missing, 'allocation_content_closure_lost')
        work = physical + tiny
    else:
        work = max(missing + plan['maximum_outer_blob_bytes'], missing + physical + tiny)
    return dict(missing_content_bytes=missing, remaining_physical_allowance_bytes=physical,
                full_archive_ingest_scratch_bytes=plan['maximum_outer_blob_bytes'],
                metadata_import_scratch_bytes=tiny, metadata_allowance_bytes=METADATA,
                incremental_peak_allowance_bytes=work+METADATA, committed_chains=len(cached))


def stat_row(path, expected, fd=None):
    s = os.stat(path) if fd is None else os.fstat(fd)
    v = os.statvfs(path) if fd is None else os.fstatvfs(fd)
    require(stat.S_ISDIR(s.st_mode) and s.st_dev == expected['device']
            and v.f_blocks*v.f_frsize == expected['capacity_bytes'], 'allocation_filesystem_identity_changed')
    require(0 < v.f_frsize <= 4096 and 0 < v.f_bsize <= 4096 and not v.f_flag & os.ST_RDONLY,
            'allocation_unsupported_block_geometry')
    return dict(path=path,device=s.st_dev,capacity_bytes=v.f_blocks*v.f_frsize,
                available_bytes=v.f_bavail*v.f_frsize,available_inodes=v.f_favail)


def check(B, profile, plan, phase, sealed_fd=None):
    require(phase in ('TARGET_PREPARE','BEFORE_SEAL','BEFORE_PHASE_A','BEFORE_PHASE_B','MONITOR'), 'allocation_phase')
    expected = plan['allocation']['filesystems']
    image_expected = expected['image_filesystems'][0]
    image = stat_row(image_expected['path'], image_expected)
    root = stat_row('/var/lib',expected['root'])
    opt = stat_row('/opt',expected['opt'],sealed_fd)
    if phase == 'MONITOR':
        for row in (image,opt):
            require(row['available_bytes'] >= reserve(row)+HEADROOM and row['available_inodes'] >= 100000,
                    'live_storage_headroom_reached')
        return dict(scope='LIVE_RESERVE_ONLY',capacity_pass=False)
    paths = B.image_fs_paths(B.parse(B.capture(B.CRI+['imagefsinfo','--output','json'],'R3.IMAGEFS')))
    require(paths == [r['path'] for r in expected['image_filesystems']], 'allocation_imagefs_path_changed')
    for row in (image,opt):
        kind = B.capture(['findmnt','-n','-o','FSTYPE','--target',row['path']],'R3.FILESYSTEM',limit=4096).decode().strip()
        require(kind == 'xfs', 'allocation_reviewed_filesystem_required')
    content = set()
    digests = sorted(plan['blobs'])
    for start in range(0,len(digests),128):
        batch = digests[start:start+128]
        raw = B.capture(B.CTR+['content','ls','--quiet']+['digest=='+d for d in batch], 'R3.CONTENT',timeout=30,limit=65536)
        content.update(parse_content(raw,batch))
    raw = B.capture(B.CTR+['snapshots','--snapshotter','overlayfs','ls'],'R3.SNAPSHOTS',timeout=30,limit=8*1024**2)
    result = budget(plan,content,parse_snapshots(raw,plan['chains']),phase)
    # Refresh free space after bounded cache queries; the report is not a reservation.
    image = stat_row(image_expected['path'],image_expected)
    opt = stat_row('/opt',expected['opt'],sealed_fd)
    total = sum(r['archive_bytes'] for r in profile['images'])
    copies = 2 if phase == 'TARGET_PREPARE' else 1 if phase == 'BEFORE_SEAL' else 0
    image['required_bytes'] = reserve(image)+HEADROOM+result['incremental_peak_allowance_bytes']
    opt['required_bytes'] = reserve(opt)+HEADROOM+copies*total+METADATA
    root['required_bytes'] = 4*GIB+16*1024**2
    result.update(filesystems=[image,opt,root],reserve_bytes=reserve(image),extra_headroom_bytes=HEADROOM,
        storage_budget_pass=True,resource_capacity_pass=False,strict_filesystem_upper_bound=False,
        scope='OBSERVED_TWO_PHASE_ALLOCATION_BUDGET_WITH_LIVE_MONITOR_NOT_A_RESERVATION')
    for row in result['filesystems']:
        if row['available_bytes'] < row['required_bytes'] or row['available_inodes'] < 100000:
            result['storage_budget_pass']=False
            error=ValueError('r3_storage_budget_insufficient')
            error.observation=result
            raise error
    return result

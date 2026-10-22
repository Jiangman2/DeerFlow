#!/usr/bin/env python3
"""Single reviewed continuation: fixed six OCI imports, content then unpack."""
import argparse
import base64
import fcntl
import hashlib
import io
import json
import os
import pwd
from pathlib import Path
import re
import shlex
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import threading
import time
import types
import uuid
import zlib

OP = 'e8104bf11adc4959ad0b6078e7c97990'
CACHE_RUN = '3654b51a0bd44c47b4d264fa204c0683'
BASE_SHA = '6e1c2532bdec3b90d83f8eb43962412fd0f0d93d98f1590d61f181f9e7e4060f'
PROFILE_SHA = 'fdeef64d8c1ced80251f733e0e83cad589032712c1b8a476affc1797105002d2'
SOURCE_FAIL_SHA = 'e7d0b74db03405e4e24a892e24690f17c757b2eafb70fd10876e4e7f3b9f558c'
TARGET_FAIL_SHA = '2552e68ee47e275b80e955ef25d79d726f623cf4f174f411955648f0bb22ccc2'
PLAN_SHA = 'e5963c99260ee90f690f7ab0c26e8cf41988b752e74f1211cac22f90b4041ff2'
CACHE_REPORT_SHA = '5a3b7b992a170204247d27a33b740b78d65f0d89c443032020669023c3e7ba15'
ALLOCATION_SHA = '0ac778ad2f9f473e9a24d2fafe0e9401c6e2672b4b6479e647fad3d4eb24de5e'
ALLOCATION_Z64 = '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'
SOURCE = Path('/var/lib/portal-ppt-native-preload-source-' + OP)
OLD_TARGET = Path('/var/lib/portal-ppt-native-preload-target-' + OP)
OLD_CONTROL = Path('/var/lib/portal-ppt-native-preload-control-' + OP)
SOURCE_SEALED = Path('/opt/portal-ppt-native-sealed-r2-' + OP)
CACHE_SOURCE = Path('/var/lib/portal-ppt-native-cache-source-' + CACHE_RUN)
ONCE = Path('/var/lib/portal-ppt-native-preload-r3-' + OP + '.STARTED.json')
MARKER = 'PORTAL_NATIVE_R3_RESULT='
MAX_PACKED = 4 * 1024 ** 2
B = None
A = None
STATE = None
REPORT = None
IMPORT_REQUESTED = False
CODE_SNAPSHOT = None
REPORT_PARENT = Path('/var/lib')


def require(ok, code):
    if not ok:
        raise ValueError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def handoff_report(raw):
    """Finish all private writes before giving the new directory to cnooc."""
    require(len(raw)<=8*1024**2,'handoff_report_limit')
    owner=pwd.getpwnam('cnooc')
    require(owner.pw_uid>0 and owner.pw_gid>=0,'handoff_user_invalid')
    for path in (REPORT_PARENT,)+tuple(REPORT_PARENT.parents):
        value=path.lstat()
        require(stat.S_ISDIR(value.st_mode) and value.st_uid==0 and not value.st_mode&0o022,'handoff_parent_unsafe')
    parent_fd=os.open(str(REPORT_PARENT),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    directory_fd=report_fd=None
    name='portal-ppt-native-r3-report-'+uuid.uuid4().hex
    try:
        os.mkdir(name,0o700,dir_fd=parent_fd)
        directory_fd=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=parent_fd)
        value=os.fstat(directory_fd)
        require(value.st_uid==0 and value.st_mode&0o777==0o700,'handoff_directory_identity')
        report_fd=os.open('REPORT.json',os.O_RDWR|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory_fd)
        with os.fdopen(report_fd,'wb',closefd=False) as stream:
            stream.write(raw);stream.flush();os.fsync(stream.fileno())
        value=os.fstat(report_fd)
        require(stat.S_ISREG(value.st_mode) and value.st_nlink==1 and value.st_uid==0 and value.st_size==len(raw),'handoff_file_identity')
        os.lseek(report_fd,0,os.SEEK_SET)
        require(os.read(report_fd,len(raw)+1)==raw,'handoff_report_changed')
        require(os.listdir(directory_fd)==['REPORT.json'],'handoff_file_set')
        os.fsync(directory_fd);os.fsync(parent_fd)
        os.fchown(report_fd,owner.pw_uid,owner.pw_gid)
        os.fchown(directory_fd,owner.pw_uid,owner.pw_gid)
        # No persistent writes after the directory handoff.
        return str(REPORT_PARENT/name/'REPORT.json')
    finally:
        for fd in (report_fd,directory_fd,parent_fd):
            if fd is not None:os.close(fd)


def file_identity(value):
    return (value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns)


def canonical(value):
    return json.dumps(value, separators=(',', ':'), sort_keys=True, ensure_ascii=True).encode('ascii')


def pack(value):
    return base64.b64encode(zlib.compress(canonical(value), 9)).decode('ascii')


def unpack(value):
    compressed = base64.b64decode(value, validate=True)
    decoder = zlib.decompressobj()
    raw = decoder.decompress(compressed, MAX_PACKED + 1)
    require(len(raw) <= MAX_PACKED and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail, 'packed_input_limit')
    return json.loads(raw)


def private_bytes(path, limit=8 * 1024 ** 2):
    path = Path(path)
    for directory in (path.parent,) + tuple(path.parent.parents):
        value = directory.lstat()
        require(stat.S_ISDIR(value.st_mode) and value.st_uid == 0 and not value.st_mode & 0o022, 'private_parent_required')
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1 and not before.st_mode & 0o022 and before.st_size <= limit, 'private_file_required')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        require(len(raw) <= limit and file_identity(before) == file_identity(os.fstat(fd)) == file_identity(path.lstat()), 'private_file_changed')
        return raw
    finally:
        os.close(fd)


def load_modules(remote=False):
    global B, A
    helper_path = (OLD_CONTROL if remote else SOURCE) / 'preload_native.py'
    raw = private_bytes(helper_path)
    require(sha(raw) == BASE_SHA, 'frozen_helper_changed')
    B = types.ModuleType('fixed_preload_r2_helpers')
    B.__file__ = str(helper_path)
    exec(compile(raw, str(helper_path), 'exec'), B.__dict__)
    B.DEADLINE = time.monotonic() + 21600
    profile_raw = private_bytes(helper_path.parent / 'PROFILE.json', 65536)
    require(sha(profile_raw) == PROFILE_SHA, 'fixed_profile_changed')
    allocation_raw = base64.b64decode(ALLOCATION_Z64, validate=True)
    decoder = zlib.decompressobj()
    allocation_raw = decoder.decompress(allocation_raw, 256 * 1024 + 1)
    require(len(allocation_raw) <= 256 * 1024 and decoder.eof and not decoder.unused_data and sha(allocation_raw) == ALLOCATION_SHA, 'allocation_module_changed')
    A = types.ModuleType('fixed_r3_allocation')
    exec(compile(allocation_raw, 'fixed_r3_allocation.py', 'exec'), A.__dict__)
    return B.parse(profile_raw)


def state_paths(token):
    require(isinstance(token, str) and re.fullmatch('[a-f0-9]{32}', token), 'invalid_operation')
    return dict(source=Path('/var/lib/portal-ppt-native-preload-r3-source-' + token),
                target=Path('/var/lib/portal-ppt-native-preload-r3-target-' + token),
                control=Path('/var/lib/portal-ppt-native-preload-r3-control-' + token),
                upload=Path('/opt/portal-ppt-native-r3-upload-' + token),
                sealed_name='portal-ppt-native-r3-sealed-' + token)


def write_receipt(name, value):
    require(re.fullmatch('[A-Za-z0-9_.-]+', name), 'receipt_name')
    B.write_new(STATE / name, B.encoded(value))


def claim_once(token):
    try:
        B.write_new(ONCE, B.encoded(dict(prior_operation=OP, operation=token, automatic_retry=False)))
    except FileExistsError:
        raise ValueError('r3_attempt_already_started')


def prior_binding(remote=False):
    path = OLD_TARGET if remote else SOURCE
    raw = private_bytes(path / 'FAIL.json')
    require(sha(raw) == (TARGET_FAIL_SHA if remote else SOURCE_FAIL_SHA), 'prior_failure_changed')
    value = B.parse(raw)
    require(value.get('operation') == OP and value.get('target_import_requested') is False, 'prior_import_state')
    require(not (path / 'IMPORT-ATTEMPT.json').exists() and not (path / 'REPORT.json').exists(), 'prior_attempt_advanced')
    if remote:
        require(not (path / 'PREPARED.json').exists(), 'prior_target_prepared')
    else:
        require(sha(private_bytes(path / 'TARGET-PREPARE.json')) == TARGET_FAIL_SHA, 'prior_target_receipt_changed')


def common_lock():
    B.safe_dir('/var/lib')
    fd = os.open('/var/lib/portal-ppt-native-preload-r1.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        value = os.fstat(fd)
        require(stat.S_ISREG(value.st_mode) and value.st_uid == 0 and value.st_nlink == 1 and not value.st_mode & 0o022, 'unsafe_common_lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd)
        raise


def base_report(token):
    return dict(schema='portal-ppt-native-preload-r3.v1', operation=token, prior_operation=OP, target='datax-02',
                source_host='datax-01', status='INCOMPLETE', evidence=str(STATE) if STATE else None,
                profile_sha256=PROFILE_SHA, source_plan_sha256=PLAN_SHA, cache_report_sha256=CACHE_REPORT_SHA,
                allocation_module_sha256=ALLOCATION_SHA, automatic_retry=False, target_import_requested=False,
                image_import_may_be_partial=False, image_rows=[], ready_for_rollout=False, native_runtime_passed=False,
                workload_mutation=False, database_access=False, model_access=False, existing_images_deleted=False,
                pin_label_added=False, phase_a='NOT_REQUESTED', phase_b='NOT_REQUESTED')


def wire_plan(plan):
    names = sorted(plan['chains'])
    chain_index = {name: number for number, name in enumerate(names)}
    blobs = sorted(plan['blobs'].items())
    blob_index = {dig: number for number, (dig, size) in enumerate(blobs)}
    values = {key: plan[key] for key in ('schema', 'prior_operation', 'profile_sha256', 'maximum_outer_blob_bytes', 'catalog_sha256', 'allocation')}
    values['blobs'] = blobs
    # ChainIDs are deterministically recomputed from parent+diff, not transmitted twice.
    values['chains'] = [[chain_index.get(plan['chains'][name]['parent'], -1), blob_index[plan['chains'][name]['blob_digest']],
                        plan['chains'][name]['diff_id'], plan['chains'][name]['uncompressed_bytes_upper_bound']] for name in names]
    values['images'] = []
    for row in plan['images']:
        item = {key: row[key] for key in ('role', 'basename', 'archive_sha256', 'archive_bytes', 'manifest_digest', 'config_digest', 'expanded_groups')}
        item['blob_indices'] = [blob_index[dig] for dig in sorted(row['outer_blobs'])]
        item['chain_indices'] = [chain_index[layer['chain_id']] for layer in row['layers']]
        item['layer_blob_indices'] = [blob_index[layer['blob_digest']] for layer in row['layers']]
        item['layer_upper_bounds'] = [layer['uncompressed_bytes_upper_bound'] for layer in row['layers']]
        values['images'].append(item)
    return values


def unwire_plan(values):
    table = values['chains']
    require(0 < len(table) <= 2048 and 0 < len(values['blobs']) <= 2048 and len(values['images']) == 6, 'wire_size')
    plan = {key: values[key] for key in ('schema', 'prior_operation', 'profile_sha256', 'maximum_outer_blob_bytes', 'catalog_sha256', 'allocation')}
    plan['blobs'] = dict(values['blobs'])
    require(len(plan['blobs']) == len(values['blobs']), 'wire_duplicate_blob')
    plan['chains'] = {}
    names={};pending=set(range(len(table)))
    for parent,blob_index,diff,bound in table:
        B.digest(diff)
        require(type(parent) is int and -1 <= parent < len(table) and type(blob_index) is int and 0 <= blob_index < len(values['blobs']) and type(bound) is int and bound >= 0, 'wire_entry')
    while pending:
        progress=False
        for index in list(pending):
            parent,blob_index,diff,bound=table[index]
            if parent!=-1 and parent not in names:continue
            names[index]=diff if parent==-1 else 'sha256:'+sha((names[parent]+' '+diff).encode())
            pending.remove(index);progress=True
        require(progress,'wire_parent_cycle')
    for index,(parent,blob_index,diff,bound) in enumerate(table):
        name=names[index]
        dig, size = values['blobs'][blob_index]
        require(name not in plan['chains'], 'wire_duplicate_chain')
        plan['chains'][name] = dict(chain_id=name,parent='' if parent == -1 else names[parent],blob_digest=dig,
                                   compressed_bytes=size,diff_id=diff,uncompressed_bytes_upper_bound=bound)
    plan['images'] = []
    for row in values['images']:
        item = {key: row[key] for key in ('role', 'basename', 'archive_sha256', 'archive_bytes', 'manifest_digest', 'config_digest', 'expanded_groups')}
        require(all(type(i) is int and 0 <= i < len(values['blobs']) for i in row['blob_indices']), 'wire_blob_index')
        require(all(type(i) is int and 0 <= i < len(table) for i in row['chain_indices']), 'wire_chain_index')
        require(len(row['layer_blob_indices']) == len(row['layer_upper_bounds']) == len(row['chain_indices'])
                and all(type(i) is int and 0 <= i < len(values['blobs']) for i in row['layer_blob_indices']), 'wire_layer_blob_index')
        item['outer_blobs'] = dict(values['blobs'][index] for index in row['blob_indices'])
        item['layers'] = []
        for index,blob_index,bound in zip(row['chain_indices'],row['layer_blob_indices'],row['layer_upper_bounds']):
            require(type(bound) is int and bound >= 0, 'wire_layer_bound')
            layer = dict(plan['chains'][names[index]])
            layer.update(blob_digest=values['blobs'][blob_index][0],compressed_bytes=values['blobs'][blob_index][1],
                         uncompressed_bytes_upper_bound=bound)
            item['layers'].append(layer)
        plan['images'].append(item)
    return plan


def validate_plan(profile, plan):
    require(plan['prior_operation'] == OP and plan['profile_sha256'] == PROFILE_SHA and len(plan['images']) == 6, 'plan_binding')
    expected = {row['role']: row for row in profile['images']}
    require(set(expected) == {row['role'] for row in plan['images']}, 'plan_roles')
    all_blobs, all_chains = {}, {}
    for image in plan['images']:
        row = expected[image['role']]
        for key in ('basename', 'archive_sha256', 'archive_bytes', 'manifest_digest', 'config_digest'):
            require(image[key] == row[key], 'plan_image_identity')
        parent = ''
        for layer in image['layers']:
            for key in ('blob_digest', 'diff_id', 'chain_id'):
                B.digest(layer[key])
            chain = layer['diff_id'] if not parent else 'sha256:' + sha((parent + ' ' + layer['diff_id']).encode())
            require(layer['parent'] == parent and layer['chain_id'] == chain and image['outer_blobs'][layer['blob_digest']] == layer['compressed_bytes'], 'plan_chain_identity')
            projection = (layer['parent'],layer['diff_id'])
            if chain in all_chains:
                require(all_chains[chain]['identity'] == projection,'plan_chain_conflict')
                all_chains[chain]['upper'] = max(all_chains[chain]['upper'],layer['uncompressed_bytes_upper_bound'])
                all_chains[chain]['blob_variants'].add((layer['blob_digest'],layer['compressed_bytes']))
            else:
                all_chains[chain] = dict(identity=projection,upper=layer['uncompressed_bytes_upper_bound'],
                                         blob_variants={(layer['blob_digest'],layer['compressed_bytes'])})
            parent = chain
        for dig, size in image['outer_blobs'].items():
            B.digest(dig)
            require(type(size) is int and 0 < size <= row['archive_bytes'] and (dig not in all_blobs or all_blobs[dig] == size), 'plan_blob_size')
            all_blobs[dig] = size
    require(all_blobs == plan['blobs'] and set(all_chains) == set(plan['chains']), 'plan_global_tables')
    for name,expected in all_chains.items():
        actual=plan['chains'][name]
        require(actual['chain_id']==name and (actual['parent'],actual['diff_id'])==expected['identity']
                and actual['uncompressed_bytes_upper_bound']==expected['upper']
                and (actual['blob_digest'],actual['compressed_bytes']) in expected['blob_variants'],'plan_global_chain_binding')


def storage_check(profile, plan, phase, sealed_fd=None):
    try:
        value = A.check(B, profile, plan, phase, sealed_fd=sealed_fd)
    except Exception as exc:
        if hasattr(exc,'observation'):
            REPORT.setdefault('storage_checks',[]).append(dict(phase=phase,observation=exc.observation,check_failed=True))
        raise
    if phase != 'MONITOR':
        REPORT.setdefault('storage_checks', []).append(dict(phase=phase, observation=value))
    return value


def monitor(profile, plan, sealed_fd):
    return lambda: storage_check(profile, plan, 'MONITOR', sealed_fd)


def fd_regular(fd, size=None, private=True):
    value = os.fstat(fd)
    require(stat.S_ISREG(value.st_mode) and value.st_nlink == 1 and (size is None or value.st_size == size), 'fd_regular_required')
    if private:
        require(value.st_uid == 0 and stat.S_IMODE(value.st_mode) == 0o600, 'fd_private_required')
    return value


def make_sealed_directory(name):
    """Root may anchor a new child even though /opt's owner can rename it."""
    require(re.fullmatch('portal-ppt-native-r3-sealed-[a-f0-9]{32}', name), 'sealed_name')
    root_fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    opt_fd = None
    directory_fd = None
    try:
        opt_fd = os.open('opt', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
        os.mkdir(name, 0o700, dir_fd=opt_fd)
        directory_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=opt_fd)
        value = os.fstat(directory_fd)
        entry = os.stat(name, dir_fd=opt_fd, follow_symlinks=False)
        require(stat.S_ISDIR(value.st_mode) and value.st_uid == 0 and stat.S_IMODE(value.st_mode) == 0o700
                and (value.st_dev,value.st_ino) == (entry.st_dev,entry.st_ino) and os.listdir(directory_fd) == [], 'sealed_directory_identity')
        # Subsequent operations retain this FD; no later path lookup of this name.
        result = directory_fd
        directory_fd = None
        return result, dict(device=value.st_dev,inode=value.st_ino,uid=value.st_uid,mode='0o700',
                            display_path='/opt/' + name,path_is_not_reopened=True)
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        if opt_fd is not None:
            os.close(opt_fd)
        os.close(root_fd)


def seal_fd(upload_fd, sealed_fd, row, watcher=None):
    name = row['basename']
    require(re.fullmatch('[A-Za-z0-9_.-]+', name), 'archive_basename')
    source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=upload_fd)
    target = None
    try:
        before = fd_regular(source,row['archive_bytes'],private=False)
        target = os.open(name,os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,0o600,dir_fd=sealed_fd)
        created = fd_regular(target,0)
        count, digest, started, watched = 0,hashlib.sha256(),time.monotonic(),time.monotonic()
        with os.fdopen(source,'rb',closefd=False) as src, os.fdopen(target,'wb',closefd=False) as dst:
            while True:
                B.deadline()
                block = src.read(4 * 1024 ** 2)
                if not block:
                    break
                count += len(block)
                require(count <= row['archive_bytes'] and time.monotonic()-started < 1800,'seal_limit')
                digest.update(block)
                dst.write(block)
                if time.monotonic()-watched > 10:
                    if watcher:
                        watcher()
                    B.progress('SEAL_TARGET_FD',role=row['role'],bytes=count,total_bytes=row['archive_bytes'])
                    watched=time.monotonic()
            dst.flush()
            os.fsync(dst.fileno())
        require(count == row['archive_bytes'] and digest.hexdigest() == row['archive_sha256'] and B.identity(before) == B.identity(os.fstat(source)), 'sealed_archive_identity')
        os.close(target)
        target = None
        reader = os.open(name,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,dir_fd=sealed_fd)
        try:
            final = fd_regular(reader,row['archive_bytes'])
            require((final.st_dev,final.st_ino) == (created.st_dev,created.st_ino),'sealed_inode_replaced')
            return reader
        except BaseException:
            os.close(reader)
            raise
    finally:
        os.close(source)
        if target is not None:
            os.close(target)


def archive_parts(fd,row,image):
    before = fd_regular(fd,row['archive_bytes'])
    wanted={'oci-layout','index.json','blobs/sha256/'+row['manifest_digest'][7:],'blobs/sha256/'+row['config_digest'][7:]}
    parts,blobs,seen={},{},set()
    os.lseek(fd,0,os.SEEK_SET)
    with os.fdopen(os.dup(fd),'rb') as stream:
        with tarfile.open(fileobj=stream,mode='r:') as archive:
            for number,member in enumerate(archive):
                B.deadline()
                require(number<4096 and member.name not in seen and not member.linkname and not member.pax_headers,'sealed_header_shape')
                seen.add(member.name)
                if member.isdir():
                    require(member.name.rstrip('/') in ('.','blobs','blobs/sha256') and member.size==0,'sealed_directory_header')
                    continue
                require(member.isfile(),'sealed_header_shape')
                if member.name.startswith('blobs/sha256/'):
                    dig='sha256:'+member.name.rsplit('/',1)[1]
                    B.digest(dig)
                    blobs[dig]=member.size
                else:
                    require(member.name in ('oci-layout','index.json','manifest.json'),'sealed_unknown_entry')
                if member.name in wanted:
                    require(member.size<=B.META_LIMIT,'metadata_limit')
                    parts[member.name]=archive.extractfile(member).read(B.META_LIMIT+1)
    require(B.identity(before)==B.identity(os.fstat(fd)) and set(parts)==wanted and blobs==image['outer_blobs'],'sealed_plan_header_binding')
    require(B.parse(parts['oci-layout'])=={'imageLayoutVersion':'1.0.0'},'oci_layout_version')
    index=B.parse(parts['index.json'])
    require(index.get('schemaVersion')==2 and len(index.get('manifests',[]))==1,'index_shape')
    descriptor=index['manifests'][0]
    expected_short=row['tag'] if row['role']=='ppt-master-agent-host-forward' else row['tag'].rsplit(':',1)[1]
    annotations=descriptor.get('annotations',{})
    require(descriptor['digest']==row['manifest_digest'] and annotations.get('io.containerd.image.name')==row['tag']
            and annotations.get('org.opencontainers.image.ref.name')==expected_short,'index_reference_binding')
    for dig in (row['manifest_digest'],row['config_digest']):
        require('sha256:'+sha(parts['blobs/sha256/'+dig[7:]])==dig,'metadata_digest_binding')
    manifest=B.parse(parts['blobs/sha256/'+row['manifest_digest'][7:]])
    config=B.parse(parts['blobs/sha256/'+row['config_digest'][7:]])
    require(manifest['config']['digest']==row['config_digest'] and config.get('architecture')=='arm64' and config.get('os')=='linux','metadata_platform_binding')
    require(len(manifest['layers'])==len(config['rootfs']['diff_ids'])==len(image['layers']),'metadata_layer_count')
    for desc,diff,layer in zip(manifest['layers'],config['rootfs']['diff_ids'],image['layers']):
        require(desc['digest']==layer['blob_digest'] and desc['size']==layer['compressed_bytes'] and diff==layer['diff_id'],'metadata_layer_binding')
    return parts


def metadata_tar_fd(sealed_fd,row,parts):
    name=row['basename']+'.metadata.tar'
    target=os.open(name,os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,0o600,dir_fd=sealed_fd)
    try:
        created=fd_regular(target,0)
        with os.fdopen(target,'wb',closefd=False) as stream:
            with tarfile.open(fileobj=stream,mode='w',format=tarfile.USTAR_FORMAT) as archive:
                for filename in sorted(parts):
                    member=tarfile.TarInfo(filename)
                    member.size=len(parts[filename]);member.mode=0o600
                    archive.addfile(member,io.BytesIO(parts[filename]))
            stream.flush();os.fsync(stream.fileno())
    finally:
        os.close(target)
    reader=os.open(name,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,dir_fd=sealed_fd)
    try:
        final=fd_regular(reader)
        require((created.st_dev,created.st_ino)==(final.st_dev,final.st_ino) and final.st_size<=4*B.META_LIMIT+65536,'metadata_tar_inode_or_size')
        return reader
    except BaseException:
        os.close(reader)
        raise


def run_fd(argv,fd,phase,watcher,timeout=2400,limit=2*1024**2):
    """Only ctr inherits the already verified read-only archive FD."""
    require(fcntl.fcntl(fd,fcntl.F_GETFL)&os.O_ACCMODE==os.O_RDONLY,'import_fd_not_readonly')
    require(argv[-1]=='/proc/self/fd/'+str(fd),'import_fd_argv')
    fd_regular(fd)
    os.lseek(fd,0,os.SEEK_SET)
    B.progress(phase)
    proc=None;threads=[];counts=[0,0];failed=threading.Event();exceeded=threading.Event()
    def drain(stream,which):
        try:
            while True:
                block=os.read(stream.fileno(),65536)
                if not block:break
                counts[which]+=len(block)
                if counts[which]>limit:exceeded.set()
        except Exception:failed.set()
    started=last=time.monotonic()
    try:
        proc=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              env=B.SAFE_ENV,start_new_session=True,pass_fds=(fd,))
        for which,stream in enumerate((proc.stdout,proc.stderr)):
            thread=threading.Thread(target=drain,args=(stream,which),daemon=True);threads.append(thread);thread.start()
        while proc.poll() is None:
            B.deadline()
            require(time.monotonic()-started<timeout,'import_timeout')
            require(not failed.is_set() and not exceeded.is_set(),'import_output_limit')
            if time.monotonic()-last>10:
                watcher();B.progress(phase,elapsed_seconds=int(time.monotonic()-started));last=time.monotonic()
            time.sleep(.1)
        for thread in threads:thread.join(timeout=3)
        require(all(not thread.is_alive() for thread in threads) and not failed.is_set() and not exceeded.is_set(),'import_pipe_failure')
        require(proc.returncode==0,'import_nonzero')
        return dict(returncode=0,stdout_bytes=counts[0],stderr_bytes=counts[1],seconds=round(time.monotonic()-started,3))
    finally:
        try:B.stop(proc,True)
        finally:
            if proc:
                for stream in (proc.stdout,proc.stderr):
                    if stream:stream.close()


def import_argv(row,fd,no_unpack):
    argv=['nice','-n','10','ionice','-c','2','-n','7']+B.CTR+['images','import','--local','--platform','linux/arm64',
          '--snapshotter','overlayfs','--digests','--base-name',row['repository'],'--label','io.cri-containerd.image=managed']
    if no_unpack:argv.append('--no-unpack')
    return argv+['/proc/self/fd/'+str(fd)]


def all_content_present(plan):
    wanted=sorted(plan['blobs']);found=set()
    for offset in range(0,len(wanted),128):
        batch=wanted[offset:offset+128]
        raw=B.capture(B.CTR+['content','ls','--quiet']+['digest=='+dig for dig in batch],'PHASE_A_CONTENT_CHECK',limit=65536)
        items=raw.decode('ascii').split()
        require(len(items)==len(set(items)) and set(items)<=set(batch),'content_filter_output')
        found.update(items)
    require(found==set(wanted),'phase_a_content_incomplete')


def verify_references(profile):
    for row in profile['images']:
        for ref in B.refs(row):require(B.reference(ref)==row['manifest_digest'],'candidate_reference_not_exact')
        B.manifest_config(row['manifest_digest'],row['config_digest'])


def request_import(phase,row,fd,profile,plan,sealed_fd):
    global IMPORT_REQUESTED
    write_receipt(row['role']+'.'+phase+'-REQUESTED.json',dict(role=row['role'],phase=phase,automatic_retry=False))
    IMPORT_REQUESTED=True;REPORT['target_import_requested']=True;REPORT['image_import_may_be_partial']=True
    receipt=run_fd(import_argv(row,fd,phase=='PHASE-A'),fd,phase+'_'+row['role'],monitor(profile,plan,sealed_fd))
    write_receipt(row['role']+'.'+phase+'-PASS.json',receipt)


def prepare_target(profile,plan,token):
    global STATE,REPORT
    prior_binding(True);claim_once(token)
    paths=state_paths(token);STATE=B.fresh(paths['target']);REPORT=base_report(token)
    write_receipt('STARTED.json',REPORT)
    storage_check(profile,plan,'TARGET_PREPARE');B.collision_check(profile)
    B.run(['runuser','-u','cnooc','--','mkdir','-m','700',str(paths['upload'])],'CREATE_USER_UPLOAD')
    import pwd
    owner=pwd.getpwnam('cnooc');value=paths['upload'].lstat()
    require(owner.pw_uid>0 and stat.S_ISDIR(value.st_mode) and value.st_uid==owner.pw_uid and stat.S_IMODE(value.st_mode)==0o700,'upload_directory_identity')
    REPORT.update(status='PREPARED_NO_IMPORT',upload=str(paths['upload']),upload_device=value.st_dev,upload_inode=value.st_ino,upload_uid=owner.pw_uid)
    write_receipt('PREPARED.json',REPORT)
    return REPORT


def import_target(profile,plan,token):
    global STATE,REPORT
    paths=state_paths(token);STATE=paths['target'];B.safe_dir(STATE)
    prepared=B.parse(private_bytes(STATE/'PREPARED.json'))
    require(prepared['operation']==token and prepared['status']=='PREPARED_NO_IMPORT','prepared_binding')
    REPORT=base_report(token);write_receipt('RUN-REQUESTED.json',REPORT)
    prior_binding(True);storage_check(profile,plan,'BEFORE_SEAL');B.collision_check(profile)
    upload_fd=B.open_anchored(paths['upload'])
    sealed_fd=None;full_fds={};meta_fds={}
    try:
        value=os.fstat(upload_fd)
        require(stat.S_ISDIR(value.st_mode) and (value.st_dev,value.st_ino,value.st_uid)==(prepared['upload_device'],prepared['upload_inode'],prepared['upload_uid']),'upload_directory_replaced')
        sealed_fd,identity=make_sealed_directory(paths['sealed_name']);REPORT['sealed_directory']=identity
        images={row['role']:row for row in plan['images']}
        for row in profile['images']:
            full_fds[row['role']]=seal_fd(upload_fd,sealed_fd,row,monitor(profile,plan,sealed_fd))
            parts=archive_parts(full_fds[row['role']],row,images[row['role']])
            meta_fds[row['role']]=metadata_tar_fd(sealed_fd,row,parts)
            require(os.fstat(meta_fds[row['role']]).st_size<=plan['allocation']['maximum_metadata_import_bytes'],'metadata_tar_exceeds_allocation_bound')
            del parts
            write_receipt(row['role']+'.SEALED.json',dict(role=row['role'],archive_sha256=row['archive_sha256'],archive_bytes=row['archive_bytes'],metadata_only_bytes=os.fstat(meta_fds[row['role']]).st_size))
        storage_check(profile,plan,'BEFORE_PHASE_A',sealed_fd);B.collision_check(profile)
        write_receipt('PHASE-A-REQUESTED.json',dict(mode='FULL_OCI_CONTENT_ONLY',automatic_retry=False));REPORT['phase_a']='REQUESTED'
        for row in profile['images']:
            B.collision_check(profile)
            request_import('PHASE-A',row,full_fds[row['role']],profile,plan,sealed_fd)
        all_content_present(plan);verify_references(profile)
        write_receipt('PHASE-A-PASS.json',dict(all_fixed_outer_blobs_present=True,all_refs_and_config_bound=True));REPORT['phase_a']='PASS'
        storage_check(profile,plan,'BEFORE_PHASE_B',sealed_fd)
        write_receipt('PHASE-B-REQUESTED.json',dict(mode='ORIGINAL_METADATA_ONLY_OCI_UNPACK_FROM_STORE',automatic_retry=False));REPORT['phase_b']='REQUESTED'
        for row in profile['images']:
            # The previous cache observation is not a reservation. Recheck the
            # content closure and remaining allocation before each unpack.
            all_content_present(plan)
            storage_check(profile,plan,'BEFORE_PHASE_B',sealed_fd)
            B.collision_check(profile)
            request_import('PHASE-B',row,meta_fds[row['role']],profile,plan,sealed_fd)
            receipt,config=B.complete_row(row);REPORT['image_rows'].append(receipt)
            if row['role']=='ppt-master-agent-host-forward':
                try:REPORT['host_runtime_inputs']=B.host_projection(config,row['config_digest'])
                except B.Failure as exc:REPORT['host_runtime_inputs']=dict(state='NOT_COLLECTED',error_code=exc.code)
            del config
        for row in profile['images']:B.complete_row(row)
        write_receipt('PHASE-B-PASS.json',dict(all_six_complete=True));REPORT['phase_b']='PASS'
        try:REPORT['postgres_cache']=B.postgres_cache(profile)
        except Exception as exc:REPORT['postgres_cache']=dict(state='NOT_COLLECTED',error_code=exc.code if isinstance(exc,B.Failure) else 'METADATA_UNAVAILABLE',scope='LOCAL_NODE_CACHE_ONLY_NOT_CLUSTER_ABSENCE')
        REPORT.update(status='COMPLETE_PRELOAD',image_import_may_be_partial=False)
        write_receipt('REPORT.json',REPORT)
        return REPORT
    finally:
        for fd in list(full_fds.values())+list(meta_fds.values()):os.close(fd)
        if sealed_fd is not None:os.close(sealed_fd)
        os.close(upload_fd)


def remote_result(raw,token):
    values=re.findall(rb'PORTAL_NATIVE_R3_RESULT=([A-Za-z0-9+/=]+)',raw)
    require(len(values)==1,'remote_result_count')
    result=B.parse(base64.b64decode(values[0],validate=True))
    require(result.get('schema')=='portal-ppt-native-preload-r3.v1' and result.get('operation')==token and result.get('target')=='datax-02','remote_result_binding')
    return result


def bootstrap_command(code,plan,token):
    paths=state_paths(token)
    # Target executes only check(); evidence expansion belongs to the source.
    # Remove only the fixed source-side table from the embedded module, retaining
    # the exact runtime functions and the fully expanded, authenticated wire plan.
    allocation=zlib.decompress(base64.b64decode(ALLOCATION_Z64,validate=True))
    require(sha(allocation)==ALLOCATION_SHA,'source_allocation_identity')
    allocation,count=re.subn(rb'^BINDING = json.loads\([^\n]*\)\r?$',b'BINDING = {}',allocation,flags=re.MULTILINE)
    require(count==1,'remote_allocation_table_shape')
    remote_sha=sha(allocation);remote_z64=base64.b64encode(zlib.compress(allocation,9)).decode()
    if REPORT is not None:REPORT['remote_allocation_module_sha256']=remote_sha
    remote_code=code.decode('utf-8')
    for key,old,new in (('ALLOCATION_SHA',ALLOCATION_SHA,remote_sha),('ALLOCATION_Z64',ALLOCATION_Z64,remote_z64)):
        target=key+' = '+repr(old)
        require(remote_code.count(target)==1,'remote_allocation_constant_shape')
        remote_code=remote_code.replace(target,key+' = '+repr(new))
    payload=pack(dict(code=remote_code,plan=wire_plan(plan),token=token))
    source="import base64,zlib,json,os,stat,socket,hashlib\nassert os.geteuid()==0 and socket.gethostname().split('.')[0]=='datax-02'\n"
    source+='p=json.loads(zlib.decompress(base64.b64decode('+repr(payload)+')))\n'
    source+="s=os.lstat('/var/lib'); assert stat.S_ISDIR(s.st_mode) and s.st_uid==0 and not s.st_mode&0o022\n"
    source+='d='+repr(str(paths['control']))+'\nos.mkdir(d,0o700)\n'
    source+="files={'preload_native_r3.py':p['code'].encode(),'PLAN-WIRE.json':json.dumps(p['plan'],separators=(',',':')).encode()}\n"
    source+="for n,b in files.items():\n fd=os.open(d+'/'+n,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)\n with os.fdopen(fd,'wb') as f:f.write(b);f.flush();os.fsync(f.fileno())\n"
    source+="os.execvp('python3',['python3','-I','-B','-u',d+'/preload_native_r3.py','--prepare-target','--token',p['token']])\n"
    command='sudo -p '+shlex.quote('[portal sudo password] ')+' python3 -I -B -u -c '+shlex.quote(source)
    require(len(command.encode())<110*1024,'bootstrap_argv_limit')
    return command


def source_distribute(profile):
    global STATE,REPORT,IMPORT_REQUESTED
    prior_binding()
    plan_raw=private_bytes(CACHE_SOURCE/'PLAN.json');report_raw=private_bytes(CACHE_SOURCE/'REPORT.json')
    require(sha(plan_raw)==PLAN_SHA and sha(report_raw)==CACHE_REPORT_SHA,'accepted_cache_evidence_changed')
    plan,accepted=B.parse(plan_raw),B.parse(report_raw)
    validate_plan(profile,plan)
    plan=A.prepare_plan(plan,accepted)
    validate_plan(profile,plan)
    token=uuid.uuid4().hex;claim_once(token);paths=state_paths(token)
    STATE=B.fresh(paths['source']);REPORT=base_report(token);write_receipt('STARTED.json',REPORT)
    REPORT['node_before']=B.node_guard(profile)
    B.safe_dir(SOURCE_SEALED)
    source_observations=[]
    for row in profile['images']:
        fd=B.open_anchored(SOURCE_SEALED/row['basename'])
        try:
            value=fd_regular(fd,row['archive_bytes'])
            source_observations.append(dict(role=row['role'],device=value.st_dev,inode=value.st_ino,bytes=value.st_size,archive_sha256=row['archive_sha256'],sha_basis='PREVIOUS_EXACT_SOURCE_SEAL_NOT_REHASHED'))
        finally:os.close(fd)
    control=B.disk('/var/lib');source=B.disk('/opt')
    require(control['available_bytes']>=4*1024**3+32*1024**2 and control['available_inodes']>=1024,'source_control_space')
    require(source['available_bytes']>=B.reserve(source)+2*1024**3 and source['available_inodes']>=100000,'source_existing_seal_reserve')
    REPORT['source_reused_seals']=source_observations
    write_receipt('AUGMENTED-PLAN.json',plan)
    known=STATE/'known_hosts'
    B.write_new(known,B.trusted_key(private_bytes(SOURCE/'known_hosts',65536),profile['target']))
    remote='cnooc@'+profile['target']['ip'];options=B.ssh_options(known)
    command=bootstrap_command(CODE_SNAPSHOT,plan,token)
    rc,raw=B.run(['ssh']+options+['-tt',remote,command],'R3_REMOTE_PREPARE',timeout=600,interactive=True,allow_failure=True,limit=2*1024**2)
    result=remote_result(raw,token);write_receipt('TARGET-PREPARE.json',result)
    REPORT['target_report']=result
    require(rc==0 and result.get('status')=='PREPARED_NO_IMPORT','target_prepare_failed')
    write_receipt('TRANSFER-REQUESTED.json',dict(archive_count=6,total_bytes=profile['archive_total_bytes'],automatic_retry=False))
    B.run(['scp']+options+[str(SOURCE_SEALED/row['basename']) for row in profile['images']]+[remote+':'+str(paths['upload'])+'/'],
          'TRANSFER_EXISTING_SIX_SEALS_ONCE',timeout=5400,interactive=True,limit=2*1024**2)
    write_receipt('TRANSFER-PASS.json',dict(archive_count=6))
    B.node_guard(profile)
    command='sudo -p '+shlex.quote('[portal sudo password] ')+' python3 -I -B -u '+str(paths['control']/'preload_native_r3.py')+' --run-target --token '+token
    IMPORT_REQUESTED=True;REPORT['target_import_requested']=True
    rc,raw=B.run(['ssh']+options+['-tt',remote,command],'R3_REMOTE_TWO_PHASE_IMPORT',timeout=17400,interactive=True,allow_failure=True,limit=2*1024**2)
    result=remote_result(raw,token);write_receipt('TARGET-REPORT.json',result)
    IMPORT_REQUESTED=result.get('target_import_requested') is not False
    REPORT.update(target_import_requested=IMPORT_REQUESTED,target_report=result)
    require(rc==0 and result.get('status')=='COMPLETE_PRELOAD' and len(result.get('image_rows',[]))==6,'two_phase_import_incomplete')
    REPORT.update(status='COMPLETE_PRELOAD',image_rows=result['image_rows'],host_runtime_inputs=result.get('host_runtime_inputs'),postgres_cache=result.get('postgres_cache'),
                  phase_a=result['phase_a'],phase_b=result['phase_b'],node_after=B.node_guard(profile),image_import_may_be_partial=False)
    write_receipt('REPORT.json',REPORT)
    return REPORT


def main():
    global CODE_SNAPSHOT
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group(required=True)
    for mode in ('distribute','prepare-target','run-target'):modes.add_argument('--'+mode,action='store_true')
    parser.add_argument('--token');args=parser.parse_args()
    require(sys.platform.startswith('linux') and os.geteuid()==0,'linux_root_required')
    require(socket.gethostname().split('.')[0]==('datax-01' if args.distribute else 'datax-02'),'host_scope')
    os.umask(0o077)
    profile=load_modules(not args.distribute)
    CODE_SNAPSHOT=B.read_file(Path(__file__).absolute(),256*1024)
    def interrupted(signum,frame):raise ValueError('interrupted_no_retry')
    for sig in (signal.SIGTERM,signal.SIGHUP):signal.signal(sig,interrupted)
    lock=common_lock()
    try:
        if args.distribute:
            require(args.token is None,'source_token_forbidden')
            return source_distribute(profile)
        state_paths(args.token)
        plan=unwire_plan(B.parse(private_bytes(Path(__file__).parent/'PLAN-WIRE.json')))
        validate_plan(profile,plan)
        return prepare_target(profile,plan,args.token) if args.prepare_target else import_target(profile,plan,args.token)
    finally:os.close(lock)


if __name__=='__main__':
    remote='--prepare-target' in sys.argv or '--run-target' in sys.argv
    try:result=main();rc=0
    except (Exception,KeyboardInterrupt) as exc:
        code=exc.code if B and isinstance(exc,B.Failure) else str(exc) if isinstance(exc,ValueError) else 'OPERATION_FAILED'
        if not re.fullmatch('[A-Za-z0-9_]{1,120}',code):code='OPERATION_FAILED'
        result=REPORT or base_report(None)
        result.update(status='INCOMPLETE',error_code=code,target_import_requested=IMPORT_REQUESTED,image_import_may_be_partial=IMPORT_REQUESTED,automatic_retry=False)
        if STATE is not None:
            try:write_receipt('FAIL.json',result)
            except Exception:pass
        rc=1
    raw=(json.dumps(result,sort_keys=True,indent=2,ensure_ascii=True)+'\n').encode()
    if remote:print(MARKER+base64.b64encode(raw).decode(),flush=True)
    else:
        summary=dict(status=result['status'],report_sha256=sha(raw),report_bytes=len(raw),exitcode=rc,
                     operation=result.get('operation'),target_import_requested=result.get('target_import_requested'),
                     image_import_may_be_partial=result.get('image_import_may_be_partial'),automatic_retry=False)
        try:summary['download_report']=handoff_report(raw)
        except Exception:
            summary['download_report_status']='HANDOFF_UNAVAILABLE_PRIVATE_EVIDENCE_RETAINED'
            sys.stdout.buffer.write(raw);sys.stdout.buffer.flush()
        if result.get('error_code'):summary['error_code']=result['error_code']
        print(json.dumps(summary,sort_keys=True),flush=True)
    raise SystemExit(rc)

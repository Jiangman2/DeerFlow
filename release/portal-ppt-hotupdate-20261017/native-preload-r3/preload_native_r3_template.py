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
ALLOCATION_SHA = '@@ALLOCATION_MODULE_SHA256@@'
ALLOCATION_Z64 = '@@ALLOCATION_MODULE_Z64@@'
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

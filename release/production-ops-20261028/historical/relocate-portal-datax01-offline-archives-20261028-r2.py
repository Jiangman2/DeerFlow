#!/usr/bin/env python3
"""Move verified closed offline archives off root; preserve original paths as symlinks.

Python 3.7+, datax-01 only. No runtime images, logs, database or service mutations.
The destination is retained permanently as recovery material, not a temp directory.
"""
import argparse
import hashlib
import json
import os
from pathlib import PurePosixPath
import re
import shutil
import signal
import socket
import stat
import subprocess
import time

PLAN = [{'path': '/home/cnooc/portal-ppt-native-preload-20261017-r1/runner.candidate.oci.tar', 'device': 64512, 'inode': 549813491, 'bytes': 976393728, 'mtime_ns': 1792232915769597305, 'uid': 1000}, {'path': '/home/cnooc/portal-ppt-native-preload-20261017-r1/runner-rollback-compatible.candidate.oci.tar', 'device': 64512, 'inode': 549813492, 'bytes': 976332288, 'mtime_ns': 1792232920469608212, 'uid': 1000}, {'path': '/home/cnooc/ppt-retained-gateway-58a50175-20261020/gateway.oci.tar', 'device': 64512, 'inode': 245062689, 'bytes': 870492672, 'mtime_ns': 1792499086803695043, 'uid': 1000}, {'path': '/home/cnooc/portal-ppt-native-preload-20261017-r1/gateway.candidate.oci.tar', 'device': 64512, 'inode': 549813489, 'bytes': 870471168, 'mtime_ns': 1792232904789571826, 'uid': 1000}, {'path': '/home/cnooc/portal-ppt-native-preload-20261017-r1/gateway-rollback-compatible.candidate.oci.tar', 'device': 64512, 'inode': 549813490, 'bytes': 870313472, 'mtime_ns': 1792232909009581619, 'uid': 1000}, {'path': '/home/cnooc/output-recovery-r3-05900f6102e0/gateway.oci.tar', 'device': 64512, 'inode': 380524596, 'bytes': 869703680, 'mtime_ns': 1791982345159249840, 'uid': 1000}, {'path': '/home/cnooc/weknora-glm-offline-build-20261016-r1.tar.gz', 'device': 64512, 'inode': 42703330, 'bytes': 773435535, 'mtime_ns': 1792142857699122254, 'uid': 1000}, {'path': '/home/cnooc/deer-flow-skills-20260901-enterprise-arm64-rc15-researchfund1.oci.tar', 'device': 64512, 'inode': 33613910, 'bytes': 207321600, 'mtime_ns': 1788255732927498067, 'uid': 1000}, {'path': '/home/cnooc/chromium-linux-arm64-1223.zip', 'device': 64512, 'inode': 33611276, 'bytes': 194706293, 'mtime_ns': 1792542943766729755, 'uid': 1000}, {'path': '/home/cnooc/browser-r7-package-24C5PVGO/chromium-linux-arm64-1223.zip', 'device': 64512, 'inode': 345623790, 'bytes': 194706293, 'mtime_ns': 1792548097108864732, 'uid': 1000}, {'path': '/home/cnooc/browser-cdp-r6-r3-kp9r8Du8/chromium-linux-arm64-1223.zip', 'device': 64512, 'inode': 239155669, 'bytes': 194706293, 'mtime_ns': 1792545983743899090, 'uid': 1000}, {'path': '/home/cnooc/browser-cdp-r6-r2-PewxHb0a/chromium-linux-arm64-1223.zip', 'device': 64512, 'inode': 149246810, 'bytes': 194706293, 'mtime_ns': 1792545546402862141, 'uid': 1000}, {'path': '/home/cnooc/browser-cdp-r6-MIS2ZLvK/chromium-linux-arm64-1223.zip', 'device': 64512, 'inode': 5688303, 'bytes': 194706293, 'mtime_ns': 1792543760628642335, 'uid': 1000}, {'path': '/home/cnooc/browser-version-r5-r2-Pv4Uu8Fm/chromium-linux-arm64-1223.zip', 'device': 64512, 'inode': 774897315, 'bytes': 194706293, 'mtime_ns': 1792543166567251402, 'uid': 1000}, {'path': '/home/cnooc/browser-version-r5-8JWzIB78/chromium-linux-arm64-1223.zip', 'device': 64512, 'inode': 674218931, 'bytes': 194706293, 'mtime_ns': 1792542947156737692, 'uid': 1000}, {'path': '/home/cnooc/deerflow-conversation-file-parsing-20260921-r7.tar.gz', 'device': 64512, 'inode': 35797511, 'bytes': 126333130, 'mtime_ns': 1789959944256485137, 'uid': 1000}, {'path': '/home/cnooc/deerflow-conversation-file-parsing-20260921-r8.tar.gz', 'device': 64512, 'inode': 35012516, 'bytes': 126333530, 'mtime_ns': 1789963428274769625, 'uid': 1000}, {'path': '/home/cnooc/deerflow-conversation-file-parsing-20260921-r10.tar.gz', 'device': 64512, 'inode': 33617315, 'bytes': 126292068, 'mtime_ns': 1789986030108453790, 'uid': 1000}, {'path': '/home/cnooc/deerflow-conversation-file-parsing-20260921-r9.tar.gz', 'device': 64512, 'inode': 35797484, 'bytes': 126289069, 'mtime_ns': 1789964370647005104, 'uid': 1000}, {'path': '/home/cnooc/deerflow-conversation-file-parsing-20260921-r11.tar.gz', 'device': 64512, 'inode': 34751421, 'bytes': 126232541, 'mtime_ns': 1789989766957273528, 'uid': 1000}, {'path': '/home/cnooc/ppt-retained-frontend-ee145e63-20261020/frontend.oci.tar', 'device': 64512, 'inode': 208100769, 'bytes': 125340160, 'mtime_ns': 1792498960863397794, 'uid': 1000}, {'path': '/home/cnooc/portal-ppt-native-preload-20261017-r1/frontend.oci.tar', 'device': 64512, 'inode': 549813488, 'bytes': 124756992, 'mtime_ns': 1792232900649562220, 'uid': 1000}, {'path': '/home/cnooc/production-source-supplement-r7-20260928T062128Z.tar.gz', 'device': 64512, 'inode': 40446687, 'bytes': 114910510, 'mtime_ns': 1790576581735148129, 'uid': 1000}, {'path': '/home/cnooc/deerflow-rc15-skillhubsession1-20260908.tar.gz', 'device': 64512, 'inode': 35334807, 'bytes': 111625819, 'mtime_ns': 1788851260417096854, 'uid': 1000}, {'path': '/home/cnooc/deerflow-rc15-roleexpertskillhub1-offline1-20260906.tar.gz', 'device': 64512, 'inode': 35326106, 'bytes': 111618621, 'mtime_ns': 1788692159007924500, 'uid': 1000}, {'path': '/home/cnooc/deerflow-rc15-roleexpertskillhub1-serverfix1-20260906.tar.gz', 'device': 64512, 'inode': 35038749, 'bytes': 111478470, 'mtime_ns': 1788679647618221654, 'uid': 1000}, {'path': '/home/cnooc/deerflow-rc15-skillhubsession2-20260908.tar.gz', 'device': 64512, 'inode': 33617176, 'bytes': 111474023, 'mtime_ns': 1788866193190870615, 'uid': 1000}, {'path': '/home/cnooc/provisioner.same-version.oci.tar', 'device': 64512, 'inode': 41144583, 'bytes': 73379840, 'mtime_ns': 1792317777058512868, 'uid': 1000}, {'path': '/home/cnooc/deerflow-ppt-master-v660-20260922-r6.tar.gz', 'device': 64512, 'inode': 39742905, 'bytes': 67794163, 'mtime_ns': 1790059459070455865, 'uid': 1000}, {'path': '/home/cnooc/deerflow-ppt-master-v660-20260922-r7.tar.gz', 'device': 64512, 'inode': 34798182, 'bytes': 67592371, 'mtime_ns': 1790070183125355917, 'uid': 1000}, {'path': '/home/cnooc/deerflow-ppt-master-v660-20260922-r8.tar.gz', 'device': 64512, 'inode': 34798603, 'bytes': 67591384, 'mtime_ns': 1790071663978800402, 'uid': 1000}, {'path': '/home/cnooc/k8s-bootstrap-images.tar', 'device': 64512, 'inode': 35828123, 'bytes': 65925632, 'mtime_ns': 1789125109118598171, 'uid': 1000}, {'path': '/home/cnooc/deerflow-ppt-master-v660-20260922-r5.tar.gz', 'device': 64512, 'inode': 39729971, 'bytes': 64648067, 'mtime_ns': 1790056839474408330, 'uid': 1000}, {'path': '/home/cnooc/deerflow-ppt-master-v660-20260922-r4.tar.gz', 'device': 64512, 'inode': 39728817, 'bytes': 64646878, 'mtime_ns': 1790055259200743003, 'uid': 1000}, {'path': '/home/cnooc/deerflow-ppt-master-v660-20260922-r3.tar.gz', 'device': 64512, 'inode': 35014858, 'bytes': 64407724, 'mtime_ns': 1790052566674488616, 'uid': 1000}, {'path': '/home/cnooc/production-external-source-r8-20260928T080436Z.tar.gz', 'device': 64512, 'inode': 40446692, 'bytes': 60333309, 'mtime_ns': 1790582689200251043, 'uid': 1000}, {'path': '/home/cnooc/weknora-v08-anydoc-stage1-20260912-r1.tar.gz', 'device': 64512, 'inode': 39577993, 'bytes': 46685042, 'mtime_ns': 1789301431969918084, 'uid': 1000}, {'path': '/home/cnooc/weknora-v08-ui-stage1-20260913-r5.tar(1).gz', 'device': 64512, 'inode': 41103160, 'bytes': 46680081, 'mtime_ns': 1789297260950561003, 'uid': 1000}, {'path': '/home/cnooc/weknora-v08-ui-stage1-20260913-r5.tar.gz', 'device': 64512, 'inode': 40939714, 'bytes': 46680081, 'mtime_ns': 1789297261680562640, 'uid': 1000}, {'path': '/home/cnooc/weknora-v08-image-stage-20260908-r1.tar.gz', 'device': 64512, 'inode': 35331165, 'bytes': 46677504, 'mtime_ns': 1788871642453450461, 'uid': 1000}, {'path': '/home/cnooc/weknora-v08-image-stage-20260908-r2.tar.gz', 'device': 64512, 'inode': 34741628, 'bytes': 46677822, 'mtime_ns': 1788873781198369885, 'uid': 1000}, {'path': '/home/cnooc/weknora-v08-image-stage-20260908-r3.tar.gz', 'device': 64512, 'inode': 40889751, 'bytes': 46677995, 'mtime_ns': 1788916656777102107, 'uid': 1000}, {'path': '/home/cnooc/weknora-v08-ui-stage1-20260913-r1.tar.gz', 'device': 64512, 'inode': 35832771, 'bytes': 46675407, 'mtime_ns': 1789273102496683906, 'uid': 1000}, {'path': '/home/cnooc/weknora-v08-ui-stage1-20260913-r2.tar.gz', 'device': 64512, 'inode': 35827818, 'bytes': 46675866, 'mtime_ns': 1789287549318985097, 'uid': 1000}, {'path': '/home/cnooc/weknora-v08-ui-stage1-20260913-r3.tar.gz', 'device': 64512, 'inode': 35332873, 'bytes': 46676194, 'mtime_ns': 1789288037300084266, 'uid': 1000}, {'path': '/home/cnooc/weknora-v08-ui-stage1-20260913-r4.tar.gz', 'device': 64512, 'inode': 36016667, 'bytes': 46677673, 'mtime_ns': 1789289164762621793, 'uid': 1000}, {'path': '/home/cnooc/weknora-ui-r5-build-materials-20260928.tar.gz', 'device': 64512, 'inode': 40446660, 'bytes': 46671323, 'mtime_ns': 1790588149033519465, 'uid': 1000}, {'path': '/home/cnooc/weknora-v08-sandbox-stage3-gvisor-canary-20260917-r1.tar.gz', 'device': 64512, 'inode': 34741610, 'bytes': 40612263, 'mtime_ns': 1789623021996413719, 'uid': 1000}, {'path': '/home/cnooc/portal-ppt-refresh-20261017-r2-tDrkKzjp/EVIDENCE.tar.gz', 'device': 64512, 'inode': 781180702, 'bytes': 37363374, 'mtime_ns': 1792215602299495859, 'uid': 1000}, {'path': '/home/cnooc/weknora-ui-0.8.0-enterprise-sso1-r4-arm64-20260913.oci.tar', 'device': 64512, 'inode': 39578000, 'bytes': 37210112, 'mtime_ns': 1789290046604590746, 'uid': 1000}, {'path': '/home/cnooc/frontend-diagnosis-05882c2d-3pZvtk/frontend-05882c2d-source.zip', 'device': 64512, 'inode': 380547083, 'bytes': 35244786, 'mtime_ns': 1792051752734247946, 'uid': 1000}]
EXTRA_ROOTS = ['/home/cnooc/deerflow-digital-domains-skillruntime12-images', '/home/cnooc/deerflow-digital-employee-domains-r10-images-20260917', '/home/cnooc/deerflow-media-generation1-images-20260918', '/home/cnooc/digital-employee-domains-r7-images-20260917', '/home/cnooc/digitalemployee-promptfix1-images-20260915', '/home/cnooc/digitalemployee-ui2-r1-images-20260914', '/home/cnooc/digitalemployee1-r7-gateway-20260914', '/home/cnooc/model-output-recovery-20261014-candidate-r2', '/home/cnooc/model-output-recovery-20261014-candidate-r3', '/home/cnooc/weknora-v08-sandbox-images-20260917']
DEST = '/opt/portal-offline-archives-20261028-r2'
PRIOR_REPORT = '/opt/portal-offline-archives-20261028-r1/REPORT.json'
PRIOR_SHA256 = '7321d078df1148f9ca26a649f033c187444b07ae32efd6ab521c3a391c6cb3ac'
SOURCE_DEVICE = 64512
DEST_DEVICE = 64785
DEADLINE = 0
COPY_RATE = 16 * 1024**2
READ_RATE = 32 * 1024**2
MIN_AGE = 48 * 3600
MIN_BYTES = 32 * 1024**2


class Refused(Exception):
    pass


def require(ok, code):
    if not ok:
        raise Refused(code)


def budget():
    require(not DEADLINE or time.monotonic() < DEADLINE, 'TIME_LIMIT')


def parent_fd(path):
    parts = PurePosixPath(path).parts
    require(parts and parts[0] == '/' and '..' not in parts, 'INVALID_PATH')
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in parts[1:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        return fd, parts[-1]
    except BaseException:
        os.close(fd)
        raise


def identity(s):
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink, s.st_uid, s.st_gid, s.st_mode)


def pin(path, s):
    return dict(path=path, device=s.st_dev, inode=s.st_ino, bytes=s.st_size, mtime_ns=s.st_mtime_ns, uid=s.st_uid)


def archive_name(path):
    return path.endswith(('.tar', '.tar.gz', '.tar(1).gz', '.zip'))


def check_row(row, s):
    require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1, 'NOT_SINGLE_REGULAR_FILE')
    require(tuple(row[k] for k in ('device', 'inode', 'bytes', 'mtime_ns', 'uid')) ==
            (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_uid), 'SOURCE_CHANGED')
    require(s.st_dev == SOURCE_DEVICE and s.st_size >= MIN_BYTES, 'SOURCE_SIZE_OR_DEVICE')
    require(time.time() - max(s.st_mtime, s.st_ctime) >= MIN_AGE, 'SOURCE_TOO_RECENT')
    require(not s.st_mode & (stat.S_ISUID | stat.S_ISGID), 'SPECIAL_FILE_MODE')


def unchanged(parent, name, fd, initial):
    require(identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == identity(initial) ==
            identity(os.fstat(fd)), 'SOURCE_CHANGED_DURING_COPY')


def unescape_mount(value):
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m.group(1), 8)), value)


def object_identity(path):
    try:
        s = os.stat(path)
        return (s.st_dev, s.st_ino, stat.S_IFMT(s.st_mode))
    except (FileNotFoundError, ProcessLookupError):
        return None


def check_mount(fields, info, path, pid, destination=None):
    root, point = unescape_mount(fields[3]), unescape_mount(fields[4])
    require(point != path and not point.startswith(path + '/'), 'SOURCE_MOUNTPOINT')
    if fields[2] != '{}:{}'.format(os.major(info.st_dev), os.minor(info.st_dev)) or root == '/':
        return
    require(path != root, 'SOURCE_DIRECT_FILE_MOUNT')
    if not path.startswith(root.rstrip('/') + '/'):
        return
    # Only the diagnosed system-service read-only /home mirror is accepted.
    # Other bind paths and container/chroot roots remain blocked.
    require(root == point == '/home' and 'ro' in fields[5].split(','), 'SOURCE_BIND_MOUNTED')
    process_root = '/proc/' + pid + '/root'
    require(object_identity(process_root) == object_identity('/'), 'MOUNT_PROCESS_ROOT_DIFFERS')
    for directory in ('/opt', DEST):
        expected = object_identity(directory)
        require(expected is not None and object_identity(process_root + directory) == expected,
                'ARCHIVE_DESTINATION_NOT_VISIBLE_IN_NAMESPACE')
    expected_source = (info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode))
    require(object_identity(process_root + path) == expected_source, 'ARCHIVE_SOURCE_NAMESPACE_MISMATCH')
    if destination is not None:
        expected_copy = object_identity(destination)
        require(expected_copy is not None and object_identity(process_root + destination) == expected_copy,
                'ARCHIVE_COPY_NOT_VISIBLE_IN_NAMESPACE')


def require_unused(info, path, destination=None):
    """Reject open/mapped files; accept only the verified host read-only /home mirror."""
    key = (info.st_dev, info.st_ino)
    began = time.monotonic()
    seen_ns = set()
    checked = 0
    for pid in os.listdir('/proc'):
        if not pid.isdigit() or int(pid) == os.getpid():
            continue
        budget()
        require(time.monotonic() - began < 45, 'PROCESS_SCAN_TIME_LIMIT')
        try:
            with os.scandir('/proc/' + pid + '/fd') as entries:
                for e in entries:
                    checked += 1
                    require(checked < 300000, 'PROCESS_SCAN_SIZE_LIMIT')
                    try:
                        s = e.stat()
                    except FileNotFoundError:
                        continue
                    require((s.st_dev, s.st_ino) != key, 'SOURCE_OPEN_BY_PROCESS')
            with open('/proc/' + pid + '/maps', 'r') as stream:
                for line in stream:
                    fields = line.split(None, 5)
                    if len(fields) >= 5 and int(fields[4]) == info.st_ino:
                        major, minor = (int(x, 16) for x in fields[3].split(':'))
                        require(os.makedev(major, minor) != info.st_dev, 'SOURCE_MEMORY_MAPPED')
            ns = (os.readlink('/proc/' + pid + '/ns/mnt'), object_identity('/proc/' + pid + '/root'))
            if ns not in seen_ns:
                seen_ns.add(ns)
                with open('/proc/' + pid + '/mountinfo', 'r') as stream:
                    content = stream.read(4194305)
                require(len(content) <= 4194304, 'MOUNT_SCAN_LIMIT')
                for line in content.splitlines():
                    fields = line.split()
                    require(len(fields) > 5, 'MOUNT_RECORD_INVALID')
                    check_mount(fields, info, path, pid, destination)
        except (FileNotFoundError, ProcessLookupError):
            continue


def transfer(src, out, rate, limit):
    digest, size = hashlib.sha256(), 0
    began = time.monotonic()
    while True:
        budget()
        block = os.read(src, 1024**2)
        if not block:
            break
        size += len(block)
        require(size <= limit, 'FILE_GREW')
        digest.update(block)
        if out is not None:
            view = memoryview(block)
            while view:
                written = os.write(out, view)
                require(written > 0, 'SHORT_WRITE')
                view = view[written:]
        if rate:
            delay = size / rate - (time.monotonic() - began)
            if delay > 0:
                time.sleep(min(delay, 1))
    return digest.hexdigest(), size


def relocate(row, destfd, journal, check_unused=require_unused):
    path = row['path']
    require(path.startswith('/home/cnooc/') and archive_name(path), 'OUTSIDE_ARCHIVE_ALLOWLIST')
    parent, name = parent_fd(path)
    source = output = None
    temporary_link = None
    try:
        source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        initial = os.fstat(source)
        check_row(row, initial)
        check_unused(initial, path, None)
        require(os.fstat(destfd).st_dev == DEST_DEVICE != initial.st_dev, 'NOT_SEPARATE_DESTINATION')
        space = os.fstatvfs(destfd)
        require(space.f_bavail * space.f_frsize > initial.st_size + 20 * 1024**3, 'DESTINATION_HEADROOM')
        target = hashlib.sha256(path.encode()).hexdigest()[:24] + '-' + name
        # No overwrites. Incomplete copies remain for audit, original stays untouched.
        output = os.open(target, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=destfd)
        sha, size = transfer(source, output, COPY_RATE, initial.st_size)
        require(size == initial.st_size, 'COPY_LENGTH_MISMATCH')
        os.fchown(output, initial.st_uid, initial.st_gid)
        os.fchmod(output, stat.S_IMODE(initial.st_mode))
        for key in os.listxattr(source):
            os.setxattr(output, key, os.getxattr(source, key))
        os.utime(output, ns=(initial.st_atime_ns, initial.st_mtime_ns))
        os.fsync(output)
        os.fsync(destfd)
        before_hash = identity(os.fstat(output))
        os.lseek(output, 0, os.SEEK_SET)
        verified = transfer(output, None, READ_RATE, initial.st_size)
        require(verified == (sha, size), 'DESTINATION_HASH_MISMATCH')
        copied = os.fstat(output)
        require(identity(copied) == before_hash, 'DESTINATION_CHANGED_DURING_HASH')
        require((copied.st_uid, copied.st_gid, stat.S_IMODE(copied.st_mode)) ==
                (initial.st_uid, initial.st_gid, stat.S_IMODE(initial.st_mode)), 'DESTINATION_METADATA_MISMATCH')
        require({k: os.getxattr(source, k) for k in os.listxattr(source)} ==
                {k: os.getxattr(output, k) for k in os.listxattr(output)}, 'DESTINATION_XATTR_MISMATCH')
        unchanged(parent, name, source, initial)
        destination = DEST + '/' + target
        check_unused(initial, path, destination)
        journal({'phase': 'VERIFIED_COPY_BEFORE_REPLACE', 'source': path, 'destination': destination,
                 'sha256': sha, 'bytes': size, 'original_mode': stat.S_IMODE(initial.st_mode),
                 'original_uid': initial.st_uid, 'original_gid': initial.st_gid})
        temporary_link = '.portal-archive-link-' + hashlib.sha256(path.encode()).hexdigest()[:24]
        os.symlink(destination, temporary_link, dir_fd=parent)
        os.chown(temporary_link, initial.st_uid, initial.st_gid, dir_fd=parent, follow_symlinks=False)
        unchanged(parent, name, source, initial)
        require(identity(os.stat(target, dir_fd=destfd, follow_symlinks=False)) == identity(os.fstat(output)) == identity(copied), 'DESTINATION_CHANGED')
        # One atomic replacement: readers see the original or the verified retained copy.
        os.replace(temporary_link, name, src_dir_fd=parent, dst_dir_fd=parent)
        temporary_link = None
        os.fsync(parent)
        require(os.readlink(name, dir_fd=parent) == destination, 'SOURCE_LINK_READBACK_FAILED')
        require(os.stat(name, dir_fd=parent).st_ino == copied.st_ino and
                os.stat(name, dir_fd=parent).st_dev == copied.st_dev, 'SOURCE_LINK_TARGET_FAILED')
        return {'status': 'RELOCATED_VERIFIED_ORIGINAL_PATH_PRESERVED', 'source': path,
                'destination': destination, 'sha256': sha, 'bytes': size,
                'estimated_root_reclaimed_bytes': initial.st_blocks * 512}
    finally:
        if temporary_link is not None:
            try:
                os.unlink(temporary_link, dir_fd=parent)
            except FileNotFoundError:
                pass
        for fd in (output, source, parent):
            if fd is not None:
                os.close(fd)


def discover():
    rows = {r['path']: r for r in PLAN}
    skipped = []
    for root in EXTRA_ROOTS:
        pfd, name = parent_fd(root)
        fd = None
        try:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
            require(os.fstat(fd).st_dev == SOURCE_DEVICE, 'EXTRA_DIRECTORY_DEVICE')
            entries = os.listdir(fd)
            require(len(entries) <= 1024, 'EXTRA_DIRECTORY_LIMIT')
            for entry in entries:
                path = root + '/' + entry
                if path in rows or not archive_name(path):
                    continue
                s = os.stat(entry, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(s.st_mode) or s.st_size < MIN_BYTES:
                    continue
                row = pin(path, s)
                check_row(row, s)
                rows[path] = row
        except (OSError, Refused) as exc:
            skipped.append({'directory': root, 'error_code': str(exc) if isinstance(exc, Refused) else type(exc).__name__})
        finally:
            if fd is not None:
                os.close(fd)
            os.close(pfd)
    require(0 < len(rows) <= 128 and sum(r['bytes'] for r in rows.values()) <= 32 * 1024**3, 'PLAN_LIMIT')
    return sorted(rows.values(), key=lambda r: -r['bytes']), skipped


def available(path):
    s = os.statvfs(path)
    return s.f_bavail * s.f_frsize


def interrupted(signum, frame):
    raise InterruptedError('OPERATOR_INTERRUPTED')


def check_prior_report():
    parent, name = parent_fd(PRIOR_REPORT)
    fd = None
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        require(stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_size <= 2097152, 'PRIOR_REPORT_INVALID')
        with os.fdopen(os.dup(fd), 'rb') as stream:
            raw = stream.read(2097153)
        require(hashlib.sha256(raw).hexdigest() == PRIOR_SHA256, 'PRIOR_REPORT_CHANGED')
        r = json.loads(raw)
        require(r.get('relocated') == 0 and len(r.get('results', [])) == 61 and
                all(x.get('error_code') == 'SOURCE_BIND_MOUNTED' for x in r['results']), 'PRIOR_STATE_NOT_NOOP')
        return {'path': PRIOR_REPORT, 'sha256': PRIOR_SHA256, 'relocated': 0}
    finally:
        if fd is not None:
            os.close(fd)
        os.close(parent)


def main():
    global DEADLINE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--relocate-offline-archives-once', required=True, action='store_true')
    parser.parse_args()
    require(socket.gethostname().split('.')[0] == 'datax-01' and os.geteuid() == 0, 'HOST_OR_USER')
    require(os.stat('/').st_dev == SOURCE_DEVICE, 'ROOT_DEVICE_CHANGED')
    prior = check_prior_report()
    pfd, name = parent_fd(DEST)
    destfd = None
    try:
        opt = os.fstat(pfd)
        require(opt.st_dev == DEST_DEVICE and opt.st_uid == 0 and not opt.st_mode & 0o022, 'DESTINATION_MOUNT_OR_PERMISSIONS')
        require(opt.st_mode & 0o001, 'OPT_NOT_TRAVERSABLE_BY_ORIGINAL_USERS')
        require('system.posix_acl_access' not in os.listxattr(pfd) and
                'system.posix_acl_default' not in os.listxattr(pfd), 'OPT_CUSTOM_ACL_REQUIRES_REVIEW')
        require(os.path.ismount('/opt'), 'OPT_NOT_MOUNTPOINT')
        os.nice(19)
        ionice = shutil.which('ionice', path='/usr/sbin:/usr/bin:/sbin:/bin')
        require(ionice is not None, 'IONICE_REQUIRED')
        require(subprocess.run([ionice, '-c', '3', '-p', str(os.getpid())], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=10).returncode == 0, 'IONICE_NOT_ACCEPTED')
        DEADLINE = time.monotonic() + 5400
        rows, discovery_skips = discover()
        total = sum(r['bytes'] for r in rows)
        require(available('/opt') > total + 20 * 1024**3, 'DESTINATION_TOTAL_HEADROOM')
        os.mkdir(name, 0o755, dir_fd=pfd)  # Exclusive once claim; never resume blindly.
        destfd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
        os.fchmod(destfd, 0o755)  # Preserve path traversal even with root umask 077.
        os.fsync(pfd)
        def journal(row):
            fd = os.open('JOURNAL.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600, dir_fd=destfd)
            with os.fdopen(fd, 'a', encoding='utf-8') as stream:
                stream.write(json.dumps(row, sort_keys=True) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(destfd)
        journal({'phase': 'PLAN', 'plan': rows, 'discovery_skips': discovery_skips})
        before = available('/')
        started = time.monotonic()
        results = []
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        for i, row in enumerate(rows):
            print(json.dumps({'phase': 'VERIFY_COPY_AND_RELOCATE', 'checked': i, 'total': len(rows),
                              'file': os.path.basename(row['path'])}), flush=True)
            stop = False
            try:
                budget()
                result = relocate(row, destfd, journal)
            except (Exception, KeyboardInterrupt) as exc:
                result = {'source': row['path'], 'status': 'SKIPPED_OR_INCOMPLETE_REVIEW_JOURNAL',
                          'error_code': str(exc) if isinstance(exc, Refused) else type(exc).__name__}
                stop = isinstance(exc, (InterruptedError, KeyboardInterrupt)) or str(exc) == 'TIME_LIMIT'
            results.append(result)
            journal(result)
            if stop:
                break
        moved = [r for r in results if r['status'] == 'RELOCATED_VERIFIED_ORIGINAL_PATH_PRESERVED']
        report = {'schema': 'portal-datax01-offline-archive-relocation.v2', 'prior_noop_receipt': prior,
                  'status': 'ARCHIVES_RELOCATED_REVIEW_REQUIRED' if moved else 'NO_ARCHIVES_RELOCATED',
                  'planned': len(rows), 'processed': len(results), 'relocated': len(moved),
                  'skipped_or_incomplete': len(results) - len(moved), 'discovery_skips': discovery_skips,
                  'error_counts': {code: sum(r.get('error_code') == code for r in results)
                                   for code in sorted(set(r['error_code'] for r in results if 'error_code' in r))},
                  'results': results, 'before_root_available_bytes': before, 'after_root_available_bytes': available('/'),
                  'estimated_root_reclaimed_bytes': sum(r['estimated_root_reclaimed_bytes'] for r in moved),
                  'destination': DEST, 'original_paths_preserved_as_symlinks': bool(moved), 'retained_archives_must_not_be_deleted': True,
                  'service_restart_requests': 0, 'containerd_commands': 0, 'database_access': False,
                  'business_files_selected': False, 'log_files_selected': False, 'automatic_retry': False,
                  'elapsed_seconds': round(time.monotonic() - started, 3), 'report_path': DEST + '/REPORT.json'}
        raw = (json.dumps(report, sort_keys=True) + '\n').encode()
        fd = os.open('REPORT.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=destfd)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(destfd)
        report.pop('results')
        report['report_sha256'] = hashlib.sha256(raw).hexdigest()
        print(json.dumps(report, sort_keys=True), flush=True)
    finally:
        if destfd is not None:
            os.close(destfd)
        os.close(pfd)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(json.dumps({'status': 'INCOMPLETE', 'error_code': str(exc) if isinstance(exc, Refused) else type(exc).__name__,
                          'automatic_retry': False, 'review_journal_if_present': DEST + '/JOURNAL.jsonl'}), flush=True)
        raise SystemExit(1)

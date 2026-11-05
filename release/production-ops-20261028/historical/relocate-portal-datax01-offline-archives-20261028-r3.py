#!/usr/bin/env python3
"""Move verified closed offline archives off root; preserve original paths as symlinks.

Python 3.7+, datax-01 only. Selected pinned old rotated logs are retained off root; no runtime image, database or service mutations.
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

PLAN = [{'path': '/var/log/messages-20260916.gz', 'device': 64512, 'inode': 71315886, 'bytes': 48888359, 'mtime_ns': 1789488064540311985, 'uid': 0}, {'path': '/var/log/syslog-20260916.gz', 'device': 64512, 'inode': 71315883, 'bytes': 48888359, 'mtime_ns': 1789488064540311985, 'uid': 0}, {'path': '/var/log/messages-20260917.gz', 'device': 64512, 'inode': 67954879, 'bytes': 44015112, 'mtime_ns': 1789574465456535826, 'uid': 0}, {'path': '/var/log/syslog-20260917.gz', 'device': 64512, 'inode': 69348474, 'bytes': 44015112, 'mtime_ns': 1789574465456535826, 'uid': 0}, {'path': '/var/log/auth.log-20260920.gz', 'device': 64512, 'inode': 69426480, 'bytes': 30265155, 'mtime_ns': 1789833662564739647, 'uid': 0}, {'path': '/var/log/auth.log-20260913.gz', 'device': 64512, 'inode': 67749668, 'bytes': 29025569, 'mtime_ns': 1789228861625430746, 'uid': 0}, {'path': '/var/log/spark3/spark-spark-org.apache.spark.deploy.history.HistoryServer-1-as.hdp.com.out.1', 'device': 64512, 'inode': 437196457, 'bytes': 28453464, 'mtime_ns': 1744008079376324306, 'uid': 1014}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-08', 'device': 64512, 'inode': 873266090, 'bytes': 11435617, 'mtime_ns': 1744127999336163240, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-09', 'device': 64512, 'inode': 873266068, 'bytes': 11253859, 'mtime_ns': 1744214398529394110, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-11', 'device': 64512, 'inode': 873266074, 'bytes': 11253859, 'mtime_ns': 1744387198835860918, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-13', 'device': 64512, 'inode': 873266076, 'bytes': 11090350, 'mtime_ns': 1744559998702326524, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-10', 'device': 64512, 'inode': 873266073, 'bytes': 11066775, 'mtime_ns': 1744300798632627385, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-12', 'device': 64512, 'inode': 873266075, 'bytes': 11070433, 'mtime_ns': 1744473598789093795, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-14', 'device': 64512, 'inode': 873266077, 'bytes': 11038409, 'mtime_ns': 1744646398785559693, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-16', 'device': 64512, 'inode': 873266097, 'bytes': 10928199, 'mtime_ns': 1744819198892020599, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-22', 'device': 64512, 'inode': 873266103, 'bytes': 10925303, 'mtime_ns': 1745337599311412418, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-25', 'device': 64512, 'inode': 873266106, 'bytes': 10924631, 'mtime_ns': 1745596799241108057, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-15', 'device': 64512, 'inode': 873266096, 'bytes': 10925076, 'mtime_ns': 1744732798928790311, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-17', 'device': 64512, 'inode': 873266098, 'bytes': 10924493, 'mtime_ns': 1744905598955251799, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-19', 'device': 64512, 'inode': 873266100, 'bytes': 10926489, 'mtime_ns': 1745078399111716083, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-24', 'device': 64512, 'inode': 873266105, 'bytes': 10920885, 'mtime_ns': 1745510399987878046, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-23', 'device': 64512, 'inode': 873266104, 'bytes': 10921775, 'mtime_ns': 1745423999194644056, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-21', 'device': 64512, 'inode': 873266102, 'bytes': 10922783, 'mtime_ns': 1745251199168180109, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-26', 'device': 64512, 'inode': 873266107, 'bytes': 10920935, 'mtime_ns': 1745683199324340079, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-18', 'device': 64512, 'inode': 873266099, 'bytes': 10923633, 'mtime_ns': 1744991999098484109, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-20', 'device': 64512, 'inode': 873266101, 'bytes': 10923475, 'mtime_ns': 1745164799224948317, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-06', 'device': 64512, 'inode': 873266081, 'bytes': 10771997, 'mtime_ns': 1746547199766656786, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-22', 'device': 64512, 'inode': 874123788, 'bytes': 10763409, 'mtime_ns': 1747929599888356330, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-27', 'device': 64512, 'inode': 873266108, 'bytes': 10759610, 'mtime_ns': 1745769599417570856, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-06-06', 'device': 64512, 'inode': 874123804, 'bytes': 10620569, 'mtime_ns': 1749225596196817670, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-07', 'device': 64512, 'inode': 873266082, 'bytes': 10582948, 'mtime_ns': 1746633599829888891, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-01', 'device': 64512, 'inode': 873266070, 'bytes': 10579093, 'mtime_ns': 1746115199590496631, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-28', 'device': 64512, 'inode': 872522511, 'bytes': 10581673, 'mtime_ns': 1745855999450801563, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-06-27', 'device': 64512, 'inode': 875048325, 'bytes': 10583695, 'mtime_ns': 1751039998044675315, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-06-30', 'device': 64512, 'inode': 875048328, 'bytes': 10582848, 'mtime_ns': 1751299195884362981, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-04', 'device': 64512, 'inode': 873266079, 'bytes': 10578599, 'mtime_ns': 1746374399820193044, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-10', 'device': 64512, 'inode': 873266093, 'bytes': 10578940, 'mtime_ns': 1746892799989581863, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-29', 'device': 64512, 'inode': 874123776, 'bytes': 10577087, 'mtime_ns': 1745942399864033458, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-13', 'device': 64512, 'inode': 874123779, 'bytes': 10575980, 'mtime_ns': 1747151999859273391, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-16', 'device': 64512, 'inode': 874123782, 'bytes': 10573736, 'mtime_ns': 1747411199838968692, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-04-30', 'device': 64512, 'inode': 873266069, 'bytes': 10575861, 'mtime_ns': 1746028799487264424, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-08', 'device': 64512, 'inode': 873266091, 'bytes': 10574597, 'mtime_ns': 1746719999883120342, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-09', 'device': 64512, 'inode': 873266092, 'bytes': 10573915, 'mtime_ns': 1746806399866350922, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-03', 'device': 64512, 'inode': 873266078, 'bytes': 10575219, 'mtime_ns': 1746287999586960501, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-19', 'device': 64512, 'inode': 874123785, 'bytes': 10572214, 'mtime_ns': 1747670399838664205, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-02', 'device': 64512, 'inode': 873266071, 'bytes': 10575031, 'mtime_ns': 1746201599623728656, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-05', 'device': 64512, 'inode': 873266080, 'bytes': 10574557, 'mtime_ns': 1746460799743424785, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-11', 'device': 64512, 'inode': 873266094, 'bytes': 10573569, 'mtime_ns': 1746979199922812312, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-12', 'device': 64512, 'inode': 873266095, 'bytes': 10572151, 'mtime_ns': 1747065599926042943, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-17', 'device': 64512, 'inode': 874123783, 'bytes': 10569981, 'mtime_ns': 1747497599832200461, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-20', 'device': 64512, 'inode': 874123786, 'bytes': 10567747, 'mtime_ns': 1747756799801894864, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-14', 'device': 64512, 'inode': 874123780, 'bytes': 10570663, 'mtime_ns': 1747238399952505258, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-15', 'device': 64512, 'inode': 874123781, 'bytes': 10570821, 'mtime_ns': 1747324799955737054, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-18', 'device': 64512, 'inode': 874123784, 'bytes': 10568963, 'mtime_ns': 1747583999785432280, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-25', 'device': 64512, 'inode': 874123791, 'bytes': 10567786, 'mtime_ns': 1748188799968050944, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-28', 'device': 64512, 'inode': 874123794, 'bytes': 10568135, 'mtime_ns': 1748447999987746354, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-06-24', 'device': 64512, 'inode': 875048322, 'bytes': 10567776, 'mtime_ns': 1750780799004985937, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-23', 'device': 64512, 'inode': 874123789, 'bytes': 10566581, 'mtime_ns': 1748015999931587427, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-21', 'device': 64512, 'inode': 874123787, 'bytes': 10567421, 'mtime_ns': 1747843199935125830, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-24', 'device': 64512, 'inode': 874123790, 'bytes': 10565919, 'mtime_ns': 1748102399884819088, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-31', 'device': 64512, 'inode': 874123797, 'bytes': 10564356, 'mtime_ns': 1748707198727438046, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-06-09', 'device': 64512, 'inode': 874123807, 'bytes': 10562124, 'mtime_ns': 1749484799506521390, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-26', 'device': 64512, 'inode': 874123792, 'bytes': 10563487, 'mtime_ns': 1748275199931282635, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-27', 'device': 64512, 'inode': 874123793, 'bytes': 10562110, 'mtime_ns': 1748361598094509675, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-30', 'device': 64512, 'inode': 874123796, 'bytes': 10559889, 'mtime_ns': 1748620793194192376, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-06-01', 'device': 64512, 'inode': 874123798, 'bytes': 10558485, 'mtime_ns': 1748793595690660820, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-06-02', 'device': 64512, 'inode': 874123800, 'bytes': 10556815, 'mtime_ns': 1748879995463890854, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-06-03', 'device': 64512, 'inode': 874123801, 'bytes': 10560076, 'mtime_ns': 1748966399777132619, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-06-25', 'device': 64512, 'inode': 875048323, 'bytes': 10562943, 'mtime_ns': 1750867198738215867, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-06-26', 'device': 64512, 'inode': 875048324, 'bytes': 10561579, 'mtime_ns': 1750953596881441689, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-05-29', 'device': 64512, 'inode': 874123795, 'bytes': 10559362, 'mtime_ns': 1748534392670959237, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-06-04', 'device': 64512, 'inode': 874123802, 'bytes': 10557329, 'mtime_ns': 1749052790920340661, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-06-05', 'device': 64512, 'inode': 874123803, 'bytes': 10556005, 'mtime_ns': 1749139197943590512, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-06-07', 'device': 64512, 'inode': 874123805, 'bytes': 10556591, 'mtime_ns': 1749311999050056791, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-06-28', 'device': 64512, 'inode': 875048326, 'bytes': 10557971, 'mtime_ns': 1751126392677892069, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-06-29', 'device': 64512, 'inode': 875048327, 'bytes': 10557655, 'mtime_ns': 1751212798181137517, 'uid': 1009}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-06-08', 'device': 64512, 'inode': 874123806, 'bytes': 10549955, 'mtime_ns': 1749398399043288554, 'uid': 1009}, {'path': '/var/log/zookeeper/zookeeper.log.1', 'device': 64512, 'inode': 873219189, 'bytes': 10485785, 'mtime_ns': 1748734288887430560, 'uid': 1005}, {'path': '/var/log/hadoop/hdfs/hdfs-audit.log.2025-06-10', 'device': 64512, 'inode': 874123808, 'bytes': 10407067, 'mtime_ns': 1749571197669748382, 'uid': 1009}, {'path': '/var/log/ambari-metrics-monitor/ambari-metrics-monitor.log.1', 'device': 64512, 'inode': 102251511, 'bytes': 9999934, 'mtime_ns': 1749345225735904569, 'uid': 1006}]
EXTRA_ROOTS = ['/var/lib/portal-ppt-host-oci-bound-r1-output-bf5f67d679944a64861f8394e38f8584', '/var/lib/portal-ppt-provisioner-same-version-import-r1', '/var/lib/portal-ppt-refresh-tool-TeLN6c6D']
DEST = '/opt/portal-offline-archives-20261028-r3'
PRIOR_REPORT = '/opt/portal-offline-archives-20261028-r2/REPORT.json'
PRIOR_SHA256 = 'd4282c5256a00369054d59949b8b59788bb7b78c4833c7f59e6cd4d322317e10'
SOURCE_DEVICE = 64512
DEST_DEVICE = 64785
DEADLINE = 0
COPY_RATE = 16 * 1024**2
READ_RATE = 32 * 1024**2
MIN_AGE = 30 * 86400
MIN_BYTES = 1024**2


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


def allowed_source(path):
    if path.startswith('/var/log/'):
        return path in {r['path'] for r in PLAN} and bool(re.search(r'(?:[-.]\d{8}|\.\d+|\.\d{4}-\d{2}-\d{2})(?:\.gz|\.xz|\.zst)?$', path))
    return (path.startswith('/home/cnooc/') or str(PurePosixPath(path).parent) in EXTRA_ROOTS) and archive_name(path)


def check_row(row, s):
    require(stat.S_ISREG(s.st_mode) and s.st_nlink == 1, 'NOT_SINGLE_REGULAR_FILE')
    require(tuple(row[k] for k in ('device', 'inode', 'bytes', 'mtime_ns', 'uid')) ==
            (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_uid), 'SOURCE_CHANGED')
    require(s.st_dev == SOURCE_DEVICE and s.st_size >= MIN_BYTES, 'SOURCE_SIZE_OR_DEVICE')
    minimum_age = MIN_AGE if row['path'].startswith('/var/log/') else min(MIN_AGE, 48 * 3600)
    require(time.time() - max(s.st_mtime, s.st_ctime) >= minimum_age, 'SOURCE_TOO_RECENT')
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
    require(allowed_source(path), 'OUTSIDE_ARCHIVE_ALLOWLIST')
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
        require(r.get('relocated') == 61 and len(r.get('results', [])) == 61 and
                all(x.get('status') == 'RELOCATED_VERIFIED_ORIGINAL_PATH_PRESERVED' for x in r['results']), 'PRIOR_STATE_NOT_ACCEPTED')
        return {'path': PRIOR_REPORT, 'sha256': PRIOR_SHA256, 'relocated': 61}
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
        report = {'schema': 'portal-datax01-offline-archive-relocation.v3', 'prior_success_receipt': prior,
                  'status': 'ARCHIVES_RELOCATED_REVIEW_REQUIRED' if moved else 'NO_ARCHIVES_RELOCATED',
                  'planned': len(rows), 'processed': len(results), 'relocated': len(moved),
                  'skipped_or_incomplete': len(results) - len(moved), 'discovery_skips': discovery_skips,
                  'error_counts': {code: sum(r.get('error_code') == code for r in results)
                                   for code in sorted(set(r['error_code'] for r in results if 'error_code' in r))},
                  'results': results, 'before_root_available_bytes': before, 'after_root_available_bytes': available('/'),
                  'estimated_root_reclaimed_bytes': sum(r['estimated_root_reclaimed_bytes'] for r in moved),
                  'destination': DEST, 'original_paths_preserved_as_symlinks': bool(moved), 'retained_archives_must_not_be_deleted': True,
                  'service_restart_requests': 0, 'containerd_commands': 0, 'database_access': False,
                  'business_files_selected': False, 'log_files_selected': True, 'automatic_retry': False,
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

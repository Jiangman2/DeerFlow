#!/usr/bin/env python3
"""Read-only datax-03 NFS feasibility inventory. Python 3.7+ stdlib."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time

HOST = 'datax-03'
SELF_IP = '192.168.111.18'
PEER_IP = '192.168.111.2'
TARGETS = ('/opt/deerflow-storage/weknora-files', '/opt/deerflow-storage/weknora-docreader')
SERVICES = ('nfs-server.service', 'nfs-kernel-server.service', 'rpcbind.service', 'firewalld.service')
PROPS = ('LoadState', 'ActiveState', 'SubState', 'UnitFileState', 'Result')
SERVICE_VALUES = set(('loaded not-found masked error active inactive activating deactivating '
                      'failed reloading dead running exited listening plugged start-pre start '
                      'start-post stop stop-sigterm stop-sigkill auto-restart enabled enabled-runtime '
                      'disabled static indirect generated transient alias masked-runtime linked '
                      'linked-runtime bad success resources timeout exit-code signal core-dump '
                      'watchdog start-limit-hit dependency condition skipped').split())
NAME = re.compile(r'[A-Za-z0-9_.:-]{1,80}\Z')


class Unavailable(RuntimeError):
    pass


def need(ok, reason):
    if not ok:
        raise Unavailable(reason)


def emit(value):
    print(json.dumps(value, sort_keys=True, ensure_ascii=True), flush=True)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_ancestors(path):
    for p in reversed(tuple(path.parents)):
        s = p.lstat()
        need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
             'unsafe_ancestor')


def read(path, limit=1048576, private_config=False):
    p = Path(path)
    if private_config:
        safe_ancestors(p)
    fd = os.open(str(p), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        s = os.fstat(stream.fileno())
        need(stat.S_ISREG(s.st_mode) and s.st_size <= limit, 'file_type_or_size')
        if private_config:
            need(s.st_uid == 0 and not s.st_mode & 0o022 and s.st_nlink == 1,
                 'unsafe_config_file')
        raw = stream.read(limit + 1)
        need(len(raw) <= limit, 'file_limit')
        return raw


def bounded_run(argv, timeout=8, limit=1048576):
    p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, close_fds=True)
    buffers = [bytearray(), bytearray()]
    bad = threading.Event()

    def drain(pipe, index):
        try:
            while True:
                block = pipe.read(4096)
                if not block:
                    return
                if len(buffers[index]) + len(block) > limit:
                    bad.set()
                    return
                buffers[index].extend(block)
        except Exception:
            bad.set()
        finally:
            pipe.close()

    threads = [threading.Thread(target=drain, args=(p.stdout, 0), daemon=True),
               threading.Thread(target=drain, args=(p.stderr, 1), daemon=True)]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + timeout
    expired = False
    try:
        while p.poll() is None:
            if bad.is_set():
                break
            if time.monotonic() >= deadline:
                expired = True
                break
            time.sleep(.02)
    finally:
        if p.poll() is None:
            p.kill()  # Only our read-only child; never an existing service.
        p.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=2)
    need(not expired, 'command_timeout')
    need(not bad.is_set() and not any(t.is_alive() for t in threads), 'command_output_limit_or_read')
    return p.returncode, bytes(buffers[0])


def tool(name):
    found = shutil.which(name, path='/usr/sbin:/usr/bin:/sbin:/bin')
    if found is None:
        return None
    p = Path(found).resolve(strict=True)
    safe_ancestors(p)
    s = p.stat()
    need(stat.S_ISREG(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
         'unsafe_executable')
    return str(p)


def read_command_allowed(name, args):
    fixed = {
        'exportfs': [['-s']],
        'ip': [['-o', '-4', 'addr', 'show'], ['-j', 'route', 'get', PEER_IP]],
        'ss': [['-H', flag, '( sport = :2049 or sport = :111 )'] for flag in ('-lnt', '-lnu')],
        'findmnt': [['--json', '--target', path, '--output', 'TARGET,SOURCE,FSTYPE,OPTIONS']
                    for path in TARGETS],
        'systemctl': [['show', service, '--no-pager'] + ['--property=' + key for key in PROPS]
                      for service in SERVICES],
    }
    if name in fixed:
        return args in fixed[name]
    if name == 'firewall-cmd':
        if args in (['--state'], ['--get-active-zones']):
            return True
        return (len(args) == 2 and args[0].startswith('--zone=')
                and bool(NAME.fullmatch(args[0][7:]))
                and args[1] in ('--query-service=nfs', '--query-port=2049/tcp'))
    return False


def service_projection(raw):
    result = {}
    for line in raw.decode('utf-8', 'replace').splitlines():
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        if key in PROPS:
            need(key not in result, 'duplicate_service_field')
            result[key] = value if value in SERVICE_VALUES else ('EMPTY' if not value else 'OTHER_REDACTED')
    need('LoadState' in result and 'ActiveState' in result, 'service_fields_missing')
    return result


def relation(export_path, target):
    p, t = Path(export_path), Path(target)
    if p == t:
        return 'EXACT'
    if p in t.parents:
        return 'ANCESTOR'
    if t in p.parents:
        return 'DESCENDANT'
    return 'UNRELATED'


def export_projection(raw, active):
    lines = raw.decode('utf-8', 'strict').replace('\\\n', ' ').splitlines()
    count = 0
    overlaps = []
    for line in lines:
        parts = shlex.split(line, comments=True)
        if not parts:
            continue
        need(len(parts) >= 2 and parts[0].startswith('/'), 'export_shape')
        path = parts[0]
        need('..' not in Path(path).parts and str(Path(path)) == path, 'export_path_shape')
        matches = {target: relation(path, target) for target in TARGETS
                   if relation(path, target) != 'UNRELATED'}
        count += 1
        if matches:
            row = dict(path_sha256=digest(path.encode()), relationships=matches,
                       client_specs=len(parts)-1)
            if active:
                entries = [re.fullmatch(r'[^()\s]+\(([^()]*)\)', item) for item in parts[1:]]
                need(all(entries), 'active_export_client_shape')
                row['options'] = [{key: key in m.group(1).split(',') for key in
                                   ('rw', 'ro', 'root_squash', 'no_root_squash', 'sync', 'async')}
                                  for m in entries]
            overlaps.append(row)
    return dict(export_lines=count, target_overlaps=overlaps)


def listening_projection(raw):
    result = []
    for line in raw.decode('utf-8', 'strict').splitlines():
        parts = line.split()
        need(len(parts) >= 5, 'socket_shape')
        address = parts[3]
        match = re.search(r':(111|2049)$', address)
        need(match is not None, 'unexpected_socket_port')
        host = address[:match.start()]
        result.append(dict(port=int(match.group(1)), wildcard=host in ('*', '0.0.0.0', '[::]', '::'),
                           loopback=host in ('127.0.0.1', '[::1]', '::1')))
    return result


def memory_projection(raw):
    values = {}
    wanted = {'MemTotal', 'MemAvailable', 'MemFree', 'SwapTotal', 'SwapFree'}
    for line in raw.decode('ascii', 'strict').splitlines():
        match = re.fullmatch(r'([A-Za-z_()]+):\s+([0-9]+) kB', line)
        if match and match.group(1) in wanted:
            values[match.group(1) + '_bytes'] = int(match.group(2)) * 1024
    need('MemTotal_bytes' in values and 'MemAvailable_bytes' in values, 'meminfo_fields')
    return values


def counters(reader=read):
    cpu = reader('/proc/stat').decode('ascii').splitlines()[0].split()
    need(cpu[0] == 'cpu' and len(cpu) >= 9, 'cpu_counter_shape')
    cpu = [int(x) for x in cpu[1:9]]  # guest time already included in user/nice.
    disk = {}
    for line in reader('/proc/diskstats').decode('ascii').splitlines():
        f = line.split()
        if len(f) >= 14 and f[2] in ('vdb', 'vdb1'):
            disk[f[2]] = dict(read_bytes=int(f[5])*512, write_bytes=int(f[9])*512,
                              io_ms=int(f[12]), weighted_io_ms=int(f[13]))
    net = {}
    for line in reader('/proc/net/dev').decode('ascii').splitlines()[2:]:
        name, values = line.rsplit(':', 1)
        name = name.strip()
        if name == 'lo' or not NAME.fullmatch(name):
            continue
        f = values.split()
        need(len(f) >= 16, 'net_counter_shape')
        net[name] = dict(rx_bytes=int(f[0]), tx_bytes=int(f[8]),
                         rx_drops=int(f[3]), tx_drops=int(f[11]))
    return dict(monotonic=time.monotonic(), cpu=cpu, disk=disk, net=net)


def counter_delta(first, second):
    elapsed = second['monotonic'] - first['monotonic']
    need(elapsed > 0, 'sample_interval')
    delta = [b-a for a, b in zip(first['cpu'], second['cpu'])]
    result = dict(seconds=round(elapsed, 3), cpu='COUNTER_RESET_OR_UNAVAILABLE')
    if len(delta) == 8 and min(delta) >= 0 and sum(delta) > 0:
        result['cpu'] = dict(busy_percent=round((sum(delta)-delta[3]-delta[4])*100/sum(delta), 2),
                             iowait_percent=round(delta[4]*100/sum(delta), 2))
    for group in ('disk', 'net'):
        result[group] = {}
        for name in sorted(set(first[group]) | set(second[group])):
            if name not in first[group] or name not in second[group]:
                result[group][name] = dict(state='APPEARED_OR_DISAPPEARED')
                continue
            values = {key: second[group][name][key]-value for key, value in first[group][name].items()}
            if any(v < 0 for v in values.values()):
                result[group][name] = dict(state='COUNTER_RESET')
            else:
                result[group][name] = {key + '_per_second': round(value/elapsed, 2)
                                      for key, value in values.items()}
    return result


class Audit:
    def __init__(self):
        self.started = time.monotonic()
        self.results = {}

    def section(self, name, function):
        emit(dict(storage_audit='READ_ONLY', section=name))
        try:
            need(time.monotonic()-self.started < 100, 'total_collection_budget')
            self.results[name] = dict(state='COLLECTED', data=function())
        except Exception as exc:
            self.results[name] = dict(state='UNAVAILABLE', error_type=type(exc).__name__,
                                     reason=str(exc) if isinstance(exc, Unavailable) else 'READ_FAILED',
                                     raw_error_printed=False)

    def command(self, name, args, accept=(0,)):
        need(read_command_allowed(name, args), 'command_not_read_allowlisted')
        need(time.monotonic()-self.started < 100, 'total_collection_budget')
        executable = tool(name)
        need(executable is not None, 'tool_not_installed_' + name)
        rc, raw = bounded_run([executable] + args)
        need(rc in accept, 'read_command_exit_' + str(rc))
        return rc, raw

    def services(self):
        rows = {}
        for name in SERVICES:
            rc, raw = self.command('systemctl', ['show', name, '--no-pager']
                                   + ['--property=' + key for key in PROPS], (0, 1, 4))
            rows[name] = dict(exit_code=rc, **service_projection(raw))
        return rows

    def paths(self):
        rows = {}
        for path in TARGETS:
            p = Path(path)
            safe_ancestors(p)
            s = p.lstat()
            need(stat.S_ISDIR(s.st_mode), 'storage_root_not_real_directory')
            v = os.statvfs(path)
            _, raw = self.command('findmnt', ['--json', '--target', path, '--output', 'TARGET,SOURCE,FSTYPE,OPTIONS'])
            fs = json.loads(raw).get('filesystems', [])
            need(len(fs) == 1 and fs[0].get('fstype') in ('xfs', 'ext4'), 'unexpected_filesystem')
            row = dict(uid=s.st_uid, gid=s.st_gid, mode=oct(stat.S_IMODE(s.st_mode)),
                       filesystem=fs[0]['fstype'], mounted_at_opt=fs[0].get('target') == '/opt',
                       source_is_vdb1=fs[0].get('source') == '/dev/vdb1',
                       writable_mount='rw' in fs[0].get('options', '').split(','),
                       available_bytes=v.f_bavail*v.f_frsize, total_bytes=v.f_blocks*v.f_frsize,
                       available_inodes=v.f_favail, file_tree_scanned=False)
            rows[path] = row
        return rows

    def configs(self):
        paths = []
        if os.path.lexists('/etc/exports'):
            paths.append(Path('/etc/exports'))
        directory = Path('/etc/exports.d')
        if os.path.lexists(str(directory)):
            safe_ancestors(directory)
            s = directory.lstat()
            need(stat.S_ISDIR(s.st_mode) and s.st_uid == 0 and not s.st_mode & 0o022,
                 'unsafe_exports_directory')
            with os.scandir(str(directory)) as it:
                paths += [directory / e.name for e in it
                          if not e.name.startswith('.') and e.name.endswith('.exports')]
        need(len(paths) <= 128, 'too_many_export_configs')
        rows = []
        for path in sorted(paths):
            raw = read(str(path), private_config=True)
            rows.append(dict(file_path_sha256=digest(str(path).encode()), content_sha256=digest(raw),
                             **export_projection(raw, False)))
        return dict(config_files=len(rows), configs=rows)

    def networking(self):
        _, raw = self.command('ip', ['-o', '-4', 'addr', 'show'])
        local = re.findall(r'\binet ([0-9.]+)/[0-9]+\b', raw.decode('ascii', 'strict'))
        _, raw = self.command('ip', ['-j', 'route', 'get', PEER_IP])
        routes = json.loads(raw)
        need(len(routes) == 1, 'route_shape')
        r = routes[0]
        dev = r.get('dev', '')
        return dict(reviewed_self_ip_present=SELF_IP in local, peer=PEER_IP,
                    route_source_matches_reviewed_self=r.get('prefsrc', r.get('src')) == SELF_IP,
                    interface=dev if NAME.fullmatch(dev) else 'UNKNOWN',
                    network_probe_sent=False, peer_firewall='NOT_TESTED')

    def firewall(self):
        if tool('firewall-cmd') is None:
            return dict(state='TOOL_NOT_INSTALLED', network_access='NOT_PROVEN')
        rc, raw = self.command('firewall-cmd', ['--state'], (0, 252))
        if rc == 252:
            return dict(state='NOT_RUNNING', network_access='NOT_PROVEN')
        need(raw.strip() == b'running', 'firewalld_state_unrecognized')
        _, raw = self.command('firewall-cmd', ['--get-active-zones'])
        zones = [line for line in raw.decode('utf-8', 'strict').splitlines()
                 if line and not line[0].isspace()]
        need(len(zones) <= 8 and all(NAME.fullmatch(z) for z in zones), 'firewall_zones_shape')
        results = []
        for zone in zones:
            row = dict(zone=zone)
            for option in ('--query-service=nfs', '--query-port=2049/tcp'):
                rc, raw = self.command('firewall-cmd', ['--zone=' + zone, option], (0, 1))
                need(raw.strip() in (b'yes', b'no'), 'firewall_query_unrecognized')
                need((rc == 0) == (raw.strip() == b'yes'), 'firewall_query_inconsistent')
                row[option[2:]] = rc == 0
            results.append(row)
        return dict(state='RUNNING', zones=results, network_access='NOT_PROVEN',
                    other_firewalls_and_rich_rules='NOT_INSPECTED')

    def kernel(self):
        result = dict(nfsd_listed_in_filesystems=bool(re.search(
            rb'(?m)\bnfsd\s*$', read('/proc/filesystems'))), nfsd_versions='NOT_EXPOSED')
        if os.path.exists('/proc/fs/nfsd/versions'):
            versions = read('/proc/fs/nfsd/versions', 4096).decode('ascii', 'strict').split()
            need(all(re.fullmatch(r'[+-][234](?:\.[0-9]+)?', x) for x in versions), 'nfs_versions_shape')
            result['nfsd_versions'] = versions
        return result

    def sample(self):
        first = counters()
        time.sleep(5)
        result = counter_delta(first, counters())
        result['memory'] = memory_projection(read('/proc/meminfo'))
        f = read('/proc/loadavg').decode('ascii').split()
        need(len(f) >= 3, 'loadavg_shape')
        result['load_average'] = [float(x) for x in f[:3]]
        result['cpu_count'] = os.cpu_count()
        result['disk_mapping_caveat'] = 'vdb/vdb1_ONLY_SEE_PATHS_SOURCE_CHECK'
        network = self.results.get('networking', {}).get('data', {})
        interface = network.get('interface')
        # Only the reviewed peer route's interface, not every Pod/veth interface.
        result['net'] = {interface: result['net'][interface]} if interface in result['net'] else {}
        result['network_sample_state'] = 'OBSERVED' if result['net'] else 'ROUTE_INTERFACE_NOT_OBSERVED'
        return result

    def run(self):
        self.section('tools', lambda: {name: tool(name) is not None for name in
                     ('exportfs', 'mount.nfs', 'rpc.nfsd', 'rpc.mountd', 'systemctl', 'findmnt', 'ip', 'ss', 'firewall-cmd')})
        self.section('paths', self.paths)
        self.section('services', self.services)
        self.section('export_configs', self.configs)
        self.section('active_exports', lambda: export_projection(
                     self.command('exportfs', ['-s'])[1], True) if tool('exportfs') else
                     dict(state='TOOL_NOT_INSTALLED', active_exports='UNKNOWN'))
        self.section('kernel', self.kernel)
        self.section('networking', self.networking)
        for protocol, flag in (('tcp', '-lnt'), ('udp', '-lnu')):
            self.section('listen_' + protocol, lambda flag=flag: listening_projection(
                         self.command('ss', ['-H', flag, '( sport = :2049 or sport = :111 )'])[1]))
        self.section('firewall', self.firewall)
        self.section('passive_resources', self.sample)
        failures = [name for name, value in self.results.items() if value['state'] == 'UNAVAILABLE']
        emit(dict(storage_share_audit='COMPLETE' if not failures else 'PARTIAL', node=HOST,
                  utc=datetime.datetime.now(datetime.timezone.utc).isoformat(), results=self.results,
                  unavailable_sections=failures, files_modified=False, production_writes=0,
                  business_data_copied=False, services_started=False, model_requests=0,
                  ready_to_export=False, ready_to_change_mounts=False, ready_to_scale=False,
                  elapsed_seconds=round(time.monotonic()-self.started, 2)))
        return 2 if failures else 0


def main():
    try:
        need(len(sys.argv) == 1, 'no_arguments_supported')
        need(sys.platform == 'linux' and os.geteuid() == 0, 'linux_root_required')
        need(socket.gethostname().split('.')[0] == HOST, 'wrong_host')
        return Audit().run()
    except Exception as exc:
        emit(dict(storage_share_audit='INCOMPLETE', error_type=type(exc).__name__,
                  check=str(exc) if isinstance(exc, Unavailable) else 'READ_FAILED',
                  raw_error_printed=False, production_writes=0, ready_to_scale=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())

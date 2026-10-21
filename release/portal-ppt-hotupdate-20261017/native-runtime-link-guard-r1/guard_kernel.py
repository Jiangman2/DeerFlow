"""Disable non-loopback links in this owned Pod netns; stdlib only."""
import argparse
import fcntl
import hashlib
import json
import os
import re
import socket
import stat
import struct
import sys

MANIFEST = 'sha256:9b3a6d80d20b73585c6642213077236bb9948f35d0d5daa262e78d120422eb9e'
CONFIG = 'sha256:5b43bc9cd5f51029b87aaf0b212c064124e29fb814c5543b7de7d864812f2902'
SCHEMA = 'portal-owned-pod-link-guard.v1'
SIOCGIFFLAGS, SIOCSIFFLAGS = 0x8913, 0x8914
IFF_UP, IFF_LOOPBACK = 1, 8


def require(value, code):
    if not value:
        raise RuntimeError(code)


def netns():
    info = os.stat('/proc/self/ns/net')
    link = os.readlink('/proc/self/ns/net')
    require(link == 'net:[%d]' % info.st_ino, 'netns_link_inode')
    return dict(device=info.st_dev, inode=info.st_ino, link=link)


def topology():
    rows = socket.if_nameindex()
    require(rows and len(rows) <= 64, 'interface_count')
    require(all(type(index) is int and index > 0 and isinstance(name, str)
                and re.fullmatch(r'[A-Za-z0-9_.:-]{1,15}', name) for index, name in rows), 'interface_shape')
    require(len({index for index, _ in rows}) == len(rows) and len({name for _, name in rows}) == len(rows), 'interface_duplicates')
    return sorted(rows, key=lambda item:item[1])


def flags(sock, name):
    # Linux ifreq is 40 bytes on both target ARM64 and local x86_64 fixtures.
    request = struct.pack('16sH22x', name.encode('ascii'), 0)
    require(len(request) == 40, 'ifreq_size')
    response = fcntl.ioctl(sock.fileno(), SIOCGIFFLAGS, request)
    require(len(response) == 40, 'ifreq_response_size')
    return struct.unpack_from('H', response, 16)[0]


def snapshot(sock):
    before = netns()
    entries = topology()
    rows = [dict(index=index, name=name, flags=flags(sock,name)) for index,name in entries]
    require(topology() == entries and netns() == before, 'namespace_or_interfaces_changed')
    require(sum(row['name']=='lo' for row in rows) == 1, 'unique_lo_required')
    require(all(bool(row['flags'] & IFF_LOOPBACK) == (row['name']=='lo') for row in rows), 'loopback_flag_identity')
    require(next(row for row in rows if row['name']=='lo')['flags'] & IFF_UP, 'loopback_not_up')
    return dict(netns=before, interfaces=rows)


def identity(value):
    return [(row['index'],row['name']) for row in value['interfaces']]


def disable_links(sock):
    before = snapshot(sock)
    for row in before['interfaces']:
        if row['name'] != 'lo' and row['flags'] & IFF_UP:
            require(netns() == before['netns'] and topology() == identity(before), 'namespace_or_interfaces_changed')
            current = flags(sock,row['name'])
            fcntl.ioctl(sock.fileno(),SIOCSIFFLAGS,struct.pack('16sH22x',row['name'].encode('ascii'),current & ~IFF_UP))
    after = snapshot(sock)
    require(after['netns']==before['netns'] and identity(after)==identity(before), 'namespace_or_interfaces_changed')
    require(all(row['flags'] & IFF_UP == 0 for row in after['interfaces'] if row['name']!='lo'), 'non_loopback_still_up')
    require(snapshot(sock) == after, 'post_guard_snapshot_unstable')
    return before, after


def own_source_sha():
    raw = open('/proc/self/cmdline','rb').read(262145)
    require(len(raw)<=262144, 'argv_bound')
    argv = raw.split(b'\x00')
    require(argv.count(b'-c') == 1, 'inline_source_argv')
    return hashlib.sha256(argv[argv.index(b'-c')+1]).hexdigest()


def own_privileges():
    wanted={'CapEff','CapPrm','CapBnd','CapAmb','CapInh','NoNewPrivs'}
    values={}
    with open('/proc/self/status') as stream:
        for line in stream:
            key,_,value=line.partition(':')
            if key in wanted:
                require(key not in values, 'duplicate_privilege_field')
                values[key]=int(value.strip(),10 if key=='NoNewPrivs' else 16)
    require(values==dict(CapEff=4096,CapPrm=4096,CapBnd=4096,CapAmb=0,CapInh=0,NoNewPrivs=1), 'guard_privilege_contract')
    return values


def write_receipt(directory, receipt):
    info = os.lstat(directory)
    require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and not os.listdir(directory), 'receipt_mount_not_fresh')
    values = dict(owner=receipt['owner'],pod_uid=receipt['pod_uid'],code_sha256=receipt['code_sha256'],
                  netns_stat='%d:%d' % (receipt['after']['netns']['device'],receipt['after']['netns']['inode']),
                  netns_link=receipt['after']['netns']['link'],
                  interfaces='\n'.join('%d %s' % (row['index'],row['name']) for row in receipt['after']['interfaces']))
    values['REPORT.json'] = json.dumps(receipt,sort_keys=True,separators=(',',':'))
    for name,value in values.items():
        fd = os.open(os.path.join(directory,name),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o444)
        with os.fdopen(fd,'wb') as stream:
            stream.write((value+'\n').encode()); stream.flush(); os.fsync(stream.fileno())
    fd = os.open(os.path.join(directory,'READY'),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o444)
    with os.fdopen(fd,'wb') as stream:
        stream.write(b'COMPLETE\n'); stream.flush(); os.fsync(stream.fileno())
    os.chmod(directory,0o555)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--owner',required=True); parser.add_argument('--pod-uid',required=True)
    parser.add_argument('--code-sha',required=True); parser.add_argument('--receipt-directory')
    args=parser.parse_args()
    require(sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode, 'isolated_stdlib_flags')
    require(os.geteuid()==0 and re.fullmatch('[a-f0-9]{32}',args.owner)
            and re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',args.pod_uid), 'guard_identity')
    require(own_source_sha()==args.code_sha, 'inline_source_digest')
    privileges=own_privileges()
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        before,after=disable_links(sock)
    receipt=dict(schema=SCHEMA,status='OWNED_NETNS_NON_LOOPBACK_DOWN',owner=args.owner,pod_uid=args.pod_uid,
                 code_sha256=args.code_sha,image=dict(manifest_digest=MANIFEST,config_digest=CONFIG),
                 before=before,after=after,loopback_up=True,non_loopback_down=True,
                 guard_privileges=privileges,
                 capabilities_revoked_by_guard=False,scope='THIS_OWNED_POD_NETWORK_NAMESPACE_ONLY')
    if args.receipt_directory:
        write_receipt(args.receipt_directory,receipt)
    print(json.dumps(receipt,sort_keys=True,separators=(',',':')),flush=True)


if __name__=='__main__':
    main()

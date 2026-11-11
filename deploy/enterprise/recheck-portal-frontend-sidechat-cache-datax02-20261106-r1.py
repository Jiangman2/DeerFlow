"""Read-only recheck of datax-02's already imported fixed side-chat image.

Only local containerd/CRI read commands; no import, unpack, tag, task, workload,
configuration or database mutation. Retains the original failed receipt and marker.
Writes one independent root-private verification report. Python 3.7+.
"""
import argparse
from collections import Counter
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid

HOST='datax-02'
NODE_UID='3148d4c1-6a97-42ed-a78c-950dcea5cbec'
SOCKET='/run/containerd/containerd.sock'
PRIOR_PATH=Path('/var/lib/portal-frontend-sidechat-security-preload-20261105-r1/REPORT.json')
PRIOR_BYTES=115986
PRIOR_SHA='b23b2d1f07eb5b2d5c00d72b8393e5b9503c1a51b90107e1d7179c1097139120'
PRIOR_OPERATION='281ac71347fa44a59ef78d56c690c8f3'
REPORT_PARENT=Path('/var/lib')
REPORT_PREFIX='portal-frontend-sidechat-security-recheck-20261106-r1-'
SAFE_PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin'
MIB=1024*1024
MAX_COMMANDS=90
TOTAL_SECONDS=240
TRANSPORT_PIN={'bytes': 127456768, 'sha256': '402aa5df479bc57be3923555a32240dedf614a239c58ca999c5ec2998590b61c'}
PROTECTED={'deerflow.local/deer-flow-frontend:20261016-shoptype-4b08bcc9-c55d72b6da1f': 'sha256:94775d3eff1788d6d9a7317cd495423124cf298d27b164a0a138c3aa131b7240', 'deerflow.local/deer-flow-frontend:20261019-portal-ui-r2-61aad75f8': 'sha256:1f2b6f34931ea39154c3e99e17e041fd8e8f76bbc2c42ed1f7b3d8d6cb0ba42a', 'deerflow.local/deer-flow-frontend@sha256:1f2b6f34931ea39154c3e99e17e041fd8e8f76bbc2c42ed1f7b3d8d6cb0ba42a': 'sha256:1f2b6f34931ea39154c3e99e17e041fd8e8f76bbc2c42ed1f7b3d8d6cb0ba42a', 'deerflow.local/deer-flow-frontend:20261105-new-chat-v41-r1': 'sha256:8a964aebacc5b3b566bc368a03dfd6a333f9f970d4fb0d4a6cb32d8d4ed7577d', 'deerflow.local/deer-flow-frontend@sha256:8a964aebacc5b3b566bc368a03dfd6a333f9f970d4fb0d4a6cb32d8d4ed7577d': 'sha256:8a964aebacc5b3b566bc368a03dfd6a333f9f970d4fb0d4a6cb32d8d4ed7577d', 'deerflow.local/deer-flow-frontend:20261103-dashboard-navigation-r2': 'sha256:74c2e66ecc1c2e9b483faac428fc9211ff43bc0e0ed59ee141de379c678fdfb5', 'deerflow.local/deer-flow-frontend@sha256:74c2e66ecc1c2e9b483faac428fc9211ff43bc0e0ed59ee141de379c678fdfb5': 'sha256:74c2e66ecc1c2e9b483faac428fc9211ff43bc0e0ed59ee141de379c678fdfb5', 'deerflow.local/deer-flow-frontend:20261102-usage-heatmap-r2': 'sha256:97a84226f1c9f77871c05cebaae1fa42b7525644edc844bccf75e29518ba44c1', 'deerflow.local/deer-flow-frontend@sha256:97a84226f1c9f77871c05cebaae1fa42b7525644edc844bccf75e29518ba44c1': 'sha256:97a84226f1c9f77871c05cebaae1fa42b7525644edc844bccf75e29518ba44c1', 'deerflow.local/deer-flow-frontend:20261102-usage-monitor-r2': 'sha256:f79aa01640d41beae2c249ef03accaaf64563ad45788cb7c97992a73dc11c3d2', 'deerflow.local/deer-flow-frontend@sha256:f79aa01640d41beae2c249ef03accaaf64563ad45788cb7c97992a73dc11c3d2': 'sha256:f79aa01640d41beae2c249ef03accaaf64563ad45788cb7c97992a73dc11c3d2', 'deerflow.local/deer-flow-frontend:20261102-composer-controls-r4': 'sha256:458c15afe850e72ece3451e4ccb22f0349f9b4a03c1c888439a648d9161aa992', 'deerflow.local/deer-flow-frontend@sha256:458c15afe850e72ece3451e4ccb22f0349f9b4a03c1c888439a648d9161aa992': 'sha256:458c15afe850e72ece3451e4ccb22f0349f9b4a03c1c888439a648d9161aa992', 'deerflow.local/deer-flow-frontend:20261102-agent-composer-r2': 'sha256:b27c3d82481566e88613aad15a1033f63ff2c04a646db82cc49c2731cc4c9db6', 'deerflow.local/deer-flow-frontend@sha256:b27c3d82481566e88613aad15a1033f63ff2c04a646db82cc49c2731cc4c9db6': 'sha256:b27c3d82481566e88613aad15a1033f63ff2c04a646db82cc49c2731cc4c9db6', 'deerflow.local/deer-flow-frontend:20261101-service-recovery-core-r1': 'sha256:f53ee1253d827a3542f3cc897e8b1c82f8ccf008ddf86bc0b64d6c1074faf944', 'deerflow.local/deer-flow-frontend@sha256:f53ee1253d827a3542f3cc897e8b1c82f8ccf008ddf86bc0b64d6c1074faf944': 'sha256:f53ee1253d827a3542f3cc897e8b1c82f8ccf008ddf86bc0b64d6c1074faf944', 'deerflow.local/deer-flow-frontend:20261101-service-recovery-r1': 'sha256:1fd0cd93e8234216ed1fff29751983777dcb7fb093625d616903a0b1067257c7', 'deerflow.local/deer-flow-frontend@sha256:1fd0cd93e8234216ed1fff29751983777dcb7fb093625d616903a0b1067257c7': 'sha256:1fd0cd93e8234216ed1fff29751983777dcb7fb093625d616903a0b1067257c7', 'deerflow.local/deer-flow-frontend:20261101-datalake-session-r1': 'sha256:34f1bd6936ac606c7b39992eaff4aadce65a0bae800c60c400f7ebae4f56eb00', 'deerflow.local/deer-flow-frontend@sha256:34f1bd6936ac606c7b39992eaff4aadce65a0bae800c60c400f7ebae4f56eb00': 'sha256:34f1bd6936ac606c7b39992eaff4aadce65a0bae800c60c400f7ebae4f56eb00', 'deerflow.local/deer-flow-frontend:20261031-dashboard-recovery-r1': 'sha256:6fe4c264b3774924eddafa03ec83244c56f1e93af26f13de905308948493b602', 'deerflow.local/deer-flow-frontend@sha256:6fe4c264b3774924eddafa03ec83244c56f1e93af26f13de905308948493b602': 'sha256:6fe4c264b3774924eddafa03ec83244c56f1e93af26f13de905308948493b602', 'deerflow.local/deer-flow-frontend:20261030-mobile-fit-r1': 'sha256:b9476120110c0e0ea0d146267c8e640b2b74ae6e5d951e99519fe754aa2e004e', 'deerflow.local/deer-flow-frontend@sha256:b9476120110c0e0ea0d146267c8e640b2b74ae6e5d951e99519fe754aa2e004e': 'sha256:b9476120110c0e0ea0d146267c8e640b2b74ae6e5d951e99519fe754aa2e004e', 'deerflow.local/deer-flow-frontend:20261024-chat-notice-layout-r1': 'sha256:cf701f067e050f014dc4e0178252070fa22806fddc8c4a085c51049451352298', 'deerflow.local/deer-flow-frontend@sha256:cf701f067e050f014dc4e0178252070fa22806fddc8c4a085c51049451352298': 'sha256:cf701f067e050f014dc4e0178252070fa22806fddc8c4a085c51049451352298', 'deerflow.local/deer-flow-frontend:20261028-frontend-polish-r1': 'sha256:1d0f917d767bcb80e9dbb6e9a6281b6730442511fcb1a1e3a35b203f049daf3b', 'deerflow.local/deer-flow-frontend@sha256:1d0f917d767bcb80e9dbb6e9a6281b6730442511fcb1a1e3a35b203f049daf3b': 'sha256:1d0f917d767bcb80e9dbb6e9a6281b6730442511fcb1a1e3a35b203f049daf3b', 'deerflow.local/deer-flow-frontend:20261028-combined-features-r1': 'sha256:99a1da37496816a6abb0524587069cdf59dcec1bea4d5cc94c58f57c7817bbb2', 'deerflow.local/deer-flow-frontend@sha256:99a1da37496816a6abb0524587069cdf59dcec1bea4d5cc94c58f57c7817bbb2': 'sha256:99a1da37496816a6abb0524587069cdf59dcec1bea4d5cc94c58f57c7817bbb2'}
TAG='deerflow.local/deer-flow-frontend:20261105-sidechat-security-r1'
MANIFEST='sha256:172243f3abbb8c5a2ebff0242d4386f19ef63e9c9c9cffe9b238cbcfe6159ab3'
CONFIG='sha256:0e24234e305f54a1e0aa160281ee7a463925b526ae358fbb66053fefd71ed821'
FINAL_CHAIN='sha256:e32c2f7b2ef3eb868c2becc3ed830171434b1699d41539f5b51ec1db08d736f7'
LAYER_DESCRIPTORS=[{'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:a9986cd6f37dbddae7862a6d4be71683472e7c2ea708e87db14f8a6393c00f00', 'size': 4187659}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:87919ed734a39bc185155d715bdcf2ce6c52d112ff642650d9e0e35502b481e9', 'size': 55602437}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:74849c662da757b89cfe8b5406d7beaaf491727ea6843cbba85b8889d1543956', 'size': 1262880}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:66fa63c7a061939c4346f9c3c1ada45434b276dd4e4274721d6bf13cb8adec6e', 'size': 444}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:4591a96efaeed069f286b92737a33a80828e458531afbba077117241671715a4', 'size': 117}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:8bc60db695b155d35246abf006d587bc6aba198389c9189a73da79bffdf5f41e', 'size': 105924}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:e32b6068f0918a468d56eabe66b5ec75dfc0c0e540d9c39339ab59900cc349d8', 'size': 26532675}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:c967b91af1dc6244e609d6156fc08557c7f930105bc03e22efa5d85ac1495f8d', 'size': 5623056}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:8073d07bf5bad8222343773969e7de3ec32772c0757287bf260b7abbe2d260b0', 'size': 34113897}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:083f0db41168326cebeefb86f768d0b82a0bef8c49c46d6d8239ee2c91a1e766', 'size': 1623}, {'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': 'sha256:4f4fb700ef54461cfa02571ae0db9a0dc1e0cdb5577484a6d75e68dc38e8acc1', 'size': 32}]
DIFF_IDS=['sha256:1b349a3334531b575b01d409540dd8aca14ef29abddacd50f36643d7c949db4b', 'sha256:e684b36a88e4418f68c35cfaab13837b79387cc658521cd4caaf4b5082c1a096', 'sha256:1643ca6117e9649371b9303fbdd663619d725425d3f270fa8bb42358a1026de4', 'sha256:313ad79ffb021b780b84389b9ce101fa9f7e41634299e359ef09d3220b0bf677', 'sha256:b85cffa583671206141c75a1d072c1f86afa5861afb7b25564a4712eace9391e', 'sha256:7815cc4f178793bb239dd2ff6c0486f47eca818895314ca8bd504c894e976210', 'sha256:299b938f4abb3699e5d3c9b37954b31a59eb2255f505f294deb5ed7723e5f3f1', 'sha256:4d616c922d272590e7884b31260a1ea4f32753a8cdc66c84001ec285aee40c3a', 'sha256:13746e7b6e4f2e2e0ad8b8c9bd6eea677455ccd7b791ab22608c42579dd02bd2', 'sha256:b31011a2d79242c35e3efbaead10a13531cbf96cb4c927aba9e67d81953bf437', 'sha256:5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef']
REPO='deerflow.local/deer-flow-frontend'
TEMP_TAG=REPO+':portal-ppt-delta-private-'+PRIOR_OPERATION
CANONICAL=(TAG,REPO+'@'+MANIFEST)
REFERENCES=dict(PROTECTED,**{CANONICAL[0]:MANIFEST,CANONICAL[1]:MANIFEST,TEMP_TAG:MANIFEST})
BLOBS={MANIFEST,CONFIG}|{d['digest'] for d in LAYER_DESCRIPTORS}
EXPECTED_PRIOR_PHASES={'actual_imagefs':5,'budget_committed_chains':4,'budget_content_presence':4,
 'content_closure':2,'content_gc_labels':3,'cri_visibility':2,'image_content_and_unpack':3,
 'image_reference':169,'manifest_readback':1,'config_readback':1,'snapshot_closure':1,
 'phase_a':1,'phase_b':1,'publish_canonical_reference':2,'containerd_server_version':1}

class Refused(Exception):
    pass

def require(ok,code):
    if not ok: raise Refused(code)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')

def parse(raw):
    def pairs(rows):
        out={}
        for k,v in rows:
            require(k not in out,'DUPLICATE_JSON_KEY');out[k]=v
        return out
    value=json.loads(raw.decode('utf-8'),object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(Refused('NONFINITE_JSON')))
    require(isinstance(value,dict),'JSON_OBJECT_REQUIRED')
    return value

def validate_prior(raw):
    require(len(raw)==PRIOR_BYTES and sha(raw)==PRIOR_SHA,'PRIOR_RECEIPT_PIN_CHANGED')
    r=parse(raw)
    require(r.get('schema')=='portal-frontend-ui-cache-preload.v1' and r.get('status')=='INCOMPLETE'
      and r.get('error_code')=='command_limit' and r.get('operation')==PRIOR_OPERATION
      and r.get('hostname')==HOST and r.get('expected_node_uid')==NODE_UID,'PRIOR_IDENTITY_CHANGED')
    require(r.get('manifest_digest')==MANIFEST and r.get('config_digest')==CONFIG
      and r.get('phase_a')==r.get('phase_b')=='PASS' and r.get('image_import_requested') is True,'PRIOR_IMPORT_NOT_COMPLETED')
    require(r.get('transport_pin')==TRANSPORT_PIN,'PRIOR_TRANSPORT_CHANGED')
    require(r.get('references_before')==dict(PROTECTED,**{CANONICAL[0]:None,CANONICAL[1]:None})
      and set(r.get('canonical_names_created',[]))==set(CANONICAL),'PRIOR_REFERENCES_CHANGED')
    verified=r.get('verified_archive',{})
    require(verified.get('all_blob_hashes_verified') is True and verified.get('all_layer_diff_ids_verified') is True
      and verified.get('layers')==len(LAYER_DESCRIPTORS),'PRIOR_ARCHIVE_NOT_VERIFIED')
    commands=r.get('commands',[])
    require(len(commands)==200 and Counter(row.get('phase') for row in commands)==Counter(EXPECTED_PRIOR_PHASES)
      and commands[-1].get('phase')=='image_reference','PRIOR_STOP_LOCATION_CHANGED')
    require(all(row.get('returncode')==0 and row.get('error_code') is None for row in commands),'PRIOR_COMMAND_FAILURE')
    mutations=[row['phase'] for row in commands if row.get('containerd_mutation_requested')]
    require(Counter(mutations)==Counter({'phase_a':1,'phase_b':1,'publish_canonical_reference':2}),'PRIOR_MUTATION_SCOPE_CHANGED')
    return r

def read_prior():
    parent=PRIOR_PATH.parent
    for path in (Path('/var'),Path('/var/lib'),parent):
        info=path.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid==0 and info.st_mode & 0o022==0,'PRIOR_DIRECTORY_UNSAFE')
    fd=os.open(str(PRIOR_PATH),os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        before=os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_uid==0
          and before.st_size==PRIOR_BYTES,'PRIOR_FILE_UNSAFE')
        chunks=[];size=0
        while True:
            data=os.read(fd,65536)
            if not data:break
            size+=len(data);require(size<=PRIOR_BYTES,'PRIOR_SIZE_BOUND');chunks.append(data)
        after=os.fstat(fd)
        require((before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)==
          (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns),'PRIOR_CHANGED_DURING_READ')
        return b''.join(chunks)
    finally:os.close(fd)

def allowed_command(args,is_cri=False):
    if is_cri:
        return args in (['inspecti','--output=json',name] for name in CANONICAL)
    if args in (['content','get',MANIFEST],['content','get',CONFIG],
       ['content','ls','digest=='+MANIFEST],['content','ls','digest=='+CONFIG],
       ['snapshots','--snapshotter','overlayfs','ls'],
       ['content','ls','--quiet']+['digest=='+d for d in sorted(BLOBS)]):return True
    if args in (['images','ls','name=='+name] for name in REFERENCES):return True
    return args in (['images','check','--quiet','--snapshotter','overlayfs','name=='+name] for name in CANONICAL)

class Reader:
    def __init__(self):
        self.ctr=shutil.which('ctr',path=SAFE_PATH);self.cri=shutil.which('crictl',path=SAFE_PATH)
        require(self.ctr and self.cri,'LOCAL_TOOLS_REQUIRED')
        self.started=time.monotonic();self.commands=[]
    def call(self,phase,args,is_cri=False,limit=MIB):
        require(allowed_command(args,is_cri),'READ_ONLY_COMMAND_SCOPE')
        require(len(self.commands)<MAX_COMMANDS and 0<limit<=4*MIB,'READ_COMMAND_BOUND')
        left=TOTAL_SECONDS-(time.monotonic()-self.started);require(left>0,'TOTAL_DEADLINE')
        prefix=([self.cri,'--runtime-endpoint','unix://'+SOCKET,'--image-endpoint','unix://'+SOCKET]
          if is_cri else [self.ctr,'--address',SOCKET,'--namespace','k8s.io'])
        argv=prefix+args
        row={'phase':phase,'argv_sha256':sha(canonical(argv)),'mutation_requested':False}
        self.commands.append(row)
        with tempfile.TemporaryFile() as output,tempfile.TemporaryFile() as errors:
            try:result=subprocess.run(argv,stdout=output,stderr=errors,timeout=min(20,left),check=False)
            except subprocess.TimeoutExpired:raise Refused('READ_COMMAND_TIMEOUT')
            row['exitcode']=result.returncode;row['output_bytes']=output.tell()
            require(result.returncode==0,'READ_COMMAND_FAILED');require(output.tell()<=limit,'READ_OUTPUT_BOUND')
            output.seek(0);raw=output.read(limit+1);row['output_sha256']=sha(raw)
        if len(self.commands)%20==0:
            print(json.dumps({'phase':'VERIFY_IMPORTED_CACHE_READONLY','queries':len(self.commands)}),flush=True)
        return raw

def image_target(raw,name):
    rows=[line.split() for line in raw.decode('utf-8').splitlines() if line.strip()]
    require(rows and rows[0][:3]==['REF','TYPE','DIGEST'] and len(rows)==2 and len(rows[1])>=3
      and rows[1][0]==name,'IMAGE_REFERENCE_SHAPE')
    return rows[1][2]

def labels(raw,digest):
    rows=[line.split() for line in raw.decode('utf-8').splitlines() if line.strip()]
    require(len(rows)==2 and rows[0]==['DIGEST','SIZE','AGE','LABELS'] and len(rows[1])>=4 and rows[1][0]==digest,'CONTENT_LABEL_SHAPE')
    result={}
    for token in rows[1][-1].split(','):
        if token=='-':continue
        require('=' in token,'CONTENT_LABEL_TOKEN');key,value=token.split('=',1)
        require(key not in result,'DUPLICATE_CONTENT_LABEL');result[key]=value
    return result

def chains():
    result=[];parent=''
    for diff in DIFF_IDS:
        key=diff if not parent else 'sha256:'+sha((parent+' '+diff).encode('ascii'))
        result.append({'chain_id':key,'parent':parent});parent=key
    require(parent==FINAL_CHAIN,'SEALED_CHAIN_CHANGED')
    return result

def verify_snapshots(raw):
    rows=raw.decode('utf-8').splitlines()
    require(rows and rows[0].split()==['KEY','PARENT','KIND'] and len(rows)<=20001,'SNAPSHOT_TABLE_SHAPE')
    wanted={x['chain_id']:x['parent'] for x in chains()};found={}
    for line in rows[1:]:
        words=line.split()
        if not words or words[0] not in wanted:continue
        require(len(words) in (2,3) and words[0] not in found,'SNAPSHOT_DUPLICATE_OR_SHAPE')
        parent,kind=('',words[1]) if len(words)==2 else (words[1],words[2])
        require(parent==wanted[words[0]] and kind=='Committed','SNAPSHOT_NOT_COMMITTED')
        found[words[0]]=parent
    require(found==wanted,'SNAPSHOT_CHAIN_MISSING')

def verify_cache(reader,prior):
    def references(phase):
        result={}
        for name,digest in REFERENCES.items():
            result[name]=image_target(reader.call(phase,['images','ls','name=='+name]),name)
            require(result[name]==digest,'IMAGE_REFERENCE_CHANGED')
        return result
    before=references('references_before')
    manifest_raw=reader.call('manifest_readback',['content','get',MANIFEST])
    config_raw=reader.call('config_readback',['content','get',CONFIG])
    require(sha(manifest_raw)==MANIFEST[7:] and sha(config_raw)==CONFIG[7:],'CACHED_METADATA_DIGEST')
    manifest=parse(manifest_raw);config=parse(config_raw)
    require(manifest.get('layers')==LAYER_DESCRIPTORS and manifest.get('config',{}).get('digest')==CONFIG,'SEALED_MANIFEST_CHANGED')
    require(config.get('architecture')=='arm64' and config.get('os')=='linux' and config.get('rootfs',{}).get('diff_ids')==DIFF_IDS,'SEALED_CONFIG_CHANGED')
    present=reader.call('content_closure',['content','ls','--quiet']+['digest=='+d for d in sorted(BLOBS)]).decode('ascii').splitlines()
    require(len(present)==len(set(present)) and set(present)==BLOBS,'CONTENT_CLOSURE_MISSING')
    gc=labels(reader.call('manifest_gc',['content','ls','digest=='+MANIFEST]),MANIFEST)
    expected={'containerd.io/gc.ref.content.config':CONFIG}
    expected.update({'containerd.io/gc.ref.content.l.'+str(i):d['digest'] for i,d in enumerate(LAYER_DESCRIPTORS)})
    require(all(gc.get(k)==v for k,v in expected.items()),'MANIFEST_GC_CLOSURE_MISSING')
    gc=labels(reader.call('config_gc',['content','ls','digest=='+CONFIG]),CONFIG)
    require(gc.get('containerd.io/gc.ref.snapshot.overlayfs')==FINAL_CHAIN,'CONFIG_SNAPSHOT_GC_MISSING')
    verify_snapshots(reader.call('snapshot_closure',['snapshots','--snapshotter','overlayfs','ls'],limit=4*MIB))
    for name in CANONICAL:
        ready=reader.call('image_content_and_unpack',['images','check','--quiet','--snapshotter','overlayfs','name=='+name])
        require(ready.decode('utf-8').splitlines()==[name],'IMAGE_CONTENT_OR_UNPACK_NOT_READY')
        status=parse(reader.call('cri_visibility',['inspecti','--output=json',name],is_cri=True,limit=4*MIB)).get('status',{})
        require(status.get('id')==CONFIG and TAG in status.get('repoTags',[]) and CANONICAL[1] in status.get('repoDigests',[]),'CRI_IDENTITY_CHANGED')
    after=references('references_after')
    require(after==before and reader.call('stable_manifest',['content','get',MANIFEST])==manifest_raw
      and reader.call('stable_config',['content','get',CONFIG])==config_raw,'CACHE_CHANGED_DURING_READ')
    return {'content_and_unpack':'PASS','cri_visibility':'PASS','old_references_preserved':True,
      'protected_reference_count':len(PROTECTED),'snapshot_chains_verified':len(DIFF_IDS),'content_blobs_present':len(BLOBS),
      'layer_payloads_rehashed_now':False,'layer_hash_basis':'sealed_original_import_receipt_and_fresh_containerd_closure',
      'storage_capacity_rechecked':False,'stable_readback':True,'temporary_reference_preserved':True}

def persist_report(out):
    parent=REPORT_PARENT.lstat()
    require(stat.S_ISDIR(parent.st_mode) and parent.st_uid==0 and parent.st_mode&0o022==0,'REPORT_PARENT_UNSAFE')
    directory=REPORT_PARENT/(REPORT_PREFIX+out['operation'])
    directory.mkdir(mode=0o700)
    data=canonical(dict(out,report_directory_created=True))+b'\n';require(len(data)<=65536,'REPORT_SIZE_BOUND')
    fd=os.open(str(directory/'REPORT.json'),os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
    except BaseException:raise
    return {'report_directory_created':True,'report_path':str(directory/'REPORT.json'),'report_bytes':len(data),'report_sha256':sha(data)}

def run():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--verify-imported-cache-readonly',required=True,action='store_true');parser.parse_args()
    out={'schema':'portal-frontend-sidechat-cache-recheck.v1','status':'INCOMPLETE','operation':uuid.uuid4().hex,
      'automatic_retry':False,'image_import_requested':False,'image_retag_requested':False,'image_deleted':False,
      'production_workload_mutation':False,'configuration_modified':False,'database_access':False,'provider_requests':0,
      'ready_for_rollout':False,'kubernetes_node_uid_freshly_checked':False,'expected_node_uid':NODE_UID,
      'manifest_digest':MANIFEST,'config_digest':CONFIG,'original_receipt_modified':False,'original_marker_preserved':True,
      'raw_errors_exported':False,'report_directory_created':False}
    reader=None;prior_raw=None;started=time.monotonic();step='host_guard';host_ok=False
    try:
        require(os.name=='posix' and os.geteuid()==0 and os.uname().nodename.split('.')[0]==HOST
          and os.uname().machine in ('arm64','aarch64') and sys.flags.isolated and sys.flags.dont_write_bytecode,'DATAX02_ISOLATED_ROOT_REQUIRED')
        host_ok=True;out['hostname']=HOST;step='validate_prior_receipt'
        prior_raw=read_prior();prior=validate_prior(prior_raw)
        out['prior_receipt']={'path':str(PRIOR_PATH),'bytes':PRIOR_BYTES,'sha256':PRIOR_SHA,'operation':PRIOR_OPERATION,
          'status':'INCOMPLETE','error_code':'command_limit','commands':200,'verified_stop':'final_reference_readback'}
        reader=Reader();step='verify_imported_cache'
        out.update(verify_cache(reader,prior));step='stable_prior_receipt'
        require(read_prior()==prior_raw,'PRIOR_RECEIPT_CHANGED_DURING_CHECK')
        out['status']='FRONTEND_SIDECHAT_CACHE_RECHECK_ACCEPTED_NOT_DEPLOYED'
    except BaseException as error:
        code=str(error) if isinstance(error,Refused) else 'LOCAL_READ_FAILURE'
        out.update(error_class=type(error).__name__,error_code=code)
    out.update(last_step=step,elapsed_seconds=round(time.monotonic()-started,3),observed_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
      queries=len(reader.commands) if reader else 0)
    if reader:out['commands']=reader.commands
    if host_ok and prior_raw is not None:
        try:
            out.update(persist_report(out))
        except BaseException:
            out.update(status='INCOMPLETE',error_code='REPORT_PERSISTENCE_FAILED')
    out['exitcode']=0 if out['status']=='FRONTEND_SIDECHAT_CACHE_RECHECK_ACCEPTED_NOT_DEPLOYED' else 1
    print(json.dumps({k:v for k,v in out.items() if k!='commands'},sort_keys=True),flush=True)
    return out['exitcode']

if __name__=='__main__':raise SystemExit(run())

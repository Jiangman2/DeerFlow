#!/usr/bin/env python3
"""Fixed datax-02 read-only inventory. This never authorizes image deletion."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time
import types
import zlib

GUARD_SHA = '81c069d566023c68301498742906cfc5f78207e70a8e0d03ef0d0632ff7d2e2d'
GUARD_Z64 = '@@GUARD_Z64@@'
OP = 'abf96dde50174b1eb7caf70a4e6711b1'
PLAN_SHA = 'b3f08cc0ca0f8cb75488ce37a26531071699f5eb5d9097687604851753d72aa9'
FAIL_SHA = '4a9f9824ca153f962a8d7191342811b03f8d6c9ef799b2f388ca51b5f1d9833a'
PRIOR_REPORT_SHA = '3b1c3d86982efd87a5322392276d49170ee50d547f103e61d4c2ef172718af16'
PROTECTED_HISTORY = @@PROTECTED_HISTORY@@
LIMIT = 8 * 1024 ** 2


def require(value, code):
    if not value:raise ValueError(code)


def sha(raw):return hashlib.sha256(raw).hexdigest()


def number(value):
    require((type(value) is int or isinstance(value,str) and re.fullmatch('[0-9]{1,20}',value))
            and 0 <= int(value) <= 2**60, 'invalid_numeric_metadata')
    return int(value)


def image_id(value):
    require(isinstance(value,str) and re.fullmatch('(?:sha256:)?[a-f0-9]{64}',value), 'invalid_image_identity')
    return value if value.startswith('sha256:') else 'sha256:'+value


def object_id(value):
    require(isinstance(value,str) and re.fullmatch('[a-f0-9]{64}',value), 'invalid_container_identity')
    return value


def reference(value):
    require(isinstance(value,str) and len(value)<=512 and '://' not in value
            and re.fullmatch('[A-Za-z0-9][A-Za-z0-9._:/-]*(?:@sha256:[a-f0-9]{64})?',value), 'invalid_image_reference')
    return value


def references(value):
    if value is None:value=[]
    require(isinstance(value,list) and len(value)<=256, 'invalid_reference_list')
    return sorted(set(reference(item) for item in value))


def project_images(value):
    rows=value.get('images');require(isinstance(rows,list) and len(rows)<=4096,'invalid_cri_images')
    return [dict(id=image_id(row['id']),repoTags=references(row.get('repoTags',[])),
                 repoDigests=references(row.get('repoDigests',[])),size_bytes=number(row['size'])) for row in rows]


def project_containers(value):
    rows=value.get('containers');require(isinstance(rows,list) and len(rows)<=8192,'invalid_cri_containers')
    result=[]
    for row in rows:
        state=row['state'];require(state in ('CONTAINER_CREATED','CONTAINER_RUNNING','CONTAINER_EXITED','CONTAINER_UNKNOWN'),'invalid_container_state')
        image=row['image'];require(isinstance(image,dict),'invalid_container_image')
        result.append(dict(id=object_id(row['id']),image=reference(image['image']),imageRef=reference(row['imageRef']),
                           state=state,podSandboxId=object_id(row['podSandboxId'])))
    return result


def project_ctr(raw):
    lines=raw.decode('utf-8').splitlines()
    require(lines and lines[0].split()[:3]==['REF','TYPE','DIGEST'] and len(lines)<=8193,'invalid_ctr_header')
    result=[]
    for line in lines[1:]:
        words=line.split();require(len(words)>=3,'invalid_ctr_row')
        require(words[1] in ('application/vnd.oci.image.manifest.v1+json','application/vnd.oci.image.index.v1+json',
                            'application/vnd.docker.distribution.manifest.v2+json','application/vnd.docker.distribution.manifest.list.v2+json'), 'invalid_image_media_type')
        result.append(dict(reference=reference(words[0]),media_type=words[1],digest=image_id(words[2])))
    return result


def project_namespaces(raw):
    names=raw.decode('ascii').split()
    require(len(names)<=128 and len(names)==len(set(names))
            and all(re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',name) for name in names),'invalid_namespace_projection')
    return sorted(names)


def filesystem(module,path):
    fd=module.B.open_anchored(Path(path))
    try:
        value,space=os.fstat(fd),os.fstatvfs(fd)
        require(stat.S_ISDIR(value.st_mode),'filesystem_not_directory')
        return dict(path=path,device=value.st_dev,uid=value.st_uid,gid=value.st_gid,mode=oct(stat.S_IMODE(value.st_mode)),
                    capacity_bytes=space.f_blocks*space.f_frsize,available_bytes=space.f_bavail*space.f_frsize,
                    available_inodes=space.f_favail,block_bytes=space.f_bsize,fragment_bytes=space.f_frsize,
                    read_only=bool(space.f_flag&os.ST_RDONLY))
    finally:os.close(fd)


def checkpoint_projection(value):
    result={}
    for name in ('operation','role','phase','status','error_code','phase_a','phase_b'):
        if name in value:
            item=value[name]
            require(isinstance(item,str) and re.fullmatch('[A-Za-z0-9_.-]{1,128}',item),'invalid_checkpoint_label')
            result[name]=item
    for name in ('automatic_retry','target_import_requested','image_import_may_be_partial','all_fixed_outer_blobs_present','all_refs_and_config_bound','all_six_complete'):
        if name in value:
            require(type(value[name]) is bool,'invalid_checkpoint_flag');result[name]=value[name]
    for name in ('archive_bytes','metadata_only_bytes','returncode','stdout_bytes','stderr_bytes'):
        if name in value:result[name]=number(value[name])
    if 'archive_sha256' in value:
        require(isinstance(value['archive_sha256'],str) and re.fullmatch('[a-f0-9]{64}',value['archive_sha256']),'invalid_checkpoint_sha')
        result['archive_sha256']=value['archive_sha256']
    return result


def checkpoints(module,profile):
    target=module.state_paths(OP)['target'];module.B.safe_dir(target)
    present=set(os.listdir(str(target)))
    names={'STARTED.json','PREPARED.json','RUN-REQUESTED.json','FAIL.json','REPORT.json',
           'PHASE-A-REQUESTED.json','PHASE-A-PASS.json','PHASE-B-REQUESTED.json','PHASE-B-PASS.json'}
    for row in profile['images']:
        names.add(row['role']+'.SEALED.json')
        for phase in ('PHASE-A','PHASE-B'):
            for state in ('REQUESTED','PASS'):names.add(row['role']+'.'+phase+'-'+state+'.json')
    required={'STARTED.json','PREPARED.json','RUN-REQUESTED.json','FAIL.json','PHASE-A-REQUESTED.json','PHASE-A-PASS.json'}
    for row in profile['images']:
        required.update(row['role']+suffix for suffix in ('.SEALED.json','.PHASE-A-REQUESTED.json','.PHASE-A-PASS.json'))
    result=dict(files=[],unexpected_names_sha256=sorted(sha(name.encode()) for name in present-names),
                any_phase_b_receipt=any('PHASE-B' in name for name in present),
                expected_state='PHASE_A_COMPLETE_PHASE_B_NOT_REQUESTED',state_unchanged=False)
    for name in sorted(names):
        row=dict(name=name,present=name in present)
        if row['present']:
            raw=module.private_bytes(target/name,1024**2)
            row.update(bytes=len(raw),sha256=sha(raw),safe=checkpoint_projection(module.B.parse(raw)))
        result['files'].append(row)
    hashes={row['name']:row.get('sha256') for row in result['files']}
    result['state_unchanged']=present==required and hashes.get('FAIL.json')==FAIL_SHA
    result['expected_checkpoint_file_count']=24
    result['actual_checkpoint_file_count']=len(present)
    result['phase_a_pass_receipt_present']='PHASE-A-PASS.json' in present
    sealed=Path('/opt')/module.state_paths(OP)['sealed_name']
    fd=module.B.open_anchored(sealed)
    try:
        value=os.fstat(fd)
        result['sealed_directory']=dict(display_path=str(sealed),device=value.st_dev,inode=value.st_ino,uid=value.st_uid,
                                        mode=oct(stat.S_IMODE(value.st_mode)),expected_device=64785,expected_inode=14340598,
                                        identity_matches=(value.st_dev,value.st_ino,value.st_uid,stat.S_IMODE(value.st_mode))==(64785,14340598,0,0o700))
    finally:os.close(fd)
    wire=module.private_bytes(module.state_paths(OP)['control']/'PLAN-WIRE.json',1024**2)
    result['plan_wire']=dict(bytes=len(wire),sha256=sha(wire),matches_accepted=len(wire)==122307 and sha(wire)==PLAN_SHA)
    if result['plan_wire']['matches_accepted']:
        module.validate_plan(profile,module.unwire_plan(module.B.parse(wire)))
        result['plan_wire']['fixed_image_structure_valid']=True
    return result


def safe_error(exc):
    if isinstance(exc,KeyboardInterrupt):return 'COLLECTION_INTERRUPTED'
    code=str(exc) if isinstance(exc,ValueError) else getattr(exc,'code','READ_ONLY_STEP_FAILED')
    return code if isinstance(code,str) and re.fullmatch('[A-Za-z0-9_.-]{1,128}',code) else 'READ_ONLY_STEP_FAILED'


def command_step(module,report,name,argv,parser,json_input=False):
    step=dict(name=name,status='NOT_COLLECTED');report['steps'].append(step)
    try:
        rc,raw=module.B.run(argv,'READ_ONLY_'+name,timeout=30,limit=LIMIT,allow_failure=True)
        step.update(returncode=rc,stdout_bytes=len(raw))
        require(rc==0,'COMMAND_NONZERO_EXIT')
        report[name]=parser(module.B.parse(raw) if json_input else raw)
        step['status']='COMPLETE'
    except Exception as exc:step['error_code']=safe_error(exc)


def report_bytes(report):
    raw=(json.dumps(report,sort_keys=True,indent=2)+'\n').encode()
    if len(raw)>LIMIT:
        report['status']='PARTIAL_REVIEW_REQUIRED'
        report['output_limit_observation']=dict(projected_bytes=len(raw),projected_sha256=sha(raw),limit=LIMIT)
        for name in ('cri_images','cri_containers','containerd_images','containerd_namespaces'):
            if name in report:report[name]=dict(status='OMITTED_REPORT_SIZE_LIMIT',row_count=len(report[name]))
        raw=(json.dumps(report,sort_keys=True,indent=2)+'\n').encode()
    require(len(raw)<=LIMIT,'safe_report_size_limit')
    return raw


def run():
    module=None;report=dict(schema='portal-native-image-archive-inventory.v1',operation=OP,status='INCOMPLETE',
        collected_at_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
        scope='LOCAL_DATAx02_CANDIDATE_INVENTORY_NOT_UNUSED_OR_DELETION_AUTHORIZATION',steps=[],
        prior_report_sha256=PRIOR_REPORT_SHA,expected_target_fail_sha256=FAIL_SHA,
        production_mutation=False,image_export=False,image_import=False,image_delete=False,image_prune=False,
        workload_mutation=False,database_access=False,model_access=False,ssh_scp=False,
        archive_payload_reads=False,raw_environment_exported=False,ready_for_deletion=False,ready_for_rollout=False,
        missing_proofs=['Fresh cluster Pod/controller desired images and explicit rollback retention are not collected.',
                        'Other containerd namespaces and lease/content sharing are not comprehensively reconciled.',
                        'Reported image sizes are not reclaimable disk bytes; aliases and shared layers can retain all data.'])
    rc=1
    try:
        def interrupted(signum,frame):raise KeyboardInterrupt()
        for sig in (signal.SIGTERM,signal.SIGHUP):signal.signal(sig,interrupted)
        raw=zlib.decompress(base64.b64decode(GUARD_Z64,validate=True));require(sha(raw)==GUARD_SHA,'embedded_guard_changed')
        guard=types.ModuleType('fixed_guard_readonly_helpers');exec(compile(raw,'fixed_guard_readonly_helpers.py','exec'),guard.__dict__)
        report['host_identity']=guard.host_identity();module,profile=guard.load_target()
        module.B.DEADLINE=time.monotonic()+180
        report['protected_images']=dict(current_candidates=[{k:row[k] for k in ('role','tag','digest_reference','manifest_digest','config_digest')} for row in profile['images']],
                                        historical_inputs=PROTECTED_HISTORY,scope='PROTECTION_HINTS_NOT_EXHAUSTIVE_CURRENT_USAGE')
        for name,path in (('root_filesystem','/var/lib'),('opt_filesystem','/opt')):
            try:report[name]=filesystem(module,path)
            except Exception as exc:report['steps'].append(dict(name=name,status='NOT_COLLECTED',error_code=safe_error(exc)))
        try:report['checkpoint']=checkpoints(module,profile)
        except Exception as exc:report['steps'].append(dict(name='checkpoint',status='NOT_COLLECTED',error_code=safe_error(exc)))
        command_step(module,report,'cri_images',module.B.CRI+['images','-o','json'],project_images,True)
        command_step(module,report,'cri_containers',module.B.CRI+['ps','-a','-o','json'],project_containers,True)
        command_step(module,report,'containerd_images',module.B.CTR+['images','ls'],project_ctr)
        command_step(module,report,'containerd_namespaces',module.B.CTR+['namespaces','list','--quiet'],project_namespaces)
        report['status']='COMPLETE_REVIEW_REQUIRED' if all(s['status']=='COMPLETE' for s in report['steps']) else 'PARTIAL_REVIEW_REQUIRED'
        rc=0 if report['status']=='COMPLETE_REVIEW_REQUIRED' else 1
    except (Exception,KeyboardInterrupt) as exc:report['error_code']=safe_error(exc)
    raw=report_bytes(report)
    if report['status']!='COMPLETE_REVIEW_REQUIRED':rc=1
    summary=dict(status=report['status'],report_sha256=sha(raw),report_bytes=len(raw),exitcode=rc,ready_for_deletion=False)
    try:
        require(module is not None,'handoff_unavailable')
        summary['download_report']=module.handoff_report(raw)
    except Exception:
        summary['download_report_status']='UNAVAILABLE_SAFE_REPORT_PRINTED'
        sys.stdout.buffer.write(raw);sys.stdout.buffer.flush()
    print(json.dumps(summary,sort_keys=True),flush=True)
    return rc


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--collect',action='store_true',required=True);parser.parse_args()
    raise SystemExit(run())

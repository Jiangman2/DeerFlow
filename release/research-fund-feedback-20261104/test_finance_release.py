"""Exercise the sealed package on private directories, never production paths."""
import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import tarfile
import time
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent

def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value

@pytest.fixture(scope='module')
def host():
    return module('finance_release', HERE/'update-portal-research-fund-skill-20261104-r1.py')

@pytest.fixture(scope='module')
def staged(tmp_path_factory,host):
    remote=module('finance_maintenance',HERE/'maintenance-remote-20261104.py')
    root=tmp_path_factory.mktemp('finance-release')/'skills'
    (root/'public').mkdir(parents=True)
    (root/'public'/'unrelated-skill').mkdir()
    (root/'public'/'unrelated-skill'/'keep').write_text('unchanged')
    remote.ROOT=root
    old=json.loads((HERE/'old-bundle.json').read_text(encoding='utf-8'))
    remote.unpack(base64.b64decode(old['archive_b64']),host.BUNDLE['before'],root/'public'/remote.NAME)
    request={'mode':'stage','operation':'c'*32,'before':host.BUNDLE['before'],
             'after':host.BUNDLE['after'],'archive_b64':host.BUNDLE['archive_b64'],
             'archive_sha256':host.BUNDLE['archive_sha256']}
    report=remote.stage(request)
    return root,request,report

@pytest.fixture
def setup(tmp_path,staged):
    root,request,report=staged
    copy=tmp_path/'skills'
    shutil.copytree(root,copy)
    remote=module('finance_maintenance_case',HERE/'maintenance-remote-20261104.py')
    remote.ROOT=copy
    return remote,dict(request),report

def test_stage_qualifies_three_profiles_and_all_feedback(staged):
    _,_,report=staged
    assert report['status']=='STAGED_SYNTHETIC_ACCEPTED'
    assert len(report['synthetic_checks'])==10
    assert sum(x.get('passed',1) for x in report['synthetic_checks'])==39
    assert report['backup_verified']

def force_two_renames(remote,request):
    tx=remote.transaction(request['operation'])
    report=json.loads((tx/'JOURNAL.json').read_bytes())
    report['exchange_supported']=False
    remote.save_journal(tx,report)
    return tx

def test_publish_retains_backup_and_other_skill(setup):
    remote,request,_=setup
    tx=force_two_renames(remote,request)
    report=remote.publish(request)
    assert report['switch_strategy']=='TWO_RENAMES_WITH_SHORT_NAME_GAP'
    assert report['status']=='SKILL_PACKAGE_UPDATED'
    remote.verify(remote.ROOT/'public'/remote.NAME,request['after'])
    remote.verify(tx/'backup',request['before'])
    remote.verify(tx/'previous-live',request['before'])
    assert (remote.ROOT/'public'/'unrelated-skill'/'keep').read_text()=='unchanged'
    with pytest.raises(remote.Refused,match='STAGE_NOT_ACCEPTED'):
        remote.publish(request)

@pytest.mark.parametrize('which',['live','candidate','backup'])
def test_changed_files_block_switch(setup,which):
    remote,request,_=setup
    tx=remote.transaction(request['operation'])
    path=remote.ROOT/'public'/remote.NAME if which=='live' else tx/which
    (path/'SKILL.md').write_bytes(b'changed')
    with pytest.raises(remote.Refused,match='MANIFEST_CHANGED'):
        remote.publish(request)
    assert not (tx/'previous-live').exists()

def test_failed_second_rename_restores_original_name(setup,monkeypatch):
    remote,request,_=setup
    tx=force_two_renames(remote,request)
    original=os.rename
    def fail_candidate(source,target):
        if Path(source)==tx/'candidate':
            raise OSError('simulated rename failure')
        original(source,target)
    monkeypatch.setattr(remote.os,'rename',fail_candidate)
    with pytest.raises(remote.Refused,match='SECOND_RENAME_FAILED_LIVE_NAME_RESTORED'):
        remote.publish(request)
    remote.verify(remote.ROOT/'public'/remote.NAME,request['before'])
    remote.verify(tx/'backup',request['before'])

@pytest.mark.skipif(os.name=='nt',reason='Linux symlink and NFS deployment path')
def test_symlink_rejected(setup,tmp_path):
    remote,request,_=setup
    candidate=remote.transaction(request['operation'])/'candidate'/'SKILL.md'
    candidate.unlink()
    other=tmp_path/'outside'
    other.write_text('outside')
    candidate.symlink_to(other)
    with pytest.raises(remote.Refused,match='SYMLINK_REFUSED'):
        remote.publish(request)

@pytest.mark.parametrize('attack',['traversal','symlink','unexpected-file'])
def test_archive_boundaries(setup,tmp_path,attack):
    remote,request,_=setup
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w:gz') as tar:
        name=remote.NAME+'/../escape' if attack=='traversal' else remote.NAME+'/unexpected'
        member=tarfile.TarInfo(name)
        if attack=='symlink':
            member.type=tarfile.SYMTYPE;member.linkname='/tmp/elsewhere'
            tar.addfile(member)
        else:
            member.size=1;tar.addfile(member,io.BytesIO(b'x'))
    with pytest.raises(remote.Refused):
        remote.unpack(stream.getvalue(),request['after'],remote.ROOT/'bad-candidate')
    assert not (remote.ROOT/'bad-candidate').exists()

def test_archive_tampering_fails_before_stage(setup):
    remote,request,_=setup
    request['operation']='d'*32
    request['archive_b64']=base64.b64encode(b'invalid').decode()
    with pytest.raises(remote.Refused,match='ARCHIVE_DIGEST_MISMATCH'):
        remote.stage(request)
    assert not remote.transaction(request['operation']).exists()

def test_cleanup_accepts_successful_kubectl_text_without_json(host,monkeypatch):
    host.STARTED=time.monotonic();host.COMMANDS=0
    monkeypatch.setattr(host.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=b'pod deleted\n',stderr=b''))
    assert host.command(['delete','pod','synthetic'])=={'delete_acknowledged':True}

def test_cleanup_failure_remains_failure(host,monkeypatch):
    host.STARTED=time.monotonic();host.COMMANDS=0
    monkeypatch.setattr(host.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stdout=b'',stderr=b'failure'))
    with pytest.raises(host.Refused,match='KUBERNETES_COMMAND_NOT_ACCEPTED'):
        host.command(['delete','pod','synthetic'])

@pytest.mark.parametrize('verb',['patch','apply','rollout','scale','replace'])
def test_workload_modification_verbs_forbidden(host,verb):
    host.STARTED=time.monotonic();host.COMMANDS=0
    with pytest.raises(host.Refused,match='UNEXPECTED_COMMAND'):
        host.command([verb,'deployment','gateway'])

def test_maintenance_pod_never_pulls_and_has_no_production_configuration(host):
    seed={'node':'synthetic-node','image':'synthetic@sha256:'+'0'*64}
    pod=host.maintenance_pod('synthetic','a'*32,seed)
    spec=pod['spec'];c=spec['containers'][0]
    assert spec['restartPolicy']=='Never' and spec['automountServiceAccountToken'] is False
    assert c['imagePullPolicy']=='Never' and 'env' not in c and 'envFrom' not in c
    assert spec['volumes']==[{'name':'skills','persistentVolumeClaim':{'claimName':'deer-flow-skills-rwx-pvc'}}]

def test_wrong_host_before_commands_or_file_write(host,monkeypatch,capsys):
    host.COMMANDS=0
    monkeypatch.setattr(host.sys,'argv',['release','--update-research-fund-skill-once'])
    monkeypatch.setattr(host.socket,'gethostname',lambda:'not-production')
    assert host.main()==1
    report=json.loads(capsys.readouterr().out)
    assert report['error_code']=='WRONG_HOST' and report['read_and_maintenance_commands']==0
    assert report['skill_switch_requested'] is False

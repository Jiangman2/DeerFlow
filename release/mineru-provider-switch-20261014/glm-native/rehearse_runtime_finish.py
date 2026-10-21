"""Linux-root synthetic recovery fixture. Does NOT qualify a real candidate image."""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import prepare_runtime_finish as r
import package_runtime_finish as publisher
import test_runtime_finish


def main():
    assert os.geteuid() == 0
    os.umask(0o077)
    root=Path('/root/runtime-rehearsal'); root.mkdir(mode=0o700)
    old=root/'old'; old.mkdir(); (old/'package').mkdir(); (old/'build1').mkdir()
    context=old/'package/context.tar'; fakezip=io.BytesIO()
    with zipfile.ZipFile(fakezip,'w') as z:
        for name in r.DICT_NAMES:
            z.writestr(r.ZIP_PREFIX+name, b'synthetic dictionary '+name.encode())
    rows={'source/SOURCE.SHA256':b'locked-synthetic-source', 'offline/'+r.ZIP_NAME:fakezip.getvalue(),
          'offline/INPUTS.SHA256':(hashlib.sha256(fakezip.getvalue()).hexdigest()+'  '+r.ZIP_NAME+'\n').encode()}
    with tarfile.open(context,'w') as t:
        for name, raw in rows.items(): r.safe.add(t,name,raw=raw)
    r.safe.record(old/'package/BUILD-PLAN.json', {'context_sha256':r.safe.digest(context), 'scope':'TEST_FIXTURE'})
    r.safe.write_new(old/'package/verify_native_candidate.py',b'# synthetic helper preserved byte for byte\n')
    r.safe.write_new(old/'package/PACKAGE.SHA256',''.join(r.safe.digest(p)+'  '+p.name+'\n'
        for p in sorted((old/'package').iterdir())).encode())
    r.safe.record(old/'build1/BUILD-FAILED.json',{'stage':'compile-and-runtime-check',
                                               'images_imported':False,'production_writes':0})
    r.safe.write_new(old/'build1/05-compile-and-runtime-check.log',
        b'offline_native_qualification=PASS provider_requests=0\n[runtime-check 4/5] exit code: 2\n')
    snapshots=old/'build1/buildkit/runc-overlayfs/snapshots/snapshots'
    fs=snapshots/'40/fs'; (fs/'out/evidence').mkdir(parents=True)
    hashes={}
    for name in ('WeKnora','weknora-anydoc-probe','weknora-glm-probe'):
        raw=b'synthetic non-executable binary '+name.encode()
        r.safe.write_new(fs/'out'/name,raw); hashes[name]=hashlib.sha256(raw).hexdigest()
    for name in r.PROOF: r.safe.write_new(fs/'out/evidence'/name,b'synthetic evidence')
    (fs/'out/evidence/source.sha256').write_bytes(rows['source/SOURCE.SHA256'])
    (fs/'out/evidence/binaries.sha256').write_text(''.join(sha+'  /out/'+name+'\n' for name,sha in hashes.items()))
    err=snapshots/'41/fs/tmp/weknora-glm-qualification/glm-help.txt'; err.parent.mkdir(parents=True)
    r.safe.write_new(err,b'panic: Dictionary file does not exist: synthetic-test\n')
    # Overrides are restricted to this local test process. Published module stays pinned.
    r.OLD=old; r.PREPARED=root/'PREPARED.json'; r.ERROR_HASH=r.safe.digest(err)
    r.safe.record(r.PREPARED,{'build_root':str(old),'package_manifest_sha256':r.safe.digest(old/'package/PACKAGE.SHA256'),
                            'context_sha256':r.safe.digest(context)})
    payload=root/'tool'; payload.mkdir()
    for name in ('runtime_check.py','Dockerfile.runtime-finish','finish_runtime_build.py','verify_runtime_candidate.py'):
        r.safe.write_new(payload/name,(publisher.HERE/name).read_bytes())
    r.safe.record(payload/'LOCAL-VERIFICATION.json',{'scope':'TEST_ONLY'})
    r.HERE=payload
    before={str(p.relative_to(old)):r.safe.digest(p) for p in old.rglob('*') if p.is_file()}
    result=r.prepare(root/'rebuild')
    after={str(p.relative_to(old)):r.safe.digest(p) for p in old.rglob('*') if p.is_file()}
    assert before==after
    with tarfile.open(root/'rebuild/package/context.tar','r:') as t:
        members=t.getmembers()
        assert all(m.isfile() and m.mode==0o600 and m.uid==m.gid==0 for m in members)
        assert len([m for m in members if m.name.startswith('payload/')])==3
        assert len([m for m in members if m.name.startswith('dict/')])==5
        assert not any('go-proxy' in m.name or 'build_native.sh' in m.name for m in members)
    suite=unittest.defaultTestLoader.loadTestsFromNames(('test_runtime_finish', 'test_dwarf_build_fix',
        'test_sqlite_build_fix', 'test_native_build', 'test_unpack_rust'))
    with (root/'tests.txt').open('w') as out:
        tests=unittest.TextTestRunner(stream=out,verbosity=2).run(suite)
    assert tests.wasSuccessful() and tests.testsRun==49 and not tests.skipped
    proof={'local_tool_tests':'PASS','tests':tests.testsRun,'linux_packet_rehearsal':'PASS',
           'recovery_fixture':'SYNTHETIC_NOT_REAL_BINARY', 'original_inputs_unchanged':True,
           'native_candidate_runtime':'NOT_TESTED','production_writes':0,'provider_requests':0,
           'ready_for_rollout':False}
    r.safe.record(Path('/out/LOCAL-VERIFICATION.json'),proof)
    publisher.pack(root/'published',Path('/out/LOCAL-VERIFICATION.json'))
    subprocess.run(['bash','-n',str(root/'published/START-weknora-glm-runtime-finish.sh')],check=True)
    # Check the actual compressed packet, regular-file extraction, hashes and -I entrypoint.
    packet=root/'published/weknora-glm-runtime-finish-20261016-r1.tar.gz'
    unpacked=root/'unpacked'; unpacked.mkdir()
    with tarfile.open(packet,'r:gz') as t:
        for m in t:
            assert m.isfile() and '/' not in m.name and not m.linkname and m.mode==0o600
            r.safe.write_new(unpacked/m.name,t.extractfile(m).read())
    r.safe.validate_package(unpacked)
    subprocess.run([sys.executable,'-I','-B',str(unpacked/'prepare_runtime_finish.py'),'--help'],
                   stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
    print(json.dumps(dict(proof,published_packet='PASS',starter_bash_syntax='PASS')),flush=True)


if __name__=='__main__': main()

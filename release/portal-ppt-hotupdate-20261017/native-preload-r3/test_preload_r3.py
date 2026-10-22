"""Small offline fixtures only. Real POSIX FDs; no daemon, SSH or image import."""
import base64
import ast
import copy
import hashlib
import importlib.util
import io
import json
import os
import re
from pathlib import Path
import shutil
import stat
import sys
import tarfile
import tempfile
import time
import types
import unittest
from unittest import mock

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value)
    return value


M=load(HERE/'preload_native_r3_template.py','r3_under_test')
B=load(ROOT/'native-preload-r2/preload_native_r2.py','frozen_r2_under_test')
M.B=B
B.DEADLINE=None


def digest(raw):return 'sha256:'+hashlib.sha256(raw).hexdigest()


def fixture_archive(root,role='frontend'):
    layer=b'fixture layer bytes'
    ld=digest(layer);diff=digest(b'fixture uncompressed')
    config=json.dumps({'architecture':'arm64','os':'linux','config':{'Env':['TOKEN=must-never-appear']},'rootfs':{'type':'layers','diff_ids':[diff]}},separators=(',',':')).encode()
    cd=digest(config)
    manifest=json.dumps({'schemaVersion':2,'config':{'digest':cd,'size':len(config)},'layers':[{'digest':ld,'size':len(layer)}]},separators=(',',':')).encode()
    md=digest(manifest);tag='fixture.invalid/portal:fixed'
    index=json.dumps({'schemaVersion':2,'manifests':[{'digest':md,'size':len(manifest),'annotations':{'io.containerd.image.name':tag,'org.opencontainers.image.ref.name':'fixed'}}]},separators=(',',':')).encode()
    parts={'oci-layout':b'{"imageLayoutVersion":"1.0.0"}','index.json':index,'blobs/sha256/'+md[7:]:manifest,'blobs/sha256/'+cd[7:]:config}
    all_parts=dict(parts);all_parts['blobs/sha256/'+ld[7:]]=layer
    path=root/'fixture.oci.tar'
    with tarfile.open(str(path),'w',format=tarfile.USTAR_FORMAT) as archive:
        for name in ('.','blobs','blobs/sha256'):
            item=tarfile.TarInfo(name);item.type=tarfile.DIRTYPE;item.mode=0o755;archive.addfile(item)
        for name,data in all_parts.items():
            item=tarfile.TarInfo(name);item.size=len(data);item.mode=0o600;archive.addfile(item,io.BytesIO(data))
    path.chmod(0o600)
    raw=path.read_bytes()
    row=dict(role=role,basename=path.name,archive_bytes=len(raw),archive_sha256=hashlib.sha256(raw).hexdigest(),manifest_digest=md,config_digest=cd,tag=tag,repository='fixture.invalid/portal',digest_reference='fixture.invalid/portal@'+md)
    image=dict(row,outer_blobs={ld:len(layer),md:len(manifest),cd:len(config)},expanded_groups=[],layers=[dict(blob_digest=ld,compressed_bytes=len(layer),diff_id=diff,chain_id=diff,parent='',uncompressed_bytes_upper_bound=1024)])
    return path,row,image,parts


@unittest.skipUnless(os.name=='posix','Requires real Linux directory FD and O_NOFOLLOW semantics')
class PosixFixtures(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.fds=[]
        M.B=B;B.DEADLINE=None;M.IMPORT_REQUESTED=False;M.REPORT={'target_import_requested':False}
    def tearDown(self):
        for fd in self.fds:
            try:os.close(fd)
            except OSError:pass
        self.tmp.cleanup()
    def fd(self,path):
        value=os.open(str(path),os.O_RDONLY|os.O_DIRECTORY);self.fds.append(value);return value
    def test_seal_directory_fd_survives_parent_rename_and_symlink_replacement(self):
        upload=self.root/'upload';upload.mkdir();sealed=self.root/'sealed';sealed.mkdir(mode=0o700)
        path,row,image,parts=fixture_archive(upload)
        ufd,sfd=self.fd(upload),self.fd(sealed)
        moved=self.root/'moved';sealed.rename(moved)
        attack=self.root/'attack';attack.mkdir();sealed.symlink_to(attack,target_is_directory=True)
        fd=M.seal_fd(ufd,sfd,row);self.fds.append(fd)
        self.assertEqual(hashlib.sha256(os.read(fd,row['archive_bytes'])).hexdigest(),row['archive_sha256'])
        self.assertTrue((moved/row['basename']).exists());self.assertEqual(list(attack.iterdir()),[])
        self.assertEqual(M.fd_regular(fd).st_mode&0o777,0o600)
    def test_report_handoff_exact_bytes_and_directory_owner_last(self):
        if os.stat('/tmp').st_mode&0o022:self.skipTest('Fixture requires root-private tmpfs mode=700')
        raw=b'{"status":"INCOMPLETE","target_import_requested":false}\n'
        events=[];real_chown=os.fchown
        def chown(fd,uid,gid):
            value=os.fstat(fd)
            events.append('directory' if stat.S_ISDIR(value.st_mode) else 'file')
            if stat.S_ISREG(value.st_mode):
                self.assertEqual(value.st_size,len(raw));self.assertEqual(value.st_mode&0o777,0o600)
            real_chown(fd,uid,gid)
        with mock.patch.object(M,'REPORT_PARENT',self.root),mock.patch.object(M.pwd,'getpwnam',return_value=types.SimpleNamespace(pw_uid=65534,pw_gid=65534)),mock.patch.object(M.os,'fchown',side_effect=chown):
            output=Path(M.handoff_report(raw))
        self.assertEqual(events,['file','directory']);self.assertEqual(output.read_bytes(),raw)
        self.assertEqual(output.stat().st_uid,65534);self.assertEqual(output.parent.stat().st_uid,65534)
        self.assertEqual(output.parent.stat().st_mode&0o777,0o700)
        self.assertEqual(sorted(p.name for p in output.parent.iterdir()),['REPORT.json'])
    def test_catalog_scale_wire_and_generated_single_file(self):
        catalog=json.loads((ROOT/'native-cache-diagnostic-r1/CATALOG.json').read_bytes())
        profile=json.loads((ROOT/'native-preload-r1/PROFILE.json').read_bytes())
        accepted=json.loads((ROOT/'native-cache-evidence-r1/REPORT.json').read_bytes())
        allocation=load(HERE/'budget_runtime.py','allocation_for_wire_fixture')
        plan=dict(schema='portal-ppt-native-cache-plan.v1',prior_operation=M.OP,profile_sha256=M.PROFILE_SHA,
                  catalog_sha256=hashlib.sha256((ROOT/'native-cache-diagnostic-r1/CATALOG.json').read_bytes()).hexdigest(),
                  maximum_outer_blob_bytes=573414766,images=[],chains={},blobs={})
        for source in catalog['images']:
            row=copy.deepcopy(source);row['layers']=[];row['outer_blobs']={};parent=''
            for index in source['layer_indices']:
                layer=copy.deepcopy(catalog['layers'][index]);diff=layer['diff_id']
                chain=diff if not parent else digest((parent+' '+diff).encode())
                layer.update(chain_id=chain,parent=parent);parent=chain;row['layers'].append(layer)
            if row['role']=='ppt-master-agent-host-forward':
                # Only the unknown remote tail is synthetic, never release evidence.
                diff='sha256:'+'0'*64;chain=digest((parent+' '+diff).encode())
                row['layers'].append(dict(chain_id=chain,parent=parent,diff_id=diff,blob_digest='sha256:'+'1'*64,
                                         compressed_bytes=12345,uncompressed_bytes_upper_bound=8*1024**2))
            for layer in row['layers']:
                name=layer['chain_id'];previous=plan['chains'].get(name)
                if previous is None:plan['chains'][name]=dict(layer)
                else:previous['uncompressed_bytes_upper_bound']=max(previous['uncompressed_bytes_upper_bound'],layer['uncompressed_bytes_upper_bound'])
                row['outer_blobs'][layer['blob_digest']]=layer['compressed_bytes']
            # Descriptor sizes are synthetic; this is transport qualification.
            for key in ('manifest_digest','config_digest'):row['outer_blobs'][row[key]]=1024
            plan['blobs'].update(row['outer_blobs']);plan['images'].append(row)
        plan=allocation.prepare_plan(plan,accepted)
        # Additional unreferenced outer blobs exercise conservative wire-size margin.
        for number in range(128):
            name=digest(('wire-only-extra-'+str(number)).encode());plan['blobs'][name]=1024
            plan['images'][-1]['outer_blobs'][name]=1024
        M.validate_plan(profile,plan)
        unpacked=M.unwire_plan(M.unpack(M.pack(M.wire_plan(plan))))
        M.validate_plan(profile,unpacked)
        self.assertEqual(M.wire_plan(unpacked),M.wire_plan(plan))
        code=(HERE/'preload_native_r3.py').read_bytes()
        self.assertNotIn(b'@@ALLOCATION_MODULE_',code)
        generated=load(HERE/'preload_native_r3.py','generated_r3_for_fixture')
        import zlib
        self.assertEqual(zlib.decompress(base64.b64decode(generated.ALLOCATION_Z64)),(HERE/'budget_runtime.py').read_bytes())
        self.assertEqual(generated.ALLOCATION_SHA,hashlib.sha256((HERE/'budget_runtime.py').read_bytes()).hexdigest())
        command=generated.bootstrap_command(code,unpacked,'a'*32)
        self.assertLess(len(command.encode()),110*1024)
        import shlex
        launcher=shlex.split(command)[-1]
        encoded=re.search(r"base64.b64decode\('([A-Za-z0-9+/=]+)'\)",launcher).group(1)
        payload=json.loads(zlib.decompress(base64.b64decode(encoded)))
        namespace={'__name__':'derived_remote_fixture'}
        exec(compile(payload['code'],'derived_remote_fixture','exec'),namespace)
        derived=zlib.decompress(base64.b64decode(namespace['ALLOCATION_Z64']))
        self.assertEqual(hashlib.sha256(derived).hexdigest(),namespace['ALLOCATION_SHA'])
        self.assertNotEqual(namespace['ALLOCATION_SHA'],generated.ALLOCATION_SHA)
        def without_binding(raw):
            tree=ast.parse(raw)
            tree.body=[node for node in tree.body if not (isinstance(node,ast.Assign) and any(isinstance(target,ast.Name) and target.id=='BINDING' for target in node.targets))]
            return ast.dump(tree,include_attributes=False)
        self.assertEqual(without_binding(derived),without_binding((HERE/'budget_runtime.py').read_bytes()))
        self.assertEqual(namespace['unwire_plan'].__name__,'unwire_plan')
        print('WIRE_FIXTURE='+json.dumps(dict(bootstrap_bytes=len(command.encode()),limit=110*1024,
              chain_count=len(plan['chains']),synthetic_orphan_blobs=128,synthetic_host_tail=True,source_plan_exact=False)))
    def test_seal_hash_mismatch_preserves_new_inode_and_never_imports(self):
        upload=self.root/'upload';upload.mkdir();sealed=self.root/'sealed';sealed.mkdir(mode=0o700)
        path,row,image,parts=fixture_archive(upload);row['archive_sha256']='0'*64
        with mock.patch.object(M,'run_fd') as launch:
            with self.assertRaisesRegex(ValueError,'sealed_archive_identity'):M.seal_fd(self.fd(upload),self.fd(sealed),row)
        launch.assert_not_called();self.assertTrue((sealed/row['basename']).exists())
    def test_seal_rejects_symlink_and_hardlink_input(self):
        upload=self.root/'upload';upload.mkdir();sealed=self.root/'sealed';sealed.mkdir(mode=0o700)
        path,row,image,parts=fixture_archive(upload);other=upload/'other';path.rename(other);path.symlink_to(other)
        with self.assertRaises(OSError):M.seal_fd(self.fd(upload),self.fd(sealed),row)
        path.unlink();os.link(other,path)
        with self.assertRaisesRegex(ValueError,'fd_regular_required'):M.seal_fd(self.fd(upload),self.fd(sealed),row)
    def test_metadata_tar_contains_exact_four_original_parts_and_no_layer(self):
        path,row,image,parts=fixture_archive(self.root)
        fd=os.open(path,os.O_RDONLY);self.fds.append(fd)
        observed=M.archive_parts(fd,row,image);self.assertEqual(observed,parts)
        metadata=M.metadata_tar_fd(self.fd(self.root),row,observed);self.fds.append(metadata)
        with os.fdopen(os.dup(metadata),'rb') as stream:
            with tarfile.open(fileobj=stream,mode='r:') as archive:
                self.assertEqual({x.name:archive.extractfile(x).read() for x in archive},parts)
        self.assertLess(os.fstat(metadata).st_size,1024*1024)
    def test_metadata_binding_rejects_wrong_manifest_and_outer_plan(self):
        path,row,image,parts=fixture_archive(self.root);fd=os.open(path,os.O_RDONLY);self.fds.append(fd)
        wrong=dict(image);wrong['outer_blobs']=dict(image['outer_blobs']);wrong['outer_blobs'][next(iter(wrong['outer_blobs']))]+=1
        with self.assertRaisesRegex(ValueError,'sealed_plan_header_binding'):M.archive_parts(fd,row,wrong)
    def test_real_readonly_fd_inheritance_after_path_replacement(self):
        path=self.root/'blob';path.write_bytes(b'fd-fixture');path.chmod(0o600)
        fd=os.open(path,os.O_RDONLY);self.fds.append(fd);path.rename(self.root/'preserved');path.symlink_to('/dev/null')
        command=['nice','-n','10','ionice','-c','2','-n','7',sys.executable,'-I','-B','-c',"import sys; assert open(sys.argv[1],'rb').read()==b'fd-fixture'",'/proc/self/fd/'+str(fd)]
        with mock.patch.object(B,'progress'):
            result=M.run_fd(command,fd,'FIXTURE_FD',lambda:None,timeout=5)
        self.assertEqual(result['returncode'],0)
    def test_fd_runner_timeout_kills_owned_descendant_and_preserves_input(self):
        path=self.root/'blob';path.write_bytes(b'x');path.chmod(0o600);fd=os.open(path,os.O_RDONLY);self.fds.append(fd)
        pidfile=self.root/'child.pid'
        code="import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)']);open(sys.argv[1],'w').write(str(p.pid));time.sleep(30)"
        command=[sys.executable,'-I','-B','-c',code,str(pidfile),'/proc/self/fd/'+str(fd)]
        with mock.patch.object(B,'progress'):
            with self.assertRaisesRegex(ValueError,'import_timeout'):M.run_fd(command,fd,'FIXTURE_TIMEOUT',lambda:None,timeout=.4)
        child=int(pidfile.read_text());state=Path('/proc')/str(child)/'stat'
        until=time.monotonic()+2
        while state.exists() and state.read_text().split()[2]!='Z' and time.monotonic()<until:time.sleep(.05)
        self.assertTrue(not state.exists() or state.read_text().split()[2]=='Z')
        self.assertEqual(path.read_bytes(),b'x')
    def test_requested_receipt_is_once_failure_does_not_create_pass(self):
        root=self.root/'state';root.mkdir(mode=0o700);M.STATE=root
        row={'role':'fixture'}
        with mock.patch.object(B,'safe_dir'),mock.patch.object(M,'import_argv',return_value=['fixture']),mock.patch.object(M,'run_fd',side_effect=ValueError('fixed_import_failure')):
            with self.assertRaisesRegex(ValueError,'fixed_import_failure'):M.request_import('PHASE-A',row,9,{}, {},9)
        self.assertTrue((root/'fixture.PHASE-A-REQUESTED.json').exists());self.assertFalse((root/'fixture.PHASE-A-PASS.json').exists())
        with mock.patch.object(B,'safe_dir'),mock.patch.object(M,'run_fd') as launch:
            with self.assertRaises(FileExistsError):M.request_import('PHASE-A',row,9,{}, {},9)
        launch.assert_not_called()
    def test_phase_a_failure_stops_before_phase_b_requested(self):
        upload=self.root/'upload';upload.mkdir();path,row,image,parts=fixture_archive(upload)
        state=self.root/'state';state.mkdir(mode=0o700);sealed=self.root/'sealed';sealed.mkdir(mode=0o700)
        value=upload.stat();token='a'*32
        prepared={'operation':token,'status':'PREPARED_NO_IMPORT','upload_device':value.st_dev,'upload_inode':value.st_ino,'upload_uid':value.st_uid}
        sfd=os.open(sealed,os.O_RDONLY|os.O_DIRECTORY)
        with mock.patch.object(M,'state_paths',return_value={'target':state,'upload':upload,'sealed_name':'fixture'}),mock.patch.object(M,'private_bytes',return_value=B.encoded(prepared)),mock.patch.object(M,'prior_binding'),mock.patch.object(M,'storage_check'),mock.patch.object(B,'safe_dir'),mock.patch.object(B,'collision_check'),mock.patch.object(M,'make_sealed_directory',return_value=(sfd,{'fixture':True})),mock.patch.object(M,'request_import',side_effect=ValueError('phase_a_failed')):
            with self.assertRaisesRegex(ValueError,'phase_a_failed'):M.import_target({'images':[row]},{'images':[image],'allocation':{'maximum_metadata_import_bytes':1024*1024}},token)
        self.assertTrue((state/'PHASE-A-REQUESTED.json').exists())
        self.assertFalse((state/'PHASE-B-REQUESTED.json').exists())
    def test_private_read_ignores_atime_but_rejects_nonprivate_owner_mode(self):
        path=self.root/'private';path.write_bytes(b'fixture');path.chmod(0o600)
        # /tmp is intentionally world writable; exercise final-file reader under
        # mocked parent stat only, while fstat/open/read remain real.
        original=Path.lstat
        def lstat(p):
            value=original(p)
            if p==path:return value
            values=list(value);values[0]=stat.S_IFDIR|0o700;values[4]=0
            return os.stat_result(values)
        with mock.patch.object(Path,'lstat',lstat):self.assertEqual(M.private_bytes(path),b'fixture')
        path.chmod(0o622)
        with mock.patch.object(Path,'lstat',lstat):
            with self.assertRaisesRegex(ValueError,'private_file_required'):M.private_bytes(path)


class PureFixtures(unittest.TestCase):
    def test_failed_storage_observation_is_preserved(self):
        exc=ValueError('r3_storage_budget_insufficient');exc.observation={'available_bytes':1,'required_bytes':2}
        M.REPORT={}
        with mock.patch.object(M,'A',types.SimpleNamespace(check=mock.Mock(side_effect=exc))):
            with self.assertRaisesRegex(ValueError,'r3_storage_budget_insufficient'):M.storage_check({}, {},'BEFORE_PHASE_A')
        self.assertEqual(M.REPORT['storage_checks'],[{'phase':'BEFORE_PHASE_A','observation':exc.observation,'check_failed':True}])
    def test_import_argv_only_phase_a_has_no_unpack_and_no_force(self):
        row={'repository':'fixture.invalid/portal'}
        a=M.import_argv(row,8,True);b=M.import_argv(row,8,False)
        self.assertIn('--no-unpack',a);self.assertNotIn('--no-unpack',b)
        self.assertEqual(a[-1],'/proc/self/fd/8');self.assertNotIn('--force',a)
        self.assertIn('--digests',a);self.assertEqual(a[a.index('--base-name')+1],row['repository'])
    def test_pack_roundtrip_and_trailing_stream_rejected(self):
        item={'digest':'sha256:'+'a'*64,'sizes':[1,2,3]};self.assertEqual(M.unpack(M.pack(item)),item)
        raw=base64.b64decode(M.pack(item))+b'trailing'
        with self.assertRaisesRegex(ValueError,'packed_input_limit'):M.unpack(base64.b64encode(raw).decode())
    def test_wire_roundtrip_retains_allocation_and_original_layers(self):
        layer={'chain_id':'sha256:'+'a'*64,'parent':'','blob_digest':'sha256:'+'b'*64,'compressed_bytes':12,'diff_id':'sha256:'+'a'*64,'uncompressed_bytes_upper_bound':20}
        row={'role':'fixture','basename':'x.tar','archive_sha256':'c'*64,'archive_bytes':100,'manifest_digest':'sha256:'+'d'*64,'config_digest':'sha256:'+'e'*64,'expanded_groups':[],'outer_blobs':{'sha256:'+'b'*64:12},'layers':[layer]}
        plan={'schema':'fixture','prior_operation':M.OP,'profile_sha256':M.PROFILE_SHA,'maximum_outer_blob_bytes':12,'catalog_sha256':'f'*64,'allocation':{'physical_upper':123,'accepted_cache':['x']},'blobs':row['outer_blobs'],'chains':{layer['chain_id']:layer},'images':[dict(row,role='fixture'+str(i)) for i in range(6)]}
        self.assertEqual(M.unwire_plan(M.wire_plan(plan)),plan)
        alternate=dict(layer,blob_digest='sha256:'+'f'*64,compressed_bytes=15)
        plan['blobs'][alternate['blob_digest']]=15
        plan['images'][-1]['outer_blobs']=dict(plan['blobs'])
        plan['images'][-1]['layers']=[alternate]
        self.assertEqual(M.unwire_plan(M.wire_plan(plan)),plan)
    def test_remote_receipt_wrong_operation_cannot_clear_import_uncertainty(self):
        value={'schema':'portal-ppt-native-preload-r3.v1','operation':'0'*32,'target':'datax-02','target_import_requested':False}
        raw=(M.MARKER+base64.b64encode(B.encoded(value)).decode()).encode()
        with self.assertRaisesRegex(ValueError,'remote_result_binding'):M.remote_result(raw,'1'*32)


if __name__=='__main__':unittest.main(verbosity=2)

import ast
import hashlib
import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT=pathlib.Path(__file__).resolve().parents[1]
PATH=ROOT/'deploy/enterprise/export-portal-skillhub-auth-build-input-20261105-r2.py'
spec=importlib.util.spec_from_file_location('skillhub_export',PATH)
probe=importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

def fixture(path, platform='arm64', changes=None, extra=None):
    layer=b'synthetic-program-layer'
    config=json.dumps({'architecture':platform,'os':'linux','rootfs':{'type':'layers','diff_ids':['sha256:'+probe.sha(layer)]}}).encode()
    def desc(raw):
        return {'digest':'sha256:'+probe.sha(raw),'size':len(raw)}
    manifest=json.dumps({'schemaVersion':2,'config':desc(config),'layers':[desc(layer)]}).encode()
    digest='sha256:'+probe.sha(manifest)
    files={'oci-layout':b'{"imageLayoutVersion":"1.0.0"}',
           'index.json':json.dumps({'schemaVersion':2,'manifests':[desc(manifest)]}).encode(),
           'blobs/sha256/'+probe.sha(manifest):manifest,
           'blobs/sha256/'+probe.sha(config):config,
           'blobs/sha256/'+probe.sha(layer):layer}
    if changes:
        changes(files,manifest,config,layer)
    with tarfile.open(path,'w') as archive:
        for name,raw in files.items():
            info=tarfile.TarInfo(name);info.size=len(raw)
            archive.addfile(info,io.BytesIO(raw))
        if extra:
            extra(archive)
    return digest

def indexed_fixture(path, mode='normal', levels=1):
    leaf=fixture(path)
    with tarfile.open(path,'r:') as archive:
        files={m.name:archive.extractfile(m).read() for m in archive.getmembers() if m.isfile()}
    descriptor=json.loads(files['index.json'])['manifests'][0]
    descriptor['platform']={'os':'linux','architecture':'arm64','variant':'v8'}
    if mode=='no_arm64':
        descriptor['platform']['architecture']='amd64'
    for depth in range(levels):
        children=[descriptor,{'digest':'sha256:'+'a'*64,'size':100,'platform':{'os':'linux','architecture':'amd64'}},
                  {'digest':'sha256:'+'b'*64,'size':100,'platform':{'os':'unknown','architecture':'unknown'}}]
        if mode=='ambiguous':
            children.append(dict(descriptor))
        raw=json.dumps({'schemaVersion':2,'mediaType':'application/vnd.oci.image.index.v1+json','manifests':children}).encode()
        root='sha256:'+probe.sha(raw)
        files['blobs/sha256/'+probe.sha(raw)]=raw
        descriptor={'digest':root,'size':len(raw),'platform':{'os':'linux','architecture':'arm64'}}
    files['index.json']=json.dumps({'schemaVersion':2,'manifests':[descriptor]}).encode()
    if mode=='missing_child':
        del files['blobs/sha256/'+leaf[7:]]
    with tarfile.open(path,'w') as archive:
        for name,raw in files.items():
            info=tarfile.TarInfo(name);info.size=len(raw)
            archive.addfile(info,io.BytesIO(raw))
    return root,leaf

class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=pathlib.Path(self.temp.name)/'image.tar'
    def test_platform_index_export_preserves_pinned_root(self):
        root,leaf=indexed_fixture(self.path)
        out=probe.audit_oci(self.path,root)
        self.assertEqual(out['root_manifest'],root)
        self.assertEqual(out['manifest'],leaf)
        self.assertEqual(out['index_depth'],1)
    def test_nested_platform_index(self):
        root,leaf=indexed_fixture(self.path,levels=2)
        self.assertEqual(probe.audit_oci(self.path,root)['manifest'],leaf)
    def test_ambiguous_arm64_refused(self):
        root,leaf=indexed_fixture(self.path,mode='ambiguous')
        with self.assertRaisesRegex(probe.Refused,'ONE_ARM64_MANIFEST_REQUIRED'):
            probe.audit_oci(self.path,root)
    def test_arm64_missing_refused(self):
        root,leaf=indexed_fixture(self.path,mode='no_arm64')
        with self.assertRaisesRegex(probe.Refused,'ONE_ARM64_MANIFEST_REQUIRED'):
            probe.audit_oci(self.path,root)
    def test_missing_selected_child_refused(self):
        root,leaf=indexed_fixture(self.path,mode='missing_child')
        with self.assertRaisesRegex(probe.Refused,'OCI_DESCRIPTOR_MISSING_OR_OVERSIZE'):
            probe.audit_oci(self.path,root)
    def test_excess_index_depth_refused(self):
        root,leaf=indexed_fixture(self.path,levels=4)
        with self.assertRaisesRegex(probe.Refused,'IMAGE_INDEX_DEPTH_BOUND'):
            probe.audit_oci(self.path,root)
    def test_cached_resolution_uses_same_logic_as_export(self):
        root,leaf=indexed_fixture(self.path)
        with tarfile.open(self.path,'r:') as archive:
            def read(digest,limit):
                return archive.extractfile('blobs/sha256/'+digest[7:]).read(limit+1)
            manifest,chain=probe.resolve_program_manifest(read,root)
            self.assertEqual(chain[-1]['digest'],leaf)
            self.assertEqual(probe.validate_platform_config(read,manifest)['architecture'],'arm64')
    def test_r1_index_failure_reproduced(self):
        root,leaf=indexed_fixture(self.path)
        with tarfile.open(self.path,'r:') as archive:
            pinned=json.load(archive.extractfile('blobs/sha256/'+root[7:]))
        self.assertFalse('config' in pinned and 1<=len(pinned.get('layers',[]))<=40)
        self.assertEqual(probe.audit_oci(self.path,root)['manifest'],leaf)

    def test_python37_syntax(self):
        ast.parse(PATH.read_text(encoding='utf-8'),feature_version=(3,7))
    def test_wrong_platform_has_no_commands_or_files(self):
        with patch.object(probe.os,'name','nt'),patch.object(sys,'argv',['probe','--export-current-program-image-once']),patch.object(probe,'snapshot') as snapshot,patch.object(probe.subprocess,'run') as run,patch('sys.stdout',new_callable=io.StringIO) as stdout:
            self.assertEqual(probe.main(),1)
        snapshot.assert_not_called();run.assert_not_called()
        out=json.loads(stdout.getvalue())
        self.assertEqual(out['error_code'],'DATAX01_ROOT_REQUIRED')
        self.assertFalse(out['export_directory_created'])
    def test_exact_complete_oci(self):
        digest=fixture(self.path)
        out=probe.audit_oci(self.path,digest)
        self.assertEqual(out['manifest'],digest)
        self.assertEqual(out['layers'],1)
        self.assertTrue(out['complete_blob_hashes_verified'])
    def test_other_manifest_refused(self):
        fixture(self.path)
        with self.assertRaisesRegex(probe.Refused,'RUNTIME_MANIFEST_MISMATCH'):
            probe.audit_oci(self.path,'sha256:'+'0'*64)
    def test_wrong_platform_refused(self):
        digest=fixture(self.path,platform='amd64')
        with self.assertRaisesRegex(probe.Refused,'PLATFORM_MISMATCH'):
            probe.audit_oci(self.path,digest)
    def test_corrupt_layer_refused(self):
        def corrupt(files,m,c,l):
            files['blobs/sha256/'+probe.sha(l)]=b'X'*len(l)
        digest=fixture(self.path,changes=corrupt)
        with self.assertRaisesRegex(probe.Refused,'BLOB_HASH_MISMATCH'):
            probe.audit_oci(self.path,digest)
    def test_missing_layer_refused(self):
        def remove(files,m,c,l):
            del files['blobs/sha256/'+probe.sha(l)]
        digest=fixture(self.path,changes=remove)
        with self.assertRaisesRegex(probe.Refused,'BLOB_MISSING'):
            probe.audit_oci(self.path,digest)
    def test_corrupt_manifest_refused(self):
        def corrupt(files,m,c,l):
            files['blobs/sha256/'+probe.sha(m)]=b'X'*len(m)
        digest=fixture(self.path,changes=corrupt)
        with self.assertRaisesRegex(probe.Refused,'MANIFEST_HASH_MISMATCH'):
            probe.audit_oci(self.path,digest)
    def test_unsafe_member_refused(self):
        digest=fixture(self.path,changes=lambda files,*args:files.update({'../../escape':b'x'}))
        with self.assertRaisesRegex(probe.Refused,'OCI_UNEXPECTED_MEMBER'):
            probe.audit_oci(self.path,digest)
    def test_unreferenced_blob_refused(self):
        digest=fixture(self.path,changes=lambda files,*args:files.update({'blobs/sha256/'+probe.sha(b'extra'):b'extra'}))
        with self.assertRaisesRegex(probe.Refused,'UNREFERENCED_IMAGE_CONTENT'):
            probe.audit_oci(self.path,digest)
    def test_duplicate_member_refused(self):
        def extra(archive):
            info=tarfile.TarInfo('index.json');info.size=2
            archive.addfile(info,io.BytesIO(b'{}'))
        digest=fixture(self.path,extra=extra)
        with self.assertRaisesRegex(probe.Refused,'OCI_UNEXPECTED_MEMBER'):
            probe.audit_oci(self.path,digest)
    def test_symlink_member_refused(self):
        def extra(archive):
            info=tarfile.TarInfo('link');info.type=tarfile.SYMTYPE;info.linkname='/app/app.jar'
            archive.addfile(info)
        digest=fixture(self.path,extra=extra)
        with self.assertRaisesRegex(probe.Refused,'OCI_LINK_OR_SPECIAL_FILE'):
            probe.audit_oci(self.path,digest)
    def test_unsafe_directory_refused(self):
        def extra(archive):
            info=tarfile.TarInfo('../escape/');info.type=tarfile.DIRTYPE
            archive.addfile(info)
        digest=fixture(self.path,extra=extra)
        with self.assertRaisesRegex(probe.Refused,'OCI_UNEXPECTED_DIRECTORY'):
            probe.audit_oci(self.path,digest)
    def test_descriptor_bound(self):
        digest=fixture(self.path,changes=lambda files,*args:files.update({'index.json':b' '*262145}))
        with self.assertRaisesRegex(probe.Refused,'OCI_DESCRIPTOR_MISSING_OR_OVERSIZE'):
            probe.audit_oci(self.path,digest)
    def test_archive_bound(self):
        digest=fixture(self.path)
        with patch.object(probe,'MAX_ARCHIVE',100),self.assertRaisesRegex(probe.Refused,'INVALID_EXPORT_FILE'):
            probe.audit_oci(self.path,digest)
    def test_read_timeout_sanitized(self):
        with patch.object(probe.subprocess,'run',side_effect=subprocess.TimeoutExpired(['private'],1)),self.assertRaisesRegex(probe.Refused,'READ_TIMEOUT'):
            probe.read_command(['private'])
    def test_read_output_bound(self):
        def run(args,stdout,stderr,**kwargs):
            stdout.write(b'x'*20)
            return subprocess.CompletedProcess(args,0)
        with patch.object(probe.subprocess,'run',side_effect=run),self.assertRaisesRegex(probe.Refused,'READ_OUTPUT_BOUND'):
            probe.read_command(['synthetic'],10)
    def test_command_failure_hides_diagnostic(self):
        def run(args,stdout,stderr,**kwargs):
            stderr.write(b'private configuration')
            return subprocess.CompletedProcess(args,1)
        with patch.object(probe.subprocess,'run',side_effect=run),self.assertRaisesRegex(probe.Refused,'^READ_FAILED$'):
            probe.read_command(['synthetic'])
    def test_hash_file_is_exact(self):
        self.path.write_bytes(b'program')
        self.assertEqual(probe.hash_file(self.path),{'bytes':7,'sha256':hashlib.sha256(b'program').hexdigest()})
    def test_changed_deployment_stops_before_container_access(self):
        deployment={'metadata':{'name':'skillhub-server','uid':probe.DEPLOYMENT_UID,'generation':15},'spec':{}}
        with patch.object(probe,'get',side_effect=[deployment,{}]),patch.object(probe,'read_command') as read,self.assertRaisesRegex(probe.Refused,'SERVER_DEPLOYMENT_CHANGED'):
            probe.snapshot()
        read.assert_not_called()

if __name__=='__main__':
    unittest.main()

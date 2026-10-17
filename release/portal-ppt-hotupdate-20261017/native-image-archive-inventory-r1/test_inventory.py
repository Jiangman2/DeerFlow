import ast
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock
import zlib

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('inventory_under_test',HERE/'collect_inventory.py')
M=importlib.util.module_from_spec(spec);spec.loader.exec_module(M)
D='sha256:'+'a'*64


class ProjectionFixtures(unittest.TestCase):
    def test_images_allow_only_identity_refs_and_size(self):
        raw={'images':[dict(id=D,repoTags=['repo/image:tag'],repoDigests=['repo/image@'+D],size='123',
                            labels={'password':'SECRET'},spec={'config':{'Env':['KEY=SECRET']}})]}
        result=M.project_images(raw)
        self.assertEqual(result,[dict(id=D,repoTags=['repo/image:tag'],repoDigests=['repo/image@'+D],size_bytes=123)])
        raw['images'][0]['repoTags']=None
        self.assertEqual(M.project_images(raw)[0]['repoTags'],[])
        self.assertNotIn('SECRET',json.dumps(result))
    def test_container_projection_drops_metadata_and_annotation_values(self):
        row=dict(id='b'*64,image={'image':'repo/image:tag','annotations':{'key':'SECRET'}},imageRef=D,
                 state='CONTAINER_EXITED',podSandboxId='c'*64,metadata={'name':'SECRET'},labels={'secret':'SECRET'})
        result=M.project_containers({'containers':[row]})
        self.assertEqual(set(result[0]),{'id','image','imageRef','state','podSandboxId'})
        self.assertNotIn('SECRET',json.dumps(result));self.assertEqual(result[0]['state'],'CONTAINER_EXITED')
    def test_ctr_first_three_columns_only(self):
        raw=('REF TYPE DIGEST SIZE PLATFORMS LABELS\nrepo/image:tag application/vnd.oci.image.manifest.v1+json '+D+' 1GiB linux/arm64 secret=SECRET\n').encode()
        result=M.project_ctr(raw)
        self.assertEqual(set(result[0]),{'reference','media_type','digest'});self.assertNotIn('SECRET',json.dumps(result))
        self.assertEqual(M.project_namespaces(b'k8s.io\ndefault\n'),['default','k8s.io'])
    def test_invalid_reference_and_identity_fail_closed(self):
        for ref in ('https://user:secret@host/image','repo/image:tag?token=SECRET','repo/image\nSECRET','repo@bad'):
            with self.assertRaises(ValueError):M.reference(ref)
        with self.assertRaises(ValueError):M.project_images({'images':[dict(id='SECRET',size=1)]})
        with self.assertRaises(ValueError):M.number(True)
    def test_nonzero_or_invalid_json_preserves_safe_step_without_stderr(self):
        for rc,raw,parse in ((1,b'SECRET',json.loads),(0,b'notjson SECRET',json.loads)):
            report={'steps':[]};module=types.SimpleNamespace(B=types.SimpleNamespace(run=lambda *a,**k:(rc,raw),parse=parse))
            M.command_step(module,report,'cri_images',['crictl','images'],M.project_images,True)
            self.assertEqual(report['steps'][0]['status'],'NOT_COLLECTED');self.assertNotIn('SECRET',json.dumps(report))
    def test_large_projection_is_explicitly_omitted_not_printed_unbounded(self):
        report=dict(status='COMPLETE_REVIEW_REQUIRED',cri_images=['x'*100 for _ in range(20)])
        with mock.patch.object(M,'LIMIT',1024):raw=M.report_bytes(report)
        self.assertLessEqual(len(raw),1024);self.assertEqual(report['status'],'PARTIAL_REVIEW_REQUIRED')
        self.assertEqual(report['cri_images'],dict(status='OMITTED_REPORT_SIZE_LIMIT',row_count=20))
    def test_embedded_helpers_exact_and_no_write_entry_called(self):
        raw=zlib.decompress(base64.b64decode(M.GUARD_Z64))
        self.assertEqual(hashlib.sha256(raw).hexdigest(),M.GUARD_SHA)
        self.assertEqual(raw,(HERE.parent/'native-preload-r3-local-resume/resume_target.py').read_bytes())
        tree=ast.parse((HERE/'collect_inventory.py').read_bytes())
        calls=[node.func for node in ast.walk(tree) if isinstance(node,ast.Call)]
        self.assertFalse(any(isinstance(f,ast.Attribute) and isinstance(f.value,ast.Name) and f.value.id=='guard' and f.attr=='run' for f in calls))
        commands=[['images','-o','json'],['ps','-a','-o','json'],['images','ls'],['namespaces','list','--quiet']]
        found=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='command_step':
                found.append(ast.literal_eval(node.args[3].right))
        self.assertEqual(found,commands)
    def test_complete_checkpoint_set_and_missing_or_phase_b_changed(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);target=root/'target';target.mkdir();control=root/'control';control.mkdir();sealed=root/'sealed';sealed.mkdir()
            images=[{'role':'role-'+str(i)} for i in range(6)];profile={'images':images}
            required={'STARTED.json','PREPARED.json','RUN-REQUESTED.json','FAIL.json','PHASE-A-REQUESTED.json','PHASE-A-PASS.json'}
            for row in images:required.update(row['role']+s for s in ('.SEALED.json','.PHASE-A-REQUESTED.json','.PHASE-A-PASS.json'))
            raw=b'{"status":"INCOMPLETE","password":"SECRET"}\n'
            for name in required:(target/name).write_bytes(raw)
            (control/'PLAN-WIRE.json').write_bytes(b'{}')
            module=types.SimpleNamespace(B=types.SimpleNamespace(safe_dir=lambda p:None,parse=json.loads,open_anchored=lambda p:os.open(sealed,os.O_RDONLY|os.O_DIRECTORY)),
                private_bytes=lambda p,n:p.read_bytes(),state_paths=lambda t:dict(target=target,control=control,sealed_name='fixture'))
            with mock.patch.object(M,'FAIL_SHA',hashlib.sha256(raw).hexdigest()):
                result=M.checkpoints(module,profile)
                self.assertTrue(result['state_unchanged']);self.assertEqual(result['actual_checkpoint_file_count'],24)
                self.assertEqual(sum(r['present'] for r in result['files']),24);self.assertNotIn('SECRET',json.dumps(result))
                (target/'PHASE-A-PASS.json').unlink();self.assertFalse(M.checkpoints(module,profile)['state_unchanged'])
                (target/'PHASE-A-PASS.json').write_bytes(raw);(target/'PHASE-B-REQUESTED.json').write_bytes(raw)
                changed=M.checkpoints(module,profile);self.assertTrue(changed['any_phase_b_receipt']);self.assertFalse(changed['state_unchanged'])


if __name__=='__main__':unittest.main(verbosity=2)

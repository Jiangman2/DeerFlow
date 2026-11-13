"""R2 accepts only unchanged f880 or a guarded, qualified 2.0.1 installation."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


post = module('postcheck_r2', HERE / 'verify-energy-daily-result-links-20261112-r2.py')
reader = module('postcheck_skill_reader_r2', HERE / 'skill_reader_r2.py')
base = module('original_postcheck_tests_reused_for_r2', HERE / 'test_gateway_postcheck.py')
base.post = post


class R2OriginalBoundaryTests(base.PostcheckTests):
    """Retain every original get/exec, journal, image and rollout boundary."""


def accepted_receipt():
    tree, runtime = '3' * 64, '4' * 64
    return {'schema':'energy-browser-fix-install.v1','status':'ENERGY_BROWSER_FIX_INSTALLED_RUNTIME_ACCEPTED',
            'skill_updated':True,'maintenance_pod_absent':True,'code_package_sha256':post.CODE_PACKAGE_SHA,
            'publication':{'before_tree_sha256':post.OLD_TREE,'after_tree_sha256':tree,'after_files':107},
            'runtime':{'manifest_sha256':runtime,'source_image_id':post.RUNTIME_IMAGE_ID},
            'shared_runtime_qualification':{'status':'NONROOT_ARM64_BROWSER_AND_FIXED_REPORT_QUALIFIED','uid':1000,'gid':1000,
             'package_sha256':post.CODE_PACKAGE_SHA,'runtime_manifest_sha256':runtime,'after_tree_sha256':tree,'png_sha256':'5' * 64}}


class R2HotfixBindingTests(unittest.TestCase):
    def test_accepted_nonroot_receipt_binds_new_tree_and_runtime(self):
        binding = post.parse_hotfix_receipt(accepted_receipt())
        self.assertEqual(binding['after_files'],107)
        self.assertEqual(binding['after_tree_sha256'],'3' * 64)
        self.assertEqual(binding['qualified_uid'],1000)

    def test_incomplete_existing_receipt_is_not_a_fallback(self):
        receipt = accepted_receipt()
        receipt['status'] = 'INCOMPLETE'
        with self.assertRaisesRegex(post.Refused,'ACCEPTED_BROWSER_FIX_INSTALL_REQUIRED'):
            post.parse_hotfix_receipt(receipt)

    def test_root_qualification_cannot_replace_ordinary_task_user(self):
        receipt = accepted_receipt()
        receipt['shared_runtime_qualification']['uid'] = 0
        with self.assertRaisesRegex(post.Refused,'BROWSER_FIX_NONROOT_QUALIFICATION_CHANGED'):
            post.parse_hotfix_receipt(receipt)

    def test_all_package_runtime_and_lineage_mismatches_are_rejected(self):
        for section,key,value in ((None,'code_package_sha256','0' * 64),('publication','before_tree_sha256','0' * 64),
                                  ('publication','after_files',106),('runtime','source_image_id','sha256:' + '0' * 64),
                                  ('shared_runtime_qualification','package_sha256','0' * 64),
                                  ('shared_runtime_qualification','runtime_manifest_sha256','0' * 64),
                                  ('shared_runtime_qualification','after_tree_sha256','0' * 64)):
            receipt = accepted_receipt()
            (receipt[section] if section else receipt)[key] = value
            with self.subTest(section=section,key=key), self.assertRaises(post.Refused):
                post.parse_hotfix_receipt(receipt)

    def test_new_child_source_is_embedded_exactly(self):
        self.assertEqual(post.SKILL_READER_R2,(HERE / 'skill_reader_r2.py').read_text(encoding='utf-8'))

    def test_no_accepted_hotfix_uses_original_105_file_verifier(self):
        release, assets = post.load_release(base.SEALED)
        updater = post.readonly_updater_type(release)(assets)
        with patch.object(release.Updater,'verify_installed_skill',return_value={'files':105}) as original:
            result = updater.verify_installed_skill({'fixture':'pod'})
        self.assertEqual(result,{'files':105})
        original.assert_called_once()

    def test_new_skill_uses_only_fixed_small_file_reader(self):
        release, assets = post.load_release(base.SEALED)
        updater = post.readonly_updater_type(release)(assets)
        binding = post.parse_hotfix_receipt(accepted_receipt())
        updater.hotfix_binding = binding
        pod = {'metadata':{'name':'gateway-fixture','uid':'pod-uid'},'spec':{
               'containers':[{'name':'gateway','volumeMounts':[{'name':'skills','mountPath':'/app/skills','readOnly':True}]}],
               'volumes':[{'name':'skills','persistentVolumeClaim':{'claimName':'deer-flow-skills-rwx-pvc'}}]}}
        result = {'status':'ENERGY_BROWSER_FIX_SKILL_TREE_VERIFIED','files':107,'tree_sha256':binding['after_tree_sha256'],
                  'runtime_manifest_sha256':binding['runtime_manifest_sha256'],'read_only':True,'runtime_dependency_files_read':0,
                  'browser_launched':False,'credentials_read':False}
        with patch.object(updater,'child',return_value=result) as child:
            view = updater.verify_installed_skill(pod)
        self.assertEqual(view['files'],107)
        self.assertEqual(child.call_args[0][2],post.SKILL_READER_R2)
        self.assertEqual(child.call_args[1]['arguments'],('/app/skills/public/energy-market-daily','3' * 64,'4' * 64))

    @unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'Actual root-private ledger test runs in the isolated root Linux container')
    def test_actual_root_private_receipt_accepts_and_insecure_mode_refuses(self):
        with tempfile.TemporaryDirectory(prefix='.energy-postcheck-fixture-',dir='/var/lib') as temporary:
            path = Path(temporary) / 'REPORT.json'
            path.write_bytes(post.canonical(accepted_receipt()))
            path.chmod(0o600)
            with patch.object(post,'HOTFIX_PATH',path):
                self.assertEqual(post.load_hotfix_binding()['after_files'],107)
                path.chmod(0o644)
                with self.assertRaisesRegex(post.Refused,'BROWSER_FIX_RECEIPT_NOT_ROOT_PRIVATE'):
                    post.load_hotfix_binding()
                path.chmod(0o600)
                path.write_bytes(b'{')
                with self.assertRaises(ValueError):
                    post.load_hotfix_binding()


class SmallSkillReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gateway-skill-r2-')
        self.root = Path(self.temp.name) / 'energy-market-daily'
        self.root.mkdir()
        archive = ROOT / 'release/energy-daily-browser-fix-20261112-r1/energy-market-daily-2.0.1-public-code.zip'
        self.assertEqual(post.sha(archive.read_bytes()),post.CODE_PACKAGE_SHA)
        with zipfile.ZipFile(archive) as package:
            for name in package.namelist():
                path = self.root / name[len('energy-market-daily/'):]
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(package.read(name))
        self.runtime_hash = '4' * 64
        binding = {'schema':'energy-browser-runtime-binding.v1','directory':reader.RUNTIME_DIRECTORY,'manifest_sha256':self.runtime_hash}
        self.pointer = self.root / 'assets/browser-runtime.json'
        self.pointer.parent.mkdir(exist_ok=True)
        self.pointer.write_bytes(post.canonical(binding) + b'\n')
        manifest_path = self.root / 'MANIFEST.json'
        manifest = json.loads(manifest_path.read_bytes())
        manifest['files']['assets/browser-runtime.json'] = {'bytes':self.pointer.stat().st_size,'sha256':post.sha(self.pointer.read_bytes())}
        manifest_path.write_bytes((json.dumps(manifest,ensure_ascii=False,indent=2) + '\n').encode())
        self.tree = post.sha(post.canonical(reader.inventory(self.root)))

    def tearDown(self):
        self.temp.cleanup()

    def test_actual_107_file_tree_passes_without_runtime_directory(self):
        result = reader.inspect(self.root,self.tree,self.runtime_hash)
        self.assertEqual(result['files'],107)
        self.assertEqual(result['runtime_dependency_files_read'],0)
        self.assertFalse((self.root / reader.RUNTIME_DIRECTORY).exists())

    def test_changed_code_is_rejected(self):
        (self.root / 'scripts/daily.py').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'SKILL_TREE_CHANGED'):
            reader.inspect(self.root,self.tree,self.runtime_hash)

    def test_extra_small_file_is_rejected(self):
        (self.root / 'unexpected.txt').write_text('unexpected')
        with self.assertRaisesRegex(ValueError,'SKILL_TREE_BOUND'):
            reader.inspect(self.root,self.tree,self.runtime_hash)

    def test_wrong_browser_binding_is_rejected_even_if_tree_hash_is_known(self):
        with self.assertRaisesRegex(ValueError,'BROWSER_BINDING_CHANGED'):
            reader.inspect(self.root,self.tree,'6' * 64)

    def test_manifest_cannot_omit_new_pointer(self):
        path = self.root / 'MANIFEST.json'
        manifest = json.loads(path.read_bytes())
        del manifest['files']['assets/browser-runtime.json']
        path.write_bytes(post.canonical(manifest))
        tree = post.sha(post.canonical(reader.inventory(self.root)))
        with self.assertRaisesRegex(ValueError,'SKILL_MANIFEST_CHANGED'):
            reader.inspect(self.root,tree,self.runtime_hash)

    @unittest.skipUnless(sys.version_info >= (3,8), 'Gateway reader runs in production Python 3.12, not the Python 3.7 operator host')
    def test_actual_reader_subprocess_is_readonly_and_does_not_launch_browser(self):
        result = subprocess.run([sys.executable,'-I','-B',str(HERE / 'skill_reader_r2.py'),str(self.root),self.tree,self.runtime_hash],
                                stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'))
        observation = json.loads(result.stdout)
        self.assertEqual(observation['status'],'ENERGY_BROWSER_FIX_SKILL_TREE_VERIFIED')
        self.assertEqual(observation['runtime_dependency_files_read'],0)
        self.assertFalse(observation['browser_launched'])


if __name__ == '__main__':
    unittest.main()

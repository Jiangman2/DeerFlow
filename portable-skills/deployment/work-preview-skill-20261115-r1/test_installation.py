"""Boundary tests for the Skill-only publisher derived from accepted assets."""
import ast
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('work_preview_package',HERE/'seal.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
FILES,META=builder.build()
host={'__name__':'isolated_work_preview_installer'};exec(compile(FILES[builder.NAME],'<installer>','exec'),host)
ASSETS=host['load_assets']()
pub={'__name__':'isolated_work_preview_publisher'};exec(compile(ASSETS['publisher'],'<publisher>','exec'),pub)
ver={'__name__':'isolated_work_preview_verifier'};exec(compile(ASSETS['verifier'],'<verifier>','exec'),ver)

class InstallationTests(unittest.TestCase):
    def test_only_three_public_source_files_changed(self):
        old=builder.SKILL.with_name('energy-market-daily-2.1.5');changed=[]
        for path in builder.SKILL.rglob('*'):
            if path.is_file() and path.name not in ('CANDIDATE.md','MANIFEST.json') and path.read_bytes()!=(old/path.relative_to(builder.SKILL)).read_bytes():changed.append(path.relative_to(builder.SKILL).as_posix())
        self.assertEqual(sorted(changed),['SKILL.md','scripts/daily.py','scripts/delivery_text.py'])

    def test_assets_version_count_and_original_tree_are_fully_bound(self):
        self.assertEqual(set(ASSETS),{'publisher','verifier','package','test_files'})
        self.assertEqual(host['CANDIDATE_VERSION'],'2.1.6-portable');self.assertEqual(pub['CANDIDATE_VERSION'],'2.1.6-portable')
        self.assertEqual(host['CANDIDATE_FILES'],108);self.assertEqual(ver['OFFLINE_TEST_COUNT'],39)
        self.assertEqual(host['OLD_TREE'],pub['OLD_TREE']);self.assertEqual(host['OLD_TREE'],builder.OLD_TREE)

    def test_gateway_and_secret_mutations_are_not_allowed(self):
        op=host['Operation'](ASSETS)
        with patch.object(host['subprocess'],'Popen') as process:
            for args in [['patch','deployment','gateway'],['get','secret','deer-flow-env','-o','json'],['delete','deployment','gateway']]:
                with self.assertRaises(host['Refused']):op.command(args)
        process.assert_not_called()

    def test_prior_installed_skill_receipt_matches_exact_bytes(self):
        path=builder.REPO/'release/energy-html-reader-and-skill-20261114-r1/OPERATOR-SKILL-INSTALL-REPORT.json';raw=path.read_bytes()
        self.assertEqual(host['OLD_INSTALL'].as_posix(),'/var/lib/portal-energy-html-reader-skill-install-20261114-r1/REPORT.json')
        with patch.dict(host,{'read':lambda path:raw}):binding=host['qualification_binding']()
        self.assertEqual(binding[3]['sha256'],'e47a95e921c403f37c3e8f7d97161c348c35e7da3bb8446c8ecdcab36abe41bb')

    def test_completed_gateway_418_receipt_is_accepted_without_replay(self):
        raw=(builder.REPO/'release/energy-reader-work-repair-20261115-r2/REPORT-RECONSTRUCTED.json').read_bytes()
        with patch.dict(host,{'read':lambda path:raw}):
            bound=host['gateway_context_binding']()
            self.assertEqual(bound['gateway']['generation'],418)
            with self.assertRaisesRegex(host['Refused'],'LIVE_IDENTITY_CHANGED'):host['gateway_context_binding'](dict(bound['gateway'],generation=419))

    def test_actual_serving_reader_is_checked(self):
        op=host['Operation'](ASSETS)
        dep={'metadata':{'uid':'ce96a382-140e-4baf-9a85-d623b7df6e3d','generation':3},'spec':{}}
        raw=(builder.REPO/'release/energy-reader-work-repair-20261115-r2/REPORT-RECONSTRUCTED.json').read_bytes()
        with patch.dict(host,{'read':lambda path:raw,'sha':lambda value:'6061194b0c497f516b8b6c55e7f74f435bca5effc378d7f097c8b2d67d0dcd72' if value==host['canonical']({}) else builder.sha(value)}):
            with patch.object(op,'command',return_value=dep) as query:op.reader_binding()
        query.assert_called_once_with(['get','deployment','nginx-security-heai-20261012-r1','-o','json'])

    def test_qualification_uses_cached_image_ordinary_uid_and_public_only_mount(self):
        op=host['Operation'](ASSETS);spec=op.requested['spec']
        self.assertFalse(spec['automountServiceAccountToken'])
        self.assertTrue(all(row['imagePullPolicy']=='Never' for row in spec['containers']+spec['initContainers']))
        row=next(r for r in spec['containers'] if r['name']=='qualification')
        self.assertEqual(row['securityContext']['runAsUser'],1000)
        self.assertTrue(any(m['mountPath']=='/mnt/skills/public' and m['subPath']=='public' and m['readOnly'] for m in row['volumeMounts']))

    def test_host_and_publisher_use_same_new_release_directory(self):
        op=host['Operation'](ASSETS)
        mounted=next(c for c in op.requested['spec']['containers'] if c['name']=='qualification')
        target=next(m['subPath'] for m in mounted['volumeMounts'] if m['mountPath']=='/mnt/skills/public/energy-market-daily')
        self.assertEqual(target,'.energy-work-preview-skill-install-20261115-r2-'+op.operation+'/candidate')
        self.assertIn('.energy-work-preview-skill-install-20261115-r2-',ASSETS['publisher'].decode())
        self.assertEqual(host['CONTROL'].as_posix(),'/var/lib/portal-energy-work-preview-skill-install-20261115-r2')

    def test_four_gateway_budget_and_reader_checks_are_readonly(self):
        self.assertNotIn('patch',host['gateway_context_binding'].__code__.co_names)
        self.assertNotIn('patch',host['Operation'].reader_binding.__code__.co_names)
        self.assertIn('gateway_418_isolated_message_is_link_only',ver['FINAL_TEXT_PROBE'])
        self.assertIn('preview=text[:2000][:2000]',ver['FINAL_TEXT_PROBE'])

    def test_python37_entry_and_sealed_resources_compile(self):
        ast.parse(FILES[builder.NAME].decode(),feature_version=(3,7))
        for key in ('publisher','verifier'):compile(ASSETS[key],'<sealed-'+key+'>','exec')

if __name__=='__main__':unittest.main()

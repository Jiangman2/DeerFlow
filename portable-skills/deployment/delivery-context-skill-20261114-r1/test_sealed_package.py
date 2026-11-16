"""Synthetic receipt-bound packages exist only in disposable test directories."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('context_skill_sealer',HERE / 'seal.py')
seal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seal)


def synthetic_receipt():
    operation='86bbcfd1fb7f40819284fa431ecce9f1'
    plan='880c16308caa9e09267a9cee041577caa0088cbf2b90100cbb5e5a91649bea0f'
    requested='d9a8a00422329427b737b3f67d1c13f35482a16d6614cbc3365b705ad198b616'
    actual='7faa311dc54be05137ec9c756aa278927e78d47f28118f6779415729c9029aa1'
    uid='a62fc04c-7a82-4b57-8fbf-a8c7a3839ea1'
    return {'schema':'task-conversation-context-activation.v1',
            'status':'TASK_CONVERSATION_CONTEXT_DEPLOYED_RUNTIME_ACCEPTED','operation':operation,
            'all_gateway_pods_verified':True,'context_injection_runtime_accepted':True,
            'after':{'uid':uid,'generation':417,'spec_sha256':actual},
            'accepted_source':{'parent_sha256':'a8e8b4f3ff94f79e2e0458414f4c2d55310bdd170a8859c00b99dd1d65ad929c',
                               'candidate_sha256':'e598a0fd0e9d9a83ecdb781be6a345bf974cdef957852d181732f3596ad58330'},
            'verified_pods':70,'verified_source_files_per_pod':14,'service_backends_verified':True,
            'future_scheduled_runs_budget':10000,'global_max_recursion_limit':10000,
            'plan_sha256':plan,'verified_fleet_sha256':'d'*64,
            'context_injection_qualification_basis':'ISOLATED_SOURCE_FUNCTIONS_AND_TRANSPORT_PREFIX_WITH_SYNTHETIC_TASK_ID',
            'loaded_application_modules_verified':False,'business_acceptance_pending':True,
            'tool_revision':'postcheck-r2','readonly':True,'workload_modified':False,'original_receipts_modified':False,
            'patch_request_count':0,'configmap_create_requests':0,'original_patch_not_replayed':True,
            'only_observed_api_default_accepted':True,
            'accepted_original_failure':{'operation':operation,'bytes':2698,
                'sha256':'5f14fb064db5c1d8ea563080f54f5955f3690d08e63665aec25959128999f464',
                'path':'/var/lib/portal-task-conversation-context-20261114-r1/REPORT.json','plan_sha256':plan},
            'requested_spec_sha256':requested,'observed_spec_sha256':actual,
            'accepted_defaulting':{'schema':'task-context-single-defaulting-field-proof.v1','gateway_uid':uid,
                'generation':417,'requested_spec_sha256':requested,'actual_spec_sha256':actual,
                'difference_path':'/template/spec/volumes/11/configMap/defaultMode','actual_default_mode':420,
                'difference_count':1,'field_report_sha256':'f'*64}}


class ReceiptBoundPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='synthetic-context-seal-')
        self.root=Path(self.temporary.name)
        self.receipt=self.root / 'SYNTHETIC-RUNTIME-ACCEPTANCE.json'
        self.receipt.write_bytes(seal.canonical(synthetic_receipt()))
        self.release_existed=seal.RELEASE.exists()

    def tearDown(self):
        self.assertEqual(seal.RELEASE.exists(),self.release_existed)
        self.temporary.cleanup()

    def test_missing_and_incomplete_gateway_receipts_do_not_produce_a_package(self):
        for path in (None,self.root / 'absent.json'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError,'GATEWAY_RUNTIME_ACCEPTANCE_REQUIRED'):
                seal.build(path)
        for key,value in (('status','INCOMPLETE'),('verified_pods',69),('accepted_source',{}),
                          ('context_injection_runtime_accepted',False),('service_backends_verified',False)):
            self.receipt.write_bytes(seal.canonical(dict(synthetic_receipt(),**{key:value})))
            with self.subTest(key=key), self.assertRaisesRegex(ValueError,'GATEWAY_CONTEXT_RUNTIME_ACCEPTANCE_REQUIRED'):
                seal.build(self.receipt)

    def test_deterministic_zip_contains_new_manifest_and_no_candidate_note(self):
        output,metadata=seal.build(self.receipt)
        again,repeated=seal.build(self.receipt)
        self.assertEqual(output,again)
        self.assertEqual(metadata,repeated)
        code=output['energy-market-daily-2.1.2-public-code.zip']
        files=seal.source_files()
        with zipfile.ZipFile(io.BytesIO(code)) as archive:
            self.assertEqual({name.removeprefix('energy-market-daily/'):archive.read(name) for name in archive.namelist()},files)
            self.assertEqual(len(archive.namelist()),107)
        self.assertNotIn('CANDIDATE.md',files)
        self.assertNotIn('assets/browser-runtime.json',files)
        self.assertEqual(json.loads(files['MANIFEST.json'])['version'],'2.1.2-portable')
        self.assertEqual(output['CODE-MANIFEST.json'],files['MANIFEST.json'])
        self.assertEqual(metadata['gateway_receipt_contract']['sha256'],seal.sha(self.receipt.read_bytes()))

    def test_sealed_loader_host_guard_and_later_root_receipt_contract(self):
        output,metadata=seal.build(self.receipt)
        directory=self.root / 'DISPOSABLE-SEALED-TEST'
        seal.write_output(output,directory)
        entry=directory / 'install-energy-delivery-context-skill-20261114-r1.py'
        scope={'__name__':'sealed_test_only'}
        exec(compile(entry.read_bytes(),str(entry),'exec'),scope)
        assets=scope['load_assets']()
        self.assertEqual(len(assets),4)
        self.assertEqual(scope['CANDIDATE_FILES'],108)
        for name,raw in assets.items():
            self.assertEqual(seal.sha(raw),metadata['assets'][name]['sha256'])
        stdout=io.StringIO()
        with patch.object(scope['sys'],'argv',[str(entry),'--install-delivery-context-skill-once']), patch.object(scope['os'],'uname',return_value=SimpleNamespace(nodename='wrong-host'),create=True), patch.object(scope['subprocess'],'Popen',side_effect=AssertionError('no command')), contextlib.redirect_stdout(stdout):
            self.assertEqual(scope['main'](),1)
        row=json.loads(stdout.getvalue())
        self.assertEqual(row['error_code'],'DATAX01_ROOT_REQUIRED')
        self.assertEqual(row['commands'],0)
        self.assertFalse(row['once_claimed'])
        raw=self.receipt.read_bytes()
        scope['read']=lambda path: raw
        binding=scope['gateway_context_binding'](synthetic_receipt()['after'])
        self.assertEqual(binding['sha256'],seal.sha(raw))
        scope['read']=lambda path: raw+b' '
        with self.assertRaisesRegex(ValueError,'GATEWAY_CONTEXT_RECEIPT_CHANGED'):
            scope['gateway_context_binding']()

    def test_changed_existing_output_is_refused(self):
        directory=self.root / 'synthetic-output'
        seal.write_output({'fixed.txt':b'first'},directory)
        with self.assertRaisesRegex(ValueError,'EXISTING_RELEASE_DIFFERS'):
            seal.write_output({'fixed.txt':b'different'},directory)

    def test_duplicate_receipt_fields_are_rejected(self):
        raw=self.receipt.read_bytes()
        self.receipt.write_bytes(raw[:-1]+b',"verified_pods":70}')
        with self.assertRaisesRegex(ValueError,'GATEWAY_RECEIPT_DUPLICATE_KEY'):
            seal.build(self.receipt)

    def test_unreviewed_source_inventory_is_rejected(self):
        source=json.loads(seal.SOURCE_REVIEW.read_text(encoding='utf-8-sig'))
        source['files']['scripts/daily.py']['sha256']='0'*64
        reviewed=self.root / 'changed-review.json'
        reviewed.write_bytes(seal.canonical(source))
        with patch.object(seal,'SOURCE_REVIEW',reviewed), self.assertRaisesRegex(ValueError,'REVIEWED_CANDIDATE_SOURCE_CHANGED'):
            seal.build(self.receipt)

    def test_saved_postcheck_receipt_needs_no_terminal_receipt_metadata(self):
        saved=synthetic_receipt()
        self.assertNotIn('runtime_acceptance_receipt',saved)
        self.receipt.write_bytes(seal.canonical(saved))
        contract,binding=seal.gateway_receipt(self.receipt)
        self.assertEqual(contract['path'],'/var/lib/portal-task-conversation-context-postcheck-20261114-r2/RUNTIME-ACCEPTANCE.json')
        self.assertEqual(contract['bytes'],self.receipt.stat().st_size)
        self.assertEqual(binding['sha256'],seal.sha(self.receipt.read_bytes()))
        incomplete=dict(saved,status='GATEWAY_417_ROLLOUT_IN_PROGRESS_NO_CHANGE')
        self.receipt.write_bytes(seal.canonical(incomplete))
        with self.assertRaisesRegex(ValueError,'GATEWAY_CONTEXT_RUNTIME_ACCEPTANCE_REQUIRED'):
            seal.build(self.receipt)

    def test_first_terminal_json_reconstructs_only_exact_saved_receipt_bytes(self):
        saved=synthetic_receipt()
        saved['observed_at']='2026-11-14T04:00:00+00:00'
        raw=seal.canonical(saved)
        path='/var/lib/portal-task-conversation-context-postcheck-20261114-r2/RUNTIME-ACCEPTANCE.json'
        printed=dict(saved,runtime_acceptance_receipt={'path':path,'bytes':len(raw),'sha256':seal.sha(raw),
            'existing_acceptance_reused':False,'stdout_without_receipt_metadata_matches_saved':True})
        # Presentation whitespace is allowed only outside the reconstructed file.
        self.receipt.write_text(json.dumps(printed,indent=2),encoding='utf-8')
        contract,binding=seal.gateway_receipt(self.receipt)
        self.assertEqual(contract['bytes'],len(raw))
        self.assertEqual(contract['sha256'],seal.sha(raw))
        self.assertEqual(binding['local_input_basis'],'TERMINAL_JSON_RECONSTRUCTED_AND_EXACT_RECEIPT_HASH_VERIFIED')
        later=dict(printed,observed_at='2026-11-14T05:00:00+00:00')
        self.receipt.write_bytes(seal.canonical(later))
        with self.assertRaisesRegex(ValueError,'GATEWAY_PRINTED_RECEIPT_RECONSTRUCTION_MISMATCH'):
            seal.build(self.receipt)
        wrong=dict(printed,runtime_acceptance_receipt=dict(printed['runtime_acceptance_receipt'],path='/var/lib/another/REPORT.json'))
        self.receipt.write_bytes(seal.canonical(wrong))
        with self.assertRaisesRegex(ValueError,'GATEWAY_PRINTED_RECEIPT_METADATA_INVALID'):
            seal.build(self.receipt)
        for reused,matches in ((True,False),(True,True),(False,False),(0,True),(False,1)):
            repeated=dict(printed,runtime_acceptance_receipt=dict(printed['runtime_acceptance_receipt'],
                existing_acceptance_reused=reused,stdout_without_receipt_metadata_matches_saved=matches))
            self.receipt.write_bytes(seal.canonical(repeated))
            with self.assertRaisesRegex(ValueError,'GATEWAY_REPEATED_STDOUT_IS_NOT_SAVED_RECEIPT'):
                seal.build(self.receipt)


if __name__=='__main__':
    unittest.main()

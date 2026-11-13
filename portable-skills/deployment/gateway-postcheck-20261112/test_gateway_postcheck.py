"""Read-only recovery boundaries against the sealed production r4 verifier."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SPEC = importlib.util.spec_from_file_location('gateway_postcheck', HERE / 'verify-energy-daily-result-links-20261112-r1.py')
post = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(post)
SEALED = ROOT / 'release/energy-daily-portable-20261111/gateway-final-r4/activate-energy-daily-result-links-20261111-r4.py'


class PostcheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.release, cls.assets = post.load_release(SEALED)

    def updater(self):
        return post.readonly_updater_type(self.release)(self.assets)

    def deployment(self, updated=70):
        return {'metadata': {'uid': self.release.DEPLOYMENT_UID, 'generation': 414},
                'spec': {'replicas': 70}, 'status': {'observedGeneration':414,'replicas':70 if updated==70 else 71,
                'updatedReplicas':updated,'readyReplicas':70,'availableReplicas':70,'terminatingReplicas':0}}

    def journal(self, image='sha256:' + '1' * 64):
        return {'operation':'a' * 32,'configmap':{'name':'gateway-energy-links-' + 'a' * 12,'uid':'12345678-1234-1234-1234-123456789abc'},
                'sample':{'image_id_sha256':post.sha(image.encode())},'openssl_compatibility':{'fixed':'openssl'}}

    def test_original_sealed_assets_are_loaded_without_running_main(self):
        self.assertEqual(set(self.assets), set(self.release.ASSET_NAMES) | {'pre'})
        self.assertEqual(len(self.release.FILE_KEYS), 6)

    def test_tampered_script_is_rejected_before_execution(self):
        with tempfile.TemporaryDirectory(prefix='post-script-') as temporary:
            path = Path(temporary) / 'tampered.py'
            path.write_bytes(b'raise AssertionError("must not execute")')
            with self.assertRaisesRegex(post.Refused, 'ORIGINAL_R4_SCRIPT_CHANGED'):
                post.load_release(path)

    def test_unchanged_health_child_matches_original_r4_exactly(self):
        updater = self.updater()
        captured = []
        class Captured(Exception):
            pass
        def capture(*args, **kwargs):
            captured.append(args[2])
            raise Captured()
        with patch.object(updater.pre, 'mount_projection', return_value=({}, {})), patch.object(updater, 'child', side_effect=capture):
            with self.assertRaises(Captured):
                updater.source({'metadata':{'name':'gateway-fixture'}}, health=True)
        self.assertEqual(captured, [post.health_source(self.release)])

    def test_mutating_commands_are_rejected_before_parent_transport(self):
        updater = self.updater()
        with patch.object(self.release.Updater, 'command', side_effect=AssertionError('must not execute')):
            for args in (['patch','deployment','gateway'],['delete','pod','gateway-fixture'],['create','-f','-']):
                with self.assertRaisesRegex(post.Refused, 'READONLY_COMMAND_REQUIRED'):
                    updater.command(args)

    def test_get_with_input_body_is_rejected(self):
        with self.assertRaisesRegex(post.Refused, 'READONLY_COMMAND_REQUIRED'):
            self.updater().command(['get','deployment','gateway','-o','json'], input_bytes=b'{}')

    def test_only_fixed_gateway_readers_can_be_execed(self):
        updater = self.updater()
        updater.known_pods.add('gateway-fixture')
        args = ['exec','gateway-fixture','-c','gateway','--','python3','-I','-B','-u','-c',updater.allowed_health_source,'{}']
        with patch.object(self.release.Updater, 'command', return_value={'fixture':'pass'}) as parent:
            self.assertEqual(updater.command(args), {'fixture':'pass'})
            self.assertEqual(parent.call_count, 1)
            for index, value in ((1,'another-pod'),(3,'maintenance'),(10,'print("arbitrary script")')):
                changed = list(args)
                changed[index] = value
                with self.assertRaisesRegex(post.Refused, 'FIXED_READONLY_CHILD_REQUIRED'):
                    updater.command(changed)
            self.assertEqual(parent.call_count, 1)

    def test_journal_override_never_writes_original_receipts(self):
        updater = self.updater()
        updater.report['once_claimed'] = True
        with patch.object(self.release, 'private_write', side_effect=AssertionError('must not write')):
            updater.journal('READONLY_FIXTURE')
        self.assertEqual(updater.report['last_step'], 'READONLY_FIXTURE')

    def test_pending_rollout_returns_without_wait_or_fleet_check(self):
        updater = self.updater()
        deployment = self.deployment(updated=10)
        binding = {'fixture':'evidence'}
        with patch.object(post, 'journal_snapshot', return_value=(self.journal(), {}, deployment['spec'], binding)), \
             patch.object(updater, 'get', return_value=deployment) as get, \
             patch.object(updater, 'verify', side_effect=AssertionError('not converged')), \
             patch.object(self.release.time, 'sleep', side_effect=AssertionError('must not wait')):
            out = updater.run_postcheck()
        self.assertEqual(out['status'], 'GATEWAY_414_ROLLOUT_IN_PROGRESS_NO_CHANGE')
        self.assertEqual(out['current']['updated'], 10)
        self.assertEqual(get.call_count, 1)
        self.assertFalse(out['rollout_waited'])

    def test_competing_deployment_is_rejected_without_verifying(self):
        updater = self.updater()
        deployment = self.deployment(updated=10)
        deployment['metadata']['generation'] = 415
        with patch.object(post, 'journal_snapshot', return_value=(self.journal(), {}, deployment['spec'], {})), \
             patch.object(updater, 'get', return_value=deployment), \
             patch.object(updater, 'verify', side_effect=AssertionError('must not verify')):
            with self.assertRaisesRegex(self.release.Refused, 'POST_PATCH_SPEC_DRIFT'):
                updater.run_postcheck()

    def full_fixtures(self, image='sha256:' + '1' * 64):
        updater = self.updater()
        deployment = self.deployment()
        journal = self.journal()
        pod = {'metadata':{'name':'gateway-fixture','uid':'12345678-1234-1234-1234-123456789abc'},
               'status':{'containerStatuses':[{'imageID':image}]}}
        cm = self.release.configmap_manifest(journal['operation'], journal['configmap']['name'], self.assets)
        cm['metadata']['uid'] = journal['configmap']['uid']
        objects = {('deployment','gateway'):deployment, ('pod','gateway-fixture'):pod, ('configmap',journal['configmap']['name']):cm,
                   ('configmap',self.release.OPENSSL_CM):{},('service','gateway'):{'metadata':{'uid':'service-fixture'},'spec':{'selector':{'app':'gateway'}}}}
        return updater, deployment, journal, pod, objects

    def test_image_id_is_bound_to_pre_rollout_journal(self):
        updater, deployment, journal, pod, objects = self.full_fixtures(image='sha256:' + '2' * 64)
        candidates = [('gateway-fixture', pod['metadata']['uid'])] * 70
        with patch.object(post, 'journal_snapshot', return_value=(journal, {}, deployment['spec'], {})), \
             patch.object(updater, 'get', side_effect=lambda kind,name:copy.deepcopy(objects[(kind,name)])), \
             patch.object(self.release, 'openssl_identity', return_value=journal['openssl_compatibility']), \
             patch.object(updater.pre, 'parse_candidates', return_value=candidates), \
             patch.object(updater, 'command', return_value='fixture'), \
             patch.object(updater, 'verify', side_effect=AssertionError('must not verify changed image')):
            with self.assertRaisesRegex(post.Refused, 'BASELINE_IMAGE_ID_CHANGED'):
                updater.run_postcheck()

    def test_completed_check_reports_hash_of_all_verified_pods(self):
        updater, deployment, journal, pod, objects = self.full_fixtures()
        candidates = [('gateway-fixture', pod['metadata']['uid'])] * 70
        verified = [{'fixture':index} for index in range(70)]
        def verify(_):
            updater.report.update(verified_pods=verified,all_gateway_pods_verified=True)
        with patch.object(post, 'journal_snapshot', return_value=(journal, {}, deployment['spec'], {'same':'evidence'})), \
             patch.object(updater, 'get', side_effect=lambda kind,name:copy.deepcopy(objects[(kind,name)])), \
             patch.object(self.release, 'openssl_identity', return_value=journal['openssl_compatibility']), \
             patch.object(updater.pre, 'parse_candidates', return_value=candidates), \
             patch.object(updater, 'command', return_value='fixture'), patch.object(updater, 'verify', side_effect=verify):
            out = updater.run_postcheck()
        self.assertEqual(out['status'], 'GATEWAY_414_RESULT_LINKS_READONLY_RUNTIME_ACCEPTED')
        self.assertEqual(out['verified_pod_count'], 70)
        self.assertEqual(out['verified_pods_sha256'], post.sha(post.canonical(verified)))
        self.assertNotIn('verified_pods', out)
        self.assertFalse(out['service_matches_original_pre_rollout_snapshot_proven'])

    def test_current_evidence_change_after_fleet_check_is_rejected(self):
        updater, deployment, journal, pod, objects = self.full_fixtures()
        candidates = [('gateway-fixture', pod['metadata']['uid'])] * 70
        snapshots = [(journal, {}, deployment['spec'], {'one':'evidence'}),(journal, {}, deployment['spec'], {'changed':'evidence'})]
        with patch.object(post, 'journal_snapshot', side_effect=snapshots), \
             patch.object(updater, 'get', side_effect=lambda kind,name:copy.deepcopy(objects[(kind,name)])), \
             patch.object(self.release, 'openssl_identity', return_value=journal['openssl_compatibility']), \
             patch.object(updater.pre, 'parse_candidates', return_value=candidates), \
             patch.object(updater, 'command', return_value='fixture'), patch.object(updater, 'verify'):
            with self.assertRaisesRegex(post.Refused, 'ORIGINAL_EVIDENCE_CHANGED_DURING_POSTCHECK'):
                updater.run_postcheck()

    def journal_files(self, temporary):
        control = Path(temporary)
        before, expected, identity = {'fixture':'before'}, {'fixture':'candidate'}, {'generation':413,'fixture':'identity'}
        receipt = self.journal()
        receipt.update(schema='energy-daily-gateway-result-link-activation.v4',once_claimed=True,patch_acknowledged=True,
                       patch_request_count=1,maintenance_absent=True,before=identity,
                       qualification={'status':'GATEWAY_CONVERSATION_LINK_SYNTHETIC_ACCEPTED','candidate_files_verified':6})
        (control / 'BEFORE-DEPLOYMENT.json').write_bytes(post.canonical(before))
        (control / 'CANDIDATE-SPEC.json').write_bytes(post.canonical(expected))
        (control / 'JOURNAL-0000.json').write_bytes(post.canonical(receipt))
        fake = types.SimpleNamespace(CONTROL=control,load_assets=lambda:{'pre':types.SimpleNamespace(deployment_guard=lambda obj:None)},
                                     strict_identity=lambda obj:identity,candidate_spec=lambda obj,name:expected)
        return control, fake, receipt, before, expected

    def test_existing_journal_scope_and_private_hashes_are_reconciled(self):
        with tempfile.TemporaryDirectory(prefix='post-journal-') as temporary:
            control, fake, receipt, before, expected = self.journal_files(temporary)
            result = post.journal_snapshot(fake, control)
            self.assertEqual(result[:3], (receipt, before, expected))
            self.assertEqual(result[3]['journal_sha256'], post.sha((control / 'JOURNAL-0000.json').read_bytes()))

    def test_missing_journal_sequence_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='post-journal-') as temporary:
            control, fake, receipt, before, expected = self.journal_files(temporary)
            (control / 'JOURNAL-0000.json').rename(control / 'JOURNAL-0001.json')
            with self.assertRaisesRegex(post.Refused, 'JOURNAL_SEQUENCE_CHANGED'):
                post.journal_snapshot(fake, control)

    def test_unacknowledged_patch_requires_manual_review(self):
        with tempfile.TemporaryDirectory(prefix='post-journal-') as temporary:
            control, fake, receipt, before, expected = self.journal_files(temporary)
            receipt['patch_acknowledged'] = False
            (control / 'JOURNAL-0000.json').write_bytes(post.canonical(receipt))
            with self.assertRaisesRegex(post.Refused, 'ACKNOWLEDGED_PATCH_JOURNAL_REQUIRED'):
                post.journal_snapshot(fake, control)

    def test_saved_candidate_cannot_expand_original_patch_scope(self):
        with tempfile.TemporaryDirectory(prefix='post-journal-') as temporary:
            control, fake, receipt, before, expected = self.journal_files(temporary)
            (control / 'CANDIDATE-SPEC.json').write_bytes(post.canonical({'unrelated':'change'}))
            with self.assertRaisesRegex(post.Refused, 'SAVED_CANDIDATE_SCOPE_CHANGED'):
                post.journal_snapshot(fake, control)


if __name__ == '__main__':
    unittest.main()

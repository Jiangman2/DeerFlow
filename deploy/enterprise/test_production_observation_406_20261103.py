"""Accept only the sealed observation and known format-equivalent sources."""
import copy
import json
import unittest

import production_observation_406_20261103 as audit


class ObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.read = staticmethod(audit.reader())
        cls.manifest = json.loads(cls.read(audit.MANIFEST))
        cls.report = json.loads(cls.read(audit.ARCHIVE + "OPERATOR.json"))

    def changed(self, path):
        return lambda name: self.read(name) + (b"\n# changed\n" if name == path else b"")

    def test_original_false_positives_reconciled_without_changing_receipt(self):
        self.assertEqual(self.report["samples"][0]["observation"]["source_comparison"]["counts"]["mismatch"], 14)
        result = audit.verify(self.read)
        self.assertEqual(result["per_sample_python_sources"], {"exact_lf_hash": 613, "qualified_format_only": 22, "unexplained": 0})
        self.assertFalse(result["all_gateway_processes_verified"])

    def test_manifest_tampering_rejected(self):
        with self.assertRaisesRegex(AssertionError, "MANIFEST_CHANGED"):
            audit.verify(self.changed(audit.MANIFEST))

    def test_each_original_evidence_file_is_immutable(self):
        for name in self.manifest["evidence"]:
            with self.subTest(name=name), self.assertRaisesRegex(AssertionError, "EVIDENCE_CHANGED"):
                audit.verify(self.changed(audit.ARCHIVE + name))

    def test_changed_runtime_code_is_not_accepted_as_formatting(self):
        for row in self.manifest["format_variants"]:
            with self.subTest(path=row["path"]), self.assertRaisesRegex(AssertionError, "ENTERPRISE_SOURCE_CHANGED"):
                audit.verify(self.changed(row["path"]))

    def test_unchanged_runtime_file_still_enforced(self):
        with self.assertRaisesRegex(AssertionError, "ENTERPRISE_SOURCE_CHANGED"):
            audit.verify(self.changed("backend/app/gateway/oa_policy_flow_config.py"))

    def test_every_historical_format_snapshot_is_enforced(self):
        for row in self.manifest["format_variants"]:
            with self.subTest(path=row["path"]), self.assertRaisesRegex(AssertionError, "FORMAT_ORIGINAL_CHANGED"):
                audit.verify(self.changed(row["original_archive"]))

    def test_pending_frontend_copy_is_bound(self):
        with self.assertRaisesRegex(AssertionError, "PENDING_FRONTEND_CHANGED"):
            audit.verify(self.changed(self.manifest["pending_frontend"]["runtime_path"]))

    def check_bad_report(self, mutate, pattern):
        report = copy.deepcopy(self.report)
        mutate(report)
        with self.assertRaisesRegex(AssertionError, pattern):
            audit.reconcile_observation(report, self.manifest)

    def test_unknown_runtime_hash_rejected(self):
        self.check_bad_report(lambda r: r["samples"][0]["observation"]["source_comparison"]["differences"][0].update(sha256="0" * 64), "UNRECOGNIZED_RUNTIME_HASH")

    def test_missing_node_rejected(self):
        self.check_bad_report(lambda r: r["samples"].pop(), "SAMPLE_NODE_SCOPE")

    def test_database_or_business_access_overclaim_rejected(self):
        self.check_bad_report(lambda r: r.update(database_access=True), "READONLY_SCOPE")
        self.check_bad_report(lambda r: r.update(browser_business_acceptance_claimed=True), "BUSINESS_OVERCLAIM")

    def test_all_processes_overclaim_rejected(self):
        self.check_bad_report(lambda r: r.update(all_gateway_processes_verified=True), "SCOPE_OVERCLAIM")

    def test_process_environment_failure_not_masked_by_pod_template(self):
        self.check_bad_report(lambda r: r["samples"][0]["observation"]["gateway_processes"]["processes"][0]["environment"]["OA_POLICY_FLOW_RUN_TIMEOUT_SECONDS"].update(matches_expected=False, value=1800), "START_ENV_MISMATCH")

    def test_absent_explicit_oa_flag_is_not_misclassified_as_disabled(self):
        self.assertEqual(self.report["deployment_configuration"]["OA_POLICY_FLOW_ENABLED"]["explicit_count"], 0)
        self.assertEqual(audit.reconcile_observation(self.report, self.manifest)["unexplained"], 0)


if __name__ == "__main__":
    unittest.main()

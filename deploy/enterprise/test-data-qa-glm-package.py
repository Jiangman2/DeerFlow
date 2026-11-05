"""Offline negative checks for a packaged importer; Python 3.7, no cluster."""

import copy
import pathlib
import sys
import unittest

SCRIPT = pathlib.Path(sys.argv.pop(1))


class PackageChecks(unittest.TestCase):
    def setUp(self):
        self.scope = {"__name__": "offline_package_checks"}
        exec(compile(SCRIPT.read_bytes(), str(SCRIPT), "exec"), self.scope)

    def test_exact_payload_closure_and_base_metadata(self):
        self.scope["accepted_nfs_gate"]()
        _, _, profile, payload = self.scope["load_inputs"]()
        self.assertEqual(len(self.scope["ROUTE_PIN"]), 15)
        self.assertEqual(len(payload["roles"]), 1)
        self.assertEqual(profile["schema"], "portal-gateway-data-qa-glm-profile.v1")
        self.assertEqual(profile["roles"][0]["expected_layer_count"], 67)
        self.assertTrue(all(p.startswith("app/backend/packages/harness/deerflow/") and p.endswith(".py") for p in self.scope["ROUTE_PIN"]))
        self.assertFalse(self.scope["PRODUCT_QUALIFICATION"]["production_deployed"])

    def test_changed_embedded_helper_rejected(self):
        self.scope["SOURCES"]["preparer"] += b"\n"
        with self.assertRaises(self.scope["Refused"]):
            self.scope["load_inputs"]()

    def test_changed_blob_rejected(self):
        self.scope["SOURCES"]["blobs"] += b" "
        with self.assertRaises(self.scope["Refused"]):
            self.scope["load_inputs"]()

    def test_unreviewed_file_scope_rejected(self):
        self.scope["PAYLOAD_LIMITS"]["app/backend/config.yaml"] = 1024
        with self.assertRaises(self.scope["Refused"]):
            self.scope["load_inputs"]()

    def test_changed_field_receipt_rejected(self):
        self.scope["PRODUCT_QUALIFICATION"] = copy.deepcopy(self.scope["PRODUCT_QUALIFICATION"])
        self.scope["PRODUCT_QUALIFICATION"]["data_qa"]["report_sha256"] = "0" * 64
        with self.assertRaises(self.scope["Refused"]):
            self.scope["accepted_nfs_gate"]()

    def test_changed_expected_source_rejected(self):
        self.scope["ROUTE_PIN"] = copy.deepcopy(self.scope["ROUTE_PIN"])
        first = next(iter(self.scope["ROUTE_PIN"]))
        self.scope["ROUTE_PIN"][first]["sha256"] = "0" * 64
        with self.assertRaises(self.scope["Refused"]):
            self.scope["load_inputs"]()


if __name__ == "__main__":
    unittest.main()

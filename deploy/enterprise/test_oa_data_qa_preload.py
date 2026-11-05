"""Qualify a generated preload tool on Linux Python 3.7 without containerd."""

import base64
import copy
import hashlib
import json
import os
import pathlib
import subprocess
import unittest
from unittest.mock import patch

SCRIPT = pathlib.Path(os.environ["OA_PRELOAD_SCRIPT"])
SOURCE = SCRIPT.read_bytes()


def load():
    scope = {"__name__": "offline_qualification"}
    exec(compile(SOURCE, str(SCRIPT), "exec"), scope)
    return scope


class PreloadTests(unittest.TestCase):
    def setUp(self):
        self.n = load()

    def test_no_external_actions_during_validation(self):
        with patch.object(subprocess, "run", side_effect=AssertionError("external_command")):
            with patch.object(subprocess, "Popen", side_effect=AssertionError("external_process")):
                self.n["accepted_nfs_gate"]()
                scope, resource, profile, prepared = self.n["load_inputs"]()
        self.assertEqual(set(prepared["roles"]), {"gateway-forward"})

    def test_exact_ten_joint_candidate_files(self):
        scope, resource, profile, prepared = self.n["load_inputs"]()
        files = profile["tail_files"]["gateway-forward"]["files"]
        self.assertEqual(files, self.n["ROUTE_PIN"])
        self.assertEqual({path.rsplit("/", 1)[-1] for path in files}, {"oa_policy_flow_config.py", "oa_policy_flow_results.py", "oa_policy_flow_service.py", "oa_policy_flow.py", "services.py", "model.py", "0021_oa_result_sets.py", "tools.py", "data_qa_stream.py", "data_qa_progress.py"})

    def test_only_one_new_layer(self):
        scope, resource, profile, prepared = self.n["load_inputs"]()
        role = prepared["roles"]["gateway-forward"]
        self.assertEqual(len(role["manifest"]["layers"]), 68)
        self.assertEqual(profile["bases"]["gateway"]["layer_count"], 67)
        self.assertEqual(len(role["checked"]["tail_layers"]), 1)

    def test_base_config_unchanged_except_diffid_history(self):
        scope, resource, profile, prepared = self.n["load_inputs"]()
        config = copy.deepcopy(prepared["roles"]["gateway-forward"]["config"])
        config["rootfs"]["diff_ids"].pop()
        config["history"].pop()
        self.assertEqual(self.n["sha"](self.n["canonical"](config)), profile["bases"]["gateway"]["config_object_sha256"])

    def test_previous_reference_preserved(self):
        scope, resource, profile, prepared = self.n["load_inputs"]()
        base = profile["bases"]["gateway"]
        self.assertEqual(base["manifest_digest"], "sha256:eeeb190145dd6677e0da372bbdf36ab207a6826d89960eca2ed49de15d40dcc5")
        self.assertEqual(profile["preserve"]["gateway"][base["tag"]], base["manifest_digest"])

    def test_fixed_five_nodes(self):
        scope, resource, profile, prepared = self.n["load_inputs"]()
        self.assertEqual(set(profile["nodes"]), {f"ecs-zdz-pro-{i:04d}" for i in range(18, 23)})
        for host in profile["nodes"]:
            node, roles = scope.node_roles(host)
            self.assertEqual([r["role"] for r in roles], ["gateway-forward"])
        with self.assertRaises(scope.Failure):
            scope.node_roles("unexpected-node")

    def test_canary_receipt_mutation_refused(self):
        self.n["PRODUCT_QUALIFICATION"]["base_manifest"] = "wrong"
        with self.assertRaises(self.n["Refused"]):
            self.n["accepted_nfs_gate"]()

    def test_candidate_file_pin_mutation_refused(self):
        self.n["ROUTE_PIN"][next(iter(self.n["ROUTE_PIN"]))]["sha256"] = "0" * 64
        with self.assertRaises(self.n["Refused"]):
            self.n["accepted_nfs_gate"]()

    def test_embedded_helper_mutation_refused(self):
        self.n["SOURCES"]["preparer"] += b"\n"
        with self.assertRaises(self.n["Refused"]):
            self.n["load_inputs"]()

    def test_blob_mutation_refused_even_with_outer_hash_updated(self):
        value = json.loads(self.n["SOURCES"]["blobs"])
        name = next(iter(value))
        value[name] = base64.b64encode(b"not the image").decode()
        raw = self.n["canonical"](value)
        self.n["SOURCES"]["blobs"] = raw
        self.n["SOURCE_PINS"]["blobs"] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        with self.assertRaises(self.n["Refused"]):
            self.n["load_inputs"]()

    def test_extra_payload_path_refused(self):
        self.n["PAYLOAD_LIMITS"]["app/config.yaml"] = 1024
        with self.assertRaises(self.n["Refused"]):
            self.n["load_inputs"]()

    def test_fresh_once_directory_and_explicit_flag(self):
        self.assertEqual(self.n["CONTROL"], "/var/lib/portal-gateway-oa-data-qa-node-20261021-r1")
        self.assertIn(b"--preload-gateway-oa-data-qa-once", SOURCE)
        self.assertNotIn(b"--prepare-gateway-haineng-brand-once", SOURCE)


if __name__ == "__main__":
    unittest.main()

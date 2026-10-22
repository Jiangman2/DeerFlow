"""Offline Linux qualification of the packaged joint controller (Python 3.7)."""

import copy
import importlib.util
import os
import pathlib
import tempfile
import unittest

FOLDER = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("glm_fixtures", FOLDER / "test_glm_stream_activation.py")
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)


def load():
    scope = {"__name__": "qualification"}
    exec(compile(pathlib.Path(os.environ["JOINT_ACTIVATION_SCRIPT"]).read_bytes(), "<joint>", "exec"), scope)
    return scope


class JointFake(old.FakeController):
    def __init__(self, scope):
        super().__init__(scope)
        self.d["metadata"].update(name="gateway", namespace="deer-flow")
        self.d["spec"]["template"]["spec"].update(volumes=[dict(name="config", configMap=dict(name=scope["PLANNER"]["CONFIG"]))])
        scope["CURRENT_BINDING"] = scope["binding"](self.d)
        scope["configuration_gate"] = lambda c, backend: c.report.update(config_file_sha256="a" * 64)
        self.old_reference = False
        self.inventory_drift = False
        self.bad_live = False
        self.patch_failure = False
        self.report["config_file_sha256"] = "a" * 64

    def remote(self, pod, payload):
        self.remote_count += 1
        result = old.runtime(payload["stream_fixed"])
        if payload.get("references"):
            result["agent_references"] = dict(status="OLD_REFERENCES_FOUND" if self.old_reference else "PASS", affected_files=int(self.old_reference), modified=False, inventory_sha256=("b" if self.inventory_drift and self.remote_count == 2 else "a") * 64)
        if payload.get("data_qa"):
            result["data_qa"] = copy.deepcopy(self.n["ACCEPTANCE"]["candidate_checks"][0]["observation"])
            if self.bad_live:
                result["data_qa"]["status"] = "FAIL"
        return result

    def patch(self, patch):
        self.patches.append(patch)
        if self.patch_failure:
            raise self.n["Refused"]("patch_outcome_uncertain")
        _, expected = self.n["plan"](self.d)
        self.d["spec"] = expected
        self.d["metadata"]["generation"] += 1
        self.d["status"]["observedGeneration"] += 1
        return copy.deepcopy(self.d)


class JointActivationTests(unittest.TestCase):
    def setUp(self):
        self.n = load()

    def test_five_preloads_and_two_actual_candidate_receipts(self):
        self.n["preload_gate"]()
        self.n["candidate_receipt_gate"](self.n["ACCEPTANCE"])
        self.assertEqual(self.n["MD"], self.n["PREP"]["RELEASE"]["profile"]["roles"][0]["manifest_digest"])

    def test_partial_candidate_cannot_be_accepted(self):
        for path in ("status", "disabled_button_hint", "dedicated_key_resolved", "external_paths_denied"):
            value = copy.deepcopy(self.n["ACCEPTANCE"])
            value["candidate_checks"][1]["observation"][path] = None
            with self.assertRaises(self.n["Refused"]):
                self.n["candidate_receipt_gate"](value)

    def test_three_paired_writes_and_full_cas(self):
        c = JointFake(self.n)
        patch, expected = self.n["plan"](c.d)
        self.assertEqual(len([v for v in patch if v["op"] != "test"]), 3)
        self.assertEqual(next(v["value"] for v in patch if v["path"] == "/spec"), c.d["spec"])
        pod = expected["template"]["spec"]
        self.assertEqual(pod["volumes"][0]["configMap"]["name"], self.n["ACCEPTANCE"]["prepared_config"]["name"])
        self.assertEqual(pod["containers"][0]["env"][-1]["valueFrom"]["secretKeyRef"]["name"], self.n["ACCEPTANCE"]["prepared_secret"]["name"])

    def test_one_patch_all_70_runtime_checks(self):
        c = JointFake(self.n)
        self.n["execute"](c, {})
        self.assertEqual(len(c.patches), 1)
        self.assertEqual(c.remote_count, 72)
        self.assertEqual(c.report["data_qa_runtime_accepted"], 70)
        self.assertTrue(c.report["other_configuration_preserved"])
        self.assertFalse(c.report["business_values_verified"])
        c.helper.write_new.assert_called_once()

    def test_old_reference_or_different_views_stops_before_patch(self):
        for flag in ("old_reference", "inventory_drift", "front_drift"):
            n = load()
            c = JointFake(n)
            setattr(c, flag, True)
            with self.assertRaises(n["Refused"]):
                n["execute"](c, {})
            self.assertEqual(c.patches, [])
            c.helper.write_new.assert_not_called()

    def test_runtime_failure_never_claims_acceptance_or_repatches(self):
        c = JointFake(self.n)
        c.bad_live = True
        with self.assertRaisesRegex(self.n["Refused"], "data_qa_runtime"):
            self.n["execute"](c, {})
        self.assertEqual(len(c.patches), 1)
        self.assertFalse(c.report.get("stage_passed"))

    def test_uncertain_patch_not_retried(self):
        c = JointFake(self.n)
        c.patch_failure = True
        with self.assertRaisesRegex(self.n["Refused"], "patch_outcome_uncertain"):
            self.n["execute"](c, {})
        self.assertEqual(len(c.patches), 1)

    def test_rollout_timeout_does_not_send_another_patch(self):
        c = JointFake(self.n)
        c.never_ready = True
        ticks = iter((0, 0, 5000))
        with self.assertRaisesRegex(self.n["Refused"], "rollout_deadline"):
            self.n["execute"](c, {}, now=lambda: next(ticks), sleep=lambda _: None)
        self.assertEqual(len(c.patches), 1)

    def test_runtime_has_only_live_config_path_and_accepted_source_files(self):
        runtime = self.n["SOURCES"]["runtime"]
        self.assertLess(len(runtime), 110000)
        self.assertNotIn(b'pathlib.Path("/candidate/config.yaml")', runtime)
        self.assertIn(b"AppConfig.resolve_config_path()", runtime)
        for name, raw in self.n["SOURCES"].items():
            self.assertEqual(self.n["pin"](raw), self.n["PINS"][name])
            compile(raw, name, "exec")

    def test_paired_rollback_restores_old_spec_and_rejects_later_change(self):
        c = JointFake(self.n)
        old_spec = copy.deepcopy(c.d["spec"])
        patch, expected = self.n["plan"](c.d)
        c.patch(patch)
        rollback = self.n["PLANNER"]["rollback_plan"](c.d, accepted_new_spec=expected, saved_old_spec=old_spec)
        self.assertEqual(rollback[-1]["value"], old_spec)
        c.d["spec"]["replicas"] = 71
        with self.assertRaises(ValueError):
            self.n["PLANNER"]["rollback_plan"](c.d, accepted_new_spec=expected, saved_old_spec=old_spec)


class AgentInventoryTests(unittest.TestCase):
    def setUp(self):
        self.scope = {}
        exec(compile((FOLDER / "check-data-qa-agent-references.py").read_bytes(), "<inventory>", "exec"), self.scope)

    def scan(self, root):
        return self.scope["agent_references"](root)

    def test_all_three_roots_and_no_business_text_export(self):
        with tempfile.TemporaryDirectory() as folder:
            root = pathlib.Path(folder)
            for relative in ("agents/one", "team_agents/two", "users/u/agents/three"):
                p = root / relative
                p.mkdir(parents=True)
                (p / "config.yaml").write_text("private value", encoding="utf-8")
                (p / "SOUL.md").write_text("use datalake_detail_qa", encoding="utf-8")
            result = self.scan(root)
            self.assertEqual(result["files_checked"], 6)
            self.assertEqual(result["affected_files"], 3)
            self.assertNotIn("private value", str(result))
            self.assertNotIn("three", str(result))

    def test_absent_agent_directories_are_empty(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.scan(folder)["status"], "PASS")

    def test_symlink_or_oversized_file_refuses(self):
        for kind in ("link", "large"):
            with tempfile.TemporaryDirectory() as folder:
                root = pathlib.Path(folder)
                agent = root / "agents/a"
                agent.mkdir(parents=True)
                file = agent / "SOUL.md"
                if kind == "link":
                    file.symlink_to(root / "missing")
                else:
                    file.write_bytes(b"x" * 1048577)
                with self.assertRaises((ValueError, OSError)):
                    self.scan(root)


if __name__ == "__main__":
    unittest.main()

"""Offline host boundaries; no Kubernetes, production files or network used."""

import ast
import base64
import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


BASE = Path(__file__).resolve().parents[1]
PATH = BASE / "deploy/enterprise/update-energy-market-chat-workflow-20261110-r1.py"
SPEC = importlib.util.spec_from_file_location("energy_chatfix_host_test", PATH)
host = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(host)
PRIVATE = "PRIVATE_SENTINEL_NO_EXPORT_49362"


def deployment(name="gateway", generation=500):
    container = "collector" if name == host.COLLECTOR else name
    mount = {"name": "skills", "mountPath": "/mnt/skills", "readOnly": True}
    if container == "collector":
        mount.update(mountPath="/opt/energy-skill", subPath="public/energy-market-daily")
    return {
        "metadata": {"name": name, "uid": name + "-uid", "generation": generation},
        "spec": {"replicas": 1, "selector": {"matchLabels": {"app": name}},
                 "template": {"spec": {"containers": [{"name": container, "image": "fixed-image", "volumeMounts": [mount]}],
                                       "volumes": [{"name": "skills", "persistentVolumeClaim": {"claimName": host.PVC}}]}}},
        "status": {"observedGeneration": generation, "replicas": 1, "updatedReplicas": 1, "readyReplicas": 1, "availableReplicas": 1},
    }


def rs(dep, name="current-rs", uid="current-rs-uid"):
    return {"metadata": {"name": name, "uid": uid, "ownerReferences": [{"kind": "Deployment", "name": dep["metadata"]["name"], "uid": dep["metadata"]["uid"], "controller": True}]}}


def pod(dep, replica, name="good-pod", node="worker-1"):
    obj = {"metadata": {"name": name, "uid": name + "-uid", "ownerReferences": [{"kind": "ReplicaSet", "name": replica["metadata"]["name"], "uid": replica["metadata"]["uid"], "controller": True}]},
           "spec": copy.deepcopy(dep["spec"]["template"]["spec"]),
           "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}], "containerStatuses": []}}
    obj["spec"]["nodeName"] = node
    for container in obj["spec"]["containers"]:
        obj["status"]["containerStatuses"].append({"name": container["name"], "ready": True, "imageID": "sha256:runtime", "containerID": "containerd://synthetic",
            "restartCount": 0, "state": {"running": {"startedAt": "2026-11-10T00:00:00Z"}}})
    return obj


def maintenance(tool, uid="owned-maintenance-uid"):
    obj = host.maintenance_manifest(tool.maintenance, tool.operation)
    obj["metadata"]["uid"] = uid
    obj["spec"]["nodeName"] = host.NODE
    obj["status"] = {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}],
                     "containerStatuses": [{"name": "maintenance", "ready": True, "imageID": host.IMAGE_ID, "containerID": "containerd://owned",
                                            "restartCount": 0, "state": {"running": {"startedAt": "2026-11-10T00:00:00Z"}}}]}
    return obj


class HostBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tool = host.Update()

    def test_wrong_host_fails_before_cluster_or_filesystem(self):
        with patch.object(host.os, "name", "nt"), patch.object(host.subprocess, "Popen") as process:
            with self.assertRaises(host.Refused) as caught:
                self.tool.run()
        self.assertEqual(caught.exception.code, "DATAX01_ROOT_REQUIRED")
        self.assertFalse(self.tool.report["once_claimed"])
        process.assert_not_called()

    def test_unsealed_release_refuses_before_creating_resources(self):
        with patch.multiple(host, CHILD_B64="UNSEALED", CHILD_SHA256="UNSEALED", PAYLOAD_B64="UNSEALED", PAYLOAD_SHA256="UNSEALED"):
            with self.assertRaises(host.Refused) as caught:
                host.load_sealed()
        self.assertEqual(caught.exception.code, "UNSEALED_TOOL")

    def test_maintenance_has_only_skills_tmp_and_no_secret_or_user_data(self):
        obj = host.maintenance_manifest(self.tool.maintenance, self.tool.operation)
        spec = obj["spec"]
        self.assertFalse(spec["automountServiceAccountToken"])
        self.assertEqual(spec["nodeSelector"], {"kubernetes.io/hostname": host.NODE})
        self.assertEqual(len(spec["containers"]), 1)
        container = spec["containers"][0]
        self.assertEqual(container["imagePullPolicy"], "Never")
        self.assertEqual(container["image"], host.IMAGE)
        self.assertEqual(container["args"][-1], "import time;time.sleep(1200)")
        self.assertEqual(spec["activeDeadlineSeconds"], 1200)
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertEqual({v["name"] for v in spec["volumes"]}, {"skills", "tmp"})
        self.assertEqual({v["mountPath"] for v in container["volumeMounts"]}, {"/mnt/skills", "/tmp"})
        self.assertNotIn("secret", json.dumps(obj).lower())
        self.assertNotIn("user-data", json.dumps(obj))
        self.assertNotIn("envFrom", container)

    def test_command_scope_rejects_secret_reads_and_workload_mutations(self):
        with patch.object(host.subprocess, "Popen") as process:
            for args in (["get", "secret", "private", "-o", "json"], ["patch", "deployment", "gateway"], ["apply", "-f", "-"], ["rollout", "restart", "deployment/gateway"]):
                with self.subTest(args=args), self.assertRaises(host.Refused):
                    self.tool.command(args)
        process.assert_not_called()

    def test_create_scope_accepts_only_exact_owned_maintenance_manifest(self):
        valid = host.maintenance_manifest(self.tool.maintenance, self.tool.operation)
        self.tool.validate_command(["create", "-f", "-", "-o", "json"], host.canonical(valid))
        for change in ("wrong_name", "secret_volume", "image_pull"):
            obj = copy.deepcopy(valid)
            if change == "wrong_name":
                obj["metadata"]["name"] = "another-pod"
            elif change == "secret_volume":
                obj["spec"]["volumes"].append({"name": "private", "secret": {"secretName": "private"}})
            else:
                obj["spec"]["containers"][0]["imagePullPolicy"] = "Always"
            with self.subTest(change=change), self.assertRaises(host.Refused):
                self.tool.validate_command(["create", "-f", "-", "-o", "json"], host.canonical(obj))

    def test_uid_precondition_is_required_and_no_arbitrary_delete(self):
        self.tool.pod_uid = "owned-uid"
        args = ["delete", "--raw", "/api/v1/namespaces/" + host.NS + "/pods/" + self.tool.maintenance, "-f", "-"]
        good = {"apiVersion": "v1", "kind": "DeleteOptions", "preconditions": {"uid": "owned-uid"}, "gracePeriodSeconds": 10}
        self.tool.validate_command(args, host.canonical(good))
        for bad in ({}, dict(good, preconditions={"uid": "other-uid"})):
            with self.assertRaises(host.Refused):
                self.tool.validate_command(args, host.canonical(bad))
        with self.assertRaises(host.Refused):
            self.tool.validate_command(["delete", "pod", "gateway"], None)

    def test_cleanup_only_own_uid_and_absence_confirmed(self):
        live = maintenance(self.tool)
        self.tool.pod_uid = live["metadata"]["uid"]
        with patch.object(self.tool, "get", return_value=live), patch.object(self.tool, "command", side_effect=[b"{}", b""]) as command:
            self.tool.cleanup()
        args, kwargs = command.call_args_list[0]
        self.assertEqual(args[0][0:2], ["delete", "--raw"])
        self.assertEqual(json.loads(args[1])["preconditions"], {"uid": self.tool.pod_uid})
        self.assertTrue(self.tool.report["maintenance_pod_absent"])
        self.assertTrue(self.tool.report["maintenance_delete_acknowledged"])

    def test_cleanup_refuses_reused_pod_name_with_new_uid(self):
        self.tool.pod_uid = "original-uid"
        with patch.object(self.tool, "get", return_value=maintenance(self.tool, "replacement-uid")), patch.object(self.tool, "command") as command:
            with self.assertRaises(host.Refused) as caught:
                self.tool.cleanup()
        self.assertEqual(caught.exception.code, "MAINTENANCE_UID_CHANGED")
        command.assert_not_called()

    def test_prepublish_baseline_change_stops_before_child(self):
        before = {"gateway": {"uid": "before"}}
        with patch.object(self.tool, "snapshots", return_value=({}, {"gateway": {"uid": "changed"}})), patch.object(self.tool, "infrastructure", return_value={}), patch.object(self.tool, "child") as child:
            with self.assertRaises(host.Refused) as caught:
                self.tool.publish(before, {}, "code", {"stage_manifest_sha256": "a" * 64}, {})
        self.assertEqual(caught.exception.code, "BASELINE_CHANGED_BEFORE_PUBLISH")
        self.assertFalse(self.tool.report["skill_publish_requested"])
        child.assert_not_called()

    def test_prepublish_change_after_consumer_inventory_also_stops(self):
        before = {"gateway": {"uid": "before"}}
        with patch.object(self.tool, "snapshots", side_effect=[({}, before), ({}, {"gateway": {"uid": "changed"}})]), patch.object(self.tool, "infrastructure", return_value={}), patch.object(self.tool, "consumers", return_value=[]), patch.object(self.tool, "child") as child:
            with self.assertRaises(host.Refused):
                self.tool.publish(before, {}, "code", {"stage_manifest_sha256": "a" * 64}, {})
        child.assert_not_called()

    def test_failed_publish_preserves_partial_information_without_retry(self):
        before = {"gateway": {"uid": "before"}}
        partial = {"status": "INCOMPLETE", "replacement_may_have_applied": True, "applied": ["scripts/entry.py"]}
        with patch.object(self.tool, "snapshots", return_value=({}, before)), patch.object(self.tool, "infrastructure", return_value={}), patch.object(self.tool, "consumers", return_value=[]), patch.object(self.tool, "child", return_value=partial) as child:
            with self.assertRaises(host.Refused) as caught:
                self.tool.publish(before, {}, "code", {"stage_manifest_sha256": "a" * 64}, {})
        self.assertEqual(caught.exception.code, "CHILD_PUBLISH_INCOMPLETE")
        self.assertEqual(child.call_count, 1)
        self.assertEqual(self.tool.report["publication"], partial)
        self.assertTrue(self.tool.report["skill_switch_may_have_applied"])
        self.assertFalse(self.tool.report["automatic_rollback"])

    def test_known_old_then_mixed_then_new_view_is_observed_without_republishing(self):
        before = {"gateway": {"uid": "before"}}
        dep = deployment()
        selected = [(pod(dep, rs(dep)), "gateway")]
        hashes = {name: "a" * 64 for name in host.FILES}
        old = dict(host.SEALED_FILES, **host.OLD_FILES)
        mixed = dict(old, **{"scripts/entry.py": hashes["scripts/entry.py"]})
        new = dict(host.SEALED_FILES, **hashes)
        observations = [{"status": "FIXED_SKILL_FILES_OBSERVED", "files": values} for values in (old, mixed, new)]
        with patch.object(self.tool, "snapshots", return_value=({}, before)), patch.object(self.tool, "infrastructure", return_value={}), patch.object(self.tool, "consumers", return_value=selected), patch.object(self.tool, "child", side_effect=[{"status": "TWO_FILE_UPDATE_ACCEPTED"}, {"status": "CHATFIX_LIVE_FILES_VERIFIED"}]) as child, patch.object(self.tool, "execute", side_effect=observations) as execute, patch.object(host.time, "sleep") as sleep:
            targets = self.tool.publish(before, {}, "code", {"stage_manifest_sha256": "b" * 64}, hashes)
            self.tool.verify_consumers(targets, hashes)
        self.assertEqual([call.args[1] for call in child.call_args_list], ["publish", "verify"])
        self.assertEqual(execute.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(self.tool.report["consumers"][0]["attempts"], 3)
        self.assertEqual(self.tool.report["consumers"][0]["observation"]["files"], new)

    def test_unknown_hash_rejected_without_wait_or_publish(self):
        dep = deployment()
        selected = [(pod(dep, rs(dep)), "gateway")]
        hashes = {name: "a" * 64 for name in host.FILES}
        for bad_file, code in (("SKILL.md", "CONSUMER_UNKNOWN_WRAPPER_HASH"), ("UPSTREAM.json", "CONSUMER_SEALED_FILES_CHANGED")):
            values = dict(host.SEALED_FILES, **hashes)
            values[bad_file] = "f" * 64
            with patch.object(self.tool, "execute", return_value={"status": "FIXED_SKILL_FILES_OBSERVED", "files": values}) as execute, patch.object(self.tool, "child") as child, patch.object(host.time, "sleep") as sleep:
                with self.assertRaises(host.Refused) as caught:
                    self.tool.verify_consumers(selected, hashes)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(execute.call_count, 1)
            self.assertEqual(self.tool.report["consumers"][0]["attempts"], 1)
            sleep.assert_not_called()
            child.assert_not_called()

    def test_known_old_cache_has_five_observation_limit(self):
        dep = deployment()
        values = dict(host.SEALED_FILES, **host.OLD_FILES)
        with patch.object(self.tool, "execute", return_value={"status": "FIXED_SKILL_FILES_OBSERVED", "files": values}) as execute, patch.object(host.time, "sleep") as sleep:
            with self.assertRaises(host.Refused) as caught:
                self.tool.verify_consumers([(pod(dep, rs(dep)), "gateway")], {name: "a" * 64 for name in host.FILES})
        self.assertEqual(caught.exception.code, "CONSUMER_FILES_NOT_PROPAGATED")
        self.assertEqual(execute.call_count, 5)
        self.assertEqual(sleep.call_count, 4)
        self.assertEqual(self.tool.report["consumers"][0]["attempts"], 5)

    def test_maintenance_cache_image_and_no_restart_are_required(self):
        live = maintenance(self.tool)
        self.tool.pod_uid = live["metadata"]["uid"]
        self.tool.validate_maintenance(live)
        for field, value in (("imageID", "sha256:other"), ("restartCount", 1)):
            changed = copy.deepcopy(live)
            changed["status"]["containerStatuses"][0][field] = value
            with self.assertRaises(host.Refused):
                self.tool.validate_maintenance(changed)

    def test_unexpected_maintenance_secret_mount_is_refused(self):
        live = maintenance(self.tool)
        live["spec"]["volumes"].append({"name": "private", "secret": {"secretName": "private"}})
        with self.assertRaises(host.Refused) as caught:
            self.tool.validate_maintenance(live)
        self.assertEqual(caught.exception.code, "MAINTENANCE_MOUNTS_CHANGED")

    def test_child_has_longer_host_deadline_than_internal_deadline(self):
        live = maintenance(self.tool)
        with patch.object(self.tool, "get", return_value=live), patch.object(self.tool, "execute", return_value={}) as execute:
            for action, expected in (("stage", 460), ("publish", 150), ("verify", 150)):
                self.tool.child("code", action, b"{}")
                self.assertEqual(execute.call_args.kwargs["timeout"], expected)


class InventoryTests(unittest.TestCase):
    def test_only_current_owned_ready_nonterminal_pods_selected(self):
        dep = deployment()
        replica = rs(dep)
        live = pod(dep, replica)
        cases = [live]
        for name, modification in (
            ("failed", lambda p: p["status"].update(phase="Failed")),
            ("completed", lambda p: p["status"].update(phase="Succeeded")),
            ("deleting", lambda p: p["metadata"].update(deletionTimestamp="2026-11-10T00:00:00Z")),
            ("unready", lambda p: p["status"]["containerStatuses"][0].update(ready=False)),
            ("wrong-owner", lambda p: p["metadata"]["ownerReferences"][0].update(uid="other")),
            ("wrong-rs-name", lambda p: p["metadata"]["ownerReferences"][0].update(name="other")),
            ("wrong-image", lambda p: p["spec"]["containers"][0].update(image="old-image")),
        ):
            item = pod(dep, replica, name)
            modification(item)
            cases.append(item)
        chosen = host.owned_ready_pods(cases, [replica], dep, "gateway")
        self.assertEqual([p["metadata"]["name"] for p in chosen], ["good-pod"])

    def test_replicaset_must_belong_to_target_deployment(self):
        dep = deployment()
        replica = rs(dep)
        live = pod(dep, replica)
        replica["metadata"]["ownerReferences"][0]["uid"] = "other-deployment"
        self.assertFalse(host.owned_ready_pods([live], [replica], dep, "gateway"))

    def test_convergence_not_historical_generation_is_required(self):
        dep = deployment(generation=999)
        self.assertTrue(host.converged(dep))
        for name in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas", "observedGeneration"):
            changed = copy.deepcopy(dep)
            changed["status"][name] = 0
            self.assertFalse(host.converged(changed))
        dep["status"]["terminatingReplicas"] = 1
        self.assertFalse(host.converged(dep))

    def test_mount_root_uses_real_pvc_and_subpath(self):
        dep = deployment()
        live = pod(dep, rs(dep))
        self.assertEqual(host.mounted_skill(live, "gateway"), "/mnt/skills/public/energy-market-daily")
        collector = deployment(host.COLLECTOR)
        live = pod(collector, rs(collector))
        self.assertEqual(host.mounted_skill(live, "collector"), "/opt/energy-skill")
        live["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] = "different-pvc"
        with self.assertRaises(host.Refused):
            host.mounted_skill(live, "collector")

    def test_same_pod_uid_does_not_hide_container_restart(self):
        dep = deployment()
        live = pod(dep, rs(dep))
        changed = copy.deepcopy(live)
        changed["status"]["containerStatuses"][0]["restartCount"] = 1
        self.assertNotEqual(host.runtime_identity(live, "gateway"), host.runtime_identity(changed, "gateway"))

    def test_consumer_observer_imports_no_application_and_exports_only_hashes(self):
        tree = ast.parse(host.OBSERVE)
        imports = {alias.name for item in ast.walk(tree) if isinstance(item, ast.Import) for alias in item.names}
        self.assertEqual(imports, {"hashlib", "json", "os", "pathlib", "stat", "sys"})
        self.assertNotIn("import requests", host.OBSERVE)
        self.assertNotIn("environ", host.OBSERVE)
        self.assertNotIn("credential", host.OBSERVE.replace("credentials_read", ""))


class SealedAndCommandTests(unittest.TestCase):
    def sealed(self, extra=False, bad_hash=False):
        raw_files = {"SKILL.md": b"skill", "scripts/entry.py": b"pass"}
        if extra:
            raw_files["upstream/app.py"] = b"forbidden"
        value = {"files": {name: base64.b64encode(raw).decode() for name, raw in raw_files.items()},
                 "sha256": {name: host.sha(raw) for name, raw in raw_files.items()}}
        if bad_hash:
            value["sha256"]["SKILL.md"] = "0" * 64
        payload = host.canonical(value)
        code = b"pass"
        return patch.multiple(host, CHILD_B64=base64.b64encode(code).decode(), CHILD_SHA256=host.sha(code),
                              PAYLOAD_B64=base64.b64encode(payload).decode(), PAYLOAD_SHA256=host.sha(payload))

    def test_sealed_payload_is_exactly_two_files_with_matching_hashes(self):
        with self.sealed():
            code, payload, hashes = host.load_sealed()
        self.assertEqual(code, "pass")
        self.assertEqual(set(hashes), set(host.FILES))

    def test_sealed_payload_cannot_include_upstream_or_bad_hash(self):
        for kwargs in ({"extra": True}, {"bad_hash": True}):
            with self.subTest(kwargs=kwargs), self.sealed(**kwargs), self.assertRaises(host.Refused):
                host.load_sealed()

    def fake_process(self, stdout_data=b"{}", stderr_data=b"", returncode=0):
        class Fake:
            pid = 99999
            def __init__(self, command, **kwargs):
                self.returncode = returncode
                kwargs["stdout"].write(stdout_data)
                kwargs["stdout"].flush()
                kwargs["stderr"].write(stderr_data)
                kwargs["stderr"].flush()
            def communicate(self, **kwargs):
                return None, None
            def poll(self):
                return self.returncode
        return Fake

    def test_output_bound_stops_without_exporting_response(self):
        tool = host.Update()
        with patch.object(host.subprocess, "Popen", self.fake_process(stdout_data=PRIVATE.encode() * 1000)):
            with self.assertRaises(host.Refused) as caught:
                tool.command(["get", "pod", "fixed", "-o", "json"], bound=100)
        self.assertEqual(caught.exception.code, "COMMAND_OUTPUT_BOUND")
        self.assertNotIn(PRIVATE, str(caught.exception))

    def test_stderr_bound_and_command_failure_are_redacted(self):
        tool = host.Update()
        for raw, status, expected in ((PRIVATE.encode(), 1, "KUBECTL_COMMAND_FAILED"), (b"x" * 262145, 0, "COMMAND_OUTPUT_BOUND")):
            with patch.object(host.subprocess, "Popen", self.fake_process(stderr_data=raw, returncode=status)):
                with self.assertRaises(host.Refused) as caught:
                    tool.command(["get", "pod", "fixed", "-o", "json"])
            self.assertEqual(caught.exception.code, expected)
            self.assertNotIn(PRIVATE, str(caught.exception))

    def test_child_exit_one_can_return_structured_failure_for_partial_publication(self):
        tool = host.Update()
        with patch.object(host.subprocess, "Popen", self.fake_process(stdout_data=b'{"status":"INCOMPLETE"}', returncode=1)):
            raw = tool.command(["get", "pod", "fixed", "-o", "json"], allow_child_failure=True)
        self.assertEqual(json.loads(raw)["status"], "INCOMPLETE")

    def test_command_budget_stops_before_execution(self):
        tool = host.Update()
        tool.commands = host.MAX_COMMANDS
        with patch.object(host.subprocess, "Popen") as process:
            with self.assertRaises(host.Refused) as caught:
                tool.command(["get", "pod", "fixed", "-o", "json"])
        self.assertEqual(caught.exception.code, "COMMAND_BUDGET")
        process.assert_not_called()


if __name__ == "__main__":
    unittest.main()

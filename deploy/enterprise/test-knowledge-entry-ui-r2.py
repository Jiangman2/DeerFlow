"""R2 runtime-only acceptance; retains r1's parser and local Nginx fixtures."""
import ast
import copy
import importlib.util
import json
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixture = load("entry_fixture", "test-knowledge-entry-ui.py")
m = load("entry_r2", "knowledge-entry-ui-20261014-r2.py")
SOURCE = Path(m.__file__)
fixture.m = m
fixture.SOURCE = SOURCE
BASE = fixture.BASE


def cluster():
    container = {"name": "nginx", "image": m.IMAGE, "volumeMounts": [{
        "name": "nginx-config", "mountPath": "/etc/nginx/nginx.conf.template",
        "subPath": "nginx.conf", "readOnly": True}]}
    spec = {"containers": [container], "volumes": [{"name": "nginx-config", "configMap": {"name": m.CM}}]}
    return {
        "deployment": {"metadata": {"uid": "deployment1", "generation": 2},
                       "spec": {"replicas": 1, "template": {"spec": spec}},
                       "status": {"observedGeneration": 2, "replicas": 1, "updatedReplicas": 1,
                                  "readyReplicas": 1, "availableReplicas": 1}},
        "pod": {"metadata": {"uid": "pod1", "ownerReferences": [
            {"controller": True, "kind": "ReplicaSet", "name": "rs1", "uid": "rsuid1"}]},
            "spec": copy.deepcopy(spec), "status": {"phase": "Running", "containerStatuses": [
                {"name": "nginx", "containerID": "containerd://1", "ready": True, "state": {"running": {}}}]}},
        "replicaset": {"metadata": {"uid": "rsuid1", "ownerReferences": [
            {"controller": True, "uid": "deployment1"}]}},
        "configmap": {"metadata": {"uid": "cm1"}, "immutable": True, "data": {"nginx.conf": BASE.decode()}},
    }


class ParserAndTransportTests(fixture.PatchTests):
    def test_inherited_proxy_headers_never_silently_dropped(self):
        for anchor in (b"http {", b"server {"):
            base = BASE.replace(anchor, anchor + b"\nproxy_set_header Host $host;", 1)
            with self.assertRaisesRegex(m.CheckFailure, "inherited_proxy_headers_need_review"):
                m.patch(base)
            local = base.replace(b"location / {", b"location / { proxy_set_header Host $host;", 1)
            self.assertIn(m.INJECTION.encode(), m.patch(local))

    # R1's ConfigMap CAS is deliberately replaced: R2 must never write one.
    def test_cas(self):
        self.assertFalse(hasattr(m, "patch_cm"))
        source = ast.parse(SOURCE.read_text(encoding="utf-8"))
        verbs = []
        for node in ast.walk(source):
            if isinstance(node, ast.BinOp) and isinstance(node.left, ast.Name) and node.left.id == "KUBE":
                self.assertIsInstance(node.right, ast.List)
                self.assertIsInstance(node.right.elts[0], ast.Constant)
                verbs.append(node.right.elts[0].value)
        self.assertCountEqual(verbs, ["get", "exec"])


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.objects = cluster()
        self.calls = []
        def command(argv):
            self.calls.append(argv)
            self.assertEqual(argv[len(m.KUBE)], "get")
            return json.dumps(self.objects[argv[len(m.KUBE) + 1]]).encode()
        self.patches = [patch.object(m, "command", side_effect=command), patch.object(m, "CM_SHA256", m.sha(BASE))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_immutable_accepted_without_writes(self):
        identity, cm = m.snapshot()
        self.assertTrue(identity["configmap_immutable"])
        self.assertEqual(identity["configmap_sha256"], m.sha(BASE))
        self.assertEqual(len(self.calls), 4)
        self.assertTrue(cm["immutable"])

    def test_hash_changed_rejected(self):
        self.objects["configmap"]["data"]["nginx.conf"] += "\n# changed"
        with self.assertRaisesRegex(m.CheckFailure, "baseline_hash_changed"):
            m.snapshot()

    def test_key_missing_rejected(self):
        self.objects["configmap"]["data"] = {}
        with self.assertRaisesRegex(m.CheckFailure, "key_shape_changed"):
            m.snapshot()

    def test_mutability_changed_rejected(self):
        self.objects["configmap"]["immutable"] = False
        with self.assertRaisesRegex(m.CheckFailure, "immutability_changed"):
            m.snapshot()

    def test_non_owned_pod_rejected(self):
        self.objects["replicaset"]["metadata"]["ownerReferences"][0]["uid"] = "other"
        with self.assertRaisesRegex(m.CheckFailure, "not_owned"):
            m.snapshot()

    def test_not_ready_rejected(self):
        self.objects["pod"]["status"]["containerStatuses"][0]["ready"] = False
        with self.assertRaisesRegex(m.CheckFailure, "not_ready"):
            m.snapshot()


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.live = BASE
        self.storage = {}
        self.records = []
        self.staged = None
        self.backup = Path("/var/lib/deerflow-kb-ui-runtime-r2-fixture")
        self.identity = {"pod_uid": "pod1", "container_id": "1"}
        self.cm = cluster()["configmap"]
        def save(path, raw):
            self.assertNotIn(str(path), self.storage)
            self.storage[str(path)] = raw
        def stage(raw):
            self.staged = raw
            return "/tmp/kb-maintenance-12345678"
        def apply(directory, expected):
            self.assertEqual(expected, self.live)
            self.live = self.staged
        def execute(script, args=(), data=None):
            self.assertNotIn("configmap", script)
            if script == "sha256sum /etc/nginx/nginx.conf.template":
                return (m.sha(BASE) + "  /etc/nginx/nginx.conf.template\n").encode()
            return b""
        self.patches = [
            patch.object(m.os, "geteuid", return_value=0, create=True),
            patch.object(m.socket, "gethostname", return_value="datax-01"),
            patch.object(m.os, "umask"), patch.object(m, "CM_SHA256", m.sha(BASE)),
            patch.object(m, "snapshot", side_effect=lambda: (copy.deepcopy(self.identity), copy.deepcopy(self.cm))),
            patch.object(m, "live_read", side_effect=lambda: self.live),
            patch.object(m, "execute", side_effect=execute),
            patch.object(m, "http_probe", return_value={"status": 200}),
            patch.object(m, "verify", return_value=[{"status": 200}]),
            patch.object(m, "safe_directory"),
            patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFDIR | 0o700)),
            patch.object(m.tempfile, "mkdtemp", return_value=str(self.backup)),
            patch.object(m, "save", side_effect=save),
            patch.object(m, "read", side_effect=lambda p: self.storage[str(p)]),
            patch.object(m, "stage_runtime", side_effect=stage),
            patch.object(m, "apply_runtime", side_effect=apply),
            patch.object(m, "emit", side_effect=lambda **r: self.records.append(r)),
            patch.object(m, "command", side_effect=AssertionError("unexpected Kubernetes call")),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def run_mode(self, mode):
        args = ["entry.py", mode] + ([] if mode == "--enable" else [str(self.backup)])
        with patch.object(sys, "argv", args):
            return m.main()

    def test_enable_status_restore(self):
        self.assertEqual(self.run_mode("--enable"), 0)
        self.assertEqual(self.live, m.patch(BASE))
        self.assertFalse(self.records[-1]["configmap_modified"])
        self.assertFalse(self.records[-1]["restart_persistence"])
        self.assertEqual(self.cm, cluster()["configmap"])
        state = json.loads(self.storage[str(self.backup / "STATE.json")])
        self.assertEqual(state["operation_version"], "runtime-only-r2")
        self.assertEqual(self.run_mode("--status"), 0)
        self.assertFalse(self.records[-1]["configuration_modified"])
        self.assertEqual(m.apply_runtime.call_count, 1)
        self.assertEqual(self.run_mode("--restore"), 0)
        self.assertEqual(self.live, BASE)
        self.assertEqual(self.cm, cluster()["configmap"])

    def test_runtime_drift_refuses_restore(self):
        self.assertEqual(self.run_mode("--enable"), 0)
        self.live += b"\n# operator change"
        drifted = self.live
        self.assertEqual(self.run_mode("--status"), 0)
        self.assertEqual(self.records[-2]["runtime_config"], "DRIFTED")
        self.assertEqual(self.run_mode("--restore"), 1)
        self.assertEqual(self.records[-1]["detail"], "configuration_drift_do_not_overwrite")
        self.assertEqual(self.live, drifted)
        self.assertEqual(m.apply_runtime.call_count, 1)

    def test_container_restart_refuses_stale_restore(self):
        self.assertEqual(self.run_mode("--enable"), 0)
        self.identity["container_id"] = "2"
        self.assertEqual(self.run_mode("--restore"), 1)
        self.assertIn("container_changed", self.records[-1]["detail"])
        self.assertEqual(m.apply_runtime.call_count, 1)

    def test_backup_tampering_rejected(self):
        self.assertEqual(self.run_mode("--enable"), 0)
        self.storage[str(self.backup / "runtime.before.conf")] += b"\n"
        self.assertEqual(self.run_mode("--restore"), 1)
        self.assertEqual(self.records[-1]["detail"], "backup_digest_mismatch")
        self.assertEqual(m.apply_runtime.call_count, 1)

    def test_unsafe_backup_refuses_restore(self):
        self.assertEqual(self.run_mode("--enable"), 0)
        with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFDIR | 0o755)):
            self.assertEqual(self.run_mode("--restore"), 1)
        self.assertEqual(self.records[-1]["detail"], "backup_directory_not_private")

    def test_syntax_failure_never_applies(self):
        with patch.object(m, "stage_runtime", side_effect=m.CheckFailure("syntax_failed")):
            self.assertEqual(self.run_mode("--enable"), 1)
        self.assertEqual(self.live, BASE)
        m.apply_runtime.assert_not_called()
        self.assertIsNotNone(self.records[-1]["private_backup"])
        self.assertFalse(self.records[-1]["runtime_change_attempted"])

    def test_repeated_enable_stops_before_mutation(self):
        self.assertEqual(self.run_mode("--enable"), 0)
        self.assertEqual(self.run_mode("--enable"), 1)
        self.assertIn("already_marked", self.records[-1]["detail"])
        self.assertEqual(m.apply_runtime.call_count, 1)

    def test_uncertain_reload_has_backup_no_retry(self):
        def interrupted(directory, expected):
            self.live = self.staged
            raise m.CheckFailure("reload_outcome_unknown")
        with patch.object(m, "apply_runtime", side_effect=interrupted) as apply:
            self.assertEqual(self.run_mode("--enable"), 1)
            self.assertEqual(apply.call_count, 1)
        self.assertEqual(self.live, m.patch(BASE))
        self.assertTrue(self.records[-1]["runtime_change_attempted"])
        self.assertFalse(self.records[-1]["automatic_retry"])
        self.assertEqual(self.run_mode("--status"), 0)
        self.assertEqual(self.run_mode("--restore"), 0)
        self.assertEqual(self.live, BASE)

    def test_drift_before_apply_no_write(self):
        def stage(raw):
            self.staged = raw
            self.live += b"\n# concurrent change"
            return "/tmp/kb-maintenance-12345678"
        with patch.object(m, "stage_runtime", side_effect=stage):
            self.assertEqual(self.run_mode("--enable"), 1)
        self.assertEqual(self.records[-1]["detail"], "baseline_changed_before_apply")
        m.apply_runtime.assert_not_called()


if __name__ == "__main__":
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(case)
                               for case in (ParserAndTransportTests, SnapshotTests, LifecycleTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        sys.exit(1)
    if "--nginx" in sys.argv:
        fixture.integration("--keep" in sys.argv)

"""Active-entry R3 regression; mocks never access production."""
import ast
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

# Reuse the parser, atomic runtime and failure-recovery cases without changing
# the released r1/r2 scripts or their tests.
import importlib.util
def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

prior = load("kb_r2_tests", "test-knowledge-entry-ui-r2.py")
m = load("kb_active_r3", "knowledge-entry-ui-20261014-r3.py")
prior.m = m
prior.SOURCE = Path(m.__file__)
prior.fixture.m = m
prior.fixture.SOURCE = Path(m.__file__)
BASE = prior.BASE


def cluster():
    values = prior.cluster()
    values["pod"]["metadata"]["labels"] = {"app": m.DEPLOYMENT}
    values["pod"]["status"]["podIP"] = "192.0.2.10"
    values["pod"]["status"]["containerStatuses"][0]["imageID"] = m.IMAGE
    values["replicaset"]["metadata"]["ownerReferences"][0]["kind"] = "Deployment"
    for name in m.SERVICES:
        values["service_" + name] = {
            "metadata": {"uid": "uid-" + name},
            "spec": {"selector": {"app": m.DEPLOYMENT}, "ports": [{"port": 2026, "targetPort": 2026}]},
        }
        values["endpointslices_" + name] = {"items": [{
            "metadata": {"labels": {"kubernetes.io/service-name": name}},
            "ports": [{"port": 2026}],
            "endpoints": [{"conditions": {"ready": True}, "addresses": ["192.0.2.10"],
                           "targetRef": {"kind": "Pod", "name": m.POD, "uid": "pod1"}}],
        }]}
    return values


def pinned(values):
    return {
        "deployment_uid": "deployment1",
        "deployment_spec_sha256": m.digest(values["deployment"]["spec"]),
        "pod_uid": "pod1", "container_id": "containerd://1",
        "configmap_uid": "cm1", "configmap_sha256": m.sha(BASE), "configmap_immutable": True,
        "services": {name: {"uid": "uid-" + name, "spec_sha256": m.digest(values["service_" + name]["spec"]),
                            "selector": {"app": m.DEPLOYMENT}, "ready_pod": m.POD,
                            "ready_pod_uid": "pod1"} for name in m.SERVICES},
    }


class ParserTests(prior.ParserAndTransportTests):
    def test_cas(self):
        self.assertFalse(hasattr(m, "patch_cm"))
        tree = ast.parse(Path(m.__file__).read_text(encoding="utf-8"))
        verbs = []
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.left, ast.Name) and node.left.id == "KUBE":
                verbs.append(node.right.elts[0].value)
        self.assertEqual(set(verbs), {"get", "exec"})
        self.assertEqual(len(verbs), 3)

    def test_payload_unchanged_from_r2(self):
        old = load("kb_original_r2", "knowledge-entry-ui-20261014-r2.py")
        self.assertEqual(m.patch(BASE), old.patch(BASE))
        self.assertEqual(m.JS, old.JS)
        self.assertEqual(m.CSS, old.CSS)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.values = cluster()
        def command(args, **kwargs):
            self.assertEqual(args[len(m.KUBE)], "get")
            kind = args[len(m.KUBE) + 1]
            name = args[len(m.KUBE) + 2]
            if kind == "deployment":
                self.assertEqual(name, m.DEPLOYMENT)
            if kind == "pod":
                self.assertEqual(name, m.POD)
            if kind == "service":
                kind += "_" + name
            if kind == "endpointslices":
                kind += "_" + args[args.index("-l") + 1].split("=", 1)[1]
            return json.dumps(self.values[kind]).encode()
        for p in (patch.object(m, "command", side_effect=command),
                  patch.object(m, "CM_SHA256", m.sha(BASE)),
                  patch.object(m, "PINNED_IDENTITY", pinned(self.values))):
            p.start()
            self.addCleanup(p.stop)

    def test_actual_backends_pinned(self):
        identity, cm = m.snapshot()
        self.assertEqual(identity, m.PINNED_IDENTITY)
        self.assertTrue(cm["immutable"])

    def test_old_deployment_selected_rejected(self):
        self.values["service_nginx"]["spec"]["selector"] = {"app": "nginx"}
        with self.assertRaisesRegex(m.CheckFailure, "selector_changed"):
            m.snapshot()

    def test_service_points_to_old_pod_rejected(self):
        self.values["endpointslices_nginx"]["items"][0]["endpoints"][0]["targetRef"]["name"] = "nginx-old"
        with self.assertRaisesRegex(m.CheckFailure, "other_ready_backend"):
            m.snapshot()

    def test_second_ready_backend_rejected(self):
        entry = self.values["endpointslices_nginx-nodeport"]["items"][0]
        entry["endpoints"].append(copy.deepcopy(entry["endpoints"][0]))
        with self.assertRaisesRegex(m.CheckFailure, "one_ready_endpoint"):
            m.snapshot()

    def test_endpoint_uid_changed_rejected(self):
        self.values["endpointslices_nginx"]["items"][0]["endpoints"][0]["targetRef"]["uid"] = "other"
        with self.assertRaisesRegex(m.CheckFailure, "other_ready_backend"):
            m.snapshot()

    def test_service_uid_and_spec_and_deployment_pin(self):
        for resource, key, field in (("service_nginx", "metadata", "uid"),
                                     ("service_nginx-nodeport", "spec", "clusterIP"),
                                     ("deployment", "spec", "revisionHistoryLimit")):
            before = copy.deepcopy(self.values)
            self.values[resource][key][field] = "different"
            with self.assertRaisesRegex(m.CheckFailure, "pinned_identity_changed"):
                m.snapshot()
            self.values = before

    def test_master_image_container_drift(self):
        status = self.values["pod"]["status"]["containerStatuses"][0]
        status["containerID"] = "containerd://restarted"
        with self.assertRaisesRegex(m.CheckFailure, "pinned_identity_changed"):
            m.snapshot()
        status["containerID"] = "containerd://1"
        status["imageID"] = "sha256:other"
        with self.assertRaisesRegex(m.CheckFailure, "image_id_changed"):
            m.snapshot()

    def test_configmap_is_not_silently_rebased(self):
        self.values["configmap"]["data"]["nginx.conf"] += "\n"
        with self.assertRaisesRegex(m.CheckFailure, "baseline_hash_changed"):
            m.snapshot()

    def test_not_ready_is_rejected(self):
        self.values["pod"]["status"]["containerStatuses"][0]["ready"] = False
        with self.assertRaisesRegex(m.CheckFailure, "not_ready"):
            m.snapshot()

    def test_master_path_check(self):
        for text in ("", "nginx: master process nginx -c /etc/nginx/nginx.conf\n",
                     "nginx: master process nginx -c /tmp/nginx.conf\n" * 2):
            with patch.object(m, "execute", return_value=text.encode()), self.assertRaises(m.CheckFailure):
                m.guard_master_runtime()
        with patch.object(m, "execute", return_value=b"nginx: master process nginx -c /tmp/nginx.conf\n"):
            m.guard_master_runtime()


class LifecycleTests(prior.LifecycleTests):
    def setUp(self):
        super().setUp()
        self.backup = Path("/var/lib/deerflow-kb-ui-active-r3-fixture")
        m.tempfile.mkdtemp.return_value = str(self.backup)
        for p in (
            patch.object(m, "BEFORE_SHA", m.sha(BASE)),
            patch.object(m, "AFTER_SHA", m.sha(m.patch(BASE))),
            patch.object(m, "guard_master_runtime"),
            patch.object(m, "route_checks", return_value=[]),
            patch.object(m, "require_routes"),
        ):
            p.start()
            self.addCleanup(p.stop)

    def test_enable_status_restore(self):
        self.assertEqual(self.run_mode("--enable"), 0)
        self.assertEqual(self.live, m.patch(BASE))
        state = json.loads(self.storage[str(self.backup / "STATE.json")])
        self.assertEqual(state["operation_version"], "active-runtime-r3")
        self.assertFalse(self.records[-1]["service_selectors_changed"])
        self.assertEqual(self.records[-1]["authenticated_html"], "NOT_TESTED")
        self.assertEqual(self.run_mode("--status"), 0)
        self.assertFalse(self.records[-1]["configuration_modified"])
        self.assertEqual(self.run_mode("--restore"), 0)
        self.assertEqual(self.live, BASE)

    def test_runtime_drift_refuses_restore(self):
        self.assertEqual(self.run_mode("--enable"), 0)
        self.live += b"\n# operator change"
        drifted = self.live
        self.assertEqual(self.run_mode("--status"), 0)
        self.assertTrue(any(r.get("runtime_config") == "DRIFTED" for r in self.records))
        self.assertEqual(self.run_mode("--restore"), 1)
        self.assertEqual(self.records[-1]["detail"], "configuration_drift_do_not_overwrite")
        self.assertEqual(self.live, drifted)
        self.assertEqual(m.apply_runtime.call_count, 1)

    def test_wrong_runtime_pin_never_applies(self):
        with patch.object(m, "BEFORE_SHA", "unapproved"):
            self.assertEqual(self.run_mode("--enable"), 1)
        m.apply_runtime.assert_not_called()

    def test_services_change_after_staging_never_apply(self):
        def stage(raw):
            self.staged = raw
            self.identity["service_uid"] = "changed"
            return "/tmp/kb-maintenance-12345678"
        with patch.object(m, "stage_runtime", side_effect=stage):
            self.assertEqual(self.run_mode("--enable"), 1)
        m.apply_runtime.assert_not_called()

    def test_public_not_verified_must_not_claim_pass_or_reapply(self):
        with patch.object(m, "require_routes", side_effect=m.CheckFailure("public_not_verified")):
            self.assertEqual(self.run_mode("--enable"), 1)
        self.assertEqual(self.live, m.patch(BASE))
        self.assertTrue(self.records[-1]["runtime_change_attempted"])
        self.assertEqual(self.records[-1]["knowledge_entry_ui_config"], "UNCONFIRMED")
        self.assertEqual(m.apply_runtime.call_count, 1)
        self.assertEqual(self.run_mode("--restore"), 0)


class RouteTests(unittest.TestCase):
    def reply(self, body, marker=True, status=200, rc=0):
        header = "HTTP/1.1 %d Test\r\n" % status
        if marker:
            header += "X-Knowledge-Entry-UI: " + m.MARKER + "\r\n"
        return header.encode() + b"\r\n" + body + ("\nKB_ACTIVE_CURL_RC=%d\n" % rc).encode()

    def test_exact_body_required_and_no_tls_bypass(self):
        def execute(script, args):
            self.assertEqual(args[:2], ["curl", "--disable"])
            self.assertEqual(args[-1], "https://" + m.HOST + m.PATHS[0])
            for bad in ("-k", "--insecure", "-L", "--location", "--cookie", "--user"):
                self.assertNotIn(bad, args)
            self.assertIn("Accept-Encoding: identity", args)
            return self.reply(m.JS.encode())
        with patch.object(m, "execute", side_effect=execute):
            row = m.route_probe("PUBLIC_HTTPS", m.PATHS[0])
        self.assertTrue(row["asset_body_matches"])
        m.require_routes([row] * 6, True)
        with patch.object(m, "execute", return_value=self.reply(b"other")):
            row = m.route_probe("PUBLIC_HTTPS", m.PATHS[0])
        self.assertFalse(row["asset_body_matches"])
        with self.assertRaises(m.CheckFailure):
            m.require_routes([row] * 6, True)

    def test_tls_failure_redacted(self):
        with patch.object(m, "execute", return_value=b"\nKB_ACTIVE_CURL_RC=60\n"):
            row = m.route_probe("PUBLIC_HTTPS", m.PATHS[0])
        self.assertEqual(row["failure_class"], "TLS_VERIFICATION")
        self.assertIsNone(row["status"])
        with self.assertRaises(m.CheckFailure):
            m.require_routes([row] * 6, True)

    def test_marker_in_body_is_not_trusted(self):
        with patch.object(m, "execute", return_value=self.reply(
                ("X-Knowledge-Entry-UI: " + m.MARKER).encode(), marker=False)):
            row = m.route_probe("PUBLIC_HTTPS", m.PATHS[0])
        self.assertFalse(row["ui_asset_marker"])

    def test_404_not_success_for_enable_but_valid_restore(self):
        with patch.object(m, "execute", return_value=self.reply(b"", marker=False, status=404)):
            row = m.route_probe("PUBLIC_HTTPS", m.PATHS[0])
        with self.assertRaises(m.CheckFailure):
            m.require_routes([row] * 6, True)
        m.require_routes([row] * 6, False)

    def test_probe_network_or_exec_failure_safe(self):
        with patch.object(m, "execute", side_effect=RuntimeError("sensitive raw string")), \
                patch.object(m, "emit") as emit:
            receipts = m.route_checks()
        self.assertEqual(len(receipts), 6)
        self.assertNotIn("sensitive", json.dumps(receipts))
        self.assertNotIn("sensitive", str(emit.call_args_list))


def integration(keep=False):
    # Calls only the isolated Nginx fixture. No DNS or production network calls:
    # all three route labels map to fixture loopback for HTTP body/parser tests.
    original_verify = m.verify
    def verify(enabled):
        result = original_verify(enabled)
        receipts = m.route_checks()
        m.require_routes(receipts, enabled)
        m.guard_master_runtime()
        return result
    with patch.object(m, "ROUTE_ORIGINS", {
        "LOCAL_SERVICE_1": "http://127.0.0.1:2026",
        "LOCAL_SERVICE_2": "http://127.0.0.1:2026",
        "LOCAL_PUBLIC_ROUTE_SIMULATION_HTTP": "http://127.0.0.1:2026",
    }), patch.object(m, "verify", side_effect=verify):
        prior.fixture.integration(keep)


if __name__ == "__main__":
    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(case)
                               for case in (ParserTests, SnapshotTests, LifecycleTests, RouteTests))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        sys.exit(1)
    if "--nginx" in sys.argv:
        integration("--keep" in sys.argv)


"""Offline qualification: no Kubernetes, model, website, or database access."""
import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("refresh_baseline_v2", ROOT / "refresh_baseline_v2.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def fixtures(gateways=70):
    deployments, replicas, pods = {}, {}, {}
    for component in m.COMPONENTS:
        count = gateways if component == "gateway" else 2
        uid = "deployment-" + component
        deployments[component] = {
            "metadata": {"name": component, "uid": uid, "generation": 7},
            "spec": {
                "replicas": count,
                "selector": {"matchLabels": {"app": component}},
                "template": {"spec": {"containers": [{"name": component,
                    "image": "deerflow.local/" + component + ":baseline",
                    "env": [{"name": "API_KEY", "value": "DO_NOT_EXPORT_SECRET"}],
                    "command": ["node"], "args": ["server.js", "--token=DO_NOT_EXPORT_SECRET"],
                    "workingDir": "/app/frontend", "ports": [{"containerPort": 3000}],
                    "readinessProbe": {"httpGet": {"path": "/?token=DO_NOT_EXPORT_SECRET", "port": 3000,
                        "httpHeaders": [{"name": "Authorization", "value": "DO_NOT_EXPORT_SECRET"}]}},
                    "volumeMounts": [{"name": "secret-volume", "mountPath": "/etc/credentials", "readOnly": True}]}],
                    "volumes": [{"name": "secret-volume", "secret": {"secretName": "DO_NOT_EXPORT_SECRET"}}]}},
            },
            "status": {"replicas": count, "updatedReplicas": count, "readyReplicas": count,
                "availableReplicas": count, "observedGeneration": 7},
        }
        replicas[component] = [{"metadata": {"uid": "rs-" + component,
            "ownerReferences": [{"kind": "Deployment", "uid": uid, "controller": True}]}}]
        pods[component] = []
        for i in range(count):
            name = component + "-" + str(i)
            pods[component].append({"metadata": {"name": name, "uid": "uid-" + name,
                "ownerReferences": [{"kind": "ReplicaSet", "uid": "rs-" + component, "controller": True}]},
                "spec": {"nodeName": "fixture-node", "containers": [{"name": component,
                    "image": "deerflow.local/" + component + ":baseline"}]},
                "status": {"phase": "Running", "conditions": [{"type": "Ready", "status": "True"}],
                    "containerStatuses": [{"name": component, "ready": True, "restartCount": 0,
                        "imageID": "containerd://sha256:" + "a" * 64,
                        "containerID": "containerd://" + name, "state": {"running": {}}}]}})
    return deployments, replicas, pods


class FakeKube:
    def __init__(self, gateways=70):
        self.deployments, self.replicas, self.pods = fixtures(gateways)
        self.calls, self.steps, self.sampled = [], [], []
        self.active = self.peak = 0
        self.lock = threading.Lock()
        self.read_counts = {}
        self.fail_pod = None
        self.drift = None

    def call(self, args, stdin=None):
        m.validate_command(args, stdin)
        with self.lock:
            self.calls.append(list(args))
        if args[0] == "get":
            resource = args[1]
            if resource == "deployment":
                name = args[2]
                self.read_counts[name] = self.read_counts.get(name, 0) + 1
                result = copy.deepcopy(self.deployments[name])
                if self.drift == "deployment" and name == "gateway" and self.read_counts[name] > 1:
                    result["spec"]["replicas"] += 1
                return result
            selector = args[args.index("--selector") + 1]
            component = next(n for n in m.COMPONENTS if "app=" + n in selector)
            result = copy.deepcopy(self.replicas[component] if resource == "replicasets" else self.pods[component])
            if resource == "pods" and self.read_counts.get(component, 0) > 1 and component == "gateway":
                if self.drift == "container":
                    result[0]["status"]["containerStatuses"][0]["containerID"] += "-new"
                elif self.drift == "pod_uid":
                    result[0]["metadata"]["uid"] += "-new"
                elif self.drift == "image":
                    result[0]["status"]["containerStatuses"][0]["imageID"] = "containerd://sha256:" + "b" * 64
                elif self.drift == "ready":
                    result[0]["status"]["containerStatuses"][0]["ready"] = False
                elif self.drift == "pod_spec":
                    result[0]["spec"]["env"] = {"changed": True}
                elif self.drift == "extra_pod":
                    extra = copy.deepcopy(result[0])
                    extra["metadata"]["uid"] += "-extra"
                    extra["metadata"]["name"] += "-extra"
                    result.append(extra)
            return {"items": result}
        component = args[args.index("-c") + 1]
        name = args[2]
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
            self.sampled.append(name)
        try:
            time.sleep(0.002)
            if name == self.fail_pod:
                raise RuntimeError("DO_NOT_EXPORT_SECRET raw provider diagnostic")
            return {"sources": {path: {"status": "PRESENT", "sha256": "b" * 64, "bytes": 5}
                    for path in m.SOURCE_FILES[component]},
                "dependencies": {key: "1.2.3" for key in m.DEPENDENCIES[component]},
                "runtime": "v22.0.0" if component == "frontend" else "3.11.9", "architecture": "aarch64"}
        finally:
            with self.lock:
                self.active -= 1


class InventoryTests(unittest.TestCase):
    def test_all_seventy_gateways_are_sampled_with_at_most_four_in_flight(self):
        fake = FakeKube()
        report = m.collect(fake)
        self.assertEqual(report["collection"], "COMPLETE_REVIEW_REQUIRED")
        self.assertEqual(len(report["components"]["gateway"]["samples"]), 70)
        self.assertEqual(sum(x.startswith("gateway-") for x in fake.sampled), 70)
        self.assertLessEqual(fake.peak, 4)
        self.assertGreaterEqual(fake.peak, 2)
        self.assertEqual(report["database_migration_metadata"], "NOT_COLLECTED")
        self.assertFalse(report["ready_for_rollout"])

    def test_selector_expressions_and_controller_uids_filter_foreign_pods(self):
        fake = FakeKube(1)
        fake.deployments["gateway"]["spec"]["selector"]["matchExpressions"] = [
            {"key": "zone", "operator": "In", "values": ["a", "b"]},
            {"key": "legacy", "operator": "DoesNotExist"}]
        fake.replicas["gateway"].append({"metadata": {"uid": "foreign-rs", "ownerReferences": [
            {"kind": "Deployment", "uid": "foreign", "controller": True}]}})
        foreign = copy.deepcopy(fake.pods["gateway"][0])
        foreign["metadata"].update(name="foreign-pod", uid="foreign-uid",
            ownerReferences=[{"kind": "ReplicaSet", "uid": "foreign-rs", "controller": True}])
        fake.pods["gateway"].append(foreign)
        report = m.collect(fake)
        self.assertNotIn("foreign-pod", fake.sampled)
        self.assertEqual(report["collection"], "COMPLETE_REVIEW_REQUIRED")
        selected = [c[c.index("--selector") + 1] for c in fake.calls if "--selector" in c]
        self.assertIn("app=gateway,zone in (a,b),!legacy", selected)

    def test_no_exec_on_unready_or_terminating_pod(self):
        fake = FakeKube(3)
        fake.pods["gateway"][0]["status"]["containerStatuses"][0]["ready"] = False
        fake.pods["gateway"][1]["metadata"]["deletionTimestamp"] = "2026-10-17T00:00:00Z"
        report = m.collect(fake)
        self.assertNotIn("gateway-0", fake.sampled)
        self.assertNotIn("gateway-1", fake.sampled)
        self.assertIn("gateway-2", fake.sampled)
        self.assertEqual(report["collection"], "INCOMPLETE_REVIEW_REQUIRED")

    def test_all_drift_types_fail_closed_and_keep_other_evidence(self):
        for drift in ("deployment", "container", "pod_uid", "image", "ready", "pod_spec", "extra_pod"):
            with self.subTest(drift=drift):
                fake = FakeKube(2)
                fake.drift = drift
                report = m.collect(fake)
                self.assertFalse(report["components"]["gateway"]["stable"])
                self.assertEqual(report["collection"], "INCOMPLETE_REVIEW_REQUIRED")
                self.assertEqual(len(report["components"]["frontend"]["samples"]), 2)

    def test_secrets_and_raw_errors_do_not_escape_and_failed_sample_retained(self):
        fake = FakeKube(2)
        fake.fail_pod = "gateway-0"
        report = m.collect(fake)
        output = json.dumps(report)
        self.assertNotIn("DO_NOT_EXPORT_SECRET", output)
        self.assertNotIn("raw provider diagnostic", output)
        self.assertIn("gateway-0", report["components"]["gateway"]["samples"])
        self.assertEqual(report["components"]["gateway"]["samples"]["gateway-0"]["status"], "FAILED")
        runtime = report["components"]["frontend"]["runtime_shape"]
        self.assertEqual(runtime["launcher"], "CUSTOM_REDACTED")
        self.assertEqual(runtime["mounts"][0]["reference_type"], "secret")
        self.assertEqual(runtime["probes"]["readinessProbe"]["http_headers_count"], 1)

    def test_source_missing_not_silently_qualified_as_standalone(self):
        fake = FakeKube(1)
        original = fake.call
        def missing(args, stdin=None):
            result = original(args, stdin)
            if args[0] == "exec" and args[args.index("-c") + 1] == "frontend":
                result["sources"] = {p: {"status": "MISSING"} for p in m.SOURCE_FILES["frontend"]}
            return result
        fake.call = missing
        report = m.collect(fake)
        for sample in report["components"]["frontend"]["samples"].values():
            self.assertEqual(sample["source_qualification"], "SOURCE_UNAVAILABLE_REVIEW_REQUIRED")
        self.assertFalse(report["ready_for_build"])

    def test_real_image_digest_retained_and_unknown_identity_fails_closed(self):
        self.assertEqual(m.image_digest("containerd://sha256:" + "a" * 64), "sha256:" + "a" * 64)
        self.assertEqual(m.image_digest("docker-pullable://repo/component@sha256:" + "b" * 64), "sha256:" + "b" * 64)
        fake = FakeKube(1)
        report = m.collect(fake)
        self.assertIn("sha256:" + "a" * 64, json.dumps(m.summary(report)))
        fake.pods["gateway"][0]["status"]["containerStatuses"][0]["imageID"] = "DO_NOT_EXPORT_SECRET"
        report = m.collect(fake)
        self.assertEqual(report["collection"], "INCOMPLETE_REVIEW_REQUIRED")
        self.assertNotIn("DO_NOT_EXPORT_SECRET", json.dumps(report))

    def test_untrusted_remote_receipt_never_reaches_output(self):
        fake = FakeKube(1)
        original = fake.call
        def extra(args, stdin=None):
            result = original(args, stdin)
            if args[0] == "exec":
                result["extra"] = "DO_NOT_EXPORT_SECRET"
            return result
        fake.call = extra
        report = m.collect(fake)
        self.assertNotIn("DO_NOT_EXPORT_SECRET", json.dumps(report))
        self.assertEqual(report["collection"], "INCOMPLETE_REVIEW_REQUIRED")

    def test_invalid_remote_field_values_are_rejected_without_echo(self):
        for field in ("runtime", "version", "sha256", "bytes", "status"):
            fake = FakeKube(1)
            original = fake.call
            def invalid(args, stdin=None):
                result = original(args, stdin)
                if args[0] == "exec":
                    if field == "runtime":
                        result["runtime"] = "DO_NOT_EXPORT_SECRET"
                    elif field == "version":
                        result["dependencies"][next(iter(result["dependencies"]))] = "DO_NOT_EXPORT_SECRET"
                    else:
                        result["sources"][next(iter(result["sources"]))][field] = "DO_NOT_EXPORT_SECRET"
                return result
            fake.call = invalid
            with self.subTest(field=field):
                report = m.collect(fake)
                self.assertNotIn("DO_NOT_EXPORT_SECRET", json.dumps(report))
                self.assertEqual(report["collection"], "INCOMPLETE_REVIEW_REQUIRED")


def production_terminal_fixture():
    fake = FakeKube(1)
    component = "provisioner"
    fake.deployments[component]["spec"]["replicas"] = 1
    for key in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas"):
        fake.deployments[component]["status"][key] = 1
    active, retired = fake.pods[component]
    active["metadata"].update(name="provisioner-75c958cc5f-vjpwx", uid="d8a25ad7-be5e-43d0-8ff2-69a5fd1e1d49")
    retired["metadata"].update(name="provisioner-75c958cc5f-st4p8", uid="0a14e221-f6e5-4b28-8e33-c3c7234ab5b3")
    retired["status"] = {"phase": "Failed", "reason": "Evicted",
        "message": "DO_NOT_EXPORT_SECRET eviction details",
        "conditions": [{"type": "Ready", "status": "False"}],
        "containerStatuses": [{"name": component, "ready": False, "restartCount": 0,
            "state": {"terminated": {"reason": "ContainerStatusUnknown", "exitCode": 137,
                "message": "DO_NOT_EXPORT_SECRET runtime details"}}}]}
    return fake, retired


class TerminalInventoryTests(unittest.TestCase):
    def test_production_evicted_record_excluded_only_from_active_counts(self):
        fake, retired = production_terminal_fixture()
        report = m.collect(fake)
        self.assertEqual(report["schema"], "portal-ppt-readonly-baseline.v2")
        self.assertEqual(report["collection"], "COMPLETE_REVIEW_REQUIRED")
        row = report["components"]["provisioner"]
        self.assertEqual((row["owned_count"], row["active_count"], row["terminal_count"], row["ready_count"]), (2, 1, 1, 1))
        self.assertEqual(len(row["samples"]), 1)
        self.assertNotIn(retired["metadata"]["name"], fake.sampled)
        terminal = next(p for p in row["pods_before"] if p["classification"] == "CONTROL_PLANE_TERMINAL")
        self.assertEqual(terminal["uid_sha256"], m.opaque(retired["metadata"]["uid"]))
        self.assertEqual((terminal["phase"], terminal["reason"]), ("Failed", "Evicted"))
        self.assertFalse(terminal["deleting"])
        self.assertEqual(terminal["retired_runtime_termination"], "NOT_VERIFIED")
        self.assertEqual(terminal["owners"][0]["uid_sha256"], m.opaque("rs-provisioner"))
        state = terminal["container_statuses"]["containerStatuses"][0]["state"]["terminated"]
        self.assertEqual((state["reason"], state["exit_code"]), ("ContainerStatusUnknown", 137))
        compact = m.summary(report)["components"]["provisioner"]
        self.assertEqual(compact["terminal_count"], 1)
        self.assertEqual(compact["image_identity_groups"]["groups"], {"sha256:" + "a" * 64: 1})
        self.assertNotIn("DO_NOT_EXPORT_SECRET", json.dumps(report))
        self.assertFalse(report["ready_for_build"])
        self.assertFalse(report["ready_for_rollout"])
        self.assertEqual(report["retired_runtime_termination"], "NOT_VERIFIED")

    def test_terminal_missing_statuses_is_api_terminal_only(self):
        for phase in ("Failed", "Succeeded"):
            with self.subTest(phase=phase):
                fake, retired = production_terminal_fixture()
                retired["status"] = {"phase": phase}
                report = m.collect(fake)
                self.assertEqual(report["collection"], "COMPLETE_REVIEW_REQUIRED")
                self.assertEqual(report["components"]["provisioner"]["terminal_count"], 1)
                self.assertEqual(report["retired_runtime_termination"], "NOT_VERIFIED")

    def test_nonterminal_extra_pods_remain_blockers(self):
        for phase in ("Pending", "Unknown", "Running", None):
            with self.subTest(phase=phase):
                fake, retired = production_terminal_fixture()
                retired["status"]["phase"] = phase
                report = m.collect(fake)
                self.assertEqual(report["collection"], "INCOMPLETE_REVIEW_REQUIRED")
                self.assertEqual(report["components"]["provisioner"]["active_count"], 2)
                self.assertEqual(report["components"]["provisioner"]["terminal_count"], 0)

    def test_terminal_running_or_ready_contradictions_in_all_container_kinds_block(self):
        for field in ("containerStatuses", "initContainerStatuses", "ephemeralContainerStatuses"):
            for contradiction in ("running", "ready"):
                with self.subTest(field=field, contradiction=contradiction):
                    fake, retired = production_terminal_fixture()
                    status = {"name": "extra", "ready": contradiction == "ready", "state": {}}
                    if contradiction == "running":
                        status["state"] = {"running": {}}
                    retired["status"].setdefault(field, []).append(status)
                    report = m.collect(fake)
                    row = report["components"]["provisioner"]
                    self.assertEqual(report["collection"], "INCOMPLETE_REVIEW_REQUIRED")
                    self.assertEqual((row["active_count"], row["terminal_count"]), (2, 0))
                    self.assertIn({"code": "terminal_pod_state_contradiction"}, row["errors"])

    def test_terminal_ready_conditions_block(self):
        for condition in ("Ready", "ContainersReady"):
            with self.subTest(condition=condition):
                fake, retired = production_terminal_fixture()
                retired["status"]["conditions"] = [{"type": condition, "status": "True"}]
                report = m.collect(fake)
                self.assertEqual(report["collection"], "INCOMPLETE_REVIEW_REQUIRED")
                self.assertEqual(report["components"]["provisioner"]["terminal_count"], 0)

    def test_terminal_sandbox_readiness_does_not_claim_container_liveness(self):
        fake, retired = production_terminal_fixture()
        retired["status"]["conditions"].append({"type": "PodReadyToStartContainers", "status": "True"})
        report = m.collect(fake)
        self.assertEqual(report["collection"], "COMPLETE_REVIEW_REQUIRED")
        row = report["components"]["provisioner"]
        self.assertEqual(row["terminal_count"], 1)
        terminal = next(p for p in row["pods_before"] if p["classification"] == "CONTROL_PLANE_TERMINAL")
        self.assertIn({"type": "PodReadyToStartContainers", "status": "True"}, terminal["readiness_conditions"])
        self.assertEqual(terminal["retired_runtime_termination"], "NOT_VERIFIED")

    def test_deleting_running_and_active_unknown_image_still_block(self):
        for defect in ("deleting", "unknown_image", "not_ready"):
            with self.subTest(defect=defect):
                fake, retired = production_terminal_fixture()
                active = fake.pods["provisioner"][0]
                if defect == "deleting":
                    active["metadata"]["deletionTimestamp"] = "2026-10-17T00:00:00Z"
                elif defect == "unknown_image":
                    active["status"]["containerStatuses"][0]["imageID"] = "unknown"
                else:
                    active["status"]["containerStatuses"][0]["ready"] = False
                report = m.collect(fake)
                self.assertEqual(report["collection"], "INCOMPLETE_REVIEW_REQUIRED")
                if defect != "unknown_image":
                    self.assertNotIn(active["metadata"]["name"], fake.sampled)

    def test_terminal_drift_is_not_hidden_by_active_count_filter(self):
        for drift in ("uid", "reason", "exit", "owner", "owner_detail", "removed", "deleting", "phase", "init", "ephemeral"):
            with self.subTest(drift=drift):
                fake, retired = production_terminal_fixture()
                original = fake.call
                def changed(args, stdin=None):
                    result = original(args, stdin)
                    if args[:2] == ["get", "pods"] and "app=provisioner" in args and fake.read_counts["provisioner"] > 1:
                        pod = result["items"][1]
                        if drift == "uid":
                            pod["metadata"]["uid"] += "-changed"
                        elif drift == "reason":
                            pod["status"]["reason"] = "NodeLost"
                        elif drift == "exit":
                            pod["status"]["containerStatuses"][0]["state"]["terminated"]["exitCode"] = 1
                        elif drift == "owner":
                            pod["metadata"]["ownerReferences"][0]["uid"] = "foreign"
                        elif drift == "owner_detail":
                            pod["metadata"]["ownerReferences"][0]["blockOwnerDeletion"] = True
                        elif drift == "removed":
                            result["items"].pop()
                        elif drift == "deleting":
                            pod["metadata"]["deletionTimestamp"] = "2026-10-17T00:00:00Z"
                        elif drift == "phase":
                            pod["status"]["phase"] = "Succeeded"
                        else:
                            key = "initContainerStatuses" if drift == "init" else "ephemeralContainerStatuses"
                            pod["status"][key] = [{"name": "diagnostic", "ready": False, "state": {"terminated": {"exitCode": 0}}}]
                    return result
                fake.call = changed
                report = m.collect(fake)
                self.assertFalse(report["components"]["provisioner"]["stable"])
                self.assertEqual(report["collection"], "INCOMPLETE_REVIEW_REQUIRED")

    def test_last_running_state_does_not_override_current_termination(self):
        fake, retired = production_terminal_fixture()
        retired["status"]["containerStatuses"][0]["lastState"] = {"running": {}}
        report = m.collect(fake)
        self.assertEqual(report["collection"], "COMPLETE_REVIEW_REQUIRED")
        self.assertEqual(report["components"]["provisioner"]["terminal_count"], 1)


class BoundaryTests(unittest.TestCase):
    def test_mutations_secret_reads_and_arbitrary_exec_are_rejected(self):
        for args in (["delete", "pods", "gateway-0"], ["get", "secrets", "-o", "json"],
                     ["get", "configmap", "x", "-o", "json"], ["get", "pods", "-o", "json"],
                     ["exec", "-i", "gateway-0", "-c", "gateway", "--", "sh", "-c", "true"],
                     ["get", "deployment", "foreign", "--show-managed-fields=false", "-o", "json"]):
            with self.subTest(args=args), self.assertRaises(m.CheckFailure):
                m.validate_command(args, None)
        args, stdin = m.source_command("gateway-0", "gateway")
        with self.assertRaises(m.CheckFailure):
            m.validate_command(args, b"print('arbitrary code')")

    def test_continuously_drained_output_is_bounded(self):
        with self.assertRaises(m.CheckFailure) as caught:
            m.run_bounded([sys.executable, "-c", "import sys; sys.stdout.write('x'*1000000); sys.stderr.write('y'*1000000)"],
                          None, timeout=5, limit=4096)
        self.assertEqual(caught.exception.code, "command_output_limit")
        self.assertLessEqual(caught.exception.evidence["retained_stdout_bytes"], 4096)

    def test_timeout_does_not_export_stderr(self):
        started = time.monotonic()
        with self.assertRaises(m.CheckFailure) as caught:
            m.run_bounded([sys.executable, "-u", "-c", "import sys,time; print('SECRET',file=sys.stderr); time.sleep(15)"],
                          None, timeout=0.2, limit=4096)
        self.assertEqual(caught.exception.code, "command_timeout")
        self.assertNotIn("SECRET", json.dumps(caught.exception.evidence))
        self.assertLess(time.monotonic() - started, 4)

    def test_python37_syntax_and_source_probes_do_not_import_business_code(self):
        ast.parse((ROOT / "refresh_baseline_v2.py").read_text(encoding="utf-8"), feature_version=(3, 7))
        for component in m.COMPONENTS:
            args, stdin = m.source_command(component + "-0", component)
            text = stdin.decode() if stdin else args[-1]
            for denied in ("import deerflow", "import app.", "requests", "urlopen", "psycopg", "subprocess", "fetch("):
                self.assertNotIn(denied, text)

    def test_actual_python_probe_schema_and_real_digest_without_network(self):
        # The controller embeds POSIX source reads. On Windows replace only the
        # POSIX alarm/uname and path/O_NOFOLLOW portability primitives, keeping
        # the actual JSON, digest, size and package-metadata logic executable.
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "fixture.py"
            fixture.write_bytes(b"print('synthetic fixture')\n")
            script = m.PYTHON_PROBE.replace("__FILES__", json.dumps([str(fixture), str(fixture) + ".absent"]))
            script = script.replace("__PACKAGES__", "[]")
            if os.name != "posix":
                script = script.replace("signal.signal(signal.SIGALRM, lambda *_: os._exit(124))", "")
                script = script.replace("signal.alarm(45)", "")
                script = script.replace("machine = os.uname().machine", "machine = 'x86_64'")
                script = script.replace("os.O_RDONLY | os.O_NOFOLLOW", "os.O_RDONLY")
            result = subprocess.run([sys.executable, "-I", "-B", "-"], input=script.encode(),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, check=True)
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt["sources"][str(fixture)]["sha256"], m.digest(fixture.read_bytes()))
            self.assertEqual(receipt["sources"][str(fixture) + ".absent"]["status"], "MISSING")
            self.assertEqual(receipt["dependencies"], {})
            self.assertIn(receipt["architecture"], ("x86_64", "amd64", "aarch64", "arm64", "OTHER"))

    @unittest.skipUnless(shutil.which("node"), "local Node executable unavailable")
    def test_actual_node_probe_fixed_paths_and_schema_without_network(self):
        args, unused = m.source_command("frontend-fixture", "frontend")
        env = dict(os.environ)
        env.pop("NODE_OPTIONS", None)
        env.pop("NODE_PATH", None)
        result = subprocess.run([shutil.which("node"), "-e", args[-1]], env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=10, check=True)
        receipt = m.clean_receipt(json.loads(result.stdout), "frontend")
        self.assertEqual(set(receipt["sources"]), set(m.SOURCE_FILES["frontend"]))
        self.assertTrue(all(v["status"] in m.SOURCE_STATUSES for v in receipt["sources"].values()))

    def test_image_reference_does_not_echo_userinfo(self):
        self.assertEqual(m.image_ref("user:SECRET@registry/path"), "REDACTED_INVALID_IMAGE_REFERENCE")
        self.assertEqual(m.image_ref("registry:5000/deerflow/frontend:r1"), "registry:5000/deerflow/frontend:r1")


if __name__ == "__main__":
    unittest.main()

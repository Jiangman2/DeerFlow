"""Offline operator-boundary checks; no Kubernetes/network/credential access."""

import ast
import base64
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "activate-energy-daily-result-links-20261111-r2.py"
spec = importlib.util.spec_from_file_location("gateway_links_activation", SOURCE)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
PRE_SOURCE = ROOT / "inspect-energy-daily-result-link-source-20261111-r2.py"
pre = types.ModuleType("source_inspector_test")
exec(compile(PRE_SOURCE.read_bytes(), "<source-inspector>", "exec"), pre.__dict__)


def deployment():
    return {
        "metadata": {
            "name": "gateway",
            "namespace": m.NS,
            "uid": m.DEPLOYMENT_UID,
            "generation": 413,
            "resourceVersion": "900",
            "annotations": {"retained": "value"},
        },
        "spec": {
            "replicas": 70,
            "selector": {"matchLabels": {"app": "gateway"}},
            "strategy": {
                "type": "RollingUpdate",
                "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 0},
            },
            "template": {
                "metadata": {
                    "labels": {"app": "gateway"},
                    "annotations": {"existing": "same"},
                },
                "spec": {
                    "terminationGracePeriodSeconds": 45,
                    "containers": [
                        {
                            "name": "gateway",
                            "image": m.IMAGE,
                            "command": ["existing-command"],
                            "args": ["unchanged"],
                            "env": [
                                {
                                    "name": "EXAMPLE",
                                    "valueFrom": {
                                        "secretKeyRef": {
                                            "name": "unchanged-secret",
                                            "key": "unchanged-key",
                                        }
                                    },
                                },
                                {
                                    "name": "OPENSSL_CONF",
                                    "value": m.OPENSSL_PATH + "/openssl.cnf",
                                },
                            ],
                            "resources": {"requests": {"cpu": "2", "memory": "4Gi"}},
                            "volumeMounts": [
                                {
                                    "name": "previous",
                                    "mountPath": "/app/backend/unrelated.py",
                                    "subPath": "code.py",
                                    "readOnly": True,
                                },
                                {
                                    "name": "openssl",
                                    "mountPath": m.OPENSSL_PATH,
                                    "readOnly": True,
                                },
                            ],
                        }
                    ],
                    "volumes": [
                        {"name": "previous", "configMap": {"name": "retained-code"}},
                        {"name": "openssl", "configMap": {"name": m.OPENSSL_CM}},
                    ],
                },
            },
        },
        "status": {
            "observedGeneration": 413,
            "replicas": 70,
            "updatedReplicas": 70,
            "readyReplicas": 70,
            "availableReplicas": 70,
            "terminatingReplicas": 0,
        },
    }


def fixture_assets():
    result = {
        "pre": pre,
        "source_inspector": PRE_SOURCE.read_bytes(),
        "qualifier": b"pass\n",
        "task_origin_guard": b"pass\n",
        "skill_reader": b"pass\n",
    }
    for relative, key in m.FILE_KEYS.items():
        result[key] = (ROOT / "backend" / relative).read_bytes()
    return result


def idle():
    return {
        "status": "GATEWAY_IDLE_ORIGIN_OBSERVED",
        "idle": True,
        "origin": m.EXPECTED_ORIGIN,
        "read_only": True,
        "provider_requests": 0,
        "work_messages_sent": 0,
        "transaction_readonly_verified": True,
        "active_runs": 0,
        "active_oa": 0,
        "active_callbacks": 0,
        "scheduled_running": 0,
        "active_work_notifications": 0,
    }


def openssl_cm():
    raw = (
        "openssl_conf = deerflow_init\n\n[deerflow_init]\nssl_conf = deerflow_ssl\n\n"
        "[deerflow_ssl]\nsystem_default = deerflow_system_default\n\n"
        "[deerflow_system_default]\nCipherString = DEFAULT:@SECLEVEL=1\n"
    )
    return {
        "metadata": {"name": m.OPENSSL_CM, "uid": "openssl-fixed"},
        "data": {"openssl.cnf": raw},
    }


def patch_fixture(updater):
    updater.sample = {
        "metadata": {
            "name": "gateway-one",
            "uid": "poduid",
            "ownerReferences": [
                {
                    "controller": True,
                    "kind": "ReplicaSet",
                    "name": "gateway-current",
                    "uid": "a" * 36,
                }
            ],
        }
    }
    updater.known_rs.add("gateway-current")
    updater.report["sample"] = {"runtime": "fixed"}
    updater.expected = {"candidate": True}
    updater.cm_uid = "cmuid"
    dep = deployment()
    updater.report["openssl_compatibility"] = m.openssl_identity(dep, openssl_cm())
    cm = m.configmap_manifest(updater.operation, updater.cm_name, updater.assets)
    cm["metadata"]["uid"] = "cmuid"
    updater.get = mock.Mock(side_effect=[dep, updater.sample, {}, openssl_cm(), cm])
    updater.source = mock.Mock()
    updater.verify_installed_skill = mock.Mock()


class SourceAndPatchTests(unittest.TestCase):
    def setUp(self):
        self.dep = deployment()
        self.patch = mock.patch.object(
            m, "BEFORE_SPEC_SHA", m.sha(m.canonical(self.dep["spec"]))
        )
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_exact_append_six_only_preserves_all_other_spec(self):
        original = copy.deepcopy(self.dep)
        candidate = m.candidate_spec(self.dep, "gateway-energy-links-abcd")
        self.assertEqual(self.dep, original)
        self.assertEqual(candidate["strategy"], original["spec"]["strategy"])
        pod = candidate["template"]["spec"]
        actual = pod["containers"][0]
        self.assertEqual(len(actual["volumeMounts"]), 2 + len(m.FILE_KEYS))
        self.assertEqual(
            actual["env"], original["spec"]["template"]["spec"]["containers"][0]["env"]
        )
        self.assertEqual(
            actual["resources"],
            original["spec"]["template"]["spec"]["containers"][0]["resources"],
        )
        del actual["volumeMounts"][-len(m.FILE_KEYS) :]
        pod["volumes"].pop()
        self.assertEqual(candidate, original["spec"])

    def test_omitted_mount_and_volume_fields_preserved(self):
        self.dep["spec"]["template"]["spec"].pop("volumes")
        self.dep["spec"]["template"]["spec"]["containers"][0].pop("volumeMounts")
        with mock.patch.object(
            m, "BEFORE_SPEC_SHA", m.sha(m.canonical(self.dep["spec"]))
        ):
            result = m.candidate_spec(self.dep, "candidate")
        self.assertEqual(len(result["template"]["spec"]["volumes"]), 1)

    def test_any_baseline_identity_change_refuses(self):
        for field, value in [("uid", "other"), ("generation", 414)]:
            bad = copy.deepcopy(self.dep)
            bad["metadata"][field] = value
            with (
                self.subTest(field=field),
                self.assertRaisesRegex(m.Refused, "BASELINE"),
            ):
                m.candidate_spec(bad, "candidate")
        bad = copy.deepcopy(self.dep)
        bad["spec"]["replicas"] = 69
        with self.assertRaisesRegex(m.Refused, "BASELINE"):
            m.candidate_spec(bad, "candidate")

    def test_existing_covering_or_nested_source_mount_refuses(self):
        for target in (
            "/app",
            "/app/backend",
            "/app/backend/app/channels/manager.py",
            "/app/backend/app/channels/manager.py/nested",
        ):
            bad = copy.deepcopy(self.dep)
            bad["spec"]["template"]["spec"]["containers"][0]["volumeMounts"][0][
                "mountPath"
            ] = target
            with mock.patch.object(
                m, "BEFORE_SPEC_SHA", m.sha(m.canonical(bad["spec"]))
            ):
                with (
                    self.subTest(target=target),
                    self.assertRaisesRegex(m.Refused, "SOURCE_ALREADY_MOUNTED"),
                ):
                    m.candidate_spec(bad, "candidate")

    def test_existing_overlay_volume_refuses(self):
        self.dep["spec"]["template"]["spec"]["volumes"][0]["name"] = m.VOLUME
        with mock.patch.object(
            m, "BEFORE_SPEC_SHA", m.sha(m.canonical(self.dep["spec"]))
        ):
            with self.assertRaisesRegex(m.Refused, "VOLUME_EXISTS"):
                m.candidate_spec(self.dep, "candidate")

    def test_cas_binds_uid_generation_resourceversion_and_entire_spec(self):
        expected = m.candidate_spec(self.dep, "candidate")
        patch = m.cas_patch(self.dep, expected)
        self.assertEqual(
            [(x["op"], x["path"]) for x in patch],
            [
                ("test", "/metadata/uid"),
                ("test", "/metadata/generation"),
                ("test", "/metadata/resourceVersion"),
                ("test", "/spec"),
                ("replace", "/spec"),
            ],
        )
        self.assertEqual(patch[-1]["value"], expected)
        self.dep["metadata"].pop("resourceVersion")
        with self.assertRaisesRegex(m.Refused, "RESOURCE_VERSION"):
            m.cas_patch(self.dep, expected)

    def test_postpatch_spec_or_generation_drift_refuses(self):
        expected = m.candidate_spec(self.dep, "candidate")
        after = copy.deepcopy(self.dep)
        after["spec"] = expected
        after["metadata"]["generation"] = 414
        m.candidate_guard(after, expected)
        for field, value in [("uid", "other"), ("generation", 415)]:
            bad = copy.deepcopy(after)
            bad["metadata"][field] = value
            with self.assertRaisesRegex(m.Refused, "SPEC_DRIFT"):
                m.candidate_guard(bad, expected)
        bad = copy.deepcopy(after)
        bad["spec"]["replicas"] = 69
        with self.assertRaisesRegex(m.Refused, "SPEC_DRIFT"):
            m.candidate_guard(bad, expected)

    def test_final_convergence_all_counts_and_generation_required(self):
        after = copy.deepcopy(self.dep)
        after["metadata"]["generation"] = 414
        after["spec"] = m.candidate_spec(self.dep, "candidate")
        after["status"]["observedGeneration"] = 414
        m.candidate_guard(after, after["spec"], converged=True)
        for key in (
            "replicas",
            "updatedReplicas",
            "readyReplicas",
            "availableReplicas",
            "observedGeneration",
            "terminatingReplicas",
        ):
            bad = copy.deepcopy(after)
            bad["status"][key] = 1
            with (
                self.subTest(key=key),
                self.assertRaisesRegex(m.Refused, "NOT_CONVERGED"),
            ):
                m.candidate_guard(bad, after["spec"], converged=True)


class IsolationAndIdleTests(unittest.TestCase):
    def test_maintenance_has_no_serviceaccount_secret_envfrom_or_business_mount(self):
        obj = m.maintenance_manifest("a" * 32, "candidate", "datax-01", "candidate-cm")
        s = obj["spec"]
        c = s["containers"][0]
        self.assertFalse(s["automountServiceAccountToken"])
        self.assertFalse(s["enableServiceLinks"])
        self.assertEqual(c["image"], m.IMAGE)
        self.assertEqual(c["imagePullPolicy"], "Never")
        self.assertNotIn("envFrom", c)
        self.assertEqual(
            {x["name"] for x in c["env"]},
            {"HOME", "PYTHONDONTWRITEBYTECODE", "OPENSSL_CONF"},
        )
        self.assertEqual(
            {tuple(sorted(set(v) - {"name"})) for v in s["volumes"]},
            {("emptyDir",), ("configMap",)},
        )
        self.assertTrue(c["securityContext"]["readOnlyRootFilesystem"])
        self.assertEqual(c["securityContext"]["capabilities"], {"drop": ["ALL"]})
        self.assertEqual(len(c["volumeMounts"]), 2 + len(m.FILE_KEYS))

    def test_maintenance_admission_change_uid_image_restart_refuses(self):
        requested = m.maintenance_manifest(
            "a" * 32, "candidate", "datax-01", "candidate-cm"
        )
        pod = copy.deepcopy(requested)
        pod["metadata"]["uid"] = "uid"
        pod["status"] = {
            "phase": "Running",
            "containerStatuses": [
                {"ready": True, "restartCount": 0, "imageID": "fixed"}
            ],
        }
        self.assertTrue(m.maintenance_guard(pod, requested, "uid", "fixed"))
        for change in ("uid", "env", "imageid", "restart", "volume"):
            bad = copy.deepcopy(pod)
            if change == "uid":
                bad["metadata"]["uid"] = "new"
            if change == "env":
                bad["spec"]["containers"][0]["env"].append(
                    {"name": "SECRET", "value": "not-real"}
                )
            if change == "imageid":
                bad["status"]["containerStatuses"][0]["imageID"] = "wrong"
            if change == "restart":
                bad["status"]["containerStatuses"][0]["restartCount"] = 1
            if change == "volume":
                bad["spec"]["volumes"].append(
                    {"name": "injected", "secret": {"secretName": "none"}}
                )
            with self.subTest(change=change), self.assertRaises(m.Refused):
                m.maintenance_guard(bad, requested, "uid", "fixed")

    def test_admission_injected_secret_lifecycle_init_or_host_access_refuses(self):
        requested = m.maintenance_manifest(
            "a" * 32, "candidate", "datax-01", "candidate-cm"
        )
        container_keys = (
            "envFrom",
            "lifecycle",
            "volumeDevices",
            "ports",
            "livenessProbe",
            "readinessProbe",
            "startupProbe",
        )
        for key in container_keys + (
            "initContainers",
            "ephemeralContainers",
            "hostNetwork",
            "hostPID",
            "hostIPC",
            "shareProcessNamespace",
        ):
            pod = copy.deepcopy(requested)
            pod["metadata"]["uid"] = "uid"
            target = (
                pod["spec"]["containers"][0] if key in container_keys else pod["spec"]
            )
            target[key] = True
            with self.subTest(key=key), self.assertRaises(m.Refused):
                m.maintenance_guard(pod, requested, "uid", "fixed")

    def test_openssl_only_accepted_from_fixed_current_projection_and_payload(self):
        self.assertEqual(
            m.openssl_identity(deployment(), openssl_cm())["sha256"], m.OPENSSL_SHA
        )
        for change in ("payload", "extra", "env", "mount", "volume"):
            dep = deployment()
            cm = openssl_cm()
            if change == "payload":
                cm["data"]["openssl.cnf"] += "\n"
            if change == "extra":
                cm["data"]["unknown"] = "value"
            if change == "env":
                dep["spec"]["template"]["spec"]["containers"][0]["env"][-1]["value"] = (
                    "/other.cnf"
                )
            if change == "mount":
                dep["spec"]["template"]["spec"]["containers"][0]["volumeMounts"][-1][
                    "readOnly"
                ] = False
            if change == "volume":
                dep["spec"]["template"]["spec"]["volumes"][-1]["configMap"]["name"] = (
                    "other"
                )
            with self.subTest(change=change), self.assertRaises(m.Refused):
                m.openssl_identity(dep, cm)

    def test_immutable_cm_contains_only_four_expected_files(self):
        obj = m.configmap_manifest("a" * 32, "candidate", fixture_assets())
        self.assertTrue(obj["immutable"])
        self.assertEqual(set(obj["data"]), set(m.FILE_KEYS.values()))
        self.assertEqual(obj["metadata"]["labels"], {m.LABEL: "a" * 32})
        self.assertNotIn("binaryData", obj)

    def test_idle_accepts_only_real_integer_zero_all_five_counters(self):
        self.assertEqual(m.accepted_idle(idle())["origin"], m.EXPECTED_ORIGIN)
        for key in (
            "active_runs",
            "active_oa",
            "active_callbacks",
            "scheduled_running",
            "active_work_notifications",
        ):
            for value in (1, None, False, "0", -1):
                bad = idle()
                bad[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(m.Refused):
                    m.accepted_idle(bad)

    def test_idle_wrong_origin_false_unknown_or_side_effect_flags_refused(self):
        for key, value in [
            ("origin", "https://other.example"),
            ("status", "INCOMPLETE"),
            ("idle", False),
            ("read_only", False),
            ("transaction_readonly_verified", False),
            ("provider_requests", 1),
            ("work_messages_sent", 1),
        ]:
            bad = idle()
            bad[key] = value
            with self.subTest(key=key), self.assertRaises(m.Refused):
                m.accepted_idle(bad)

    def test_idle_is_explicitly_an_observation_not_an_admission_lock(self):
        updater = m.Updater(fixture_assets())
        self.assertFalse(updater.report["new_task_admission_locked"])
        self.assertTrue(updater.report["observed_after_gate_new_task_race"])
        self.assertFalse(updater.report["active_task_safe_shutdown_proven"])


class EndpointTests(unittest.TestCase):
    def slices(self):
        return {
            "items": [
                {
                    "metadata": {
                        "ownerReferences": [
                            {"kind": "Service", "name": "gateway", "uid": "service-uid"}
                        ]
                    },
                    "endpoints": [
                        {
                            "conditions": {"ready": True, "terminating": False},
                            "targetRef": {
                                "kind": "Pod",
                                "name": "gateway-%02d" % n,
                                "uid": str(n),
                                "namespace": m.NS,
                            },
                        }
                        for n in range(70)
                    ],
                }
            ]
        }

    def test_exact_seventy_owned_ready_endpoints(self):
        self.assertEqual(len(m.endpoint_pods(self.slices(), "service-uid")), 70)

    def test_wrong_service_owner_refused(self):
        with self.assertRaisesRegex(m.Refused, "SERVICE_OWNER"):
            m.endpoint_pods(self.slices(), "other")

    def test_wrong_count_unready_terminating_or_foreign_namespace_refused(self):
        for change in ("count", "ready", "terminating", "namespace", "kind"):
            obj = self.slices()
            endpoint = obj["items"][0]["endpoints"][0]
            if change == "count":
                obj["items"][0]["endpoints"].pop()
            if change == "ready":
                endpoint["conditions"]["ready"] = False
            if change == "terminating":
                endpoint["conditions"]["terminating"] = True
            if change == "namespace":
                endpoint["targetRef"]["namespace"] = "other"
            if change == "kind":
                endpoint["targetRef"]["kind"] = "Node"
            with self.subTest(change=change), self.assertRaises(m.Refused):
                m.endpoint_pods(obj, "service-uid")

    def test_dualstack_duplicate_same_pod_deduplicates(self):
        obj = self.slices()
        obj["items"].append(copy.deepcopy(obj["items"][0]))
        self.assertEqual(len(m.endpoint_pods(obj, "service-uid")), 70)


class HostSafetyTests(unittest.TestCase):
    def test_sealed_assets_match_exact_six_candidate_bytes(self):
        assets = fixture_assets()
        sealed = {
            k: {"sha256": m.sha(v), "base64": base64.b64encode(v).decode()}
            for k, v in assets.items()
            if k != "pre"
        }
        with mock.patch.object(m, "ASSETS", sealed):
            loaded = m.load_assets()
            self.assertEqual(loaded["manager.py"], assets["manager.py"])
            sealed["conversation_url.py"]["base64"] = base64.b64encode(
                b"changed"
            ).decode()
            with self.assertRaisesRegex(m.Refused, "ASSET_HASH"):
                m.load_assets()

    def test_empty_assets_fail_closed(self):
        with (
            mock.patch.object(m, "ASSETS", {}),
            self.assertRaisesRegex(m.Refused, "NOT_SEALED"),
        ):
            m.load_assets()

    def test_valid_rehashed_but_unpinned_candidate_refuses(self):
        assets = fixture_assets()
        assets["manager.py"] += b"\n"
        sealed = {
            k: {"sha256": m.sha(v), "base64": base64.b64encode(v).decode()}
            for k, v in assets.items()
            if k != "pre"
        }
        with (
            mock.patch.object(m, "ASSETS", sealed),
            self.assertRaisesRegex(m.Refused, "CANDIDATE_SOURCE_CHANGED"),
        ):
            m.load_assets()

    def test_claim_is_exclusive_and_private_backup_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "private"
            updater = m.Updater(fixture_assets())
            updater.before = deployment()
            updater.expected = {"exact": "candidate"}
            with mock.patch.object(m, "CONTROL", root):
                updater.claim()
                self.assertTrue(updater.report["once_claimed"])
                self.assertEqual(
                    json.loads((root / "BEFORE-DEPLOYMENT.json").read_text()),
                    deployment(),
                )
                self.assertEqual(
                    json.loads((root / "CANDIDATE-SPEC.json").read_text()),
                    updater.expected,
                )
                with self.assertRaisesRegex(m.Refused, "ONCE_DIRECTORY_EXISTS"):
                    updater.claim()
                self.assertTrue((root / "JOURNAL-0000.json").exists())

    def test_private_write_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "receipt.json"
            m.private_write(path, b"first")
            with self.assertRaises(FileExistsError):
                m.private_write(path, b"second")
            self.assertEqual(path.read_bytes(), b"first")

    def test_get_secret_delete_other_or_arbitrary_patch_never_spawn(self):
        updater = m.Updater(fixture_assets())
        updater.before = deployment()
        updater.expected = {"new": True}
        with mock.patch.object(m.subprocess, "Popen") as proc:
            for args in (
                ["get", "secret", "credentials", "-o", "json"],
                ["get", "pods", "-A", "-o", "json"],
                ["delete", "pod", "gateway-production"],
                [
                    "patch",
                    "deployment",
                    "frontend",
                    "--type=json",
                    "--patch-file",
                    "-",
                    "-o",
                    "json",
                ],
                [
                    "exec",
                    "unknown-pod",
                    "-c",
                    "gateway",
                    "--",
                    "python3",
                    "-I",
                    "-B",
                    "-u",
                    "-c",
                    "pass",
                ],
            ):
                with self.subTest(args=args), self.assertRaises(m.Refused):
                    updater.command(
                        args,
                        input_bytes=b"{}" if args[0] in ("patch", "delete") else None,
                    )
            proc.assert_not_called()

    def test_create_rejects_extra_secret_mount_before_spawn(self):
        updater = m.Updater(fixture_assets())
        updater.sample = {"spec": {"nodeName": "datax-01"}}
        obj = m.maintenance_manifest(
            updater.operation, updater.maint_name, "datax-01", updater.cm_name
        )
        obj["spec"]["volumes"].append(
            {"name": "secret", "secret": {"secretName": "credentials"}}
        )
        with (
            mock.patch.object(m.subprocess, "Popen") as proc,
            self.assertRaisesRegex(m.Refused, "CREATE_MANIFEST_SCOPE"),
        ):
            updater.command(["create", "-f", "-", "-o", "json"], m.canonical(obj))
        proc.assert_not_called()

    def test_patch_count_and_may_apply_journal_before_command(self):
        updater = m.Updater(fixture_assets())
        patch_fixture(updater)
        updater.source = mock.Mock()
        updater.idle = mock.Mock(return_value=idle())
        updater.journal = mock.Mock()

        def fail_command(*args, **kwargs):
            self.assertEqual(updater.report["patch_request_count"], 1)
            self.assertTrue(updater.report["patch_may_have_applied"])
            updater.journal.assert_called_with("GATEWAY_SPEC_CAS_REQUEST")
            raise m.Refused("UNCERTAIN_TRANSPORT")

        updater.command = fail_command
        with (
            mock.patch.object(pre, "deployment_guard"),
            mock.patch.object(pre, "pod_guard", return_value={"runtime": "fixed"}),
            mock.patch.object(m, "private_write"),
            self.assertRaisesRegex(m.Refused, "UNCERTAIN_TRANSPORT"),
        ):
            updater.patch()

    def test_active_gate_failure_does_not_issue_patch(self):
        updater = m.Updater(fixture_assets())
        patch_fixture(updater)
        updater.idle = mock.Mock(side_effect=m.Refused("ACTIVE_TASKS_PRESENT"))
        updater.command = mock.Mock()
        with (
            mock.patch.object(pre, "deployment_guard"),
            mock.patch.object(pre, "pod_guard", return_value={"runtime": "fixed"}),
            self.assertRaisesRegex(m.Refused, "ACTIVE_TASKS"),
        ):
            updater.patch()
        updater.command.assert_not_called()
        self.assertFalse(updater.report["patch_may_have_applied"])

    def test_skill_install_receipt_and_readonly_view_required(self):
        updater = m.Updater(fixture_assets())
        receipt = {"status": "PORTABLE_SKILL_INSTALLED_GATEWAY_LINK_PENDING", "operation": "synthetic", "after_tree_sha256": "f880bc0bd379612a20c0f62fe0c3c14389aad4a9aa26960f5d67a3145f8a7044", "old_skill_removed_from_discovery": True, "maintenance_pod_absent": True}
        pod = {"metadata": {"name": "gateway-synthetic", "uid": "poduid"}, "spec": {"containers": [{"name": "gateway", "volumeMounts": [{"name": "skills", "mountPath": "/mnt/skills", "readOnly": True}]}], "volumes": [{"name": "skills", "persistentVolumeClaim": {"claimName": "deer-flow-skills-rwx-pvc"}}]}}
        reader = {"status": "PORTABLE_SKILL_TREE_VERIFIED", "files": 105, "tree_sha256": receipt["after_tree_sha256"], "read_only": True}
        updater.child = mock.Mock(return_value=reader)
        with mock.patch.object(m, "private_read", return_value=m.canonical(receipt)):
            accepted = updater.verify_installed_skill(pod)
            self.assertEqual(accepted["files"], 105)
            changed = copy.deepcopy(pod)
            changed["spec"]["containers"][0]["volumeMounts"][0]["readOnly"] = False
            with self.assertRaisesRegex(m.Refused, "READONLY_SHARED"):
                updater.verify_installed_skill(changed)
            changed["spec"]["volumes"][0]["persistentVolumeClaim"]["readOnly"] = True
            self.assertEqual(updater.verify_installed_skill(changed)["files"], 105)
        wrong = dict(receipt, old_skill_removed_from_discovery=False)
        with mock.patch.object(m, "private_read", return_value=m.canonical(wrong)), self.assertRaisesRegex(m.Refused, "INSTALL_RECEIPT_REQUIRED"):
            updater.verify_installed_skill(pod)

    def test_operator_and_embedded_fleet_code_parse_python37(self):
        ast.parse(SOURCE.read_text(encoding="utf-8"), feature_version=(3, 7))
        updater = m.Updater(fixture_assets())

        def capture(pod, container, source, arguments, timeout):
            ast.parse(source, feature_version=(3, 7))
            raise m.Refused("CAPTURE_COMPLETE")

        updater.child = capture
        with (
            mock.patch.object(pre, "mount_projection", return_value=({}, {})),
            self.assertRaisesRegex(m.Refused, "CAPTURE_COMPLETE"),
        ):
            updater.source(
                {"metadata": {"name": "gateway-one"}}, candidate=False, health=True
            )

    def test_wrong_host_exits_without_assets_commands_or_files(self):
        with (
            mock.patch.object(m, "load_assets") as load,
            mock.patch.object(m, "print", create=True),
            mock.patch.object(m.os, "name", "nt"),
        ):
            self.assertEqual(m.main(["--activate-result-links-in-idle-window-once"]), 1)
            load.assert_not_called()


if __name__ == "__main__":
    unittest.main()

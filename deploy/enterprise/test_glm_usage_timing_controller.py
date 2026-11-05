"""Offline Python 3.7 controller tests; no cluster/provider traffic."""

import copy
import importlib.util
import pathlib
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "candidate", pathlib.Path(__file__).with_name("probe-glm-usage-timing.py")
)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


def deployment():
    return {
        "metadata": {"uid": M.UID, "generation": 381},
        "spec": {
            "replicas": 70,
            "template": {
                "spec": {
                    "containers": [{"name": "gateway", "image": "fixture@sha256:abc"}]
                }
            },
        },
        "status": {
            "observedGeneration": 381,
            "replicas": 70,
            "updatedReplicas": 70,
            "readyReplicas": 70,
            "availableReplicas": 70,
        },
    }


class ControllerTests(unittest.TestCase):
    def test_ready(self):
        M.ready(deployment())

    def test_each_rollout_mismatch_refused(self):
        for key in (
            "replicas",
            "updatedReplicas",
            "readyReplicas",
            "availableReplicas",
            "observedGeneration",
        ):
            d = deployment()
            d["status"][key] -= 1
            with self.subTest(key=key), self.assertRaises(M.Refused):
                M.ready(d)

    def test_terminating_or_unavailable_refused(self):
        for key in ("terminatingReplicas", "unavailableReplicas"):
            d = deployment()
            d["status"][key] = 1
            with self.assertRaises(M.Refused):
                M.ready(d)

    def test_uid_refused(self):
        d = deployment()
        d["metadata"]["uid"] = "another"
        with self.assertRaises(M.Refused):
            M.ready(d)

    def test_binding_ignores_status_but_not_spec(self):
        d = deployment()
        b = M.binding(d)
        d["status"]["replicas"] += 1
        self.assertEqual(b, M.binding(d))
        d["spec"]["replicas"] += 1
        self.assertNotEqual(b, M.binding(d))

    def run_fixture(self, failure=None):
        d = deployment()
        pod = {
            "metadata": {
                "name": "gateway-fixture",
                "uid": "pod1",
                "ownerReferences": [{"uid": "rs1", "controller": True}],
            },
            "spec": {
                "nodeName": "fixture-node",
                "containers": [{"name": "gateway", "image": "fixture@sha256:abc"}],
            },
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {"name": "gateway", "ready": True, "containerID": "fixture"}
                ],
            },
        }
        actions = []
        report = {}

        def query(args, payload=None, timeout=25):
            actions.append(args)
            if args[:2] == ["get", "deployment"]:
                current = copy.deepcopy(d)
                if failure == "rollout":
                    current["status"]["updatedReplicas"] = 40
                return current
            if args[:2] == ["get", "replicasets"]:
                return {
                    "items": [
                        {
                            "metadata": {
                                "uid": "rs1",
                                "ownerReferences": [{"uid": M.UID, "controller": True}],
                            }
                        }
                    ]
                }
            if args[:2] == ["get", "pods"]:
                return {"items": [copy.deepcopy(pod)]}
            if args[:2] == ["get", "pod"]:
                current = copy.deepcopy(pod)
                if failure == "pod_changed":
                    current["metadata"]["uid"] = "replacement"
                return current
            self.assertEqual(args[0], "exec")
            import json

            value = json.loads(payload)
            if not value["run"]:
                return {
                    "status": "INCOMPLETE" if failure == "source" else "PREFLIGHT_PASS"
                }
            if failure == "timeout":
                raise TimeoutError()
            return {
                "status": "INCOMPLETE"
                if failure == "provider"
                else "GLM_USAGE_TIMING_OBSERVED"
            }

        claims = []
        with patch.object(M, "query", query):
            try:
                M.execute(report, lambda: claims.append(True))
            except (M.Refused, TimeoutError):
                pass
        self.assertTrue(all(a[0] in ("get", "exec") for a in actions))
        return report, claims

    def test_positive(self):
        report, claims = self.run_fixture()
        self.assertEqual(report["status"], "GLM_USAGE_TIMING_COLLECTED_NOT_DEPLOYED")
        self.assertEqual(claims, [True])

    def test_preflight_failures_do_not_claim_or_call_provider(self):
        for failure in ("rollout", "source", "pod_changed"):
            report, claims = self.run_fixture(failure)
            self.assertFalse(claims)
            self.assertFalse(report.get("provider_probe_requested"))

    def test_timeout_retains_uncertainty(self):
        report, claims = self.run_fixture("timeout")
        self.assertEqual(claims, [True])
        self.assertTrue(report["provider_outcome_may_be_uncertain"])

    def test_provider_failure_not_success(self):
        report, claims = self.run_fixture("provider")
        self.assertEqual(claims, [True])
        self.assertNotIn("status", report)


if __name__ == "__main__":
    unittest.main()

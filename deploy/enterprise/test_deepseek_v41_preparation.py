"""Offline controller checks, also runnable on operator Python 3.7."""

import importlib.util
import json
import pathlib
import unittest
from contextlib import ExitStack
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "preparation", pathlib.Path(__file__).with_name("prepare-deepseek-v41-provider.py")
)
P = importlib.util.module_from_spec(spec)
spec.loader.exec_module(P)


class PreparationTests(unittest.TestCase):
    def exercise(self, failure=None):
        deploy = dict(
            metadata=dict(uid=P.GATEWAY_UID, generation=379),
            spec=dict(
                replicas=70,
                template=dict(
                    spec=dict(containers=[dict(name="gateway", image="fixture")])
                ),
            ),
            status=dict(updatedReplicas=70, readyReplicas=70),
        )
        pod = dict(
            metadata=dict(name="fixture", uid="pod-uid"),
            spec=dict(
                nodeName="node", containers=[dict(name="gateway", image="fixture")]
            ),
            status=dict(
                phase="Running",
                containerStatuses=[
                    dict(name="gateway", ready=True, containerID="containerd://fixture")
                ],
            ),
        )
        calls = []

        def call(args, payload=None, timeout=25):
            calls.append((args, payload))
            if args[:3] == ["get", "deployment", "gateway"]:
                value = json.loads(json.dumps(deploy))
                if failure == "drift" and len(calls) > 2:
                    value["metadata"]["generation"] += 1
                return value
            if args[:2] == ["get", "pods"]:
                return dict(items=[pod])
            if args[0] == "exec":
                self.assertNotIn("synthetic-secret-key-value", " ".join(args))
                self.assertEqual(
                    json.loads(payload)["key"], "synthetic-secret-key-value"
                )
                return dict(
                    status="INCOMPLETE"
                    if failure == "provider"
                    else "PROVIDER_ACCEPTED_NOT_DEPLOYED",
                    provider_requests_attempted=5,
                )
            if args[:2] == ["get", "pod"]:
                return pod
            if args[0] == "create":
                value = json.loads(payload)
                self.assertTrue(value["immutable"])
                self.assertEqual(set(value["data"]), {P.SECRET_KEY})
                if failure == "secret_unknown":
                    raise TimeoutError()
                return dict(uid="secret-uid", immutable=True)
            raise AssertionError(args)

        report = dict(
            operation="a" * 32,
            secret_creation_requested=False,
            secret_creation_may_have_applied=False,
        )
        contexts = (
            patch.object(P, "PROFILE", {}),
            patch.object(P, "PROBE_SOURCE", "fixture"),
            patch.object(P, "call", side_effect=call),
            patch.object(P.sys.stdin, "isatty", return_value=True),
            patch.object(
                P.getpass, "getpass", return_value="synthetic-secret-key-value"
            ),
            patch("builtins.print"),
        )
        with ExitStack() as stack:
            for context in contexts:
                stack.enter_context(context)
            if failure:
                with self.assertRaises((P.Refused, TimeoutError)):
                    P.prepare(report)
            else:
                P.prepare(report)
        self.assertNotIn("synthetic-secret-key-value", json.dumps(report))
        self.assertFalse(any(c[0][0] in ("patch", "delete", "apply") for c in calls))
        return report, calls

    def test_only_secret_created_after_five_checks(self):
        report, calls = self.exercise()
        self.assertTrue(report["status"].startswith("DEEPSEEK_V41_PROVIDER_ACCEPTED"))
        self.assertEqual(sum(c[0][0] == "create" for c in calls), 1)

    def test_failed_probe_cannot_create_secret(self):
        report, calls = self.exercise("provider")
        self.assertFalse(report["secret_creation_requested"])

    def test_gateway_drift_cannot_create_secret(self):
        report, calls = self.exercise("drift")
        self.assertFalse(report["secret_creation_requested"])

    def test_secret_unknown_is_never_retried(self):
        report, calls = self.exercise("secret_unknown")
        self.assertTrue(report["secret_creation_may_have_applied"])
        self.assertEqual(sum(c[0][0] == "create" for c in calls), 1)


if __name__ == "__main__":
    unittest.main()

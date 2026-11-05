"""Offline controller checks, also runnable on operator Python 3.7."""

import hashlib
import importlib.util
import json
import pathlib
import tempfile
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
            status=dict(observedGeneration=379, replicas=70, updatedReplicas=70, readyReplicas=70, availableReplicas=70),
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
        claims = []
        if failure == "not_ready":
            deploy["status"]["readyReplicas"] = 69
        if failure == "replicas":
            deploy["spec"]["replicas"] = 60

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
                    P.prepare(report, before_provider=lambda: claims.append(True))
            else:
                P.prepare(report, before_provider=lambda: claims.append(True))
        self.assertEqual(len(claims), 0 if failure in ("not_ready", "replicas") else 1)
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

    def test_not_ready_reports_counts_without_consuming_attempt(self):
        report, calls = self.exercise("not_ready")
        self.assertEqual(report["gateway_readiness"]["readyReplicas"], 69)
        self.assertEqual(len(calls), 1)

    def test_replica_change_reports_actual_desired_without_claim(self):
        report, calls = self.exercise("replicas")
        self.assertEqual(report["gateway_readiness"]["desired"], 60)
        self.assertEqual(len(calls), 1)


class ResumeTests(unittest.TestCase):
    def fixture(self, parent, effect=None):
        operation = "b" * 32
        report = dict(operation=operation, status="INCOMPLETE", error_code="gateway_rollout_in_progress",
                      provider_probe_requested=False, secret_creation_requested=False, secret_creation_may_have_applied=False,
                      workload_modified=False, configuration_modified=False, gateway_restarted=False)
        if effect:
            report[effect] = True
        folder = parent / ("portal-deepseek-v41-preparation-" + operation)
        folder.mkdir(mode=0o700)
        raw = P.canonical(report)
        path = folder / "REPORT.json"
        path.write_bytes(raw)
        path.chmod(0o600)
        marker = parent / P.ONCE_NAME
        marker.write_bytes(P.canonical(dict(operation=operation)))
        marker.chmod(0o600)
        return dict(operation=operation, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())

    def test_only_exact_noop_report_allows_separate_once_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = pathlib.Path(tmp)
            prior = self.fixture(parent)
            self.assertEqual(P.verify_prior_attempt(parent, prior), prior["operation"])
            record = dict(operation="c" * 32)
            name = "portal-deepseek-v41-resume-" + prior["operation"] + ".once"
            P.claim_once(parent, name, record)
            with self.assertRaises(FileExistsError):
                P.claim_once(parent, name, record)
            self.assertEqual(json.loads((parent / P.ONCE_NAME).read_bytes())["operation"], prior["operation"])

    def test_any_prior_provider_or_secret_activity_refuses(self):
        for effect in ("provider_probe_requested", "secret_creation_requested", "secret_creation_may_have_applied"):
            with self.subTest(effect=effect), tempfile.TemporaryDirectory() as tmp:
                parent = pathlib.Path(tmp)
                prior = self.fixture(parent, effect)
                with self.assertRaisesRegex(P.Refused, "prior_attempt_may_have_side_effects"):
                    P.verify_prior_attempt(parent, prior)

    def test_hash_or_marker_drift_refuses(self):
        for mode in ("hash", "marker"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as tmp:
                parent = pathlib.Path(tmp)
                prior = self.fixture(parent)
                if mode == "hash":
                    prior["sha256"] = "0" * 64
                else:
                    (parent / P.ONCE_NAME).write_bytes(P.canonical(dict(operation="d" * 32)))
                with self.assertRaises(P.Refused):
                    P.verify_prior_attempt(parent, prior)

    def test_report_symlink_refuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = pathlib.Path(tmp)
            prior = self.fixture(parent)
            target = parent / ("portal-deepseek-v41-preparation-" + prior["operation"]) / "REPORT.json"
            saved = target.with_name("saved.json")
            target.rename(saved)
            target.symlink_to(saved)
            with self.assertRaises(OSError):
                P.verify_prior_attempt(parent, prior)


class Provider503ResumeTests(unittest.TestCase):
    def fixture(self, parent, mutate=None):
        operation, previous = "e" * 32, "b" * 32
        provider = dict(status="INCOMPLETE", stage="flash", http_status=503, provider_requests_attempted=1,
                        checks=[], automatic_retry=False, business_tools_executed=0,
                        stage_results=dict(flash="FAIL", thinking="NOT_ATTEMPTED", tool_call="NOT_ATTEMPTED", tool_resume="NOT_ATTEMPTED", multi_image="NOT_ATTEMPTED"))
        report = dict(operation=operation, status="INCOMPLETE", error_code="provider_acceptance_failed", resumed_noop_operation=previous,
                      provider_probe_requested=True, secret_creation_requested=False, secret_creation_may_have_applied=False,
                      workload_modified=False, configuration_modified=False, gateway_restarted=False, provider=provider,
                      profile_sha256=hashlib.sha256(P.canonical(P.PROFILE)).hexdigest())
        if mutate:
            mutate(report)
        folder = parent / ("portal-deepseek-v41-preparation-" + operation)
        folder.mkdir(mode=0o700)
        raw = P.canonical(report)
        path = folder / "REPORT.json"
        path.write_bytes(raw)
        path.chmod(0o600)
        marker = parent / ("portal-deepseek-v41-resume-" + previous + ".once")
        marker.write_bytes(P.canonical(dict(operation=operation)))
        marker.chmod(0o600)
        return dict(operation=operation, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(), kind="flash_http_503", resumed_noop_operation=previous)

    def test_exact_first_flash_503_can_be_explicitly_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = pathlib.Path(tmp)
            prior = self.fixture(parent)
            self.assertEqual(P.verify_prior_attempt(parent, prior), prior["operation"])
            name = "portal-deepseek-v41-resume-" + prior["operation"] + ".once"
            P.claim_once(parent, name, dict(operation="f" * 32))
            with self.assertRaises(FileExistsError):
                P.claim_once(parent, name, dict(operation="0" * 32))

    def test_timeout_auth_successful_stages_and_secret_activity_are_not_retried(self):
        changes = [
            lambda r: r["provider"].update(http_status=401),
            lambda r: r["provider"].pop("http_status"),
            lambda r: r["provider"].update(stage="tool_resume"),
            lambda r: r["provider"].update(provider_requests_attempted=2),
            lambda r: r["provider"].update(checks=[dict(stage="flash")]),
            lambda r: r["provider"]["stage_results"].update(thinking="PASS"),
            lambda r: r.update(secret_creation_may_have_applied=True),
            lambda r: r.update(profile_sha256="0" * 64),
        ]
        for index, change in enumerate(changes):
            with self.subTest(case=index), tempfile.TemporaryDirectory() as tmp:
                parent = pathlib.Path(tmp)
                prior = self.fixture(parent, change)
                with self.assertRaises(P.Refused):
                    P.verify_prior_attempt(parent, prior)

    def test_prior_marker_mismatch_is_not_new_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = pathlib.Path(tmp)
            prior = self.fixture(parent)
            marker = parent / ("portal-deepseek-v41-resume-" + prior["resumed_noop_operation"] + ".once")
            marker.write_bytes(P.canonical(dict(operation="f" * 32)))
            with self.assertRaisesRegex(P.Refused, "prior_once_identity_changed"):
                P.verify_prior_attempt(parent, prior)


class SyntheticTool503ResumeTests(Provider503ResumeTests):
    def fixture(self, parent, mutate=None):
        def update(report):
            report["resumed_provider_503_operation"] = report.pop("resumed_noop_operation")
            provider = report["provider"]
            provider.update(stage="tool_resume", provider_requests_attempted=4,
                            checks=[dict(stage=s, finish_reason=f) for s, f in (("flash", "stop"), ("thinking", "stop"), ("tool_call", "tool_calls"))],
                            stage_results=dict(flash="PASS", thinking="PASS", tool_call="PASS", tool_resume="FAIL", multi_image="NOT_ATTEMPTED"))
            if mutate:
                mutate(report)
        prior = super().fixture(parent, update)
        prior["kind"] = "synthetic_tool_http_503"
        prior["resumed_provider_503_operation"] = prior.pop("resumed_noop_operation")
        return prior

    def test_timeout_auth_successful_stages_and_secret_activity_are_not_retried(self):
        changes = [
            lambda r: r["provider"].update(http_status=401),
            lambda r: r["provider"].pop("http_status"),
            lambda r: r["provider"].update(business_tools_executed=1),
            lambda r: r["provider"].update(provider_requests_attempted=5),
            lambda r: r["provider"].update(checks=[]),
            lambda r: r["provider"]["stage_results"].update(tool_resume="PASS"),
            lambda r: r.update(secret_creation_may_have_applied=True),
            lambda r: r.update(profile_sha256="0" * 64),
        ]
        for index, change in enumerate(changes):
            with self.subTest(case=index), tempfile.TemporaryDirectory() as tmp:
                parent = pathlib.Path(tmp)
                prior = self.fixture(parent, change)
                with self.assertRaises(P.Refused):
                    P.verify_prior_attempt(parent, prior)

    def test_prior_marker_mismatch_is_not_new_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = pathlib.Path(tmp)
            prior = self.fixture(parent)
            marker = parent / ("portal-deepseek-v41-resume-" + prior["resumed_provider_503_operation"] + ".once")
            marker.write_bytes(P.canonical(dict(operation="f" * 32)))
            with self.assertRaisesRegex(P.Refused, "prior_once_identity_changed"):
                P.verify_prior_attempt(parent, prior)


if __name__ == "__main__":
    unittest.main()

"""Offline tests using real Kubernetes models; never enter app lifespan."""

import argparse
import ast
import hashlib
import importlib.util
import itertools
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from kubernetes import client
from kubernetes.client.rest import ApiException

BASELINE_SHA = None
POOL = ",".join(f"ecs-zdz-pro-{n:04d}" for n in range(13, 18))
BASELINE = None
CANDIDATE = None
SEQUENCE = itertools.count()


def load(path, env):
    name = f"node_pool_test_{next(SEQUENCE)}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        with patch.dict(os.environ, env, clear=True):
            spec.loader.exec_module(module)
    finally:
        sys.modules.pop(name, None)
    return module


def config(agent=False, pvc=True):
    values = {
        "SANDBOX_SERVICE_TYPE": "ClusterIP",
        "K8S_CREATE_NAMESPACE": "false",
        "PPT_MASTER_AGENT_HOST_ENABLED": str(agent).lower(),
        "SANDBOX_IMAGE": "example.invalid/sandbox:test",
        "PPT_MASTER_AGENT_HOST_IMAGE": "example.invalid/agent:test",
        "SANDBOX_ENV_SECRET_NAME": "test-secret-reference",
        "SANDBOX_SECRET_ENV_KEYS": "SANDBOX_API_KEY,PPT_MASTER_RESPONSES_RELAY_KEY",
        "SANDBOX_CA_SECRET_NAME": "test-ca-reference",
        "SANDBOX_OPENSSL_CONFIGMAP_NAME": "test-openssl-reference",
    }
    if pvc:
        values.update(SKILLS_PVC_NAME="test-skills", USERDATA_PVC_NAME="test-userdata")
    return values


def serialized(value):
    with client.ApiClient() as api:
        return api.sanitize_for_serialization(value)


def immediate(coro):
    """These handlers await only our immediate stub, never an event loop/socket."""
    try:
        coro.send(None)
    except StopIteration as done:
        return done.value
    finally:
        coro.close()
    raise AssertionError("Unexpected asynchronous I/O")


class NodePoolTests(unittest.TestCase):
    def setUp(self):
        for target in (
            "socket.socket.connect",
            "socket.create_connection",
            "kubernetes.config.load_kube_config",
            "kubernetes.config.load_incluster_config",
            "kubernetes.client.ApiClient.call_api",
        ):
            guard = patch(
                target,
                side_effect=AssertionError(
                    "Network/Kubernetes access forbidden in offline test"
                ),
            )
            guard.start()
            self.addCleanup(guard.stop)
        env = patch.dict(os.environ, {}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def candidate(self, pool=POOL, **kwargs):
        env = config(**kwargs)
        if pool is not None:
            env["SANDBOX_NODE_POOL"] = pool
        return load(CANDIDATE, env)

    def test_baseline_digest(self):
        self.assertEqual(
            hashlib.sha256(BASELINE.read_bytes()).hexdigest(), BASELINE_SHA
        )

    def test_source_change_scope(self):
        old = ast.parse(BASELINE.read_text(encoding="utf-8"))
        new = ast.parse(CANDIDATE.read_text(encoding="utf-8"))
        allowed = {"_parse_sandbox_node_pool", "_sandbox_node_affinity"}
        new.body = [
            n
            for n in new.body
            if not (
                isinstance(n, ast.FunctionDef)
                and n.name in allowed
                or isinstance(n, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "SANDBOX_NODE_POOL"
                    for t in n.targets
                )
                or isinstance(n, ast.If)
                and "SANDBOX_NODE_POOL" in ast.unparse(n.test)
            )
        ]
        removed = 0
        for node in ast.walk(new):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "V1PodSpec"
            ):
                for kw in list(node.keywords):
                    if kw.arg == "affinity":
                        self.assertEqual(
                            ast.unparse(kw.value), "_sandbox_node_affinity()"
                        )
                        node.keywords.remove(kw)
                        removed += 1
        self.assertEqual(removed, 1)
        self.assertEqual(ast.dump(old), ast.dump(new))

    def test_unset_keeps_entire_pod_and_service_identical(self):
        for agent, pvc, legacy in itertools.product((False, True), repeat=3):
            with self.subTest(agent=agent, pvc=pvc, legacy=legacy):
                env = config(agent, pvc)
                old, new = load(BASELINE, env), load(CANDIDATE, env)
                args = ("test-sandbox", "test-thread", "test-user")
                self.assertEqual(
                    serialized(old._build_pod(*args, include_legacy_skills=legacy)),
                    serialized(new._build_pod(*args, include_legacy_skills=legacy)),
                )
                self.assertEqual(
                    serialized(old._build_service("test-sandbox")),
                    serialized(new._build_service("test-sandbox")),
                )

    def test_enabled_only_adds_required_affinity(self):
        for agent, legacy in itertools.product((False, True), repeat=2):
            with self.subTest(agent=agent, legacy=legacy):
                old = load(BASELINE, config(agent))
                new = self.candidate(agent=agent)
                args = ("test-sandbox", "test-thread", "test-user")
                before = serialized(old._build_pod(*args, include_legacy_skills=legacy))
                after = serialized(new._build_pod(*args, include_legacy_skills=legacy))
                affinity = after["spec"].pop("affinity")
                self.assertEqual(after, before)
                self.assertEqual(
                    affinity,
                    {
                        "nodeAffinity": {
                            "requiredDuringSchedulingIgnoredDuringExecution": {
                                "nodeSelectorTerms": [
                                    {
                                        "matchExpressions": [
                                            {
                                                "key": "kubernetes.io/hostname",
                                                "operator": "In",
                                                "values": POOL.split(","),
                                            }
                                        ]
                                    }
                                ]
                            }
                        }
                    },
                )
                self.assertEqual(
                    serialized(old._build_service("test-sandbox")),
                    serialized(new._build_service("test-sandbox")),
                )

    def test_invalid_pool_rejected_at_import(self):
        bad = [
            "",
            " ",
            ",",
            "node,",
            ",node",
            "a,,b",
            "a,a",
            "a, a",
            "NODE",
            "a_b",
            "a/b",
            "a.b",
            "-node",
            "node-",
            "*",
            "a b",
            "节点",
            "a\nb",
            "a" * 64,
            ",".join(f"n{i}" for i in range(65)),
            "x" * 4097,
        ]
        for value in bad:
            with self.subTest(value=repr(value)[:60]):
                with self.assertRaisesRegex(RuntimeError, "SANDBOX_NODE_POOL"):
                    self.candidate(value)

    def test_parser_bounds_and_whitespace(self):
        mod = self.candidate(" ecs-zdz-pro-0013 , ecs-zdz-pro-0017 ")
        self.assertEqual(
            mod.SANDBOX_NODE_POOL, ("ecs-zdz-pro-0013", "ecs-zdz-pro-0017")
        )
        for value in ("a", "a" * 63, ",".join(f"n{i}" for i in range(64))):
            self.assertEqual(
                self.candidate(value).SANDBOX_NODE_POOL, tuple(value.split(","))
            )

    def test_parser_rejects_nul_without_setting_impossible_os_environment(self):
        with self.assertRaisesRegex(RuntimeError, "SANDBOX_NODE_POOL"):
            self.candidate()._parse_sandbox_node_pool("a\x00b")

    def test_invalid_error_does_not_echo_value(self):
        value = "https://username:private-sentinel@example.invalid"
        with self.assertRaises(RuntimeError) as caught:
            self.candidate(value)
        self.assertNotIn("private-sentinel", str(caught.exception))

    def test_enabled_requires_both_pvc_references(self):
        for missing in ("SKILLS_PVC_NAME", "USERDATA_PVC_NAME"):
            for value in ("", "   "):
                env = config()
                env.update(SANDBOX_NODE_POOL=POOL)
                env[missing] = value
                with (
                    self.subTest(missing=missing, value=value),
                    self.assertRaisesRegex(RuntimeError, "PVC"),
                ):
                    load(CANDIDATE, env)

    def test_startup_snapshot_cannot_be_changed_by_request_environment(self):
        mod = self.candidate()
        with patch.dict(os.environ, {"SANDBOX_NODE_POOL": "unreviewed-node"}):
            manifest = serialized(mod._build_pod("s", "t"))
        values = manifest["spec"]["affinity"]["nodeAffinity"][
            "requiredDuringSchedulingIgnoredDuringExecution"
        ]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"]
        self.assertEqual(values, POOL.split(","))

    def test_affinity_objects_are_not_shared(self):
        mod = self.candidate()
        one = mod._sandbox_node_affinity()
        one.node_affinity.required_during_scheduling_ignored_during_execution.node_selector_terms[
            0
        ].match_expressions[0].values.clear()
        self.assertIn("ecs-zdz-pro-0013", str(serialized(mod._sandbox_node_affinity())))

    def test_secret_references_and_container_separation_preserved(self):
        mod = self.candidate(agent=True)
        pod = mod._build_pod("s", "t", "u")
        main, agent = pod.spec.containers
        self.assertNotIn("PPT_MASTER_RESPONSES_RELAY_KEY", [e.name for e in main.env])
        relay = next(e for e in agent.env if e.name == "PPT_MASTER_RESPONSES_RELAY_KEY")
        self.assertIsNone(relay.value)
        self.assertEqual(relay.value_from.secret_key_ref.name, "test-secret-reference")
        self.assertFalse(pod.spec.automount_service_account_token)
        self.assertIsNone(pod.spec.node_name)
        self.assertIsNone(pod.spec.tolerations)
        self.assertIsNone(pod.spec.scheduler_name)

    def test_existing_out_of_pool_sandbox_is_reused_without_mutation(self):
        mod = self.candidate()
        existing = mod._build_pod("s", "t", "u")
        existing.spec.node_name = "datax-03"
        existing.spec.affinity = None
        existing.status = client.V1PodStatus(phase="Running")
        api = Mock()
        api.read_namespaced_service.return_value = mod._build_service("s")
        api.read_namespaced_pod.return_value = existing
        mod.core_v1 = api
        result = mod.create_sandbox(
            mod.CreateSandboxRequest(sandbox_id="s", thread_id="t", user_id="u")
        )
        self.assertEqual(result.status, "Running")
        self.assertEqual(
            {c[0] for c in api.mock_calls},
            {"read_namespaced_service", "read_namespaced_pod"},
        )

    def test_reuse_identity_gate_still_rejects_other_user(self):
        mod = self.candidate()
        api = Mock()
        api.read_namespaced_service.return_value = mod._build_service("s")
        api.read_namespaced_pod.return_value = mod._build_pod("s", "t", "other-user")
        mod.core_v1 = api
        with self.assertRaises(mod.HTTPException) as caught:
            mod.create_sandbox(
                mod.CreateSandboxRequest(sandbox_id="s", thread_id="t", user_id="u")
            )
        self.assertEqual(caught.exception.status_code, 409)
        api.create_namespaced_pod.assert_not_called()
        api.delete_namespaced_pod.assert_not_called()

    def test_create_sends_restricted_pod_without_retry_elsewhere(self):
        mod = self.candidate()
        api = Mock()
        api.read_namespaced_service.return_value = mod._build_service("s")
        api.read_namespaced_pod.side_effect = [
            ApiException(status=404),
            SimpleNamespace(status=SimpleNamespace(phase="Pending")),
        ]
        mod.core_v1 = api
        result = mod.create_sandbox(
            mod.CreateSandboxRequest(sandbox_id="s", thread_id="t", user_id="u")
        )
        self.assertEqual(result.status, "Pending")
        api.create_namespaced_pod.assert_called_once()
        manifest = serialized(api.create_namespaced_pod.call_args.args[1])
        self.assertIn("affinity", manifest["spec"])
        api.delete_namespaced_pod.assert_not_called()

    def test_request_cannot_override_pool(self):
        mod = self.candidate()
        req = mod.CreateSandboxRequest(
            sandbox_id="s",
            thread_id="t",
            node_name="datax-03",
            SANDBOX_NODE_POOL="datax-03",
        )
        self.assertNotIn("node_name", req.model_dump())
        self.assertEqual(mod.SANDBOX_NODE_POOL, tuple(POOL.split(",")))

    def test_auth_and_health_still_work_without_kubernetes(self):
        mod = self.candidate()
        request = SimpleNamespace(
            url=SimpleNamespace(path="/api/sandboxes"), headers={}, method="POST"
        )

        async def next_handler(req):
            return "accepted"

        self.assertEqual(
            immediate(mod.verify_api_key(request, next_handler)).status_code, 401
        )
        mod.PROVISIONER_API_KEY = "fake-test-key"
        request.headers = {"X-API-Key": "fake-test-key"}
        self.assertEqual(
            immediate(mod.verify_api_key(request, next_handler)), "accepted"
        )
        self.assertEqual(immediate(mod.health()), {"status": "ok"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--baseline-sha", required=True)
    parser.add_argument("--candidate", required=True, type=Path)
    args, rest = parser.parse_known_args()
    BASELINE, CANDIDATE = args.baseline, args.candidate
    BASELINE_SHA = args.baseline_sha
    unittest.main(argv=[sys.argv[0], *rest])

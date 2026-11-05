"""Synthetic, offline coverage for the compatibility activation node gate."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).with_name("oa-compat-node-gate.py")
MODULE_SPEC = importlib.util.spec_from_file_location("oa_compat_node_gate", str(MODULE_PATH))
gate = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(gate)


def node(name, workload=None, taints=None):
    labels = {"kubernetes.io/hostname": name}
    if workload is not None:
        labels["workload"] = workload
    return {
        "metadata": {"name": name, "uid": "uid-" + name, "labels": labels},
        "spec": {"taints": copy.deepcopy(taints or [])},
        "status": {
            "conditions": [
                {"type": "Ready", "status": "True"},
                {"type": "DiskPressure", "status": "False"},
            ]
        },
    }


def taint(value, effect="NoSchedule"):
    return {"key": "dedicated", "value": value, "effect": effect}


def deployment(spec):
    return {"spec": {"template": {"spec": copy.deepcopy(spec)}}}


def required_affinity(terms):
    return {
        "nodeAffinity": {
            "requiredDuringSchedulingIgnoredDuringExecution": {
                "nodeSelectorTerms": copy.deepcopy(terms)
            }
        }
    }


def expression(key, operator, values=None):
    result = {"key": key, "operator": operator}
    if values is not None:
        result["values"] = values
    return result


def fixture():
    nodes = [
        node("gateway-1", "gateway", [taint("gateway")]),
        node("runner-1"),
        node("infra-1", "infra", [taint("infra")]),
    ]
    deployments = {
        "gateway": deployment({
            "nodeSelector": {"workload": "gateway"},
            "affinity": required_affinity([{
                "matchExpressions": [expression("kubernetes.io/hostname", "In", ["gateway-1"])]
            }]),
            "tolerations": [{
                "key": "dedicated", "operator": "Equal", "value": "gateway", "effect": "NoSchedule"
            }],
        }),
        "ppt-master-runner": deployment({"nodeSelector": {}, "affinity": {}, "tolerations": []}),
    }
    expected = {
        "gateway": {"nodes": {"gateway-1": "uid-gateway-1"}},
        "ppt-master-runner": {"nodes": {"runner-1": "uid-runner-1"}},
    }
    return nodes, deployments, expected


class CompatibilityNodeGateTests(unittest.TestCase):
    def setUp(self):
        self.nodes, self.deployments, self.expected = fixture()

    def pod_spec(self, name):
        return self.deployments[name]["spec"]["template"]["spec"]

    def assess(self):
        return gate.assess_nodes(self.nodes, self.deployments, self.expected)

    def assert_refused(self, observation=False):
        with self.assertRaises(gate.NodeGateRefused) as raised:
            self.assess()
        self.assertTrue(str(raised.exception))
        if observation:
            self.assertIsInstance(raised.exception.observation, dict)
        return raised.exception

    def test_dedicated_extra_node_does_not_block_preloaded_roles(self):
        self.assertIsInstance(self.assess(), dict)

    def test_full_five_gateway_ten_runner_matrix_allows_dedicated_extras(self):
        gateways = ["gateway-" + str(i) for i in range(1, 6)]
        runners = ["runner-" + str(i) for i in range(1, 11)]
        self.nodes = [node(name, "gateway", [taint("gateway")]) for name in gateways]
        self.nodes.extend(node(name) for name in runners)
        self.nodes.extend([
            node("infra-1", "infra", [taint("infra")]),
            node("infra-2", "infra", [taint("infra", "NoExecute")]),
        ])
        self.pod_spec("gateway")["affinity"] = required_affinity([{
            "matchExpressions": [expression("kubernetes.io/hostname", "In", gateways)]
        }])
        self.expected["gateway"]["nodes"] = {name: "uid-" + name for name in gateways}
        self.expected["ppt-master-runner"]["nodes"] = {name: "uid-" + name for name in runners}
        self.assertIsInstance(self.assess(), dict)

    def test_untainted_extra_node_is_uncovered_runner_capacity(self):
        self.nodes.append(node("new-worker", "other"))
        self.assert_refused(observation=True)

    def test_cordon_is_not_proof_that_extra_node_is_outside_the_matrix(self):
        extra = node("new-worker")
        extra["spec"]["unschedulable"] = True
        self.nodes.append(extra)
        self.assert_refused(observation=True)

    def test_explicit_unschedulable_taint_excludes_extra_deployment_node(self):
        extra = node("cordoned-worker", taints=[{
            "key": "node.kubernetes.io/unschedulable", "effect": "NoSchedule"
        }])
        extra["spec"]["unschedulable"] = True
        self.nodes.append(extra)
        self.assertIsInstance(self.assess(), dict)

    def test_not_ready_is_not_proof_that_extra_node_is_outside_the_matrix(self):
        extra = node("new-worker")
        extra["status"]["conditions"][0]["status"] = "False"
        self.nodes.append(extra)
        self.assert_refused(observation=True)

    def test_prefer_no_schedule_is_not_a_hard_exclusion(self):
        self.nodes[2]["spec"]["taints"] = [taint("infra", "PreferNoSchedule")]
        self.assert_refused(observation=True)

    def test_no_execute_is_a_hard_exclusion_without_a_toleration(self):
        self.nodes[2]["spec"]["taints"] = [taint("infra", "NoExecute")]
        self.assertIsInstance(self.assess(), dict)

    def test_default_admission_health_tolerations_cannot_be_ignored(self):
        for key in ("node.kubernetes.io/not-ready", "node.kubernetes.io/unreachable"):
            with self.subTest(key=key):
                self.nodes, self.deployments, self.expected = fixture()
                self.nodes.append(node("new-worker", taints=[{"key": key, "effect": "NoExecute"}]))
                self.assert_refused(observation=True)

    def test_non_best_effort_memory_pressure_toleration_cannot_be_ignored(self):
        self.pod_spec("ppt-master-runner")["containers"] = [{
            "name": "runner", "image": "example/runner:fixed",
            "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}},
        }]
        self.nodes.append(node("new-worker", taints=[{
            "key": "node.kubernetes.io/memory-pressure", "effect": "NoSchedule"
        }]))
        self.assert_refused(observation=True)

    def test_taint_removal_cannot_borrow_another_roles_preload(self):
        self.nodes[0]["spec"]["taints"] = []
        self.assert_refused(observation=True)

    def test_exists_wildcard_toleration_exposes_unpreloaded_nodes(self):
        self.pod_spec("ppt-master-runner")["tolerations"] = [{"operator": "Exists"}]
        self.assert_refused(observation=True)

    def test_equal_toleration_defaults_and_empty_effect_match_hard_taints(self):
        self.pod_spec("ppt-master-runner")["tolerations"] = [{"key": "dedicated", "value": "infra"}]
        self.nodes[2]["spec"]["taints"] = [taint("infra", "NoExecute")]
        self.assert_refused(observation=True)

    def test_required_affinity_terms_are_ored(self):
        self.nodes[2]["metadata"]["labels"]["workload"] = "gateway"
        self.nodes[2]["spec"]["taints"] = [taint("gateway")]
        self.pod_spec("gateway")["affinity"] = required_affinity([
            {"matchExpressions": [expression("kubernetes.io/hostname", "In", ["gateway-1"])]},
            {"matchExpressions": [expression("kubernetes.io/hostname", "In", ["infra-1"])]},
        ])
        self.assert_refused(observation=True)

    def test_expressions_in_one_required_term_are_anded(self):
        self.nodes[2]["metadata"]["labels"]["workload"] = "gateway"
        self.nodes[2]["spec"]["taints"] = [taint("gateway")]
        self.pod_spec("gateway")["affinity"] = required_affinity([{
            "matchExpressions": [
                expression("workload", "In", ["gateway"]),
                expression("kubernetes.io/hostname", "NotIn", ["infra-1"]),
            ]
        }])
        self.assertIsInstance(self.assess(), dict)

    def test_empty_required_term_matches_no_nodes(self):
        self.pod_spec("ppt-master-runner")["affinity"] = required_affinity([{}])
        self.nodes.append(node("new-worker"))
        self.assertIsInstance(self.assess(), dict)

    def test_supported_existence_and_numeric_expressions(self):
        self.nodes[0]["metadata"]["labels"]["capacity"] = "8"
        self.pod_spec("gateway")["affinity"] = required_affinity([{
            "matchExpressions": [
                expression("workload", "Exists"),
                expression("blocked", "DoesNotExist"),
                expression("capacity", "Gt", ["7"]),
                expression("capacity", "Lt", ["9"]),
            ]
        }])
        self.assertIsInstance(self.assess(), dict)

    def test_metadata_name_match_fields_support_in_and_not_in(self):
        self.pod_spec("gateway")["affinity"] = required_affinity([{
            "matchFields": [
                expression("metadata.name", "In", ["gateway-1", "infra-1"]),
                expression("metadata.name", "NotIn", ["infra-1"]),
            ]
        }])
        self.assertIsInstance(self.assess(), dict)

    def test_missing_pinned_node_is_rejected(self):
        self.nodes = self.nodes[1:]
        self.assert_refused(observation=True)

    def test_recreated_pinned_node_is_rejected(self):
        self.nodes[1]["metadata"]["uid"] = "replacement-uid"
        self.assert_refused(observation=True)

    def test_pinned_node_health_must_be_explicitly_good(self):
        for condition, status in (("Ready", "False"), ("DiskPressure", "True"), ("Ready", "Unknown")):
            with self.subTest(condition=condition, status=status):
                self.nodes, self.deployments, self.expected = fixture()
                for row in self.nodes[1]["status"]["conditions"]:
                    if row["type"] == condition:
                        row["status"] = status
                self.assert_refused(observation=True)

    def test_missing_pinned_health_condition_is_rejected(self):
        self.nodes[1]["status"]["conditions"] = []
        self.assert_refused(observation=True)

    def test_custom_scheduler_is_rejected(self):
        self.pod_spec("ppt-master-runner")["schedulerName"] = "custom-scheduler"
        self.assert_refused()

    def test_default_scheduler_is_supported(self):
        self.pod_spec("ppt-master-runner")["schedulerName"] = "default-scheduler"
        self.assertIsInstance(self.assess(), dict)

    def test_explicit_node_binding_is_rejected(self):
        self.pod_spec("ppt-master-runner")["nodeName"] = "runner-1"
        self.assert_refused()

    def test_unknown_expression_operator_is_rejected(self):
        self.pod_spec("gateway")["affinity"] = required_affinity([{
            "matchExpressions": [expression("workload", "Approximately", ["gateway"])]
        }])
        self.assert_refused()

    def test_unknown_match_field_is_rejected(self):
        self.pod_spec("gateway")["affinity"] = required_affinity([{
            "matchFields": [expression("metadata.uid", "In", ["uid-gateway-1"])]
        }])
        self.assert_refused()

    def test_malformed_numeric_expression_is_rejected(self):
        self.pod_spec("gateway")["affinity"] = required_affinity([{
            "matchExpressions": [expression("capacity", "Gt", ["not-an-integer"])]
        }])
        self.assert_refused()

    def test_unknown_toleration_operator_is_rejected(self):
        self.pod_spec("ppt-master-runner")["tolerations"] = [{"key": "dedicated", "operator": "Unknown"}]
        self.assert_refused()

    def test_unknown_toleration_effect_is_rejected(self):
        self.pod_spec("ppt-master-runner")["tolerations"] = [{"operator": "Exists", "effect": "MaybeSchedule"}]
        self.assert_refused()

    def test_malformed_selector_is_rejected(self):
        self.pod_spec("ppt-master-runner")["nodeSelector"] = ["workload", "runner"]
        self.assert_refused()

    def test_success_observation_does_not_dump_raw_labels_or_taints(self):
        self.nodes[2]["metadata"]["labels"]["private-label"] = "label-value-that-must-not-leak"
        self.nodes[2]["spec"]["taints"] = [{
            "key": "private-taint", "value": "taint-value-that-must-not-leak", "effect": "NoSchedule"
        }]
        encoded = json.dumps(self.assess(), sort_keys=True)
        self.assertNotIn("label-value-that-must-not-leak", encoded)
        self.assertNotIn("taint-value-that-must-not-leak", encoded)

    def test_refusal_observation_does_not_dump_raw_node_labels(self):
        extra = node("new-worker")
        extra["metadata"]["labels"]["private-label"] = "label-value-that-must-not-leak"
        self.nodes.append(extra)
        failure = self.assert_refused(observation=True)
        self.assertNotIn("label-value-that-must-not-leak", json.dumps(failure.observation, sort_keys=True))


if __name__ == "__main__":
    unittest.main()

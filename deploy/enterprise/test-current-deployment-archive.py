"""Offline privacy and drift checks; no Kubernetes or provider access."""

import copy
import importlib.util
import json
import pathlib
import unittest

spec = importlib.util.spec_from_file_location("archive_reader", pathlib.Path(__file__).with_name("inspect-current-deployment-archive.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def workload():
    return {
        "kind": "Deployment", "metadata": {"name": "weknora-app", "uid": "deployment", "generation": 4},
        "spec": {"replicas": 2, "selector": {"matchLabels": {"app": "weknora-app"}}, "template": {"spec": {
            "containers": [{"name": "app", "image": "example.invalid/app@sha256:abc", "command": ["PRIVATE_COMMAND"],
                            "args": ["PRIVATE_ARG"], "env": [{"name": "PASSWORD", "value": "PRIVATE_PASSWORD"},
                                                           {"name": "WEKNORA_ASYNQ_CORE_CONCURRENCY", "value": "8"},
                                                           {"name": "WORKER_SECRET_TIMEOUT", "value": "12345"},
                                                           {"name": "URL", "value": "https://PRIVATE_URL"}],
                            "resources": {"limits": {"memory": "24Gi"}}}],
            "volumes": [{"name": "config", "configMap": {"name": "app-config"}},
                        {"name": "data", "persistentVolumeClaim": {"claimName": "app-data"}},
                        {"name": "host", "hostPath": {"path": "PRIVATE_HOST_PATH"}}],
        }}}, "status": {"readyReplicas": 2, "observedGeneration": 4},
    }


class FakeReader:
    def __init__(self, config_drift=False, spec_drift=False):
        self.config_calls = 0
        self.deployment_calls = 0
        self.config_drift = config_drift
        self.spec_drift = spec_drift
        self.resources = []

    def get(self, resource, name=None):
        self.resources.append(resource)
        if resource == "deployments":
            self.deployment_calls += 1
            item = workload()
            if self.spec_drift and self.deployment_calls > 1:
                item["spec"]["replicas"] = 3
            return {"items": [item]}
        if resource == "configmap":
            self.config_calls += 1
            return {"metadata": {"name": name, "uid": "cm"}, "data": {"config.yaml": "PRIVATE_CONFIG" + str(self.config_drift and self.config_calls > 1)}}
        if resource == "pods":
            return {"items": [{"metadata": {"labels": {"app": "weknora-app"}}, "spec": {"nodeName": "node-a"},
                               "status": {"containerStatuses": [{"name": "app", "imageID": "sha256:abc", "ready": True}]}}]}
        return {"items": []}


class Tests(unittest.TestCase):
    def test_private_content_never_exported(self):
        reader = FakeReader()
        result = module.collect(reader)
        text = json.dumps(result)
        self.assertNotIn("PRIVATE_", text)
        self.assertNotIn("12345", text)
        self.assertEqual(result["status"], "CURRENT_METADATA_COLLECTED")
        self.assertFalse(result["release_authorized"])
        self.assertFalse(result["full_source_or_effective_database_configuration_collected"])
        self.assertNotIn("secret", reader.resources)

    def test_capacity_and_image_projection(self):
        result = module.collect(FakeReader())
        item = result["workloads"][0]
        self.assertEqual(item["replicas"], 2)
        self.assertEqual(item["containers"][0]["resources"]["limits"]["memory"], "24Gi")
        self.assertEqual(item["containers"][0]["env"][1]["numeric_capacity_value"], "8")
        self.assertEqual(result["runtime_image_counts"][0]["count"], 1)

    def test_configmap_drift(self):
        result = module.collect(FakeReader(config_drift=True))
        self.assertEqual(result["status"], "CHANGED_DURING_COLLECTION")
        self.assertEqual(result["changed_configmaps"], ["app-config"])

    def test_workload_drift(self):
        result = module.collect(FakeReader(spec_drift=True))
        self.assertFalse(result["workload_specs_stable"])
        self.assertEqual(result["status"], "CHANGED_DURING_COLLECTION")

    def test_nested_projected_and_env_references(self):
        sample = {"volumes": [{"projected": {"sources": [{"configMap": {"name": "projected"}}, {"secret": {"name": "not-config"}}]}}],
                  "env": [{"valueFrom": {"configMapKeyRef": {"name": "env-config", "key": "x"}}}], "envFrom": [{"configMapRef": {"name": "whole-config"}}]}
        self.assertEqual(module.config_refs(sample), {"projected", "env-config", "whole-config"})

    def test_status_churn_is_not_configuration_drift(self):
        a = workload()
        b = copy.deepcopy(a)
        b["metadata"]["resourceVersion"] = "new"
        b["status"]["readyReplicas"] = 1
        self.assertEqual(module.binding([a]), module.binding([b]))
        b["metadata"]["uid"] = "replacement"
        self.assertNotEqual(module.binding([a]), module.binding([b]))

    def test_unknown_resource_rejected_before_subprocess(self):
        with self.assertRaisesRegex(module.Failure, "RESOURCE_NOT_ALLOWED"):
            module.Reader().get("secrets")

    def test_secret_capacity_value_never_projected(self):
        result = module.container_projection({"env": [{"name": "MAX_WORKERS_API_KEY", "value": "123"}]})
        self.assertEqual(result["env"][0]["literal_value"], "WITHHELD")


if __name__ == "__main__":
    unittest.main()

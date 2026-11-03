from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "render_no_proxy_patch", ROOT / "render_no_proxy_patch.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RenderNoProxyPatchTests(unittest.TestCase):
    def deployment(self, env):
        return {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {"name": "gateway", "image": "example/gateway:locked", "env": env},
                            {"name": "sidecar", "env": []},
                        ]
                    }
                }
            }
        }

    def test_append_is_idempotent(self):
        self.assertEqual(
            MODULE.append_host("localhost,aiapi.cnooc.com.cn", "aiapi.cnooc.com.cn"),
            "localhost,aiapi.cnooc.com.cn",
        )

    def test_missing_lowercase_is_deleted_on_rollback(self):
        target, rollback = MODULE.render(
            self.deployment([{"name": "NO_PROXY", "value": "localhost"}]),
            "localhost",
            "",
            "aiapi.cnooc.com.cn",
        )
        target_env = target["spec"]["template"]["spec"]["containers"][0]["env"]
        rollback_env = rollback["spec"]["template"]["spec"]["containers"][0]["env"]
        self.assertEqual(target_env[0]["value"], "localhost,aiapi.cnooc.com.cn")
        self.assertEqual(target_env[1]["value"], "localhost,aiapi.cnooc.com.cn")
        self.assertEqual(rollback_env[0]["value"], "localhost")
        self.assertEqual(rollback_env[1], {"name": "no_proxy", "$patch": "delete"})

    def test_value_from_is_restored_exactly(self):
        original = {
            "name": "NO_PROXY",
            "valueFrom": {"configMapKeyRef": {"name": "proxy", "key": "no_proxy"}},
        }
        _, rollback = MODULE.render(
            self.deployment([original]),
            "localhost",
            "localhost",
            "aiapi.cnooc.com.cn",
        )
        row = rollback["spec"]["template"]["spec"]["containers"][0]["env"][0]
        self.assertIsNone(row["value"])
        self.assertEqual(row["valueFrom"], original["valueFrom"])

    def test_documents_are_json_serializable(self):
        target, rollback = MODULE.render(
            self.deployment([]),
            "127.0.0.1",
            "127.0.0.1",
            "aiapi.cnooc.com.cn",
        )
        json.dumps(target)
        json.dumps(rollback)


if __name__ == "__main__":
    unittest.main()

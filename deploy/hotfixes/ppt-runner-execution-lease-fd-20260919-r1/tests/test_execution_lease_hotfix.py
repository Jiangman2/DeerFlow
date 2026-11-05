from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


PATCHER = load_module("execution_lease_patcher", "render_execution_lease_patch.py")
OVERLAY = load_module("runner_overlay", "render_runner_overlay.py")


SOURCE = '''\
import fcntl
def _lock_file_shared(lock_file):
    fcntl.flock(lock_file, fcntl.LOCK_SH)
def _try_lock_file_exclusive(lock_file):
    fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
def _open_lock_file(lock_path):
    return open(lock_path, "a", encoding="utf-8")
def acquire_execution_lease(self, sandbox_id):
    return sandbox_id
'''


def deployment() -> dict:
    return {
        "metadata": {"resourceVersion": "10"},
        "spec": {
            "replicas": 1,
            "template": {
                "metadata": {"labels": {"app": "ppt-master-runner"}},
                "spec": {
                    "volumes": [{"name": "existing", "configMap": {"name": "startup-safety"}}],
                    "containers": [
                        {
                            "name": "ppt-master-runner",
                            "image": "example/runner:locked",
                            "volumeMounts": [{"name": "existing", "mountPath": "/app/backend/app/gateway/deps.py", "subPath": "deps.py"}],
                        }
                    ],
                },
            },
        },
    }


class ExecutionLeasePatchTests(unittest.TestCase):
    def test_exact_a_to_a_plus_patch(self):
        patched, metadata = PATCHER.render(SOURCE)
        self.assertIn('open(lock_path, "a+", encoding="utf-8")', patched)
        self.assertEqual(metadata["patch_mode"], "a-to-a-plus")
        self.assertNotEqual(metadata["source_sha256"], metadata["patched_sha256"])

    def test_single_quote_and_spacing_are_supported(self):
        source = SOURCE.replace('open(lock_path, "a", encoding="utf-8")', "open( lock_path , 'a' , encoding = 'utf-8' )")
        patched, _metadata = PATCHER.render(source)
        self.assertIn("'a+'", patched)

    def test_already_compatible_is_idempotent(self):
        source = SOURCE.replace('"a"', '"a+"')
        patched, metadata = PATCHER.render(source)
        self.assertEqual(patched, source)
        self.assertEqual(metadata["patch_mode"], "already-compatible")

    def test_unknown_open_contract_fails_closed(self):
        with self.assertRaises(ValueError):
            PATCHER.render(SOURCE.replace('"a"', '"w"'))

    def test_missing_shared_lock_contract_fails_closed(self):
        with self.assertRaises(ValueError):
            PATCHER.render(SOURCE.replace("fcntl.LOCK_SH", "0"))

    def test_overlay_preserves_existing_runner_template(self):
        before = deployment()
        patched_source, _ = PATCHER.render(SOURCE)
        cm = OVERLAY.render_configmap(patched_source)
        operations = OVERLAY.render_apply_patch(before, cm["metadata"]["name"])
        self.assertEqual(operations[0]["op"], "test")
        expected = OVERLAY.expected_after(before, cm["metadata"]["name"])
        self.assertEqual(expected["spec"]["replicas"], 1)
        self.assertEqual(expected["spec"]["template"]["spec"]["containers"][0]["image"], "example/runner:locked")
        self.assertEqual(len(expected["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]), 2)

    def test_overlay_rejects_parent_mount_conflict(self):
        before = deployment()
        before["spec"]["template"]["spec"]["containers"][0]["volumeMounts"].append(
            {"name": "bad", "mountPath": "/app/backend/packages/harness/deerflow/community/aio_sandbox"}
        )
        with self.assertRaises(ValueError):
            OVERLAY.render_apply_patch(before, "target")

    def test_rollback_requires_exact_target(self):
        before = deployment()
        live = OVERLAY.expected_after(before, "target")
        live["metadata"] = {"resourceVersion": "11"}
        rollback = OVERLAY.render_rollback_patch(before, live, "target")
        self.assertEqual(rollback[-1]["value"], before["spec"]["template"])
        live["spec"]["replicas"] = 2
        with self.assertRaises(ValueError):
            OVERLAY.render_rollback_patch(before, live, "target")

    def test_cli_metadata_is_json(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "source.py"
            output_path = root / "output.py"
            metadata_path = root / "metadata.json"
            source_path.write_text(SOURCE, encoding="utf-8")
            patched, metadata = PATCHER.render(source_path.read_text(encoding="utf-8"))
            output_path.write_text(patched, encoding="utf-8")
            metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
            self.assertEqual(json.loads(metadata_path.read_text())["lock_file_mode_after"], "a+")


if __name__ == "__main__":
    unittest.main()

"""Offline safety tests for the Gateway-only release controller."""

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest import mock


sys.modules.setdefault("fcntl", types.SimpleNamespace())
SPEC = importlib.util.spec_from_file_location(
    "ppt_relay_rollout_gateway", Path(__file__).with_name("rollout-gateway.py")
)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def deployment(image):
    return {
        "metadata": {"uid": "gateway-uid", "resourceVersion": "123"},
        "spec": {
            "replicas": 1,
            "template": {
                "spec": {
                    "containers": [
                        {"name": "sidecar", "image": "untouched", "env": [{"name": "X", "value": "secret"}]},
                        {"name": "gateway", "image": image, "imagePullPolicy": "Never"},
                    ]
                }
            },
        },
    }


def test_image_patch_is_one_field_and_compare_and_swap(tmp_path):
    before = deployment("old")
    after = deployment("new")
    after["metadata"]["resourceVersion"] = "124"
    calls = []

    with mock.patch.object(release, "get", side_effect=[before, after]), mock.patch.object(
        release, "kube", side_effect=lambda *args, **kwargs: calls.append(args) or "deployment.apps/gateway"
    ):
        result = release.patch_image(before, "new", tmp_path)

    assert result == after
    assert len(calls) == 1
    patch_file = Path(next(arg.split("=", 1)[1] for arg in calls[0] if arg.startswith("--patch-file=")))
    operations = json.loads(patch_file.read_text(encoding="utf-8"))
    assert operations == [
        {"op": "test", "path": "/metadata/uid", "value": "gateway-uid"},
        {"op": "test", "path": "/metadata/resourceVersion", "value": "123"},
        {"op": "test", "path": "/spec", "value": before["spec"]},
        {"op": "replace", "path": "/spec/template/spec/containers/1/image", "value": "new"},
    ]
    assert release.with_image(before["spec"], "new")["template"]["spec"]["containers"][0] == before["spec"]["template"]["spec"]["containers"][0]


def test_image_patch_rejects_concurrent_change(tmp_path):
    before = deployment("old")
    changed = deployment("unexpected")
    with mock.patch.object(release, "get", return_value=changed), mock.patch.object(release, "kube") as kube:
        try:
            release.patch_image(before, "new", tmp_path)
        except RuntimeError as exc:
            assert "changed" in str(exc)
        else:
            raise AssertionError("concurrent change accepted")
    kube.assert_not_called()


def test_rollback_only_reverses_our_candidate(tmp_path):
    before = deployment("old")
    original = (json.dumps(before, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    release.save_bytes(tmp_path, "gateway.before.json", original)
    release.save_json(
        tmp_path,
        "state.json",
        {
            "gateway_backup_sha256": release.sha(original),
            "source_image": "old",
            "source_image_id": "sha256:old",
            "source_sha256": "source-hash",
            "candidate_image": "candidate",
        },
    )
    unexpected = deployment("third-party")
    with mock.patch.object(release, "get", return_value=unexpected), mock.patch.object(release, "patch_image") as patch:
        try:
            release.restore(tmp_path)
        except RuntimeError as exc:
            assert "outside this release" in str(exc)
        else:
            raise AssertionError("unrelated deployment changed during rollback")
    patch.assert_not_called()


def test_rollback_restores_exact_source_image(tmp_path):
    before = deployment("old")
    original = (json.dumps(before, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    release.save_bytes(tmp_path, "gateway.before.json", original)
    release.save_json(
        tmp_path,
        "state.json",
        {
            "gateway_backup_sha256": release.sha(original),
            "source_image": "old",
            "source_image_id": "sha256:old",
            "source_sha256": "source-hash",
            "candidate_image": "candidate",
        },
    )
    current = deployment("candidate")
    with mock.patch.object(release, "get", return_value=current), mock.patch.object(
        release, "patch_image", return_value=deployment("old")
    ) as patch, mock.patch.object(release, "wait_healthy") as wait:
        release.restore(tmp_path)
    patch.assert_called_once_with(current, "old", tmp_path)
    wait.assert_called_once()

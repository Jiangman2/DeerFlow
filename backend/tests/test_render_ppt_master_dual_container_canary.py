"""Canary rendering must never mount a production thread's user-data PVC."""

import importlib.util
from pathlib import Path

import pytest


def _module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "render_ppt_master_dual_container_canary.py"
    spec = importlib.util.spec_from_file_location("render_ppt_master_dual_container_canary", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _live_pod():
    return {
        "metadata": {"name": "sandbox-original", "namespace": "deer-flow", "labels": {"app": "deer-flow-sandbox"}},
        "status": {"conditions": [{"type": "Ready", "status": "True"}]},
        "spec": {
            "nodeName": "datax-02",
            "serviceAccountName": "default",
            "volumes": [
                {"name": "user-data", "persistentVolumeClaim": {"claimName": "real-user-data"}},
                {"name": "skills-public", "persistentVolumeClaim": {"claimName": "shared-skills", "readOnly": True}},
            ],
            "initContainers": [
                {"name": "prepare-user-data", "image": "registry.test/aio:r9"},
                {"name": "verify-skill-mounts", "image": "registry.test/aio:r9"},
            ],
            "containers": [
                {
                    "name": "sandbox",
                    "image": "registry.test/aio:r9",
                    "env": [{"name": "SANDBOX_API_KEY", "valueFrom": {"secretKeyRef": {"name": "env", "key": "SANDBOX_API_KEY"}}}],
                    "volumeMounts": [{"name": "user-data", "mountPath": "/mnt/user-data", "subPath": "real/thread"}],
                },
                {
                    "name": "ppt-master-agent-host",
                    "image": "registry.test/aio:r9",
                    "env": [
                        {"name": "CODEX_CONFIG_TOML", "value": "old"},
                        {"name": "CODEX_MODEL_CATALOG_JSON", "value": "old"},
                    ],
                    "volumeMounts": [{"name": "user-data", "mountPath": "/mnt/user-data", "subPath": "real/thread"}],
                },
            ],
        },
    }


def _render(live=None, **changes):
    module = _module()
    options = dict(
        name="ppt-canary-datax-03",
        node="datax-03",
        aio_image="registry.test/aio:r9",
        agent_image="registry.test/ppt:r16",
        codex_config="model = 'deepseek'",
        model_catalog='{"models":[]}',
    )
    options.update(changes)
    return module.render(live or _live_pod(), **options)


def test_render_isolates_user_data_and_preserves_aio_contract():
    source = _live_pod()
    result = _render(source)
    assert source["spec"]["volumes"][0]["persistentVolumeClaim"]["claimName"] == "real-user-data"
    assert result["metadata"]["labels"]["app"] != "deer-flow-sandbox"
    assert result["spec"]["volumes"][0] == {"name": "user-data", "emptyDir": {}}
    assert result["spec"]["volumes"][1] == source["spec"]["volumes"][1]
    assert result["spec"]["automountServiceAccountToken"] is False
    assert result["spec"]["nodeName"] == "datax-03"
    assert result["spec"]["restartPolicy"] == "Never"
    assert "serviceAccountName" not in result["spec"]
    assert [row["image"] for row in result["spec"]["initContainers"]] == [
        "registry.test/aio:r9",
        "registry.test/aio:r9",
    ]
    aio, host = result["spec"]["containers"]
    assert aio == {**source["spec"]["containers"][0], "imagePullPolicy": "Never"}
    assert host["image"] == "registry.test/ppt:r16"
    assert host["imagePullPolicy"] == "Never"
    env = {row["name"]: row.get("value") for row in host["env"]}
    assert env["CODEX_CONFIG_TOML"] == "model = 'deepseek'"
    assert env["CODEX_MODEL_CATALOG_JSON"] == '{"models":[]}'
    assert env["PPT_MASTER_ENTERPRISE_RELAY_REQUIRED"] == "true"
    relay = next(row for row in host["env"] if row["name"] == "PPT_MASTER_RESPONSES_RELAY_KEY")
    assert relay["valueFrom"]["secretKeyRef"] == {
        "name": "env",
        "key": "PPT_MASTER_RESPONSES_RELAY_KEY",
        "optional": False,
    }


def test_render_fails_without_key_secret_reference():
    live = _live_pod()
    live["spec"]["containers"][0]["env"] = []
    with pytest.raises(ValueError, match="sandbox API key Secret"):
        _render(live)


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"agent_image": "registry.test/aio:r9"}, "must be distinct"),
        ({"aio_image": "registry.test/aio:other"}, "source AIO image"),
        ({"codex_config": ""}, "profile must not be empty"),
        ({"model_catalog": "not-json"}, "Expecting value"),
    ],
)
def test_render_rejects_invalid_release_contract(changes, message):
    with pytest.raises((ValueError, RuntimeError), match=message):
        _render(**changes)


def test_render_rejects_missing_user_data_volume():
    live = _live_pod()
    live["spec"]["volumes"].pop(0)
    with pytest.raises(ValueError, match="exactly one user-data"):
        _render(live)


def test_render_rejects_terminating_or_unready_source():
    live = _live_pod()
    live["metadata"]["deletionTimestamp"] = "2026-09-23T00:00:00Z"
    with pytest.raises(ValueError, match="Ready and non-terminating"):
        _render(live)
    del live["metadata"]["deletionTimestamp"]
    live["status"]["conditions"][0]["status"] = "False"
    with pytest.raises(ValueError, match="Ready and non-terminating"):
        _render(live)


def test_render_rejects_writable_extra_volume():
    live = _live_pod()
    live["spec"]["volumes"].append({"name": "other-data", "persistentVolumeClaim": {"claimName": "important-data"}})
    with pytest.raises(ValueError, match="other writable PVC"):
        _render(live)

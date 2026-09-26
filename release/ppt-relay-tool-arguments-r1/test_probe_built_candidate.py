"""The live probe cannot read unrelated Gateway secrets or execute a tool."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent


@pytest.fixture
def module():
    path = HERE / "probe-built-candidate.py"
    spec = importlib.util.spec_from_file_location("ppt_relay_canary_controller", path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = value
    spec.loader.exec_module(value)
    return value


def deployment():
    return {
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "gateway",
                            "env": [
                                {
                                    "name": "DeepSeek_V4_API_KEY",
                                    "valueFrom": {
                                        "secretKeyRef": {
                                            "name": "model-secret",
                                            "key": "api-key",
                                        }
                                    },
                                },
                                {
                                    "name": "PPT_MASTER_DEEPSEEK_UPSTREAM_URL",
                                    "value": "https://model.example/v1/chat/completions",
                                },
                                {
                                    "name": "SSL_CERT_FILE",
                                    "value": "/etc/deerflow-ca/ca-bundle.crt",
                                },
                                {
                                    "name": "DO_NOT_COPY_SECRET",
                                    "valueFrom": {
                                        "secretKeyRef": {
                                            "name": "other",
                                            "key": "secret",
                                        }
                                    },
                                },
                            ],
                            "volumeMounts": [
                                {
                                    "name": "enterprise-ca",
                                    "mountPath": "/etc/deerflow-ca",
                                    "readOnly": True,
                                },
                                {
                                    "name": "openssl-compat",
                                    "mountPath": "/etc/deerflow-openssl",
                                    "readOnly": True,
                                },
                                {"name": "user-data", "mountPath": "/mnt/user-data"},
                            ],
                        }
                    ],
                    "volumes": [
                        {
                            "name": "enterprise-ca",
                            "secret": {"secretName": "ca-bundle"},
                        },
                        {
                            "name": "openssl-compat",
                            "configMap": {"name": "openssl-compat"},
                        },
                        {
                            "name": "user-data",
                            "persistentVolumeClaim": {"claimName": "user-data"},
                        },
                    ],
                }
            }
        }
    }


def test_canary_copies_only_upstream_key_ca_and_config(module):
    result = {"image": "deerflow.local/gateway:candidate"}
    job = module.render_job(result, "a" * 64, deployment(), "ppt-relay-canary-test")
    pod = job["spec"]["template"]["spec"]
    container = pod["containers"][0]
    rendered = json.dumps(job)
    assert "DO_NOT_COPY_SECRET" not in rendered
    assert "user-data" not in rendered
    assert "PPT_MASTER_RESPONSES_RELAY_KEY" not in rendered
    assert container["env"][0]["valueFrom"]["secretKeyRef"] == {
        "name": "model-secret",
        "key": "api-key",
    }
    assert pod["automountServiceAccountToken"] is False
    assert pod["nodeName"] == "datax-01"
    assert container["imagePullPolicy"] == "Never"
    assert "a" * 64 in container["command"][-1]
    assert "subprocess" not in container["command"][-1]
    compile(container["command"][-1], "<pod-probe>", "exec")


def test_canary_rejects_plaintext_key_and_missing_ca(module):
    instance = deployment()
    instance["spec"]["template"]["spec"]["containers"][0]["env"][0] = {
        "name": "DeepSeek_V4_API_KEY",
        "value": "plain-secret",
    }
    with pytest.raises(ValueError, match="Secret reference"):
        module.render_job({"image": "image"}, "a" * 64, instance, "job")
    instance = deployment()
    instance["spec"]["template"]["spec"]["volumes"].pop(0)
    with pytest.raises(ValueError, match="mounts missing"):
        module.render_job({"image": "image"}, "a" * 64, instance, "job")


def test_wait_uses_long_api_request_timeout(module, monkeypatch):
    seen = []

    def fake(command, input_text=None, timeout=30):
        seen.append(command)
        return ""

    monkeypatch.setattr(module, "run", fake)
    module.kube(["wait", "--for=condition=complete", "job/test", "--timeout=240s"])
    assert "--request-timeout=245s" in seen[0]


def test_import_check_accepts_empty_ctr_output_and_checks_cri(module, monkeypatch):
    commands = []
    image = "deerflow.local/gateway:candidate"
    digest = "sha256:" + "a" * 64

    def fake(command, input_text=None, timeout=30):
        commands.append(command)
        if command[0] == "ctr":
            return ""
        return json.dumps(
            {"status": {"repoTags": [image], "repoDigests": [image + "@" + digest]}}
        )

    monkeypatch.setattr(module, "run", fake)
    module.verify_import(image, digest)
    assert [command[0] for command in commands] == ["ctr", "crictl"]


def test_import_check_rejects_wrong_cri_image_or_digest(module, monkeypatch):
    image = "deerflow.local/gateway:candidate"
    for status in (
        {"repoTags": ["deerflow.local/other:tag"], "repoDigests": []},
        {"repoTags": [image], "repoDigests": [image + "@sha256:" + "b" * 64]},
    ):

        def fake(command, input_text=None, timeout=30):
            return "" if command[0] == "ctr" else json.dumps({"status": status})

        monkeypatch.setattr(module, "run", fake)
        with pytest.raises(ValueError):
            module.verify_import(image, "sha256:" + "a" * 64)

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_provisioner_startup_probe_allows_slow_docker_desktop_cold_start() -> None:
    manifest = ROOT / "k8s" / "local-pvc" / "deer-flow-pvc-stack.yaml"
    documents = list(yaml.safe_load_all(manifest.read_text(encoding="utf-8")))
    deployment = next(
        document
        for document in documents
        if document
        and document.get("kind") == "Deployment"
        and document.get("metadata", {}).get("name") == "provisioner"
    )
    probe = deployment["spec"]["template"]["spec"]["containers"][0]["startupProbe"]
    startup_budget_seconds = probe.get("initialDelaySeconds", 0) + (
        probe["periodSeconds"] * probe["failureThreshold"]
    )

    assert startup_budget_seconds >= 300


def test_weknora_ui_proxy_uses_stable_kubernetes_service_dns() -> None:
    nginx_config = (ROOT / "k8s" / "local-pvc" / "nginx.conf").read_text(encoding="utf-8")

    assert "proxy_pass http://10.96." not in nginx_config
    assert nginx_config.count("proxy_pass http://weknora-frontend:80;") == 7


def test_embedded_weknora_auth_me_uses_a_dedicated_native_proxy() -> None:
    nginx_config = (ROOT / "k8s" / "local-pvc" / "nginx.conf").read_text(encoding="utf-8")

    assert "location = /api/weknora-native/auth/me" in nginx_config
    assert "proxy_pass http://weknora-app:8080/api/v1/auth/me;" in nginx_config
    assert "proxy_set_header X-Tenant-ID $http_x_tenant_id;" in nginx_config


def test_frontend_opens_weknora_through_the_unified_nginx_entrypoint() -> None:
    manifest = ROOT / "k8s" / "local-pvc" / "deer-flow-pvc-stack.yaml"
    documents = list(yaml.safe_load_all(manifest.read_text(encoding="utf-8")))
    deployment = next(
        document
        for document in documents
        if document
        and document.get("kind") == "Deployment"
        and document.get("metadata", {}).get("name") == "frontend"
    )
    env = {
        item["name"]: item.get("value")
        for item in deployment["spec"]["template"]["spec"]["containers"][0]["env"]
    }

    assert env["WEKNORA_FRONTEND_URL"] == "http://127.0.0.1:2026/platform/knowledge-bases"

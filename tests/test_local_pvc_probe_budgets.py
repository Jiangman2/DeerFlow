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


def test_weknora_provisions_one_personal_workspace_and_disables_more_self_service_creation() -> None:
    manifest = ROOT / "k8s" / "weknora" / "weknora.yaml"
    documents = list(yaml.safe_load_all(manifest.read_text(encoding="utf-8")))
    deployment = next(
        document
        for document in documents
        if document
        and document.get("kind") == "Deployment"
        and document.get("metadata", {}).get("name") == "weknora-app"
    )
    env = {
        item["name"]: item.get("value")
        for item in deployment["spec"]["template"]["spec"]["containers"][0]["env"]
    }

    assert env["WEKNORA_AUTH_DEFAULT_TENANT_MODE"] == "create_personal"
    assert env["WEKNORA_TENANT_ENABLE_RBAC"] == "true"
    assert env["WEKNORA_TENANT_SELF_SERVICE_CREATION_ENABLED"] == "false"
    assert env["WEKNORA_TENANT_MAX_OWNED_PER_USER"] == "1"
    assert env["WEKNORA_PREFER_CNOOC_DEEPSEEK"] == "false"


def test_weknora_allows_only_reviewed_internal_model_endpoints_through_ssrf_guard() -> None:
    manifest = ROOT / "k8s" / "weknora" / "weknora.yaml"
    documents = list(yaml.safe_load_all(manifest.read_text(encoding="utf-8")))
    deployment = next(
        document
        for document in documents
        if document
        and document.get("kind") == "Deployment"
        and document.get("metadata", {}).get("name") == "weknora-app"
    )
    env_entries = {
        item["name"]: item
        for item in deployment["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    env = {name: item.get("value") for name, item in env_entries.items()}

    assert env["SSRF_WHITELIST_EXTRA"].split(",") == [
        "model.ai.cnooc",
        "gateway",
    ]
    for hostname in ("model.ai.cnooc", "gateway"):
        assert hostname in env["NO_PROXY"].split(",")
        assert hostname in env["no_proxy"].split(",")
    assert env_entries["LLM_API_KEY"]["valueFrom"]["secretKeyRef"] == {
        "name": "weknora-secret",
        "key": "LLM_API_KEY",
    }
    assert env_entries["EMBEDDING_API_KEY"]["valueFrom"]["secretKeyRef"] == {
        "name": "weknora-secret",
        "key": "EMBEDDING_API_KEY",
    }

    stack = ROOT / "k8s" / "local-pvc" / "deer-flow-pvc-stack.yaml"
    stack_documents = list(yaml.safe_load_all(stack.read_text(encoding="utf-8")))
    gateway = next(
        document
        for document in stack_documents
        if document
        and document.get("kind") == "Deployment"
        and document.get("metadata", {}).get("name") == "gateway"
    )
    gateway_env = {
        item["name"]: item
        for item in gateway["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    for name in ("LLM_API_KEY", "EMBEDDING_API_KEY"):
        gateway_ref = gateway_env[name]["valueFrom"]["secretKeyRef"]
        weknora_ref = env_entries[name]["valueFrom"]["secretKeyRef"]
        assert (gateway_ref["name"], gateway_ref["key"]) == (
            weknora_ref["name"],
            weknora_ref["key"],
        )

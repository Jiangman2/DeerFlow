#!/usr/bin/env python3
"""Fail closed when an enterprise release would regress audited behavior.

This validator is intentionally secret-blind. It checks references, Kubernetes
field names, and routing policy, but never reads or prints Secret values.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Any

import yaml


SANDBOX_PROXY_REFERENCES = {
    "HTTP_PROXY": "$HTTP_PROXY",
    "HTTPS_PROXY": "$HTTPS_PROXY",
    "NO_PROXY": "$NO_PROXY",
    "http_proxy": "$HTTP_PROXY",
    "https_proxy": "$HTTPS_PROXY",
    "no_proxy": "$NO_PROXY",
}

SANDBOX_CA_REFERENCES = {
    "SSL_CERT_FILE": "$SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE": "$REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE": "$CURL_CA_BUNDLE",
    "NODE_EXTRA_CA_CERTS": "$NODE_EXTRA_CA_CERTS",
    "OPENSSL_CONF": "$OPENSSL_CONF",
    "DASHSCOPE_TLS_COMPAT": "$DASHSCOPE_TLS_COMPAT",
}

FORBIDDEN_PROXY_BYPASS_HOSTS = {
    "dashscope.aliyuncs.com",
    "aliyuncs.com",
    "tavily.com",
}

ENTERPRISE_DEEPSEEK_MAX_OUTPUT_TOKENS = 65_536
ENTERPRISE_DEEPSEEK_MAX_INPUT_TOKENS = 1_048_576

REQUIRED_DIRECT_HOSTS = {
    "gateway",
    "provisioner",
    "weknora-app",
    "model.ai.cnooc",
    "datalake.cnooc",
    ".svc",
    ".cluster.local",
}

REQUIRED_BASELINE_CONTROLS = {
    "model.deepseek-only",
    "sandbox.command-proxy",
    "sandbox.egress-ca",
    "external.dashscope-image",
    "external.tavily",
    "images.immutable-arm64",
    "database.pg-search-arm64",
    "storage.static-local-pv",
    "mineru.three-node-safe-parallelism",
    "mineru.visual-documents",
    "weknora.official-ui-resource-images",
    "weknora.internal-model-defaults",
    "weknora.internal-relay-ssrf",
    "weknora.neo4j",
    "weknora.same-origin-sso-ui",
    "nginx.frontend-api-order",
    "exposure.single-nodeport",
    "scheduler.bounded-recursion",
    "security.platform-boundary",
    "security.file-boundary",
    "dashboard.audit-history",
    "operations.backup-before-rollout",
}

REQUIRED_THIRD_PARTY_DIGESTS = {
    "busybox",
    "nginx",
    "postgres",
    "redis",
    "neo4j",
    "wechatopenai/weknora-docreader",
    "wechatopenai/weknora-ui",
}

REQUIRED_CUSTOM_IMAGES = {
    "paradedb/paradedb",
    "wechatopenai/weknora-app",
    "deer-flow-gateway",
    "deer-flow-frontend",
    "deer-flow-provisioner",
    "deer-flow-skills",
    "deer-flow-mineru-adapter",
}

WEKNORA_UI_DIGEST = (
    "sha256:401604e79a5717dd966a13383d775f15d3125481d3e080eba329e4a0fbcf9523"
)


class ValidationError(ValueError):
    """Raised when a release violates an enterprise deployment invariant."""


def _load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _load_documents(path: Path) -> list[dict[str, Any]]:
    return [
        document
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8"))
        if isinstance(document, dict)
    ]


def _resource(
    documents: list[dict[str, Any]],
    kind: str,
    name: str,
) -> dict[str, Any]:
    try:
        return next(
            document
            for document in documents
            if document.get("kind") == kind
            and document.get("metadata", {}).get("name") == name
        )
    except StopIteration as exc:
        raise ValidationError(f"{kind} {name!r} is missing") from exc


def _deployment(documents: list[dict[str, Any]], name: str) -> dict[str, Any]:
    return _resource(documents, "Deployment", name)


def _container(deployment: dict[str, Any], name: str) -> dict[str, Any]:
    containers = deployment["spec"]["template"]["spec"]["containers"]
    try:
        return next(container for container in containers if container.get("name") == name)
    except StopIteration as exc:
        raise ValidationError(
            f"Container {name!r} is missing from Deployment "
            f"{deployment.get('metadata', {}).get('name')!r}"
        ) from exc


def _environment(container: dict[str, Any]) -> dict[str, str]:
    return {
        item["name"]: str(item.get("value", ""))
        for item in container.get("env", [])
        if "name" in item and "value" in item
    }


def _csv(value: str) -> set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def _assert_proxy_bypass(name: str, value: str) -> None:
    entries = _csv(value)
    forbidden = sorted(
        host
        for host in FORBIDDEN_PROXY_BYPASS_HOSTS
        if host in entries or any(entry.endswith(f".{host}") for entry in entries)
    )
    if forbidden:
        raise ValidationError(
            f"{name} sends audited external providers direct: {', '.join(forbidden)}"
        )

    missing = sorted(REQUIRED_DIRECT_HOSTS - entries)
    if missing:
        raise ValidationError(
            f"{name} is missing required direct service/host entries: "
            f"{', '.join(missing)}"
        )


def validate_config(path: Path) -> list[str]:
    config = _load_yaml(path)
    if not isinstance(config, dict):
        raise ValidationError(f"{path} does not contain a YAML mapping")

    sandbox = config.get("sandbox")
    if not isinstance(sandbox, dict):
        raise ValidationError("sandbox configuration is missing")
    environment = sandbox.get("environment")
    if not isinstance(environment, dict):
        raise ValidationError("sandbox.environment configuration is missing")

    for key, expected in SANDBOX_PROXY_REFERENCES.items():
        actual = environment.get(key)
        if actual != expected:
            state = "missing" if actual is None else "empty" if actual == "" else "invalid"
            raise ValidationError(
                f"sandbox.environment.{key} is {state}; expected {expected!r}"
            )

    for key, expected in SANDBOX_CA_REFERENCES.items():
        actual = environment.get(key)
        if actual != expected:
            state = "missing" if actual is None else "empty" if actual == "" else "invalid"
            raise ValidationError(
                f"sandbox.environment.{key} is {state}; expected {expected!r}"
            )

    if environment.get("IMAGE_GENERATION_PROVIDER") != "dashscope":
        raise ValidationError(
            "sandbox.environment.IMAGE_GENERATION_PROVIDER must be 'dashscope'"
        )
    expected_ppt_image_environment = {
        "IMAGE_BACKEND": "qwen",
        "QWEN_BASE_URL": "https://dashscope.aliyuncs.com",
        "QWEN_MODEL": "qwen-image-2.0-pro",
    }
    for key, expected in expected_ppt_image_environment.items():
        if environment.get(key) != expected:
            raise ValidationError(
                f"sandbox.environment.{key} must be {expected!r}"
            )
    if "GEMINI_API_KEY" in environment:
        raise ValidationError("GEMINI_API_KEY must not be injected into enterprise Sandbox")

    models = config.get("models")
    model_names = [
        model.get("name")
        for model in models or []
        if isinstance(model, dict)
    ]
    if model_names != ["deepseek-v4-flash"]:
        raise ValidationError(
            "enterprise models must contain only 'deepseek-v4-flash'; got "
            f"{model_names!r}"
        )

    deepseek_model = models[0]
    if deepseek_model.get("max_tokens") != ENTERPRISE_DEEPSEEK_MAX_OUTPUT_TOKENS:
        raise ValidationError(
            "models[0].max_tokens must be "
            f"{ENTERPRISE_DEEPSEEK_MAX_OUTPUT_TOKENS} for long presentation tool calls"
        )
    max_input_tokens = (deepseek_model.get("profile") or {}).get(
        "max_input_tokens"
    )
    if max_input_tokens != ENTERPRISE_DEEPSEEK_MAX_INPUT_TOKENS:
        raise ValidationError(
            "models[0].profile.max_input_tokens must be "
            f"{ENTERPRISE_DEEPSEEK_MAX_INPUT_TOKENS} for the audited 1M context window"
        )

    deepseek_consumers = {
        "summarization.model_name": (config.get("summarization") or {}).get(
            "model_name"
        ),
        "input_polish.model_name": (config.get("input_polish") or {}).get(
            "model_name"
        ),
        "memory.backend_config.model.model": (
            (((config.get("memory") or {}).get("backend_config") or {}).get("model"))
            or {}
        ).get("model"),
    }
    for path_name, value in deepseek_consumers.items():
        if value != "deepseek-v4-flash":
            raise ValidationError(
                f"{path_name} must be 'deepseek-v4-flash', got {value!r}"
            )

    title_model = (config.get("title") or {}).get("model_name")
    if title_model not in (None, "deepseek-v4-flash"):
        raise ValidationError(
            "title.model_name must use the sole default model or "
            f"'deepseek-v4-flash', got {title_model!r}"
        )

    scheduler = config.get("scheduler") or {}
    if scheduler.get("enabled") is not True:
        raise ValidationError("scheduler.enabled must be true")
    if scheduler.get("recursion_limit") != 300:
        raise ValidationError("scheduler.recursion_limit must be 300")
    max_concurrent_runs = scheduler.get("max_concurrent_runs")
    if not isinstance(max_concurrent_runs, int) or not 1 <= max_concurrent_runs <= 3:
        raise ValidationError(
            "scheduler.max_concurrent_runs must be an integer between 1 and 3"
        )

    return [
        "sandbox command proxy and CA references",
        "DashScope-only image provider policy",
        "DeepSeek-only runtime model policy",
        "bounded scheduled-task policy",
    ]


def validate_manifest(path: Path) -> list[str]:
    documents = _load_documents(path)
    provisioner_env = _environment(
        _container(_deployment(documents, "provisioner"), "provisioner")
    )
    gateway_env = _environment(_container(_deployment(documents, "gateway"), "gateway"))
    weknora_env = _environment(
        _container(_deployment(documents, "weknora-app"), "app")
    )
    frontend_env = _environment(
        _container(_deployment(documents, "frontend"), "frontend")
    )

    secret_keys = _csv(provisioner_env.get("SANDBOX_SECRET_ENV_KEYS", ""))
    required_secret_keys = {
        "DASHSCOPE_API_KEY",
        "SANDBOX_API_KEY",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
    }
    missing_secret_keys = sorted(required_secret_keys - secret_keys)
    if missing_secret_keys:
        raise ValidationError(
            "SANDBOX_SECRET_ENV_KEYS is missing: " + ", ".join(missing_secret_keys)
        )
    if "GEMINI_API_KEY" in secret_keys:
        raise ValidationError("GEMINI_API_KEY remains in SANDBOX_SECRET_ENV_KEYS")

    passthrough_keys = _csv(provisioner_env.get("SANDBOX_PASSTHROUGH_ENV_KEYS", ""))
    required_passthrough_keys = {
        "IMAGE_BACKEND",
        "IMAGE_GENERATION_PROVIDER",
        "QWEN_BASE_URL",
        "QWEN_MODEL",
        "DASHSCOPE_TLS_COMPAT",
        "NO_PROXY",
        "no_proxy",
    }
    missing_passthrough = sorted(required_passthrough_keys - passthrough_keys)
    if missing_passthrough:
        raise ValidationError(
            "SANDBOX_PASSTHROUGH_ENV_KEYS is missing: "
            + ", ".join(missing_passthrough)
        )

    expected_provisioner = {
        "IMAGE_BACKEND": "qwen",
        "IMAGE_GENERATION_PROVIDER": "dashscope",
        "QWEN_BASE_URL": "https://dashscope.aliyuncs.com",
        "QWEN_MODEL": "qwen-image-2.0-pro",
        "DASHSCOPE_TLS_COMPAT": "true",
        "SANDBOX_CA_SECRET_NAME": "deer-flow-enterprise-ca",
        "SANDBOX_CA_SECRET_KEY": "ca-bundle.crt",
        "SANDBOX_CA_MOUNT_PATH": "/etc/deerflow-ca",
        "SANDBOX_OPENSSL_CONFIGMAP_NAME": "deer-flow-gateway-openssl-compat",
        "SANDBOX_OPENSSL_CONFIGMAP_KEY": "openssl.cnf",
        "SANDBOX_OPENSSL_MOUNT_PATH": "/etc/deerflow-openssl",
    }
    for key, expected in expected_provisioner.items():
        if provisioner_env.get(key) != expected:
            raise ValidationError(
                f"Provisioner {key} must be {expected!r}, got "
                f"{provisioner_env.get(key)!r}"
            )

    expected_gateway = {
        "SSL_CERT_FILE": "/etc/deerflow-ca/ca-bundle.crt",
        "REQUESTS_CA_BUNDLE": "/etc/deerflow-ca/ca-bundle.crt",
        "CURL_CA_BUNDLE": "/etc/deerflow-ca/ca-bundle.crt",
        "NODE_EXTRA_CA_CERTS": "/etc/deerflow-ca/ca-bundle.crt",
        "OPENSSL_CONF": "/etc/deerflow-openssl/openssl.cnf",
        "DASHSCOPE_TLS_COMPAT": "true",
        "TAVILY_TLS_COMPAT": "true",
        "WEKNORA_BAILIAN_TLS_COMPAT": "true",
    }
    for key, expected in expected_gateway.items():
        if gateway_env.get(key) != expected:
            raise ValidationError(
                f"Gateway {key} must be {expected!r}, got {gateway_env.get(key)!r}"
            )

    for workload, environment in (
        ("Provisioner", provisioner_env),
        ("Gateway", gateway_env),
    ):
        upper = environment.get("NO_PROXY", "")
        lower = environment.get("no_proxy", "")
        if not upper or upper != lower:
            raise ValidationError(
                f"{workload} NO_PROXY and no_proxy must be non-empty and identical"
            )
        _assert_proxy_bypass(f"{workload} NO_PROXY", upper)

    expected_weknora = {
        "WEKNORA_ASYNQ_CORE_CONCURRENCY": "2",
        "WEKNORA_ASYNQ_SHARED_CONCURRENCY": "1",
        "WEKNORA_INTERNAL_MODEL_RELAY_KEY": "",
        "SSRF_WHITELIST_EXTRA": "deer-flow-mineru-adapter,model.ai.cnooc,gateway",
    }
    for key, expected in expected_weknora.items():
        if key == "WEKNORA_INTERNAL_MODEL_RELAY_KEY":
            if key not in {
                item.get("name")
                for item in _container(
                    _deployment(documents, "weknora-app"), "app"
                ).get("env", [])
            }:
                raise ValidationError(f"WeKnora {key} Secret reference is missing")
        elif weknora_env.get(key) != expected:
            raise ValidationError(
                f"WeKnora {key} must be {expected!r}, got {weknora_env.get(key)!r}"
            )

    weknora_no_proxy = _csv(weknora_env.get("NO_PROXY", ""))
    required_weknora_direct = {
        "gateway",
        "deer-flow-mineru-adapter",
        "model.ai.cnooc",
        ".svc",
        ".cluster.local",
    }
    missing_weknora_direct = sorted(required_weknora_direct - weknora_no_proxy)
    if missing_weknora_direct:
        raise ValidationError(
            "WeKnora NO_PROXY is missing: " + ", ".join(missing_weknora_direct)
        )
    if "10.0.0.0/8" in weknora_no_proxy:
        raise ValidationError("WeKnora must not broadly bypass the 10.0.0.0/8 network")

    if frontend_env.get("WEKNORA_FRONTEND_URL") != "/platform/knowledge-bases":
        raise ValidationError(
            "Frontend WEKNORA_FRONTEND_URL must be the same-origin path "
            "'/platform/knowledge-bases'"
        )

    defaults_job = _resource(
        documents,
        "Job",
        "weknora-enterprise-model-defaults-v5",
    )
    defaults_env = _environment(_container(defaults_job, "apply-defaults"))
    expected_defaults = {
        "DEFAULT_CHAT_MODEL_ID": "deerflow-cnooc-deepseek-v4-flash",
        "DEFAULT_EMBEDDING_MODEL_ID": "deerflow-cnooc-qwen3-embedding-8b",
        "DEFAULT_RERANK_MODEL_ID": "deerflow-cnooc-qwen3-reranker-8b",
    }
    for key, expected in expected_defaults.items():
        if defaults_env.get(key) != expected:
            raise ValidationError(
                f"Enterprise model defaults {key} must be {expected!r}, got "
                f"{defaults_env.get(key)!r}"
            )

    return [
        "Provisioner Sandbox proxy/CA injection",
        "Gateway CA and scoped TLS compatibility",
        "audited direct/proxy routing policy",
        "WeKnora concurrency and narrow SSRF policy",
        "same-origin embedded WeKnora policy",
        "internal enterprise model defaults",
    ]


def validate_baseline_inventory(path: Path, repo_root: Path) -> list[str]:
    baseline = _load_yaml(path)
    if not isinstance(baseline, dict) or baseline.get("version") != 1:
        raise ValidationError("enterprise release baseline version must be 1")

    controls = baseline.get("controls")
    if not isinstance(controls, list):
        raise ValidationError("enterprise release baseline controls are missing")
    control_ids = [control.get("id") for control in controls if isinstance(control, dict)]
    if len(control_ids) != len(set(control_ids)):
        raise ValidationError("enterprise release baseline control IDs must be unique")

    missing_controls = sorted(REQUIRED_BASELINE_CONTROLS - set(control_ids))
    if missing_controls:
        raise ValidationError(
            "enterprise release baseline is missing controls: "
            + ", ".join(missing_controls)
        )

    for control in controls:
        if not isinstance(control, dict):
            raise ValidationError("enterprise release baseline contains a non-mapping control")
        for source in control.get("source", []):
            if source == "config.yaml":
                continue
            source_path = repo_root / source
            if not source_path.exists():
                raise ValidationError(
                    f"baseline control {control.get('id')!r} references missing "
                    f"source {source!r}"
                )

    manual_ids = {
        item.get("id")
        for item in baseline.get("manual_acceptance", [])
        if isinstance(item, dict)
    }
    required_manual = {
        "sso",
        "chat",
        "knowledge",
        "external-network",
        "external-agents",
        "dashboard",
        "browser",
        "security",
        "graph",
        "parser-load",
    }
    missing_manual = sorted(required_manual - manual_ids)
    if missing_manual:
        raise ValidationError(
            "manual acceptance is missing: " + ", ".join(missing_manual)
        )

    return ["complete enterprise control inventory"]


def validate_repository(repo_root: Path) -> list[str]:
    enterprise = repo_root / "k8s" / "enterprise"
    weknora = repo_root / "k8s" / "weknora"
    local_pvc = repo_root / "k8s" / "local-pvc"

    kustomization = _load_yaml(enterprise / "kustomization.yaml")
    images = {
        image["name"]: image
        for image in kustomization.get("images", [])
        if isinstance(image, dict) and "name" in image
    }
    missing_digests = sorted(
        name
        for name in REQUIRED_THIRD_PARTY_DIGESTS
        if not str(images.get(name, {}).get("digest", "")).startswith("sha256:")
    )
    if missing_digests:
        raise ValidationError(
            "third-party images are missing immutable digests: "
            + ", ".join(missing_digests)
        )
    missing_custom = sorted(REQUIRED_CUSTOM_IMAGES - set(images))
    if missing_custom:
        raise ValidationError(
            "custom enterprise images are missing: " + ", ".join(missing_custom)
        )
    for name in REQUIRED_CUSTOM_IMAGES:
        image = images[name]
        tag = str(image.get("newTag", ""))
        if not str(image.get("newName", "")).startswith("deerflow.local/"):
            raise ValidationError(f"custom image {name} must use deerflow.local")
        if not tag or tag == "latest":
            raise ValidationError(f"custom image {name} must use an immutable tag")

    if images["wechatopenai/weknora-ui"].get("digest") != WEKNORA_UI_DIGEST:
        raise ValidationError("WeKnora UI digest does not match the reviewed release")
    if (
        images["paradedb/paradedb"].get("newTag")
        != "pg16-search-arm64-20260722-1"
    ):
        raise ValidationError("WeKnora PostgreSQL must use the audited ARM64 PG16 tag")
    if (
        images["wechatopenai/weknora-app"].get("newTag")
        != "0.7.0-enterprise-proxy1-arm64-20260723"
    ):
        raise ValidationError("WeKnora App enterprise proxy image changed unexpectedly")

    storage_documents = _load_documents(enterprise / "storage-patches.yaml")
    postgres = _resource(storage_documents, "StatefulSet", "postgres")
    claim = postgres["spec"]["volumeClaimTemplates"][0]
    if claim["spec"].get("accessModes") != ["ReadWriteOnce"]:
        raise ValidationError("PostgreSQL claim template must retain ReadWriteOnce")
    expected_volumes = {
        "deer-flow-userdata-pvc": "deerflow-home-pv-datax-01",
        "deer-flow-skills-pvc": "deerflow-skills-pv-datax-01",
        "deer-flow-postgres-backups-pvc": "deerflow-postgresql-backups-pv-datax-02",
        "weknora-postgres-data": "deerflow-weknora-postgresql-pv-datax-02",
        "weknora-data-files": "deerflow-weknora-files-pv-datax-03",
        "weknora-neo4j-data": "deerflow-weknora-neo4j-pv-datax-03",
    }
    for name, volume in expected_volumes.items():
        pvc = _resource(storage_documents, "PersistentVolumeClaim", name)
        if pvc["spec"].get("volumeName") != volume:
            raise ValidationError(f"PVC {name} must remain bound to {volume}")

    # The enterprise overlay reuses the local-PVC Deployment/Service and keeps
    # only its production overrides in a strategic-merge patch.  The separate
    # enterprise resource file now owns only the disruption budget.
    mineru_patch_documents = _load_documents(
        enterprise / "mineru-adapter-patch.yaml"
    )
    mineru = _deployment(mineru_patch_documents, "deer-flow-mineru-adapter")
    if mineru["spec"].get("replicas") != 3:
        raise ValidationError("MinerU Adapter must retain three replicas")
    rolling = mineru["spec"].get("strategy", {}).get("rollingUpdate", {})
    if rolling != {"maxUnavailable": 1, "maxSurge": 0}:
        raise ValidationError(
            "MinerU rolling update must retain maxUnavailable=1 and maxSurge=0"
        )
    required_anti_affinity = (
        mineru["spec"]["template"]["spec"]
        .get("affinity", {})
        .get("podAntiAffinity", {})
        .get("requiredDuringSchedulingIgnoredDuringExecution", [])
    )
    if not required_anti_affinity:
        raise ValidationError("MinerU Adapter requires hostname anti-affinity")
    mineru_resources = _load_documents(enterprise / "mineru-adapter.yaml")
    pdb = _resource(
        mineru_resources,
        "PodDisruptionBudget",
        "deer-flow-mineru-adapter",
    )
    if pdb["spec"].get("minAvailable") != 2:
        raise ValidationError("MinerU Adapter PDB minAvailable must be 2")

    nodeport_documents = _load_documents(enterprise / "nginx-nodeport.yaml")
    nodeport = _resource(nodeport_documents, "Service", "nginx-nodeport")
    ports = nodeport["spec"].get("ports", [])
    if len(ports) != 1 or ports[0].get("nodePort") != 32026:
        raise ValidationError("enterprise exposure must be the single NodePort 32026")

    weknora_documents = _load_documents(weknora / "weknora.yaml")
    weknora_app = _deployment(weknora_documents, "weknora-app")
    weknora_app_env = _environment(_container(weknora_app, "app"))
    expected_graph = {
        "ENABLE_GRAPH_RAG": "true",
        "NEO4J_ENABLE": "true",
        "NEO4J_URI": "bolt://weknora-neo4j:7687",
    }
    for key, expected in expected_graph.items():
        if weknora_app_env.get(key) != expected:
            raise ValidationError(
                f"WeKnora graph setting {key} must be {expected!r}"
            )
    _resource(weknora_documents, "StatefulSet", "weknora-neo4j")

    internal_models = _load_yaml(enterprise / "builtin-models-internal-only.yaml")
    model_rows = {
        model.get("id"): model
        for model in internal_models.get("builtin_models", [])
        if isinstance(model, dict)
    }
    required_models = {
        "deerflow-cnooc-deepseek-v4-flash",
        "deerflow-cnooc-qwen3-embedding-8b",
        "deerflow-cnooc-qwen3-reranker-8b",
        "deerflow-dashscope-vlm",
        "deerflow-dashscope-asr",
    }
    missing_models = sorted(required_models - set(model_rows))
    if missing_models:
        raise ValidationError(
            "enterprise built-in models are missing: " + ", ".join(missing_models)
        )
    if model_rows["deerflow-cnooc-deepseek-v4-flash"].get("is_default") is not True:
        raise ValidationError("internal DeepSeek must remain the default chat model")
    embedding_dimension = (
        model_rows["deerflow-cnooc-qwen3-embedding-8b"]
        .get("parameters", {})
        .get("embedding_parameters", {})
        .get("dimension")
    )
    if embedding_dimension != 1024:
        raise ValidationError("internal enterprise embedding dimension must remain 1024")

    nginx = (local_pvc / "nginx.conf").read_text(encoding="utf-8")
    if "proxy_pass http://weknora-frontend:80;" not in nginx:
        raise ValidationError("WeKnora UI proxy must use stable Kubernetes Service DNS")
    frontend_routes = (
        "location /api/runtime-config",
        "location ^~ /api/cnooc/external-agents/",
        "location ^~ /api/shop-cart/",
    )
    generic_api = nginx.find("location /api/ {")
    if generic_api < 0:
        raise ValidationError("generic Gateway API route is missing")
    for route in frontend_routes:
        position = nginx.find(route)
        if position < 0 or position > generic_api:
            raise ValidationError(
                f"Frontend route {route!r} must precede the generic API route"
            )

    provisioner = (repo_root / "docker" / "provisioner" / "app.py").read_text(
        encoding="utf-8"
    )
    if "automount_service_account_token=False" not in provisioner:
        raise ValidationError("Sandbox Pods must disable service-account token mounting")
    for symbol in (
        "SANDBOX_CA_SECRET_NAME",
        "SANDBOX_OPENSSL_CONFIGMAP_NAME",
        "SANDBOX_SECRET_ENV_KEYS",
        "SANDBOX_PASSTHROUGH_ENV_KEYS",
    ):
        if symbol not in provisioner:
            raise ValidationError(f"Provisioner source is missing {symbol}")

    required_source_files = (
        "backend/app/gateway/file_security.py",
        "backend/app/gateway/external_agent_analytics.py",
        "backend/packages/harness/deerflow/agents/middlewares/platform_confidentiality_middleware.py",
        "docker/mineru_adapter/app.py",
        "k8s/enterprise/weknora-mineru-defaults.sql",
        "k8s/weknora/enterprise-model-feature-defaults.sql",
    )
    missing_sources = [
        source for source in required_source_files if not (repo_root / source).is_file()
    ]
    if missing_sources:
        raise ValidationError(
            "enterprise source controls are missing: " + ", ".join(missing_sources)
        )

    return [
        "immutable ARM64 image policy",
        "static storage binding and PostgreSQL access mode",
        "three-node MinerU availability policy",
        "single NodePort exposure",
        "WeKnora graph and internal model policy",
        "same-origin Nginx route ordering",
        "Sandbox and platform security source boundaries",
    ]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate DeerFlow enterprise release proxy/CA invariants."
    )
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Release config.yaml that will create deer-flow-config.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("k8s/enterprise/runtime-patches.yaml"),
        help="Enterprise runtime patch or rendered manifest.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path("."),
        help="Repository root containing k8s/, backend/, frontend/, and docker/.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("k8s/enterprise/release-baseline.yaml"),
        help="Machine-readable enterprise control inventory.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        checks = validate_config(args.config)
        checks.extend(validate_manifest(args.manifest))
        checks.extend(validate_baseline_inventory(args.baseline, args.repo_root))
        checks.extend(validate_repository(args.repo_root))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        print(f"enterprise_release_baseline=FAIL: {exc}", file=sys.stderr)
        return 1

    for check in checks:
        print(f"PASS: {check}")
    print("enterprise_release_baseline=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

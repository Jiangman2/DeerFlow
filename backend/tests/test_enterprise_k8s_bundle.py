"""Regression coverage for the ARM64 enterprise Kubernetes bundle."""

from __future__ import annotations

import hashlib
import ssl
import tomllib
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _yaml_documents(path: str) -> list[dict]:
    return [document for document in yaml.safe_load_all(_read(path)) if isinstance(document, dict)]


def test_dashboard_rollup_job_is_suspended_and_resource_bounded_by_default():
    job = yaml.safe_load(_read("k8s/local-pvc/dashboard-rollup-cronjob.yaml"))
    assert job["kind"] == "CronJob"
    assert job["spec"]["suspend"] is True
    assert job["spec"]["concurrencyPolicy"] == "Forbid"
    assert job["spec"]["jobTemplate"]["spec"]["backoffLimit"] == 0
    assert job["spec"]["jobTemplate"]["spec"]["activeDeadlineSeconds"] == 300
    container = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
    assert container["resources"]["limits"] == {"cpu": "100m", "memory": "256Mi"}
    assert "--max-changed-days" in container["args"]
    assert "--max-backfill-days" in container["args"]
    assert "--max-changed-hours" in container["args"]
    assert "--max-backfill-hours" in container["args"]
    assert job["spec"]["schedule"] == "*/5 * * * *"
    enterprise_patches = _yaml_documents("k8s/enterprise/runtime-patches.yaml")
    enterprise_job = next(document for document in enterprise_patches if document.get("kind") == "CronJob" and document.get("metadata", {}).get("name") == "dashboard-rollup-maintenance")
    enterprise_container = enterprise_job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
    assert enterprise_container["imagePullPolicy"] == "Never"
    assert {item["name"]: item["value"] for item in enterprise_container["env"]} == {
        "DEER_FLOW_DASHBOARD_REQUIRE_READ_REPLICA": "true",
        "DEER_FLOW_DASHBOARD_VERIFY_READ_REPLICA": "true",
    }


def test_enterprise_dashboard_uses_a_verified_physical_read_replica():
    replica_documents = _yaml_documents("k8s/enterprise/dashboard-postgres-replica.yaml")
    replica = next(document for document in replica_documents if document.get("kind") == "StatefulSet" and document.get("metadata", {}).get("name") == "postgres-dashboard-replica")
    assert replica["spec"]["template"]["spec"]["nodeSelector"] == {"kubernetes.io/hostname": "datax-03"}
    container = replica["spec"]["template"]["spec"]["containers"][0]
    assert "default_transaction_read_only=on" in container["args"]
    assert "pg_is_in_recovery()" in container["readinessProbe"]["exec"]["command"][-1]
    init_script = replica["spec"]["template"]["spec"]["initContainers"][0]["command"][-1]
    assert 'REPLICATION_USER" != "deerflow_dashboard_replica' in init_script
    assert "--checkpoint=spread --max-rate=32M" in init_script
    volume = next(document for document in replica_documents if document.get("kind") == "PersistentVolume")
    assert volume["spec"]["local"]["path"] == "/opt/deerflow-storage/dashboard-postgresql"
    assert volume["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"] == ["datax-03"]

    patches = _yaml_documents("k8s/enterprise/runtime-patches.yaml")
    gateway = next(document for document in patches if document.get("kind") == "Deployment" and document.get("metadata", {}).get("name") == "gateway")
    env = {entry["name"]: entry for entry in gateway["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["DEER_FLOW_DASHBOARD_REQUIRE_READ_REPLICA"]["value"] == "true"
    assert env["DEER_FLOW_DASHBOARD_VERIFY_READ_REPLICA"]["value"] == "true"
    assert env["DEER_FLOW_DASHBOARD_ROLLUPS_ENABLED"]["value"] == "true"
    assert env["DEER_FLOW_DASHBOARD_REQUIRE_HOURLY_ROLLUPS"]["value"] == "true"
    assert env["DEER_FLOW_DASHBOARD_DATABASE_URL"]["valueFrom"]["secretKeyRef"] == {
        "name": "deer-flow-dashboard-postgres",
        "key": "DATABASE_URL",
        "optional": True,
    }

    primary = next(document for document in patches if document.get("kind") == "StatefulSet" and document.get("metadata", {}).get("name") == "postgres")
    primary_args = primary["spec"]["template"]["spec"]["containers"][0]["args"]
    assert "wal_level=replica" in primary_args
    assert "max_slot_wal_keep_size=4GB" in primary_args


def test_weknora_docreader_mounts_narrow_parser_compatibility_shims():
    kustomization = yaml.safe_load(_read("k8s/weknora/kustomization.yaml"))
    generators = {entry["name"]: entry for entry in kustomization["configMapGenerator"]}
    assert generators["weknora-docreader-xlsx-compat"]["files"] == [
        "sitecustomize.py=docreader-compat/sitecustomize.py",
        "web_compat.py=docreader-compat/web_compat.py",
        "xlsx_compat.py=docreader-compat/xlsx_compat.py",
    ]

    documents = _yaml_documents("k8s/weknora/weknora.yaml")
    deployment = next(document for document in documents if document.get("kind") == "Deployment" and document["metadata"]["name"] == "weknora-docreader")
    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]

    assert {entry["name"]: entry["value"] for entry in container["env"]}["PYTHONPATH"] == "/opt/deerflow-compat"
    assert {(mount["name"], mount["mountPath"], mount.get("readOnly")) for mount in container["volumeMounts"]} >= {("xlsx-compat", "/opt/deerflow-compat", True)}
    assert any(volume["name"] == "xlsx-compat" and volume["configMap"]["name"] == "weknora-docreader-xlsx-compat" for volume in pod_spec["volumes"])

    compat = _read("k8s/weknora/docreader-compat/xlsx_compat.py")
    assert 'kwargs.pop("defaultColWidthPt", None)' in compat
    assert "for key in" not in compat
    sitecustomize = _read("k8s/weknora/docreader-compat/sitecustomize.py")
    assert "install_docreader_web_compat_when_available()" in sitecustomize


def test_enterprise_build_base_images_are_overridable():
    expected = {
        "backend/Dockerfile": (
            "ARG UV_IMAGE=",
            "ARG PYTHON_IMAGE=",
            "ARG DOCKER_CLI_IMAGE=",
            "FROM ${PYTHON_IMAGE}",
            "FROM ${DOCKER_CLI_IMAGE} AS docker-cli-source",
        ),
        "frontend/Dockerfile": ("ARG NODE_IMAGE=", "FROM ${NODE_IMAGE}"),
        "docker/provisioner/Dockerfile": (
            "ARG PYTHON_IMAGE=",
            "FROM ${PYTHON_IMAGE}",
        ),
        "docker/k8s-skills/Dockerfile": (
            "ARG ALPINE_IMAGE=",
            "FROM ${ALPINE_IMAGE}",
        ),
        "docker/sandbox-ppt-master/Dockerfile": (
            "ARG SANDBOX_BASE_IMAGE=",
            "FROM ${SANDBOX_BASE_IMAGE}",
        ),
        "docker/mineru_adapter/Dockerfile": (
            "ARG DOCREADER_IMAGE=",
            "FROM ${DOCREADER_IMAGE}",
        ),
    }

    for path, tokens in expected.items():
        dockerfile = _read(path)
        for token in tokens:
            assert token in dockerfile, f"{path} is missing {token}"


def test_gateway_image_bundles_verified_cl100k_base_cache():
    dockerfile = _read("backend/Dockerfile")
    cache_key = "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
    expected_sha256 = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7"
    cache_file = REPO_ROOT / "backend" / "assets" / "tiktoken-cache" / cache_key

    assert cache_file.is_file()
    assert hashlib.sha256(cache_file.read_bytes()).hexdigest() == expected_sha256
    assert "ENV TIKTOKEN_CACHE_DIR=/opt/deerflow/tiktoken-cache" in dockerfile
    assert f"COPY backend/assets/tiktoken-cache/{cache_key} /opt/deerflow/tiktoken-cache/{cache_key}" in dockerfile
    assert expected_sha256 in dockerfile


def test_enterprise_bundle_pins_official_amazon_m01_intermediate():
    certificate = REPO_ROOT / "k8s" / "enterprise" / "certs" / "Amazon-RSA-2048-M01.pem"

    assert certificate.is_file()
    assert hashlib.sha256(certificate.read_bytes()).hexdigest() == "1ae9a3eeba5a89627edc2760adea318c9c6375b54d71587ae0450632a2eac6ed"


def test_environment_template_documents_both_kubernetes_sandbox_auth_keys():
    env_example = _read(".env.example")
    secrets_readme = _read("k8s/local-pvc/secrets/README.md")

    for key in ("PROVISIONER_API_KEY", "SANDBOX_API_KEY"):
        assert f"# {key}=your-" in env_example
        assert key in secrets_readme

    assert "Do not reuse one value for both boundaries." in secrets_readme


def test_enterprise_sandbox_image_pins_arm64_websocat_and_cairosvg():
    dockerfile = _read("docker/sandbox-ppt-master/Dockerfile")

    assert "WEBSOCAT_VERSION=1.14.1" in dockerfile
    assert "711a69576a2ff473fb01a90ffafb571c2ed019e55479d7ae71b12c2eadeb7011" in dockerfile
    assert "websocat --version" in dockerfile
    assert '"CairoSVG==2.7.1"' in dockerfile
    assert "cairosvg.svg2png" in dockerfile
    assert '--upgrade --force-reinstall "pydantic' not in dockerfile
    assert "Version(pydantic.__version__) >= Version('2.12.5')" in dockerfile


def test_enterprise_sandbox_outer_nginx_allows_bounded_long_commands():
    dockerfile = _read("docker/sandbox-ppt-master/Dockerfile")

    assert "nginx-server-with-auth.conf" in dockerfile
    assert "grep -cF 'proxy_pass http://127.0.0.1:${AUTH_BACKEND_PORT};'" in dockerfile
    assert "proxy_read_timeout 86400s;" in dockerfile
    assert "proxy_send_timeout 86400s;" in dockerfile


def test_ppt_master_runner_is_an_independent_failure_domain():
    documents = _yaml_documents("k8s/local-pvc/deer-flow-pvc-stack.yaml")
    gateway_deployment = next(document for document in documents if document.get("kind") == "Deployment" and document.get("metadata", {}).get("name") == "gateway")
    runner_deployment = next(document for document in documents if document.get("kind") == "Deployment" and document.get("metadata", {}).get("name") == "ppt-master-runner")
    gateway_containers = {container["name"]: container for container in gateway_deployment["spec"]["template"]["spec"]["containers"]}
    runner_containers = {container["name"]: container for container in runner_deployment["spec"]["template"]["spec"]["containers"]}
    runner = runner_containers["ppt-master-runner"]
    runner_env = {entry["name"]: entry.get("value") for entry in runner["env"] if "value" in entry}
    gateway_env = {entry["name"]: entry.get("value") for entry in gateway_containers["gateway"]["env"] if "value" in entry}

    assert set(gateway_containers) == {"gateway"}
    assert gateway_containers["gateway"]["image"].startswith("deer-flow-gateway:")
    assert runner["image"].startswith("deer-flow-ppt-master-runner:")
    assert runner["image"].partition(":")[2] == gateway_containers["gateway"]["image"].partition(":")[2]
    assert runner["ports"] == [{"containerPort": 8003}]
    assert runner_env["GATEWAY_PORT"] == "8003"
    assert runner_env["DEER_FLOW_RUNTIME_PROFILE"] == "ppt-master-runner"
    assert gateway_env["PPT_MASTER_RUNNER_INTERNAL_URL"] == "http://ppt-master-runner:8003"
    assert "ppt-master-runner" in gateway_env["NO_PROXY"].split(",")
    assert runner_env["HTTP_PROXY"] == ""
    assert "ppt-master-runner" in runner_env["NO_PROXY"].split(",")
    assert {mount["name"] for mount in runner["volumeMounts"]} == {
        "config",
        "extensions-config",
        "userdata",
        "skills",
    }
    runner_spec = runner_deployment["spec"]["template"]["spec"]
    assert runner_spec["automountServiceAccountToken"] is False
    assert runner_deployment["spec"]["strategy"]["rollingUpdate"] == {
        "maxUnavailable": 0,
        "maxSurge": 1,
    }
    init_containers = {container["name"]: container for container in runner_spec["initContainers"]}
    database_guard = init_containers["wait-for-postgres"]
    assert database_guard["image"].startswith("deer-flow-ppt-master-runner:")
    database_guard_script = database_guard["args"][0]
    assert "SELECT 1" in database_guard_script
    assert "consecutive == 3" in database_guard_script
    assert "time.monotonic() + 300" in database_guard_script

    gateway_service = next(document for document in documents if document.get("kind") == "Service" and document.get("metadata", {}).get("name") == "gateway")
    runner_service = next(document for document in documents if document.get("kind") == "Service" and document.get("metadata", {}).get("name") == "ppt-master-runner")
    assert gateway_service["spec"]["ports"] == [{"name": "http", "port": 8001, "targetPort": 8001}]
    assert runner_service["spec"]["selector"] == {"app": "ppt-master-runner"}
    assert runner_service["spec"]["ports"] == [{
        "name": "http",
        "port": 8003,
        "targetPort": 8003,
    }]

    nginx = _read("k8s/local-pvc/nginx.conf")
    assert "location /api/ppt-master-runtime/" in nginx
    assert "rewrite ^/api/ppt-master-runtime/(.*) /api/$1 break;" in nginx
    assert "proxy_pass http://$ppt_master_runner_upstream;" in nginx
    stack = _read("k8s/local-pvc/deer-flow-pvc-stack.yaml")
    assert "proxy_pass http://ppt-master-runner:8003;" in stack


def test_enterprise_ppt_runner_retains_proxy_and_ca_boundary():
    patches = _yaml_documents("k8s/enterprise/runtime-patches.yaml")
    runner_patch = next(patch for patch in patches if patch.get("kind") == "Deployment" and patch.get("metadata", {}).get("name") == "ppt-master-runner")
    runner = next(container for container in runner_patch["spec"]["template"]["spec"]["containers"] if container["name"] == "ppt-master-runner")
    env = {entry["name"]: entry.get("value") for entry in runner["env"] if "value" in entry}

    assert env["SSL_CERT_FILE"] == "/etc/deerflow-ca/ca-bundle.crt"
    assert env["OPENSSL_CONF"] == "/etc/deerflow-openssl/openssl.cnf"
    assert env["TAVILY_TLS_COMPAT"] == "true"
    assert "ppt-master-runner" in env["NO_PROXY"].split(",")
    assert {mount["name"] for mount in runner["volumeMounts"]} == {
        "enterprise-ca",
        "openssl-compat",
    }

    gateway_patch = next(patch for patch in patches if patch.get("kind") == "Deployment" and patch.get("metadata", {}).get("name") == "gateway")
    gateway = next(container for container in gateway_patch["spec"]["template"]["spec"]["containers"] if container["name"] == "gateway")
    assert gateway["resources"] == {
        "requests": {"cpu": "500m", "memory": "4Gi"},
        "limits": {"cpu": "2", "memory": "16Gi"},
    }
    assert gateway_patch["spec"]["strategy"]["rollingUpdate"] == {
        "maxUnavailable": 0,
        "maxSurge": 1,
    }


def test_enterprise_ppt_agent_disables_provider_hosted_web_search():
    patches = _yaml_documents("k8s/enterprise/runtime-patches.yaml")
    provisioner = next(patch for patch in patches if patch.get("kind") == "Deployment" and patch.get("metadata", {}).get("name") == "provisioner")
    env = {entry["name"]: entry.get("value") for entry in provisioner["spec"]["template"]["spec"]["containers"][0]["env"] if "value" in entry}

    codex_config = tomllib.loads(env["CODEX_CONFIG_TOML"])

    assert codex_config["web_search"] == "disabled"
    assert codex_config["model_provider"] == "cnooc_deepseek"
    assert codex_config["model_providers"]["cnooc_deepseek"]["wire_api"] == "responses"


def test_networked_enterprise_builds_use_optional_buildkit_secrets():
    networked_dockerfiles = (
        "backend/Dockerfile",
        "frontend/Dockerfile",
        "docker/provisioner/Dockerfile",
        "docker/sandbox-ppt-master/Dockerfile",
    )

    for path in networked_dockerfiles:
        dockerfile = _read(path)
        assert "id=enterprise_proxy,required=false" in dockerfile
        assert "id=enterprise_ca,required=false" in dockerfile

    backend = _read("backend/Dockerfile")
    provisioner = _read("docker/provisioner/Dockerfile")
    sandbox = _read("docker/sandbox-ppt-master/Dockerfile")
    for dockerfile in (backend, provisioner, sandbox):
        assert "SSL_CERT_FILE=/etc/ssl/certs/deerflow-enterprise-ca-bundle.crt" in dockerfile
        assert ". /run/secrets/enterprise_proxy" in dockerfile


def test_enterprise_runtime_images_are_immutable():
    kustomization = yaml.safe_load(_read("k8s/enterprise/kustomization.yaml"))
    images = kustomization["images"]
    custom = [image for image in images if image["newName"].startswith("deerflow.local/")]
    third_party = [image for image in images if image not in custom]

    assert third_party
    assert custom
    assert all(image.get("digest", "").startswith("sha256:") for image in third_party)
    expected_tags = {
        "deer-flow-gateway": "20260914-enterprise-arm64-rc15-digitalemployee-promptfix1",
        "deer-flow-ppt-master-runner": "20260907-enterprise-arm64-rc15-skillhubux-dashboard1-r2",
        "deer-flow-frontend": "20260914-enterprise-arm64-rc15-digitalemployee-ui2-r1",
        "deer-flow-provisioner": "20260817-enterprise-arm64-rc15-sandboxstorage1",
        "deer-flow-skills": "20260901-enterprise-arm64-rc15-researchfund1",
        "deer-flow-mineru-adapter": "20260904-enterprise-arm64-rc15-uploadstability1",
        "paradedb/paradedb": "pg16-search-arm64-20260722-1",
        "wechatopenai/weknora-app": "0.8.0-enterprise-proxy1-anydoc1-arm64-20260912",
        "wechatopenai/weknora-ui": "0.8.0-enterprise-sso2-r5-arm64-20260913",
    }
    assert {image["name"]: image.get("newTag") for image in custom} == expected_tags
    assert "latest" not in _read("k8s/enterprise/kustomization.yaml")


def test_enterprise_weknora_ui_replaces_the_official_v070_base_image():
    expected_digest = "sha256:401604e79a5717dd966a13383d775f15d3125481d3e080eba329e4a0fbcf9523"
    base_documents = _yaml_documents("k8s/weknora/weknora.yaml")
    frontend = next(document for document in base_documents if document.get("kind") == "Deployment" and document.get("metadata", {}).get("name") == "weknora-frontend")
    base_image = frontend["spec"]["template"]["spec"]["containers"][0]["image"]
    assert base_image == f"wechatopenai/weknora-ui@{expected_digest}"

    enterprise = yaml.safe_load(_read("k8s/enterprise/kustomization.yaml"))
    override = next(image for image in enterprise["images"] if image["name"] == "wechatopenai/weknora-ui")
    assert override == {
        "name": "wechatopenai/weknora-ui",
        "newName": "deerflow.local/weknora-ui",
        "newTag": "0.8.0-enterprise-sso2-r5-arm64-20260913",
    }

    v08_patches = _yaml_documents("k8s/enterprise/weknora-v08-runtime-patches.yaml")
    v08_frontend = next(document for document in v08_patches if document.get("kind") == "Deployment" and document.get("metadata", {}).get("name") == "weknora-frontend")
    pod_spec = v08_frontend["spec"]["template"]["spec"]
    container = next(item for item in pod_spec["containers"] if item["name"] == "frontend")
    assert container["imagePullPolicy"] == "Never"
    assert container["volumeMounts"] is None
    assert pod_spec["volumes"] is None

    patched_index = _read("k8s/weknora/ui-patches/index.html")
    for asset in (
        "main-BUSZvhEQ.js",
        "main-DM_WFx4W.css",
        "security-DPPZJOiS.js",
        "settings-Ck3CxLjU.js",
        "index-itUW58et.js",
        "tdesign-icon-offline-D7rAiOht.js",
        "vendor-highlight-Cz3B6uZb.js",
        "vendor-mermaid-Bkyya5d_.js",
        "vendor-markdown-CxA3LRJ6.js",
    ):
        assert f"/assets/{asset}" in patched_index

    for stale_asset in (
        "main-pzt9Cxn1.js",
        "security-DMhmaURM.js",
        "settings-CwixRhv-.js",
        "index-DeX5ZUuT.js",
    ):
        assert stale_asset not in patched_index

    patch_kustomization = yaml.safe_load(_read("k8s/weknora/kustomization.yaml"))
    ui_patch = next(generator for generator in patch_kustomization["configMapGenerator"] if generator["name"] == "weknora-ui-patch")
    assert "index-itUW58et.js=ui-patches/index-itUW58et.js" in ui_patch["files"]
    assert not any("Login-JaWLYwhw.js" in item for item in ui_patch["files"])

    auth_adapter = _read("k8s/weknora/ui-patches/index-itUW58et.js")
    for dependency in (
        "tdesign-icon-offline-D7rAiOht.js",
        "vendor-highlight-Cz3B6uZb.js",
        "vendor-mermaid-Bkyya5d_.js",
    ):
        assert dependency in auth_adapter


def test_enterprise_weknora_postgres_uses_verified_arm64_image():
    kustomization = yaml.safe_load(_read("k8s/enterprise/kustomization.yaml"))
    postgres_image = next(image for image in kustomization["images"] if image["name"] == "paradedb/paradedb")

    assert postgres_image == {
        "name": "paradedb/paradedb",
        "newName": "deerflow.local/weknora-postgres",
        "newTag": "pg16-search-arm64-20260722-1",
    }

    patches = _yaml_documents("k8s/enterprise/runtime-patches.yaml")
    postgres_patch = next(patch for patch in patches if patch.get("kind") == "StatefulSet" and patch.get("metadata", {}).get("name") == "weknora-postgres")
    container = postgres_patch["spec"]["template"]["spec"]["containers"][0]
    env = {entry["name"]: entry["value"] for entry in container["env"] if "value" in entry}

    assert container["name"] == "postgres"
    assert container["imagePullPolicy"] == "Never"
    assert container["args"] == ["-c", "shared_preload_libraries=pg_search"]
    assert env["PGDATA"] == "/var/lib/postgresql/data/pgdata"


def test_enterprise_frontend_uses_same_origin_weknora_url():
    patches = _yaml_documents("k8s/enterprise/runtime-patches.yaml")
    frontend_patch = next(patch for patch in patches if patch.get("kind") == "Deployment" and patch.get("metadata", {}).get("name") == "frontend")
    container = frontend_patch["spec"]["template"]["spec"]["containers"][0]
    env = {entry["name"]: entry["value"] for entry in container["env"] if "value" in entry}

    assert env["WEKNORA_FRONTEND_URL"] == "/platform/knowledge-bases"
    assert env["DEER_FLOW_UNAUTHENTICATED_HOME_URL"] == "https://datalake.cnooc/#/home"
    assert env["DEER_FLOW_SSO_ONLY"] == "true"


def test_enterprise_weknora_app_trusts_enterprise_ca_bundle():
    patches = _yaml_documents("k8s/enterprise/runtime-patches.yaml")
    app_patch = next(patch for patch in patches if patch.get("kind") == "Deployment" and patch.get("metadata", {}).get("name") == "weknora-app")
    pod_spec = app_patch["spec"]["template"]["spec"]
    container = next(container for container in pod_spec["containers"] if container["name"] == "app")
    env = {entry["name"]: entry["value"] for entry in container["env"] if "value" in entry}
    mounts = container["volumeMounts"]
    volumes = {volume["name"]: volume for volume in pod_spec["volumes"]}

    ca_path = "/etc/deerflow-ca/ca-bundle.crt"
    assert env["SSL_CERT_FILE"] == ca_path
    assert env["REQUESTS_CA_BUNDLE"] == ca_path
    assert env["CURL_CA_BUNDLE"] == ca_path
    assert {
        "name": "enterprise-ca",
        "mountPath": "/etc/deerflow-ca",
        "readOnly": True,
    } in mounts
    assert not any(mount.get("mountPath") == "/etc/ssl/certs/ca-certificates.crt" for mount in mounts)
    assert volumes["enterprise-ca"] == {
        "name": "enterprise-ca",
        "secret": {
            "secretName": "deer-flow-enterprise-ca",
            "items": [{"key": "ca-bundle.crt", "path": "ca-bundle.crt"}],
        },
    }


def test_enterprise_gateway_scopes_legacy_proxy_tls_compatibility_to_its_pod():
    kustomization = yaml.safe_load(_read("k8s/enterprise/kustomization.yaml"))
    assert "gateway-openssl-compat.yaml" in kustomization["resources"]

    config_documents = _yaml_documents("k8s/enterprise/gateway-openssl-compat.yaml")
    assert len(config_documents) == 1
    config_map = config_documents[0]
    assert config_map["kind"] == "ConfigMap"
    assert config_map["metadata"]["name"] == "deer-flow-gateway-openssl-compat"
    openssl_config = config_map["data"]["openssl.cnf"]
    assert "CipherString = DEFAULT:@SECLEVEL=1" in openssl_config
    assert "verify = false" not in openssl_config.lower()
    assert "insecure" not in openssl_config.lower()

    patches = _yaml_documents("k8s/enterprise/runtime-patches.yaml")
    gateway = next(patch for patch in patches if patch.get("kind") == "Deployment" and patch.get("metadata", {}).get("name") == "gateway")
    pod_spec = gateway["spec"]["template"]["spec"]
    container = next(container for container in pod_spec["containers"] if container["name"] == "gateway")
    env = {entry["name"]: entry["value"] for entry in container["env"] if "value" in entry}
    ca_path = "/etc/deerflow-ca/ca-bundle.crt"
    assert env["SSL_CERT_FILE"] == ca_path
    assert env["REQUESTS_CA_BUNDLE"] == ca_path
    assert env["CURL_CA_BUNDLE"] == ca_path
    assert env["OPENSSL_CONF"] == "/etc/deerflow-openssl/openssl.cnf"
    assert env["WEKNORA_BAILIAN_TLS_COMPAT"] == "true"
    assert env["TAVILY_TLS_COMPAT"] == "true"
    assert {
        "name": "enterprise-ca",
        "mountPath": "/etc/deerflow-ca",
        "readOnly": True,
    } in container["volumeMounts"]
    assert {
        "name": "openssl-compat",
        "mountPath": "/etc/deerflow-openssl",
        "readOnly": True,
    } in container["volumeMounts"]
    assert {
        "name": "enterprise-ca",
        "secret": {
            "secretName": "deer-flow-enterprise-ca",
            "items": [{"key": "ca-bundle.crt", "path": "ca-bundle.crt"}],
        },
    } in pod_spec["volumes"]
    assert {
        "name": "openssl-compat",
        "configMap": {
            "name": "deer-flow-gateway-openssl-compat",
            "items": [{"key": "openssl.cnf", "path": "openssl.cnf"}],
        },
    } in pod_spec["volumes"]


def test_enterprise_mineru_adapter_uses_secret_and_internal_service_only():
    patch_documents = _yaml_documents("k8s/enterprise/mineru-adapter-patch.yaml")
    deployment = patch_documents[0]
    service_patch = patch_documents[1]
    service = next(document for document in _yaml_documents("k8s/local-pvc/mineru-adapter-local.yaml") if document["kind"] == "Service")
    enterprise_resources = _yaml_documents("k8s/enterprise/mineru-adapter.yaml")
    disruption_budget = enterprise_resources[0]
    worker_service = next(document for document in enterprise_resources if document.get("kind") == "Service")
    dispatcher = next(document for document in enterprise_resources if document.get("kind") == "Deployment")
    dispatcher_pdb = enterprise_resources[-1]
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item["value"] for item in container["env"] if "name" in item}
    pod_spec = deployment["spec"]["template"]["spec"]

    assert deployment["metadata"]["name"] == "deer-flow-mineru-adapter"
    assert deployment["spec"]["replicas"] == 6
    assert deployment["spec"]["strategy"] == {
        "type": "RollingUpdate",
        "rollingUpdate": {"maxUnavailable": 1, "maxSurge": 1},
    }
    assert pod_spec["topologySpreadConstraints"] == [
        {
            "topologyKey": "kubernetes.io/hostname",
            "maxSkew": 1,
            "whenUnsatisfiable": "DoNotSchedule",
            "labelSelector": {
                "matchLabels": {
                    "app": "deer-flow-mineru-adapter",
                    "deerflow.cnooc/mineru-role": "worker",
                }
            },
        }
    ]
    assert pod_spec["affinity"]["podAntiAffinity"]["preferredDuringSchedulingIgnoredDuringExecution"][0]["weight"] == 100
    assert deployment["spec"]["template"]["metadata"]["labels"]["deerflow.cnooc/mineru-role"] == "worker"
    assert container["envFrom"] == [{"secretRef": {"name": "deer-flow-mineru"}}]
    assert container["imagePullPolicy"] == "Never"
    assert container["resources"]["requests"] == {"cpu": "500m", "memory": "1Gi"}
    assert container["resources"]["limits"] == {"cpu": "2", "memory": "4Gi"}
    assert container["readinessProbe"] == {
        "httpGet": {"path": "/live", "port": "http"},
        "initialDelaySeconds": 3,
        "periodSeconds": 5,
        "timeoutSeconds": 5,
        "failureThreshold": 12,
    }
    assert container["livenessProbe"] == {
        "httpGet": {"path": "/live", "port": "http"},
        "initialDelaySeconds": 15,
        "periodSeconds": 15,
        "timeoutSeconds": 10,
        "failureThreshold": 6,
    }
    assert service["metadata"]["name"] == "deer-flow-mineru-adapter"
    assert service["spec"]["ports"][0]["port"] == 50052
    assert service_patch["spec"]["selector"] == {"app": "deer-flow-mineru-dispatcher"}
    assert worker_service["metadata"]["name"] == "deer-flow-mineru-adapter-workers"
    assert worker_service["spec"]["clusterIP"] == "None"
    assert dispatcher["metadata"]["name"] == "deer-flow-mineru-dispatcher"
    assert dispatcher["spec"]["strategy"] == {"type": "Recreate"}
    dispatcher_env = {item["name"]: item["value"] for item in dispatcher["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert dispatcher_env["MINERU_ADAPTER_MODE"] == "dispatcher"
    assert dispatcher_env["MINERU_DISPATCH_BACKEND_HOST"] == "deer-flow-mineru-adapter-workers"
    assert dispatcher_env["MINERU_DISPATCH_FAILURE_COOLDOWN_SECONDS"] == "15"
    assert dispatcher_env["MINERU_DISPATCH_PROBE_TIMEOUT_SECONDS"] == "0.25"
    assert dispatcher_env["MINERU_DISPATCH_BUSY_RETRY_LIMIT"] == "3"
    dispatcher_container = dispatcher["spec"]["template"]["spec"]["containers"][0]
    assert dispatcher_container["readinessProbe"]["httpGet"]["path"] == "/ready"
    assert dispatcher_container["livenessProbe"]["httpGet"]["path"] == "/live"
    assert all(
        document["kind"] != "Secret"
        for path in (
            "k8s/enterprise/mineru-adapter.yaml",
            "k8s/enterprise/mineru-adapter-patch.yaml",
            "k8s/local-pvc/mineru-adapter-local.yaml",
        )
        for document in _yaml_documents(path)
    )
    assert env["REQUESTS_CA_BUNDLE"] == "/etc/deerflow-ca/ca-bundle.crt"
    assert container["volumeMounts"] == [{"name": "enterprise-ca", "mountPath": "/etc/deerflow-ca", "readOnly": True}]
    assert disruption_budget["metadata"]["name"] == "deer-flow-mineru-adapter"
    assert disruption_budget["spec"] == {
        "minAvailable": 4,
        "selector": {"matchLabels": {"app": "deer-flow-mineru-adapter", "deerflow.cnooc/mineru-role": "worker"}},
    }
    assert dispatcher_pdb["metadata"]["name"] == "deer-flow-mineru-dispatcher"
    assert dispatcher_pdb["spec"]["minAvailable"] == 1


def test_enterprise_routes_presentations_and_scanned_pdf_fallback_through_mineru_adapter():
    patches = _yaml_documents("k8s/enterprise/runtime-patches.yaml")
    gateway = next(patch for patch in patches if patch.get("kind") == "Deployment" and patch.get("metadata", {}).get("name") == "gateway")
    gateway_env = {item["name"]: item["value"] for item in gateway["spec"]["template"]["spec"]["containers"][0]["env"] if "value" in item}
    assert gateway_env["DEER_FLOW_SSO_ONLY"] == "true"
    assert gateway_env["WEKNORA_PRESENTATION_PARSER_ENGINE"] == "mineru"
    assert gateway_env["WEKNORA_MINERU_ENDPOINT"] == "http://deer-flow-mineru-adapter:50052"
    assert gateway_env["WEKNORA_MINERU_AUTO_CONFIGURE"] == "true"
    assert gateway_env["WEKNORA_MINERU_SCANNED_PDF_FALLBACK"] == "true"
    assert gateway_env["WEKNORA_MINERU_DOCUMENT_VISUAL_FALLBACK"] == "true"
    assert gateway_env["DEERFLOW_UPLOAD_DOCUMENT_PARSER_ENABLED"] == "true"
    assert gateway_env["DEERFLOW_UPLOAD_DOCUMENT_PARSE_TIMEOUT_SECONDS"] == "1860"
    assert gateway_env["DEERFLOW_UPLOAD_DOCUMENT_PARSE_MAX_CONNECTIONS"] == "16"
    assert gateway_env["DEERFLOW_UPLOAD_DOCUMENT_PARSE_MAX_KEEPALIVE_CONNECTIONS"] == "8"
    assert gateway_env["DEERFLOW_UPLOAD_DOCUMENT_FALLBACK_ENABLED"] == "true"
    assert gateway_env["DEERFLOW_UPLOAD_PDF_SECURITY_MODE"] == "sanitize"
    assert gateway_env["DEERFLOW_UPLOAD_PDF_SANITIZE_DPI"] == "120"
    assert gateway_env["DEERFLOW_UPLOAD_PDF_SANITIZE_MAX_PAGES"] == "200"
    assert "model.ai.cnooc" in gateway_env["NO_PROXY"].split(",")
    assert "model.ai.cnooc" in gateway_env["no_proxy"].split(",")

    weknora = next(patch for patch in patches if patch.get("kind") == "Deployment" and patch.get("metadata", {}).get("name") == "weknora-app")
    weknora_container = weknora["spec"]["template"]["spec"]["containers"][0]
    weknora_env = {item["name"]: item["value"] for item in weknora_container["env"] if "value" in item}
    assert weknora_env["SSRF_WHITELIST_EXTRA"].split(",") == [
        "deer-flow-mineru-adapter",
        "model.ai.cnooc",
        "gateway",
    ]
    assert "deer-flow-mineru-adapter" in weknora_env["NO_PROXY"].split(",")
    assert "deer-flow-mineru-adapter" in weknora_env["no_proxy"].split(",")
    assert "model.ai.cnooc" in weknora_env["NO_PROXY"].split(",")
    assert "model.ai.cnooc" in weknora_env["no_proxy"].split(",")
    assert weknora_env["WEKNORA_PREFER_CNOOC_DEEPSEEK"] == "true"
    assert weknora_env["WEKNORA_ASYNQ_CORE_CONCURRENCY"] == "4"
    assert weknora_env["WEKNORA_ASYNQ_POSTPROCESS_CONCURRENCY"] == "2"
    assert weknora_env["WEKNORA_ASYNQ_ENRICHMENT_CONCURRENCY"] == "12"
    assert weknora_env["WEKNORA_ASYNQ_MAINTENANCE_CONCURRENCY"] == "4"
    assert weknora_env["WEKNORA_ASYNQ_SHARED_CONCURRENCY"] == "2"
    assert weknora_env["WEKNORA_DOCREADER_CALL_TIMEOUT"] == "30m"
    assert weknora_env["WEKNORA_DOCUMENT_PROCESS_TIMEOUT"] == "2h"
    assert weknora_env["SSL_CERT_FILE"] == "/etc/deerflow-ca/ca-bundle.crt"
    assert {
        "name": "enterprise-ca",
        "mountPath": "/etc/deerflow-ca",
        "readOnly": True,
    } in weknora_container["volumeMounts"]

    adapter = _yaml_documents("k8s/enterprise/mineru-adapter-patch.yaml")[0]
    adapter_env = {item["name"]: item["value"] for item in adapter["spec"]["template"]["spec"]["containers"][0]["env"] if "name" in item}
    assert adapter_env["PDF_SCAN_IMAGE_RATIO"] == "0.5"
    assert adapter_env["PDF_SCAN_MIN_CHARS_PER_PAGE"] == "10"
    assert adapter_env["PDF_SCAN_LOW_TEXT_IMAGE_RATIO"] == "0.1"
    assert adapter_env["MINERU_MAX_INLINE_IMAGE_BYTES"] == "20971520"
    assert adapter_env["MINERU_SOURCE_PAGE_DPI"] == "110"
    assert adapter_env["MINERU_SOURCE_PAGE_JPEG_QUALITY"] == "82"
    assert adapter_env["MINERU_MAX_SOURCE_PAGE_IMAGES"] == "200"
    assert adapter_env["MINERU_MULTILINGUAL_FIGURE_CAPTURE"] == "true"
    assert adapter_env["MINERU_VISUAL_PAGE_MIN_IMAGE_RATIO"] == "0.01"
    assert adapter_env["MINERU_VISUAL_PAGE_MIN_VECTOR_OBJECTS"] == "12"
    assert adapter_env["MINERU_VISUAL_PAGE_MIN_VECTOR_AREA_RATIO"] == "0.05"
    assert adapter_env["MINERU_MAX_SEARCHABLE_VISUAL_PAGES"] == "80"
    assert adapter_env["MINERU_REQUEST_MODE"] == "async"
    assert adapter_env["MINERU_TIMEOUT_SECONDS"] == "1800"
    assert adapter_env["MINERU_POLL_INTERVAL_SECONDS"] == "3"
    assert adapter_env["MINERU_TRANSIENT_POLL_RETRIES"] == "20"
    assert adapter_env["MINERU_SYNC_MAX_PAGES_PER_REQUEST"] == "20"
    assert adapter_env["MINERU_SYNC_TRANSIENT_RETRIES"] == "2"
    assert adapter_env["MINERU_SYNC_RETRY_DELAY_SECONDS"] == "2"
    assert adapter_env["MINERU_SYNC_RETRY_MAX_ELAPSED_SECONDS"] == "600"
    assert adapter_env["MINERU_TRUST_ENV"] == "false"
    assert adapter_env["MINERU_WORD_PDF_VISUAL_SUPPLEMENT_ENABLED"] == "false"


def test_enterprise_routes_internal_retrieval_models_through_gateway():
    patches = _yaml_documents("k8s/enterprise/runtime-patches.yaml")
    gateway = next(patch for patch in patches if patch.get("kind") == "Deployment" and patch.get("metadata", {}).get("name") == "gateway")
    weknora = next(patch for patch in patches if patch.get("kind") == "Deployment" and patch.get("metadata", {}).get("name") == "weknora-app")
    gateway_entries = {entry["name"]: entry for entry in gateway["spec"]["template"]["spec"]["containers"][0]["env"]}
    weknora_entries = {entry["name"]: entry for entry in weknora["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert gateway_entries["WEKNORA_ENTERPRISE_INTERNAL_MODELS_ONLY"]["value"] == "true"
    assert gateway_entries["WEKNORA_DEFAULT_EMBEDDING_MODEL_ID"]["value"] == "deerflow-cnooc-qwen3-embedding-8b"
    assert gateway_entries["WEKNORA_DEFAULT_RERANK_MODEL_ID"]["value"] == "deerflow-cnooc-qwen3-reranker-8b"
    assert gateway_entries["CNOOC_EMBEDDING_DIMENSION"]["value"] == "1024"
    assert gateway_entries["WEKNORA_INTERNAL_EMBEDDING_CONCURRENCY"]["value"] == "200"
    assert gateway_entries["CNOOC_RETRIEVAL_API_KEY"]["valueFrom"]["secretKeyRef"] == {
        "name": "deer-flow-env",
        "key": "CNOOC_RETRIEVAL_API_KEY",
    }
    relay_secret_ref = {
        "name": "deer-flow-env",
        "key": "WEKNORA_INTERNAL_MODEL_RELAY_KEY",
    }
    assert gateway_entries["WEKNORA_INTERNAL_MODEL_RELAY_KEY"]["valueFrom"]["secretKeyRef"] == relay_secret_ref
    assert weknora_entries["WEKNORA_INTERNAL_MODEL_RELAY_KEY"]["valueFrom"]["secretKeyRef"] == relay_secret_ref

    enterprise_models = yaml.safe_load(_read("k8s/enterprise/builtin-models-internal-only.yaml"))["builtin_models"]
    model_ids = {model["id"] for model in enterprise_models}
    assert model_ids == {
        "deerflow-cnooc-deepseek-v4-flash",
        "deerflow-cnooc-qwen3-embedding-8b",
        "deerflow-cnooc-qwen3-reranker-8b",
        "deerflow-dashscope-vlm",
        "deerflow-dashscope-asr",
    }
    embedding = next(model for model in enterprise_models if model["type"] == "Embedding")
    rerank = next(model for model in enterprise_models if model["type"] == "Rerank")
    assert embedding["parameters"]["base_url"] == "http://gateway:8001/api/weknora/internal-models/v1"
    assert embedding["parameters"]["embedding_parameters"]["dimension"] == 1024
    assert rerank["parameters"]["base_url"] == "http://gateway:8001/api/weknora/internal-models/v1"
    sql = _read("k8s/weknora/enterprise-model-feature-defaults.sql")
    assert "__DEERFLOW_DEFAULT_EMBEDDING_MODEL_ID__" in sql
    assert "__DEERFLOW_DEFAULT_RERANK_MODEL_ID__" in sql
    assert "deleted_at = COALESCE(deleted_at, CURRENT_TIMESTAMP)" in sql
    assert "NEW.deleted_at := COALESCE(NEW.deleted_at, CURRENT_TIMESTAMP)" in sql


def test_weknora_model_feature_defaults_are_applied_by_a_versioned_job():
    kustomization = yaml.safe_load(_read("k8s/weknora/kustomization.yaml"))
    assert "enterprise-model-feature-defaults-job.yaml" in kustomization["resources"]

    generators = {entry["name"]: entry for entry in kustomization["configMapGenerator"]}
    assert "builtin_agents.yaml" in generators["weknora-config"]["files"]
    assert "defaults.sql=enterprise-model-feature-defaults.sql" in generators["weknora-enterprise-model-defaults"]["files"]

    job = yaml.safe_load(_read("k8s/weknora/enterprise-model-feature-defaults-job.yaml"))
    assert job["kind"] == "Job"
    assert job["metadata"]["name"] == "weknora-enterprise-model-defaults-v5"
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["image"] == "paradedb/paradedb:v0.22.2-pg17"
    job_env = {item["name"]: item for item in container["env"]}
    assert job_env["DEFAULT_CHAT_MODEL_ID"]["value"] == "deerflow-qwen-plus"
    assert job_env["DEFAULT_EMBEDDING_MODEL_ID"]["value"] == "deerflow-dashscope-embedding"
    assert job_env["DEFAULT_RERANK_MODEL_ID"]["value"] == "deerflow-dashscope-rerank"
    assert job_env["PGPASSWORD"]["valueFrom"]["secretKeyRef"] == {
        "name": "weknora-secret",
        "key": "DB_PASSWORD",
    }
    command = "\n".join(container["args"])
    assert "psql" in command
    assert "__DEERFLOW_DEFAULT_CHAT_MODEL_ID__" in command
    assert "__DEERFLOW_DEFAULT_EMBEDDING_MODEL_ID__" in command
    assert "__DEERFLOW_DEFAULT_RERANK_MODEL_ID__" in command
    assert "/tmp/defaults.sql" in command
    assert "weknora-postgres" in command

    sql = _read("k8s/weknora/enterprise-model-feature-defaults.sql")
    for agent_id in (
        "builtin-data-analyst",
        "builtin-quick-answer",
        "builtin-smart-reasoning",
        "builtin-wiki-researcher",
    ):
        assert agent_id in sql
    assert "'model_id', '__DEERFLOW_DEFAULT_CHAT_MODEL_ID__'" in sql
    assert "'image_upload_enabled', true" in sql
    assert "'vlm_model_id', 'deerflow-dashscope-vlm'" in sql
    assert "'attachment_image_understanding', true" in sql

    builtin_models = yaml.safe_load(_read("k8s/weknora/builtin_models.yaml"))
    model_defaults = {model["id"]: model["is_default"] for model in builtin_models["builtin_models"] if model["type"] == "KnowledgeQA"}
    assert model_defaults == {
        "deerflow-qwen-plus": True,
        "deerflow-deepseek-v4-pro": False,
        "deerflow-cnooc-deepseek-v4-flash": False,
    }

    enterprise_patches = _yaml_documents("k8s/enterprise/runtime-patches.yaml")
    defaults_job = next(patch for patch in enterprise_patches if patch.get("kind") == "Job" and patch.get("metadata", {}).get("name") == "weknora-enterprise-model-defaults-v5")
    enterprise_job_env = {item["name"]: item["value"] for item in defaults_job["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert enterprise_job_env["DEFAULT_CHAT_MODEL_ID"] == "deerflow-cnooc-deepseek-v4-flash"
    assert enterprise_job_env["DEFAULT_EMBEDDING_MODEL_ID"] == "deerflow-cnooc-qwen3-embedding-8b"
    assert enterprise_job_env["DEFAULT_RERANK_MODEL_ID"] == "deerflow-cnooc-qwen3-reranker-8b"


def test_weknora_builtin_agents_are_ready_for_every_personal_workspace():
    config = yaml.safe_load(_read("k8s/weknora/builtin_agents.yaml"))
    agents = {agent["id"]: agent for agent in config["builtin_agents"]}
    expected_ids = {
        "builtin-data-analyst",
        "builtin-quick-answer",
        "builtin-smart-reasoning",
        "builtin-wiki-researcher",
    }
    assert expected_ids <= agents.keys()

    for agent_id in expected_ids:
        agent_config = agents[agent_id]["config"]
        assert agent_config["model_id"] == "deerflow-cnooc-deepseek-v4-flash"
        assert agent_config["rerank_model_id"] == "deerflow-cnooc-qwen3-reranker-8b"
        assert agent_config["query_understand_model_id"] == "deerflow-cnooc-deepseek-v4-flash"
        assert agent_config["image_upload_enabled"] is True
        assert agent_config["vlm_model_id"] == "deerflow-dashscope-vlm"
        assert agent_config["attachment_image_understanding"] is True
        assert agent_config["audio_upload_enabled"] is True
        assert agent_config["asr_model_id"] == "deerflow-dashscope-asr"

    manifests = list(yaml.safe_load_all(_read("k8s/weknora/weknora.yaml")))
    app = next(document for document in manifests if document.get("kind") == "Deployment" and document.get("metadata", {}).get("name") == "weknora-app")
    mounts = app["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
    assert {
        "name": "config",
        "mountPath": "/app/config/builtin_agents.yaml",
        "subPath": "builtin_agents.yaml",
        "readOnly": True,
    } in mounts


def test_enterprise_weknora_native_kb_defaults_to_mineru_without_overriding_explicit_rules():
    sql = _read("k8s/enterprise/weknora-mineru-defaults.sql")
    kustomization = yaml.safe_load(_read("k8s/enterprise/kustomization.yaml"))
    job_documents = _yaml_documents("k8s/enterprise/weknora-mineru-defaults-job.yaml")
    job = next(document for document in job_documents if document["kind"] == "Job")
    container = job["spec"]["template"]["spec"]["containers"][0]

    assert "CREATE OR REPLACE FUNCTION public.deerflow_ensure_mineru_parser_rule()" in sql
    assert "CREATE TRIGGER deerflow_mineru_parser_defaults" in sql
    assert "BEFORE INSERT OR UPDATE OF chunking_config" in sql
    assert "BEFORE INSERT" in sql
    assert "jsonb_build_array('pdf', 'doc', 'docx', 'ppt', 'pptx')" in sql
    assert "'engine', 'mineru'" in sql
    assert "missing_file_types" in sql
    assert "'builtin', 'markitdown'" in sql
    assert "UPDATE public.knowledge_bases" in sql
    assert "weknora-mineru-defaults-job.yaml" in kustomization["resources"]
    assert any(generator["name"] == "weknora-mineru-defaults" and "defaults.sql=weknora-mineru-defaults.sql" in generator["files"] for generator in kustomization["configMapGenerator"])
    assert job["metadata"]["name"] == "weknora-enterprise-mineru-defaults-v2"
    assert container["env"] == [
        {
            "name": "PGPASSWORD",
            "valueFrom": {
                "secretKeyRef": {
                    "name": "weknora-secret",
                    "key": "DB_PASSWORD",
                }
            },
        }
    ]
    assert container["volumeMounts"] == [
        {
            "name": "defaults-sql",
            "mountPath": "/sql",
            "readOnly": True,
        }
    ]


def test_weknora_sso_bootstrap_uses_same_origin_session_endpoint():
    config_js = _read("k8s/weknora/ui-patches/config.js")
    index_html = _read("k8s/weknora/ui-patches/index.html")
    application_js = _read("k8s/weknora/ui-patches/index-itUW58et.js")

    assert 'DEERFLOW_SSO_URL: "/api/weknora/sso-session"' in config_js
    assert '"/api/weknora/sso-session"' in index_html
    assert 'const url = "/api/weknora/sso-session";' in application_js
    assert "127.0.0.1:2026/api/weknora/sso-session" not in config_js
    assert "127.0.0.1:2026/api/weknora/sso-session" not in index_html
    assert ":2026/api/weknora/sso-session" not in application_js


def test_weknora_sso_bootstrap_recovers_from_stale_tenant_selection():
    index_html = _read("k8s/weknora/ui-patches/index.html")

    assert "const clearTenantScopedState" in index_html
    assert 'localStorage.removeItem("weknora_lite_mode")' in index_html
    assert 'sessionStorage.removeItem("weknora_lite_last_path")' in index_html
    assert 'localStorage.getItem("weknora_selected_tenant_id")' in index_html
    assert '"/api/weknora-native/validate-tenant"' in index_html
    assert '"X-Tenant-ID": selectedTenantId' in index_html
    assert "validationPayload?.valid === false" in index_html
    assert "selectedTenantResponse.status === 400" not in index_html


def test_weknora_knowledge_graph_is_enabled_without_native_source_changes():
    manifests = _yaml_documents("k8s/weknora/weknora.yaml")
    graph = next(document for document in manifests if document.get("kind") == "StatefulSet" and document.get("metadata", {}).get("name") == "weknora-neo4j")
    app = next(document for document in manifests if document.get("kind") == "Deployment" and document.get("metadata", {}).get("name") == "weknora-app")
    graph_container = graph["spec"]["template"]["spec"]["containers"][0]
    graph_env = {entry["name"]: entry for entry in graph_container["env"]}
    app_env = {entry["name"]: entry for entry in app["spec"]["template"]["spec"]["containers"][0]["env"]}

    assert graph_container["image"] == "neo4j:2025.10.1"
    assert "NEO4J_PASSWORD" not in graph_env
    assert graph_env["DEERFLOW_NEO4J_PASSWORD"]["valueFrom"]["secretKeyRef"] == {
        "name": "weknora-neo4j-secret",
        "key": "NEO4J_PASSWORD",
    }
    assert graph_env["NEO4J_AUTH"]["value"] == "neo4j/$(DEERFLOW_NEO4J_PASSWORD)"
    assert graph_env["NEO4J_PLUGINS"]["value"] == '["apoc"]'
    assert app_env["ENABLE_GRAPH_RAG"]["value"] == "true"
    assert app_env["NEO4J_ENABLE"]["value"] == "true"
    assert app_env["NEO4J_URI"]["value"] == "bolt://weknora-neo4j:7687"
    assert app_env["NEO4J_PASSWORD"]["valueFrom"]["secretKeyRef"] == {
        "name": "weknora-neo4j-secret",
        "key": "NEO4J_PASSWORD",
    }
    assert "NEO4J_PASSWORD" not in _read("k8s/weknora/kustomization.yaml")


def test_weknora_ui_patch_preserves_native_text_button_contrast():
    index_html = _read("k8s/weknora/ui-patches/index.html")

    assert ".t-button--theme-primary.t-button--variant-base," in index_html
    assert ".t-button--theme-primary.t-button--variant-base:hover" in index_html
    assert "\n      .t-button--theme-primary," not in index_html
    assert "\n      .t-button--theme-primary:hover" not in index_html


def test_enterprise_bundle_has_one_public_nodeport_and_deletes_weknora_nodeports():
    nodeport = yaml.safe_load(_read("k8s/enterprise/nginx-nodeport.yaml"))
    assert nodeport["spec"]["ports"] == [
        {
            "name": "app-http",
            "port": 2026,
            "targetPort": 2026,
            "nodePort": 32026,
        }
    ]

    patches = _yaml_documents("k8s/enterprise/runtime-patches.yaml")
    deleted = {patch["metadata"]["name"] for patch in patches if patch.get("$patch") == "delete"}
    assert deleted == {"weknora-app-nodeport", "weknora-frontend-nodeport"}

    provisioner_patch = next(patch for patch in patches if patch.get("kind") == "Deployment" and patch.get("metadata", {}).get("name") == "provisioner")
    provisioner_env = provisioner_patch["spec"]["template"]["spec"]["containers"][0]["env"]
    provisioner_values = {entry["name"]: entry["value"] for entry in provisioner_env}
    assert provisioner_values["SANDBOX_SERVICE_TYPE"] == "ClusterIP"
    assert provisioner_values["SANDBOX_CA_SECRET_NAME"] == "deer-flow-enterprise-ca"
    assert provisioner_values["SANDBOX_CA_SECRET_KEY"] == "ca-bundle.crt"
    assert provisioner_values["SANDBOX_CA_MOUNT_PATH"] == "/etc/deerflow-ca"
    assert provisioner_values["SANDBOX_OPENSSL_CONFIGMAP_NAME"] == "deer-flow-gateway-openssl-compat"
    assert provisioner_values["SANDBOX_OPENSSL_CONFIGMAP_KEY"] == "openssl.cnf"
    assert provisioner_values["SANDBOX_OPENSSL_MOUNT_PATH"] == "/etc/deerflow-openssl"
    assert provisioner_values["DASHSCOPE_TLS_COMPAT"] == "true"
    assert provisioner_values["SANDBOX_EPHEMERAL_STORAGE_REQUEST"] == "2Gi"
    assert provisioner_values["SANDBOX_EPHEMERAL_STORAGE_LIMIT"] == "12Gi"
    sandbox_secret_keys = set(provisioner_values["SANDBOX_SECRET_ENV_KEYS"].split(","))
    assert "DASHSCOPE_API_KEY" in sandbox_secret_keys
    assert "GEMINI_API_KEY" not in sandbox_secret_keys
    assert {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
    } <= sandbox_secret_keys
    assert {"NO_PROXY", "no_proxy"}.isdisjoint(sandbox_secret_keys)
    sandbox_passthrough_keys = set(provisioner_values["SANDBOX_PASSTHROUGH_ENV_KEYS"].split(","))
    assert {"NO_PROXY", "no_proxy"} <= sandbox_passthrough_keys
    assert provisioner_values["NO_PROXY"] == provisioner_values["no_proxy"]
    sandbox_no_proxy = set(provisioner_values["NO_PROXY"].split(","))
    assert {
        "github.com",
        ".github.com",
        "githubusercontent.com",
        ".githubusercontent.com",
    } <= sandbox_no_proxy
    assert {
        "dashscope.aliyuncs.com",
        ".aliyuncs.com",
        "api.tavily.com",
        ".tavily.com",
    }.isdisjoint(sandbox_no_proxy)

    gateway_patch = next(patch for patch in patches if patch.get("kind") == "Deployment" and patch.get("metadata", {}).get("name") == "gateway")
    gateway_env = gateway_patch["spec"]["template"]["spec"]["containers"][0]["env"]
    gateway_values = {entry["name"]: entry.get("value") for entry in gateway_env if "value" in entry}
    gateway_secret_refs = {entry["name"]: entry.get("valueFrom", {}).get("secretKeyRef", {}) for entry in gateway_env if "valueFrom" in entry}
    assert gateway_values["DASHSCOPE_TLS_COMPAT"] == "true"
    for proxy_name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        assert gateway_secret_refs[proxy_name] == {
            "name": "deer-flow-env",
            "key": proxy_name,
        }
    assert gateway_values["NO_PROXY"] == gateway_values["no_proxy"]
    assert {
        "github.com",
        ".github.com",
        "githubusercontent.com",
        ".githubusercontent.com",
    } <= set(gateway_values["NO_PROXY"].split(","))
    assert {
        "dashscope.aliyuncs.com",
        ".aliyuncs.com",
        "api.tavily.com",
        ".tavily.com",
    }.isdisjoint(set(gateway_values["NO_PROXY"].split(",")))

    config = yaml.safe_load(_read("config.yaml"))
    sandbox_environment = config["sandbox"]["environment"]
    assert sandbox_environment["HTTP_PROXY"] == "$HTTP_PROXY"
    assert sandbox_environment["HTTPS_PROXY"] == "$HTTPS_PROXY"
    assert sandbox_environment["REQUESTS_CA_BUNDLE"] == "$REQUESTS_CA_BUNDLE"
    assert sandbox_environment["NODE_EXTRA_CA_CERTS"] == "$NODE_EXTRA_CA_CERTS"
    assert sandbox_environment["OPENSSL_CONF"] == "$OPENSSL_CONF"
    assert sandbox_environment["DASHSCOPE_TLS_COMPAT"] == "$DASHSCOPE_TLS_COMPAT"


def test_enterprise_local_volumes_are_pinned_to_known_nodes():
    documents = _yaml_documents("k8s/enterprise/storage-static.yaml")
    volumes = [document for document in documents if document.get("kind") == "PersistentVolume"]

    assert len(volumes) == 9
    assert {volume["metadata"]["name"] for volume in volumes} == {
        "deerflow-home-pv-datax-01",
        "deerflow-skills-pv-datax-01",
        "deerflow-postgresql-pv-datax-02",
        "deerflow-postgresql-backups-pv-datax-02",
        "deerflow-weknora-postgresql-pv-datax-02",
        "deerflow-redis-pv-datax-03",
        "deerflow-weknora-files-pv-datax-03",
        "deerflow-weknora-docreader-pv-datax-03",
        "deerflow-weknora-neo4j-pv-datax-03",
    }
    for volume in volumes:
        assert volume["spec"]["local"]["fsType"] == "xfs"
        values = volume["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"]
        assert len(values) == 1
        assert values[0] in {"datax-01", "datax-02", "datax-03"}


def test_enterprise_postgres_claim_template_patch_preserves_access_mode():
    patches = _yaml_documents("k8s/enterprise/storage-patches.yaml")
    postgres = next(patch for patch in patches if patch.get("kind") == "StatefulSet" and patch.get("metadata", {}).get("name") == "postgres")

    claim = postgres["spec"]["volumeClaimTemplates"][0]
    assert claim["metadata"]["name"] == "data"
    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]


def test_enterprise_neo4j_uses_pinned_image_and_static_storage():
    kustomization = yaml.safe_load(_read("k8s/enterprise/kustomization.yaml"))
    neo4j_image = next(image for image in kustomization["images"] if image["name"] == "neo4j")
    assert neo4j_image == {
        "name": "neo4j",
        "newName": "m.daocloud.io/docker.io/library/neo4j",
        "digest": "sha256:155c8aad10d5c838bc3bbc476c0418779086547822acb214ec5e3d49ba336907",
    }

    patches = _yaml_documents("k8s/enterprise/storage-patches.yaml")
    claim = next(patch for patch in patches if patch.get("kind") == "PersistentVolumeClaim" and patch.get("metadata", {}).get("name") == "weknora-neo4j-data")
    assert claim["spec"]["storageClassName"] == "deerflow-weknora-local"
    assert claim["spec"]["volumeName"] == "deerflow-weknora-neo4j-pv-datax-03"
    assert claim["spec"]["resources"]["requests"]["storage"] == "50Gi"


def test_skillhub_server_trusts_only_the_pinned_globalsign_r46_root():
    certificate = REPO_ROOT / "k8s" / "skillhub" / "certs" / "globalsign-root-r46.pem"
    der = ssl.PEM_cert_to_DER_cert(certificate.read_text(encoding="ascii"))
    assert hashlib.sha1(der).hexdigest() == "53a2b04bca6bd645e6398a8ec40dd2bf77c3a290"
    assert hashlib.sha256(der).hexdigest() == "4fa3126d8d3a11d1c4855a4f807cbad6cf919d3a5a88b03bea2c6372d93c40c9"

    dockerfile = _read("k8s/skillhub/Dockerfile.server-overlay")
    assert "-alias globalsign-root-r46" in dockerfile
    assert "-cacerts -storepass changeit" in dockerfile
    assert "trustAll" not in dockerfile
    assert "disableHostnameVerification" not in dockerfile


def test_skillhub_enterprise_values_keep_native_service_capabilities_enabled():
    values = yaml.safe_load(_read("k8s/skillhub/values-deer-flow.example.yaml"))

    assert values["ingress"]["enabled"] is False
    assert values["web"]["service"]["enabled"] is True
    assert values["server"]["service"]["enabled"] is True
    assert values["auth"]["direct"]["enabled"] is True
    assert values["bootstrapAdmin"]["enabled"] is True
    assert values["postgresql"]["enabled"] is True
    assert values["postgresql"]["primary"]["persistence"]["enabled"] is True
    assert values["redis"]["enabled"] is True
    assert values["redis"]["master"]["persistence"]["enabled"] is True
    assert values["scanner"]["enabled"] is True

    server_env = {item["name"]: item.get("value") for item in values["server"]["extraEnv"]}
    assert server_env["SKILLHUB_AUTH_SESSION_BOOTSTRAP_ENABLED"] == "true"
    assert server_env["SKILLHUB_AUTH_DEERFLOW_SESSION_ENABLED"] == "true"
    # This disables only automatic personal namespace creation. Native
    # namespace workbench/create/governance routes remain available under RBAC.
    assert server_env["SKILLHUB_NAMESPACE_PERSONAL_PROVISIONING_ENABLED"] == "false"


def test_skillhub_nginx_routes_survive_the_kubernetes_template_renderer():
    config = _read("k8s/local-pvc/nginx.conf")
    assert "proxy_pass http://gateway:8001/internal/skillhub/session/assertion;" in config
    assert config.count("proxy_pass http://skillhub-web:80;") == 3
    assert "proxy_pass http://$gateway_upstream/internal/skillhub" not in config
    assert "proxy_pass http://$skillhub_upstream" not in config

    for path in (
        "k8s/local-pvc/nginx.conf",
        "docker/nginx/nginx.conf",
        "docker/nginx/nginx.local.conf",
    ):
        nginx = _read(path)
        bootstrap = nginx.split("location = /platform/skillhub/api/v1/auth/session/bootstrap {", 1)[1].split("\n        }", 1)[0]
        assertion = nginx.split("location = /_internal/skillhub/session-assertion {", 1)[1].split("\n        }", 1)[0]
        assert 'proxy_set_header Cookie "";' in bootstrap
        assert "proxy_set_header Cookie $http_cookie;" in assertion

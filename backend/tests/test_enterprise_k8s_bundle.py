"""Regression coverage for the ARM64 enterprise Kubernetes bundle."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _yaml_documents(path: str) -> list[dict]:
    return [document for document in yaml.safe_load_all(_read(path)) if isinstance(document, dict)]


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


def test_enterprise_sandbox_image_pins_arm64_websocat_and_cairosvg():
    dockerfile = _read("docker/sandbox-ppt-master/Dockerfile")

    assert "WEBSOCAT_VERSION=1.14.1" in dockerfile
    assert "711a69576a2ff473fb01a90ffafb571c2ed019e55479d7ae71b12c2eadeb7011" in dockerfile
    assert "websocat --version" in dockerfile
    assert '"CairoSVG==2.7.1"' in dockerfile
    assert "cairosvg.svg2png" in dockerfile
    assert '--upgrade --force-reinstall "pydantic' not in dockerfile
    assert "Version(pydantic.__version__) >= Version('2.12.5')" in dockerfile


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
        "deer-flow-gateway": "20260722-enterprise-arm64-rc2",
        "deer-flow-frontend": "20260722-enterprise-arm64-rc1",
        "deer-flow-provisioner": "20260722-enterprise-arm64-rc2",
        "deer-flow-skills": "20260722-enterprise-arm64-rc1",
        "deer-flow-mineru-adapter": "20260723-enterprise-arm64-rc1",
        "paradedb/paradedb": "pg16-search-arm64-20260722-1",
    }
    assert {image["name"]: image.get("newTag") for image in custom} == expected_tags
    assert "latest" not in _read("k8s/enterprise/kustomization.yaml")


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
    env = {entry["name"]: entry["value"] for entry in container["env"]}

    assert container["name"] == "postgres"
    assert container["imagePullPolicy"] == "Never"
    assert container["args"] == ["-c", "shared_preload_libraries=pg_search"]
    assert env["PGDATA"] == "/var/lib/postgresql/data/pgdata"


def test_enterprise_frontend_uses_same_origin_weknora_url():
    patches = _yaml_documents("k8s/enterprise/runtime-patches.yaml")
    frontend_patch = next(patch for patch in patches if patch.get("kind") == "Deployment" and patch.get("metadata", {}).get("name") == "frontend")
    container = frontend_patch["spec"]["template"]["spec"]["containers"][0]
    env = {entry["name"]: entry["value"] for entry in container["env"]}

    assert env["WEKNORA_FRONTEND_URL"] == "/platform/knowledge-bases"


def test_enterprise_weknora_app_trusts_enterprise_ca_bundle():
    patches = _yaml_documents("k8s/enterprise/runtime-patches.yaml")
    app_patch = next(patch for patch in patches if patch.get("kind") == "Deployment" and patch.get("metadata", {}).get("name") == "weknora-app")
    pod_spec = app_patch["spec"]["template"]["spec"]
    container = next(container for container in pod_spec["containers"] if container["name"] == "app")
    env = {entry["name"]: entry["value"] for entry in container["env"]}
    mounts = {mount["name"]: mount for mount in container["volumeMounts"]}
    volumes = {volume["name"]: volume for volume in pod_spec["volumes"]}

    ca_path = "/etc/deerflow-ca/ca-bundle.crt"
    assert env["SSL_CERT_FILE"] == ca_path
    assert env["REQUESTS_CA_BUNDLE"] == ca_path
    assert env["CURL_CA_BUNDLE"] == ca_path
    assert mounts["enterprise-ca"] == {
        "name": "enterprise-ca",
        "mountPath": "/etc/deerflow-ca",
        "readOnly": True,
    }
    assert volumes["enterprise-ca"] == {
        "name": "enterprise-ca",
        "secret": {
            "secretName": "deer-flow-enterprise-ca",
            "items": [{"key": "ca-bundle.crt", "path": "ca-bundle.crt"}],
        },
    }


def test_enterprise_mineru_adapter_uses_secret_and_internal_service_only():
    documents = _yaml_documents("k8s/enterprise/mineru-adapter.yaml")
    deployment = next(document for document in documents if document["kind"] == "Deployment")
    service = next(document for document in documents if document["kind"] == "Service")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item["value"] for item in container["env"]}

    assert deployment["metadata"]["name"] == "deer-flow-mineru-adapter"
    assert container["envFrom"] == [{"secretRef": {"name": "deer-flow-mineru"}}]
    assert container["imagePullPolicy"] == "Never"
    assert service["metadata"]["name"] == "deer-flow-mineru-adapter"
    assert service["spec"]["ports"][0]["port"] == 50052
    assert all(document["kind"] != "Secret" for document in documents)
    assert env["REQUESTS_CA_BUNDLE"] == "/etc/deerflow-ca/ca-bundle.crt"
    assert container["volumeMounts"] == [{"name": "enterprise-ca", "mountPath": "/etc/deerflow-ca", "readOnly": True}]


def test_enterprise_routes_presentation_parsing_through_mineru_adapter():
    patches = _yaml_documents("k8s/enterprise/runtime-patches.yaml")
    gateway = next(patch for patch in patches if patch.get("kind") == "Deployment" and patch.get("metadata", {}).get("name") == "gateway")
    gateway_env = {item["name"]: item["value"] for item in gateway["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert gateway_env["WEKNORA_PRESENTATION_PARSER_ENGINE"] == "mineru"
    assert gateway_env["WEKNORA_MINERU_ENDPOINT"] == "http://deer-flow-mineru-adapter:50052"
    assert gateway_env["WEKNORA_MINERU_AUTO_CONFIGURE"] == "true"

    weknora = next(patch for patch in patches if patch.get("kind") == "Deployment" and patch.get("metadata", {}).get("name") == "weknora-app")
    weknora_env = {item["name"]: item["value"] for item in weknora["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert weknora_env["SSRF_WHITELIST_EXTRA"] == "deer-flow-mineru-adapter"


def test_weknora_sso_bootstrap_uses_same_origin_session_endpoint():
    config_js = _read("k8s/weknora/ui-patches/config.js")
    index_html = _read("k8s/weknora/ui-patches/index.html")
    application_js = _read("k8s/weknora/ui-patches/index-DeX5ZUuT.js")

    assert 'DEERFLOW_SSO_URL: "/api/weknora/sso-session"' in config_js
    assert "'/api/weknora/sso-session'" in index_html
    assert 'const url = "/api/weknora/sso-session";' in application_js
    assert "127.0.0.1:2026/api/weknora/sso-session" not in config_js
    assert "127.0.0.1:2026/api/weknora/sso-session" not in index_html
    assert ":2026/api/weknora/sso-session" not in application_js


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
    assert {entry["name"]: entry["value"] for entry in provisioner_env}["SANDBOX_SERVICE_TYPE"] == "ClusterIP"


def test_enterprise_local_volumes_are_pinned_to_known_nodes():
    documents = _yaml_documents("k8s/enterprise/storage-static.yaml")
    volumes = [document for document in documents if document.get("kind") == "PersistentVolume"]

    assert len(volumes) == 8
    assert {volume["metadata"]["name"] for volume in volumes} == {
        "deerflow-home-pv-datax-01",
        "deerflow-skills-pv-datax-01",
        "deerflow-postgresql-pv-datax-02",
        "deerflow-postgresql-backups-pv-datax-02",
        "deerflow-weknora-postgresql-pv-datax-02",
        "deerflow-redis-pv-datax-03",
        "deerflow-weknora-files-pv-datax-03",
        "deerflow-weknora-docreader-pv-datax-03",
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

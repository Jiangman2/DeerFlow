"""Regression coverage for the ARM64 enterprise Kubernetes bundle."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def _yaml_documents(path: str) -> list[dict]:
    return [
        document
        for document in yaml.safe_load_all(_read(path))
        if isinstance(document, dict)
    ]


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
    }

    for path, tokens in expected.items():
        dockerfile = _read(path)
        for token in tokens:
            assert token in dockerfile, f"{path} is missing {token}"


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
    third_party = [image for image in images if not image["name"].startswith("deer-flow-")]
    custom = [image for image in images if image["name"].startswith("deer-flow-")]

    assert third_party
    assert custom
    assert all(image.get("digest", "").startswith("sha256:") for image in third_party)
    assert all(image.get("newTag") == "20260721-enterprise-arm64-1" for image in custom)
    assert "latest" not in _read("k8s/enterprise/kustomization.yaml")


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
    deleted = {
        patch["metadata"]["name"]
        for patch in patches
        if patch.get("$patch") == "delete"
    }
    assert deleted == {"weknora-app-nodeport", "weknora-frontend-nodeport"}

    provisioner_patch = next(
        patch
        for patch in patches
        if patch.get("kind") == "Deployment"
        and patch.get("metadata", {}).get("name") == "provisioner"
    )
    provisioner_env = provisioner_patch["spec"]["template"]["spec"]["containers"][0]["env"]
    assert {entry["name"]: entry["value"] for entry in provisioner_env}[
        "SANDBOX_SERVICE_TYPE"
    ] == "ClusterIP"


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
        values = volume["spec"]["nodeAffinity"]["required"]["nodeSelectorTerms"][0][
            "matchExpressions"
        ][0]["values"]
        assert len(values) == 1
        assert values[0] in {"datax-01", "datax-02", "datax-03"}

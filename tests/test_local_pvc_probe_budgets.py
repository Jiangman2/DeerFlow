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

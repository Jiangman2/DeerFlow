"""Kubernetes Gateway worker-count configuration contracts."""

from pathlib import Path


STACK = Path(__file__).resolve().parents[2] / "k8s" / "local-pvc" / "deer-flow-pvc-stack.yaml"


def test_k8s_gateway_worker_count_is_overridable_and_defaults_to_one():
    manifest = STACK.read_text(encoding="utf-8")
    assert "--port 8001 --workers ${GATEWAY_WORKERS:-1}" in manifest
    assert "--port 8001 --workers 1" not in manifest


def test_ppt_runner_remains_single_worker():
    manifest = STACK.read_text(encoding="utf-8")
    assert "--port 8003 --workers 1" in manifest

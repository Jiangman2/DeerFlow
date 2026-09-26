"""Source overlays must not be mistaken for bytes baked into an image."""

import copy
import json
from pathlib import Path

import pytest

import build_offline as builder


def protected():
    return {
        "gateway": {
            "/app/backend/app/gateway/app.py",
            "/app/backend/packages/harness/deerflow/utils/thread_id.py",
        },
        "frontend": {"/app/frontend/src/core/agents/api.ts"},
        "provisioner": {"/app/app.py"},
    }


def objects(component, mount, *, live_only=False):
    container = {
        "name": component,
        "env": [{"name": "API_KEY", "value": "DO_NOT_LOG"}],
        "volumeMounts": [mount],
    }
    template = {"containers": [copy.deepcopy(container)]}
    if live_only:
        template["containers"][0]["volumeMounts"] = []
    return (
        {
            "items": [
                {
                    "metadata": {"name": component},
                    "spec": {"template": {"spec": template}},
                }
            ]
        },
        {
            "items": [
                {
                    "metadata": {"name": "pod", "labels": {"app": component}},
                    "spec": {"containers": [container]},
                }
            ]
        },
    )


@pytest.mark.parametrize("live_only", [False, True])
@pytest.mark.parametrize(
    "component,path",
    [
        ("gateway", "/app/backend/app/gateway/app.py"),
        ("gateway", "/app/backend/app/gateway"),
        ("gateway", "/app/backend"),
        ("gateway", "/app/backend/packages/harness/deerflow/utils/thread_id.py"),
        ("frontend", "/app/frontend/src"),
        ("provisioner", "/app/app.py"),
        ("gateway", "/"),
    ],
)
def test_file_parent_and_dependency_overlays_block_even_if_readonly(
    component, path, live_only
):
    deployment, pods = objects(
        component,
        {
            "name": "scheduler-repair-volume",
            "mountPath": path,
            "readOnly": True,
            "subPath": "gateway_app.py",
        },
        live_only=live_only,
    )
    before = copy.deepcopy((deployment, pods))
    with pytest.raises(
        RuntimeError, match="Source volume overlay requires review"
    ) as e:
        builder.reject_source_overlays(deployment, pods, protected())
    assert "DO_NOT_LOG" not in str(e.value)
    assert "live Pod" in str(e.value) if live_only else "Deployment" in str(e.value)
    assert (deployment, pods) == before


@pytest.mark.parametrize(
    "path",
    [
        "/app/backend/config.yaml",
        "/app/backend/extensions_config.json",
        "/app/backend/.deer-flow",
        "/app/skills",
        "/etc/deerflow-ca",
        "/var/run/secrets/kubernetes.io/serviceaccount",
        "/app/backend/app/gateway-old",
        "/app/backend/app/gateway/app.py.backup",
        "/dev/shm",
    ],
)
def test_unrelated_configuration_data_skills_and_prefixes_are_not_blocked(path):
    deployments, pods = objects("gateway", {"name": "existing", "mountPath": path})
    before = copy.deepcopy((deployments, pods))
    builder.reject_source_overlays(deployments, pods, protected())
    assert (deployments, pods) == before


@pytest.mark.parametrize(
    "path", ["relative", "/app/../app", "", None, "/app\x00", "\\app"]
)
def test_invalid_paths_are_not_silently_treated_as_safe(path):
    deployments, pods = objects("gateway", {"name": "invalid", "mountPath": path})
    with pytest.raises(ValueError, match="mount path"):
        builder.reject_source_overlays(deployments, pods, protected())


def test_runtime_targets_include_new_files_unchanged_anchors_and_frontend_outputs(
    tmp_path,
):
    for component, relative in (
        ("gateway", "app/gateway/app.py"),
        ("frontend", "src/core/agents/api.ts"),
        ("provisioner", "app.py"),
    ):
        (tmp_path / (component + "-delta.json")).write_text(
            json.dumps(
                {
                    "entries": [{"path": relative, "source_sha256": None}],
                    "anchors": {"unchanged.py": "a" * 64},
                }
            )
        )
    paths = builder.protected_source_paths(tmp_path)
    assert "/app/backend/app/gateway/app.py" in paths["gateway"]
    assert "/app/backend/unchanged.py" in paths["gateway"]
    assert "/app/app.py" in paths["provisioner"]
    assert "/app/frontend/.next" in paths["frontend"]
    assert "/app/frontend/server.js" in paths["frontend"]
    # A mount below a compiled output directory also hides part of that output.
    for path in ("/app/frontend/.next", "/app/frontend/.next/server/app"):
        deployments, pods = objects("frontend", {"name": "compiled", "mountPath": path})
        with pytest.raises(RuntimeError, match="Source volume overlay requires review"):
            builder.reject_source_overlays(deployments, pods, paths)


def test_current_build_guard_checks_mounts_before_accepting_cluster(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace

    (tmp_path / "CANDIDATE.json").write_text(json.dumps({"components": {}}))
    controller = builder.Builder(tmp_path, tmp_path, tmp_path / "unused")
    deployment, pods = objects(
        "gateway",
        {
            "name": "scheduler-repair-20260916-r8",
            "mountPath": "/app/backend/app/gateway/app.py",
        },
    )
    monkeypatch.setattr(builder, "verify_package", lambda p: None)
    monkeypatch.setattr(builder, "protected_source_paths", lambda p: protected())
    monkeypatch.setattr(
        builder, "inspect_cluster", lambda *args: {"fixture": "healthy"}
    )
    monkeypatch.setattr(
        builder.shutil,
        "disk_usage",
        lambda p: SimpleNamespace(free=200 * builder.GIB, used=100, total=1000),
    )
    real_read = Path.read_text
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda p, *a, **kw: (
            "MemAvailable: 33554432 kB\n"
            if str(p).replace("\\", "/") == "/proc/meminfo"
            else real_read(p, *a, **kw)
        ),
    )
    monkeypatch.setattr(
        builder,
        "kube",
        lambda args: (
            deployment if "deployment" in args else pods if "pods" in args else {}
        ),
    )
    with pytest.raises(RuntimeError, match="Source volume overlay requires review"):
        controller.guard()
    assert controller.baseline is None
    assert not controller.output.exists()

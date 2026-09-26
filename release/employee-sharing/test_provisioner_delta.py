"""Compare actual R16 source with the employee-only provisioner delta."""

import importlib.util
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def delta(monkeypatch):
    directory = Path(__file__).parent
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location(
        "employee_provisioner_delta", directory / "provisioner_delta.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pair(delta, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    baseline = Path(__file__).with_name("audited-baseline-20260924")
    if not baseline.is_dir():
        pytest.skip("requires supplied production source audit")
    source, result, receipt = delta.prepare(
        baseline, root / "docker/provisioner/app.py"
    )
    modules = []
    for index, content in enumerate((source, result)):
        name = "employee_provisioner_comparison_" + str(index)
        module = types.ModuleType(name)
        module.__file__ = "employee-candidate:provisioner/app.py"
        monkeypatch.setitem(sys.modules, name, module)
        exec(compile(content, module.__file__, "exec"), module.__dict__)
        module.SANDBOX_IMAGE = "local/aio:r9"
        module.PPT_MASTER_AGENT_HOST_IMAGE = "local/agent:r16"
        module.SANDBOX_PASSTHROUGH_ENV_KEYS = (
            "NO_PROXY",
            "no_proxy",
            "DISABLE_BROWSER",
            "CODEX_CONFIG_TOML",
            "PPT_MASTER_ENTERPRISE_RELAY_REQUIRED",
        )
        module.PPT_MASTER_AGENT_ONLY_SECRET_ENV_KEYS = frozenset(
            {"PPT_MASTER_RESPONSES_RELAY_KEY"}
        )
        module.SANDBOX_SECRET_ENV_KEYS = (
            "PPT_MASTER_RESPONSES_RELAY_KEY",
            "SANDBOX_API_KEY",
        )
        modules.append(module)
    monkeypatch.setenv("NO_PROXY", "localhost,127.0.0.1,.svc,.cluster.local")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1,.svc,.cluster.local")
    monkeypatch.setenv("DISABLE_BROWSER", "false")
    monkeypatch.setenv("CODEX_CONFIG_TOML", "model_provider = 'ordinary'")
    monkeypatch.setenv(
        "PPT_MASTER_AGENT_CODEX_CONFIG_TOML", "model_provider = 'enterprise_relay'"
    )
    monkeypatch.setenv("PPT_MASTER_AGENT_CODEX_MODEL_CATALOG_JSON", '{"models": []}')
    monkeypatch.setenv("PPT_MASTER_ENTERPRISE_RELAY_REQUIRED", "true")
    return modules, source, result, receipt


def test_unselected_r16_probes_environment_and_service_code_unchanged(pair, delta):
    _, source, result, receipt = pair
    old, new = delta.symbols(source.decode()), delta.symbols(result.decode())
    for name in old:
        if name not in delta.SELECTED:
            assert new[name] == old[name], name
    assert receipt["source_image"].endswith("ppt-master-v660-r16-split")
    assert receipt["production_workloads_mutated"] is False


@pytest.mark.parametrize("pvc", [False, True])
@pytest.mark.parametrize("ppt", [False, True])
def test_ordinary_pod_and_service_are_identical_to_production(pair, pvc, ppt):
    (original, candidate), *_ = pair
    for module in (original, candidate):
        module.USERDATA_PVC_NAME = "user-data" if pvc else ""
        module.SKILLS_PVC_NAME = "skills" if pvc else ""
        module.PPT_MASTER_AGENT_HOST_ENABLED = ppt
    for legacy in (False, True):
        assert (
            original._build_pod(
                "sandbox", "thread", user_id="bob", include_legacy_skills=legacy
            ).to_dict()
            == candidate._build_pod(
                "sandbox", "thread", user_id="bob", include_legacy_skills=legacy
            ).to_dict()
        )
    assert (
        original._build_service("sandbox").to_dict()
        == candidate._build_service("sandbox").to_dict()
    )


@pytest.mark.parametrize("pvc", [False, True])
def test_employee_mount_replaces_catalog_without_changing_r16_containers(pair, pvc):
    (original, candidate), *_ = pair
    for module in (original, candidate):
        module.USERDATA_PVC_NAME = "user-data" if pvc else ""
        module.SKILLS_PVC_NAME = "skills" if pvc else ""
        module.PPT_MASTER_AGENT_HOST_ENABLED = True
    before = original._build_pod("sandbox", "thread", user_id="bob")
    after = candidate._build_pod(
        "sandbox", "thread", user_id="bob", employee_skill_projection="a" * 24
    )
    assert [c.name for c in after.spec.containers] == [
        "sandbox",
        "ppt-master-agent-host",
    ]
    for old, new in zip(before.spec.containers, after.spec.containers, strict=True):
        old_data, new_data = old.to_dict(), new.to_dict()
        old_data.pop("volume_mounts")
        new_data.pop("volume_mounts")
        assert old_data == new_data
        skill_mounts = [
            m for m in new.volume_mounts if m.mount_path.startswith("/mnt/skills")
        ]
        assert len(skill_mounts) == 1 and skill_mounts[0].read_only is True
        assert skill_mounts[0].mount_path == "/mnt/skills"
    assert "agent-skills" not in " ".join(after.spec.init_containers[0].command)


def test_existing_other_projection_cannot_be_reused_or_destroyed(pair):
    from fastapi import HTTPException

    (original, module), *_ = pair
    module.USERDATA_PVC_NAME = "user-data"
    module.PPT_MASTER_AGENT_HOST_ENABLED = True
    pod = module._build_pod(
        "sandbox", "thread", user_id="bob", employee_skill_projection="a" * 24
    )
    module.core_v1 = types.SimpleNamespace(read_namespaced_pod=lambda *a, **kw: pod)
    # The fake has no create/delete API: identity verification cannot mutate.
    with pytest.raises(HTTPException) as exc:
        module._assert_existing_sandbox_identity(
            "sandbox",
            thread_id="thread",
            user_id="bob",
            include_legacy_skills=False,
            employee_skill_projection="b" * 24,
        )
    assert exc.value.status_code == 409


def test_unverified_production_source_is_rejected_before_candidate_read(
    delta, tmp_path
):
    import json

    (tmp_path / "provisioner").mkdir()
    (tmp_path / "provisioner/app.py").write_text("corrupted = True\n")
    (tmp_path / "SOURCE-BASELINE.json").write_text(
        json.dumps({"components": {"provisioner": {"files": {"app.py": "0" * 64}}}})
    )
    with pytest.raises(ValueError, match="checksum"):
        delta.prepare(tmp_path, tmp_path / "no-candidate-needed")


def test_matching_annotation_cannot_hide_writable_skill_mount(pair):
    from fastapi import HTTPException

    (_, module), *_ = pair
    module.USERDATA_PVC_NAME = "user-data"
    module.PPT_MASTER_AGENT_HOST_ENABLED = True
    key = "a" * 24
    pod = module._build_pod(
        "sandbox", "thread", user_id="bob", employee_skill_projection=key
    )
    next(
        m for m in pod.spec.containers[0].volume_mounts if m.mount_path == "/mnt/skills"
    ).read_only = False
    module.core_v1 = types.SimpleNamespace(read_namespaced_pod=lambda *a, **kw: pod)
    with pytest.raises(HTTPException) as exc:
        module._assert_existing_sandbox_identity(
            "sandbox",
            thread_id="thread",
            user_id="bob",
            include_legacy_skills=False,
            employee_skill_projection=key,
        )
    assert exc.value.status_code == 409


def test_candidate_changes_require_new_review(delta, tmp_path):
    root = Path(__file__).resolve().parents[2]
    baseline = Path(__file__).with_name("audited-baseline-20260924-dependencies")
    if not baseline.is_dir():
        pytest.skip("requires supplied production source audit")
    candidate = tmp_path / "app.py"
    candidate.write_bytes(
        (root / "docker/provisioner/app.py").read_bytes() + b"\n# unrelated change\n"
    )
    with pytest.raises(ValueError, match="checksum"):
        delta.prepare(baseline, candidate)

"""Regression tests for provisioner three-way skills + PVC volume support."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException


@pytest.mark.parametrize("pvc", [False, True])
def test_employee_projection_replaces_all_skill_mounts(provisioner_module, pvc):
    module = provisioner_module
    module.USERDATA_PVC_NAME = "user-data" if pvc else ""
    key = "a" * 24
    pod = module._build_pod("sandbox", "thread", user_id="bob", employee_skill_projection=key)
    for container in pod.spec.containers:
        mounts = [m for m in container.volume_mounts if m.mount_path == "/mnt/skills" or m.mount_path.startswith("/mnt/skills/")]
        assert len(mounts) == 1
        assert mounts[0].mount_path == "/mnt/skills" and mounts[0].read_only
        if pvc:
            assert mounts[0].sub_path == "deer-flow/users/bob/threads/thread/agent-skills/" + key
    assert not any(v.name in ("skills-public", "skills-custom", "skills-legacy") for v in pod.spec.volumes)
    assert "employee-skill-projection" in str(pod.metadata.annotations)
    initializer = next(c for c in pod.spec.init_containers if c.name == "prepare-user-data")
    assert "agent-skills" not in " ".join(initializer.command)


@pytest.mark.parametrize("key", ["../private", "/root", "a" * 23, "g" * 24, "a" * 25])
def test_employee_projection_accepts_only_opaque_keys(provisioner_module, key):
    with pytest.raises(ValueError):
        provisioner_module.CreateSandboxRequest(sandbox_id="id", thread_id="thread", user_id="bob", employee_skill_projection=key)


def test_employee_projection_rejects_reuse_with_other_projection(provisioner_module, monkeypatch):
    module = provisioner_module
    module.USERDATA_PVC_NAME = "user-data"
    pod = module._build_pod("sandbox", "thread", user_id="bob", employee_skill_projection="a" * 24)
    monkeypatch.setattr(module, "core_v1", SimpleNamespace(read_namespaced_pod=lambda *a: pod))
    with pytest.raises(HTTPException) as raised:
        module._assert_existing_sandbox_identity("sandbox", thread_id="thread", user_id="bob", include_legacy_skills=False, employee_skill_projection="b" * 24)
    assert raised.value.status_code == 409


# ── _build_volumes ─────────────────────────────────────────────────────


class TestBuildVolumes:
    """Tests for _build_volumes: hostPath and PVC three-way layouts."""

    # ── hostPath mode (default) ────────────────────────────────────────

    def test_hostpath_without_legacy_returns_three_volumes(self, provisioner_module):
        """hostPath mode omits legacy volume unless the backend requests it."""
        provisioner_module.SKILLS_PVC_NAME = ""
        provisioner_module.USERDATA_PVC_NAME = ""
        volumes = provisioner_module._build_volumes("thread-1")
        assert len(volumes) == 3

    def test_hostpath_skills_public_volume(self, provisioner_module):
        """First skills volume mounts public/ subdirectory."""
        provisioner_module.SKILLS_PVC_NAME = ""
        volumes = provisioner_module._build_volumes("thread-1")
        pub = volumes[0]
        assert pub.name == "skills-public"
        assert pub.host_path is not None
        assert pub.host_path.path.replace("\\", "/").endswith("/public")
        assert pub.host_path.type == "Directory"
        assert pub.persistent_volume_claim is None

    def test_hostpath_skills_custom_volume(self, provisioner_module):
        """Second skills volume mounts per-user custom directory."""
        provisioner_module.SKILLS_PVC_NAME = ""
        provisioner_module.USERDATA_PVC_NAME = ""
        volumes = provisioner_module._build_volumes("thread-1", user_id="user-7")
        custom = volumes[1]
        assert custom.name == "skills-custom"
        assert custom.host_path is not None
        assert "users/user-7/skills/custom" in custom.host_path.path.replace("\\", "/")
        assert custom.host_path.type == "DirectoryOrCreate"

    def test_hostpath_skills_legacy_volume(self, provisioner_module):
        """Legacy global-custom directory is mounted only when requested."""
        provisioner_module.SKILLS_PVC_NAME = ""
        provisioner_module.USERDATA_PVC_NAME = ""
        volumes = provisioner_module._build_volumes(
            "thread-1",
            include_legacy_skills=True,
        )
        legacy = volumes[2]
        assert legacy.name == "skills-legacy"
        assert legacy.host_path is not None
        assert legacy.host_path.path.replace("\\", "/").endswith("/custom")
        assert legacy.host_path.type == "Directory"

    def test_hostpath_without_legacy_has_no_legacy_volume(self, provisioner_module):
        """Fresh installs should not require a missing global legacy directory."""
        provisioner_module.SKILLS_PVC_NAME = ""
        provisioner_module.USERDATA_PVC_NAME = ""
        volumes = provisioner_module._build_volumes("thread-1")
        assert [volume.name for volume in volumes] == [
            "skills-public",
            "skills-custom",
            "user-data",
        ]

    def test_hostpath_userdata_includes_thread_id(self, provisioner_module):
        """hostPath user-data path should include thread_id."""
        provisioner_module.USERDATA_PVC_NAME = ""
        volumes = provisioner_module._build_volumes("my-thread-42")
        userdata_vol = volumes[-1]
        path = userdata_vol.host_path.path
        assert "my-thread-42" in path
        assert path.endswith("user-data")
        assert userdata_vol.host_path.type == "DirectoryOrCreate"

    # ── PVC mode ───────────────────────────────────────────────────────

    def test_pvc_returns_public_custom_and_userdata_volumes(self, provisioner_module):
        """PVC mode reuses user-data for per-user custom skills."""
        provisioner_module.SKILLS_PVC_NAME = "my-skills-pvc"
        provisioner_module.USERDATA_PVC_NAME = "my-userdata-pvc"
        volumes = provisioner_module._build_volumes("thread-1")
        assert [volume.name for volume in volumes] == [
            "skills-public",
            "user-data",
        ]

    def test_skills_pvc_overrides_hostpath(self, provisioner_module):
        """The public skills volume comes from the shared skills PVC."""
        provisioner_module.SKILLS_PVC_NAME = "my-skills-pvc"
        provisioner_module.USERDATA_PVC_NAME = "my-userdata-pvc"
        volumes = provisioner_module._build_volumes("thread-1")
        skills_vol = volumes[0]
        assert skills_vol.name == "skills-public"
        assert skills_vol.persistent_volume_claim is not None
        assert skills_vol.persistent_volume_claim.claim_name == "my-skills-pvc"
        assert skills_vol.persistent_volume_claim.read_only is True
        assert skills_vol.host_path is None

    def test_custom_skills_reuse_userdata_pvc(self, provisioner_module):
        """Registry installs reuse user-data instead of redeclaring the same PVC."""
        provisioner_module.SKILLS_PVC_NAME = "my-skills-pvc"
        provisioner_module.USERDATA_PVC_NAME = "my-userdata-pvc"
        volumes = provisioner_module._build_volumes("thread-1", user_id="user-7")

        assert [volume.name for volume in volumes].count("user-data") == 1
        userdata = volumes[1]
        assert userdata.persistent_volume_claim is not None
        assert userdata.persistent_volume_claim.claim_name == "my-userdata-pvc"
        assert userdata.host_path is None

    def test_userdata_pvc_overrides_hostpath(self, provisioner_module):
        """When USERDATA_PVC_NAME is set, user-data volume should use PVC."""
        provisioner_module.USERDATA_PVC_NAME = "my-userdata-pvc"
        volumes = provisioner_module._build_volumes("thread-1")
        userdata_vol = volumes[-1]
        assert userdata_vol.persistent_volume_claim is not None
        assert userdata_vol.persistent_volume_claim.claim_name == "my-userdata-pvc"
        assert userdata_vol.host_path is None

    def test_both_pvc_set(self, provisioner_module):
        """When both PVC names are set, every runtime volume uses a PVC."""
        provisioner_module.SKILLS_PVC_NAME = "skills-pvc"
        provisioner_module.USERDATA_PVC_NAME = "userdata-pvc"
        volumes = provisioner_module._build_volumes("thread-1")
        assert all(volume.persistent_volume_claim is not None for volume in volumes)

    def test_pvc_volume_names_are_stable(self, provisioner_module):
        """PVC mode uses the same category-aware volume names as hostPath mode."""
        provisioner_module.SKILLS_PVC_NAME = "x"
        provisioner_module.USERDATA_PVC_NAME = "userdata-pvc"
        volumes = provisioner_module._build_volumes("thread-1")
        assert [volume.name for volume in volumes] == [
            "skills-public",
            "user-data",
        ]

    def test_pvc_legacy_volume_uses_shared_skills_claim(self, provisioner_module):
        provisioner_module.SKILLS_PVC_NAME = "skills-pvc"
        provisioner_module.USERDATA_PVC_NAME = "userdata-pvc"
        volumes = provisioner_module._build_volumes(
            "thread-1",
            include_legacy_skills=True,
        )

        legacy = volumes[1]
        assert legacy.name == "skills-legacy"
        assert legacy.persistent_volume_claim.claim_name == "skills-pvc"
        assert legacy.persistent_volume_claim.read_only is True


# ── _build_volume_mounts ───────────────────────────────────────────────


class TestBuildVolumeMounts:
    """Tests for _build_volume_mounts: three-way mount paths and subPath."""

    # ── hostPath mode ──────────────────────────────────────────────────

    def test_hostpath_without_legacy_returns_three_mounts(self, provisioner_module):
        """hostPath mode omits legacy mount unless the backend requests it."""
        provisioner_module.SKILLS_PVC_NAME = ""
        provisioner_module.USERDATA_PVC_NAME = ""
        mounts = provisioner_module._build_volume_mounts("thread-1")
        assert len(mounts) == 3

    def test_hostpath_skills_public_mount(self, provisioner_module):
        """Public skills mount at /mnt/skills/public, read-only."""
        provisioner_module.SKILLS_PVC_NAME = ""
        provisioner_module.USERDATA_PVC_NAME = ""
        mounts = provisioner_module._build_volume_mounts("thread-1")
        assert mounts[0].name == "skills-public"
        assert mounts[0].mount_path == "/mnt/skills/public"
        assert mounts[0].read_only is True

    def test_hostpath_skills_custom_mount(self, provisioner_module):
        """Per-user custom skills mount at /mnt/skills/custom, read-only."""
        provisioner_module.SKILLS_PVC_NAME = ""
        provisioner_module.USERDATA_PVC_NAME = ""
        mounts = provisioner_module._build_volume_mounts("thread-1")
        assert mounts[1].name == "skills-custom"
        assert mounts[1].mount_path == "/mnt/skills/custom"
        assert mounts[1].read_only is True

    def test_hostpath_skills_legacy_mount(self, provisioner_module):
        """Legacy skills mount at /mnt/skills/legacy, read-only."""
        provisioner_module.SKILLS_PVC_NAME = ""
        provisioner_module.USERDATA_PVC_NAME = ""
        mounts = provisioner_module._build_volume_mounts(
            "thread-1",
            include_legacy_skills=True,
        )
        assert mounts[2].name == "skills-legacy"
        assert mounts[2].mount_path == "/mnt/skills/legacy"
        assert mounts[2].read_only is True

    def test_hostpath_without_legacy_has_no_legacy_mount(self, provisioner_module):
        """Users with custom skills should not see hidden legacy content in the sandbox."""
        provisioner_module.SKILLS_PVC_NAME = ""
        provisioner_module.USERDATA_PVC_NAME = ""
        mounts = provisioner_module._build_volume_mounts("thread-1")
        assert [mount.name for mount in mounts] == [
            "skills-public",
            "skills-custom",
            "user-data",
        ]

    def test_hostpath_userdata_read_write(self, provisioner_module):
        """User-data mount should always be read-write."""
        provisioner_module.SKILLS_PVC_NAME = ""
        provisioner_module.USERDATA_PVC_NAME = ""
        mounts = provisioner_module._build_volume_mounts("thread-1")
        userdata = mounts[-1]
        assert userdata.name == "user-data"
        assert userdata.mount_path == "/mnt/user-data"
        assert userdata.read_only is False

    # ── PVC mode ───────────────────────────────────────────────────────

    def test_pvc_returns_public_custom_and_userdata_mounts(self, provisioner_module):
        """PVC mode exposes the same category-aware paths as hostPath mode."""
        provisioner_module.SKILLS_PVC_NAME = "x"
        provisioner_module.USERDATA_PVC_NAME = "userdata-pvc"
        mounts = provisioner_module._build_volume_mounts("thread-1")
        assert [(mount.name, mount.mount_path) for mount in mounts] == [
            ("skills-public", "/mnt/skills/public"),
            ("user-data", "/mnt/skills/custom"),
            ("user-data", "/mnt/user-data"),
        ]

    def test_pvc_skills_mounts_use_separate_subpaths(self, provisioner_module):
        """Public and custom skills resolve from their actual backing PVC trees."""
        provisioner_module.SKILLS_PVC_NAME = "x"
        provisioner_module.USERDATA_PVC_NAME = "userdata-pvc"
        mounts = provisioner_module._build_volume_mounts(
            "thread-42",
            user_id="user-7",
        )
        assert mounts[0].sub_path == "public"
        assert mounts[1].sub_path == "deer-flow/users/user-7/skills/custom"

    def test_pvc_no_subpath_on_userdata(self, provisioner_module):
        """hostPath mode should not set sub_path on user-data mount."""
        provisioner_module.USERDATA_PVC_NAME = ""
        mounts = provisioner_module._build_volume_mounts("thread-1")
        userdata_mount = mounts[-1]
        assert userdata_mount.sub_path is None

    def test_skills_pvc_uses_public_subpath_by_default(self, provisioner_module):
        """PVC-backed public skills are mounted from the public directory."""
        provisioner_module.SKILLS_PVC_NAME = "my-skills-pvc"
        provisioner_module.USERDATA_PVC_NAME = "my-userdata-pvc"
        provisioner_module.SKILLS_PVC_SUBPATH_TEMPLATE = ""
        mounts = provisioner_module._build_volume_mounts("thread-42", user_id="user-7")
        skills_mount = mounts[0]
        assert skills_mount.sub_path == "public"

    def test_skills_pvc_can_use_user_scoped_subpath_template(self, provisioner_module):
        """Operators can opt into per-user/thread skills subPath for shared PVCs."""
        provisioner_module.SKILLS_PVC_NAME = "my-skills-pvc"
        provisioner_module.SKILLS_PVC_SUBPATH_TEMPLATE = "deer-flow/users/{user_id}/threads/{thread_id}/skills"
        mounts = provisioner_module._build_volume_mounts("thread-42", user_id="user-7")
        skills_mount = mounts[0]
        assert skills_mount.sub_path == "deer-flow/users/user-7/threads/thread-42/skills/public"

    def test_pvc_legacy_mount_uses_custom_subpath(self, provisioner_module):
        provisioner_module.SKILLS_PVC_NAME = "skills-pvc"
        provisioner_module.USERDATA_PVC_NAME = "userdata-pvc"
        mounts = provisioner_module._build_volume_mounts(
            "thread-42",
            user_id="user-7",
            include_legacy_skills=True,
        )

        assert mounts[2].name == "skills-legacy"
        assert mounts[2].mount_path == "/mnt/skills/legacy"
        assert mounts[2].sub_path == "custom"

    def test_pvc_sets_user_scoped_subpath(self, provisioner_module):
        """PVC mode should include user_id in the user-data subPath."""
        provisioner_module.USERDATA_PVC_NAME = "my-pvc"
        mounts = provisioner_module._build_volume_mounts("thread-42", user_id="user-7")
        userdata_mount = mounts[-1]
        assert userdata_mount.sub_path == "deer-flow/users/user-7/threads/thread-42/user-data"

    def test_pvc_defaults_to_default_user_subpath(self, provisioner_module):
        """Older callers should still land under a stable default user namespace."""
        provisioner_module.USERDATA_PVC_NAME = "my-pvc"
        mounts = provisioner_module._build_volume_mounts("thread-42")
        userdata_mount = mounts[-1]
        assert userdata_mount.sub_path == "deer-flow/users/default/threads/thread-42/user-data"


# ── _build_pod integration ─────────────────────────────────────────────


class TestBuildPodVolumes:
    """Integration: _build_pod should wire volumes and mounts correctly."""

    def test_pod_hostpath_without_legacy_has_three_volumes(self, provisioner_module):
        """hostPath Pod spec should omit legacy volume by default."""
        provisioner_module.SKILLS_PVC_NAME = ""
        provisioner_module.USERDATA_PVC_NAME = ""
        pod = provisioner_module._build_pod("sandbox-1", "thread-1")
        assert len(pod.spec.volumes) == 3

    def test_pod_hostpath_without_legacy_has_three_mounts(self, provisioner_module):
        """hostPath container should omit legacy mount by default."""
        provisioner_module.SKILLS_PVC_NAME = ""
        provisioner_module.USERDATA_PVC_NAME = ""
        pod = provisioner_module._build_pod("sandbox-1", "thread-1")
        assert len(pod.spec.containers[0].volume_mounts) == 3

    def test_pod_hostpath_with_legacy_has_four_volumes(self, provisioner_module):
        """Legacy volume should be present when the backend requests it."""
        provisioner_module.SKILLS_PVC_NAME = ""
        provisioner_module.USERDATA_PVC_NAME = ""
        pod = provisioner_module._build_pod(
            "sandbox-1",
            "thread-1",
            include_legacy_skills=True,
        )
        assert len(pod.spec.volumes) == 4

    def test_pod_hostpath_with_legacy_has_four_mounts(self, provisioner_module):
        """Legacy mount should be present when the backend requests it."""
        provisioner_module.SKILLS_PVC_NAME = ""
        provisioner_module.USERDATA_PVC_NAME = ""
        pod = provisioner_module._build_pod(
            "sandbox-1",
            "thread-1",
            include_legacy_skills=True,
        )
        assert len(pod.spec.containers[0].volume_mounts) == 4

    def test_pod_pvc_has_two_volumes(self, provisioner_module):
        """PVC Pod spec reuses user-data for the per-user custom mount."""
        provisioner_module.SKILLS_PVC_NAME = "skills-pvc"
        provisioner_module.USERDATA_PVC_NAME = "userdata-pvc"
        pod = provisioner_module._build_pod("sandbox-1", "thread-1")
        assert len(pod.spec.volumes) == 2

    def test_pod_pvc_has_three_mounts(self, provisioner_module):
        """PVC container should expose public/custom skills plus user-data."""
        provisioner_module.SKILLS_PVC_NAME = "skills-pvc"
        provisioner_module.USERDATA_PVC_NAME = "userdata-pvc"
        pod = provisioner_module._build_pod("sandbox-1", "thread-1")
        assert len(pod.spec.containers[0].volume_mounts) == 3

    def test_pod_pvc_mode_uses_user_scoped_subpath(self, provisioner_module):
        """Pod should use a user-scoped subPath for PVC user-data."""
        provisioner_module.SKILLS_PVC_NAME = "skills-pvc"
        provisioner_module.USERDATA_PVC_NAME = "userdata-pvc"
        pod = provisioner_module._build_pod("sandbox-1", "thread-1", user_id="user-7")
        assert pod.spec.volumes[0].persistent_volume_claim is not None
        assert pod.spec.volumes[-1].persistent_volume_claim is not None
        userdata_mount = pod.spec.containers[0].volume_mounts[-1]
        assert userdata_mount.sub_path == "deer-flow/users/user-7/threads/thread-1/user-data"

    def test_pod_records_exact_storage_identity_annotations(self, provisioner_module):
        pod = provisioner_module._build_pod(
            "sandbox-1",
            "thread-1",
            user_id="user-7",
            include_legacy_skills=True,
        )

        assert pod.metadata.annotations == {
            provisioner_module.SANDBOX_USER_ANNOTATION: "user-7",
            provisioner_module.SANDBOX_THREAD_ANNOTATION: "thread-1",
            provisioner_module.SANDBOX_LEGACY_SKILLS_ANNOTATION: "true",
        }

    def test_existing_sandbox_identity_accepts_exact_legacy_mounts(
        self,
        provisioner_module,
        monkeypatch,
    ):
        provisioner_module.SKILLS_PVC_NAME = "skills-pvc"
        provisioner_module.USERDATA_PVC_NAME = "userdata-pvc"
        pod = provisioner_module._build_pod(
            "sandbox-1",
            "thread-1",
            user_id="user-7",
        )
        pod.metadata.annotations = None
        monkeypatch.setattr(
            provisioner_module,
            "core_v1",
            SimpleNamespace(read_namespaced_pod=lambda *_args, **_kwargs: pod),
        )

        provisioner_module._assert_existing_sandbox_identity(
            "sandbox-1",
            thread_id="thread-1",
            user_id="user-7",
            include_legacy_skills=False,
        )

    def test_existing_sandbox_identity_rejects_cross_user_reuse(
        self,
        provisioner_module,
        monkeypatch,
    ):
        provisioner_module.SKILLS_PVC_NAME = "skills-pvc"
        provisioner_module.USERDATA_PVC_NAME = "userdata-pvc"
        pod = provisioner_module._build_pod(
            "sandbox-1",
            "thread-1",
            user_id="user-7",
        )
        monkeypatch.setattr(
            provisioner_module,
            "core_v1",
            SimpleNamespace(read_namespaced_pod=lambda *_args, **_kwargs: pod),
        )

        with pytest.raises(HTTPException) as exc_info:
            provisioner_module._assert_existing_sandbox_identity(
                "sandbox-1",
                thread_id="thread-1",
                user_id="user-8",
                include_legacy_skills=False,
            )

        assert exc_info.value.status_code == 409
        assert "different user/thread" in exc_info.value.detail

    def test_unannotated_legacy_mount_mode_cannot_be_reused_as_nonlegacy(
        self,
        provisioner_module,
        monkeypatch,
    ):
        provisioner_module.SKILLS_PVC_NAME = "skills-pvc"
        provisioner_module.USERDATA_PVC_NAME = "userdata-pvc"
        pod = provisioner_module._build_pod(
            "sandbox-1",
            "thread-1",
            user_id="user-7",
            include_legacy_skills=True,
        )
        pod.metadata.annotations = None
        monkeypatch.setattr(
            provisioner_module,
            "core_v1",
            SimpleNamespace(read_namespaced_pod=lambda *_args, **_kwargs: pod),
        )

        with pytest.raises(HTTPException) as exc_info:
            provisioner_module._assert_existing_sandbox_identity(
                "sandbox-1",
                thread_id="thread-1",
                user_id="user-7",
                include_legacy_skills=False,
            )

        assert exc_info.value.status_code == 409

    def test_pod_three_way_skills_mount_paths(self, provisioner_module):
        """Ensure public/custom/legacy mount paths are correct."""
        provisioner_module.SKILLS_PVC_NAME = ""
        provisioner_module.USERDATA_PVC_NAME = ""
        pod = provisioner_module._build_pod(
            "sandbox-1",
            "thread-1",
            include_legacy_skills=True,
        )
        mount_paths = {m.name: m.mount_path for m in pod.spec.containers[0].volume_mounts}
        assert mount_paths["skills-public"] == "/mnt/skills/public"
        assert mount_paths["skills-custom"] == "/mnt/skills/custom"
        assert mount_paths["skills-legacy"] == "/mnt/skills/legacy"

    def test_pod_pvc_mode_can_use_user_scoped_skills_subpath(self, provisioner_module):
        """Pod should use a configured user-scoped subPath for PVC skills."""
        provisioner_module.SKILLS_PVC_NAME = "skills-pvc"
        provisioner_module.SKILLS_PVC_SUBPATH_TEMPLATE = "deer-flow/users/{user_id}/threads/{thread_id}/skills"
        provisioner_module.USERDATA_PVC_NAME = "userdata-pvc"
        pod = provisioner_module._build_pod("sandbox-1", "thread-1", user_id="user-7")
        skills_mount = pod.spec.containers[0].volume_mounts[0]
        assert skills_mount.sub_path == "deer-flow/users/user-7/threads/thread-1/skills/public"

    def test_ppt_master_agent_host_is_a_real_second_container_when_enabled(self, provisioner_module, monkeypatch):
        provisioner_module.PPT_MASTER_AGENT_HOST_ENABLED = True
        provisioner_module.PPT_MASTER_AGENT_HOST_IMAGE = "registry.test/ppt-agent-host:v1"
        provisioner_module.SANDBOX_ENV_SECRET_NAME = "deer-flow-env"
        monkeypatch.setenv("PPT_MASTER_ENTERPRISE_RELAY_REQUIRED", "true")
        monkeypatch.setenv("CODEX_CONFIG_TOML", "model_provider = 'model_studio'")
        monkeypatch.setenv("CODEX_MODEL_CATALOG_JSON", '{"models": [{"slug": "qwen3.7-max"}]}')
        monkeypatch.setenv("PPT_MASTER_AGENT_CODEX_CONFIG_TOML", "model_provider = 'cnooc_deepseek'")
        monkeypatch.setenv(
            "PPT_MASTER_AGENT_CODEX_MODEL_CATALOG_JSON",
            '{"models": [{"slug": "DeepSeek-V4-Flash"}]}',
        )
        provisioner_module.SANDBOX_PASSTHROUGH_ENV_KEYS = (
            "PPT_MASTER_ENTERPRISE_RELAY_REQUIRED",
            "CODEX_CONFIG_TOML",
            "CODEX_MODEL_CATALOG_JSON",
        )
        provisioner_module.SANDBOX_SECRET_ENV_KEYS = (
            "SANDBOX_API_KEY",
            "PPT_MASTER_RESPONSES_RELAY_KEY",
        )
        provisioner_module.PPT_MASTER_AGENT_ONLY_SECRET_ENV_KEYS = frozenset({"PPT_MASTER_RESPONSES_RELAY_KEY"})
        pod = provisioner_module._build_pod("sandbox-1", "thread-1")

        assert [container.name for container in pod.spec.containers] == [
            "sandbox",
            "ppt-master-agent-host",
        ]
        host = pod.spec.containers[1]
        assert host.image == provisioner_module.PPT_MASTER_AGENT_HOST_IMAGE
        assert host.command == ["python3", "/opt/ppt-master-agent-host/agent_host.py"]
        assert host.ports[0].container_port == provisioner_module.PPT_MASTER_AGENT_HOST_PORT
        assert host.startup_probe.http_get.path == "/health"
        assert host.startup_probe.http_get.port == provisioner_module.PPT_MASTER_AGENT_HOST_PORT
        assert host.startup_probe.timeout_seconds == 5
        assert host.startup_probe.failure_threshold == 60
        assert host.readiness_probe.timeout_seconds == 5
        assert host.liveness_probe.timeout_seconds == 5
        assert host.liveness_probe.failure_threshold == 6
        assert host.resources.requests == {"cpu": "250m", "memory": "512Mi"}
        sandbox_env_names = {item.name for item in pod.spec.containers[0].env}
        host_env_names = {item.name for item in host.env}
        assert "PPT_MASTER_RESPONSES_RELAY_KEY" not in sandbox_env_names
        assert "PPT_MASTER_RESPONSES_RELAY_KEY" in host_env_names
        sandbox_env = {item.name: item.value for item in pod.spec.containers[0].env if item.value is not None}
        host_env = {item.name: item.value for item in host.env if item.value is not None}
        assert sandbox_env["CODEX_CONFIG_TOML"] == "model_provider = 'model_studio'"
        assert "qwen3.7-max" in sandbox_env["CODEX_MODEL_CATALOG_JSON"]
        assert host_env["CODEX_CONFIG_TOML"] == "model_provider = 'cnooc_deepseek'"
        assert "DeepSeek-V4-Flash" in host_env["CODEX_MODEL_CATALOG_JSON"]
        assert len(host_env_names) == len(host.env)
        assert host_env["PPT_MASTER_ENTERPRISE_RELAY_REQUIRED"] == "true"
        assert host_env["TMPDIR"] == provisioner_module.PPT_MASTER_AGENT_TEMP_ROOT
        assert host_env["TMP"] == provisioner_module.PPT_MASTER_AGENT_TEMP_ROOT
        assert host_env["TEMP"] == provisioner_module.PPT_MASTER_AGENT_TEMP_ROOT
        assert [(mount.name, mount.mount_path, mount.read_only) for mount in host.volume_mounts] == [(mount.name, mount.mount_path, mount.read_only) for mount in pod.spec.containers[0].volume_mounts]

    def test_enterprise_agent_host_requires_isolated_profile(self, provisioner_module, monkeypatch):
        provisioner_module.PPT_MASTER_AGENT_HOST_ENABLED = True
        provisioner_module.PPT_MASTER_AGENT_HOST_IMAGE = "registry.test/ppt-agent-host:v1"
        monkeypatch.setenv("PPT_MASTER_ENTERPRISE_RELAY_REQUIRED", "true")
        monkeypatch.delenv("PPT_MASTER_AGENT_CODEX_CONFIG_TOML", raising=False)
        monkeypatch.delenv("PPT_MASTER_AGENT_CODEX_MODEL_CATALOG_JSON", raising=False)

        with pytest.raises(RuntimeError, match="requires its own Codex profile"):
            provisioner_module._build_pod("sandbox-1", "thread-1")

    def test_pod_uses_configurable_ephemeral_storage_budget(self, provisioner_module):
        provisioner_module.SANDBOX_EPHEMERAL_STORAGE_REQUEST = "3Gi"
        provisioner_module.SANDBOX_EPHEMERAL_STORAGE_LIMIT = "14Gi"

        pod = provisioner_module._build_pod("sandbox-1", "thread-1")
        resources = pod.spec.containers[0].resources

        assert resources.requests["ephemeral-storage"] == "3Gi"
        assert resources.limits["ephemeral-storage"] == "14Gi"

    def test_pod_initializes_writable_user_data_roots_before_containers_start(self, provisioner_module):
        provisioner_module.USERDATA_PVC_NAME = ""
        pod = provisioner_module._build_pod("sandbox-1", "thread-1")

        assert pod.spec.init_containers is not None
        assert len(pod.spec.init_containers) == 2
        initializer = next(container for container in pod.spec.init_containers if container.name == "prepare-user-data")
        assert initializer.name == "prepare-user-data"
        assert initializer.command[:2] == ["python3", "-c"]
        command = initializer.command[2]
        assert "/mnt/user-data/workspace" in command
        assert "/mnt/user-data/uploads" in command
        assert "/mnt/user-data/outputs" in command
        assert "/mnt/user-data/workspace/.ppt-master-agent/tmp" in command
        assert [(mount.name, mount.mount_path, mount.read_only) for mount in initializer.volume_mounts] == [("user-data", "/mnt/user-data", False)]

    def test_pod_pvc_initializer_creates_thread_and_custom_skill_subpaths(self, provisioner_module):
        """Fresh PVC users must not fail Pod creation because a subPath is absent."""
        provisioner_module.SKILLS_PVC_NAME = "skills-pvc"
        provisioner_module.USERDATA_PVC_NAME = "userdata-pvc"
        pod = provisioner_module._build_pod(
            "sandbox-1",
            "thread-42",
            user_id="user-7",
        )

        initializer = pod.spec.init_containers[0]
        command = initializer.command[2]
        assert "/mnt/deer-flow-home/deer-flow/users/user-7/skills/custom" in command
        assert "/mnt/deer-flow-home/deer-flow/users/user-7/threads/thread-42/user-data/workspace" in command
        assert [(mount.name, mount.mount_path, mount.sub_path) for mount in initializer.volume_mounts] == [
            ("user-data", "/mnt/deer-flow-home", None),
        ]

    def test_pod_verifies_every_skill_mount_before_starting_runtime(self, provisioner_module):
        provisioner_module.SKILLS_PVC_NAME = "skills-pvc"
        provisioner_module.USERDATA_PVC_NAME = "userdata-pvc"
        pod = provisioner_module._build_pod(
            "sandbox-1",
            "thread-42",
            user_id="user-7",
            include_legacy_skills=True,
        )

        verifier = next(container for container in pod.spec.init_containers if container.name == "verify-skill-mounts")
        assert verifier.command[:2] == ["python3", "-c"]
        assert "path.open('rb').read(1)" in verifier.command[2]
        assert [mount.mount_path for mount in verifier.volume_mounts] == [
            "/mnt/skills/public",
            "/mnt/skills/custom",
            "/mnt/skills/legacy",
        ]
        assert all(mount.read_only for mount in verifier.volume_mounts)

    def test_ppt_master_agent_host_service_port_is_cluster_internal(self, provisioner_module):
        provisioner_module.PPT_MASTER_AGENT_HOST_ENABLED = True
        service = provisioner_module._build_service("sandbox-1")

        ports = {port.name: port for port in service.spec.ports}
        assert ports["ppt-agent"].port == provisioner_module.PPT_MASTER_AGENT_HOST_PORT
        assert ports["ppt-agent"].target_port == provisioner_module.PPT_MASTER_AGENT_HOST_PORT

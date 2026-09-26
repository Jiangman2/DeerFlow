"""Published dependencies remain the publisher-selected revision, not viewer names."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from deerflow.config.paths import Paths
from deerflow.persistence.agents.publications import TeamPublicationStore
from deerflow.skills.agent_publication import PublishedAgentSkillStorage, stage_agent_skills
from deerflow.skills.storage.user_scoped_skill_storage import UserScopedSkillStorage


def skill(root: Path, name: str, text: str):
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(f"---\nname: {name}\ndescription: A test skill\n---\n{text}", encoding="utf-8")
    (directory / "references").mkdir()
    (directory / "references" / "guide.txt").write_text(text, encoding="utf-8")
    return directory


@pytest.fixture
def stores(tmp_path, monkeypatch):
    paths = Paths(base_dir=tmp_path / "state")
    monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: paths)
    monkeypatch.setattr("deerflow.skills.agent_publication.get_paths", lambda: paths)
    config = SimpleNamespace(skills=SimpleNamespace(get_skills_path=lambda: tmp_path / "skills", container_path="/mnt/skills"))
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    owner = UserScopedSkillStorage("alice", app_config=config)
    viewer = UserScopedSkillStorage("bob", app_config=config)
    monkeypatch.setattr("deerflow.skills.agent_publication.get_or_new_user_skill_storage", lambda user_id, **kwargs: owner if user_id == "alice" else viewer)
    return paths, config, owner, viewer


def publish(stores, names):
    paths, config, _, _ = stores
    target = paths.team_agent_dir("employee")
    target.mkdir(parents=True)
    revision = stage_agent_skills(target, names, user_id="alice", app_config=config)
    (target / "config.yaml").write_text(yaml.safe_dump({"name": "employee", "skills": names, "published_by_user_id": "alice", "skills_revision": revision}), encoding="utf-8")
    return target, revision


def test_viewer_uses_published_revision_with_support_files(stores):
    _, config, owner, viewer = stores
    original = skill(owner.get_user_custom_root(), "analysis", "owner-version")
    skill(viewer.get_user_custom_root(), "analysis", "viewer-version")
    skill(owner.get_user_custom_root(), "not-selected", "private")
    target, revision = publish(stores, ["analysis"])
    (original / "references" / "guide.txt").write_text("changed", encoding="utf-8")
    storage = PublishedAgentSkillStorage("bob", "thread-one", publication=target, revision=revision, app_config=config)
    selected = storage.load_skills(enabled_only=True)
    assert [s.name for s in selected] == ["analysis"]
    assert (selected[0].skill_dir / "references" / "guide.txt").read_text() == "owner-version"
    assert selected[0].get_container_file_path() == "/mnt/skills/custom/analysis/SKILL.md"
    assert not selected[0].skill_file.is_relative_to(viewer.get_user_custom_root())
    assert storage.validate_skill_file_path(selected[0].skill_file) == selected[0].skill_file.resolve()
    assert not list(target.rglob("not-selected"))
    assert [s.name for s in viewer.load_skills(enabled_only=True)] == ["analysis"]
    assert "viewer-version" in viewer.load_skills(enabled_only=True)[0].skill_file.read_text()


def test_publication_requires_explicit_selection(stores):
    paths, config, _, _ = stores
    with pytest.raises(ValueError, match="explicit"):
        stage_agent_skills(paths.team_agent_dir("employee"), None, user_id="alice", app_config=config)


def test_unavailable_selection_does_not_copy_other_packages(stores):
    paths, config, owner, viewer = stores
    skill(viewer.get_user_custom_root(), "bob-only", "private")
    with pytest.raises(ValueError, match="unavailable"):
        stage_agent_skills(paths.team_agent_dir("employee"), ["bob-only"], user_id="alice", app_config=config)
    assert not (paths.team_agent_dir("employee") / "skills").exists()


def test_revocation_stops_existing_storage(stores):
    _, config, owner, _ = stores
    skill(owner.get_user_custom_root(), "analysis", "owner-version")
    target, revision = publish(stores, ["analysis"])
    storage = PublishedAgentSkillStorage("bob", "thread-one", publication=target, revision=revision, app_config=config)
    assert storage.load_skills()
    (target / "config.yaml").unlink()
    with pytest.raises(ValueError, match="publication"):
        storage.load_skills()


def test_modified_snapshot_is_rejected(stores):
    _, config, owner, _ = stores
    skill(owner.get_user_custom_root(), "analysis", "owner-version")
    target, revision = publish(stores, ["analysis"])
    (target / "skills" / "analysis" / "SKILL.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="digest"):
        PublishedAgentSkillStorage("bob", "thread-one", publication=target, revision=revision, app_config=config).load_skills()


def test_secret_file_is_not_distributed(stores):
    paths, config, owner, _ = stores
    package = skill(owner.get_user_custom_root(), "analysis", "owner-version")
    (package / ".env").write_text("API_KEY=do-not-copy", encoding="utf-8")
    with pytest.raises(ValueError, match="secret"):
        stage_agent_skills(paths.team_agent_dir("employee"), ["analysis"], user_id="alice", app_config=config)


def test_snapshot_scan_failure_never_publishes_a_manifest(stores, monkeypatch):
    paths, config, owner, _ = stores
    skill(owner.get_user_custom_root(), "analysis", "owner-version")
    seen = []

    def failed_scan(directory):
        seen.append(directory)
        assert "owner-version" in (directory / "SKILL.md").read_text()
        return {"blocked": False, "findings": [], "scanner_errors": ["scanner unavailable"]}

    monkeypatch.setattr("deerflow.skills.agent_publication.scan_skill_dir", failed_scan)
    target = paths.team_agent_dir("employee")
    with pytest.raises(ValueError, match="scan"):
        stage_agent_skills(target, ["analysis"], user_id="alice", app_config=config)
    assert len(seen) == 1
    assert not (target / "skills.manifest.json").exists()


def test_published_storage_cannot_upgrade_or_claim_editability(stores):
    import asyncio

    _, config, owner, _ = stores
    skill(owner.get_user_custom_root(), "analysis", "owner-version")
    target, revision = publish(stores, ["analysis"])
    storage = PublishedAgentSkillStorage("bob", "thread-one", publication=target, revision=revision, app_config=config)
    storage.load_skills()
    with pytest.raises(PermissionError):
        storage.ensure_custom_skill_is_editable("analysis")
    with pytest.raises(PermissionError):
        asyncio.run(storage.ainstall_or_upgrade_skill_from_archive("unused.skill", expected_name="analysis"))


def test_projection_is_per_thread_not_user_catalog(stores):
    paths, config, owner, viewer = stores
    skill(owner.get_user_custom_root(), "analysis", "owner-version")
    target, revision = publish(stores, ["analysis"])
    first = PublishedAgentSkillStorage("bob", "thread-one", publication=target, revision=revision, app_config=config)
    second = PublishedAgentSkillStorage("bob", "thread-two", publication=target, revision=revision, app_config=config)
    assert first.load_skills() and second.load_skills()
    assert first.projected_custom_root != second.projected_custom_root
    assert not first.projected_custom_root.is_relative_to(paths.sandbox_user_data_dir("thread-one", user_id="bob"))
    assert viewer.load_skills() == []


def test_tampered_view_and_changed_revision_are_rejected(stores):
    _, config, owner, _ = stores
    skill(owner.get_user_custom_root(), "analysis", "owner-version")
    target, revision = publish(stores, ["analysis"])
    storage = PublishedAgentSkillStorage("bob", "thread-one", publication=target, revision=revision, app_config=config)
    projected = storage.load_skills()[0].skill_file
    projected.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="projection digest"):
        storage.load_skills()
    (target / "config.yaml").write_text(yaml.safe_dump({"skills_revision": "0" * 64}), encoding="utf-8")
    with pytest.raises(ValueError, match="publication changed"):
        storage.load_skills()


def test_atomic_publication_carries_complete_skill_package(stores, tmp_path):
    paths, config, owner, _ = stores
    skill(owner.get_user_custom_root(), "analysis", "frozen-owner")
    bundle = tmp_path / "bundle"
    digest = stage_agent_skills(bundle, ["analysis"], user_id="alice", app_config=config)
    publications = TeamPublicationStore(paths)
    snapshot = publications.publish(
        "employee",
        owner="alice",
        config={"skills": ["analysis"]},
        soul="duty",
        avatar=None,
        expected_revision=None,
        skill_bundle=bundle,
    )
    assert snapshot.config["skills_revision"] == digest
    assert publications.read("employee").digest == snapshot.digest
    runtime = PublishedAgentSkillStorage("bob", "thread", publication=snapshot.directory, revision=digest, app_config=config)
    assert "frozen-owner" in runtime.load_skills()[0].skill_file.read_text()


def test_corrupt_skill_bundle_cannot_replace_old_publication(stores, tmp_path):
    paths, config, owner, _ = stores
    skill(owner.get_user_custom_root(), "analysis", "frozen-owner")
    publications = TeamPublicationStore(paths)
    first = publications.publish("employee", owner="alice", config={"skills": []}, soul="old", avatar=None, expected_revision=None)
    bundle = tmp_path / "bundle"
    stage_agent_skills(bundle, ["analysis"], user_id="alice", app_config=config)
    (bundle / "skills" / "analysis" / "references" / "guide.txt").write_text("changed")
    with pytest.raises(ValueError, match="digest"):
        publications.publish(
            "employee",
            owner="alice",
            config={"skills": ["analysis"]},
            soul="new",
            avatar=None,
            expected_revision=first.revision,
            skill_bundle=bundle,
        )
    assert publications.read("employee").revision == first.revision
    assert len(publications.history("employee", owner="alice")) == 1


def test_snapshot_claim_requires_actual_bundle(stores):
    paths, _, _, _ = stores
    with pytest.raises(ValueError, match="bundle"):
        TeamPublicationStore(paths).publish(
            "employee",
            owner="alice",
            config={"skills": [], "skills_revision": "a" * 64},
            soul="duty",
            avatar=None,
            expected_revision=None,
        )


def test_thread_catalog_is_pinned_and_withdrawal_invalidates_existing_instance(stores, tmp_path):
    from deerflow.persistence.agents.runtime import EmployeePublicationBindings
    from deerflow.skills.agent_publication import get_thread_published_skill_storage

    paths, config, owner, viewer = stores
    original = skill(owner.get_user_custom_root(), "analysis", "first-owner-version")
    skill(viewer.get_user_custom_root(), "analysis", "viewer-version")
    bundle = tmp_path / "first-bundle"
    stage_agent_skills(bundle, ["analysis"], user_id="alice", app_config=config)
    publications = TeamPublicationStore(paths)
    first = publications.publish("employee", owner="alice", config={"skills": ["analysis"]}, soul="duty", avatar=None, expected_revision=None, skill_bundle=bundle)
    EmployeePublicationBindings(paths).admit("thread", user_id="bob", name="employee", source="team")
    runtime = get_thread_published_skill_storage("thread", user_id="bob", app_config=config)
    assert "first-owner-version" in runtime.load_skills()[0].skill_file.read_text()
    (original / "SKILL.md").write_text("---\nname: analysis\ndescription: new\n---\nsecond-version", encoding="utf-8")
    second_bundle = tmp_path / "second-bundle"
    stage_agent_skills(second_bundle, ["analysis"], user_id="alice", app_config=config)
    second = publications.publish("employee", owner="alice", config={"skills": ["analysis"]}, soul="new", avatar=None, expected_revision=first.revision, skill_bundle=second_bundle)
    assert "first-owner-version" in runtime.load_skills()[0].skill_file.read_text()
    assert get_thread_published_skill_storage("other-thread", user_id="bob", app_config=config) is None
    publications.unpublish("employee", owner="alice", expected_revision=second.revision)
    with pytest.raises(FileNotFoundError, match="withdrawn"):
        runtime.load_skills()


def test_broken_thread_receipt_must_not_fall_back_to_personal_catalog(stores):
    from deerflow.skills.agent_publication import get_thread_published_skill_storage

    paths, config, _, _ = stores
    directory = paths.thread_dir("thread", user_id="bob")
    directory.mkdir(parents=True)
    (directory / "employee-publication.json").write_text("{}")
    with pytest.raises(ValueError, match="receipt"):
        get_thread_published_skill_storage("thread", user_id="bob", app_config=config)


@pytest.fixture
def admitted_skill(stores, tmp_path):
    from deerflow.persistence.agents.runtime import EmployeePublicationBindings

    paths, config, owner, viewer = stores
    skill(owner.get_user_custom_root(), "analysis", "owner-authority")
    skill(viewer.get_user_custom_root(), "analysis", "wrong-viewer-authority")
    bundle = tmp_path / "prepared"
    stage_agent_skills(bundle, ["analysis"], user_id="alice", app_config=config)
    snapshot = TeamPublicationStore(paths).publish("employee", owner="alice", config={"skills": ["analysis"]}, soul="duty", avatar=None, expected_revision=None, skill_bundle=bundle)
    EmployeePublicationBindings(paths).admit("thread", user_id="bob", name="employee", source="team")
    return snapshot


def test_lead_catalog_and_policy_use_same_published_revision(stores, admitted_skill):
    from deerflow.agents.lead_agent.prompt import get_enabled_skills_for_config
    from deerflow.agents.middlewares.skill_activation_middleware import SkillActivationMiddleware
    from deerflow.agents.middlewares.skill_tool_policy_middleware import SkillToolPolicyMiddleware

    _, config, _, _ = stores
    selected = get_enabled_skills_for_config(config, user_id="bob", thread_id="thread")
    assert len(selected) == 1
    assert "owner-authority" in selected[0].skill_file.read_text()
    for middleware in (SkillActivationMiddleware, SkillToolPolicyMiddleware):
        instance = middleware(user_id="bob", thread_id="thread", app_config=config, slash_source_owner_token="server-token")
        assert instance._storage().load_skills()[0].skill_file == selected[0].skill_file


@pytest.mark.asyncio
async def test_subagent_uses_parent_thread_publication(stores, admitted_skill, monkeypatch):
    import importlib.util
    import sys

    # The suite pre-mocks the canonical executor to break its historical
    # circular import. Load the real implementation without leaking a module
    # replacement into unrelated tests.
    path = Path(__file__).parents[1] / "packages/harness/deerflow/subagents/executor.py"
    spec = importlib.util.spec_from_file_location("deerflow.subagents._employee_integration_test", path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)

    _, config, _, _ = stores
    executor = object.__new__(module.SubagentExecutor)
    executor.config = SimpleNamespace(skills=None, name="general")
    executor.app_config = config
    executor.user_id = "bob"
    executor.thread_id = "thread"
    executor.trace_id = "test"
    skills = await executor._load_skills()
    assert "owner-authority" in skills[0].skill_file.read_text()


@pytest.mark.parametrize("container_path", ["/mnt/skills", "/opt/company-skills"])
def test_aio_mount_replaces_all_viewer_skill_roots(stores, admitted_skill, monkeypatch, container_path):
    from deerflow.community.aio_sandbox.aio_sandbox_provider import AioSandboxProvider

    paths, config, _, viewer = stores
    config.skills.container_path = container_path
    monkeypatch.setenv("DEER_FLOW_HOST_BASE_DIR", str(paths.base_dir))
    monkeypatch.setattr("deerflow.community.aio_sandbox.aio_sandbox_provider.get_paths", lambda: paths)
    monkeypatch.setattr("deerflow.community.aio_sandbox.aio_sandbox_provider.get_app_config", lambda: config)
    mounts = AioSandboxProvider._get_skills_mounts(user_id="bob", thread_id="thread")
    assert len(mounts) == 1
    host, container, read_only = mounts[0]
    assert container == container_path and read_only is True
    assert Path(host) != viewer.get_user_custom_root()
    assert "owner-authority" in (Path(host) / "custom" / "analysis" / "SKILL.md").read_text()


def test_publication_manifest_cannot_be_a_link(stores, tmp_path):
    _, config, owner, _ = stores
    skill(owner.get_user_custom_root(), "analysis", "owner-version")
    target, revision = publish(stores, ["analysis"])
    original = target / "skills.manifest.json"
    displaced = tmp_path / "outside-manifest.json"
    original.rename(displaced)
    try:
        original.symlink_to(displaced)
    except OSError:
        pytest.skip("Local account cannot create symlinks; required on Linux release tests")
    storage = PublishedAgentSkillStorage("bob", "thread-one", publication=target, revision=revision, app_config=config)
    with pytest.raises(ValueError, match="manifest"):
        storage.load_skills()


def test_remote_backend_carries_bounded_projection_and_requires_matching_receipt(stores, admitted_skill, monkeypatch):
    from deerflow.community.aio_sandbox.remote_backend import RemoteSandboxBackend

    requests_seen = []

    def post(url, *, json, **kwargs):
        requests_seen.append(json)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"sandbox_url": "http://sandbox:8080", "employee_skill_projection": json.get("employee_skill_projection")})

    monkeypatch.setattr("deerflow.community.aio_sandbox.remote_backend.requests.post", post)
    monkeypatch.setattr("deerflow.community.aio_sandbox.remote_backend.user_should_see_legacy_skills", lambda *a, **kw: pytest.fail("Published employee must not consult viewer/global catalog"))
    backend = RemoteSandboxBackend("http://provisioner", api_key="server-key")
    backend.create("thread", "sandbox", user_id="bob")
    assert len(requests_seen[0]["employee_skill_projection"]) == 24
    assert requests_seen[0]["include_legacy_skills"] is False
    monkeypatch.setattr("deerflow.community.aio_sandbox.remote_backend.requests.post", lambda *a, **kw: SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"sandbox_url": "http://old:8080"}))
    with pytest.raises(RuntimeError, match="projection"):
        backend.create("thread", "sandbox", user_id="bob")


def test_sandbox_file_checks_use_publication_not_viewer_skill_state(stores, admitted_skill, monkeypatch):
    from deerflow.sandbox import tools

    monkeypatch.setattr(tools, "_get_skills_container_path", lambda: "/mnt/skills")
    monkeypatch.setattr("deerflow.skills.storage.get_or_new_user_skill_storage", lambda *a, **kw: pytest.fail("Viewer catalog must not participate"))
    assert tools._is_disabled_skill_path("/mnt/skills/custom/analysis/SKILL.md", user_id="bob", thread_id="thread") is False
    assert tools._is_disabled_skill_path("/mnt/skills/public/analysis/SKILL.md", user_id="bob", thread_id="thread") is True
    assert tools._is_disabled_skill_path("/mnt/skills/custom/not-selected/SKILL.md", user_id="bob", thread_id="thread") is True
    resolved = tools._resolve_skills_path("/mnt/skills/custom/analysis/references/guide.txt", user_id="bob", thread_id="thread")
    assert Path(resolved).read_text() == "owner-authority"


def test_withdrawal_blocks_even_cached_sandbox_tools(stores, admitted_skill, monkeypatch):
    from deerflow.sandbox import tools

    paths, _, _, _ = stores
    TeamPublicationStore(paths).unpublish("employee", owner="alice", expected_revision=admitted_skill.revision)
    runtime = SimpleNamespace(context={"thread_id": "thread", "user_id": "bob"}, config={}, state={"sandbox": {"sandbox_id": "cached"}})
    monkeypatch.setattr(tools, "get_sandbox_provider", lambda: SimpleNamespace(get=lambda _: pytest.fail("No cached sandbox after withdrawal")))
    with pytest.raises(FileNotFoundError, match="withdrawn"):
        tools.ensure_sandbox_initialized(runtime)


def test_local_provider_replaces_static_and_personal_skill_mounts(stores, admitted_skill, monkeypatch):
    from deerflow.sandbox.local.local_sandbox import PathMapping
    from deerflow.sandbox.local.local_sandbox_provider import LocalSandboxProvider

    _, config, _, viewer = stores
    monkeypatch.setattr(LocalSandboxProvider, "_setup_path_mappings", lambda self: [PathMapping("/mnt/skills/public", str(viewer.get_user_custom_root()), True)])
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)
    provider = LocalSandboxProvider()
    sandbox_id = provider.acquire("thread", user_id="bob")
    sandbox = provider.get(sandbox_id)
    mounts = [item for item in sandbox.path_mappings if item.container_path.startswith("/mnt/skills")]
    assert len(mounts) == 1 and mounts[0].container_path == "/mnt/skills" and mounts[0].read_only
    provider.validate_employee_skills(sandbox_id, "thread", user_id="bob")
    sandbox.path_mappings = [PathMapping("/mnt/skills", str(viewer.get_user_custom_root()), True)]
    with pytest.raises(RuntimeError, match="mount"):
        provider.validate_employee_skills(sandbox_id, "thread", user_id="bob")


def test_cached_sandbox_requires_employee_mount_confirmation(stores, admitted_skill, monkeypatch):
    from deerflow.sandbox import tools

    runtime = SimpleNamespace(context={"thread_id": "thread", "user_id": "bob"}, config={}, state={"sandbox": {"sandbox_id": "cached"}})

    def deny(*a, **kw):
        raise RuntimeError("Employee mount mismatch")

    provider = SimpleNamespace(get_scoped=lambda *_args, **_kwargs: object(), validate_employee_skills=deny)
    monkeypatch.setattr(tools, "get_sandbox_provider", lambda: provider)
    with pytest.raises(RuntimeError, match="mount"):
        tools.ensure_sandbox_initialized(runtime)


def test_aio_cached_acquire_verifies_backend_projection(stores, admitted_skill, monkeypatch):
    from contextlib import nullcontext

    from deerflow.community.aio_sandbox.aio_sandbox_provider import AioSandboxProvider

    provider = object.__new__(AioSandboxProvider)
    provider._acquire_serializer = SimpleNamespace(hold=lambda _: nullcontext())
    monkeypatch.setattr(provider, "_acquire_internal", lambda *a, **kw: provider._sandbox_id_for_thread("thread", "bob"))
    seen = []
    provider._backend = SimpleNamespace(validate_employee_skills=lambda *a, **kw: seen.append((a, kw)))
    provider.acquire("thread", user_id="bob")
    assert len(seen) == 1
    assert seen[0][1]["user_id"] == "bob"


def test_remote_reuse_rechecks_projection_identity(stores, admitted_skill, monkeypatch):
    from deerflow.community.aio_sandbox.remote_backend import RemoteSandboxBackend

    seen = []

    def verify(*a, **kw):
        seen.append((a, kw))
        raise RuntimeError("Existing mount mismatch")

    backend = RemoteSandboxBackend("http://provisioner", api_key="key")
    monkeypatch.setattr(backend, "_provisioner_create", verify)
    with pytest.raises(RuntimeError, match="mount"):
        backend.validate_employee_skills("sandbox", "thread", user_id="bob")
    assert len(seen) == 1

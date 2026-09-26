"""An existing employee conversation must never silently switch publication."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from deerflow.config.paths import Paths
from deerflow.persistence.agents.publications import TeamPublicationStore
from deerflow.persistence.agents.runtime import EmployeePublicationBindings


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    paths = Paths(base_dir=tmp_path)
    monkeypatch.setattr("deerflow.config.agents_config.get_paths", lambda: paths)
    return paths, TeamPublicationStore(paths), EmployeePublicationBindings(paths)


def publish(store, description="first", previous=None):
    return store.publish("reports", owner="alice", config={"description": description, "skills": []}, soul=description + " soul", avatar=None, expected_revision=previous)


def test_existing_thread_keeps_one_coherent_config_soul_snapshot(runtime):
    _, store, bindings = runtime
    first = publish(store)
    admitted = bindings.admit("thread-1", user_id="bob", name="reports", source="team")
    assert admitted.revision == first.revision
    second = publish(store, "second", first.revision)
    old = bindings.admit("thread-1", user_id="bob", name="reports", source="team")
    new = bindings.admit("thread-2", user_id="bob", name="reports", source="team")
    assert (old.config["description"], old.soul) == ("first", "first soul")
    assert new.revision == second.revision


def test_public_binding_metadata_reports_update_without_exposing_definition(runtime):
    _, store, bindings = runtime
    first = publish(store)
    bindings.admit("thread", user_id="bob", name="reports", source="team")
    second = publish(store, "second", first.revision)
    info = bindings.describe("thread", user_id="bob")
    assert info == {"bound": True, "name": "reports", "revision": first.revision, "current_revision": second.revision, "update_available": True, "knowledge_mode": "inherit"}
    assert bindings.describe("thread", user_id="unrelated") == {"bound": False}
    assert bindings.read("thread", user_id="bob").revision == first.revision
    store.unpublish("reports", owner="alice", expected_revision=second.revision)
    with pytest.raises(FileNotFoundError):
        bindings.describe("thread", user_id="bob")


@pytest.mark.parametrize("name,source", [("other", "team"), ("reports", "personal"), (None, "personal")])
def test_bound_thread_rejects_identity_switch(runtime, name, source):
    _, store, bindings = runtime
    publish(store)
    bindings.admit("thread-1", user_id="bob", name="reports", source="team")
    with pytest.raises(ValueError, match="different employee"):
        bindings.admit("thread-1", user_id="bob", name=name, source=source)


def test_owner_and_thread_are_independent(runtime):
    _, store, bindings = runtime
    first = publish(store)
    bindings.admit("thread-1", user_id="bob", name="reports", source="team")
    second = publish(store, "second", first.revision)
    assert bindings.admit("thread-1", user_id="charlie", name="reports", source="team").revision == second.revision


def test_withdrawal_blocks_next_admission_without_deleting_pin(runtime):
    paths, store, bindings = runtime
    first = publish(store)
    bindings.admit("thread-1", user_id="bob", name="reports", source="team")
    target = paths.thread_dir("thread-1", user_id="bob") / "employee-publication.json"
    before = target.read_bytes()
    store.unpublish("reports", owner="alice", expected_revision=first.revision)
    with pytest.raises(FileNotFoundError):
        bindings.admit("thread-1", user_id="bob", name="reports", source="team")
    assert target.read_bytes() == before


def test_corrupt_pin_never_rebinds_to_latest(runtime):
    paths, store, bindings = runtime
    publish(store)
    bindings.admit("thread-1", user_id="bob", name="reports", source="team")
    target = paths.thread_dir("thread-1", user_id="bob") / "employee-publication.json"
    target.write_text('{"revision":"forged"}', encoding="utf-8")
    with pytest.raises(ValueError):
        bindings.admit("thread-1", user_id="bob", name="reports", source="team")
    assert target.read_text() == '{"revision":"forged"}'


def test_pin_is_outside_downloadable_user_data_and_contains_no_definition(runtime):
    paths, store, bindings = runtime
    first = publish(store)
    bindings.admit("thread-1", user_id="bob", name="reports", source="team")
    target = paths.thread_dir("thread-1", user_id="bob") / "employee-publication.json"
    document = json.loads(target.read_text())
    assert document["revision"] == first.revision
    assert set(document) == {"schema", "name", "source", "publication_id", "revision", "digest"}
    assert not (target.parent / "user-data" / target.name).exists()


def test_concurrent_first_admission_agrees(runtime):
    _, store, bindings = runtime
    first = publish(store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: bindings.admit("thread-1", user_id="bob", name="reports", source="team"), range(2)))
    assert [row.revision for row in results] == [first.revision, first.revision]


def test_legacy_employee_pin_preserves_original_bytes(runtime):
    paths, store, bindings = runtime
    root = paths.team_agent_dir("reports")
    root.mkdir(parents=True)
    config = b"# Original retained\ndescription: legacy\npublished_by_user_id: alice\nfuture_field: keep\n"
    (root / "config.yaml").write_bytes(config)
    (root / "SOUL.md").write_text("legacy soul", encoding="utf-8")
    first = bindings.admit("thread-1", user_id="bob", name="reports", source="team")
    publish(store, "second", first.revision)
    restored = bindings.admit("thread-1", user_id="bob", name="reports", source="team")
    assert restored.soul == "legacy soul"
    assert restored.config["future_field"] == "keep"
    assert (root / "config.yaml").read_bytes() == config


def test_ordinary_chat_does_not_create_publication_state(runtime):
    paths, _, bindings = runtime
    assert bindings.admit("thread-1", user_id="bob", name=None, source=None) is None
    assert not paths.thread_dir("thread-1", user_id="bob").exists()


def test_continue_without_client_employee_identity_restores_pinned_version(runtime):
    _, store, bindings = runtime
    first = publish(store)
    bindings.admit("parent", user_id="bob", name="reports", source="team")
    publish(store, "new", first.revision)
    resumed = bindings.admit("parent", user_id="bob", name=None, source=None)
    assert resumed.revision == first.revision
    assert resumed.name == "reports"


def test_branch_copies_exact_pin_not_current_head_or_skill_projection(runtime):
    paths, store, bindings = runtime
    first = publish(store)
    bindings.admit("parent", user_id="bob", name="reports", source="team")
    latest = publish(store, "new", first.revision)
    assert latest.revision != first.revision
    branched = bindings.branch("parent", "child", user_id="bob")
    assert branched.revision == first.revision
    assert bindings.read("child", user_id="bob").revision == first.revision
    assert not (paths.thread_dir("child", user_id="bob") / "agent-skills").exists()
    assert not (paths.thread_dir("child", user_id="bob") / "user-data").exists()
    assert bindings.branch("parent", "child", user_id="bob").revision == first.revision


def test_branch_does_not_replace_existing_pin_or_copy_unavailable_parent(runtime):
    paths, store, bindings = runtime
    first = publish(store)
    bindings.admit("parent", user_id="bob", name="reports", source="team")
    latest = publish(store, "new", first.revision)
    bindings.admit("child", user_id="bob", name="reports", source="team")
    with pytest.raises(ValueError, match="different"):
        bindings.branch("parent", "child", user_id="bob")
    assert bindings.read("child", user_id="bob").revision == latest.revision
    assert bindings.branch("parent", "stranger-child", user_id="stranger") is None
    assert not paths.thread_dir("stranger-child", user_id="stranger").exists()
    store.unpublish("reports", owner="alice", expected_revision=latest.revision)
    with pytest.raises(FileNotFoundError):
        bindings.branch("parent", "withdrawn-child", user_id="bob")
    assert not paths.thread_dir("withdrawn-child", user_id="bob").exists()


@pytest.mark.asyncio
async def test_gateway_minimal_continue_rehydrates_identity_before_resource_checks(runtime, monkeypatch):
    from app.gateway.agent_publications import admit_agent_publication

    paths, store, bindings = runtime
    first = publish(store)
    bindings.admit("parent", user_id="bob", name="reports", source="team")
    monkeypatch.setattr("app.gateway.agent_publications.get_paths", lambda: paths)
    config = {"context": {}, "configurable": {}}
    result = await admit_agent_publication(config, thread_id="parent", user_id="bob", commit=False)
    assert result.revision == first.revision
    for values in config.values():
        assert values["agent_name"] == "reports"
        assert values["agent_source"] == "team"
        assert values["agent_publication_revision"] == first.revision


def test_legacy_unspecified_source_resolves_team_once(runtime):
    paths, store, bindings = runtime
    first = publish(store)
    assert bindings.admit("thread-1", user_id="bob", name="reports", source=None).revision == first.revision
    personal = paths.user_agent_dir("bob", "reports")
    personal.mkdir(parents=True)
    (personal / "config.yaml").write_text("description: later namesake\n", encoding="utf-8")
    second = publish(store, "second", first.revision)
    assert second.revision != first.revision
    assert bindings.admit("thread-1", user_id="bob", name="reports", source=None).revision == first.revision
    # Compatibility for NEW personal conversations remains unchanged.
    assert bindings.admit("thread-2", user_id="bob", name="reports", source=None) is None


def test_implicit_withdrawn_team_does_not_fall_back_to_legacy(runtime):
    paths, store, bindings = runtime
    first = publish(store)
    store.unpublish("reports", owner="alice", expected_revision=first.revision)
    legacy = paths.agent_dir("reports")
    legacy.mkdir(parents=True)
    (legacy / "config.yaml").write_text("description: legacy namesake\n", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        bindings.admit("thread-1", user_id="bob", name="reports", source=None)


def test_resource_validation_preview_does_not_bind_a_rejected_run(runtime):
    paths, store, bindings = runtime
    first = publish(store)
    preview = bindings.admit("thread-1", user_id="bob", name="reports", source="team", commit=False)
    assert preview.revision == first.revision
    assert not paths.thread_dir("thread-1", user_id="bob").exists()


def test_admission_commit_cannot_switch_revision_after_scope_validation(runtime):
    from deerflow.persistence.agents.publications import PublicationConflict

    paths, store, bindings = runtime
    first = publish(store)
    preview = bindings.admit("thread-1", user_id="bob", name="reports", source="team", commit=False)
    publish(store, "second", first.revision)
    with pytest.raises(PublicationConflict):
        bindings.admit("thread-1", user_id="bob", name="reports", source="team", expected_revision=preview.revision)
    assert not (paths.thread_dir("thread-1", user_id="bob") / "employee-publication.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", [None, "team"])
async def test_bootstrap_rejection_does_not_leave_publication_receipt(runtime, monkeypatch, source):
    from fastapi import HTTPException

    from app.gateway.agent_publications import admit_agent_publication

    paths, store, _ = runtime
    publish(store)
    monkeypatch.setattr("app.gateway.agent_publications.get_paths", lambda: paths)
    config = {"context": {"agent_name": "reports", "agent_source": source, "is_bootstrap": True}}
    with pytest.raises(HTTPException) as error:
        await admit_agent_publication(config, thread_id="thread-1", user_id="bob")
    assert error.value.status_code == 409
    assert not (paths.thread_dir("thread-1", user_id="bob") / "employee-publication.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("key,value", [("agent_name", "different"), ("agent_source", "personal"), ("is_bootstrap", True)])
async def test_conflicting_runtime_identity_sections_fail_before_pin(runtime, monkeypatch, key, value):
    from fastapi import HTTPException

    from app.gateway.agent_publications import admit_agent_publication

    paths, store, _ = runtime
    publish(store)
    monkeypatch.setattr("app.gateway.agent_publications.get_paths", lambda: paths)
    config = {section: {"agent_name": "reports", "agent_source": "team", "is_bootstrap": False} for section in ("context", "configurable")}
    config["configurable"][key] = value
    with pytest.raises(HTTPException) as error:
        await admit_agent_publication(config, thread_id="thread-1", user_id="bob")
    assert error.value.status_code == 409
    assert not (paths.thread_dir("thread-1", user_id="bob") / "employee-publication.json").exists()


@pytest.mark.asyncio
async def test_gateway_admission_ignores_browser_revision_and_uses_trusted_owner(runtime, monkeypatch):
    from app.gateway.agent_publications import admit_agent_publication

    paths, store, _ = runtime
    first = publish(store)
    monkeypatch.setattr("app.gateway.agent_publications.get_paths", lambda: paths)
    config = {section: {"agent_name": "reports", "agent_source": "team", "agent_publication_revision": "forged", "agent_publication_id": "forged"} for section in ("context", "configurable")}
    result = await admit_agent_publication(config, thread_id="thread-1", user_id="bob")
    assert result.revision == first.revision
    for section in ("context", "configurable"):
        assert config[section]["agent_publication_revision"] == first.revision
        assert config[section]["agent_publication_id"] == first.publication_id


@pytest.mark.asyncio
async def test_gateway_unavailable_publication_is_visible_error_not_rebind(runtime, monkeypatch):
    from fastapi import HTTPException

    from app.gateway.agent_publications import admit_agent_publication

    paths, store, bindings = runtime
    first = publish(store)
    bindings.admit("thread-1", user_id="bob", name="reports", source="team")
    store.unpublish("reports", owner="alice", expected_revision=first.revision)
    monkeypatch.setattr("app.gateway.agent_publications.get_paths", lambda: paths)
    with pytest.raises(HTTPException) as error:
        await admit_agent_publication({"context": {"agent_name": "reports", "agent_source": "team"}}, thread_id="thread-1", user_id="bob")
    assert error.value.status_code == 409


def test_prompt_uses_supplied_snapshot_soul_without_reloading_mutable_head(monkeypatch):
    from deerflow.agents.lead_agent import prompt

    monkeypatch.setattr(prompt, "load_agent_soul", lambda *a, **kw: pytest.fail("must not reload head"))
    assert prompt.get_agent_soul("reports", source="team", soul_override="first </soul>") == "<soul>\nfirst &lt;/soul&gt;\n</soul>\n"

"""The real edit/read/publish routes share one coherent private definition."""

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, UploadFile
from PIL import Image

from app.gateway.routers import agents
from deerflow.config.agent_storage_config import AgentStorageConfig
from deerflow.config.agents_api_config import load_agents_api_config_from_dict
from deerflow.config.app_config import AppConfig, reset_app_config, set_app_config
from deerflow.config.paths import Paths
from deerflow.config.sandbox_config import SandboxConfig
from deerflow.persistence.agents.file import FileAgentStore


@pytest.fixture(params=["file", "sql"])
def environment(tmp_path, monkeypatch, request):
    paths = Paths(base_dir=tmp_path)
    store = FileAgentStore()
    if request.param == "sql":
        from sqlalchemy import create_engine

        from deerflow.persistence.agents.model import AgentRow
        from deerflow.persistence.agents.sql import SqlAgentStore
        from deerflow.persistence.base import Base

        monkeypatch.setattr("deerflow.persistence.agents.sql.get_paths", lambda: paths)
        url = "sqlite:///" + (tmp_path / "agents.db").as_posix()
        engine = create_engine(url)
        Base.metadata.create_all(engine, tables=[AgentRow.__table__])
        engine.dispose()
        store = SqlAgentStore(url)
    for module in (agents, __import__("deerflow.config.agents_config", fromlist=["get_paths"])):
        monkeypatch.setattr(module, "get_paths", lambda: paths)
    monkeypatch.setattr(agents, "get_effective_user_id", lambda: "alice")
    monkeypatch.setattr(agents, "get_current_user", lambda: SimpleNamespace(id="alice", is_disabled=False))
    monkeypatch.setattr("app.gateway.employee_knowledge.EmployeeKnowledgeBroker.authorize", AsyncMock())
    monkeypatch.setattr(agents, "get_agent_store", lambda: store)
    monkeypatch.setattr("deerflow.persistence.agents.get_agent_store", lambda: store)
    load_agents_api_config_from_dict({"enabled": True})
    set_app_config(AppConfig(models=[], sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"), agent_storage=AgentStorageConfig(backend="db" if request.param == "sql" else "file")))
    store.create("reports", {"description": "original", "skills": [], "future_binding": {"keep": [1, 2]}}, "Original SOUL", user_id="alice")
    yield store, paths
    reset_app_config()
    load_agents_api_config_from_dict({})


def avatar_upload(color="red"):
    payload = io.BytesIO()
    Image.new("RGB", (16, 16), color=color).save(payload, format="PNG")
    payload.seek(0)
    return UploadFile(file=payload, filename="portrait.png")


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", [None, ""])
async def test_employee_delete_requires_review_without_cancelling_memory(environment, monkeypatch, revision):
    store, _ = environment
    before = store.read_definition("reports", user_id="alice")
    monkeypatch.setattr(agents, "_cancel_pending_memory_for_agent", lambda *a: pytest.fail("unreviewed memory cancellation"))
    with pytest.raises(HTTPException) as exc:
        await agents.delete_agent("reports", expected_draft_revision=revision)
    assert exc.value.status_code == 428
    assert store.read_definition("reports", user_id="alice") == before


@pytest.mark.asyncio
async def test_employee_delete_rechecks_revision_at_storage_boundary(environment, monkeypatch):
    store, _ = environment
    before = store.read_definition("reports", user_id="alice")
    original = store.delete_definition

    def racing_delete(*args, **kwargs):
        store.patch_definition("reports", {"description": "winner"}, None, user_id="alice", expected_revision=before.revision)
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "delete_definition", racing_delete)
    monkeypatch.setattr(agents, "_cancel_pending_memory_for_agent", lambda *a: pytest.fail("stale memory cancellation"))
    with pytest.raises(HTTPException) as exc:
        await agents.delete_agent("reports", expected_draft_revision=before.revision)
    assert exc.value.status_code == 409
    assert store.read_definition("reports", user_id="alice").config["description"] == "winner"


@pytest.mark.asyncio
async def test_reviewed_employee_delete_preserves_publication_and_other_users(environment, monkeypatch):
    store, paths = environment
    store.create("reports", {"description": "Bob"}, "Bob soul", user_id="bob")
    before = store.read_definition("reports", user_id="alice")
    published = agents.TeamPublicationStore(paths).publish("reports", owner="alice", config={"name": "reports", "skills": []}, soul=before.soul, avatar=None, expected_revision=None)
    calls = []
    monkeypatch.setattr(agents, "_cancel_pending_memory_for_agent", lambda *a: calls.append(a))
    await agents.delete_agent("reports", expected_draft_revision=before.revision)
    with pytest.raises(FileNotFoundError):
        store.read_definition("reports", user_id="alice")
    assert store.read_definition("reports", user_id="bob").soul == "Bob soul"
    assert agents.TeamPublicationStore(paths).head("reports").revision == published.revision
    assert calls == [("reports", "alice"), ("reports", "alice")]


@pytest.mark.asyncio
async def test_avatar_update_has_cas_revision_and_keeps_legacy_bytes(environment):
    store, paths = environment
    root = paths.user_agent_dir("alice", "reports")
    root.mkdir(parents=True, exist_ok=True)
    (root / "avatar.webp").write_bytes(b"legacy-portrait")
    before = store.read_definition("reports", user_id="alice")
    updated = await agents.upload_agent_avatar("reports", avatar_upload(), expected_draft_revision=before.revision)
    after = store.read_definition("reports", user_id="alice")
    assert updated.draft_revision == after.revision != before.revision
    assert updated.avatar_url and "?v=" in updated.avatar_url
    assert after.soul == before.soul
    assert after.config["future_binding"] == before.config["future_binding"]
    assert (root / "avatar.webp").read_bytes() == b"legacy-portrait"
    avatar = await agents.get_agent_avatar("reports")
    assert avatar.path != root / "avatar.webp"
    assert Image.open(avatar.path).format == "WEBP"
    with pytest.raises(HTTPException) as exc:
        await agents.upload_agent_avatar("reports", avatar_upload("blue"), expected_draft_revision=before.revision)
    assert exc.value.status_code == 409
    assert store.read_definition("reports", user_id="alice") == after
    with pytest.raises(HTTPException) as exc:
        await agents.delete_agent_avatar("reports", expected_draft_revision=before.revision)
    assert exc.value.status_code == 409
    removed = await agents.delete_agent_avatar("reports", expected_draft_revision=after.revision)
    assert removed.draft_revision != after.revision and removed.avatar_url is None
    with pytest.raises(HTTPException) as exc:
        await agents.get_agent_avatar("reports")
    assert exc.value.status_code == 404
    assert (root / "avatar.webp").read_bytes() == b"legacy-portrait"


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["upload", "remove"])
async def test_avatar_without_revision_is_rejected_without_writes(environment, operation):
    store, paths = environment
    before = store.read_definition("reports", user_id="alice")
    with pytest.raises(HTTPException) as exc:
        if operation == "upload":
            await agents.upload_agent_avatar("reports", avatar_upload(), expected_draft_revision=None)
        else:
            await agents.delete_agent_avatar("reports", expected_draft_revision=None)
    assert exc.value.status_code == 428
    assert store.read_definition("reports", user_id="alice") == before
    assert not (paths.user_agent_dir("alice", "reports") / ".avatar-assets").exists()


@pytest.mark.asyncio
async def test_legacy_avatar_clear_leaves_downgrade_marker_and_original_bytes(environment):
    store, paths = environment
    root = paths.user_agent_dir("alice", "reports")
    root.mkdir(parents=True, exist_ok=True)
    (root / "avatar.webp").write_bytes(b"legacy")
    before = store.read_definition("reports", user_id="alice")
    cleared = await agents.delete_agent_avatar("reports", expected_draft_revision=before.revision)
    assert cleared.avatar_url is None
    assert (root / ".avatar-assets" / "FORMAT-v1").read_bytes() == b"avatar-reference-v1\n"
    assert (root / "avatar.webp").read_bytes() == b"legacy"


@pytest.mark.asyncio
async def test_avatar_transfer_race_cannot_overwrite_newer_draft(environment, monkeypatch):
    store, _ = environment
    before = store.read_definition("reports", user_id="alice")
    encode = agents._encode_custom_avatar

    def concurrent_edit(*args):
        result = encode(*args)
        store.patch_definition("reports", {"description": "newer"}, None, user_id="alice", expected_revision=before.revision)
        return result

    monkeypatch.setattr(agents, "_encode_custom_avatar", concurrent_edit)
    with pytest.raises(HTTPException) as exc:
        await agents.upload_agent_avatar("reports", avatar_upload(), expected_draft_revision=before.revision)
    assert exc.value.status_code == 409
    assert (await agents.get_agent("reports", source="personal")).avatar_url is None
    assert store.read_definition("reports", user_id="alice").config["description"] == "newer"


@pytest.mark.asyncio
async def test_published_avatar_is_exact_reviewed_asset_not_latest_upload(environment):
    store, paths = environment
    before = store.read_definition("reports", user_id="alice")
    first = await agents.upload_agent_avatar("reports", avatar_upload(), expected_draft_revision=before.revision)
    personal_avatar = await agents.get_agent_avatar("reports")
    first_bytes = personal_avatar.path.read_bytes()
    published = await agents.publish_agent("reports", agents.AgentPublishRequest(expected_draft_revision=first.draft_revision))
    await agents.upload_agent_avatar("reports", avatar_upload("blue"), expected_draft_revision=first.draft_revision)
    snapshot = agents.TeamPublicationStore(paths).read("reports")
    assert snapshot.revision == published.publication_revision
    assert (snapshot.directory / "avatar.webp").read_bytes() == first_bytes


@pytest.mark.asyncio
async def test_detail_revision_binds_config_and_soul(environment):
    store, _ = environment
    response = await agents.get_agent("reports", source="personal")
    before = store.read_definition("reports", user_id="alice")
    assert response.draft_revision == before.revision
    assert response.soul == before.soul
    updated = await agents.update_agent("reports", agents.AgentUpdateRequest(description="updated", soul="Updated SOUL", expected_draft_revision=before.revision))
    after = store.read_definition("reports", user_id="alice")
    assert updated.draft_revision == after.revision != before.revision
    assert updated.soul == "Updated SOUL"
    assert after.config["future_binding"] == {"keep": [1, 2]}


@pytest.mark.asyncio
async def test_stale_form_is_409_and_never_overwrites_newer_changes(environment):
    store, _ = environment
    before = store.read_definition("reports", user_id="alice")
    winner = store.patch_definition("reports", {"description": "winner"}, "winner", user_id="alice", expected_revision=before.revision)
    with pytest.raises(HTTPException) as exc:
        await agents.update_agent("reports", agents.AgentUpdateRequest(description="loser", expected_draft_revision=before.revision))
    assert exc.value.status_code == 409
    assert store.read_definition("reports", user_id="alice") == winner


@pytest.mark.asyncio
@pytest.mark.parametrize("revision", [None, ""])
async def test_missing_form_revision_cannot_bypass_concurrency_guard(environment, revision):
    store, _ = environment
    before = store.read_definition("reports", user_id="alice")
    with pytest.raises(HTTPException) as exc:
        await agents.update_agent("reports", agents.AgentUpdateRequest(description="blind overwrite", expected_draft_revision=revision))
    assert exc.value.status_code == 428
    assert store.read_definition("reports", user_id="alice") == before


@pytest.mark.asyncio
async def test_concurrent_edit_during_resource_validation_is_409(environment, monkeypatch):
    store, _ = environment
    before = store.read_definition("reports", user_id="alice")

    def validate(value, **kwargs):
        store.patch_definition("reports", {"description": "another editor"}, None, user_id="alice", expected_revision=before.revision)
        return value

    monkeypatch.setattr(agents, "validate_agent_skills", validate)
    with pytest.raises(HTTPException) as exc:
        await agents.update_agent("reports", agents.AgentUpdateRequest(skills=[], description="stale", expected_draft_revision=before.revision))
    assert exc.value.status_code == 409
    assert store.get("reports", user_id="alice").description == "another editor"


@pytest.mark.asyncio
async def test_null_selection_does_not_erase_unknown_fields(environment, monkeypatch):
    store, _ = environment
    monkeypatch.setattr(agents, "validate_agent_skills", lambda value, **kwargs: value)
    before = store.read_definition("reports", user_id="alice")
    await agents.update_agent("reports", agents.AgentUpdateRequest(skills=None, expected_draft_revision=before.revision))
    after = store.read_definition("reports", user_id="alice")
    assert after.config["skills"] is None
    assert after.config["future_binding"] == before.config["future_binding"]


@pytest.mark.asyncio
async def test_publish_reads_one_raw_definition_without_separate_soul_lookup(environment, monkeypatch):
    store, paths = environment
    before = store.read_definition("reports", user_id="alice")
    monkeypatch.setattr(agents, "load_agent_soul", lambda *a, **kw: pytest.fail("split SOUL read"))
    response = await agents.publish_agent("reports", agents.AgentPublishRequest(expected_draft_revision=before.revision))
    snapshot = agents.TeamPublicationStore(paths).read("reports")
    assert snapshot.soul == before.soul
    assert "future_binding" not in snapshot.config
    assert store.read_definition("reports", user_id="alice").config["future_binding"] == before.config["future_binding"]
    assert snapshot.config["resource_policy_version"] == 1
    assert response.publication_revision == snapshot.revision


@pytest.mark.asyncio
async def test_publish_rejects_stale_draft_without_creating_publication(environment):
    store, paths = environment
    before = store.read_definition("reports", user_id="alice")
    store.patch_definition("reports", {"description": "new"}, None, user_id="alice", expected_revision=before.revision)
    with pytest.raises(HTTPException) as exc:
        await agents.publish_agent("reports", agents.AgentPublishRequest(expected_draft_revision=before.revision))
    assert exc.value.status_code == 409
    assert not agents.TeamPublicationStore(paths).has_record("reports")


@pytest.mark.asyncio
async def test_publish_requires_reviewed_draft_revision(environment):
    with pytest.raises(HTTPException) as exc:
        await agents.publish_agent("reports", agents.AgentPublishRequest())
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_restore_old_revision_resets_new_managed_fields_but_keeps_private_data(environment, monkeypatch):
    from app.gateway.routers import employee_changes

    store, paths = environment
    monkeypatch.setattr(employee_changes, "get_paths", lambda: paths)
    monkeypatch.setattr(employee_changes, "get_agent_store", lambda: store)
    user = SimpleNamespace(id="alice", is_disabled=False)
    before = store.read_definition("reports", user_id="alice")
    published = agents.TeamPublicationStore(paths).publish("reports", owner="alice", config={"name": "reports", "description": "old", "skills": []}, soul="old soul", avatar=None, expected_revision=None)
    current = store.patch_definition("reports", {"thinking_enabled": True, "tags": ["later"]}, None, user_id="alice", expected_revision=before.revision)
    result = await employee_changes.restore_publication_draft("reports", published.revision, employee_changes.VersionRestore(expected_draft_revision=current.revision), user=user)
    restored = store.read_definition("reports", user_id="alice")
    assert restored.config["thinking_enabled"] is None
    assert restored.config["tags"] == [] and restored.soul == "old soul"
    assert restored.config["future_binding"] == before.config["future_binding"]
    assert result["publication_changed"] is False
    assert agents.TeamPublicationStore(paths).read("reports").revision == published.revision


@pytest.mark.asyncio
async def test_proposal_api_is_creator_only_and_conflicting_approval_keeps_winner(environment, monkeypatch):
    from app.gateway.routers import employee_changes
    from deerflow.persistence.agents.proposals import EmployeeProposalStore

    store, paths = environment
    monkeypatch.setattr(employee_changes, "get_paths", lambda: paths)
    monkeypatch.setattr(employee_changes, "get_agent_store", lambda: store)
    before = store.read_definition("reports", user_id="alice")
    proposal = EmployeeProposalStore(paths).create("reports", "alice", before, {"description": "proposal"}, None)
    with pytest.raises(HTTPException) as exc:
        await employee_changes.get_proposal("reports", proposal["id"], user=SimpleNamespace(id="bob", is_disabled=False))
    assert exc.value.status_code == 404
    winner = store.patch_definition("reports", {"description": "winner"}, None, user_id="alice", expected_revision=before.revision)
    with pytest.raises(HTTPException) as exc:
        await employee_changes.decide_proposal("reports", proposal["id"], employee_changes.ProposalDecision(approve=True, expected_draft_revision=before.revision), user=SimpleNamespace(id="alice", is_disabled=False))
    assert exc.value.status_code == 409
    assert store.read_definition("reports", user_id="alice") == winner

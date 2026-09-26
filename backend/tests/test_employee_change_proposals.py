import pytest

from deerflow.config.paths import Paths
from deerflow.persistence.agents.drafts import AgentDefinition


def test_proposal_does_not_edit_definition_and_preserves_unknown_fields(tmp_path):
    from deerflow.persistence.agents.proposals import EmployeeProposalStore

    paths = Paths(base_dir=tmp_path)
    definition = AgentDefinition.from_parts("employee", {"description": "old", "future": "keep"}, "soul")
    store = EmployeeProposalStore(paths)
    proposal = store.create("employee", "alice", definition, {"description": "new"}, None)
    assert proposal["state"] == "pending"
    assert proposal["expected_draft_revision"] == definition.revision
    assert definition.config["description"] == "old" and definition.config["future"] == "keep"
    assert store.get("employee", "alice", proposal["id"]) == proposal
    with pytest.raises(FileNotFoundError):
        store.get("employee", "bob", proposal["id"])


def test_proposal_refuses_identity_and_private_field_changes(tmp_path):
    from deerflow.persistence.agents.proposals import EmployeeProposalStore

    definition = AgentDefinition.from_parts("employee", {}, "soul")
    for updates in ({"name": "other"}, {"github": {"installation": 1}}, {"secret": "value"}):
        with pytest.raises(ValueError):
            EmployeeProposalStore(Paths(base_dir=tmp_path)).create("employee", "alice", definition, updates, None)


def test_duplicate_model_proposal_reuses_review_and_pending_count_is_bounded(tmp_path, monkeypatch):
    from deerflow.persistence.agents.proposals import EmployeeProposalStore

    definition = AgentDefinition.from_parts("employee", {"description": "old"}, "soul")
    store = EmployeeProposalStore(Paths(base_dir=tmp_path))
    monkeypatch.setattr(store, "MAX_PENDING", 2)
    first = store.create("employee", "alice", definition, {"description": "new"}, None)
    assert store.create("employee", "alice", definition, {"description": "new"}, None) == first
    store.create("employee", "alice", definition, {"description": "second"}, None)
    with pytest.raises(ValueError, match="pending"):
        store.create("employee", "alice", definition, {"description": "third"}, None)


def test_proposal_target_digest_allows_idempotent_reconciliation(tmp_path):
    from deerflow.persistence.agents.drafts import patched_definition
    from deerflow.persistence.agents.proposals import EmployeeProposalStore

    definition = AgentDefinition.from_parts("employee", {"description": "old"}, "soul")
    store = EmployeeProposalStore(Paths(base_dir=tmp_path))
    proposal = store.create("employee", "alice", definition, {"description": "new"}, "new soul")
    target = patched_definition(definition, proposal["updates"], proposal["soul"], definition.revision)
    assert proposal["target_revision"] == target.revision
    store.finish("employee", "alice", proposal["id"], state="applied", current_revision=target.revision)
    assert store.get("employee", "alice", proposal["id"])["state"] == "applied"
    with pytest.raises(ValueError):
        store.finish("employee", "alice", proposal["id"], state="rejected", current_revision=definition.revision)

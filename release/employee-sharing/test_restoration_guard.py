"""Restoration must preserve the LATEST state, not an upgrade-time snapshot."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def guard():
    spec = importlib.util.spec_from_file_location(
        "employee_restoration_guard", Path(__file__).with_name("restoration_guard.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def state():
    return {
        "schema": 1,
        "agent_backend": "db",
        "employee_files": {
            "schema": 1,
            "kind": "files",
            "complete": True,
            "files": {
                "users/alice/agents/employee/config.yaml": {
                    "sha256": "a" * 64,
                    "bytes": 3,
                    "mode": 0o640,
                },
                "team_agents/employee/current.json": {
                    "sha256": "b" * 64,
                    "bytes": 7,
                    "mode": 0o600,
                },
                "team_agents/.resource-grants/grant.json": {
                    "sha256": "c" * 64,
                    "bytes": 8,
                    "mode": 0o600,
                },
            },
        },
        "employee_sql": {
            "schema": 1,
            "kind": "sql",
            "complete": True,
            "rows": {
                "id-one": {"sha256": "d" * 64, "user_id": "alice", "name": "employee"},
            },
        },
        "thread_bindings": {
            "schema": 1,
            "kind": "bindings",
            "complete": True,
            "files": {
                "users/bob/threads/thread/employee-publication.json": {
                    "sha256": "e" * 64,
                    "bytes": 8,
                    "mode": 0o600,
                },
            },
        },
    }


def test_exact_latest_copy_is_equivalent_but_never_rollout_approval(guard):
    original = state()
    report = guard.compare_latest(original, copy.deepcopy(original))
    assert report["data_equivalent"] is True
    assert report["production_restore_authorized"] is False
    assert report["consistent_snapshot_proven"] is False
    assert report["ready_for_rollout"] is False


@pytest.mark.parametrize(
    "scope,key",
    [
        ("employee_files", "users/alice/agents/new/config.yaml"),
        ("employee_files", "team_agents/.resource-grants/new.json"),
        ("thread_bindings", "users/new/threads/new/employee-publication.json"),
    ],
)
def test_pre_upgrade_backup_cannot_erase_post_upgrade_additions(guard, scope, key):
    old = state()
    current = copy.deepcopy(old)
    current[scope]["files"][key] = {"sha256": "f" * 64, "bytes": 1, "mode": 0o600}
    result = guard.compare_latest(current, old)
    assert not result["data_equivalent"]
    assert result["differences"][scope]["missing"] == [key]


@pytest.mark.parametrize(
    "key",
    [
        "team_agents/employee/current.json",
        "team_agents/.resource-grants/grant.json",
        "users/alice/agents/employee/config.yaml",
    ],
)
def test_withdrawal_revocation_and_edit_cannot_be_rewound(guard, key):
    old = state()
    current = copy.deepcopy(old)
    current["employee_files"]["files"][key]["sha256"] = "f" * 64
    result = guard.compare_latest(current, old)
    assert not result["data_equivalent"]
    assert result["differences"]["employee_files"]["changed"] == [key]


def test_deleted_employee_cannot_be_resurrected_or_replaced_by_namesake(guard):
    old, current = state(), state()
    current["employee_sql"]["rows"] = {
        "new-id": {"sha256": "f" * 64, "name": "employee", "user_id": "alice"}
    }
    result = guard.compare_latest(current, old)
    assert not result["data_equivalent"]
    assert result["differences"]["employee_sql"] == {
        "missing": ["new-id"],
        "unexpected": ["id-one"],
        "changed": [],
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_bindings",
        "missing_sql",
        "incomplete",
        "wrong_backend",
        "invalid_digest",
        "missing_mode",
        "escape",
        "wrong_binding_scope",
    ],
)
def test_incomplete_or_malformed_evidence_never_compares_equal(guard, mutation):
    broken = state()
    if mutation == "missing_bindings":
        broken.pop("thread_bindings")
    elif mutation == "missing_sql":
        broken["employee_sql"] = None
    elif mutation == "incomplete":
        broken["employee_files"]["complete"] = False
    elif mutation == "wrong_backend":
        broken["agent_backend"] = "unknown"
    elif mutation in ("invalid_digest", "missing_mode"):
        record = next(iter(broken["employee_files"]["files"].values()))
        record.pop("mode") if mutation == "missing_mode" else record.update(
            sha256="unverified"
        )
    elif mutation == "escape":
        broken["employee_files"]["files"]["../outside"] = {
            "sha256": "f" * 64,
            "bytes": 1,
            "mode": 0o600,
        }
    else:
        broken["thread_bindings"]["files"][
            "users/bob/threads/thread/user-data/private.txt"
        ] = {"sha256": "f" * 64, "bytes": 1, "mode": 0o600}
    with pytest.raises(ValueError):
        guard.compare_latest(broken, broken)


def test_binding_inventory_never_reads_uploads_or_messages(guard, tmp_path):
    thread = tmp_path / "users" / "bob" / "threads" / "one"
    (thread / "user-data" / "uploads").mkdir(parents=True)
    (thread / "employee-publication.json").write_text('{"revision":"current"}')
    (thread / "user-data" / "uploads" / "private.txt").write_text("PRIVATE_CONTENT")
    report = guard.capture_bindings(tmp_path)
    assert set(report["files"]) == {"users/bob/threads/one/employee-publication.json"}
    assert "PRIVATE_CONTENT" not in json.dumps(report)
    assert report["complete"] is True


def test_binding_inventory_refuses_links_and_limits(guard, tmp_path):
    (tmp_path / "threads" / "one").mkdir(parents=True)
    (tmp_path / "threads" / "one" / "employee-publication.json").write_bytes(b"abc")
    with pytest.raises(ValueError, match="limit"):
        guard.capture_bindings(tmp_path, max_bytes=2)
    with pytest.raises(ValueError, match="limit"):
        guard.capture_bindings(tmp_path, max_entries=1)
    linked = tmp_path / "threads" / "two"
    try:
        linked.symlink_to(tmp_path / "threads" / "one", target_is_directory=True)
    except OSError:
        pytest.skip("Linux exercises links")
    with pytest.raises(ValueError, match="link"):
        guard.capture_bindings(tmp_path)


def test_binding_change_during_scan_is_rejected(guard, tmp_path, monkeypatch):
    thread = tmp_path / "threads" / "one"
    thread.mkdir(parents=True)
    receipt = thread / "employee-publication.json"
    receipt.write_bytes(b"before")
    real = guard.read_record

    def mutate(path, remaining):
        result = real(path, remaining)
        path.write_bytes(b"after-longer")
        return result

    monkeypatch.setattr(guard, "read_record", mutate)
    with pytest.raises(ValueError, match="changed"):
        guard.capture_bindings(tmp_path)


def test_data_observation_never_approves_legacy_image_downgrade(guard):
    original = state()
    report = guard.compare_latest(original, original)
    assert report["legacy_image_downgrade"] == "NOT_AUTHORIZED"


def test_real_publication_and_revocation_survive_latest_file_rehearsal(guard, tmp_path):
    """Real store/backup code, no mocks; still not a coordinated SQL/NFS restore."""
    from datetime import datetime, timedelta, timezone

    from deerflow.config.paths import Paths
    from deerflow.persistence.agents import definition_backup as backup
    from deerflow.persistence.agents.inventory import capture_files
    from deerflow.persistence.agents.publications import TeamPublicationStore
    from deerflow.persistence.agents.resource_grants import (
        EmployeeGrantStore,
        GrantResource,
    )

    paths = Paths(base_dir=tmp_path / "live")
    paths.base_dir.mkdir()
    personal = paths.user_agent_dir("alice", "original")
    personal.mkdir(parents=True)
    original = b"name: original\nfuture_field: do-not-drop\n"
    (personal / "config.yaml").write_bytes(original)
    publications = TeamPublicationStore(paths)
    first = publications.publish(
        "reports",
        owner="alice",
        config={"name": "reports", "skills": []},
        soul="first",
        avatar=None,
        expected_revision=None,
    )
    grants = EmployeeGrantStore(paths)
    grant = grants.request(
        publication_id=first.publication_id,
        publisher="alice",
        requested_by="alice",
        resource=GrantResource(kind="knowledge", tenant_id="tenant", kb_id="kb"),
    )
    grant = grants.decide(
        grant.id,
        actor="kb-owner",
        expected_revision=grant.revision,
        approve=True,
        expires_at=datetime.now(timezone.utc) + timedelta(days=3),
    )
    backup.create(paths, tmp_path / "old-backup")
    second = publications.publish(
        "reports",
        owner="alice",
        config={"name": "reports", "skills": []},
        soul="edited",
        avatar=None,
        expected_revision=first.revision,
    )
    withdrawn = publications.unpublish(
        "reports", owner="alice", expected_revision=second.revision
    )
    revoked = grants.revoke(
        grant.id, actor="kb-owner", expected_revision=grant.revision
    )
    added = paths.user_agent_dir("bob", "created-after-upgrade")
    added.mkdir(parents=True)
    (added / "config.yaml").write_text(
        "name: created-after-upgrade\n", encoding="utf-8"
    )

    def evidence(root):
        return {
            "schema": 1,
            "agent_backend": "file",
            "employee_sql": None,
            "employee_files": capture_files(Paths(base_dir=root)),
            "thread_bindings": guard.capture_bindings(root),
        }

    latest = evidence(paths.base_dir)
    backup.create(paths, tmp_path / "latest-backup")
    backup.restore_to_new_directory(tmp_path / "old-backup", tmp_path / "old-rehearsal")
    assert not guard.compare_latest(latest, evidence(tmp_path / "old-rehearsal"))[
        "data_equivalent"
    ]
    backup.restore_to_new_directory(
        tmp_path / "latest-backup", tmp_path / "latest-rehearsal"
    )
    restored = Paths(base_dir=tmp_path / "latest-rehearsal")
    assert guard.compare_latest(latest, evidence(restored.base_dir))["data_equivalent"]
    assert TeamPublicationStore(restored).head("reports").revision == withdrawn.revision
    with pytest.raises(FileNotFoundError):
        TeamPublicationStore(restored).read("reports")
    assert EmployeeGrantStore(restored).get(grant.id).state == "revoked"
    assert EmployeeGrantStore(restored).get(grant.id).revision == revoked.revision
    assert (
        restored.user_agent_dir("alice", "original") / "config.yaml"
    ).read_bytes() == original
    assert (
        restored.user_agent_dir("bob", "created-after-upgrade") / "config.yaml"
    ).exists()
    assert evidence(paths.base_dir) == latest


def test_real_thread_binding_missing_from_definition_backup_is_not_silently_accepted(
    guard, tmp_path
):
    from deerflow.config.paths import Paths
    from deerflow.persistence.agents import definition_backup as backup
    from deerflow.persistence.agents.inventory import capture_files
    from deerflow.persistence.agents.publications import TeamPublicationStore
    from deerflow.persistence.agents.runtime import EmployeePublicationBindings

    paths = Paths(base_dir=tmp_path / "live")
    paths.base_dir.mkdir()
    publication = TeamPublicationStore(paths).publish(
        "reports",
        owner="alice",
        config={"name": "reports", "skills": []},
        soul="first",
        avatar=None,
        expected_revision=None,
    )
    EmployeePublicationBindings(paths).admit(
        "thread-one",
        user_id="bob",
        name="reports",
        source="team",
        expected_revision=publication.revision,
    )

    def evidence(root):
        return {
            "schema": 1,
            "agent_backend": "file",
            "employee_sql": None,
            "employee_files": capture_files(Paths(base_dir=root)),
            "thread_bindings": guard.capture_bindings(root),
        }

    before = evidence(paths.base_dir)
    backup.create(paths, tmp_path / "definition-only")
    backup.restore_to_new_directory(
        tmp_path / "definition-only", tmp_path / "rehearsal"
    )
    comparison = guard.compare_latest(before, evidence(tmp_path / "rehearsal"))
    assert not comparison["data_equivalent"]
    assert comparison["differences"]["thread_bindings"]["missing"] == [
        "users/bob/threads/thread-one/employee-publication.json"
    ]
    assert (
        EmployeePublicationBindings(paths).read("thread-one", user_id="bob").revision
        == publication.revision
    )

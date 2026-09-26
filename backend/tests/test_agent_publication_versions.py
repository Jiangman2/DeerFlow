"""Publication replacement must be atomic, owner-scoped and compare-and-swap."""

from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context
from queue import Empty
from time import monotonic
from unittest.mock import Mock

import pytest
import yaml

from deerflow.config.paths import Paths
from deerflow.persistence.agents.publications import PublicationConflict, PublicationForbidden, TeamPublicationStore


@pytest.fixture
def store(tmp_path):
    return TeamPublicationStore(Paths(base_dir=tmp_path))


def publish(store, *, expected=None, owner="alice", text="first"):
    return store.publish("reports", owner=owner, config={"name": "reports", "description": text, "skills": []}, soul=text, avatar=None, expected_revision=expected)


def test_publish_is_complete_and_immutable(store):
    first = publish(store)
    second = publish(store, expected=first.revision, text="second")
    assert first.publication_id == second.publication_id
    assert first.revision != second.revision
    assert store.read("reports").soul == "second"
    assert store.read_revision("reports", first.revision, owner="alice").soul == "first"
    assert first.directory != second.directory


def test_stale_publish_and_cross_owner_cannot_replace(store):
    first = publish(store)
    with pytest.raises(PublicationConflict):
        publish(store)
    with pytest.raises(PublicationForbidden):
        publish(store, expected=first.revision, owner="bob")
    assert store.read("reports").revision == first.revision


def test_failed_head_commit_keeps_old_complete_version(store, monkeypatch):
    first = publish(store)
    monkeypatch.setattr(store, "_commit_head", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        publish(store, expected=first.revision, text="incomplete")
    current = store.read("reports")
    assert current.revision == first.revision
    assert current.config["description"] == current.soul == "first"
    assert len(store.history("reports", owner="alice")) == 1


def test_same_expected_revision_has_one_winner_across_store_instances(store):
    first = publish(store)

    def contender(index):
        try:
            return publish(TeamPublicationStore(store.paths), expected=first.revision, text=str(index)).revision
        except PublicationConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        result = list(executor.map(contender, range(2)))
    assert len([value for value in result if value]) == 1
    assert len(store.history("reports", owner="alice")) == 2


def test_unpublish_keeps_history_and_owner_and_never_revives_old_layout(store):
    first = publish(store)
    withdrawn = store.unpublish("reports", owner="alice", expected_revision=first.revision)
    with pytest.raises(FileNotFoundError):
        store.read("reports")
    with pytest.raises(PublicationForbidden):
        store.history("reports", owner="bob")
    with pytest.raises(PublicationForbidden):
        publish(store, expected=withdrawn.revision, owner="bob")
    republished = publish(store, expected=withdrawn.revision, text="third")
    assert republished.publication_id == first.publication_id
    assert len(store.history("reports", owner="alice")) == 3


def test_legacy_publication_is_readable_and_preserved_on_first_versioned_publish(store):
    directory = store.paths.team_agent_dir("reports")
    directory.mkdir(parents=True)
    (directory / "config.yaml").write_text(yaml.safe_dump({"name": "reports", "published_by_user_id": "alice", "description": "legacy"}), encoding="utf-8")
    (directory / "SOUL.md").write_text("legacy soul", encoding="utf-8")
    legacy = store.read("reports")
    assert legacy.revision.startswith("legacy-")
    published = publish(store, expected=legacy.revision)
    assert store.read_revision("reports", legacy.revision, owner="alice").soul == "legacy soul"
    assert store.read("reports").revision == published.revision


@pytest.mark.parametrize("name", ["../reports", "", "a/b"])
def test_unsafe_names_are_rejected(store, name):
    with pytest.raises(ValueError):
        store.read(name)


def test_corrupt_snapshot_fails_closed_without_legacy_fallback(store):
    first = publish(store)
    (first.directory / "SOUL.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        store.read("reports")


def test_ownerless_legacy_is_not_claimable(store):
    directory = store.paths.team_agent_dir("reports")
    directory.mkdir(parents=True)
    (directory / "config.yaml").write_text("name: reports\n", encoding="utf-8")
    with pytest.raises(PublicationForbidden):
        publish(store, expected=store.read("reports").revision)


def test_history_is_bounded_and_private(store):
    first = publish(store)
    second = publish(store, expected=first.revision, text="second")
    assert [item.revision for item in store.history("reports", owner="alice", limit=1)] == [second.revision]
    with pytest.raises(PublicationForbidden):
        store.read_revision("reports", first.revision, owner="bob")
    with pytest.raises(ValueError):
        store.read_revision("reports", "../../secrets", owner="alice")


def test_interrupted_legacy_snapshot_can_be_retried_without_losing_old_employee(store, monkeypatch):
    import deerflow.persistence.agents.publications as publications

    root = store.paths.team_agent_dir("reports")
    root.mkdir(parents=True)
    (root / "config.yaml").write_text("name: reports\npublished_by_user_id: alice\n", encoding="utf-8")
    (root / "SOUL.md").write_text("original", encoding="utf-8")
    original = store.read("reports")
    write_file = publications._write_file

    def interrupt(path, content):
        if path.name == "SOUL.md":
            raise OSError("interrupted snapshot write")
        return write_file(path, content)

    monkeypatch.setattr(publications, "_write_file", interrupt)
    with pytest.raises(OSError):
        publish(store, expected=original.revision)
    assert store.read("reports").soul == "original"
    monkeypatch.setattr(publications, "_write_file", write_file)
    publish(store, expected=original.revision)
    assert store.read_revision("reports", original.revision, owner="alice").soul == "original"


def _process_contender(base, revision, ready, release, outcomes):
    store = TeamPublicationStore(Paths(base_dir=base))
    # Finish lazy imports and filesystem setup before the competition clock.
    # Spawned interpreters do not inherit the parent's warmed modules.
    assert store.read("reports").revision == revision
    ready.put(True)
    if not release.wait(180):
        raise TimeoutError("Publication contender was not released; refusing an unsynchronized write")
    try:
        outcomes.put(publish(store, expected=revision).revision)
    except PublicationConflict:
        outcomes.put("conflict")


def test_process_contender_cannot_publish_without_parent_release(store):
    first = publish(store)
    ready, release, outcomes = Mock(), Mock(), Mock()
    release.wait.return_value = False
    with pytest.raises(TimeoutError, match="refusing an unsynchronized write"):
        _process_contender(store.paths.base_dir, first.revision, ready, release, outcomes)
    ready.put.assert_called_once_with(True)
    outcomes.put.assert_not_called()
    assert store.read("reports").revision == first.revision
    assert len(store.history("reports", owner="alice")) == 1


def test_concurrent_processes_cannot_replace_same_revision_twice(store):
    first = publish(store)
    context = get_context("spawn")
    ready, outcomes = context.Queue(), context.Queue()
    release = context.Event()
    processes = [context.Process(target=_process_contender, args=(store.paths.base_dir, first.revision, ready, release, outcomes)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        # This is interpreter warm-up, not the lock/commit budget. Keep the
        # actual competition below at 30s and report child exit codes on error.
        startup_deadline = monotonic() + 180
        for _ in processes:
            try:
                assert ready.get(timeout=max(0.01, startup_deadline - monotonic()))
            except Empty:
                pytest.fail("Publication child startup timed out; exit codes: " + repr([process.exitcode for process in processes]))
        release.set()
        result = [outcomes.get(timeout=30) for _ in processes]
        assert result.count("conflict") == 1
        for process in processes:
            process.join(timeout=5)
            assert process.exitcode == 0, "Publication contender did not exit cleanly"
        assert len(store.history("reports", owner="alice")) == 2
    finally:
        release.set()
        for process in processes:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        ready.close()
        outcomes.close()


def test_legacy_read_is_non_mutating_and_upgrade_keeps_all_original_files(store):
    root = store.paths.team_agent_dir("reports")
    root.mkdir(parents=True)
    original = {
        "config.yaml": b"name: reports\npublished_by_user_id: alice\nfuture_binding: preserve-me\n",
        "SOUL.md": "已有员工职责，必须保留。".encode(),
        "avatar.webp": b"legacy-avatar",
        "memory.json": b'{"facts": ["keep"]}',
    }
    for name, value in original.items():
        (root / name).write_bytes(value)
    first = store.read("reports")
    assert sorted(path.name for path in root.iterdir()) == sorted(original)
    second = publish(store, expected=first.revision)
    assert all((root / name).read_bytes() == value for name, value in original.items())
    archived = store.read_revision("reports", first.revision, owner="alice")
    assert archived.config["future_binding"] == "preserve-me"
    assert (archived.directory / "avatar.webp").read_bytes() == original["avatar.webp"]
    store.unpublish("reports", owner="alice", expected_revision=second.revision)
    assert all((root / name).read_bytes() == value for name, value in original.items())


def test_exact_shared_loads_and_listing_follow_atomic_head_without_shadowing(catalog_paths, store):
    from deerflow.config import agents_config as ac

    first = publish(store)
    publish(store, expected=first.revision, text="latest")
    assert ac.load_agent_config("reports", source="team").description == "latest"
    assert ac.load_agent_soul("reports", source="team") == "latest"
    assert [item.name for item in ac.list_team_agents()] == ["reports"]


def test_withdrawn_team_employee_never_revives_legacy_namesake(catalog_paths, store):
    from deerflow.config import agents_config as ac

    legacy = store.paths.agent_dir("reports")
    legacy.mkdir(parents=True)
    (legacy / "config.yaml").write_text("name: reports\ndescription: legacy namesake\n", encoding="utf-8")
    (legacy / "SOUL.md").write_text("unrelated legacy", encoding="utf-8")
    first = publish(store)
    store.unpublish("reports", owner="alice", expected_revision=first.revision)
    assert ac.list_team_agents() == []
    with pytest.raises(FileNotFoundError):
        ac.load_agent_config("reports", source="team")
    assert ac.load_agent_soul("reports", source="legacy") == "unrelated legacy"


@pytest.fixture
def catalog_paths(store, monkeypatch):
    from deerflow.config import agents_config as ac

    monkeypatch.setattr(ac, "get_paths", lambda: store.paths)
    return store.paths


def test_reserved_identity_survives_first_publish_and_cannot_be_claimed_by_namesake(tmp_path):
    import pytest

    from deerflow.config.paths import Paths
    from deerflow.persistence.agents.publications import PublicationForbidden, TeamPublicationStore

    store = TeamPublicationStore(Paths(base_dir=tmp_path))
    identity = store.reserve_identity("new-employee", owner="alice")
    assert identity == store.reserve_identity("new-employee", owner="alice")
    assert not store.has_record("new-employee")
    with pytest.raises(PublicationForbidden):
        store.reserve_identity("new-employee", owner="bob")
    with pytest.raises(PublicationForbidden):
        store.publish("new-employee", owner="bob", config={}, soul="", avatar=None, expected_revision=None)
    snapshot = store.publish("new-employee", owner="alice", config={}, soul="", avatar=None, expected_revision=None)
    assert snapshot.publication_id == identity

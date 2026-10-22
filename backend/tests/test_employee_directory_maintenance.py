import hashlib
import json

import pytest

from app.gateway.employee_directory_maintenance import backup, restore
from app.gateway.employee_featured_store import EmployeeIdentity, FeaturedConflict, FeaturedStore, FeaturedUnavailable

OLD = EmployeeIdentity("team", "existing", "owner")
NEW = EmployeeIdentity("team", "new", "owner")


def prepared(tmp_path):
    store = FeaturedStore(tmp_path / "hot.json")
    store.initialize([OLD], actor="release")
    archive = tmp_path / "before.json"
    receipt = backup(store, archive)
    assert receipt["sha256"] == hashlib.sha256(archive.read_bytes()).hexdigest()
    return store, archive, receipt["sha256"]


def test_restore_is_new_transaction_keeps_preimage_and_invalidates_stale_edits(tmp_path):
    store, archive, digest = prepared(tmp_path)
    store.set_hot(OLD, False, expected_version=1, actor="admin")
    store.set_hot(NEW, False, expected_version=0, actor="admin")
    old = store.path.read_bytes()
    receipt = restore(store, archive, sha256=digest, expected_revision=3, actor="rollback", backup_current=tmp_path / "rollback-before.json")
    assert receipt["revision"] == 4
    saved = json.loads((tmp_path / "rollback-before.json").read_bytes())
    assert saved == json.loads(old)
    snapshot = store.snapshot()
    assert snapshot.flag(OLD) == (True, 3)
    assert snapshot.flag(NEW) == (True, 2)
    assert snapshot.document["changes"][-1]["actor"] == "rollback"
    for identity, stale in [(OLD, 2), (NEW, 1)]:
        with pytest.raises(FeaturedConflict):
            store.set_hot(identity, False, expected_version=stale, actor="stale-admin")


@pytest.mark.parametrize("change", ["hash", "revision", "invalid", "collision"])
def test_failed_restore_never_changes_live_file(tmp_path, change):
    store, archive, digest = prepared(tmp_path)
    original = store.path.read_bytes()
    preimage = tmp_path / "restore-before.json"
    revision = 1
    if change == "hash":
        digest = "0" * 64
    if change == "revision":
        revision = 999
    if change == "invalid":
        archive.write_text("{}")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if change == "collision":
        preimage.write_text("do not overwrite")
    with pytest.raises((FeaturedUnavailable, FeaturedConflict)):
        restore(store, archive, sha256=digest, expected_revision=revision, actor="rollback", backup_current=preimage)
    assert store.path.read_bytes() == original
    if change == "collision":
        assert preimage.read_text() == "do not overwrite"
    else:
        assert not preimage.exists()


def test_backup_no_overwrite_or_relative_path(tmp_path):
    store, archive, _ = prepared(tmp_path)
    saved = archive.read_bytes()
    for output in (archive, store.path, "relative.json"):
        with pytest.raises(FeaturedUnavailable):
            backup(store, output)
    assert archive.read_bytes() == saved


def test_atomic_restore_failure_keeps_live_and_private_preimage(tmp_path, monkeypatch):
    store, archive, digest = prepared(tmp_path)
    store.set_hot(OLD, False, expected_version=1, actor="admin")
    original = store.path.read_bytes()
    monkeypatch.setattr("app.gateway.employee_featured_store.os.replace", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(FeaturedUnavailable):
        restore(store, archive, sha256=digest, expected_revision=2, actor="rollback", backup_current=tmp_path / "restore-before.json")
    assert store.path.read_bytes() == original
    assert json.loads((tmp_path / "restore-before.json").read_bytes()) == json.loads(original)


def test_missing_live_state_requires_separate_operator_recovery(tmp_path):
    store, archive, digest = prepared(tmp_path)
    store.path.unlink()
    with pytest.raises(FeaturedUnavailable):
        restore(store, archive, sha256=digest, expected_revision=1, actor="rollback", backup_current=tmp_path / "restore-before.json")
    assert not store.path.exists()

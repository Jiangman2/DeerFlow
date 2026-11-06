"""The artifact qualifier must work in historical and isolated Git readers."""

import subprocess
from pathlib import Path

import pytest
import test_work_routing_source_manifest as previous

VERIFIER = previous.VERIFIER
QUALIFIER = VERIFIER.live_artifacts
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def files():
    paths = list(QUALIFIER.FILES)
    paths += [QUALIFIER.ARCHIVE + "baseline/" + path for path in paths]
    return {path: (ROOT / path).read_bytes() for path in paths}


def test_unrelated_historical_tree_is_left_for_existing_verifiers():
    read = {"frontend/src/example.ts": b"historical source"}.__getitem__
    assert QUALIFIER.historical_read(read) is read


def test_exact_successor_projects_its_verified_parent(files):
    read = QUALIFIER.historical_read(files.__getitem__)
    for path in QUALIFIER.FILES:
        assert read(path) == files[QUALIFIER.ARCHIVE + "baseline/" + path].replace(b"\r\n", b"\n")


@pytest.mark.parametrize("path", list(QUALIFIER.FILES))
@pytest.mark.parametrize("kind", ["source", "baseline"])
@pytest.mark.parametrize("tamper", ["missing", "changed"])
def test_partial_or_changed_evidence_is_rejected(files, path, kind, tamper):
    target = QUALIFIER.ARCHIVE + "baseline/" + path if kind == "baseline" else path
    if tamper == "missing":
        del files[target]
    else:
        files[target] += b"\n// unqualified change\n"
    with pytest.raises((AssertionError, KeyError)):
        QUALIFIER.historical_read(files.__getitem__)


def test_successor_cannot_drop_the_entire_archive(files):
    current_only = {path: files[path] for path in QUALIFIER.FILES}
    with pytest.raises(KeyError):
        QUALIFIER.historical_read(current_only.__getitem__)


def test_file_path_import_and_git_archive_verify_selected_evidence(tmp_path, monkeypatch, files):
    root, *_ = previous.evidence.__wrapped__(tmp_path, monkeypatch)
    for path, content in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    for directory in ("backend/packages/harness", VERIFIER.RUNNER, VERIFIER.FORMAT_BASELINE):
        target = root / directory / ".keep"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"synthetic\n")

    def git(*args):
        return subprocess.check_output(
            ["git", "-c", "user.name=Synthetic Test", "-c", "user.email=synthetic@example.test", *args],
            cwd=root,
            stderr=subprocess.STDOUT,
        )

    git("init", "--quiet")
    git("add", ".")
    git("commit", "--quiet", "-m", "synthetic artifact evidence")
    ref = git("rev-parse", "HEAD").decode().strip()
    assert VERIFIER.verify(ref)["git_ref"] == ref
    first_path = next(iter(QUALIFIER.FILES))
    (root / first_path).write_bytes(b"unqualified working tree")
    assert VERIFIER.verify(ref)["git_ref"] == ref
    with pytest.raises(AssertionError, match="LIVE_ARTIFACT_SOURCE_CHANGED"):
        VERIFIER.verify()

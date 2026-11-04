"""The routing seal layers over immutable production, Work and incident evidence."""

import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "deploy/enterprise/verify-release-source-20261023.py"
SPEC = importlib.util.spec_from_file_location("work_routing_source_verifier", SOURCE)
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


def digest(raw):
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def serialized(value):
    return json.dumps(value, indent=2).encode() + b"\n"


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    names = {key: f"backend/app/{key}.py" for key in ("work", "incident", "normalized", "base", "new")}
    raw = {step: f"VALUE = {step}\n".encode() for step in range(5)}
    front = "frontend/src/example.ts"
    lock = {
        "source_sets": {"gateway": {name: digest(raw[0]) for key, name in names.items() if key != "new"}, "frontend": {front: digest(b"export const value = 0;\n")}},
        "format_normalizations": {names["normalized"]: {"formatted_sha256": digest(raw[1]), "kind": "layout"}},
        "scope": {"test": "synthetic"},
    }
    # Formatting provenance needs an actual archived parent with equal AST;
    # VALUE=0 -> VALUE=1 is a logic change and must never qualify as layout.
    formatted_parent = b"VALUE  =  1\n"
    lock["source_sets"]["gateway"][names["normalized"]] = digest(formatted_parent)
    lock_raw = serialized(lock)
    work = {
        "schema": "production-source-candidate.v1",
        "status": "NOT_DEPLOYED",
        "base_commit": "a" * 40,
        "base_lock_sha256": digest(lock_raw),
        "changes": {names[key]: {"base_sha256": digest(raw[0]), "candidate_sha256": digest(raw[1])} for key in ("work", "incident")},
    }
    work_raw = serialized(work)
    incident = {
        "schema": "incident-source-candidate.v1",
        "status": "NOT_DEPLOYED",
        "base_commit": "b" * 40,
        "base_lock_sha256": digest(lock_raw),
        "parent_candidate_sha256": digest(work_raw),
        "changes": {"gateway": {names["incident"]: {"base_sha256": digest(raw[1]), "candidate_sha256": digest(raw[2])}}, "frontend": {}},
    }
    incident_raw = serialized(incident)
    previous = {"work": digest(raw[1]), "incident": digest(raw[2]), "normalized": digest(raw[1]), "base": digest(raw[0]), "new": None}
    routing = {
        "schema": "work-routing-source-candidate.v1",
        "status": "NOT_DEPLOYED",
        "base_commit": VERIFIER.WORK_ROUTING_BASE_COMMIT,
        "base_lock_sha256": digest(lock_raw),
        "parent_candidate_sha256": digest(incident_raw),
        "scope": "Synthetic routing candidate; not deployed.",
        "changes": {"gateway": {name: {"base_sha256": previous[key], "candidate_sha256": digest(raw[3])} for key, name in names.items()}, "frontend": {}},
    }
    files = {VERIFIER.LOCK: lock_raw, VERIFIER.CANDIDATE: work_raw, VERIFIER.INCIDENT_DELTA: incident_raw, VERIFIER.WORK_ROUTING_DELTA: serialized(routing), front: b"export const value = 0;\n"}
    files[VERIFIER.FORMAT_BASELINE + names["normalized"]] = formatted_parent
    files.update({name: raw[3] for name in names.values()})
    for name, value in files.items():
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    monkeypatch.setattr(VERIFIER, "ROOT", tmp_path)
    return tmp_path, names, lock, work, incident, routing, files


def save_manifest(root, manifest):
    (root / VERIFIER.WORK_ROUTING_DELTA).write_bytes(serialized(manifest))


def test_routing_seal_uses_each_effective_parent_without_rewriting_historical_evidence(evidence):
    root, _, _, _, _, _, original = evidence
    result = VERIFIER.verify()
    assert result["status"] == "CANDIDATE_SOURCE_VERIFIED" and result["production_modified"] is False
    assert result["candidate"]["status"] == "NOT_DEPLOYED"
    assert result["candidate"]["work_routing_delta"] == {"status": "NOT_DEPLOYED", "base_commit": VERIFIER.WORK_ROUTING_BASE_COMMIT, "changed_sources": 5}
    for name in (VERIFIER.LOCK, VERIFIER.CANDIDATE, VERIFIER.INCIDENT_DELTA):
        assert (root / name).read_bytes() == original[name]


@pytest.mark.parametrize(
    "tamper",
    [
        "null_manifest",
        "schema",
        "unknown_field",
        "status",
        "base_commit",
        "base_lock",
        "parent",
        "component",
        "frontend",
        "changes_type",
        "change_schema",
        "hash",
        "hash_type",
        "parent_work",
        "parent_incident",
        "parent_normalized",
        "parent_base",
        "parent_new",
        "outside",
        "traversal",
        "backslash",
        "noncanonical",
    ],
)
def test_routing_seal_rejects_authority_parent_component_and_path_drift(evidence, tamper):
    root, names, _, _, _, routing, _ = evidence
    bad = copy.deepcopy(routing)
    if tamper == "null_manifest":
        bad = None
    elif tamper in {"schema", "status", "base_commit"}:
        bad[tamper] = {"schema": "production-source-candidate.v1", "status": "DEPLOYED", "base_commit": "c" * 40}[tamper]
    elif tamper == "unknown_field":
        bad["deployment_accepted"] = True
    elif tamper in {"base_lock", "parent"}:
        bad["base_lock_sha256" if tamper == "base_lock" else "parent_candidate_sha256"] = "0" * 64
    elif tamper == "component":
        bad["changes"]["ppt-master-runner"] = {}
    elif tamper == "frontend":
        bad["changes"]["frontend"]["frontend/src/example.ts"] = {"base_sha256": None, "candidate_sha256": "0" * 64}
    elif tamper == "changes_type":
        bad["changes"]["frontend"] = []
    elif tamper == "change_schema":
        bad["changes"]["gateway"][names["work"]]["ignore_hash"] = True
    elif tamper in {"hash", "hash_type"}:
        bad["changes"]["gateway"][names["work"]]["candidate_sha256"] = "G" * 64 if tamper == "hash" else None
    elif tamper.startswith("parent_"):
        bad["changes"]["gateway"][names[tamper.removeprefix("parent_")]]["base_sha256"] = "f" * 64
    else:
        name = {"outside": "backend/tests/example.py", "traversal": "backend/app/../../secret.py", "backslash": "backend/app/evil\\name.py", "noncanonical": "backend/app/./evil.py"}[tamper]
        bad["changes"]["gateway"][name] = {"base_sha256": None, "candidate_sha256": "0" * 64}
    save_manifest(root, bad)
    with pytest.raises(AssertionError):
        VERIFIER.verify()


@pytest.mark.parametrize("tamper", ["source", "unlisted", "missing_routing", "missing_parent", "parent_bytes", "invalid_python"])
def test_routing_source_requires_exact_declared_files_and_immutable_parent(evidence, tamper):
    root, names, _, _, _, routing, _ = evidence
    if tamper == "source":
        (root / names["work"]).write_bytes(b"VALUE = 4\n")
    elif tamper == "unlisted":
        (root / "backend/app/unlisted.py").write_bytes(b"VALUE = 4\n")
    elif tamper == "missing_routing":
        (root / VERIFIER.WORK_ROUTING_DELTA).unlink()
    elif tamper == "missing_parent":
        (root / VERIFIER.INCIDENT_DELTA).unlink()
    elif tamper == "parent_bytes":
        with (root / VERIFIER.INCIDENT_DELTA).open("ab") as stream:
            stream.write(b" ")
    else:
        raw = b"def invalid(\n"
        (root / names["work"]).write_bytes(raw)
        routing["changes"]["gateway"][names["work"]]["candidate_sha256"] = digest(raw)
        save_manifest(root, routing)
    with pytest.raises((AssertionError, SyntaxError)):
        VERIFIER.verify()


def test_parent_binding_and_source_hash_only_normalize_line_endings(evidence):
    root, names, _, _, _, _, _ = evidence
    for name in (VERIFIER.LOCK, VERIFIER.CANDIDATE, VERIFIER.INCIDENT_DELTA, *names.values()):
        target = root / name
        target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))
    assert VERIFIER.verify()["candidate"]["work_routing_delta"]["status"] == "NOT_DEPLOYED"


@pytest.mark.skipif(shutil.which("git") is None, reason="Git executable is required for the synthetic archive integration test")
def test_git_archive_reads_the_routing_seal_from_the_requested_commit(evidence):
    root, names, _, _, _, _, _ = evidence
    # All archive roots are synthetic local inputs; this test performs no fetch.
    for directory in ("backend/packages/harness", VERIFIER.RUNNER, VERIFIER.FORMAT_BASELINE):
        target = root / directory / ".keep"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"synthetic\n")

    def git(*args):
        return subprocess.check_output(["git", "-c", "user.name=Synthetic Test", "-c", "user.email=synthetic@example.test", *args], cwd=root, stderr=subprocess.STDOUT)

    git("init", "--quiet")
    git("add", ".")
    git("commit", "--quiet", "-m", "synthetic source seal")
    ref = git("rev-parse", "HEAD").decode().strip()
    assert VERIFIER.verify(ref)["candidate"]["work_routing_delta"]["changed_sources"] == 5
    (root / VERIFIER.WORK_ROUTING_DELTA).unlink()
    (root / names["work"]).write_bytes(b"VALUE = 4\n")
    assert VERIFIER.verify(ref)["git_ref"] == ref
    with pytest.raises(AssertionError):
        VERIFIER.verify()

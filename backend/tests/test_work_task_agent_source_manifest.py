"""The task-agent seal extends, rather than rewrites, the accepted routing seal."""

import copy
import shutil
import subprocess

import pytest
import test_work_routing_source_manifest as previous

VERIFIER = previous.VERIFIER
digest = previous.digest
serialized = previous.serialized


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    root, names, lock, work, incident, routing, original = previous.evidence.__wrapped__(tmp_path, monkeypatch)
    # Keep one r1 override; the fourth layer must also bind the effective
    # incident, Work, formatted-lock and original-lock parents independently.
    routing = copy.deepcopy(routing)
    routing["changes"]["gateway"] = {names["new"]: routing["changes"]["gateway"][names["new"]]}
    routing_raw = serialized(routing)
    (root / VERIFIER.WORK_ROUTING_DELTA).write_bytes(routing_raw)
    base = {"work": digest(b"VALUE = 1\n"), "incident": digest(b"VALUE = 2\n"), "normalized": digest(b"VALUE = 1\n"), "base": digest(b"VALUE = 0\n"), "new": digest(b"VALUE = 3\n")}
    task_agent = {
        "schema": "work-task-agent-source-candidate.v1",
        "status": "NOT_DEPLOYED",
        "base_commit": VERIFIER.WORK_ROUTING_BASE_COMMIT,
        "base_lock_sha256": digest(original[VERIFIER.LOCK]),
        "parent_candidate_sha256": digest(routing_raw),
        "scope": "Synthetic finite-task candidate, not deployed.",
        "changes": {"gateway": {name: {"base_sha256": base[key], "candidate_sha256": digest(b"VALUE = 4\n")} for key, name in names.items()}, "frontend": {}},
    }
    names["fresh"] = "backend/packages/harness/fresh_task.py"
    task_agent["changes"]["gateway"][names["fresh"]] = {"base_sha256": None, "candidate_sha256": digest(b"VALUE = 4\n")}
    for name in names.values():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"VALUE = 4\n")
    (root / VERIFIER.WORK_TASK_AGENT_DELTA).write_bytes(serialized(task_agent))
    return root, names, task_agent, routing_raw, original


def save(root, manifest):
    (root / VERIFIER.WORK_TASK_AGENT_DELTA).write_bytes(serialized(manifest))


def test_fourth_overlay_reports_only_undeployed_evidence_and_preserves_every_parent(evidence):
    root, _, _, routing_raw, original = evidence
    result = VERIFIER.verify()
    assert result["status"] == "CANDIDATE_SOURCE_VERIFIED" and result["production_modified"] is False
    assert result["candidate"]["work_task_agent_delta"] == {"status": "NOT_DEPLOYED", "base_commit": VERIFIER.WORK_ROUTING_BASE_COMMIT, "changed_sources": 6}
    assert result["candidate"]["work_routing_delta"]["changed_sources"] == 1
    assert (root / VERIFIER.WORK_ROUTING_DELTA).read_bytes() == routing_raw
    for name in (VERIFIER.LOCK, VERIFIER.CANDIDATE, VERIFIER.INCIDENT_DELTA):
        assert (root / name).read_bytes() == original[name]


@pytest.mark.parametrize(
    "tamper",
    [
        "null",
        "schema",
        "unknown_field",
        "status",
        "base_commit",
        "lock",
        "parent",
        "component",
        "frontend",
        "change_type",
        "change_schema",
        "hash",
        "hash_type",
        "parent_new",
        "parent_work",
        "parent_incident",
        "parent_normalized",
        "parent_base",
        "parent_fresh",
        "outside",
        "traversal",
        "backslash",
        "noncanonical",
    ],
)
def test_fourth_overlay_rejects_schema_authority_parent_and_component_drift(evidence, tamper):
    root, names, manifest, _, _ = evidence
    bad = copy.deepcopy(manifest)
    if tamper == "null":
        bad = None
    elif tamper in {"schema", "status", "base_commit"}:
        bad[tamper] = {"schema": "work-routing-source-candidate.v1", "status": "DEPLOYED", "base_commit": "c" * 40}[tamper]
    elif tamper == "unknown_field":
        bad["production_accepted"] = True
    elif tamper in {"lock", "parent"}:
        bad["base_lock_sha256" if tamper == "lock" else "parent_candidate_sha256"] = "0" * 64
    elif tamper == "component":
        bad["changes"]["ppt-master-runner"] = {}
    elif tamper == "frontend":
        bad["changes"]["frontend"]["frontend/src/changed.ts"] = {"base_sha256": None, "candidate_sha256": "0" * 64}
    elif tamper == "change_type":
        bad["changes"]["frontend"] = []
    elif tamper == "change_schema":
        bad["changes"]["gateway"][names["work"]]["skip"] = True
    elif tamper in {"hash", "hash_type"}:
        bad["changes"]["gateway"][names["work"]]["candidate_sha256"] = "G" * 64 if tamper == "hash" else None
    elif tamper.startswith("parent_"):
        bad["changes"]["gateway"][names[tamper.removeprefix("parent_")]]["base_sha256"] = "f" * 64
    else:
        name = {"outside": "backend/tests/unsealed.py", "traversal": "backend/app/../../secret.py", "backslash": "backend/app/evil\\name.py", "noncanonical": "backend/app/./evil.py"}[tamper]
        bad["changes"]["gateway"][name] = {"base_sha256": None, "candidate_sha256": "0" * 64}
    save(root, bad)
    with pytest.raises(AssertionError):
        VERIFIER.verify()


@pytest.mark.parametrize("tamper", ["source", "extra", "missing_fourth", "missing_parent", "parent_bytes", "invalid_python"])
def test_fourth_overlay_cannot_exempt_changed_missing_or_unlisted_source(evidence, tamper):
    root, names, manifest, _, _ = evidence
    if tamper == "source":
        (root / names["work"]).write_bytes(b"VALUE = 5\n")
    elif tamper == "extra":
        (root / "backend/app/unlisted.py").write_bytes(b"VALUE = 5\n")
    elif tamper == "missing_fourth":
        (root / VERIFIER.WORK_TASK_AGENT_DELTA).unlink()
    elif tamper == "missing_parent":
        (root / VERIFIER.WORK_ROUTING_DELTA).unlink()
    elif tamper == "parent_bytes":
        with (root / VERIFIER.WORK_ROUTING_DELTA).open("ab") as stream:
            stream.write(b" ")
    else:
        raw = b"def invalid(\n"
        (root / names["work"]).write_bytes(raw)
        manifest["changes"]["gateway"][names["work"]]["candidate_sha256"] = digest(raw)
        save(root, manifest)
    with pytest.raises((AssertionError, SyntaxError)):
        VERIFIER.verify()


def test_parent_binding_and_source_hashes_accept_only_newline_normalization(evidence):
    root, names, _, _, _ = evidence
    for name in (VERIFIER.LOCK, VERIFIER.CANDIDATE, VERIFIER.INCIDENT_DELTA, VERIFIER.WORK_ROUTING_DELTA, *names.values()):
        target = root / name
        target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))
    assert VERIFIER.verify()["candidate"]["work_task_agent_delta"]["status"] == "NOT_DEPLOYED"


@pytest.mark.skipif(shutil.which("git") is None, reason="Git executable is required for the synthetic archive integration test")
def test_requested_git_archive_includes_the_fourth_manifest(evidence):
    root, names, _, _, _ = evidence
    for directory in (VERIFIER.RUNNER, VERIFIER.FORMAT_BASELINE):
        target = root / directory / ".keep"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"synthetic\n")

    def git(*args):
        return subprocess.check_output(["git", "-c", "user.name=Synthetic Test", "-c", "user.email=synthetic@example.test", *args], cwd=root, stderr=subprocess.STDOUT)

    git("init", "--quiet")
    git("add", ".")
    git("commit", "--quiet", "-m", "synthetic fourth source seal")
    ref = git("rev-parse", "HEAD").decode().strip()
    assert VERIFIER.verify(ref)["candidate"]["work_task_agent_delta"]["changed_sources"] == 6
    (root / VERIFIER.WORK_TASK_AGENT_DELTA).unlink()
    (root / names["work"]).write_bytes(b"VALUE = 5\n")
    assert VERIFIER.verify(ref)["git_ref"] == ref
    with pytest.raises(AssertionError):
        VERIFIER.verify()

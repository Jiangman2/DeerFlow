"""An R3 repair seal extends the four immutable source evidence layers."""

import copy
import shutil
import subprocess

import pytest
import test_work_task_agent_source_manifest as previous

VERIFIER = previous.VERIFIER
digest = previous.digest
serialized = previous.serialized


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    root, names, task_agent, routing_raw, original = previous.evidence.__wrapped__(tmp_path, monkeypatch)
    # Cover every effective parent independently: the R2-only new module, R1,
    # incident, Work, formatted production input and original production input.
    task_agent = copy.deepcopy(task_agent)
    task_agent["changes"]["gateway"] = {names["fresh"]: task_agent["changes"]["gateway"][names["fresh"]]}
    task_raw = serialized(task_agent)
    (root / VERIFIER.WORK_TASK_AGENT_DELTA).write_bytes(task_raw)
    parent = {"work": 1, "incident": 2, "normalized": 1, "base": 0, "new": 3, "fresh": 4}
    fix = {
        "schema": "work-task-agent-fix-source-candidate.v1",
        "status": "NOT_DEPLOYED",
        "base_commit": VERIFIER.WORK_ROUTING_BASE_COMMIT,
        "base_lock_sha256": digest(original[VERIFIER.LOCK]),
        "parent_candidate_sha256": digest(task_raw),
        "scope": "Synthetic bounded task-agent repair; no deployment.",
        "changes": {"gateway": {name: {"base_sha256": digest(f"VALUE = {parent[key]}\n".encode()), "candidate_sha256": digest(b"VALUE = 5\n")} for key, name in names.items()}, "frontend": {}},
    }
    names["fix_new"] = "backend/packages/harness/fresh_fix.py"
    fix["changes"]["gateway"][names["fix_new"]] = {"base_sha256": None, "candidate_sha256": digest(b"VALUE = 5\n")}
    for name in names.values():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"VALUE = 5\n")
    (root / VERIFIER.WORK_TASK_AGENT_FIX_DELTA).write_bytes(serialized(fix))
    parents = {name: (root / name).read_bytes() for name in (VERIFIER.LOCK, VERIFIER.CANDIDATE, VERIFIER.INCIDENT_DELTA)}
    parents.update({VERIFIER.WORK_ROUTING_DELTA: routing_raw, VERIFIER.WORK_TASK_AGENT_DELTA: task_raw})
    return root, names, fix, parents


def save(root, manifest):
    (root / VERIFIER.WORK_TASK_AGENT_FIX_DELTA).write_bytes(serialized(manifest))


def test_fifth_overlay_reports_only_candidate_evidence_and_preserves_all_four_layers(evidence):
    root, _, _, parents = evidence
    result = VERIFIER.verify()
    assert result["status"] == "CANDIDATE_SOURCE_VERIFIED" and result["production_modified"] is False
    assert result["candidate"]["work_task_agent_fix_delta"] == {"status": "NOT_DEPLOYED", "base_commit": VERIFIER.WORK_ROUTING_BASE_COMMIT, "changed_sources": 7}
    assert result["candidate"]["work_task_agent_delta"]["changed_sources"] == 1
    assert result["candidate"]["work_routing_delta"]["changed_sources"] == 1
    for name, raw in parents.items():
        assert (root / name).read_bytes() == raw


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
        "parent_fresh",
        "parent_new",
        "parent_incident",
        "parent_work",
        "parent_normalized",
        "parent_base",
        "parent_fix_new",
        "outside",
        "traversal",
        "backslash",
        "noncanonical",
    ],
)
def test_fifth_overlay_rejects_schema_authority_parent_and_path_drift(evidence, tamper):
    root, names, manifest, _ = evidence
    bad = copy.deepcopy(manifest)
    if tamper == "null":
        bad = None
    elif tamper in {"schema", "status", "base_commit"}:
        bad[tamper] = {"schema": "work-task-agent-source-candidate.v1", "status": "DEPLOYED", "base_commit": "c" * 40}[tamper]
    elif tamper == "unknown_field":
        bad["production_accepted"] = True
    elif tamper in {"lock", "parent"}:
        bad["base_lock_sha256" if tamper == "lock" else "parent_candidate_sha256"] = "0" * 64
    elif tamper == "component":
        bad["changes"]["ppt-master-runner"] = {}
    elif tamper == "frontend":
        bad["changes"]["frontend"]["frontend/src/changed.ts"] = {"base_sha256": None, "candidate_sha256": "0" * 64}
    elif tamper == "change_type":
        bad["changes"]["gateway"] = []
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


@pytest.mark.parametrize("tamper", ["source", "extra", "missing_fifth", "missing_fourth", "missing_routing", "parent_bytes", "invalid_python"])
def test_fifth_overlay_cannot_exempt_missing_changed_or_unlisted_source(evidence, tamper):
    root, names, manifest, _ = evidence
    if tamper == "source":
        (root / names["work"]).write_bytes(b"VALUE = 6\n")
    elif tamper == "extra":
        (root / "backend/app/unlisted.py").write_bytes(b"VALUE = 6\n")
    elif tamper == "missing_fifth":
        (root / VERIFIER.WORK_TASK_AGENT_FIX_DELTA).unlink()
    elif tamper == "missing_fourth":
        (root / VERIFIER.WORK_TASK_AGENT_DELTA).unlink()
    elif tamper == "missing_routing":
        (root / VERIFIER.WORK_ROUTING_DELTA).unlink()
    elif tamper == "parent_bytes":
        with (root / VERIFIER.WORK_TASK_AGENT_DELTA).open("ab") as stream:
            stream.write(b" ")
    else:
        raw = b"def invalid(\n"
        (root / names["work"]).write_bytes(raw)
        manifest["changes"]["gateway"][names["work"]]["candidate_sha256"] = digest(raw)
        save(root, manifest)
    with pytest.raises((AssertionError, SyntaxError)):
        VERIFIER.verify()


def test_fifth_parent_binding_and_source_hashes_only_normalize_newlines(evidence):
    root, names, _, parents = evidence
    for name in (*parents, VERIFIER.WORK_TASK_AGENT_FIX_DELTA, *names.values()):
        target = root / name
        target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))
    assert VERIFIER.verify()["candidate"]["work_task_agent_fix_delta"]["status"] == "NOT_DEPLOYED"


@pytest.mark.skipif(shutil.which("git") is None, reason="Git executable is required for the synthetic archive integration test")
def test_requested_git_archive_includes_fifth_layer_and_ignores_working_tree_edits(evidence):
    root, names, _, _ = evidence
    for directory in (VERIFIER.RUNNER, VERIFIER.FORMAT_BASELINE):
        target = root / directory / ".keep"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"synthetic\n")

    def git(*args):
        return subprocess.check_output(["git", "-c", "user.name=Synthetic Test", "-c", "user.email=synthetic@example.test", *args], cwd=root, stderr=subprocess.STDOUT)

    manifest = (root / VERIFIER.WORK_TASK_AGENT_FIX_DELTA).read_bytes()
    (root / VERIFIER.WORK_TASK_AGENT_FIX_DELTA).unlink()
    (root / names["fix_new"]).unlink()
    for key, step in {"work": 1, "incident": 2, "normalized": 1, "base": 0, "new": 3, "fresh": 4}.items():
        (root / names[key]).write_bytes(f"VALUE = {step}\n".encode())
    git("init", "--quiet")
    git("add", ".")
    git("commit", "--quiet", "-m", "synthetic fourth source seal")
    parent_ref = git("rev-parse", "HEAD").decode().strip()
    (root / VERIFIER.WORK_TASK_AGENT_FIX_DELTA).write_bytes(manifest)
    for name in names.values():
        (root / name).write_bytes(b"VALUE = 5\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "synthetic fifth source seal")
    ref = git("rev-parse", "HEAD").decode().strip()
    assert VERIFIER.verify(ref)["candidate"]["work_task_agent_fix_delta"]["changed_sources"] == 7
    assert "work_task_agent_fix_delta" not in VERIFIER.verify(parent_ref)["candidate"]
    (root / VERIFIER.WORK_TASK_AGENT_FIX_DELTA).unlink()
    (root / names["work"]).write_bytes(b"VALUE = 6\n")
    assert VERIFIER.verify(ref)["git_ref"] == ref
    with pytest.raises(AssertionError):
        VERIFIER.verify()

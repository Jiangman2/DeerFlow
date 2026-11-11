"""A planner-only repair seal must not replace the five earlier source layers."""

import copy
import shutil
import subprocess

import pytest
import test_work_task_agent_fix_source_manifest as previous

VERIFIER = previous.VERIFIER
digest = previous.digest
serialized = previous.serialized
AGENT = "backend/app/integrations/work/agent.py"


@pytest.fixture
def evidence(tmp_path, monkeypatch):
    root, _, repair, parents = previous.evidence.__wrapped__(tmp_path, monkeypatch)
    repair = copy.deepcopy(repair)
    repair["changes"]["gateway"][AGENT] = {"base_sha256": None, "candidate_sha256": digest(b"VALUE = 5\n")}
    repair_raw = serialized(repair)
    (root / VERIFIER.WORK_TASK_AGENT_FIX_DELTA).write_bytes(repair_raw)
    target = root / AGENT
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"VALUE = 6\n")
    model_fix = {
        "schema": "work-task-agent-model-fix-source-candidate.v1",
        "status": "NOT_DEPLOYED",
        "base_commit": VERIFIER.WORK_ROUTING_BASE_COMMIT,
        "base_lock_sha256": digest(parents[VERIFIER.LOCK]),
        "parent_candidate_sha256": digest(repair_raw),
        "scope": "Synthetic planner-only fix, no deployment.",
        "changes": {"gateway": {AGENT: {"base_sha256": digest(b"VALUE = 5\n"), "candidate_sha256": digest(b"VALUE = 6\n")}}, "frontend": {}},
    }
    (root / VERIFIER.WORK_TASK_AGENT_MODEL_FIX_DELTA).write_bytes(serialized(model_fix))
    return root, model_fix, {**parents, VERIFIER.WORK_TASK_AGENT_FIX_DELTA: repair_raw}


def save(root, manifest):
    (root / VERIFIER.WORK_TASK_AGENT_MODEL_FIX_DELTA).write_bytes(serialized(manifest))


def test_sixth_overlay_is_a_single_agent_candidate_and_preserves_five_parent_layers(evidence):
    root, _, parents = evidence
    result = VERIFIER.verify()
    assert result["status"] == "CANDIDATE_SOURCE_VERIFIED" and result["production_modified"] is False
    assert result["candidate"]["work_task_agent_model_fix_delta"] == {"status": "NOT_DEPLOYED", "base_commit": VERIFIER.WORK_ROUTING_BASE_COMMIT, "changed_sources": 1}
    assert result["candidate"]["work_task_agent_fix_delta"]["changed_sources"] == 8
    for name, raw in parents.items():
        assert (root / name).read_bytes() == raw


@pytest.mark.parametrize(
    "tamper",
    [
        "null",
        "schema",
        "status",
        "base_commit",
        "unknown_field",
        "lock",
        "parent",
        "parent_source",
        "change_schema",
        "hash",
        "hash_type",
        "gateway_type",
        "frontend_type",
        "frontend",
        "runner",
        "missing_agent",
        "other_work_file",
        "outside",
        "traversal",
        "backslash",
        "noncanonical",
    ],
)
def test_sixth_overlay_rejects_changed_authority_parent_and_any_other_source_scope(evidence, tamper):
    root, manifest, _ = evidence
    bad = copy.deepcopy(manifest)
    if tamper == "null":
        bad = None
    elif tamper in {"schema", "status", "base_commit"}:
        bad[tamper] = {"schema": "work-task-agent-fix-source-candidate.v1", "status": "DEPLOYED", "base_commit": "c" * 40}[tamper]
    elif tamper == "unknown_field":
        bad["production_accepted"] = True
    elif tamper in {"lock", "parent"}:
        bad["base_lock_sha256" if tamper == "lock" else "parent_candidate_sha256"] = "0" * 64
    elif tamper == "parent_source":
        bad["changes"]["gateway"][AGENT]["base_sha256"] = digest(b"VALUE = 4\n")
    elif tamper == "change_schema":
        bad["changes"]["gateway"][AGENT]["skip"] = True
    elif tamper in {"hash", "hash_type"}:
        bad["changes"]["gateway"][AGENT]["candidate_sha256"] = "G" * 64 if tamper == "hash" else None
    elif tamper in {"gateway_type", "frontend_type"}:
        bad["changes"][tamper.removesuffix("_type")] = []
    elif tamper == "frontend":
        bad["changes"]["frontend"]["frontend/src/example.ts"] = {"base_sha256": None, "candidate_sha256": "0" * 64}
    elif tamper == "runner":
        bad["changes"]["ppt-master-runner"] = {}
    elif tamper == "missing_agent":
        bad["changes"]["gateway"] = {}
    else:
        name = {
            "other_work_file": "backend/app/integrations/work/drafts.py",
            "outside": "backend/tests/other.py",
            "traversal": "backend/app/../../agent.py",
            "backslash": "backend/app/integrations/work\\agent.py",
            "noncanonical": "backend/app/integrations/work/./agent.py",
        }[tamper]
        bad["changes"]["gateway"][name] = {"base_sha256": None, "candidate_sha256": "0" * 64}
    save(root, bad)
    with pytest.raises(AssertionError):
        VERIFIER.verify()


@pytest.mark.parametrize("tamper", ["source", "unlisted", "missing_sixth", "missing_fifth", "parent_bytes", "invalid_python"])
def test_sixth_overlay_never_exempts_missing_or_changed_sources(evidence, tamper):
    root, manifest, _ = evidence
    if tamper == "source":
        (root / AGENT).write_bytes(b"VALUE = 7\n")
    elif tamper == "unlisted":
        (root / "backend/app/unlisted.py").write_bytes(b"VALUE = 7\n")
    elif tamper == "missing_sixth":
        (root / VERIFIER.WORK_TASK_AGENT_MODEL_FIX_DELTA).unlink()
    elif tamper == "missing_fifth":
        (root / VERIFIER.WORK_TASK_AGENT_FIX_DELTA).unlink()
    elif tamper == "parent_bytes":
        with (root / VERIFIER.WORK_TASK_AGENT_FIX_DELTA).open("ab") as stream:
            stream.write(b" ")
    else:
        raw = b"def invalid(\n"
        (root / AGENT).write_bytes(raw)
        manifest["changes"]["gateway"][AGENT]["candidate_sha256"] = digest(raw)
        save(root, manifest)
    with pytest.raises((AssertionError, SyntaxError)):
        VERIFIER.verify()


def test_sixth_layer_only_normalizes_newlines(evidence):
    root, _, parents = evidence
    for name in (*parents, VERIFIER.WORK_TASK_AGENT_MODEL_FIX_DELTA, AGENT):
        target = root / name
        target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))
    assert VERIFIER.verify()["candidate"]["work_task_agent_model_fix_delta"]["status"] == "NOT_DEPLOYED"


@pytest.mark.skipif(shutil.which("git") is None, reason="Git executable is required for the synthetic archive integration test")
def test_requested_git_archive_includes_sixth_layer_and_retains_older_ref_compatibility(evidence):
    root, _, _ = evidence
    for directory in (VERIFIER.RUNNER, VERIFIER.FORMAT_BASELINE):
        target = root / directory / ".keep"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"synthetic\n")

    def git(*args):
        return subprocess.check_output(["git", "-c", "user.name=Synthetic Test", "-c", "user.email=synthetic@example.test", *args], cwd=root, stderr=subprocess.STDOUT)

    manifest = (root / VERIFIER.WORK_TASK_AGENT_MODEL_FIX_DELTA).read_bytes()
    (root / VERIFIER.WORK_TASK_AGENT_MODEL_FIX_DELTA).unlink()
    (root / AGENT).write_bytes(b"VALUE = 5\n")
    git("init", "--quiet")
    git("add", ".")
    git("commit", "--quiet", "-m", "synthetic fifth source seal")
    previous_ref = git("rev-parse", "HEAD").decode().strip()
    (root / VERIFIER.WORK_TASK_AGENT_MODEL_FIX_DELTA).write_bytes(manifest)
    (root / AGENT).write_bytes(b"VALUE = 6\n")
    git("add", ".")
    git("commit", "--quiet", "-m", "synthetic sixth source seal")
    ref = git("rev-parse", "HEAD").decode().strip()
    assert VERIFIER.verify(ref)["candidate"]["work_task_agent_model_fix_delta"]["changed_sources"] == 1
    assert "work_task_agent_model_fix_delta" not in VERIFIER.verify(previous_ref)["candidate"]
    (root / VERIFIER.WORK_TASK_AGENT_MODEL_FIX_DELTA).unlink()
    (root / AGENT).write_bytes(b"VALUE = 7\n")
    assert VERIFIER.verify(ref)["git_ref"] == ref
    with pytest.raises(AssertionError):
        VERIFIER.verify()

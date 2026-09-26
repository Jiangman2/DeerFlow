import importlib.util
import hashlib
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def probe(monkeypatch):
    directory = Path(__file__).parent
    monkeypatch.syspath_prepend(str(directory))
    spec = importlib.util.spec_from_file_location(
        "employee_delta_probe_tests", directory / "probe_gateway_delta.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_finder_executes_composed_bytes_not_checkout(probe, monkeypatch):
    name = "_employee_probe_unique_fixture"
    finder = probe.CandidateFinder(
        {name: ("VALUE = 'audited-plus-delta'\n", "candidate.py")}
    )
    monkeypatch.setattr(sys, "meta_path", [finder, *sys.meta_path])
    try:
        module = __import__(name)
        assert module.VALUE == "audited-plus-delta"
        assert finder.loaded == {name}
        assert module.__spec__.origin.startswith("employee-candidate:")
    finally:
        sys.modules.pop(name, None)


def test_already_imported_target_must_not_silently_use_checkout(probe, monkeypatch):
    finder = probe.CandidateFinder({"json": ("", "json.py")})
    before = list(sys.meta_path)
    with pytest.raises(RuntimeError, match="already imported"):
        finder.install()
    assert sys.meta_path == before


def test_preparation_requires_production_source_hash(probe, tmp_path):
    relative = next(iter(probe.RECIPES))
    (tmp_path / "SOURCE-BASELINE.json").write_text(
        json.dumps({"components": {"gateway": {"files": {relative: "0" * 64}}}})
    )
    target = tmp_path / "gateway" / relative
    target.parent.mkdir(parents=True)
    target.write_text("broken = True\n")
    with pytest.raises(ValueError, match="checksum"):
        probe.prepare(tmp_path, tmp_path)


def test_actual_preparation_includes_explicit_composed_targets(probe):
    root = Path(__file__).resolve().parents[2]
    baseline = Path(__file__).with_name("audited-baseline-20260924-supplement")
    if not baseline.is_dir():
        pytest.skip("requires supplied production source audit")
    modules, receipts = probe.prepare(baseline, root / "backend")
    expected = {
        path.removeprefix("packages/harness/")[:-3]
        .replace("/", ".")
        .removesuffix(".__init__")
        for path in probe.RECIPES
    }
    assert set(modules) == expected
    assert "gap_emitted" not in modules["app.gateway.services"][0]
    assert len(receipts) == len(probe.RECIPES) == 26
    assert (
        "asyncio.to_thread(read_employee_publication, thread_id, user_id)"
        in modules["deerflow.runtime.context_compaction"][0]
    )


def test_dependency_probe_executes_audited_scanner_and_auth_not_checkout(probe):
    root = Path(__file__).resolve().parents[2]
    baseline = Path(__file__).with_name("audited-baseline-20260924-dependencies")
    if not baseline.is_dir():
        pytest.skip("requires supplied dependency source audit")
    modules, receipts = probe.prepare(
        baseline, root / "backend", audited_dependencies=True
    )
    lock = json.loads((baseline / "SOURCE-BASELINE.json").read_text(encoding="utf-8"))
    expected = {p for p in lock["components"]["gateway"]["files"] if p.endswith(".py")}
    assert {item[1] for item in modules.values()} == expected
    for name in (
        "app.gateway.authz",
        "deerflow.skills.skillscan.orchestrator",
        "deerflow.skills.permissions",
    ):
        source, relative = modules[name]
        assert source == (baseline / "gateway" / relative).read_bytes().decode("utf-8")
    assert len(receipts) == 26


def test_audited_dependencies_must_match_evidence_before_import(
    probe, monkeypatch, tmp_path
):
    relative = "app/gateway/authz.py"
    target = tmp_path / "gateway" / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(b"CHANGED = True\n")
    lock = {
        "components": {
            "gateway": {"files": {relative: hashlib.sha256(b"original\n").hexdigest()}}
        }
    }
    (tmp_path / "SOURCE-BASELINE.json").write_text(json.dumps(lock))
    monkeypatch.setattr(probe, "RECIPES", {})
    with pytest.raises(ValueError, match="checksum"):
        probe.prepare(tmp_path, tmp_path, audited_dependencies=True)


def test_dependency_coverage_never_weakens_composed_module_gate(probe, capsys):
    from types import SimpleNamespace

    finder = probe.CandidateFinder(
        {"changed": ("", "changed.py"), "dependency": ("", "dep.py")}
    )
    finder.loaded.add("dependency")
    session = SimpleNamespace(exitstatus=0)
    probe.CoverageGate(finder, required={"changed"}).pytest_sessionfinish(session, 0)
    assert session.exitstatus == 1
    assert "unloaded=changed" in capsys.readouterr().out
    finder.loaded = {"changed"}
    session = SimpleNamespace(exitstatus=0)
    probe.CoverageGate(finder, required={"changed"}).pytest_sessionfinish(session, 0)
    assert session.exitstatus == 0
    assert "dependency_unloaded=dependency" in capsys.readouterr().out


def test_reviewed_candidate_includes_explicit_new_modules_and_rejects_missing_evidence(
    probe, monkeypatch
):
    root = Path(__file__).resolve().parents[2]
    baseline = Path(__file__).with_name("audited-baseline-20260924-dependencies")
    if not baseline.is_dir():
        pytest.skip("requires supplied dependency source audit")
    modules, receipts = probe.prepare(
        baseline, root / "backend", audited_dependencies=True, reviewed_candidate=True
    )
    assert len(receipts) == 46
    assert "deerflow.skills.agent_selection" in modules
    assert "deerflow.skills.package_files" not in modules
    assert "deerflow.agents.middlewares.skill_tool_policy_middleware" not in modules
    original = probe.read_review()
    tampered = {
        **original,
        "reviewed_candidates": {
            **original["reviewed_candidates"],
            "app/gateway/employee_knowledge.py": "0" * 64,
        },
    }
    monkeypatch.setattr(probe, "read_review", lambda: tampered)
    with pytest.raises(ValueError, match="checksum"):
        probe.prepare(baseline, root / "backend", reviewed_candidate=True)
    monkeypatch.setattr(probe, "read_review", lambda: original)
    with pytest.raises(ValueError, match="absence"):
        probe.prepare(
            baseline.with_name("audited-baseline-20260924-supplement"),
            root / "backend",
            reviewed_candidate=True,
        )


def test_candidate_review_is_not_permission_to_add_unreviewed_paths(
    probe, monkeypatch, tmp_path
):
    review = probe.read_review()
    review["additions"].append("../../outside.py")
    monkeypatch.setattr(probe, "read_review", lambda: review)
    (tmp_path / "SOURCE-BASELINE.json").write_text(
        json.dumps(
            {
                "components": {
                    "gateway": {
                        "image_id": review["source_image_id"],
                        "files": {},
                        "missing": [],
                    }
                }
            }
        )
    )
    with pytest.raises(ValueError, match="review"):
        probe.prepare(tmp_path, tmp_path, reviewed_candidate=True)


def test_staged_probe_gives_spawned_children_the_same_composed_source(probe, tmp_path):
    source, target = tmp_path / "checkout", tmp_path / "runtime"
    files = {
        "app/gateway/example.py": b"VALUE = 'checkout'\n",
        "packages/harness/deerflow/unchanged.py": b"VALUE = 2\n",
        "tests/test_example.py": b"def test_example(): pass\n",
        "app/__pycache__/example.pyc": b"stale bytecode",
        ".env": b"NOT_A_TEST_INPUT=secret",
    }
    for relative, content in files.items():
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    modules = {"app.gateway.example": ("VALUE = 'audited'\n", "app/gateway/example.py")}
    probe.stage_runtime(source, modules, target)
    assert (target / "app/gateway/example.py").read_bytes() == b"VALUE = 'audited'\n"
    assert (source / "app/gateway/example.py").read_bytes() == files[
        "app/gateway/example.py"
    ]
    assert (
        target / "packages/harness/deerflow/unchanged.py"
    ).read_bytes() == b"VALUE = 2\n"
    assert not (target / ".env").exists()
    assert not list(target.rglob("*.pyc"))
    with pytest.raises(ValueError, match="exists"):
        probe.stage_runtime(source, modules, target)


def test_staged_probe_rejects_escape_before_any_destination_write(probe, tmp_path):
    target = tmp_path / "runtime"
    with pytest.raises(ValueError, match="Unsafe"):
        probe.stage_runtime(tmp_path, {"bad": ("", "../outside.py")}, target)
    assert not target.exists()

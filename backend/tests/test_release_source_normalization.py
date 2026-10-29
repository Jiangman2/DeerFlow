"""Formatting provenance must reject executable changes, not just hash drift."""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / "deploy/enterprise/verify-release-source-20261023.py"
spec = importlib.util.spec_from_file_location("release_source_verifier", SOURCE)
assert spec is not None and spec.loader is not None
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def test_layout_normalization_preserves_syntax():
    assert verifier.syntax_fingerprint("def f():\n return (1 + 2)\n") == verifier.syntax_fingerprint("def f():\n    return 1 + 2\n")


def test_import_order_requires_explicit_opt_in():
    before = "import logging\nimport json\nVALUE = 3\n"
    after = "import json\nimport logging\nVALUE = 3\n"
    assert verifier.syntax_fingerprint(before) != verifier.syntax_fingerprint(after)
    assert verifier.syntax_fingerprint(before, import_order=True) == verifier.syntax_fingerprint(after, import_order=True)


@pytest.mark.parametrize(
    "candidate",
    [
        "import json as logging\nVALUE = 3\n",
        "import json\nVALUE = 4\n",
        "import json\nVALUE = dangerous()\n",
        "VALUE = 3\nimport json\n",
    ],
)
def test_executable_or_binding_changes_cannot_be_classified_as_formatting(candidate):
    assert verifier.syntax_fingerprint("import json\nVALUE = 3\n", import_order=True) != verifier.syntax_fingerprint(candidate, import_order=True)


def test_undeployed_candidate_overlay_retains_base_hash_and_checks_new_hash():
    lock = {"source_sets": {"gateway": {"backend/app/example.py": "a" * 64}}, "format_normalizations": {}}
    manifest = {
        "schema": "production-source-candidate.v1",
        "status": "NOT_DEPLOYED",
        "base_commit": "b" * 40,
        "base_lock_sha256": "c" * 64,
        "changes": {"backend/app/example.py": {"base_sha256": "a" * 64, "candidate_sha256": "d" * 64}, "backend/app/new.py": {"base_sha256": None, "candidate_sha256": "e" * 64}},
    }
    assert verifier.candidate_overrides(lock, manifest, "c" * 64) == {"backend/app/example.py": "d" * 64, "backend/app/new.py": "e" * 64}


@pytest.mark.parametrize("field,value", [("status", "DEPLOYED"), ("base_lock_sha256", "f" * 64)])
def test_candidate_manifest_cannot_claim_deployment_or_change_base_lock(field, value):
    manifest = {"schema": "production-source-candidate.v1", "status": "NOT_DEPLOYED", "base_commit": "b" * 40, "base_lock_sha256": "c" * 64, "changes": {}}
    manifest[field] = value
    with pytest.raises(AssertionError):
        verifier.candidate_overrides({"source_sets": {"gateway": {}}}, manifest, "c" * 64)


def test_candidate_manifest_rejects_incorrect_parent_hash_and_outside_paths():
    manifest = {"schema": "production-source-candidate.v1", "status": "NOT_DEPLOYED", "base_commit": "b" * 40, "base_lock_sha256": "c" * 64, "changes": {"backend/app/example.py": {"base_sha256": "f" * 64, "candidate_sha256": "d" * 64}}}
    with pytest.raises(AssertionError):
        verifier.candidate_overrides({"source_sets": {"gateway": {"backend/app/example.py": "a" * 64}}}, manifest, "c" * 64)
    manifest["changes"] = {"frontend/example.py": {"base_sha256": None, "candidate_sha256": "d" * 64}}
    with pytest.raises(AssertionError):
        verifier.candidate_overrides({"source_sets": {"gateway": {}}}, manifest, "c" * 64)


@pytest.mark.parametrize("tamper", [None, "changed_source", "unlisted_source", "missing_manifest"])
def test_candidate_verification_rejects_undeclared_or_changed_source(tmp_path, monkeypatch, tamper):
    lock = {"source_sets": {"gateway": {"backend/app/example.py": "a" * 64}, "frontend": {}}, "format_normalizations": {}, "scope": {"test": "synthetic"}}
    lock_raw = json.dumps(lock).encode()
    manifest = {
        "schema": "production-source-candidate.v1",
        "status": "NOT_DEPLOYED",
        "base_commit": "b" * 40,
        "base_lock_sha256": hashlib.sha256(lock_raw).hexdigest(),
        "changes": {"backend/app/example.py": {"base_sha256": "a" * 64, "candidate_sha256": hashlib.sha256(b"VALUE = 1\n").hexdigest()}},
    }
    for path, raw in ((verifier.LOCK, lock_raw), (verifier.CANDIDATE, json.dumps(manifest).encode()), ("backend/app/example.py", b"VALUE = 1\n")):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    monkeypatch.setattr(verifier, "ROOT", tmp_path)
    if tamper == "changed_source":
        (tmp_path / "backend/app/example.py").write_text("VALUE = 2\n")
    elif tamper == "unlisted_source":
        (tmp_path / "backend/app/extra.py").write_text("VALUE = 3\n")
    elif tamper == "missing_manifest":
        (tmp_path / verifier.CANDIDATE).unlink()
    if tamper:
        with pytest.raises(AssertionError):
            verifier.verify()
    else:
        result = verifier.verify()
        assert result["status"] == "CANDIDATE_SOURCE_VERIFIED"
        assert result["candidate"]["status"] == "NOT_DEPLOYED"

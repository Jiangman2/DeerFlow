"""Reject drift or unregistered scope in the standards portal source candidate."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/enterprise"))
import standards_portal_source_20261117 as source


@pytest.fixture(scope="module")
def data():
    return {name: (ROOT / name).read_bytes() for name in source.FILES | {source.MANIFEST}}


def test_exact_six_sources_are_registered_without_production_acceptance(data):
    seal = source.qualify(data.__getitem__)
    result = source.summary(seal)
    assert result["status"] == "NOT_DEPLOYED"
    assert result["base_commit"] == "c0c6dfa547a1963677db974a98f3dbc47b9c130e"
    assert result["runtime_sources_verified"] == 6
    assert result["frontend_runtime_paths"] == sorted(source.FILES)
    assert result["development_guides_verified"] == 1
    assert not result["production_acceptance_claimed"]
    assert not result["historical_inventories_modified"]


@pytest.mark.parametrize("path", sorted(source.FILES))
def test_any_changed_runtime_source_is_rejected(data, path):
    changed = dict(data, **{path: data[path] + b"\n// unregistered change\n"})
    with pytest.raises(ValueError, match="STANDARDS_PORTAL_SOURCE_CHANGED"):
        source.qualify(changed.__getitem__)


@pytest.mark.parametrize("path", sorted(source.FILES))
def test_any_missing_runtime_source_is_rejected(data, path):
    changed = {name: raw for name, raw in data.items() if name != path}
    with pytest.raises(ValueError, match="STANDARDS_PORTAL_FILE_MISSING"):
        source.qualify(changed.__getitem__)


def test_changed_manifest_is_rejected(data):
    changed = dict(data, **{source.MANIFEST: data[source.MANIFEST] + b" "})
    with pytest.raises(ValueError, match="STANDARDS_PORTAL_SEAL_CHANGED"):
        source.qualify(changed.__getitem__)


@pytest.mark.parametrize("path", sorted(source.FILES))
def test_missing_manifest_cannot_hide_any_candidate_source(data, path):
    with pytest.raises(ValueError, match="STANDARDS_PORTAL_MANIFEST_REQUIRED"):
        source.qualify({path: data[path]}.__getitem__)


def test_historical_tree_without_any_candidate_is_unchanged():
    assert source.qualify({}.__getitem__) is None
    assert source.summary(None) is None


def test_checkout_line_endings_do_not_change_registered_utf8_content(data):
    windows = {name: raw.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
               for name, raw in data.items()}
    assert source.qualify(windows.__getitem__) == source.qualify(data.__getitem__)


@pytest.mark.parametrize("change,error", [
    (lambda seal: seal.update(schema="other.v1"), "STANDARDS_PORTAL_SCHEMA"),
    (lambda seal: seal.update(status="DEPLOYED"), "STANDARDS_PORTAL_STATUS"),
    (lambda seal: seal.update(base_commit="other-parent"), "STANDARDS_PORTAL_PARENT"),
    (lambda seal: seal.update(production_acceptance_claimed=True), "STANDARDS_PORTAL_OVERCLAIM"),
    (lambda seal: seal.update(runtime_modified_by_registration=True), "STANDARDS_PORTAL_OVERCLAIM"),
    (lambda seal: seal["source_files"].pop(sorted(source.FILES)[0]), "STANDARDS_PORTAL_SCOPE"),
    (lambda seal: seal["source_files"].update({"frontend/src/unregistered.ts": {"kind": "runtime-source", "sha256": "0" * 64}}), "STANDARDS_PORTAL_SCOPE"),
    (lambda seal: seal["source_files"][source.GUIDE].update(kind="runtime-source"), "STANDARDS_PORTAL_KIND"),
])
def test_approved_seal_still_cannot_expand_scope_or_claim_deployment(data, monkeypatch, change, error):
    seal = json.loads(data[source.MANIFEST])
    change(seal)
    raw = json.dumps(seal).encode()
    monkeypatch.setattr(source, "MANIFEST_SHA256", source.digest(raw))
    with pytest.raises(ValueError, match=error):
        source.qualify(lambda name: raw if name == source.MANIFEST else data[name])


def test_committed_verifier_reads_only_exact_git_blobs(data, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "standards_portal_verifier", ROOT / "deploy/enterprise/verify-standards-portal-source-20261117.py")
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)

    def git(argv, **kwargs):
        assert argv == ["git", "cat-file", "--batch"]
        paths = kwargs["input"].decode().splitlines()
        assert set(paths) == {"chosen-ref:" + name for name in data}
        return b"".join(b"0" * 40 + b" blob " + str(len(data[name.split(":", 1)[1]])).encode()
                        + b"\n" + data[name.split(":", 1)[1]] + b"\n" for name in paths)

    monkeypatch.setattr(verifier.subprocess, "check_output", git)
    result = verifier.verify("chosen-ref")
    assert result["source_verified"] and not result["runtime_accepted"]
    assert not result["production_commands_executed"]

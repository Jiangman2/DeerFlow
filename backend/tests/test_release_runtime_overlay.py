"""An image tag alone cannot prove a mounted production UI patch is archived."""

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("release_overlay", ROOT / "deploy/enterprise/verify-release-source-20261023.py")
VERIFIER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFIER)


@pytest.fixture
def evidence():
    lock = json.loads((ROOT / "deploy/enterprise/production-source-lock-20261026.json").read_text())
    patch = lock["runtime_patch"]
    paths = [*patch["files"], *patch["qualified_source_files"], lock["current_observation"]["path"]]
    return lock, {name: (ROOT / name).read_bytes() for name in paths}


def test_accepted_archive_matches_independently_observed_configmap(evidence):
    lock, files = evidence
    result = VERIFIER.verify_runtime_patch(files.__getitem__, lock)
    assert result["configmap_hash"] == "PASS"
    assert result["full_base_image_source_mapping"] == "NOT_PROVEN"


def test_old_locks_keep_their_historical_scope():
    assert VERIFIER.verify_runtime_patch(lambda name: pytest.fail(name), {}) is None


@pytest.mark.parametrize("kind", ["payload", "configmap", "source", "observation", "deployment", "image"])
def test_runtime_archive_refuses_independent_evidence_drift(evidence, kind):
    lock, files = copy.deepcopy(evidence)
    patch = lock["runtime_patch"]
    if kind == "payload":
        files[patch["archive_prefix"] + "install.sh"] += b"# unexpected change\n"
    elif kind == "configmap":
        patch["configmap"]["content_sha256"] = "0" * 64
    elif kind == "source":
        files[next(iter(patch["qualified_source_files"]))] += b"// drift\n"
    elif kind == "observation":
        files[lock["current_observation"]["path"]] += b" "
    elif kind == "deployment":
        lock["current_observation"]["bindings"]["gateway"]["generation"] += 1
    else:
        lock["images"]["gateway"] = "unrelated-image"
    with pytest.raises(AssertionError):
        VERIFIER.verify_runtime_patch(files.__getitem__, lock)

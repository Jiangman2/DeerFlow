"""Candidate image inputs are not an authorized production rollout package."""

import base64
import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


HERE = Path(__file__).parent


@pytest.fixture(scope="module")
def bundle_module():
    import sys

    sys.path.insert(0, str(HERE))
    spec = importlib.util.spec_from_file_location(
        "employee_candidate_bundle_tests", HERE / "candidate_bundle.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    sys.path.remove(str(HERE))


@pytest.fixture(scope="module")
def composed(bundle_module):
    baseline = HERE / "audited-baseline-20260924-dependencies"
    if not baseline.is_dir():
        pytest.skip("requires supplied source audit")
    return bundle_module.compose_inputs(baseline, HERE.resolve().parents[1])


def test_candidate_bundle_contains_only_reviewed_components(composed):
    assert set(composed) == {"gateway", "frontend", "provisioner"}
    assert {name: len(value["entries"]) for name, value in composed.items()} == {
        "gateway": 46,
        "frontend": 33,
        "provisioner": 1,
    }
    for component in composed.values():
        assert component["ready_for_rollout"] is False
        assert len(component["source_report_sha256s"]) == 3
        for entry in component["entries"]:
            assert (
                hashlib.sha256(
                    base64.b64decode(entry["content_base64"], validate=True)
                ).hexdigest()
                == entry["target_sha256"]
            )
    assert "pnpm-lock.yaml" in composed["frontend"]["anchors"]
    assert "src/components/ui/dialog.tsx" in composed["frontend"]["anchors"]
    assert (
        "packages/harness/deerflow/skills/skillscan/orchestrator.py"
        in composed["gateway"]["anchors"]
    )
    assert not any(
        e["path"].endswith("package_files.py") for e in composed["gateway"]["entries"]
    )


def test_candidate_materialization_is_checksummed_and_never_overwrites(
    bundle_module, composed, tmp_path
):
    destination = tmp_path / "candidate"
    bundle_module.materialize(composed, destination)
    index = json.loads((destination / "CANDIDATE.json").read_bytes())
    assert index["ready_for_rollout"] is False
    assert index["images_built"] is False
    assert not list(destination.glob("*rollout*"))
    assert not list(destination.glob("*rollback*"))
    for line in (destination / "PACKAGE.SHA256").read_text().splitlines():
        expected, name = line.split("  ", 1)
        assert hashlib.sha256((destination / name).read_bytes()).hexdigest() == expected
    for component in composed:
        source = (destination / ("Dockerfile." + component)).read_text()
        assert "RUN --network=none" in source
        assert (
            "pip install" not in source
            and "pnpm install" not in source
            and "apt-get" not in source
        )
        assert "# syntax=" not in source  # no implicit remote Dockerfile frontend
    before = (destination / "CANDIDATE.json").read_bytes()
    with pytest.raises(ValueError, match="exists"):
        bundle_module.materialize(composed, destination)
    assert (destination / "CANDIDATE.json").read_bytes() == before


def test_tampered_delta_is_rejected_before_candidate_directory_exists(
    bundle_module, composed, tmp_path
):
    altered = copy.deepcopy(composed)
    altered["gateway"]["entries"][0]["content_base64"] = base64.b64encode(
        b"unreviewed\n"
    ).decode()
    destination = tmp_path / "candidate"
    with pytest.raises(ValueError, match="checksum"):
        bundle_module.materialize(altered, destination)
    assert not destination.exists()


def test_all_eighty_reviewed_files_apply_over_captured_sources(
    bundle_module, composed, tmp_path
):
    baseline = HERE / "audited-baseline-20260924-dependencies"
    lock = json.loads((baseline / "SOURCE-BASELINE.json").read_bytes())
    for component, document in composed.items():
        root = tmp_path / component
        root.mkdir()
        for relative in lock["components"][component]["files"]:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((baseline / component / relative).read_bytes())
        manifest = tmp_path / (component + "-delta.json")
        manifest.write_bytes(bundle_module.json_bytes(document))
        command = (
            ["node", str(HERE / "apply_image_delta.mjs")]
            if component == "frontend"
            else [sys.executable, str(HERE / "apply_image_delta.py")]
        )
        for mode in ("--check-only", "--image-build-stage", "--check-only"):
            completed = subprocess.run(
                [*command, str(root), str(manifest), mode],
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert completed.returncode == 0, completed.stderr
        for entry in document["entries"]:
            assert (
                hashlib.sha256((root / entry["path"]).read_bytes()).hexdigest()
                == entry["target_sha256"]
            )
        for relative, expected in document["anchors"].items():
            assert (
                hashlib.sha256((root / relative).read_bytes()).hexdigest() == expected
            )


def test_candidate_directory_links_rejected(bundle_module, composed, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("requires host symlink permission; exercised on Linux")
    with pytest.raises(ValueError, match="Linked"):
        bundle_module.materialize(composed, linked / "candidate")
    assert not list(outside.iterdir())


def test_package_verifier_refuses_extra_or_tampered_content(
    bundle_module, composed, tmp_path
):
    from verify_candidate import verify

    destination = bundle_module.materialize(composed, tmp_path / "candidate")
    assert verify(destination)["images_built"] is False
    extra = destination / "override.json"
    extra.write_bytes(b"{}")
    with pytest.raises(ValueError, match="Unexpected"):
        verify(destination)
    extra.unlink()
    (destination / "gateway-delta.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="checksum"):
        verify(destination)


def test_overlay_aware_bundle_binds_real_report_and_does_not_change_deltas(
    bundle_module, composed, tmp_path
):
    from verify_candidate import verify

    report = HERE / "audited-source-overlays-20260924.json"
    if not report.exists():
        pytest.skip("requires internal source-overlay evidence")
    before = copy.deepcopy(composed)
    destination = bundle_module.materialize(
        composed, tmp_path / "overlay-candidate", overlay_report=report.read_bytes()
    )
    assert composed == before
    result = verify(destination)
    assert result["source_overlay_evidence"] == "AUDITED"
    assert result["ready_for_rollout"] is False
    source = (destination / "Dockerfile.gateway").read_text()
    assert source.index("effective_sources.py /app/backend") < source.index(
        "apply_image_delta.py /app/backend"
    )
    evidence = json.loads((destination / "source-overlays.json").read_bytes())
    assert (
        evidence["source_report_sha256"]
        == hashlib.sha256(report.read_bytes()).hexdigest()
    )
    assert len(evidence["report"]["workloads"]["gateway"]["source_bindings"]) == 15
    # Even a rechecksummed internal edit must agree with the frozen index.
    payload = json.loads((destination / "source-overlays.json").read_bytes())
    payload["report"]["workloads"]["gateway"]["deployment_uid"] = "wrong"
    (destination / "source-overlays.json").write_bytes(
        bundle_module.json_bytes(payload)
    )
    (destination / "PACKAGE.SHA256").write_text(
        "".join(
            hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n"
            for path in sorted(destination.iterdir())
            if path.name != "PACKAGE.SHA256"
        )
    )
    with pytest.raises(ValueError, match="Effective source manifest"):
        verify(destination)


def test_delivery_archive_is_flat_build_only_and_cannot_overwrite(
    bundle_module, composed, tmp_path
):
    import tarfile
    from package_candidate import package
    from verify_candidate import verify

    source = bundle_module.materialize(composed, tmp_path / "candidate")
    archive = tmp_path / "deerflow-employee-sharing-build-inputs-20260924-v2.tar.gz"
    digest = package(source, archive)
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == digest
    assert archive.with_name(archive.name + ".sha256").read_bytes() == (
        digest + "  " + archive.name + "\n"
    ).encode("ascii")
    unpacked = tmp_path / "unpacked"
    unpacked.mkdir()
    # Check fixed paths ourselves; no unrestricted tar extraction in the test.
    with tarfile.open(archive, "r:gz") as stream:
        for item in stream:
            parts = item.name.split("/")
            assert len(parts) == 2 and parts[0] == archive.name[:-7]
            assert item.isfile() and item.mode == 0o644
            (unpacked / parts[1]).write_bytes(stream.extractfile(item).read())
    assert verify(unpacked)["ready_for_rollout"] is False
    before = archive.read_bytes()
    with pytest.raises(ValueError, match="exists"):
        package(source, archive)
    assert archive.read_bytes() == before
    with pytest.raises(ValueError, match="BUILD INPUTS"):
        package(source, tmp_path / "production-hotfix.tar.gz")

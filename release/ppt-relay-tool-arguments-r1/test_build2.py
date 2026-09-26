"""Deep image graph support must retain every offline identity/content gate."""

import ast
import hashlib
import importlib.util
import json
import tarfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
BASE = (
    Path.home()
    / "Downloads/deerflow-ppt-relay-tool-arguments-20260924-r1-build1.tar.gz"
)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def packaged(tmp_path, monkeypatch):
    module = load(HERE / "package_build2.py", "ppt_build2_pack")
    path = module.package(BASE, tmp_path)
    with tarfile.open(path) as bundle:
        bundle.extractall(tmp_path, filter="data")
    root = tmp_path / module.NAME
    monkeypatch.syspath_prepend(str(root))
    import sys

    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    return module, root


@pytest.fixture
def verifier(packaged):
    return load(packaged[1] / "verify_oci_base.py", "ppt_build2_verify")


def graph(tmp_path, count, problem=None):
    root = tmp_path / "oci"
    blobs = root / "blobs/sha256"
    blobs.mkdir(parents=True)

    def blob(data, media):
        raw = data if isinstance(data, bytes) else json.dumps(data).encode()
        digest = hashlib.sha256(raw).hexdigest()
        (blobs / digest).write_bytes(raw)
        return {"digest": "sha256:" + digest, "size": len(raw), "mediaType": media}

    layers = [
        blob(("layer-%d" % i).encode(), "application/vnd.oci.image.layer.v1.tar+gzip")
        for i in range(count)
    ]
    diff_ids = ["sha256:" + "%064x" % i for i in range(count)]
    if problem == "diff_count":
        diff_ids.pop()
    if problem == "bad_diff":
        diff_ids[-1] = "DO-NOT-LOG-secret"
    if problem == "nonstring_diff":
        diff_ids[-1] = None
    rootfs = {"type": "layers", "diff_ids": diff_ids}
    if problem == "rootfs":
        rootfs = None
    if problem == "rootfs_type":
        rootfs["type"] = "unexpected"
    if problem == "external":
        layers[-1]["urls"] = ["https://must-not-fetch.invalid/layer"]
    if problem == "size":
        layers[-1]["size"] += 1
    config = blob(
        {
            "os": "linux",
            "architecture": "amd64" if problem == "arch" else "arm64",
            "rootfs": rootfs,
            "config": {"Env": ["API_KEY=DO-NOT-LOG-secret"]},
        },
        "application/vnd.oci.image.config.v1+json",
    )
    manifest = blob(
        {
            "schemaVersion": 2,
            "config": config,
            "layers": None if problem == "layers_type" else layers,
        },
        "application/vnd.oci.image.manifest.v1+json",
    )
    manifest["annotations"] = {
        "io.containerd.image.name": "deerflow.local/fixture:exact"
    }
    (root / "index.json").write_text(
        json.dumps({"schemaVersion": 2, "manifests": [manifest]})
    )
    (root / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
    if problem in ("missing", "corrupt"):
        last = blobs / layers[-1]["digest"].split(":")[1]
        if problem == "missing":
            last.unlink()
        else:
            raw = last.read_bytes()
            last.write_bytes(raw[:-1] + b"X")
    return root, {
        "source_image": "deerflow.local/fixture:exact",
        "source_image_id": "sha256:" + "f" * 64
        if problem == "identity"
        else config["digest"],
    }


@pytest.mark.parametrize("count", [1, 128, 129, 345, 347, 1024])
def test_supported_graph_hashes_every_layer(verifier, tmp_path, count, monkeypatch):
    root, expected = graph(tmp_path, count)
    original = verifier.blob
    visited = []

    def tracked(root, descriptor, *, metadata):
        if not metadata:
            visited.append(descriptor["digest"])
        return original(root, descriptor, metadata=metadata)

    monkeypatch.setattr(verifier, "blob", tracked)
    result = verifier.verify(root, expected)
    assert result["layers_verified"] == len(visited) == count
    assert len(set(visited)) == count and not result["ready_for_rollout"]


@pytest.mark.parametrize(
    "problem",
    [
        "diff_count",
        "bad_diff",
        "nonstring_diff",
        "rootfs",
        "rootfs_type",
        "layers_type",
        "external",
        "size",
        "arch",
        "identity",
        "missing",
        "corrupt",
    ],
)
def test_deep_graph_does_not_bypass_any_safety_check(verifier, tmp_path, problem):
    root, expected = graph(tmp_path, 345, problem)
    with pytest.raises((ValueError, OSError)) as caught:
        verifier.verify(root, expected)
    assert "DO-NOT-LOG-secret" not in str(caught.value)


@pytest.mark.parametrize("count", [0, 1025])
def test_layer_work_budget_is_still_bounded(verifier, tmp_path, count):
    root, expected = graph(tmp_path, count)
    with pytest.raises(ValueError, match="layer_count_out_of_range"):
        verifier.verify(root, expected)


def test_original_failure_reproduced_but_corrected_bundle_accepts(verifier, tmp_path):
    root, expected = graph(tmp_path, 345)
    with tarfile.open(BASE) as bundle:
        member = next(m for m in bundle if m.name.endswith("/verify_oci_base.py"))
        namespace = {}
        exec(
            compile(bundle.extractfile(member).read(), "frozen-build1", "exec"),
            namespace,
        )
    with pytest.raises(ValueError, match="Invalid image layer graph"):
        namespace["verify"](root, expected)
    assert verifier.verify(root, expected)["layers_verified"] == 345


def test_index_fanout_limit_not_changed(verifier):
    with pytest.raises(ValueError, match="Invalid OCI index"):
        verifier.manifests({"schemaVersion": 2, "manifests": [{}] * 129})


def test_new_bundle_scope_integrity_and_host_compatibility(packaged):
    module, root = packaged
    old = module.read_base(BASE)
    changed = {
        name
        for name, raw in old.items()
        if name != "PACKAGE.SHA256" and (root / name).read_bytes() != raw
    }
    assert changed == {"verify_oci_base.py", "BUILD-README.md", "BUILD-VERIFICATION.md"}
    for name in (
        "BASELINE.json",
        "PATCH.json",
        "codex_responses_adapter.py",
        "Dockerfile.gateway",
        "isolated_buildkit.py",
        "bounded_runc.py",
        "build-candidate.py",
        "prepare_oci.py",
        "verify_built_image.py",
        "build_resources.py",
    ):
        assert (root / name).read_bytes() == old[name]
    listed = set()
    for line in (root / "PACKAGE.SHA256").read_text().splitlines():
        digest, name = line.split("  ", 1)
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
        listed.add(name)
    assert listed == {p.name for p in root.iterdir()} - {"PACKAGE.SHA256"}
    for path in root.glob("*.py"):
        if path.name not in ("codex_responses_adapter.py", "smoke.py"):
            ast.parse(path.read_text(), feature_version=(3, 7))
    controller = load(root / "build-candidate.py", "ppt_build2_controller")
    controller.verify_package(root)
    with pytest.raises(ValueError, match="exists"):
        module.package(BASE, root.parent)

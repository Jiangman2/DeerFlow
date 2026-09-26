"""Runtime image IDs must bind to an exact complete local ARM64 OCI graph."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).parent
OCI = "application/vnd.oci.image."


@pytest.fixture
def verifier():
    spec = importlib.util.spec_from_file_location(
        "employee_oci_test", HERE / "verify_oci_base.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def layout(tmp_path, *, arch="arm64", indexed=False, layer_urls=False, layer_count=1):
    root = tmp_path / "oci"
    blobs = root / "blobs/sha256"
    blobs.mkdir(parents=True)

    def blob(data, media_type):
        content = data if isinstance(data, bytes) else json.dumps(data).encode()
        digest = hashlib.sha256(content).hexdigest()
        (blobs / digest).write_bytes(content)
        return {
            "digest": "sha256:" + digest,
            "size": len(content),
            "mediaType": media_type,
        }

    layers = [
        blob(
            b"opaque compressed layer fixture"
            if index == 0
            else ("opaque layer fixture %s" % index).encode(),
            OCI + "layer.v1.tar+gzip",
        )
        for index in range(layer_count)
    ]
    layer = layers[0]
    if layer_urls:
        layer["urls"] = ["https://must-not-fetch.invalid/layer"]
    config = blob(
        {
            "os": "linux",
            "architecture": arch,
            "rootfs": {
                "type": "layers",
                "diff_ids": ["sha256:" + "a" * 64] * layer_count,
            },
        },
        OCI + "config.v1+json",
    )
    manifest = blob(
        {"schemaVersion": 2, "config": config, "layers": layers},
        OCI + "manifest.v1+json",
    )
    selected = copy.deepcopy(manifest)
    if indexed:
        selected["platform"] = {"os": "linux", "architecture": arch}
        selected = blob(
            {"schemaVersion": 2, "manifests": [selected]}, OCI + "index.v1+json"
        )
    selected["annotations"] = {"io.containerd.image.name": "deerflow.local/base:exact"}
    (root / "index.json").write_text(
        json.dumps({"schemaVersion": 2, "manifests": [selected]})
    )
    (root / "oci-layout").write_text(json.dumps({"imageLayoutVersion": "1.0.0"}))
    expected = {
        "source_image": "deerflow.local/base:exact",
        "source_image_id": config["digest"],
    }
    return root, expected, config, manifest, selected, layer


@pytest.mark.parametrize("identity", ["config", "manifest", "index"])
def test_complete_local_graph_accepts_exact_runtime_identity(
    verifier, tmp_path, identity
):
    root, expected, config, manifest, selected, _ = layout(
        tmp_path, indexed=identity == "index"
    )
    expected["source_image_id"] = {
        "config": config,
        "manifest": manifest,
        "index": selected,
    }[identity]["digest"]
    before = {
        p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }
    result = verifier.verify(root, expected)
    assert result["manifest_digest"] == manifest["digest"]
    assert result["layers_verified"] == 1
    assert result["ready_for_rollout"] is False
    assert before == {
        p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


@pytest.mark.parametrize(
    "problem",
    [
        "arch",
        "identity",
        "name",
        "corrupt",
        "missing",
        "size",
        "duplicate",
        "external",
        "escape",
    ],
)
def test_incomplete_or_wrong_oci_base_never_passes(verifier, tmp_path, problem):
    root, expected, _, _, _, layer = layout(
        tmp_path,
        arch="amd64" if problem == "arch" else "arm64",
        layer_urls=problem == "external",
    )
    if problem == "identity":
        expected["source_image_id"] = "sha256:" + "b" * 64
    elif problem == "name":
        expected["source_image"] = "deerflow.local/not-the-base:exact"
    elif problem in ("corrupt", "missing"):
        target = root / "blobs/sha256" / layer["digest"].split(":")[1]
        if problem == "corrupt":
            target.write_bytes(b"opaque compressed layer fixturE")
        else:
            target.unlink()
    elif problem in ("size", "duplicate", "escape"):
        index = json.loads((root / "index.json").read_bytes())
        if problem == "size":
            index["manifests"][0]["size"] += 1
        elif problem == "duplicate":
            index["manifests"].append(copy.deepcopy(index["manifests"][0]))
        else:
            index["manifests"][0]["digest"] = "sha256:../../escape"
        (root / "index.json").write_text(json.dumps(index))
    with pytest.raises((ValueError, OSError)):
        verifier.verify(root, expected)


def test_linked_blob_is_not_trusted(verifier, tmp_path):
    root, expected, _, _, _, layer = layout(tmp_path)
    target = root / "blobs/sha256" / layer["digest"].split(":")[1]
    outside = tmp_path / "outside"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("Linux covers symlinks")
    with pytest.raises(ValueError, match="[Ll]ink|regular"):
        verifier.verify(root, expected)


@pytest.mark.parametrize("count", [128, 129, 345, 355, 1024])
def test_deep_audited_images_verify_every_layer(verifier, tmp_path, count):
    root, expected, _, _, _, _ = layout(tmp_path, layer_count=count)
    result = verifier.verify(root, expected)
    assert result["layers_verified"] == count
    assert result["ready_for_rollout"] is False


@pytest.mark.parametrize("problem", ["missing", "corrupt", "identity"])
def test_345_layers_do_not_relax_content_or_identity_checks(
    verifier, tmp_path, problem
):
    root, expected, _, manifest, _, _ = layout(tmp_path, layer_count=345)
    graph = json.loads(
        (root / "blobs/sha256" / manifest["digest"].split(":")[1]).read_bytes()
    )
    last = root / "blobs/sha256" / graph["layers"][-1]["digest"].split(":")[1]
    if problem == "missing":
        last.unlink()
    elif problem == "corrupt":
        before = last.read_bytes()
        last.write_bytes(before[:-1] + b"X")
    else:
        expected["source_image_id"] = "sha256:" + "f" * 64
    with pytest.raises((ValueError, OSError)):
        verifier.verify(root, expected)


@pytest.mark.parametrize(
    "layers,rootfs,detail",
    [
        (None, {}, "layers_not_array"),
        ([], {}, "layer_count_out_of_range count=0 limit=1024"),
        ([{}] * 1025, {}, "layer_count_out_of_range count=1025 limit=1024"),
        ([{}], None, "rootfs_not_object"),
        ([{}], {"type": "invalid"}, "rootfs_type_not_layers"),
        ([{}], {"type": "layers", "diff_ids": None}, "diff_ids_not_array"),
        (
            [{}],
            {"type": "layers", "diff_ids": []},
            "layer_diff_id_count_mismatch layers=1 diff_ids=0",
        ),
        ([{}], {"type": "layers", "diff_ids": [None]}, "invalid_diff_id index=0"),
        (
            [{}],
            {"type": "layers", "diff_ids": ["secret-marker"]},
            "invalid_diff_id index=0",
        ),
    ],
)
def test_layer_graph_failures_have_precise_non_secret_diagnostics(
    verifier, layers, rootfs, detail
):
    with pytest.raises(ValueError) as caught:
        verifier.validate_layer_graph({"layers": layers}, {"rootfs": rootfs})
    assert str(caught.value) == "Invalid image layer graph: " + detail
    assert "secret-marker" not in str(caught.value)


def test_deep_layer_support_does_not_relax_index_fanout(verifier):
    with pytest.raises(ValueError, match="Invalid OCI index"):
        verifier.manifests({"schemaVersion": 2, "manifests": [{}] * 129})

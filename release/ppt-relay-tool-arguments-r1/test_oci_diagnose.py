"""Metadata-only audit must not disclose config or read image layers."""

import ast
import copy
import hashlib
import importlib.util
import json
import tarfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.fixture
def tools(tmp_path):
    path = (
        Path.home()
        / "Downloads/deerflow-ppt-relay-tool-arguments-20260924-r1-build1.tar.gz"
    )
    with tarfile.open(path) as bundle:
        member = next(m for m in bundle if m.name.endswith("/verify_oci_base.py"))
        raw = bundle.extractfile(member).read()
    namespace = {}
    exec(compile(raw, "frozen-build1-verifier", "exec"), namespace)
    from types import SimpleNamespace

    verifier = SimpleNamespace(**namespace)
    spec = importlib.util.spec_from_file_location(
        "layer_diagnostic", HERE / "diagnose-oci-layers.py"
    )
    diagnostic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(diagnostic)
    return verifier, diagnostic


def layout(tmp_path, count, *, diff_count=None, rootfs_type="layers", bad_diff=False):
    root = tmp_path / "layout"
    blobs = root / "blobs/sha256"
    blobs.mkdir(parents=True)

    def blob(obj, media):
        raw = json.dumps(obj).encode()
        digest = hashlib.sha256(raw).hexdigest()
        (blobs / digest).write_bytes(raw)
        return {"digest": "sha256:" + digest, "size": len(raw), "mediaType": media}

    diffs = ["sha256:" + "a" * 64] * (count if diff_count is None else diff_count)
    if bad_diff:
        diffs[-1] = "DO-NOT-PRINT-secret"
    config = blob(
        {
            "os": "linux",
            "architecture": "arm64",
            "config": {"Env": ["TOKEN=DO-NOT-PRINT-secret"]},
            "rootfs": {"type": rootfs_type, "diff_ids": diffs},
        },
        "application/vnd.oci.image.config.v1+json",
    )
    # NO layer blobs exist. Any layer access would fail.
    manifest = blob(
        {
            "schemaVersion": 2,
            "config": config,
            "layers": [
                {
                    "digest": "sha256:" + "b" * 64,
                    "size": 1000000000,
                    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
                }
            ]
            * count,
        },
        "application/vnd.oci.image.manifest.v1+json",
    )
    selected = copy.deepcopy(manifest)
    selected["annotations"] = {"io.containerd.image.name": "deerflow.local/a:base"}
    (root / "index.json").write_text(
        json.dumps({"schemaVersion": 2, "manifests": [selected]})
    )
    (root / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
    return root, {
        "source_image": "deerflow.local/a:base",
        "source_image_id": config["digest"],
    }


@pytest.mark.parametrize("count", [1, 128, 129, 345])
def test_reports_exact_count_without_layers_or_mutation(tools, tmp_path, count):
    v, d = tools
    root, expected = layout(tmp_path, count)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    report = d.inspect(v, root, expected)
    assert report["layer_count"] == report["diff_id_count"] == count
    assert report["exceeds_build1_layer_limit"] == (count > 128)
    assert report["runtime_identity_match"] and report["metadata_hashes_verified"]
    assert not report["full_image_verified"] and not report["layers_read_or_extracted"]
    assert "DO-NOT-PRINT-secret" not in json.dumps(report)
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize(
    "kwargs,field",
    [
        ({"diff_count": 2}, "counts_match"),
        ({"rootfs_type": "other"}, "rootfs_type_is_layers"),
        ({"bad_diff": True}, "invalid_diff_id_count"),
    ],
)
def test_distinguishes_other_layer_graph_failures(tools, tmp_path, kwargs, field):
    v, d = tools
    root, expected = layout(tmp_path, 3, **kwargs)
    report = d.inspect(v, root, expected)
    assert report[field] == (1 if field == "invalid_diff_id_count" else False)
    assert "DO-NOT-PRINT-secret" not in json.dumps(report)


def test_host_diagnostic_python37_syntax():
    ast.parse((HERE / "diagnose-oci-layers.py").read_text(), feature_version=(3, 7))

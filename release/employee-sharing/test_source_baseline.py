import base64
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def baseline():
    spec = importlib.util.spec_from_file_location("employee_source_baseline", Path(__file__).with_name("source_baseline.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def report():
    raw = b"# audited production source\n"
    item = {"sha256": hashlib.sha256(raw).hexdigest(), "base64": base64.b64encode(raw).decode()}
    return {"audit": "employee-sharing-source-baseline-v1", "production_mutation": "NONE", "secrets_collected": False, "errors": [], "components": {
        name: {"deployment": name, "image": "local/" + name + ":audited", "image_id": "sha256:" + "a" * 64, "node": "datax-01", "files": {"app.py": item, "missing.py": {"missing": True}}}
        for name in ("gateway", "frontend", "provisioner")
    }}


def test_exact_source_lock_and_new_staging_directory(baseline, tmp_path):
    source = tmp_path / "audit.json"
    source.write_text(json.dumps(report()))
    loaded = baseline.load(source)
    destination = tmp_path / "staged"
    baseline.materialize(loaded, destination)
    assert (destination / "gateway/app.py").read_bytes() == b"# audited production source\n"
    assert not (destination / "gateway/missing.py").exists()
    lock = loaded["lock"]
    assert lock["files_verified"] == 3
    assert lock["report_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert "base64" not in json.dumps(lock)
    with pytest.raises(ValueError, match="exist"):
        baseline.materialize(loaded, destination)


@pytest.mark.parametrize("failure", ["digest", "traversal", "absolute", "backslash", "drive", "missing-component", "duplicate-key", "reported-error"])
def test_invalid_audit_cannot_enter_package_source(baseline, tmp_path, failure):
    document = report()
    files = document["components"]["gateway"]["files"]
    if failure == "digest":
        files["app.py"]["sha256"] = "0" * 64
    elif failure in ("traversal", "absolute", "backslash", "drive"):
        name = {"traversal": "../outside", "absolute": "/outside", "backslash": "dir\\outside", "drive": "C:outside"}[failure]
        files[name] = files.pop("app.py")
    elif failure == "missing-component":
        del document["components"]["provisioner"]
    elif failure == "reported-error":
        document["errors"] = [{"type": "failure"}]
    raw = json.dumps(document)
    if failure == "duplicate-key":
        raw = raw.replace('"errors": []', '"errors": ["bad"], "errors": []')
    source = tmp_path / "audit.json"
    source.write_text(raw)
    with pytest.raises(ValueError):
        baseline.load(source)


def test_legacy_node_version_collision_is_not_reported_as_kubernetes_node(baseline, tmp_path):
    document = report()
    document["components"]["frontend"]["node"] = "v22.23.1"
    path = tmp_path / "audit.json"
    path.write_text(json.dumps(document))
    component = baseline.load(path)["lock"]["components"]["frontend"]
    assert component["node"] is None
    assert component["runtime"] == {"node": "v22.23.1"}
    assert component["metadata_warnings"] == ["legacy_collector_node_collision"]


def test_supplement_merge_keeps_digest_provenance_and_missing_attestations(baseline, tmp_path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps(report()))
    document = report()
    document["audit"] = "employee-sharing-source-supplement-v1"
    for component in document["components"].values():
        component["files"]["new.py"] = component["files"].pop("missing.py")
    second.write_text(json.dumps(document))
    merged = baseline.merge([baseline.load(first), baseline.load(second)])
    assert merged["lock"]["report_sha256s"] == [hashlib.sha256(path.read_bytes()).hexdigest() for path in (first, second)]
    assert merged["lock"]["components"]["gateway"]["missing"] == ["missing.py", "new.py"]
    assert merged["lock"]["files_verified"] == 3
    baseline.materialize(merged, tmp_path / "merged")


@pytest.mark.parametrize("change", ["tag", "digest", "source", "missing-now-present", "present-now-missing", "runtime"])
def test_supplement_merge_rejects_drift(baseline, tmp_path, change):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    original = report()
    original["components"]["gateway"]["runtime"] = {"python": "3.12.13"}
    first.write_text(json.dumps(original))
    document = report()
    component = document["components"]["gateway"]
    if change == "tag":
        component["image"] += "-changed"
    elif change == "digest":
        component["image_id"] = "sha256:" + "b" * 64
    elif change == "source":
        raw = b"# changed"
        component["files"]["app.py"] = {"sha256": hashlib.sha256(raw).hexdigest(), "base64": base64.b64encode(raw).decode()}
    elif change == "missing-now-present":
        component["files"]["missing.py"] = component["files"]["app.py"]
    elif change == "present-now-missing":
        component["files"]["app.py"] = {"missing": True}
        component["files"]["anchor.py"] = report()["components"]["gateway"]["files"]["app.py"]
    else:
        component["runtime"] = {"python": "3.11.0"}
    second.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="changed|differs"):
        baseline.merge([baseline.load(first), baseline.load(second)])

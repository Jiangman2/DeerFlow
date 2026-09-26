"""Image import gates must execute real reviewed modules, without replacements."""

import base64
import hashlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).parent


@pytest.fixture
def probe(monkeypatch):
    monkeypatch.syspath_prepend(str(HERE))
    monkeypatch.delitem(sys.modules, "app", raising=False)
    spec = importlib.util.spec_from_file_location(
        "employee_image_smoke", HERE / "smoke_image_imports.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    # The successful probe actually imports our disposable fixture, not production.
    sys.modules.pop("app", None)


def image(tmp_path, monkeypatch, content=b"VALUE = 2\n"):
    root = tmp_path / "image"
    root.mkdir()
    (root / "app.py").write_bytes(content)
    monkeypatch.syspath_prepend(str(root))
    return root, {
        "schema": 1,
        "scope": "employee-sharing-image-candidate",
        "ready_for_rollout": False,
        "component": "provisioner",
        "source_image_id": "sha256:" + "a" * 64,
        "anchors": {},
        "entries": [
            {
                "path": "app.py",
                "source_sha256": hashlib.sha256(b"VALUE = 1\n").hexdigest(),
                "target_sha256": hashlib.sha256(content).hexdigest(),
                "content_base64": base64.b64encode(content).decode(),
            }
        ],
    }


def test_import_probe_loads_reviewed_file_without_claiming_business_acceptance(
    probe, tmp_path, monkeypatch
):
    root, manifest = image(tmp_path, monkeypatch)
    receipt = probe.verify(root, manifest)
    assert sys.modules["app"].VALUE == 2
    assert receipt["modules"] == ["app"]
    assert receipt["business_acceptance"] == "NOT_RUN"
    assert receipt["ready_for_rollout"] is False


def test_unapplied_sources_are_not_probed(probe, tmp_path, monkeypatch):
    root, manifest = image(tmp_path, monkeypatch)
    (root / "app.py").write_bytes(b"VALUE = 1\n")
    with pytest.raises(ValueError, match="not applied"):
        probe.verify(root, manifest)
    assert "app" not in sys.modules


def test_missing_runtime_dependency_is_not_mocked_or_installed(
    probe, tmp_path, monkeypatch
):
    root, manifest = image(
        tmp_path, monkeypatch, b"import deliberately_missing_employee_dependency\n"
    )
    with pytest.raises(
        ModuleNotFoundError, match="deliberately_missing_employee_dependency"
    ):
        probe.verify(root, manifest)


def test_cached_outside_module_cannot_satisfy_image_gate(probe, tmp_path, monkeypatch):
    root, manifest = image(tmp_path, monkeypatch)
    outside = tmp_path / "unreviewed.py"
    outside.write_bytes(b"VALUE = 2\n")
    fake = types.ModuleType("app")
    fake.__file__ = str(outside)
    monkeypatch.setitem(sys.modules, "app", fake)
    with pytest.raises(ValueError, match="outside reviewed"):
        probe.verify(root, manifest)

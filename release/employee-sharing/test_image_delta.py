"""The image-only installers must reject drift before changing any source."""

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


def sha(content):
    return hashlib.sha256(content).hexdigest()


def entry(path, before, after):
    return {
        "path": path,
        "source_sha256": None if before is None else sha(before),
        "target_sha256": sha(after),
        "content_base64": base64.b64encode(after).decode("ascii"),
    }


@pytest.fixture(params=["python", "node"])
def installer(request, tmp_path):
    component = "gateway" if request.param == "python" else "frontend"
    names = (
        ("app/one.py", "app/added.py", "app/stable.py")
        if component == "gateway"
        else ("src/one.ts", "src/new/added.ts", "src/stable.ts")
    )
    before = b"VALUE = 1\n" if component == "gateway" else b"export const value = 1;\n"
    after = before.replace(b"1", b"2")
    root = tmp_path / "image"
    root.mkdir()
    for relative in (names[0], names[2]):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(before)
    manifest = {
        "schema": 1,
        "scope": "employee-sharing-image-candidate",
        "ready_for_rollout": False,
        "component": component,
        "source_image_id": "sha256:" + "a" * 64,
        "anchors": {names[2]: sha(before)},
        "entries": [entry(names[0], before, after), entry(names[1], None, after)],
    }

    def invoke(document=None, check=False, raw=None):
        source = tmp_path / "delta.json"
        source.write_bytes(
            raw
            if raw is not None
            else (
                json.dumps(
                    manifest if document is None else document,
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n"
            ).encode("utf-8")
        )
        executable = (
            [sys.executable, str(HERE / "apply_image_delta.py")]
            if request.param == "python"
            else ["node", str(HERE / "apply_image_delta.mjs")]
        )
        result = subprocess.run(
            [
                *executable,
                str(root),
                str(source),
                "--check-only" if check else "--image-build-stage",
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
        if result.returncode:
            raise ValueError(result.stderr)
        return json.loads(result.stdout)

    return root, names, before, after, manifest, invoke


def test_image_delta_check_then_apply_is_exact_and_idempotent(installer):
    root, names, before, after, _, invoke = installer
    result = invoke(check=True)
    assert result["mode"] == "check-only"
    assert (root / names[0]).read_bytes() == before
    assert not (root / names[1]).exists()
    assert invoke()["files"] == 2
    assert (root / names[0]).read_bytes() == (root / names[1]).read_bytes() == after
    assert (root / names[2]).read_bytes() == before
    assert invoke()["files"] == 2


@pytest.mark.parametrize(
    "kind",
    [
        "source",
        "anchor",
        "target",
        "collision",
        "duplicate",
        "case",
        "escape",
        "forward",
        "ready",
        "overscope",
    ],
)
def test_image_delta_rejects_incomplete_or_unsafe_bundle_before_any_write(
    installer, kind
):
    root, names, before, _, manifest, invoke = installer
    document = copy.deepcopy(manifest)
    if kind == "source":
        document["entries"][0]["source_sha256"] = "b" * 64
    elif kind == "anchor":
        document["anchors"][names[2]] = "b" * 64
    elif kind == "target":
        document["entries"][1]["target_sha256"] = "b" * 64
    elif kind == "collision":
        path = root / names[1]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"DO NOT OVERWRITE")
    elif kind == "duplicate":
        document["entries"].append(document["entries"][0])
    elif kind == "case":
        document["entries"][1]["path"] = names[0].upper()
    elif kind == "escape":
        document["entries"][1]["path"] = "../outside.py"
    elif kind == "forward":
        document["schema"] = 2
    elif kind == "ready":
        document["ready_for_rollout"] = True
    else:
        document["entries"][1]["path"] = "package.json"
    with pytest.raises(ValueError):
        invoke(document)
    assert (root / names[0]).read_bytes() == before
    assert (root / names[2]).read_bytes() == before
    assert not list(root.rglob("*.employee-tmp-*"))


def test_image_delta_refuses_linked_parent(installer, tmp_path):
    root, names, before, after, manifest, invoke = installer
    outside = tmp_path / "outside"
    outside.mkdir()
    directory = root / "linked"
    try:
        directory.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation needs host permission; covered in Linux")
    prefix = "app" if manifest["component"] == "gateway" else "src"
    # Link inside the otherwise allowed component root.
    link = root / prefix / "linked"
    link.symlink_to(outside, target_is_directory=True)
    manifest["entries"][1] = entry(
        prefix + "/linked/added" + Path(names[1]).suffix, None, after
    )
    with pytest.raises(ValueError):
        invoke()
    assert (root / names[0]).read_bytes() == before
    assert not list(outside.iterdir())


def test_image_delta_refuses_duplicate_json_keys(installer):
    root, names, before, _, manifest, invoke = installer
    source = (json.dumps(manifest, indent=2) + "\n").replace(
        '"schema": 1,', '"schema": 0,\n  "schema": 1,'
    )
    with pytest.raises(ValueError, match="[Dd]uplicate"):
        invoke(raw=source.encode())
    assert (root / names[0]).read_bytes() == before
    assert not (root / names[1]).exists()


def test_python_installer_preserves_executable_mode(tmp_path):
    if sys.platform == "win32":
        pytest.skip("POSIX mode assertion runs in Linux")
    spec = importlib.util.spec_from_file_location(
        "image_delta_mode_test", HERE / "apply_image_delta.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "image"
    root.mkdir()
    file = root / "app.py"
    file.write_bytes(b"VALUE = 1\n")
    file.chmod(0o751)
    manifest = {
        "schema": 1,
        "scope": "employee-sharing-image-candidate",
        "ready_for_rollout": False,
        "component": "provisioner",
        "source_image_id": "sha256:" + "a" * 64,
        "anchors": {},
        "entries": [entry("app.py", file.read_bytes(), b"VALUE = 2\n")],
    }
    module.apply(root, manifest, check_only=False)
    assert file.stat().st_mode & 0o777 == 0o751

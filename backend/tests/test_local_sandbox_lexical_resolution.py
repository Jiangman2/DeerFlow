"""Official #5112 path resolution without retaining interned thread components."""

import os

import pytest

from deerflow.sandbox.local import local_sandbox as module
from deerflow.sandbox.local.local_sandbox import LocalSandbox, PathMapping


def test_resolution_paths_do_not_construct_pathlib_components(monkeypatch, tmp_path):
    root = tmp_path / "private-thread-123"
    root.mkdir()
    sandbox = LocalSandbox("lexical", [PathMapping("/mnt/data", str(root), read_only=True)])

    def forbidden_path(*args, **kwargs):
        raise AssertionError("resolution interns high-cardinality path components")

    monkeypatch.setattr(module, "Path", forbidden_path)
    local = os.path.realpath(os.path.join(str(root), "nested", "file.txt"))
    assert sandbox._resolve_path("/mnt/data/nested/file.txt") == local
    assert sandbox._is_read_only_path(local)
    assert sandbox._reverse_resolve_path(local) == "/mnt/data/nested/file.txt"


@pytest.mark.parametrize("relative", ["../outside/secret", "nested/../../outside/secret"])
def test_commonpath_containment_does_not_accept_prefix_siblings(tmp_path, relative):
    root = tmp_path / "mount"
    root.mkdir()
    sandbox = LocalSandbox("containment", [PathMapping("/mnt/data", str(root))])
    with pytest.raises(PermissionError):
        sandbox._resolve_path("/mnt/data/" + relative)

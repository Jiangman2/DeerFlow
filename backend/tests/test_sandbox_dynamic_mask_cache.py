"""Per-user/thread roots must not enter the process-global compiled regex cache."""

import re
from types import SimpleNamespace

import pytest

from deerflow.sandbox import tools


@pytest.mark.parametrize("source", ["thread", "custom", "acp"])
def test_dynamic_roots_do_not_enter_compiled_pattern_cache(monkeypatch, tmp_path, source):
    marker = "unique_mask_scope_22f16"
    root = str(tmp_path / marker)
    tools._compiled_mask_patterns.cache_clear()
    monkeypatch.setattr(tools, "_get_skills_host_path", lambda: None)
    monkeypatch.setattr(tools, "_get_skills_container_path", lambda: "/mnt/skills")
    monkeypatch.setattr(tools, "_get_acp_workspace_host_path", lambda *args: root if source == "acp" else None)

    class CustomDir:
        def exists(self):
            return source == "custom"

        def __str__(self):
            return root

    monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: SimpleNamespace(user_custom_skills_dir=lambda user: CustomDir()))
    thread = {"workspace_path": root + "/workspace", "uploads_path": root + "/uploads", "outputs_path": root + "/outputs"} if source == "thread" else None
    virtual = {"thread": "/mnt/user-data", "custom": "/mnt/skills/custom", "acp": "/mnt/acp-workspace"}[source]
    try:
        assert tools.mask_local_paths_in_output(root + "/nested/report.txt", thread) == virtual + "/nested/report.txt"
        assert tools._compiled_mask_patterns.cache_info().currsize == 0
        assert not any(marker in str(key) for key in re._cache)
    finally:
        tools._compiled_mask_patterns.cache_clear()


def test_resolved_root_cache_is_bounded_without_path_component_interning(monkeypatch):
    roots = tools._mask_source_roots
    roots.cache_clear()
    monkeypatch.setattr(tools.os.path, "realpath", lambda path: path)
    for index in range(300):
        roots(f"/temporary/thread-{index}/user-data")
    assert roots.cache_info().maxsize == 256
    assert roots.cache_info().currsize == 256
    roots.cache_clear()


@pytest.mark.parametrize("root", ["/host/thread/user-data", r"C:\host\thread\user-data"])
def test_lexical_parent_preserves_host_style(root):
    separator = "\\" if "\\" in root else "/"
    thread = {name + "_path": root + separator + name for name in ("workspace", "uploads", "outputs")}
    assert tools._thread_virtual_to_actual_mappings(thread)["/mnt/user-data"] == root

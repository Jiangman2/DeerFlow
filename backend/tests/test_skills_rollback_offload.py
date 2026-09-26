"""Custom Skill reads and rollback validation must not block Gateway's loop."""

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.gateway.routers import skills as router
from deerflow.skills.types import SkillCategory


@pytest.mark.asyncio
async def test_custom_skill_response_reads_storage_on_worker_thread(monkeypatch):
    loop_thread = threading.get_ident()
    observed = []
    skill = SimpleNamespace(name="demo", category=SkillCategory.CUSTOM)

    class _Storage:
        def load_skills(self, **_kwargs):
            observed.append(threading.get_ident())
            return [skill]

        def read_custom_skill(self, _name):
            observed.append(threading.get_ident())
            return "# demo"

    monkeypatch.setattr(router, "_get_user_skill_storage", lambda _config: _Storage())
    monkeypatch.setattr(
        router,
        "_skill_to_response",
        lambda _skill: SimpleNamespace(
            model_dump=lambda: {
                "name": "demo",
                "description": "Demo",
                "license": None,
                "category": SkillCategory.CUSTOM,
                "enabled": True,
                "editable": True,
            }
        ),
    )
    result = await router._read_custom_skill_response("demo", None)
    assert result.content == "# demo"
    assert len(observed) == 2
    assert all(thread != loop_thread for thread in observed)


@pytest.mark.asyncio
async def test_rollback_validation_and_previous_file_read_are_offloaded(monkeypatch, tmp_path):
    loop_thread = threading.get_ident()
    observed = []
    target = tmp_path / "SKILL.md"
    target.write_text("old", encoding="utf-8")

    class _Storage:
        def custom_skill_exists(self, _name):
            return True

        def read_history(self, _name):
            return [{"prev_content": "new", "ts": "now"}]

        def validate_skill_markdown_content(self, _name, _content):
            observed.append(("validate", threading.get_ident()))

        def get_custom_skill_file(self, _name):
            return target

        def write_custom_skill(self, *_args):
            return None

        def append_history(self, *_args):
            return None

    async def _admin(*_args, **_kwargs):
        return None

    async def _scan(*_args, **_kwargs):
        return []

    async def _security(*_args, **_kwargs):
        return SimpleNamespace(decision="allow", reason="okay")

    async def _refresh(*_args, **_kwargs):
        return None

    async def _response(*_args, **_kwargs):
        return "ok"

    original_read_text = Path.read_text

    def read_text(path, *args, **kwargs):
        if path == target:
            observed.append(("read", threading.get_ident()))
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(router, "_get_user_skill_storage", lambda _config: _Storage())
    monkeypatch.setattr(router, "require_admin_user", _admin)
    monkeypatch.setattr(router, "_scan_static_skill_markdown_or_raise", _scan)
    monkeypatch.setattr(router, "scan_skill_content", _security)
    monkeypatch.setattr(router, "refresh_user_skills_system_prompt_cache_async", _refresh)
    monkeypatch.setattr(router, "get_effective_user_id", lambda: "test-user")
    monkeypatch.setattr(router, "_read_custom_skill_response", _response)
    result = await router.rollback_custom_skill("demo", router.SkillRollbackRequest(history_index=0), None, None)
    assert result == "ok"
    assert {name for name, _ in observed} == {"validate", "read"}
    assert all(thread != loop_thread for _, thread in observed)

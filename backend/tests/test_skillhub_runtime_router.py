from __future__ import annotations

import json
import shlex
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from _router_auth_helpers import make_authed_test_app
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.deps import get_config, get_current_user_from_request
from app.gateway.routers import skills as skills_router
from deerflow.agents.lead_agent.prompt import get_skills_prompt_section, invalidate_user_skill_cache
from deerflow.config.extensions_config import reset_extensions_config
from deerflow.config.paths import Paths
from deerflow.sandbox.local.local_sandbox import LocalSandbox, PathMapping
from deerflow.skills.storage import reset_user_skill_storage
from deerflow.skills.storage.user_scoped_skill_storage import UserScopedSkillStorage

_USER_ID = "31a3c831-885a-4c7a-9971-cb88d167ef52"
_OTHER_USER_ID = "090223ce-4d68-4f47-9b5e-0c6efe9bfc41"


def _make_user(user_id: str = _USER_ID) -> User:
    return User(
        email=f"skillhub-{user_id}@example.com",
        password_hash="x",
        system_role="user",
        id=UUID(user_id),
    )


def _skill_zip(name: str, *, marker: str = "v1") -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "SKILL.md",
            f"---\nname: {name}\ndescription: Registry skill {marker}\n---\n\n# {name}\n",
        )
        archive.writestr("references/version.txt", marker)
    return buffer.getvalue()


def _executable_skill_zip(name: str) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "SKILL.md",
            f"---\nname: {name}\ndescription: Runtime-readable registry skill\n---\n\n# {name}\n",
        )
        archive.writestr("references/message.txt", "skillhub-runtime-ok")
        archive.writestr(
            "scripts/check.py",
            "from pathlib import Path\nprint((Path(__file__).parents[1] / 'references' / 'message.txt').read_text(encoding='utf-8'))\n",
        )
    return buffer.getvalue()


def _make_app(
    monkeypatch,
    tmp_path: Path,
    *,
    user_id: str = _USER_ID,
) -> tuple[FastAPI, UserScopedSkillStorage]:
    monkeypatch.setenv("DEER_FLOW_SKILLHUB_ENABLED", "true")
    extensions_path = tmp_path / "extensions_config.json"
    extensions_path.write_text('{"mcpServers": {}, "skills": {}}\n', encoding="utf-8")
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(extensions_path))
    reset_extensions_config()
    skills_root = tmp_path / "skills"
    paths = Paths(base_dir=tmp_path)
    config = SimpleNamespace(
        skills=SimpleNamespace(
            get_skills_path=lambda: skills_root,
            container_path="/mnt/skills",
            use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage",
        ),
        skill_evolution=SimpleNamespace(enabled=True, moderation_model_name=None),
    )
    monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: paths)
    monkeypatch.setattr("deerflow.config.paths._paths", None)
    storage = UserScopedSkillStorage(user_id, host_path=str(skills_root), app_config=config)
    monkeypatch.setattr(skills_router, "_get_user_skill_storage_for_user", lambda cfg, user_id: storage)

    async def _scan(*args, **kwargs):
        from deerflow.skills.security_scanner import ScanResult

        return ScanResult(decision="allow", reason="ok")

    monkeypatch.setattr("deerflow.skills.installer.scan_skill_content", _scan)
    monkeypatch.setattr(skills_router, "refresh_user_skills_system_prompt_cache_async", lambda user_id: _async_none())

    app = make_authed_test_app(user_factory=lambda: _make_user(user_id))
    app.state.config = config
    app.dependency_overrides[get_config] = lambda: config
    app.dependency_overrides[get_current_user_from_request] = lambda: _make_user(user_id)
    app.include_router(skills_router.router)
    return app, storage


async def _async_none() -> None:
    return None


def test_registry_routes_fail_closed_when_skillhub_is_disabled(monkeypatch, tmp_path):
    app, _storage = _make_app(monkeypatch, tmp_path)
    monkeypatch.delenv("DEER_FLOW_SKILLHUB_ENABLED")

    with TestClient(app) as client:
        response = client.get("/api/skillhub/runtime/skills")

    assert response.status_code == 503
    assert response.json()["detail"] == "SkillHub integration is disabled"


def _install(client: TestClient, *, namespace: str = "global", slug: str = "weather", version: str = "1.0.0", marker: str = "v1"):
    return client.post(
        "/api/skillhub/runtime/skills",
        data={"namespace": namespace, "slug": slug, "version": version},
        files={"file": (f"{slug}-{version}.zip", _skill_zip(slug, marker=marker), "application/zip")},
    )


def test_normal_user_can_install_list_toggle_and_uninstall_registry_skill(monkeypatch, tmp_path):
    app, storage = _make_app(monkeypatch, tmp_path)

    with TestClient(app) as client:
        installed = _install(client)
        assert installed.status_code == 200
        assert installed.json()["action"] == "installed"
        assert installed.json()["skill_name"] == "weather"
        assert installed.json()["enabled"] is True

        listing = client.get("/api/skillhub/runtime/skills")
        assert listing.status_code == 200
        assert listing.json()["skills"] == [
            {
                "namespace": "global",
                "slug": "weather",
                "version": "1.0.0",
                "skill_name": "weather",
                "enabled": True,
                "installed": True,
                "sha256": installed.json()["sha256"],
                "installed_at": installed.json()["installed_at"],
                "updated_at": installed.json()["updated_at"],
            }
        ]

        disabled = client.patch(
            "/api/skillhub/runtime/skills/global/weather",
            json={"enabled": False},
        )
        assert disabled.status_code == 200
        assert disabled.json()["enabled"] is False
        assert storage.get_skill_enabled_state("weather") is False

        removed = client.delete("/api/skillhub/runtime/skills/global/weather")
        assert removed.status_code == 200
        assert removed.json() == {"success": True}

    assert not storage.get_custom_skill_dir("weather").exists()
    assert json.loads((storage.get_user_skills_root() / "_registry_installations.json").read_text(encoding="utf-8"))["installations"] == {}


def test_registry_upgrade_replaces_complete_tree_and_preserves_disabled_state(monkeypatch, tmp_path):
    app, storage = _make_app(monkeypatch, tmp_path)

    with TestClient(app) as client:
        assert _install(client).status_code == 200
        assert client.patch("/api/skillhub/runtime/skills/global/weather", json={"enabled": False}).status_code == 200

        upgraded = _install(client, version="1.1.0", marker="v2")

    assert upgraded.status_code == 200
    assert upgraded.json()["action"] == "upgraded"
    assert upgraded.json()["version"] == "1.1.0"
    assert upgraded.json()["enabled"] is False
    assert (storage.get_custom_skill_dir("weather") / "references" / "version.txt").read_text(encoding="utf-8") == "v2"
    assert "Registry skill v2" in storage.read_custom_skill("weather")


def test_registry_install_preserves_complete_tree_and_runs_from_sandbox_path(monkeypatch, tmp_path):
    """A successful install must include scripts/resources at the path agents use."""
    app, storage = _make_app(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/skillhub/runtime/skills",
            data={"namespace": "global", "slug": "runtime-check", "version": "1.0.0"},
            files={
                "file": (
                    "runtime-check-1.0.0.zip",
                    _executable_skill_zip("runtime-check"),
                    "application/zip",
                )
            },
        )

    assert response.status_code == 200
    installed_root = storage.get_custom_skill_dir("runtime-check")
    installed_files = sorted(path.relative_to(installed_root).as_posix() for path in installed_root.rglob("*") if path.is_file())
    assert installed_files == [
        "SKILL.md",
        "references/message.txt",
        "scripts/check.py",
    ]

    discovered = {skill.name: skill for skill in storage.load_skills(enabled_only=True)}
    assert "runtime-check" in discovered
    assert discovered["runtime-check"].get_container_file_path("/mnt/skills") == (
        "/mnt/skills/custom/runtime-check/SKILL.md"
    )

    sandbox = LocalSandbox(
        "skillhub-runtime-check",
        [
            PathMapping(
                container_path="/mnt/skills/custom",
                local_path=str(installed_root.parent),
                read_only=True,
            )
        ],
    )
    # Use pytest's Python 3 interpreter, not a host PATH alias to Python 2.
    output = sandbox.execute_command(f"{shlex.quote(sys.executable)} /mnt/skills/custom/runtime-check/scripts/check.py")
    assert output.strip() == "skillhub-runtime-ok"


def test_same_registry_version_is_idempotent(monkeypatch, tmp_path):
    app, storage = _make_app(monkeypatch, tmp_path)

    with TestClient(app) as client:
        first = _install(client)
        second = _install(client)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["action"] == "already_current"
    assert "Registry skill v1" in storage.read_custom_skill("weather")


def test_registry_install_refuses_to_overwrite_unmanaged_custom_skill(monkeypatch, tmp_path):
    app, storage = _make_app(monkeypatch, tmp_path)
    storage.write_custom_skill(
        "weather",
        "SKILL.md",
        "---\nname: weather\ndescription: User authored\n---\n\n# Keep me\n",
    )

    with TestClient(app) as client:
        response = _install(client)

    assert response.status_code == 409
    assert "not managed by SkillHub" in response.json()["detail"]
    assert "# Keep me" in storage.read_custom_skill("weather")


def test_registry_install_requires_archive_skill_name_to_match_slug(monkeypatch, tmp_path):
    app, storage = _make_app(monkeypatch, tmp_path)

    with TestClient(app) as client:
        response = client.post(
            "/api/skillhub/runtime/skills",
            data={"namespace": "global", "slug": "weather", "version": "1.0.0"},
            files={"file": ("weather.zip", _skill_zip("different-skill"), "application/zip")},
        )

    assert response.status_code == 400
    assert "must match" in response.json()["detail"]
    assert not storage.get_custom_skill_dir("weather").exists()
    assert not storage.get_custom_skill_dir("different-skill").exists()


def test_registry_install_reports_scanner_outage_as_retryable_service_failure(monkeypatch, tmp_path):
    app, storage = _make_app(monkeypatch, tmp_path)

    async def _unavailable_scan(*args, **kwargs):
        from deerflow.skills.security_scanner import ScanResult

        return ScanResult(
            decision="block",
            reason="Security scan unavailable for skill content; manual review required.",
        )

    monkeypatch.setattr("deerflow.skills.installer.scan_skill_content", _unavailable_scan)

    with TestClient(app) as client:
        response = _install(client)

    assert response.status_code == 503
    assert "安全复核服务暂时不可用" in response.json()["detail"]
    assert not storage.get_custom_skill_dir("weather").exists()


def test_installed_skill_is_discoverable_in_next_user_prompt(monkeypatch, tmp_path):
    app, _storage = _make_app(monkeypatch, tmp_path)
    reset_user_skill_storage(_USER_ID)
    invalidate_user_skill_cache(_USER_ID)

    try:
        with TestClient(app) as client:
            assert _install(client).status_code == 200

        prompt = get_skills_prompt_section(
            app_config=app.state.config,
            user_id=_USER_ID,
        )

        assert "<name>weather</name>" in prompt
        assert "Registry skill v1" in prompt
    finally:
        invalidate_user_skill_cache(_USER_ID)
        reset_user_skill_storage(_USER_ID)


def test_registry_install_is_isolated_to_authenticated_user(monkeypatch, tmp_path):
    owner_app, owner_storage = _make_app(monkeypatch, tmp_path)
    with TestClient(owner_app) as client:
        assert _install(client).status_code == 200

    other_app, other_storage = _make_app(
        monkeypatch,
        tmp_path,
        user_id=_OTHER_USER_ID,
    )
    with TestClient(other_app) as client:
        listing = client.get("/api/skillhub/runtime/skills")

    assert listing.status_code == 200
    assert listing.json() == {"skills": []}
    assert owner_storage.get_custom_skill_dir("weather").is_dir()
    assert not other_storage.get_custom_skill_dir("weather").exists()

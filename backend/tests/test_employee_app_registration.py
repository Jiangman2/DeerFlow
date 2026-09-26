"""Employee API/broker registration uses the real app factory and lifecycle.

Database, channels and external startup integrations are inert local fixtures;
these tests do not claim deployment or real SSO/knowledge connectivity.
"""

import importlib
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from deerflow.config.app_config import AppConfig
from deerflow.config.paths import Paths
from deerflow.config.sandbox_config import SandboxConfig


@pytest.fixture(autouse=True)
def isolated_app_construction(monkeypatch):
    # Importing the real app constructs its global FastAPI instance. Never let
    # that read the developer's Redis/provider configuration or depend on test
    # order and cached config from a previously executed module.
    from deerflow.config import app_config

    config = AppConfig(models=[], sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"), memory={"enabled": False})
    monkeypatch.setattr(app_config, "_app_config", config)
    monkeypatch.setattr(app_config, "_app_config_is_custom", True)


def test_app_factory_exposes_employee_review_and_version_routes():
    gateway = importlib.import_module("app.gateway.app")
    paths = gateway.create_app().openapi()["paths"]
    assert "post" in paths["/api/agents/{name}/publication-check"]
    assert "post" in paths["/api/employee-resource-grants/{grant_id}/decision"]
    assert "post" in paths["/api/agents/{name}/proposals/{proposal_id}/decision"]
    assert "post" in paths["/api/agents/{name}/publications/{revision}/restore-draft"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ppt_only", [False, True])
async def test_knowledge_broker_registered_in_gateway_not_ppt_runner(tmp_path, monkeypatch, ppt_only):
    gateway = importlib.import_module("app.gateway.app")
    from app.gateway.employee_knowledge import EmployeeKnowledgeBroker
    from deerflow.runtime import employee_resources

    config = AppConfig(models=[], sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"), memory={"enabled": False})
    paths = Paths(base_dir=tmp_path)
    monkeypatch.setattr(gateway, "get_app_config", lambda: config)
    monkeypatch.setattr(gateway, "get_gateway_config", lambda: SimpleNamespace(host="localhost", port=0))
    monkeypatch.setattr(gateway, "_is_ppt_master_runner_profile", lambda: ppt_only)
    for name in ("configure_logging", "ensure_browser_runtime_available", "warn_if_auth_disabled_enabled", "setup_monocle_tracing_if_enabled", "cleanup_stale_upload_staging_files"):
        monkeypatch.setattr(gateway, name, MagicMock())
    monkeypatch.setattr("app.gateway.oa_policy_flow_config.load_oa_policy_flow_config", lambda: SimpleNamespace(enabled=False))
    monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: paths)
    monkeypatch.setattr("deerflow.agents.memory.get_memory_manager", MagicMock())
    monkeypatch.setattr(gateway, "_ensure_admin_user", AsyncMock())
    monkeypatch.setattr(gateway.auth, "close_oidc_service", AsyncMock())
    monkeypatch.setattr(gateway, "close_conversation_document_parser_client", AsyncMock())
    monkeypatch.setattr("app.channels.service.start_channel_service", AsyncMock(return_value=SimpleNamespace(get_status=lambda: {})))
    monkeypatch.setattr("app.channels.service.stop_channel_service", AsyncMock())
    monkeypatch.setattr("deerflow.community.browser_automation.get_browser_session_manager", lambda: SimpleNamespace(close_all_sessions=AsyncMock(return_value=0)))
    # Also inert for the complete checkout; the selective production composer
    # does not add that unrelated startup migration.
    monkeypatch.setattr(gateway.agents, "migrate_digital_employee_categories", MagicMock(return_value=0))
    monkeypatch.setattr(employee_resources, "_search", None)
    monkeypatch.setattr(employee_resources, "_authorize", None)

    @asynccontextmanager
    async def runtime(app, startup):
        yield

    monkeypatch.setattr(gateway, "langgraph_runtime", runtime)
    async with gateway.lifespan(FastAPI()):
        if ppt_only:
            assert employee_resources._search is None
            assert employee_resources._authorize is None
        else:
            assert isinstance(employee_resources._search.__self__, EmployeeKnowledgeBroker)
            assert employee_resources._search.__self__ is employee_resources._authorize.__self__

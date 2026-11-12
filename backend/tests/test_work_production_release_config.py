import json
import os
from pathlib import Path

from app.integrations.work.cards import CardSettings
from app.integrations.work.config import WorkSettings
from app.integrations.work.file_read import WorkFileSettings
from app.integrations.work.file_search import WorkSearchSettings
from app.integrations.work.notifications import work_notifications_enabled


def test_combined_release_configuration_uses_existing_production_credentials(monkeypatch):
    for name in list(os.environ):
        if name.startswith("DEER_FLOW_WORK_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("DEER_FLOW_WORK_ENABLED", "true")
    monkeypatch.setenv("DEER_FLOW_WORK_PROFILE", "production")
    # Existing credentials are not part of the non-secret release overlay.
    monkeypatch.setenv("DEER_FLOW_WORK_STATE_KEY", "existing-secret-sentinel")
    manifest = Path(__file__).resolve().parents[2] / "deploy/enterprise/work-production-env-20261110.json"
    overlay = json.loads(manifest.read_text(encoding="utf-8"))
    assert len(overlay) == 9
    assert "DEER_FLOW_WORK_STATE_KEY" not in overlay
    for name, value in overlay.items():
        monkeypatch.setenv(name, value)
    settings = WorkSettings.from_env()
    assert settings.enabled and settings.profile == "production" and settings.persistent
    card = CardSettings.from_env(settings.profile, app_id="cli_synthetic")
    assert card is not None and card.transport == "websocket"
    assert CardSettings.from_env(settings.profile) is None
    assert WorkSearchSettings.from_env(settings.profile).scope == "drive:drive:readonly"
    files = WorkFileSettings.from_env(settings.profile)
    assert files.sheets_scope == "sheets:spreadsheet:readonly"
    assert files.bitable_scope == "bitable:app:readonly"
    assert files.drive_scope == "drive:file:download"
    assert work_notifications_enabled()
    assert os.environ["DEER_FLOW_WORK_STATE_KEY"] == "existing-secret-sentinel"

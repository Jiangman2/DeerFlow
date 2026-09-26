from types import SimpleNamespace

import pytest

from app.gateway.employee_publication import publication_config, prepare_resources
from deerflow.config.paths import Paths


def test_publication_does_not_copy_unknown_private_fields_or_github_identity():
    raw = {"name": "employee", "skills": [], "description": "purpose", "private_api_key": "secret", "github": {"installation_id": 123}, "future_private_setting": "retained-in-draft"}
    published = publication_config(raw)
    assert published["description"] == "purpose"
    assert "private_api_key" not in published and "github" not in published and "future_private_setting" not in published
    assert raw["future_private_setting"] == "retained-in-draft"


def test_preparation_requires_explicit_skill_list_and_never_changes_draft(tmp_path, monkeypatch):
    paths = Paths(base_dir=tmp_path)
    definition = SimpleNamespace(config={"name": "employee", "skills": None}, soul="keep", revision="draft")
    with pytest.raises(ValueError, match="explicit"):
        prepare_resources(paths, "employee", "alice", definition, staging=tmp_path / "stage")
    assert definition.soul == "keep" and definition.config["skills"] is None

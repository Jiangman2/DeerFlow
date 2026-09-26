"""The explicit creation-form selection survives Gateway context normalization."""

import pytest

from app.gateway.services import merge_run_context_overrides


@pytest.mark.parametrize("selection", [None, [], ["report"]])
def test_agent_builder_selection_is_forwarded_without_permission_promotion(selection):
    config = {}
    merge_run_context_overrides(config, {"is_bootstrap": True, "agent_skill_selection": selection})
    assert config["context"]["agent_skill_selection"] == selection
    assert config["configurable"]["agent_skill_selection"] == selection
    assert "user_id" not in config["context"]


def test_agent_source_is_forwarded_for_both_runtime_consumers():
    config = {}
    merge_run_context_overrides(config, {"agent_name": "reports", "agent_source": "team"})
    assert config["context"]["agent_source"] == "team"
    assert config["configurable"]["agent_source"] == "team"

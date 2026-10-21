import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest

_path = Path(__file__).resolve().parents[2] / "deploy/enterprise/data-qa-config-migration.py"
_spec = importlib.util.spec_from_file_location("data_qa_migration", _path)
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)


def config():
    return {
        "models": [{"name": "preserved"}],
        "tools": [
            {"name": "datalake_smart_qa", "group": "knowledge", "use": "deerflow.community.appforge.tools:datalake_smart_qa_tool", "project_id": "keep", "document_store_ids": ["keep"]},
            {"name": "datalake_metrics_qa", "group": "knowledge", "use": "deerflow.community.appforge.tools:datalake_metrics_qa_tool", "api_key": "$OLD_KEY", "inputs": {"hytoken": "old"}},
            {"name": "datalake_detail_qa", "group": "knowledge", "use": "deerflow.community.appforge.tools:datalake_detail_qa_tool"},
            {"name": "other", "group": "other", "use": "other:tool", "custom": [1, 2]},
        ],
        "sandbox": {"use": "preserved"},
    }


def test_migration_changes_only_retired_registrations_and_does_not_mutate_source():
    original = config()
    saved = deepcopy(original)
    result = module.migrate_configuration(original)
    assert original == saved
    assert result["models"] == original["models"]
    assert result["sandbox"] == original["sandbox"]
    assert result["tools"][0] == original["tools"][0]
    assert result["tools"][-1] == original["tools"][-1]
    assert result["tools"][1] == module.NEW
    assert [t["name"] for t in result["tools"] if t["name"].startswith("datalake_")] == ["datalake_smart_qa", "datalake_data_qa"]
    assert "inputs" not in result["tools"][1]
    assert result["tools"][1]["api_key"] == "$APPFORGE_DATA_QA_API_KEY"


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda c: c["tools"].append(deepcopy(c["tools"][0])), "DUPLICATE_TOOL_NAMES"),
        (lambda c: c["tools"].append({"name": "datalake_md_search", "use": "x:y"}), "UNEXPECTED_MASTER_DATA"),
        (lambda c: c["tools"][1].update(use="other:tool"), "RETIRED_PROVIDER_MISMATCH"),
        (lambda c: c["tools"][1].update(name="alias"), "RETIRED_PROVIDER_ALIAS"),
    ],
)
def test_unexpected_effective_config_is_not_silently_rewritten(change, code):
    source = config()
    change(source)
    with pytest.raises(ValueError, match=code):
        module.migrate_configuration(source)


def test_repeat_migration_requires_review_instead_of_overwriting():
    with pytest.raises(ValueError, match="ALREADY_MIGRATED"):
        module.migrate_configuration(module.migrate_configuration(config()))


@pytest.mark.parametrize("field", ["tools", "disallowed_tools", "system_prompt"])
def test_custom_agent_old_references_require_explicit_review(field):
    source = config()
    source["subagents"] = {"custom": {"audit": {field: ["datalake_detail_qa"]}}}
    with pytest.raises(ValueError, match="RETIRED_REFERENCE_OUTSIDE_REGISTRATIONS"):
        module.migrate_configuration(source)

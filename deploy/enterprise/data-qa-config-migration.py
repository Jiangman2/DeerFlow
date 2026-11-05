"""Pure configuration transformation. No cluster calls or file writes.

The release controller must create a NEW ConfigMap from this result, validate
it with the candidate image and dedicated Secret, and switch image/config/env
references together. Never overwrite a mounted production ConfigMap.
"""

from copy import deepcopy

RETIRED = frozenset({"datalake_metrics_qa", "datalake_detail_qa", "datalake_metric_qa"})
NEW = {
    "name": "datalake_data_qa",
    "group": "knowledge",
    "use": "deerflow.community.appforge.tools:datalake_data_qa_tool",
    "api_url": "http://10.77.124.142:9080",
    "api_key": "$APPFORGE_DATA_QA_API_KEY",
    "app_id": "app-v16p2syh",
    "conversation_prefix": "SquareWorkflow_",
    "timeout_seconds": 180,
    "trust_env": False,
}


def contains_retired_reference(value):
    if isinstance(value, str):
        return any(name in value for name in RETIRED)
    if isinstance(value, dict):
        return any(contains_retired_reference(k) or contains_retired_reference(v) for k, v in value.items())
    if isinstance(value, list):
        return any(contains_retired_reference(v) for v in value)
    return False


def migrate_configuration(source):
    """Replace retired registrations only; preserve all other configuration.

    This enterprise rollout has no master-data tool. Refuse an unexpected
    registration instead of silently removing someone else's active feature.
    No old identity input or API credential is copied into the new tool.
    """
    if not isinstance(source, dict) or not isinstance(source.get("tools"), list):
        raise ValueError("TOOLS_LIST_REQUIRED")
    # A deny-list referring to the old tools must not silently grant the new
    # tool; prompts and allow-lists likewise need an explicit reviewed edit.
    if contains_retired_reference({k: v for k, v in source.items() if k != "tools"}):
        raise ValueError("RETIRED_REFERENCE_OUTSIDE_REGISTRATIONS")
    tools = source["tools"]
    if any(not isinstance(t, dict) or not isinstance(t.get("name"), str) or not isinstance(t.get("use"), str) for t in tools):
        raise ValueError("UNEXPECTED_TOOL_SHAPE")
    names = [t["name"] for t in tools]
    if len(names) != len(set(names)):
        raise ValueError("DUPLICATE_TOOL_NAMES")
    if names.count("datalake_smart_qa") != 1:
        raise ValueError("SMART_QA_REQUIRED")
    if "datalake_md_search" in names or any(t["use"].endswith(":datalake_md_search_tool") for t in tools):
        raise ValueError("UNEXPECTED_MASTER_DATA_REGISTRATION")
    if "datalake_data_qa" in names:
        raise ValueError("ALREADY_MIGRATED_REVIEW_EXISTING_CONFIG")
    replaced = [t for t in tools if t["name"] in RETIRED]
    if not replaced:
        raise ValueError("RETIRED_REGISTRATION_NOT_FOUND")
    for t in tools:
        module, _, export = t["use"].partition(":")
        if t["name"] in RETIRED:
            if module not in ("deerflow.community.appforge", "deerflow.community.appforge.tools") or export != t["name"] + "_tool":
                raise ValueError("RETIRED_PROVIDER_MISMATCH")
        elif export in {name + "_tool" for name in RETIRED}:
            raise ValueError("RETIRED_PROVIDER_ALIAS")
        elif t["name"].startswith("datalake_") and t["name"] != "datalake_smart_qa":
            raise ValueError("UNEXPECTED_DATALAKE_REGISTRATION")
    result = deepcopy(source)
    output = []
    added = False
    for t in result["tools"]:
        if t["name"] in RETIRED:
            if not added:
                output.append(deepcopy(NEW))
                added = True
        else:
            output.append(t)
    result["tools"] = output
    return result

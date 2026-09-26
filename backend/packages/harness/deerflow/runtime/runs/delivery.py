"""Changed-output acceptance (#4494), with explicit enterprise publishers.

This checks publication, not document quality. The publishing tool/Agent host
still owns its content validation and atomic handoff. Model prose and graph
state are deliberately not inputs to this verifier.
"""

from typing import Any

DELIVERY_INCOMPLETE_ERROR = "Artifact delivery incomplete: no produced output artifact was presented"
DELIVERY_RECEIPT_FAILED_ERROR = "Artifact delivery verification failed: terminal delivery receipt could not be persisted"
PPT_AGENT_PUBLISHER = "ppt_master_agent_host"
_OUTPUT_PREFIX = "/mnt/user-data/outputs"
_DIRECT_PUBLISHING_TOOLS = ("generate_image", "generate_video", "browser_screenshot", "web_capture")


def is_output_path(path: Any, *, allow_root: bool = False) -> bool:
    if not isinstance(path, str) or "\\" in path or any(ord(char) < 32 for char in path):
        return False
    if allow_root:
        path = path.rstrip("/")
    if path == _OUTPUT_PREFIX:
        return allow_root
    if not path.startswith(_OUTPUT_PREFIX + "/"):
        return False
    return all(part not in {"", ".", ".."} for part in path[len(_OUTPUT_PREFIX) + 1 :].split("/"))


def empty_delivery_content() -> dict[str, Any]:
    return {"presented": 0, "paths": [], "by_tool": {}}


def _paths(mapping: Any, key: str) -> list[str]:
    value = mapping.get(key) if isinstance(mapping, dict) else None
    return [path for path in value if isinstance(path, str)] if isinstance(value, list) else []


def delivery_content_with_outputs(content: dict[str, Any], produced_paths: list[str]) -> dict[str, Any]:
    if not produced_paths:
        return content
    by_tool = content.get("by_tool", {})
    presented = [path.rstrip("/") for path in _paths(by_tool, "present_files") if is_output_path(path, allow_root=True)]
    direct = [path for tool in _DIRECT_PUBLISHING_TOOLS for path in _paths(by_tool, tool) if is_output_path(path)]
    external = [path for path in _paths(content.get("by_publisher", {}), PPT_AGENT_PUBLISHER) if is_output_path(path)]
    matched = [path for path in produced_paths if path in direct or path in external or any(path == p or path.startswith(p + "/") for p in presented)]
    all_presented = list(dict.fromkeys(presented + direct + external))
    # Match official semantics: at least one changed output must be presented.
    # This is not a substitute for PPT's stricter all-capability acceptance.
    return {
        **content,
        "verification": {"source": "outputs_changed", "requirement": "trusted_publication_matches_produced_output"},
        "produced_paths": produced_paths,
        "presented_paths": all_presented,
        "matched_paths": matched,
        "stage": "presented" if matched else ("mismatched" if all_presented else "not_started"),
        "satisfied": bool(matched),
    }


def delivery_error(content: dict[str, Any]) -> str | None:
    if not content.get("produced_paths") or content.get("satisfied") is True:
        return None
    return DELIVERY_INCOMPLETE_ERROR

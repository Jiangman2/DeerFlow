"""Incident regression: a 1273-chunk document repeatedly read at page one."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage

from deerflow.agents.middlewares.loop_detection_middleware import _hash_tool_calls
from deerflow.agents.middlewares.tool_output_budget_middleware import _budget_content
from deerflow.community.weknora import tools
from deerflow.config.tool_output_config import ToolOutputConfig


def chunk_page(page, total=1273, count=100):
    return {
        "page": page,
        "page_size": 100,
        "total": total,
        "data": [{"id": f"c-{page}-{i}", "chunk_index": (page - 1) * 100 + i, "content": "test evidence " * 100} for i in range(count)],
    }


def invoke(args, response):
    with patch.object(tools, "_get_json", return_value=response) as get, patch.object(tools, "get_current_weknora_scope", return_value=None):
        result = tools.weknora_list_document_chunks_tool.invoke(args)
    return result, get


def test_next_call_reaches_second_page_preserving_document_and_filters():
    raw, _ = invoke({"knowledge_id": "doc-1", "page_size": 100, "chunk_types": ["text"]}, chunk_page(1))
    page = json.loads(raw)
    next_call = page["pagination"]["next_call"]
    assert next_call == {"knowledge_id": "doc-1", "page": 2, "page_size": 100, "chunk_types": ["text"]}
    second, get = invoke(next_call, chunk_page(2))
    assert get.call_args.kwargs["params"] == {"page": 2, "page_size": 100, "chunk_type": ["text"]}
    assert json.loads(second)["chunks"][0]["chunk_index"] == 100
    original = [{"name": "weknora_list_document_chunks", "args": {"knowledge_id": "doc-1", "page_size": 100}}]
    assert _hash_tool_calls(original) != _hash_tool_calls([{"name": original[0]["name"], "args": next_call}])


@pytest.mark.parametrize("page,total,count,has_more", [(13, 1273, 73, False), (1, 0, 0, False), (1, None, 100, None), (1, 1273, 0, True)])
def test_terminal_unknown_and_inconsistent_pages(page, total, count, has_more):
    raw, _ = invoke({"knowledge_id": "doc-1", "page": page, "page_size": 100}, chunk_page(page, total, count))
    pagination = json.loads(raw)["pagination"]
    assert pagination["has_more"] is has_more
    assert pagination["next_call"] is None


@pytest.mark.parametrize("persist", [True, False])
def test_budget_preserves_navigation_and_current_page_scope(persist):
    raw, _ = invoke({"knowledge_id": "doc-1", "page_size": 100}, chunk_page(1))
    with patch("deerflow.agents.middlewares.tool_output_budget_middleware._externalize", return_value="/mnt/user-data/outputs/.tool-results/page.txt" if persist else None) as save:
        result = _budget_content(raw, tool_name="weknora_list_document_chunks", tool_call_id="tc", outputs_path="unused", config=ToolOutputConfig(fallback_max_chars=2000))
    compact = json.loads(result)
    assert compact["pagination"]["next_call"]["page"] == 2
    assert compact["pagination"]["returned_count"] == 100
    assert compact["full_output"]["scope"] == "current_page_only"
    assert bool(compact["full_output"]["path"]) is persist
    assert compact["preview_only"] is True
    assert compact["omitted_chunks"] + len(compact["chunks"]) == 100
    assert len(result) <= (3000 if persist else 2000)
    assert save.call_args.args[0] == raw  # Persist the entire page, never the excerpt.


def test_scope_denial_is_not_changed_by_pagination():
    with (
        patch.object(tools, "get_current_weknora_scope", return_value={"knowledge_base_ids": ["allowed"]}),
        patch.object(tools, "_scope_tenant_id", return_value=None),
        patch.object(tools, "_document_row_matches_run_scope", return_value=False),
        patch.object(tools, "_get_json", return_value={"data": {"id": "denied"}}) as get,
    ):
        result = json.loads(tools.weknora_list_document_chunks_tool.invoke({"knowledge_id": "denied", "page": 2}))
    assert "error" in result
    assert get.call_count == 1


def test_identical_page_still_hard_stops():
    from deerflow.agents.middlewares.loop_detection_middleware import LoopDetectionMiddleware

    guard = LoopDetectionMiddleware()
    runtime = SimpleNamespace(context={"thread_id": "pagination-test", "run_id": "run-one"})
    guard.before_agent({}, runtime)
    for i in range(5):
        message = AIMessage(content="", id=f"m-{i}", tool_calls=[{"id": f"t-{i}", "name": "weknora_list_document_chunks", "args": {"knowledge_id": "doc-1", "page_size": 100}}])
        result = guard.after_model({"messages": [message]}, runtime)
    assert result["messages"][0].additional_kwargs["error_reason"] == "loop_capped"

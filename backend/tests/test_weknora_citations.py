import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.middlewares.weknora_citation_middleware import WeKnoraCitationMiddleware
from deerflow.community.weknora.citations import normalize_weknora_citations


def _reference(
    *,
    chunk_id: str = "chunk-1",
    knowledge_id: str = "doc-1",
    knowledge_base_id: str = "kb-1",
    title: str = "制度.pdf",
) -> dict[str, str]:
    url = f"/platform/knowledge-bases/{knowledge_base_id}?knowledge_id={knowledge_id}"
    return {
        "chunk_id": chunk_id,
        "knowledge_id": knowledge_id,
        "knowledge_base_id": knowledge_base_id,
        "knowledge_title": title,
        "knowledge_filename": title,
        "citation_url": url,
        "citation_markdown": f"[citation:{title}]({url})",
    }


def test_normalize_weknora_native_kb_tag_uses_chunk_reference():
    reference = _reference()
    answer = '结论来自制度。<kb doc="制度.pdf" chunk_id="chunk-1" kb_id="kb-1" />'

    normalized = normalize_weknora_citations(answer, [reference])

    assert normalized == f"结论来自制度。{reference['citation_markdown']}"


def test_normalize_bare_citation_uses_unique_current_turn_reference():
    reference = _reference()

    normalized = normalize_weknora_citations("结论。[citation:制度.pdf]", [reference])

    assert normalized == f"结论。{reference['citation_markdown']}"


def test_normalize_native_tag_preserves_multiple_chunks_from_same_document():
    first = _reference(chunk_id="chunk-1")
    second = _reference(chunk_id="chunk-2")
    answer = '第二处依据。<kb doc="制度.pdf" chunk_id="chunk-2" kb_id="kb-1" />'

    normalized = normalize_weknora_citations(answer, [first, second])

    assert normalized == f"第二处依据。{second['citation_markdown']}"


def test_normalize_bare_citation_does_not_guess_between_same_named_documents():
    first = _reference(knowledge_id="doc-1", knowledge_base_id="kb-1")
    second = _reference(chunk_id="chunk-2", knowledge_id="doc-2", knowledge_base_id="kb-2")

    normalized = normalize_weknora_citations("结论。[citation:制度.pdf]", [first, second])

    assert normalized == "结论。[citation:制度.pdf]"


def test_normalize_preserves_complete_links_and_code_examples():
    reference = _reference()
    complete = reference["citation_markdown"]
    answer = "\n".join(
        [
            f"正文 {complete}",
            "`[citation:制度.pdf]`",
            "```md",
            "[citation:制度.pdf]",
            "```",
        ]
    )

    assert normalize_weknora_citations(answer, [reference]) == answer


def test_citation_middleware_repairs_terminal_answer_from_weknora_tool_result():
    reference = _reference()
    tool_result = json.dumps(
        {
            "answer": "工具答案",
            "references": [reference],
        },
        ensure_ascii=False,
    )
    final = AIMessage(content="最终答案。[citation:制度.pdf]", id="assistant-1")
    state = {
        "messages": [
            HumanMessage(content="请查询制度", id="human-1"),
            ToolMessage(
                content=tool_result,
                name="weknora_ask",
                tool_call_id="call-1",
                id="tool-1",
            ),
            final,
        ]
    }

    result = WeKnoraCitationMiddleware().after_model(
        state,
        SimpleNamespace(context={"thread_id": "thread-1", "run_id": "run-1"}),
    )

    assert result is not None
    repaired = result["messages"][0]
    assert isinstance(repaired, AIMessage)
    assert repaired.id == final.id
    assert repaired.content == f"最终答案。{reference['citation_markdown']}"


def test_citation_middleware_restores_weknora_answer_image_dropped_by_model():
    image_url = "/api/weknora/knowledge-bases/kb-1/files?file_path=resource%3A%2F%2Fimage_handle-1"
    tool_result = json.dumps(
        {
            "answer": f"原图如下：\n\n![井位图]({image_url})",
            "references": [_reference()],
            "answer_images": [
                {
                    "resource_uri": "resource://image_handle-1",
                    "url": image_url,
                    "markdown": f"![井位图]({image_url})",
                }
            ],
        },
        ensure_ascii=False,
    )
    final = AIMessage(content="根据知识库，图片展示了井位部署方案。", id="assistant-image")
    state = {
        "messages": [
            HumanMessage(content="请显示知识库中的井位图", id="human-image"),
            ToolMessage(
                content=tool_result,
                name="weknora_ask",
                tool_call_id="call-image",
                id="tool-image",
            ),
            final,
        ]
    }

    result = WeKnoraCitationMiddleware().after_model(
        state,
        SimpleNamespace(context={"thread_id": "thread-image", "run_id": "run-image"}),
    )

    assert result is not None
    assert result["messages"][0].content == (f"根据知识库，图片展示了井位部署方案。\n\n![井位图]({image_url})")


def test_citation_middleware_does_not_duplicate_existing_weknora_answer_image():
    image_url = "/api/weknora/knowledge-bases/kb-1/files?file_path=resource%3A%2F%2Fimage_handle-1"
    markdown = f"![井位图]({image_url})"
    tool_result = json.dumps(
        {
            "answer": markdown,
            "references": [_reference()],
            "answer_images": [
                {
                    "resource_uri": "resource://image_handle-1",
                    "url": image_url,
                    "markdown": markdown,
                }
            ],
        },
        ensure_ascii=False,
    )
    final = AIMessage(content=f"原图如下：\n\n{markdown}", id="assistant-image-present")
    state = {
        "messages": [
            HumanMessage(content="请显示原图", id="human-image-present"),
            ToolMessage(
                content=tool_result,
                name="weknora_ask",
                tool_call_id="call-image-present",
                id="tool-image-present",
            ),
            final,
        ]
    }

    result = WeKnoraCitationMiddleware().after_model(
        state,
        SimpleNamespace(context={"thread_id": "thread-image", "run_id": "run-image"}),
    )

    assert result is None


def test_citation_middleware_repairs_lead_answer_from_current_task_result():
    reference = _reference()
    final = AIMessage(content="汇总答案。[citation:制度.pdf]", id="assistant-task")
    state = {
        "messages": [
            HumanMessage(content="委派知识库查询", id="human-task"),
            ToolMessage(
                content=f"Task Succeeded. Result: 子任务答案。{reference['citation_markdown']}",
                name="task",
                tool_call_id="call-task",
                id="tool-task",
            ),
            final,
        ]
    }

    result = WeKnoraCitationMiddleware().after_model(
        state,
        SimpleNamespace(context={"thread_id": "thread-1", "run_id": "run-task"}),
    )

    assert result is not None
    assert result["messages"][0].content == f"汇总答案。{reference['citation_markdown']}"


def test_citation_middleware_ignores_historical_and_non_weknora_references():
    reference = _reference()
    old_tool_result = json.dumps({"references": [reference]}, ensure_ascii=False)
    final = AIMessage(content="示例：[citation:制度.pdf]", id="assistant-2")
    state = {
        "messages": [
            HumanMessage(content="旧任务", id="human-old"),
            ToolMessage(
                content=old_tool_result,
                name="weknora_search",
                tool_call_id="call-old",
                id="tool-old",
            ),
            AIMessage(content="旧回答", id="assistant-old"),
            HumanMessage(content="新任务，不使用知识库", id="human-new"),
            ToolMessage(
                content="ordinary tool output",
                name="bash",
                tool_call_id="call-new",
                id="tool-new",
            ),
            final,
        ]
    }

    result = WeKnoraCitationMiddleware().after_model(
        state,
        SimpleNamespace(context={"thread_id": "thread-1", "run_id": "run-2"}),
    )

    assert result is None

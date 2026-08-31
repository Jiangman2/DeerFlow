from types import SimpleNamespace

from langchain_core.messages import HumanMessage, SystemMessage

from deerflow.agents.middlewares.weknora_scope_middleware import WeKnoraScopeMiddleware, is_weknora_scope_reminder
from deerflow.runtime.weknora_context import WEKNORA_SCOPE_SELECTION_LIMIT, normalize_weknora_scope_items


def _runtime(context):
    return SimpleNamespace(context=context)


def test_scope_items_keep_same_child_id_from_different_knowledge_bases():
    rows = normalize_weknora_scope_items(
        [
            {"id": "shared-tag", "name": "A 标签", "type": "tag", "kb_id": "kb-a"},
            {"id": "shared-tag", "name": "B 标签", "type": "tag", "kb_id": "kb-b"},
        ]
    )

    assert [(row["kb_id"], row["id"]) for row in rows] == [
        ("kb-a", "shared-tag"),
        ("kb-b", "shared-tag"),
    ]


def test_scope_items_are_bounded_for_one_run():
    rows = normalize_weknora_scope_items(
        [
            {
                "id": f"doc-{index}",
                "name": f"Document {index}",
                "type": "file",
                "kb_id": "kb-a",
            }
            for index in range(WEKNORA_SCOPE_SELECTION_LIMIT + 5)
        ]
    )

    assert len(rows) == WEKNORA_SCOPE_SELECTION_LIMIT
    assert rows[-1]["id"] == f"doc-{WEKNORA_SCOPE_SELECTION_LIMIT - 1}"


def test_weknora_scope_middleware_blocks_historical_kb_context_when_scope_is_empty():
    middleware = WeKnoraScopeMiddleware()
    state = {"messages": [HumanMessage(content="Use knowledge", id="msg-1")]}

    result = middleware.before_agent(state, _runtime({}))

    assert result is not None
    reminder = result["messages"][0]
    assert isinstance(reminder, SystemMessage)
    assert is_weknora_scope_reminder(reminder)
    assert "本次运行没有选择 WeKnora 知识范围" in reminder.content
    assert "不要调用任何 `weknora_*` 工具" in reminder.content
    assert "`/mnt/user-data/knowledge_bases`" in reminder.content
    assert "只是历史上下文" in reminder.content


def test_weknora_scope_middleware_injects_selected_kb_file_and_tag_scope():
    middleware = WeKnoraScopeMiddleware()
    state = {"messages": [HumanMessage(content="Use selected docs", id="msg-1")]}

    result = middleware.before_agent(
        state,
        _runtime(
            {
                "weknora_knowledge_base_ids": ["kb-1", "kb-2"],
                "weknora_knowledge_bases": [
                    {"id": "kb-1", "name": "Team Playbook"},
                    {"id": "kb-2", "name": "Ops Manual"},
                    {"id": "kb-3", "name": "Unselected"},
                ],
                "weknora_knowledge_ids": ["doc-1"],
                "weknora_tag_scopes": [
                    {"knowledge_base_id": "kb-2", "tag_ids": ["tag-1"]},
                ],
                "weknora_scope_items": [
                    {"id": "kb-1", "name": "Team Playbook", "type": "kb"},
                    {
                        "id": "doc-1",
                        "name": "Expense Policy.pdf",
                        "type": "file",
                        "kb_id": "kb-1",
                        "kb_name": "Team Playbook",
                    },
                    {
                        "id": "tag-1",
                        "name": "Finance",
                        "type": "tag",
                        "kb_id": "kb-2",
                        "kb_name": "Ops Manual",
                    },
                ],
            }
        ),
    )

    assert result is not None
    messages = result["messages"]
    assert len(messages) == 2

    reminder = messages[0]
    assert isinstance(reminder, SystemMessage)
    assert is_weknora_scope_reminder(reminder)
    assert "Team Playbook (kb-1)" in reminder.content
    assert "Expense Policy.pdf (doc-1)" in reminder.content
    assert "Finance (tag-1)" in reminder.content
    assert "位于 Ops Manual" in reminder.content
    assert "Unselected" not in reminder.content
    assert "weknora_search" in reminder.content
    assert "weknora_ask" in reminder.content
    assert "weknora_read_document" in reminder.content
    assert "weknora_list_agents" in reminder.content
    assert "weknora_invoke_agent" in reminder.content
    assert "不得自动批准 pending_actions" in reminder.content
    assert "自动注入检索、问答和智能体调用" in reminder.content
    assert "不得删除、替换或扩大该范围" in reminder.content
    assert "citation_markdown" in reminder.content
    assert "resource_images[].markdown" in reminder.content
    assert "不得为 WeKnora 来源构造或暴露 `/mnt/`" in reminder.content

    user_message = messages[1]
    assert isinstance(user_message, HumanMessage)
    assert user_message.id == "msg-1__weknora_user"
    assert user_message.content == "Use selected docs"

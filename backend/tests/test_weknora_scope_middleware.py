from types import SimpleNamespace

from langchain_core.messages import HumanMessage, SystemMessage

from deerflow.agents.middlewares.weknora_scope_middleware import WeKnoraScopeMiddleware, is_weknora_scope_reminder


def _runtime(context):
    return SimpleNamespace(context=context)


def test_weknora_scope_middleware_blocks_historical_kb_context_when_scope_is_empty():
    middleware = WeKnoraScopeMiddleware()
    state = {"messages": [HumanMessage(content="Use knowledge", id="msg-1")]}

    result = middleware.before_agent(state, _runtime({}))

    assert result is not None
    reminder = result["messages"][0]
    assert isinstance(reminder, SystemMessage)
    assert is_weknora_scope_reminder(reminder)
    assert "本次运行没有选择 WeKnora 知识库" in reminder.content
    assert "不要调用 `weknora_search` 或 `weknora_ask`" in reminder.content
    assert "`/mnt/user-data/knowledge_bases`" in reminder.content
    assert "只是历史上下文" in reminder.content


def test_weknora_scope_middleware_injects_selected_knowledge_base_ids():
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
    assert "Ops Manual (kb-2)" in reminder.content
    assert "Unselected" not in reminder.content
    assert "weknora_search" in reminder.content
    assert "weknora_ask" in reminder.content
    assert 'knowledge_base_ids` 原样传为 ["kb-1", "kb-2"]' in reminder.content
    assert "citation_markdown" in reminder.content
    assert "resource_images[].markdown" in reminder.content
    assert "不得为 WeKnora 来源构造或暴露 `/mnt/`" in reminder.content

    user_message = messages[1]
    assert isinstance(user_message, HumanMessage)
    assert user_message.id == "msg-1__weknora_user"
    assert user_message.content == "Use selected docs"

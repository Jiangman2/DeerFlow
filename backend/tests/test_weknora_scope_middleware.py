from types import SimpleNamespace

from langchain_core.messages import HumanMessage, SystemMessage

from deerflow.agents.middlewares.weknora_scope_middleware import WeKnoraScopeMiddleware, is_weknora_scope_reminder


def _runtime(context):
    return SimpleNamespace(context=context)


def test_weknora_scope_middleware_skips_when_scope_is_empty():
    middleware = WeKnoraScopeMiddleware()
    state = {"messages": [HumanMessage(content="Use knowledge", id="msg-1")]}

    assert middleware.before_agent(state, _runtime({})) is None


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
    assert 'knowledge_base_ids` exactly as ["kb-1", "kb-2"]' in reminder.content

    user_message = messages[1]
    assert isinstance(user_message, HumanMessage)
    assert user_message.id == "msg-1__weknora_user"
    assert user_message.content == "Use selected docs"

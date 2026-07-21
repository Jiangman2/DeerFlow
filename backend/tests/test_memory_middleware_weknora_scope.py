from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares.memory_middleware import MemoryMiddleware
from deerflow.config.memory_config import MemoryConfig


def _runtime(context: dict) -> SimpleNamespace:
    return SimpleNamespace(context=context)


def test_selected_weknora_run_does_not_enter_durable_memory_queue() -> None:
    manager = MagicMock()
    middleware = MemoryMiddleware(memory_config=MemoryConfig(enabled=True))
    state = {
        "messages": [
            HumanMessage(content="检索所选知识库"),
            AIMessage(content="临时知识库中的项目口令是青松-4729"),
        ]
    }

    with patch(
        "deerflow.agents.middlewares.memory_middleware.get_memory_manager",
        return_value=manager,
    ):
        result = middleware.after_agent(
            state,
            _runtime(
                {
                    "thread_id": "thread-selected-kb",
                    "weknora_knowledge_base_ids": ["kb-1"],
                }
            ),
        )

    assert result is None
    manager.add.assert_not_called()


def test_unselected_run_still_uses_normal_durable_memory_policy() -> None:
    manager = MagicMock()
    middleware = MemoryMiddleware(memory_config=MemoryConfig(enabled=True))
    messages = [HumanMessage(content="请记住我偏好中文"), AIMessage(content="好的")]

    with (
        patch(
            "deerflow.agents.middlewares.memory_middleware.get_memory_manager",
            return_value=manager,
        ),
        patch(
            "deerflow.agents.middlewares.memory_middleware.get_effective_user_id",
            return_value="user-1",
        ),
    ):
        result = middleware.after_agent(
            {"messages": messages},
            _runtime({"thread_id": "thread-no-kb", "weknora_knowledge_base_ids": []}),
        )

    assert result is None
    manager.add.assert_called_once()
    assert manager.add.call_args.args[:2] == ("thread-no-kb", messages)

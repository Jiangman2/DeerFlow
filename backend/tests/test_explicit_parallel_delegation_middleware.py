from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares.explicit_parallel_delegation_middleware import ExplicitParallelDelegationMiddleware


def test_requires_initial_delegation_for_explicit_ultra_numbered_workstreams() -> None:
    messages = [
        HumanMessage(
            name="user-input",
            content="请在 Ultra 模式下并行委派：\n1. 检索资料\n2. 分析案例\n3. 构建矩阵",
        )
    ]

    assert ExplicitParallelDelegationMiddleware.requires_initial_delegation(messages)


def test_does_not_gate_simple_or_non_parallel_requests() -> None:
    assert not ExplicitParallelDelegationMiddleware.requires_initial_delegation([HumanMessage(content="请总结这份报告")])
    assert not ExplicitParallelDelegationMiddleware.requires_initial_delegation([HumanMessage(content="请在 Ultra 模式下完成一项任务")])


def test_recognizes_existing_task_delegation() -> None:
    delegated = AIMessage(
        content="",
        tool_calls=[{"name": "task", "args": {"description": "research", "prompt": "research", "subagent_type": "general-purpose"}, "id": "call-1"}],
    )

    assert ExplicitParallelDelegationMiddleware.has_task_delegation([delegated])

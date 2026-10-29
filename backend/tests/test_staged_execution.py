"""Synthetic stage transactions: no provider, network or business writes."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from deerflow.agents.middlewares.staged_execution_middleware import StagedExecutionMiddleware
from deerflow.config.staged_execution_config import StagedExecutionConfig


def config(**kwargs):
    return StagedExecutionConfig(enabled=True, thread_ids=["synthetic"], **kwargs)


def runtime(run="r1", thread="synthetic"):
    return SimpleNamespace(context={"run_id": run, "thread_id": thread})


def call(name, identifier, **args):
    return AIMessage(content="", tool_calls=[{"name": name, "id": identifier, "args": args}])


STAGES = [
    {"objective": "Extract evidence", "acceptance": "Evidence is available"},
    {"objective": "Validate and deliver", "acceptance": "Validated final artifact is published"},
]


class ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@tool
def read_evidence() -> str:
    """Read synthetic evidence without I/O."""
    return "synthetic evidence"


@tool
def present_files() -> str:
    """Simulate publication of one validated JSON file without writing it."""
    return "synthetic.json published"


def make_graph(responses, *, middleware=None, extra_tools=None, checkpointer=None):
    model = ScriptedModel(responses=responses)
    graph = create_agent(
        model,
        tools=extra_tools or [read_evidence, present_files],
        middleware=[middleware or StagedExecutionMiddleware(config())],
        checkpointer=checkpointer,
        context_schema=dict,
    )
    return graph, model


def invoke(graph, text="Make one validated JSON file", run="r1", thread="synthetic"):
    return graph.invoke(
        {"messages": [HumanMessage(content=text, id=run + "-user")]},
        config={"configurable": {"thread_id": thread}, "recursion_limit": 60},
        context={"run_id": run, "thread_id": thread},
    )


def sequential_responses():
    return [
        call("start_execution_stages", "plan", stages=STAGES),
        call("read_evidence", "read"),
        call("advance_execution_stage", "next", stage=1, result="Evidence found", evidence_ids=["read"]),
        call("present_files", "publish"),
        call("advance_execution_stage", "finish", stage=2, result="One JSON delivered", evidence_ids=["publish"]),
        AIMessage(content="Delivered synthetic.json"),
    ]


def test_default_off_and_empty_allowlist_fail_closed():
    assert not StagedExecutionConfig().enabled
    for cfg in [StagedExecutionConfig(), StagedExecutionConfig(enabled=True)]:
        middleware = StagedExecutionMiddleware(cfg)
        assert middleware.before_agent({"messages": []}, runtime()) is None
    assert StagedExecutionMiddleware(config()).before_agent({"messages": []}, runtime(thread="other")) is None


@pytest.mark.parametrize("field,value", [("max_stages", 1), ("max_stage_model_calls", 0), ("max_total_model_calls", 0)])
def test_config_rejects_unbounded_or_invalid_limits(field, value):
    with pytest.raises(ValueError):
        config(**{field: value})


def test_simple_answer_has_no_extra_call_or_stage_plan():
    graph, model = make_graph([AIMessage(content="42"), AIMessage(content="should not run")])
    result = invoke(graph, "6 * 7?")
    assert result["messages"][-1].content == "42"
    assert model.i == 1
    assert not result["staged_execution"]["stages"]


def test_short_dependent_tool_flow_needs_no_stage_or_extra_planning_call():
    graph, model = make_graph([call("read_evidence", "read"), call("present_files", "publish"), AIMessage(content="Delivered"), AIMessage(content="must not run")])
    result = invoke(graph, "Read this short input and deliver its checked result")
    assert result["messages"][-1].content == "Delivered"
    assert model.i == 3
    assert result["staged_execution"]["model_calls"] == 3
    assert not result["staged_execution"]["stages"]
    assert [m.name for m in result["messages"] if isinstance(m, ToolMessage)] == ["read_evidence", "present_files"]


def test_rejected_no_effect_submission_can_be_corrected_without_a_stage():
    from langchain.tools import ToolRuntime

    effects = []

    @tool
    def submit_checked(value: int, runtime: ToolRuntime[dict]) -> ToolMessage:
        """Submit a checked synthetic integer; invalid values cause no effect."""
        if value != 42:
            return ToolMessage(content="VALIDATION_REJECTED_NO_EFFECT", tool_call_id=runtime.tool_call_id, status="error")
        effects.append(value)
        return ToolMessage(content="accepted", tool_call_id=runtime.tool_call_id, status="success")

    graph, model = make_graph(
        [call("submit_checked", "bad", value=41), call("submit_checked", "corrected", value=42), AIMessage(content="42"), AIMessage(content="must not run")],
        extra_tools=[submit_checked],
    )
    result = invoke(graph, "Submit the checked value")
    assert effects == [42]
    assert model.i == 3
    assert not result["staged_execution"]["stages"]
    assert [m.status for m in result["messages"] if isinstance(m, ToolMessage)] == ["error", "success"]
    assert not result["messages"][-1].additional_kwargs.get("deerflow_terminal_stop")


def test_adopting_stages_after_discovery_does_not_replay_completed_effect():
    effects = []

    @tool
    def save_initial_result() -> str:
        """Save one synthetic result in memory."""
        effects.append("saved")
        return "saved"

    @tool
    def inspect_saved_result() -> str:
        """Inspect the already saved result without rewriting it."""
        assert effects == ["saved"]
        return "saved result verified"

    graph, _ = make_graph(
        [
            call("save_initial_result", "saved-before-plan"),
            call("start_execution_stages", "plan", stages=STAGES),
            call("inspect_saved_result", "verify-existing"),
            call("advance_execution_stage", "next", stage=1, result="Existing result verified", evidence_ids=["verify-existing"]),
            call("present_files", "publish"),
            call("advance_execution_stage", "finish", stage=2, result="Delivered", evidence_ids=["publish"]),
            AIMessage(content="Delivered"),
        ],
        extra_tools=[save_initial_result, inspect_saved_result, present_files],
    )
    result = invoke(graph, "Inspect the input and complete the remaining dependent work")
    assert effects == ["saved"]
    assert result["staged_execution"]["status"] == "completed"
    assert result["staged_execution"]["model_calls"] == 7
    assert result["staged_execution"]["completed"][0]["evidence_ids"] == ["verify-existing"]


def test_sequential_stages_cross_real_model_boundaries_and_checkpoint():
    saver = InMemorySaver()
    graph, _ = make_graph(sequential_responses(), checkpointer=saver)
    result = invoke(graph)
    plan = result["staged_execution"]
    assert plan["status"] == "completed"
    assert plan["completed"][0]["evidence_ids"] == ["read"]
    assert plan["completed"][1]["evidence_ids"] == ["publish"]
    assert plan["model_calls"] == 6
    assert result["messages"][-1].content == "Delivered synthetic.json"
    assert graph.get_state({"configurable": {"thread_id": "synthetic"}}).values["staged_execution"] == plan


@pytest.mark.asyncio
async def test_async_graph_has_the_same_stage_transactions():
    graph, _ = make_graph(sequential_responses())
    result = await graph.ainvoke(
        {"messages": [HumanMessage(content="Make one JSON", id="u")]},
        context={"thread_id": "synthetic", "run_id": "async"},
    )
    assert result["staged_execution"]["status"] == "completed"


def test_mixed_stage_and_business_tool_batch_executes_neither():
    first = call("start_execution_stages", "plan", stages=STAGES)
    first.tool_calls.append({"name": "present_files", "id": "write", "args": {}})
    graph, _ = make_graph([first])
    result = invoke(graph)
    assert not any(isinstance(m, ToolMessage) for m in result["messages"])
    assert result["messages"][-1].additional_kwargs["deerflow_terminal_stop"]


@pytest.mark.parametrize("evidence", [["invented"], ["old"], ["plan"]])
def test_cannot_advance_with_invented_historical_or_planning_receipt(evidence):
    graph, _ = make_graph(
        [
            call("start_execution_stages", "plan", stages=STAGES),
            call("advance_execution_stage", "invalid", stage=1, result="claimed", evidence_ids=evidence),
            AIMessage(content="done"),
        ]
    )
    result = invoke(graph)
    assert not result["staged_execution"]["completed"]
    errors = [m for m in result["messages"] if isinstance(m, ToolMessage) and m.status == "error"]
    assert errors and "STAGE_EVIDENCE_REQUIRED" in errors[0].content
    assert result["messages"][-1].additional_kwargs["deerflow_terminal_stop"]


def test_error_receipt_is_not_accepted():
    middleware = StagedExecutionMiddleware(config())
    initial = middleware.before_agent({"messages": [HumanMessage(content="x", id="u")]}, runtime())
    plan = initial["staged_execution"]
    plan.update(stages=deepcopy(STAGES), status="active", current_stage=0)
    state = {**initial, "messages": [ToolMessage(content="bad", tool_call_id="failed", name="read_evidence", status="error")]}
    updated = middleware.before_model(state, runtime())["staged_execution"]
    assert updated["receipts"] == {}


def test_stage_budget_stops_without_an_extra_model_call():
    responses = [call("start_execution_stages", "plan", stages=STAGES), call("read_evidence", "read")]
    graph, model = make_graph(responses, middleware=StagedExecutionMiddleware(config(max_stage_model_calls=1)))
    result = invoke(graph)
    assert result["staged_execution"]["status"] == "paused"
    assert result["staged_execution"]["stop_reason"] == "stage_call_budget"
    assert result["messages"][-1].additional_kwargs["deerflow_terminal_stop"]
    assert model.i == 0  # Exactly two scripted responses, not a third call.


def test_stage_transition_does_not_reset_total_budget():
    graph, _ = make_graph(sequential_responses(), middleware=StagedExecutionMiddleware(config(max_total_model_calls=4)))
    result = invoke(graph)
    assert len(result["staged_execution"]["completed"]) == 1
    assert result["staged_execution"]["model_calls"] == 4
    assert result["staged_execution"]["stop_reason"] == "total_call_budget"


def test_terminal_reasoning_stop_never_reengages_model_or_completes_stage():
    stopped = AIMessage(content="reasoning budget exceeded", additional_kwargs={"deerflow_terminal_stop": True})
    graph, model = make_graph([call("start_execution_stages", "plan", stages=STAGES), stopped, AIMessage(content="must not run")])
    result = invoke(graph)
    assert model.i == 2
    assert result["staged_execution"]["status"] == "paused"
    assert not result["staged_execution"]["completed"]
    assert result["messages"][-1].content == stopped.content


def test_skill_workflow_is_not_replaced_or_reengaged():
    middleware = StagedExecutionMiddleware(config())
    state = {"messages": [], "skill_workflow": {"activation_id": "skill", "status": "waiting"}}
    assert middleware.before_agent(state, runtime()) is None
    assert middleware.before_model(state, runtime()) is None
    assert middleware.after_model(state, runtime()) is None


def test_explicit_continuation_keeps_stage_and_receipts_but_new_task_resets():
    middleware = StagedExecutionMiddleware(config())
    graph, _ = make_graph(
        [
            call("start_execution_stages", "plan", stages=STAGES),
            call("read_evidence", "read"),
            AIMessage(content="Paused"),
        ],
        middleware=middleware,
    )
    result = invoke(graph)
    old = deepcopy(result["staged_execution"])
    resumed = middleware.before_agent({**result, "messages": [HumanMessage(content="继续", id="next")]}, runtime(run="r2"))
    new = resumed["staged_execution"]
    assert new["stages"] == old["stages"]
    assert new["receipts"] == old["receipts"]
    assert new["model_calls"] == old["model_calls"]
    assert new["stage_calls"] == old["stage_calls"]
    assert new["run_id"] == "r2"
    reset = middleware.before_agent({**result, "messages": [HumanMessage(content="New unrelated task", id="new")]}, runtime(run="r3"))
    assert reset["staged_execution"]["stages"] == []


def test_tool_schema_hides_runtime_and_has_no_budget_override():
    middleware = StagedExecutionMiddleware(config())
    for item in middleware.tools:
        schema = item.tool_call_schema.model_json_schema()
        assert "runtime" not in schema["properties"]
        assert "budget" not in schema["properties"]


def test_parallel_task_remains_available_without_stage_bookkeeping():
    @tool
    def task(description: str) -> str:
        """Perform synthetic independent work."""
        return description

    first = call("task", "a", description="independent A")
    first.tool_calls.append({"name": "task", "id": "b", "args": {"description": "independent B"}})
    graph, _ = make_graph([first, AIMessage(content="Combined")], extra_tools=[task])
    result = invoke(graph)
    assert len([m for m in result["messages"] if isinstance(m, ToolMessage)]) == 2
    assert result["messages"][-1].content == "Combined"
    assert not result["staged_execution"]["stages"]


def test_two_invalid_plans_stop_even_before_a_plan_exists():
    graph, model = make_graph(
        [
            call("start_execution_stages", "bad1", stages=STAGES[:1]),
            call("start_execution_stages", "bad2", stages=STAGES[:1]),
            AIMessage(content="must not run"),
        ]
    )
    result = invoke(graph)
    assert model.i == 2
    assert result["staged_execution"]["stop_reason"] == "stage_control_errors"


def test_previous_stage_evidence_cannot_complete_next_stage():
    responses = sequential_responses()[:3] + [
        call("advance_execution_stage", "bad", stage=2, result="claimed", evidence_ids=["read"]),
        AIMessage(content="paused"),
    ]
    graph, _ = make_graph(responses)
    result = invoke(graph)
    assert len(result["staged_execution"]["completed"]) == 1
    assert any(isinstance(m, ToolMessage) and m.content == "STAGE_EVIDENCE_REQUIRED" for m in result["messages"])


@pytest.mark.parametrize("status", ["error", "partial_success"])
def test_structured_failure_metadata_overrides_default_tool_success(status):
    middleware = StagedExecutionMiddleware(config())
    state = middleware.before_agent({"messages": [HumanMessage(content="x", id="u")]}, runtime())
    state["staged_execution"].update(stages=deepcopy(STAGES), status="active")
    state["messages"] = [ToolMessage(content="result", name="read_evidence", tool_call_id="partial", additional_kwargs={"deerflow_tool_meta": {"status": status}})]
    assert not middleware.before_model(state, runtime())["staged_execution"]["receipts"]


def test_mode_gate_and_actual_lead_chain_order(monkeypatch):
    from deerflow.agents.lead_agent import agent as lead
    from deerflow.config.app_config import AppConfig
    from deerflow.config.sandbox_config import SandboxConfig

    app = AppConfig(sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"))
    original = {"configurable": {"thinking_enabled": True, "subagent_enabled": False, "is_plan_mode": False}}
    baseline = lead.build_middlewares(original, None, app_config=app)
    assert not any(isinstance(m, StagedExecutionMiddleware) for m in baseline)
    app.staged_execution = config()
    enabled = lead.build_middlewares(original, None, app_config=app)
    names = [type(m).__name__ for m in enabled]
    assert names.index("TerminalResponseMiddleware") < names.index("StagedExecutionMiddleware")
    assert "SubagentLimitMiddleware" not in names
    assert original["configurable"] == {"thinking_enabled": True, "subagent_enabled": False, "is_plan_mode": False}
    flash = lead.build_middlewares({"configurable": {"thinking_enabled": False}}, None, app_config=app)
    assert not any(isinstance(m, StagedExecutionMiddleware) for m in flash)


def test_hard_stop_survives_todo_and_terminal_recovery():
    from deerflow.agents.middlewares.terminal_response_middleware import TerminalResponseMiddleware
    from deerflow.agents.middlewares.todo_middleware import TodoMiddleware
    from deerflow.agents.thread_state import ThreadState

    stop = AIMessage(content="reasoning stopped", additional_kwargs={"deerflow_terminal_stop": True})
    model = ScriptedModel(responses=[call("start_execution_stages", "plan", stages=STAGES), stop, AIMessage(content="not reached")])
    graph = create_agent(model, tools=[read_evidence], state_schema=ThreadState, context_schema=dict, middleware=[TodoMiddleware(), TerminalResponseMiddleware(), StagedExecutionMiddleware(config())])
    result = invoke(graph)
    assert model.i == 2
    assert result["messages"][-1].content == "reasoning stopped"


def test_fake_model_cannot_call_stage_tool_when_thread_not_allowed():
    graph, _ = make_graph([call("start_execution_stages", "plan", stages=STAGES), AIMessage(content="done")])
    result = invoke(graph, thread="not-allowed")
    assert not result.get("staged_execution")
    assert any(isinstance(m, ToolMessage) and m.content == "STAGE_CONTROL_DISABLED" for m in result["messages"])


def test_receipts_survive_history_compaction_before_transition():
    from langchain.agents.middleware import AgentMiddleware
    from langchain_core.messages import RemoveMessage
    from langgraph.graph.message import REMOVE_ALL_MESSAGES

    from deerflow.agents.middlewares.staged_execution_middleware import StageReceiptCaptureMiddleware

    class CompactSyntheticHistory(AgentMiddleware):
        def before_model(self, state, runtime):
            if isinstance(state["messages"][-1], ToolMessage) and state["messages"][-1].name == "read_evidence":
                return {"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), HumanMessage(content="Earlier evidence retained in stage checkpoint", name="summary")]}

    controller = StagedExecutionMiddleware(config())
    graph = create_agent(ScriptedModel(responses=sequential_responses()), tools=[read_evidence, present_files], context_schema=dict, middleware=[StageReceiptCaptureMiddleware(controller), CompactSyntheticHistory(), controller])
    result = invoke(graph)
    assert result["staged_execution"]["status"] == "completed"
    assert result["staged_execution"]["completed"][0]["evidence_ids"] == ["read"]


def test_real_output_guard_exception_stays_terminal_through_chain():
    from deerflow.agents.middlewares.llm_error_handling_middleware import LLMErrorHandlingMiddleware
    from deerflow.agents.middlewares.terminal_response_middleware import TerminalResponseMiddleware
    from deerflow.config.app_config import AppConfig
    from deerflow.config.sandbox_config import SandboxConfig
    from deerflow.models.output_safety import ModelOutputSafetyError

    class ExhaustedModel(ScriptedModel):
        attempts: int = 0

        def _generate(self, *args, **kwargs):
            self.attempts += 1
            if self.attempts == 2:
                raise ModelOutputSafetyError("reasoning_budget_exceeded")
            return super()._generate(*args, **kwargs)

    app = AppConfig(sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"))
    model = ExhaustedModel(responses=[call("start_execution_stages", "p", stages=STAGES)])
    graph = create_agent(model, tools=[read_evidence], context_schema=dict, middleware=[LLMErrorHandlingMiddleware(app_config=app), TerminalResponseMiddleware(), StagedExecutionMiddleware(config())])
    result = invoke(graph)
    assert model.attempts == 2
    assert result["messages"][-1].additional_kwargs["deerflow_terminal_stop"]
    assert result["staged_execution"]["status"] == "paused"


def test_invalid_tool_schema_is_bounded_before_a_plan_exists():
    graph, model = make_graph(
        [
            call("start_execution_stages", "bad1", stages=[{"missing": "fields"}]),
            call("start_execution_stages", "bad2", stages=[{"missing": "fields"}]),
            AIMessage(content="must not run"),
        ]
    )
    result = invoke(graph)
    assert model.i == 2
    assert result["staged_execution"]["stop_reason"] == "stage_control_errors"


def test_real_checkpoint_resume_does_not_repeat_completed_business_tools():
    sequence = sequential_responses()
    responses = sequence[:2] + [AIMessage(content="请确认是否继续？")] + sequence[2:]
    graph, _ = make_graph(responses, checkpointer=InMemorySaver())
    first = invoke(graph)
    assert first["staged_execution"]["status"] == "paused"
    result = invoke(graph, "继续", run="r2")
    assert result["staged_execution"]["status"] == "completed"
    assert result["staged_execution"]["model_calls"] == 7
    assert len([m for m in result["messages"] if isinstance(m, ToolMessage) and m.name == "read_evidence"]) == 1


def test_skill_activation_supersedes_old_generic_plan_without_changing_skill():
    middleware = StagedExecutionMiddleware(config())
    state = middleware.before_agent({"messages": [HumanMessage(content="x", id="u")]}, runtime())
    state["staged_execution"].update(stages=deepcopy(STAGES), status="active")
    workflow = {"activation_id": "skill", "status": "active", "current_stage": "confirm"}
    state.update(skill_workflow=workflow, messages=[HumanMessage(content="/ppt-master make slides", id="s")])
    result = middleware.before_agent(state, runtime(run="skill-run"))
    assert result["staged_execution"]["status"] == "superseded_by_skill"
    assert "skill_workflow" not in result
    assert workflow == state["skill_workflow"]


def test_malformed_structured_tool_metadata_is_not_evidence():
    middleware = StagedExecutionMiddleware(config())
    state = middleware.before_agent({"messages": [HumanMessage(content="x", id="u")]}, runtime())
    state["staged_execution"].update(stages=deepcopy(STAGES), status="active")
    state["messages"] = [ToolMessage(content="result", name="read_evidence", tool_call_id="bad", additional_kwargs={"deerflow_tool_meta": "not metadata"})]
    assert not middleware.before_model(state, runtime())["staged_execution"]["receipts"]

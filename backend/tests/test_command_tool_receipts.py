"""Official #4977 result wrappers plus enterprise command-repair boundaries."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.types import Command

from deerflow.agents.middlewares.tool_error_handling_middleware import ToolErrorHandlingMiddleware
from deerflow.agents.middlewares.tool_progress_middleware import ToolProgressMiddleware
from deerflow.agents.middlewares.tool_result_meta import TOOL_META_KEY, normalize_tool_message, normalize_tool_result
from deerflow.subagents.status_contract import make_subagent_additional_kwargs


def receipt(content="Task did not finish", *, call_id="current", **metadata):
    return ToolMessage(content=content, tool_call_id=call_id, additional_kwargs=metadata)


def wrap(message, shape):
    messages = {"single": message, "list": [message], "tuple": (message,)}[shape]
    return Command(update={"messages": messages, "artifacts": ["/mnt/user-data/outputs/report.pdf"]}, goto="next", resume={"opaque": 1})


@pytest.mark.parametrize("status", ["failed", "cancelled", "timed_out", "polling_timed_out"])
@pytest.mark.parametrize("shape", ["single", "list", "tuple"])
def test_structured_subagent_failure_survives_command_wrapper(status, shape):
    metadata = make_subagent_additional_kwargs(status, error="connection timed out")
    message = receipt(**metadata)
    command = wrap(message, shape)
    update = command.update
    assert normalize_tool_result(command, tool_call_id="current") is command
    assert command.update is update
    assert command.goto == "next" and command.resume == {"opaque": 1}
    assert command.update["artifacts"] == ["/mnt/user-data/outputs/report.pdf"]
    assert message.content == "Task did not finish"
    assert message.status == "success"  # Normalize our metadata, not LangGraph control semantics.
    meta = message.additional_kwargs[TOOL_META_KEY]
    assert meta["status"] == "error" and meta["error_type"] == "transient"
    assert meta["source"] == "tool_return"
    assert all(message.additional_kwargs[key] == value for key, value in metadata.items())


@pytest.mark.parametrize("error", [None, "", {"unsafe": "auth"}])
def test_structured_failure_without_text_error_falls_back_to_content(error):
    message = receipt("connection timed out", subagent_status="failed", subagent_error=error)
    normalize_tool_message(message)
    assert message.additional_kwargs[TOOL_META_KEY]["error_type"] == "transient"


@pytest.mark.parametrize("status", ["completed", "running", "future_status", None, [], {}])
def test_nonfailure_or_malformed_status_keeps_normal_content_classification(status):
    message = receipt("Completed with a new report", subagent_status=status)
    normalize_tool_message(message)
    assert message.additional_kwargs[TOOL_META_KEY]["status"] == "success"


def test_producer_metadata_remains_authoritative():
    stamped = {"status": "error", "source": "exception", "error_type": "auth"}
    message = receipt(subagent_status="failed", **{TOOL_META_KEY: stamped})
    normalize_tool_result(wrap(message, "single"), tool_call_id="current")
    assert message.additional_kwargs[TOOL_META_KEY] == stamped


@pytest.mark.parametrize("matching", [True, False])
def test_only_current_tool_message_is_stamped(matching):
    unrelated = receipt("Error: unauthorized", call_id="other")
    current = receipt("done", call_id="current" if matching else "different")
    human = HumanMessage(content="Error: not a tool receipt")
    command = Command(update={"messages": [unrelated, human, current], "custom": {"keep": True}})
    assert normalize_tool_result(command, tool_call_id="current") is command
    assert TOOL_META_KEY not in unrelated.additional_kwargs
    assert TOOL_META_KEY not in human.additional_kwargs
    assert (TOOL_META_KEY in current.additional_kwargs) == matching
    assert command.update["custom"] == {"keep": True}


@pytest.mark.parametrize("update", [None, [], "opaque", {"messages": "opaque"}, {"messages": {"content": "Error: unauthorized"}}])
def test_unknown_command_updates_are_not_reinterpreted(update):
    command = Command(update=update, goto="other")
    assert normalize_tool_result(command, tool_call_id="current") is command
    assert command.update is update


async def pipeline(command, *, tool="web_search", asynchronous=False, progress=None):
    runtime = SimpleNamespace(context={"thread_id": "thread", "run_id": "run"})
    request = SimpleNamespace(tool_call={"name": tool, "id": "current"}, runtime=runtime)
    error = ToolErrorHandlingMiddleware()
    progress = progress or ToolProgressMiddleware(stagnation_threshold=1)
    if asynchronous:

        async def tool_handler(_request):
            return command

        async def error_handler(req):
            return await error.awrap_tool_call(req, tool_handler)

        result = await progress.awrap_tool_call(request, error_handler)
    else:
        result = progress.wrap_tool_call(request, lambda req: error.wrap_tool_call(req, lambda _: command))
    return result, progress, runtime


@pytest.mark.anyio
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("shape", ["single", "list", "tuple"])
async def test_command_error_reaches_real_middleware_chain(asynchronous, shape):
    message = receipt("Error: unauthorized")
    command = wrap(message, shape)
    result, progress, _ = await pipeline(command, asynchronous=asynchronous)
    assert result is command
    assert message.additional_kwargs[TOOL_META_KEY]["status"] == "error"
    state = progress._get_state("thread", "web_search")
    assert state.phase == "blocked" and state.consecutive_problems == 1


@pytest.mark.anyio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_other_receipts_cannot_block_current_tool(asynchronous):
    other = receipt("Error: unauthorized", call_id="other")
    current = receipt("Fresh useful data for this request")
    command = Command(update={"messages": [other, current]})
    result, progress, _ = await pipeline(command, asynchronous=asynchronous)
    assert result is command
    assert TOOL_META_KEY not in other.additional_kwargs
    assert progress._get_state("thread", "web_search").consecutive_problems == 0


@pytest.mark.anyio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_command_problem_warns_then_fresh_result_recovers(asynchronous):
    command = wrap(receipt("Error: no results found"), "single")
    _, progress, _ = await pipeline(command, asynchronous=asynchronous)
    assert progress._get_state("thread", "web_search").phase == "warned"
    assert len(progress._pending[("thread", "run")]) == 1
    await pipeline(wrap(receipt("Fresh result with useful data"), "tuple"), asynchronous=asynchronous, progress=progress)
    assert progress._get_state("thread", "web_search").phase == "active"


@pytest.mark.anyio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_bash_command_failure_does_not_disable_project_repair(asynchronous):
    command = wrap(receipt("Error: missing required project configuration"), "single")
    result, progress, _ = await pipeline(command, tool="bash", asynchronous=asynchronous)
    assert result is command
    assert progress._get_state("thread", "bash").phase == "warned"
    await pipeline(wrap(receipt("Repaired project configuration"), "single"), tool="bash", asynchronous=asynchronous, progress=progress)
    assert progress._get_state("thread", "bash").phase == "active"


@pytest.mark.anyio
async def test_bash_infrastructure_exception_stamp_still_blocks():
    message = receipt(
        "Error: unauthorized",
        **{
            TOOL_META_KEY: {
                "status": "error",
                "error_type": "auth",
                "source": "exception",
                "recoverable_by_model": False,
                "recommended_next_action": "stop",
            }
        },
    )
    _, progress, _ = await pipeline(wrap(message, "single"), tool="bash")
    assert progress._get_state("thread", "bash").phase == "blocked"


@pytest.mark.anyio
async def test_default_task_exemption_preserved_but_receipt_is_error():
    message = receipt(subagent_status="failed", subagent_error="connection timeout")
    result, progress, _ = await pipeline(wrap(message, "single"), tool="task")
    assert isinstance(result, Command)
    assert message.additional_kwargs[TOOL_META_KEY]["status"] == "error"
    assert not progress._phase_states

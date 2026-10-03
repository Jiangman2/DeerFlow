from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from deerflow.runtime.runs import worker
from deerflow.runtime.runs.manager import RunRecord
from deerflow.runtime.runs.schemas import DisconnectMode, RunStatus


def _clarification_tool_message(request_id: str) -> ToolMessage:
    return ToolMessage(
        content="Need more information",
        name="ask_clarification",
        tool_call_id="call-clarify",
        artifact={"human_input": {"request_id": request_id}},
    )


def _human_input_response(request_id: str) -> HumanMessage:
    return HumanMessage(
        content="hidden response",
        additional_kwargs={
            "hide_from_ui": True,
            "human_input_response": {
                "version": 1,
                "kind": "human_input_response",
                "source": "ask_clarification",
                "request_id": request_id,
                "response_kind": "option",
                "option_id": "opt-1",
                "value": "Proceed",
            },
        },
    )


def test_open_clarification_is_cleared_by_matching_response() -> None:
    assert not worker._has_open_clarification([_clarification_tool_message("req-1"), _human_input_response("req-1")])


def test_open_clarification_detects_nested_stream_chunk() -> None:
    assert worker._chunk_has_open_clarification({"lead_agent": {"messages": [_clarification_tool_message("req-1")]}})


@pytest.mark.asyncio
async def test_durable_answer_overrides_replayed_stream_clarification() -> None:
    class ClosedClarificationCheckpointer:
        async def aget_tuple(self, *_args, **_kwargs):
            return SimpleNamespace(
                checkpoint={
                    "channel_values": {
                        "messages": [
                            _clarification_tool_message("req-1"),
                            _human_input_response("req-1"),
                        ]
                    }
                }
            )

    assert not await worker._resolve_pending_clarification(
        checkpointer=ClosedClarificationCheckpointer(),
        thread_id="thread-1",
        stream_clarification_seen=True,
    )


@pytest.mark.asyncio
async def test_stream_clarification_is_used_without_checkpointer() -> None:
    assert await worker._resolve_pending_clarification(
        checkpointer=None,
        thread_id="thread-1",
        stream_clarification_seen=True,
    )


@pytest.mark.asyncio
async def test_run_agent_does_not_start_goal_continuation_for_clarification(monkeypatch) -> None:
    class FakeAgent:
        metadata = {}
        checkpointer = None
        store = None
        interrupt_before_nodes = []
        interrupt_after_nodes = []

        def __init__(self) -> None:
            self.inputs = []

        def astream(self, input_payload, **_kwargs):
            self.inputs.append(input_payload)

            async def _gen():
                yield {"messages": []}

            return _gen()

    class FakeRunManager:
        async def wait_for_prior_finalizing(self, *_args, **_kwargs):
            return None

        async def set_status(self, _run_id, status, **kwargs):
            record.status = status
            if "stop_reason" in kwargs:
                record.stop_reason = kwargs["stop_reason"]

        async def update_model_name(self, *_args, **_kwargs):
            return None

        async def update_run_completion(self, *_args, **_kwargs):
            return None

        async def set_finalizing(self, _run_id, finalizing):
            record.finalizing = finalizing

    class FakeBridge:
        def __init__(self) -> None:
            self.end_count = 0

        async def publish(self, *_args, **_kwargs):
            return None

        async def publish_end(self, *_args, **_kwargs):
            self.end_count += 1

        async def cleanup(self, *_args, **_kwargs):
            return None

    class FakeCheckpointer:
        async def aget_tuple(self, *_args, **_kwargs):
            return SimpleNamespace(
                checkpoint={"channel_values": {"messages": [_clarification_tool_message("req-1")]}},
                config={"configurable": {"checkpoint_ns": "", "checkpoint_id": "ckpt-1"}},
                metadata={},
                pending_writes=[],
            )

    async def fail_prepare(**_kwargs):
        raise AssertionError("goal continuation must not run while clarification is pending")

    monkeypatch.setattr(worker, "_prepare_goal_continuation_input", fail_prepare)

    fake_agent = FakeAgent()
    record = RunRecord(
        run_id="run-clarification",
        thread_id="thread-clarification",
        assistant_id="lead-agent",
        status=RunStatus.pending,
        on_disconnect=DisconnectMode.cancel,
        model_name="test-model",
    )
    bridge = FakeBridge()
    await worker.run_agent(
        bridge,
        FakeRunManager(),
        record,
        ctx=worker.RunContext(checkpointer=FakeCheckpointer()),
        agent_factory=lambda config: fake_agent,
        graph_input={"messages": [HumanMessage(content="start")]},
        config={"configurable": {"thread_id": "thread-clarification"}},
    )

    assert len(fake_agent.inputs) == 1
    assert record.status == RunStatus.success
    assert record.stop_reason == "awaiting_human_input"
    assert bridge.end_count == 1

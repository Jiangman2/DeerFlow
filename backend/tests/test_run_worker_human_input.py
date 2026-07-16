import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from deerflow.runtime.runs import worker
from deerflow.runtime.runs.manager import RunRecord
from deerflow.runtime.runs.schemas import DisconnectMode, RunStatus
from deerflow.runtime.runs.worker import _has_open_clarification


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


def test_open_clarification_detects_unanswered_request() -> None:
    assert _has_open_clarification([_clarification_tool_message("req-1")])


def test_open_clarification_is_cleared_by_matching_human_response() -> None:
    assert not _has_open_clarification(
        [
            _clarification_tool_message("req-1"),
            _human_input_response("req-1"),
        ]
    )


def test_open_clarification_keeps_unmatched_request_open() -> None:
    assert _has_open_clarification(
        [
            _clarification_tool_message("req-1"),
            _human_input_response("other-request"),
        ]
    )


@pytest.mark.asyncio
async def test_run_agent_ends_without_goal_continuation_when_clarification_is_open(monkeypatch) -> None:
    class FakeAgent:
        def __init__(self) -> None:
            self.inputs = []
            self.metadata = {}
            self.checkpointer = None
            self.store = None
            self.interrupt_before_nodes = []
            self.interrupt_after_nodes = []

        def astream(self, input_payload, **_kwargs):
            self.inputs.append(input_payload)

            async def _gen():
                yield {"messages": []}

            return _gen()

    class FakeRunManager:
        async def wait_for_prior_finalizing(self, *_args, **_kwargs):
            return None

        async def set_status(self, _run_id, status, **_kwargs):
            record.status = status

        async def update_model_name(self, *_args, **_kwargs):
            return None

        async def update_run_completion(self, *_args, **_kwargs):
            return None

        async def set_finalizing(self, _run_id, finalizing):
            record.finalizing = finalizing

    class FakeBridge:
        def __init__(self) -> None:
            self.events = []

        async def publish(self, *_args, **_kwargs):
            return None

        async def publish_end(self, *_args, **_kwargs):
            self.events.append("end")

        async def cleanup(self, *_args, **_kwargs):
            return None

    class FakeCheckpointer:
        def __init__(self) -> None:
            self.read_count = 0

        async def aget_tuple(self, *_args, **_kwargs):
            self.read_count += 1
            if self.read_count == 1:
                return None
            return SimpleNamespace(
                checkpoint={
                    "channel_values": {
                        "messages": [_clarification_tool_message("req-1")],
                    },
                },
                config={"configurable": {"checkpoint_ns": "", "checkpoint_id": "ckpt-1"}},
                metadata={},
                pending_writes=[],
            )

    async def fail_prepare(**_kwargs):
        raise AssertionError("goal continuation must not run while clarification waits for user input")

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
    record.abort_event = asyncio.Event()

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
    assert bridge.events == ["end"]

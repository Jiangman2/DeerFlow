"""Official changed-output acceptance with enterprise publication boundaries."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from deerflow.config import paths as paths_module
from deerflow.config.paths import Paths
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.runs import worker
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import RunStatus
from deerflow.runtime.runs.store.memory import MemoryRunStore
from deerflow.runtime.user_context import get_effective_user_id

OUTPUT = "/mnt/user-data/outputs/report.md"


def _receipt(journal, tool, paths):
    call = uuid4()
    journal.on_tool_start({"name": tool}, "{}", run_id=call)
    journal.on_tool_end(Command(update={"artifacts": paths, "messages": [ToolMessage("published", tool_call_id=str(call))]}), run_id=call)


@pytest.fixture
def execute(tmp_path, monkeypatch):
    paths = Paths(tmp_path)
    monkeypatch.setattr(paths_module, "_paths", paths)
    user = get_effective_user_id()
    paths.ensure_thread_dirs("delivery-test", user_id=user)
    outputs = paths.sandbox_outputs_dir("delivery-test", user_id=user)

    async def run(action, *, events=None, graph_input=None, app_config=None, clarify=False):
        store = MemoryRunStore()
        manager = RunManager(store=store)
        record = await manager.create("delivery-test")
        events = events or MemoryRunEventStore()
        bridge = SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())

        class Agent:
            async def astream(self, _input, config=None, **_kwargs):
                await action(config["context"]["__run_journal"], outputs, record)
                yield {"messages": [ToolMessage("Confirm?", name="ask_clarification", tool_call_id="clarify", artifact={"human_input": {"request_id": "confirm-1"}})] if clarify else []}

        await worker.run_agent(bridge, manager, record, ctx=worker.RunContext(checkpointer=None, event_store=events, app_config=app_config), agent_factory=lambda **_kwargs: Agent(), graph_input=graph_input or {}, config={})
        receipts = await events.list_events(record.thread_id, record.run_id, event_types=["run.delivery"])
        return record, await store.get(record.run_id), receipts, bridge

    return run


@pytest.mark.anyio
@pytest.mark.parametrize("tool", ["present_files", "generate_image", "generate_video", "browser_screenshot", "web_capture"])
async def test_real_output_and_trusted_tool_receipt_succeed(execute, tool):
    async def action(journal, outputs, _record):
        (outputs / "report.md").write_text("new deliverable", encoding="utf-8")
        _receipt(journal, tool, [OUTPUT])

    record, durable, receipts, _ = await execute(action)
    assert record.status == RunStatus.success and durable["status"] == "success"
    assert len(receipts) == 1
    assert receipts[0]["content"]["matched_paths"] == [OUTPUT]
    assert receipts[0]["content"]["satisfied"] is True


@pytest.mark.anyio
@pytest.mark.parametrize("claim", ["none", "prose", "state", "untrusted_tool", "stale_path"])
async def test_produced_output_without_current_publication_is_error(execute, claim):
    async def action(journal, outputs, _record):
        (outputs / "report.md").write_text("unpresented", encoding="utf-8")
        if claim == "prose":
            journal._remember_current_run_tool_calls(AIMessage(content=f"Done [{OUTPUT}]({OUTPUT})"), caller="lead_agent")
        elif claim == "untrusted_tool":
            _receipt(journal, "arbitrary_mcp_tool", [OUTPUT])
        elif claim == "stale_path":
            _receipt(journal, "present_files", ["/mnt/user-data/outputs/old.md"])

    record, durable, receipts, _ = await execute(action, graph_input={"artifacts": [OUTPUT]} if claim == "state" else {})
    assert record.status == RunStatus.error and durable["status"] == "error"
    assert "no produced output artifact was presented" in record.error
    assert receipts[0]["content"]["produced_paths"] == [OUTPUT]
    assert receipts[0]["content"]["satisfied"] is False


@pytest.mark.anyio
@pytest.mark.parametrize("folder", [".tool-results", ".browser-frames", ".mcp"])
async def test_internal_feedback_never_demands_publication(execute, folder):
    async def action(_journal, outputs, _record):
        (outputs / folder).mkdir()
        (outputs / folder / "output.txt").write_text("process feedback", encoding="utf-8")

    record, _, receipts, _ = await execute(action)
    assert record.status == RunStatus.success
    assert "produced_paths" not in receipts[0]["content"]


@pytest.mark.anyio
async def test_delivery_receipt_outage_fails_artifact_run_only(execute):
    class Unavailable(MemoryRunEventStore):
        async def put_if_absent(self, **_kwargs):
            raise RuntimeError("synthetic receipt outage")

    async def action(journal, outputs, _record):
        (outputs / "report.md").write_text("published", encoding="utf-8")
        _receipt(journal, "present_files", [OUTPUT])

    record, durable, receipts, _ = await execute(action, events=Unavailable())
    assert record.status == RunStatus.error and durable["status"] == "error"
    assert "receipt could not be persisted" in record.error
    assert not receipts


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["cancel", "error"])
async def test_delivery_verdict_preserves_original_failure(execute, outcome):
    async def action(_journal, outputs, record):
        (outputs / "report.md").write_text("partial", encoding="utf-8")
        if outcome == "cancel":
            record.abort_event.set()
            raise asyncio.CancelledError()
        raise RuntimeError("original error")

    record, _, receipts, _ = await execute(action)
    assert record.status == (RunStatus.interrupted if outcome == "cancel" else RunStatus.error)
    if outcome == "error":
        assert record.error == "original error"
    assert receipts[0]["content"]["satisfied"] is False


@pytest.mark.parametrize(
    "presented,expected",
    [
        ("/mnt/user-data/outputs", True),
        ("/mnt/user-data/outputs/", True),
        ("/mnt/user-data/out", False),
        ("/mnt/user-data/outputs/../outputs", False),
        ("/mnt/user-data/outputs//report.md", False),
    ],
)
def test_directory_presentation_is_bounded(presented, expected):
    from deerflow.runtime.runs.delivery import delivery_content_with_outputs

    verdict = delivery_content_with_outputs({"by_tool": {"present_files": [presented]}}, [OUTPUT])
    assert verdict["satisfied"] is expected


def test_one_output_matches_official_minimum_not_all_outputs():
    from deerflow.runtime.runs.delivery import delivery_content_with_outputs

    verdict = delivery_content_with_outputs({"by_tool": {"present_files": [OUTPUT]}}, [OUTPUT, "/mnt/user-data/outputs/appendix.md"])
    assert verdict["satisfied"] is True
    assert verdict["matched_paths"] == [OUTPUT]


@pytest.mark.anyio
async def test_custom_feedback_dir_is_excluded_in_all_scans(execute):
    from deerflow.config.app_config import AppConfig

    config = AppConfig(sandbox={"use": "deerflow.sandbox.local:LocalSandboxProvider"}, tool_output={"storage_subdir": "custom-feedback"})

    async def action(_journal, outputs, _record):
        (outputs / "custom-feedback").mkdir()
        (outputs / "custom-feedback" / "trace.txt").write_text("feedback", encoding="utf-8")

    record, _, receipts, _ = await execute(action, app_config=config)
    assert record.status == RunStatus.success
    assert "produced_paths" not in receipts[0]["content"]


@pytest.mark.anyio
async def test_unpublished_draft_does_not_turn_human_wait_into_failure(execute):
    async def action(_journal, outputs, _record):
        (outputs / "report.md").write_text("draft awaiting user approval", encoding="utf-8")

    record, durable, receipts, _ = await execute(action, clarify=True)
    assert record.status == RunStatus.success and durable["stop_reason"] == "awaiting_human_input"
    assert receipts[0]["content"]["satisfied"] is False


@pytest.mark.anyio
async def test_ppt_external_handoff_satisfies_worker_without_tool_callback(execute):
    from deerflow.agents.ppt_master_runner.agent import _report_agent_delivery

    async def action(journal, outputs, record):
        (outputs / "report.md").write_text("handoff", encoding="utf-8")
        runtime = SimpleNamespace(context={"run_id": record.run_id, "thread_id": record.thread_id})
        _report_agent_delivery({"callbacks": [journal]}, runtime, {"status": "completed", "task_id": record.run_id, "thread_id": record.thread_id, "artifacts": [OUTPUT]})

    record, _, receipts, _ = await execute(action)
    assert record.status == RunStatus.success
    assert receipts[0]["content"]["by_tool"] == {}
    assert receipts[0]["content"]["matched_paths"] == [OUTPUT]


@pytest.mark.anyio
async def test_lease_loss_during_output_scan_suppresses_receipt(execute, monkeypatch):
    active = []
    original = worker._produced_output_paths

    async def scan(*args, **kwargs):
        paths = await original(*args, **kwargs)
        active[0].ownership_lost = True
        return paths

    monkeypatch.setattr(worker, "_produced_output_paths", scan)

    async def action(journal, outputs, record):
        active.append(record)
        (outputs / "report.md").write_text("new output", encoding="utf-8")
        _receipt(journal, "present_files", [OUTPUT])

    record, durable, receipts, bridge = await execute(action)
    assert record.ownership_lost
    assert durable["status"] == "running"
    assert not receipts
    bridge.publish_end.assert_awaited_once()

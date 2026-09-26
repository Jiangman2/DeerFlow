"""Only the authenticated current Agent-host handoff is a PPT publication."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from langchain_core.messages import HumanMessage

from deerflow.agents.ppt_master_runner import agent
from deerflow.runtime.events.store.memory import MemoryRunEventStore
from deerflow.runtime.journal import RunJournal

PPTX = "/mnt/user-data/outputs/周报.pptx"
PDF = "/mnt/user-data/outputs/周报.preview.pdf"


def _journal():
    return RunJournal(run_id="run-ppt", thread_id="thread-ppt", event_store=MemoryRunEventStore())


def _report(journal, result):
    return agent._report_agent_delivery({"callbacks": [journal]}, SimpleNamespace(context={"run_id": "run-ppt", "thread_id": "thread-ppt"}), result)


def _completed(**kwargs):
    return {"status": "completed", "task_id": "run-ppt", "thread_id": "thread-ppt", "artifacts": [PPTX, PDF], **kwargs}


def test_completed_ppt_handoff_records_publisher_not_fabricated_tool():
    journal = _journal()
    assert _report(journal, _completed()) == [PPTX, PDF]
    _report(journal, _completed())
    content = journal.get_delivery_content()
    assert content["by_tool"] == {}
    assert content["by_publisher"] == {"ppt_master_agent_host": [PPTX, PDF]}
    assert content["presented"] == 2


@pytest.mark.parametrize("override", [{"task_id": "old-run"}, {"thread_id": "other-thread"}, {"task_id": None}])
def test_rejects_cross_task_handoff(override):
    journal = _journal()
    with pytest.raises(RuntimeError, match="identity"):
        _report(journal, _completed(**override))
    assert journal.get_delivery_content()["presented"] == 0


@pytest.mark.parametrize("path", ["/mnt/user-data/uploads/input.pptx", "/mnt/user-data/outputs/../secret.pptx", "https://host/deck.pptx", "/mnt/user-data/outputs", "/mnt/user-data/outputs/a\\b.pptx"])
def test_rejects_noncanonical_output_handoff_atomically(path):
    journal = _journal()
    with pytest.raises(RuntimeError, match="artifact"):
        _report(journal, _completed(artifacts=[PPTX, path]))
    assert journal.get_delivery_content()["presented"] == 0


@pytest.mark.parametrize("status", ["running", "failed", "cancelled"])
def test_noncompleted_agent_cannot_publish(status):
    journal = _journal()
    assert _report(journal, _completed(status=status)) == []
    assert journal.get_delivery_content()["presented"] == 0


def test_missing_delivery_recorder_fails_closed_for_artifacts():
    with pytest.raises(RuntimeError, match="recorder"):
        _report(SimpleNamespace(record_external_llm_usage_records=lambda _: None), _completed())


def test_empty_handoff_does_not_require_recorder():
    assert _report(object(), _completed(artifacts=[])) == []


@pytest.mark.anyio
async def test_closed_journal_cannot_accept_late_external_handoff():
    journal = _journal()
    await journal.close(flush=False)
    with pytest.raises(RuntimeError, match="closed"):
        _report(journal, _completed())
    assert journal.get_delivery_content()["presented"] == 0


@pytest.mark.anyio
async def test_compiled_runner_handoff_reaches_durable_worker_receipt(tmp_path, monkeypatch):
    from deerflow.config import paths as paths_module
    from deerflow.config.app_config import AppConfig
    from deerflow.config.paths import Paths
    from deerflow.runtime.runs.manager import RunManager
    from deerflow.runtime.runs.worker import RunContext, run_agent
    from deerflow.runtime.user_context import get_effective_user_id

    paths = Paths(tmp_path)
    monkeypatch.setattr(paths_module, "_paths", paths)
    owner = get_effective_user_id()
    paths.ensure_thread_dirs("thread-ppt", user_id=owner)
    outputs = paths.sandbox_outputs_dir("thread-ppt", user_id=owner)
    provider = SimpleNamespace(
        acquire_async=AsyncMock(return_value="sandbox-test"),
        get=Mock(return_value=SimpleNamespace(base_url="http://sandbox-test:8080")),
        acquire_execution_lease=Mock(return_value="lease-test"),
        release_execution_lease=Mock(),
        release=Mock(),
    )
    monkeypatch.setattr(agent, "get_sandbox_provider", lambda: provider)
    monkeypatch.setattr(agent, "_wait_for_agent_host", AsyncMock())
    monkeypatch.setattr(agent, "_start_agent_task_with_retry", AsyncMock(return_value=httpx.Response(200, request=httpx.Request("POST", "http://synthetic/v1/tasks"))))

    async def poll(**kwargs):
        (outputs / "周报.pptx").write_bytes(b"synthetic already-validated host handoff")
        (outputs / "周报.preview.pdf").write_bytes(b"synthetic PDF companion")
        return _completed(task_id=kwargs["task_id"], thread_id=kwargs["thread_id"], final_message="已交付")

    monkeypatch.setattr(agent, "_poll_agent_turn", poll)
    config = AppConfig(sandbox={"use": "deerflow.sandbox.local:LocalSandboxProvider", "sandbox_api_key": "synthetic-test-only"})
    manager = RunManager()
    record = await manager.create("thread-ppt")
    events = MemoryRunEventStore()
    bridge = SimpleNamespace(publish=AsyncMock(), publish_end=AsyncMock(), cleanup=AsyncMock())
    await run_agent(
        bridge,
        manager,
        record,
        ctx=RunContext(checkpointer=None, event_store=events),
        agent_factory=lambda **kwargs: agent.make_ppt_master_runner_agent(kwargs["config"], config),
        graph_input={"messages": [HumanMessage("/ppt-master 生成周报")]},
        config={},
    )
    receipt = await events.list_events(record.thread_id, record.run_id, event_types=["run.delivery"])
    assert record.status.value == "success", record.error
    assert len(receipt) == 1
    assert receipt[0]["content"]["by_tool"] == {}
    assert receipt[0]["content"]["by_publisher"] == {"ppt_master_agent_host": [PPTX, PDF]}
    assert receipt[0]["content"]["matched_paths"] == sorted([PDF, PPTX])
    provider.release_execution_lease.assert_called_once_with("lease-test", "sandbox-test")

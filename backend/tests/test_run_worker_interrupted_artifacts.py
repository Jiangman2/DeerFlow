from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from deerflow.runtime.runs.worker import _reconcile_interrupted_artifact_claim


class _Journal:
    def __init__(self, message: AIMessage):
        self.message = message
        self.reconciled = None

    def get_last_lead_ai_message(self):
        return self.message

    def reconcile_ai_message(self, message, *, reason):
        self.reconciled = (message, reason)


class _Checkpointer:
    def __init__(self, values):
        self.values = values

    async def aget_tuple(self, _config):
        return SimpleNamespace(checkpoint={"channel_values": self.values})


@pytest.mark.anyio
async def test_interrupted_fake_artifact_api_link_is_reconciled(tmp_path: Path):
    message = AIMessage(
        id="final-answer",
        content=(
            "[report.pptx](/api/threads/thread-1/artifacts/"
            "mnt/user-data/outputs/report.pptx)"
        ),
    )
    journal = _Journal(message)
    checkpointer = _Checkpointer(
        {"thread_data": {"outputs_path": str(tmp_path)}, "artifacts": []},
    )

    changed = await _reconcile_interrupted_artifact_claim(
        journal=journal,
        checkpointer=checkpointer,
        thread_id="thread-1",
    )

    assert changed is True
    fallback, reason = journal.reconciled
    assert fallback.id == "final-answer"
    assert "/artifacts/" not in fallback.content
    assert fallback.additional_kwargs["deerflow_error_fallback"] is True
    assert reason == "interrupted_artifact_delivery"


@pytest.mark.anyio
async def test_interrupted_published_artifact_is_kept(tmp_path: Path):
    output = tmp_path / "report.pptx"
    output.write_bytes(b"pptx")
    virtual_path = "/mnt/user-data/outputs/report.pptx"
    message = AIMessage(
        id="real-answer",
        content=(
            "[report.pptx](/api/threads/thread-1/artifacts/"
            "mnt/user-data/outputs/report.pptx)"
        ),
    )
    journal = _Journal(message)
    checkpointer = _Checkpointer(
        {
            "thread_data": {"outputs_path": str(tmp_path)},
            "artifacts": [virtual_path],
        },
    )

    changed = await _reconcile_interrupted_artifact_claim(
        journal=journal,
        checkpointer=checkpointer,
        thread_id="thread-1",
    )

    assert changed is False
    assert journal.reconciled is None

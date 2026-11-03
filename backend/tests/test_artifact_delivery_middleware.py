from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from deerflow.agents.middlewares.artifact_delivery_middleware import ArtifactDeliveryMiddleware


def _runtime(thread_id: str = "thread-1", run_id: str = "run-1") -> SimpleNamespace:
    return SimpleNamespace(context={"thread_id": thread_id, "run_id": run_id})


def _state(outputs_dir: Path, message: AIMessage, *, artifacts: list[str] | None = None) -> dict:
    return {
        "messages": [HumanMessage(content="请制作 PPT"), message],
        "thread_data": {"outputs_path": str(outputs_dir)},
        "artifacts": artifacts or [],
    }


def test_missing_linked_output_is_removed_and_retried(tmp_path: Path):
    middleware = ArtifactDeliveryMiddleware()
    message = AIMessage(
        id="fake-delivery",
        content="已完成：[报告.pptx](/mnt/user-data/outputs/报告.pptx)",
    )

    result = middleware.after_model(_state(tmp_path, message), _runtime())

    assert result is not None
    assert result["jump_to"] == "model"
    assert result["messages"] == [RemoveMessage(id="fake-delivery")]


def test_existing_but_unpresented_output_is_retried(tmp_path: Path):
    output = tmp_path / "报告.docx"
    output.write_bytes(b"docx")
    middleware = ArtifactDeliveryMiddleware()
    message = AIMessage(id="unpublished", content="文件：`/mnt/user-data/outputs/报告.docx`")

    result = middleware.after_model(_state(tmp_path, message), _runtime())

    assert result is not None
    assert result["jump_to"] == "model"


def test_fabricated_artifact_api_link_is_retried(tmp_path: Path):
    middleware = ArtifactDeliveryMiddleware()
    message = AIMessage(
        id="fake-api-delivery",
        content=("[report.pptx](/api/threads/thread-1/artifacts/mnt/user-data/outputs/report.pptx)"),
    )

    result = middleware.after_model(_state(tmp_path, message), _runtime())

    assert result is not None
    assert result["jump_to"] == "model"
    assert result["messages"] == [RemoveMessage(id="fake-api-delivery")]


def test_existing_presented_output_is_allowed(tmp_path: Path):
    output = tmp_path / "报告.pptx"
    output.write_bytes(b"pptx")
    path = "/mnt/user-data/outputs/报告.pptx"
    middleware = ArtifactDeliveryMiddleware()
    message = AIMessage(content=f"下载：[{output.name}]({path})")

    assert (
        middleware.after_model(
            _state(tmp_path, message, artifacts=[path]),
            _runtime(),
        )
        is None
    )


def test_retry_request_receives_hidden_execution_reminder(tmp_path: Path):
    middleware = ArtifactDeliveryMiddleware()
    message = AIMessage(id="fake-delivery", content="/mnt/user-data/outputs/missing.xlsx")
    runtime = _runtime()
    middleware.after_model(_state(tmp_path, message), runtime)
    request = SimpleNamespace(
        runtime=runtime,
        messages=[HumanMessage(content="生成表格")],
        override=lambda **kwargs: kwargs,
    )

    augmented = middleware._augment_request(request)

    reminder = augmented["messages"][-1]
    assert reminder.name == "artifact_delivery_recovery"
    assert reminder.additional_kwargs["hide_from_ui"] is True
    assert "bash" in reminder.content
    assert "present_files" in reminder.content
    assert "read_file" in reminder.content


def test_repeated_fake_delivery_becomes_visible_error_without_link(tmp_path: Path):
    middleware = ArtifactDeliveryMiddleware(max_retries=1)
    runtime = _runtime()
    first = AIMessage(id="fake-1", content="/mnt/user-data/outputs/missing.pptx")
    middleware.after_model(_state(tmp_path, first), runtime)
    second = AIMessage(id="fake-2", content="/mnt/user-data/outputs/missing.pptx")

    result = middleware.after_model(_state(tmp_path, second), runtime)

    assert result is not None
    replacement = result["messages"][0]
    assert isinstance(replacement, AIMessage)
    assert "/mnt/user-data/outputs/" not in replacement.content
    assert replacement.additional_kwargs["deerflow_error_fallback"] is True
    assert replacement.additional_kwargs["error_reason"] == "Artifact delivery validation failed"


def test_delivery_failure_remains_terminal_for_the_rest_of_the_run(tmp_path: Path):
    middleware = ArtifactDeliveryMiddleware(max_retries=1)
    runtime = _runtime()
    middleware.after_model(
        _state(tmp_path, AIMessage(id="fake-1", content="/mnt/user-data/outputs/missing.pptx")),
        runtime,
    )
    middleware.after_model(
        _state(tmp_path, AIMessage(id="fake-2", content="/mnt/user-data/outputs/missing.pptx")),
        runtime,
    )

    result = middleware.after_model(
        _state(tmp_path, AIMessage(id="fake-3", content="/mnt/user-data/outputs/missing.pptx")),
        runtime,
    )

    assert result is not None
    assert "jump_to" not in result
    replacement = result["messages"][0]
    assert replacement.additional_kwargs["deerflow_error_fallback"] is True


def test_normal_response_without_output_path_is_ignored(tmp_path: Path):
    middleware = ArtifactDeliveryMiddleware()
    message = AIMessage(content="目前还在生成中，我会继续处理。")

    assert middleware.after_model(_state(tmp_path, message), _runtime()) is None


def test_tool_call_response_is_not_intercepted(tmp_path: Path):
    middleware = ArtifactDeliveryMiddleware()
    message = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call-present",
                "name": "present_files",
                "args": {"filepaths": ["/mnt/user-data/outputs/report.pptx"]},
            }
        ],
    )

    assert middleware.after_model(_state(tmp_path, message), _runtime()) is None
